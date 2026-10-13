"""
Banco di prova: carica il bot vero (telegram_listener e tutto il resto) con il
broker finto di fake_mt5.py al posto di MetaTrader5, un agente finto che
restituisce la classificazione decisa dal test e senza Telegram.

Garanzie, valide anche se i test vengono lanciati sul server:
  - si lavora in una cartella temporanea: log, active_trades.json e storico
    dei test non toccano mai quelli veri;
  - il .env non viene letto e i parametri del bot sono tolti dall'ambiente:
    i test provano sempre i valori di default del codice (le regole in vigore);
  - nessuna chiamata a MT5, a Telegram o all'API di Anthropic.

Uso in un file di test:
    from banco import *
    scenario("BUY 4186 nella zona")
    broker.reset(price=4185.0)
    segnale(1, "BUY", 4186, 4181, 4186, 4176, [4196, 4206])
    check(len(broker.positions) == 2, "2 posizioni a mercato")
    ...
    fine()
"""
import asyncio
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import types
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 1. Parametri: sempre i default del codice, mai quelli del .env o della shell
_sources = "".join(open(f, encoding="utf-8").read() for f in glob.glob(os.path.join(ROOT, "*.py")))
for _name in set(re.findall(r'os\.getenv\("([A-Z_0-9]+)"', _sources)):
    os.environ.pop(_name, None)

# 2. Cartella di lavoro temporanea (i percorsi del bot sono relativi)
WORKDIR = tempfile.mkdtemp(prefix="banco_bot_")
os.chdir(WORKDIR)

# 3. Moduli finti: broker, collegamento RPyC del server, .env, Telegram, agente
import fake_mt5  # noqa: E402

broker = fake_mt5.FakeMT5()
sys.modules["MetaTrader5"] = broker
_conn = types.ModuleType("mt5_connection")
_conn.mt5 = broker
sys.modules["mt5_connection"] = _conn
_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *a, **k: False
sys.modules["dotenv"] = _dotenv


class _Client:
    def __init__(self, *a, **k):
        pass

    def on(self, *a, **k):
        return lambda f: f


_telethon = types.ModuleType("telethon")
_telethon.TelegramClient = _Client
_telethon.events = types.SimpleNamespace(NewMessage=lambda **k: None, MessageEdited=lambda **k: None)
sys.modules["telethon"] = _telethon
_agent = types.ModuleType("agent_classifier")
_agent.agent_classify_telegram_message = None
sys.modules["agent_classifier"] = _agent

import telegram_listener as L  # noqa: E402
import risk_manager  # noqa: E402
import narrative  # noqa: E402

L.is_market_time_open = lambda: (True, "")
_next_classification = {}
L.agent_classify_telegram_message = lambda *a, **k: _next_classification["value"]


# ----------------------------------------------------------------------
# Messaggi del trader
# ----------------------------------------------------------------------
def classe(intent, direction=None, entry_min=None, entry_max=None, stop_loss=None, take_profit=None,
           be=False, pips=None, reentry=False, close_percentage=None, layer=None):
    """La classificazione che l'agente finto restituirà per il prossimo messaggio."""
    return {
        "intent": intent, "is_actionable": True, "raw_reasoning": "banco di prova",
        "data": {
            "symbol": "XAUUSD", "direction": direction, "entry_min": entry_min, "entry_max": entry_max,
            "stop_loss": stop_loss, "take_profit": take_profit or [], "is_reentry": reentry,
            "running_pips": pips or [],
            "update_details": {"move_sl_to_be": be, "close_percentage": close_percentage, "layer_target": layer},
        },
    }


class _Event:
    def __init__(self, msg_id, text, reply_to=None):
        self.id = msg_id
        self.raw_text = text
        self.sender_id = -100
        self.reply_to_msg_id = reply_to
        self.media = None
        self.forward = None
        self.date = datetime.now(timezone.utc)
        self.edit_date = datetime.now(timezone.utc)


def invia(msg_id, text, classification, edit=False, reply_to=None):
    """Il trader scrive (o modifica, edit=True) un messaggio; l'agente lo classifica così."""
    _next_classification["value"] = classification
    asyncio.run(L.process_message(_Event(msg_id, text, reply_to), edit))


def segnale(msg_id, direction, price, low, high, sl, tps):
    """Segnale rapido ("Gold buy 4186") seguito dal completamento con zona, SL e TP."""
    d = direction.upper()
    invia(msg_id, f"Gold {d.lower()} {price}", classe("NEW_SIGNAL", direction=d, entry_min=float(price)))
    invia(msg_id, f"GOLD {d} NOW · {d.title()} @ {low} - {high} · SL {sl} · TP {tps}",
          classe("UPDATE_SIGNAL", direction=d, entry_min=float(low), entry_max=float(high),
                 stop_loss=float(sl), take_profit=[float(t) for t in tps]), edit=True)


def trade_active(msg_id, reply_to, direction="BUY", pips=40.0):
    invia(msg_id, f"Trade Active ✅ · Gold {direction.title()} Running {pips:g}+ Pips · BE+ ur entries",
          classe("UPDATE_SIGNAL", be=True, pips=[pips]), reply_to=reply_to)


def pareggio(msg_id, reply_to):
    invia(msg_id, "Hold it with BE", classe("UPDATE_SIGNAL", be=True), reply_to=reply_to)


def controllo_periodico():
    """
    Un giro del controllo ogni 30 secondi, senza aspettare. Stessa sequenza di
    monitor_loop() in telegram_listener.py: se cambia lì, va cambiata qui.
    """
    ok, _reason = L.mt5_agent.check_health()
    if not ok:
        return
    try:
        L.sync_pending_entries()
        L.adopt_orphan_positions()
        L.manager.reconcile_with_broker(L.broker_position_lookup)
        L.restore_missing_stops()
        L.retry_pending_breakeven()
    except L.MT5UnavailableError:
        pass


# ----------------------------------------------------------------------
# Stato
# ----------------------------------------------------------------------
def operazione(msg_id):
    return L.manager.active_trades.get(msg_id)


def ticket(msg_id, key):
    """Il ticket in memoria (e1, e2…) dell'operazione aperta dal messaggio msg_id."""
    return L.manager.active_trades[msg_id]["tickets"][key]


def posizione(msg_id, key):
    """La posizione sul broker del ticket, o None se non è aperta."""
    return broker.positions.get(ticket(msg_id, key).get("mt5_ticket"))


def pulisci():
    """Svuota broker e memoria tra uno scenario e l'altro."""
    broker.positions.clear()
    broker.orders.clear()
    L.manager.active_trades.clear()
    L.manager.save_state_to_file()


def racconto():
    """Il bot.log di oggi (il racconto per il trader umano)."""
    files = glob.glob(os.path.join(WORKDIR, "logs", "*", "bot.log"))
    return open(files[0], encoding="utf-8").read() if files else ""


def schede():
    files = glob.glob(os.path.join(WORKDIR, "logs", "*", "operazioni.txt"))
    return open(files[0], encoding="utf-8").read() if files else ""


def diario():
    files = glob.glob(os.path.join(WORKDIR, "logs", "*", "journal.jsonl"))
    return [json.loads(line) for line in open(files[0], encoding="utf-8")] if files else []


# ----------------------------------------------------------------------
# Esito
# ----------------------------------------------------------------------
_results = {"ok": 0, "failed": []}


def scenario(title):
    print(f"\n== {title}")


def check(condition, description, detail=None):
    if condition:
        _results["ok"] += 1
        print(f"   ok  {description}")
    else:
        _results["failed"].append(description)
        print(f"   NO  {description}" + (f"  → {detail}" if detail is not None else ""))


def vicino(a, b, tolerance=0.005):
    return a is not None and b is not None and abs(a - b) <= tolerance


def fine():
    failed = _results["failed"]
    print(f"\n{_results['ok']} controlli riusciti, {len(failed)} falliti")
    if failed:
        print(f"Log e stato dei test in {WORKDIR}")
    else:
        os.chdir(ROOT)
        shutil.rmtree(WORKDIR, ignore_errors=True)
    sys.exit(1 if failed else 0)
