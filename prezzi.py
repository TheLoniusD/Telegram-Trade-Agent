"""
Cosa ha fatto davvero il prezzo dopo ogni segnale di una giornata, sulle
candele a 1 minuto di MT5. Solo lettura: non invia ordini.

Per ogni operazione del diario (logs/AAAA-MM-GG/journal.jsonl), misurando dal
bordo della zona del segnale (il prezzo da cui parte il trader):
  - quanto è andato a favore e contro prima che arrivasse lo SL del trader o il TP1;
  - se lo SL del trader è stato toccato, quando e di quanto è andato oltre;
  - se dopo lo SL il prezzo è poi arrivato al TP1 (lo stop preso "per poco");
  - dopo la richiesta di pareggio (Trade Active / BE), quanto è tornato contro
    rispetto all'ingresso prima del TP1: è la misura per il primo gradino dello
    stop di protezione (oggi ¼ della distanza dallo SL, 2,5 $ su 10 $).
In fondo, per alcuni margini sullo SL (--margini), quante operazioni chiuse a
stop pieno sarebbero sopravvissute e avrebbero poi raggiunto il TP1.

Limiti: candele da 1 minuto (se nella stessa candela il prezzo tocca sia lo SL
che il TP1 si assume prima lo SL, per prudenza); lato BUY si usa il bid, lato
SELL l'ask (bid + spread della candela), come fa il broker per SL e TP.

Rigioco ("python prezzi.py rigioco DAL AL"): i segnali e i comandi del trader
di più giornate rigiocati con le regole attuali e con varianti (protezione,
Trade Active, pareggio, tolleranza d'ingresso, TP provvisori: vedi
STRATEGIES), con il totale per giorno e per strategia accanto al risultato
reale del bot. Usa le candele M1 e, attorno a ogni segnale e comando del
trader, i tick reali (prezzo esatto del momento, stop toccati nei secondi dopo).

Uso (dove MT5 è raggiungibile, cioè sul server o sul PC con MT5):
    python prezzi.py 2026-10-05
    python prezzi.py 2026-10-02 --ore 3 --margini 0.5,1,2,3
    python prezzi.py 2026-10-05 --offset-broker 3   # ore del broker rispetto a UTC
    python prezzi.py rigioco 2026-09-24 2026-10-05
    python prezzi.py rigioco 2026-10-02 --dettaglio --strategie "attuale,senza protezione"
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

from logger_config import LOG_DIR, to_local
from verifica_be import POINT, SYMBOL, download_bars, estimate_broker_offset, load_events

PIPS_PER_DOLLAR = 10  # oro: 1 $ = 10 pips, come li conta il trader


def ts_of(event) -> float:
    return datetime.fromisoformat(event["ts"]).timestamp()


def collect_trades(events: list) -> list:
    """Le operazioni del giorno con zona, SL e TP del trader, risultato e richieste di pareggio."""
    trades, owner, first_text = {}, {}, {}
    for e in events:
        if e["event"] == "MESSAGE" and not e.get("is_edit"):
            first_text.setdefault(e.get("msg_id"), (e.get("text") or "").split("\n")[0][:24])
        if e["event"] != "TRADE_STATUS" or not e.get("ticket_id"):
            continue
        trade = trades.setdefault(e["ticket_id"], {"id": e["ticket_id"], "open_ts": ts_of(e), "msg_id": e.get("trade_msg_id"),
                                                   "be_ts": None, "result": 0.0, "closed": 0, "bot_sl": None})
        trade["status"] = e
        for t in (e.get("tickets") or {}).values():
            if t.get("mt5_ticket"):
                owner[t["mt5_ticket"]] = trade
                trade["buy"] = t.get("direction") == "BUY"
                if t.get("stop_loss") and trade["bot_sl"] is None:
                    trade["bot_sl"] = t["stop_loss"]

    for e in events:
        trade = trades.get(e.get("ticket_id")) or owner.get(e.get("mt5_ticket"))
        if trade is None and e["event"] == "MT5_ORDER":
            trade = owner.get((e.get("request") or {}).get("position"))
        if trade is None:
            continue
        be_request = (e["event"] in ("TRADE_ACTIVE_PLAN", "BE_PENDING")
                      or (e["event"] == "MT5_ORDER" and e.get("operation") == "BREAKEVEN")
                      or (e["event"] == "PENDING_CANCELLED" and "BE" in (e.get("reason") or "")))
        if be_request and trade["be_ts"] is None:
            trade["be_ts"] = ts_of(e)
        if e["event"] == "POSITION_CLOSED" and e.get("profit") is not None:
            trade["result"] += e["profit"]
            trade["closed"] += 1

    result = []
    for trade in trades.values():
        status = trade["status"]
        prices = [p for p in (status.get("entry_min"), status.get("entry_max")) if p is not None]
        if not prices or "buy" not in trade:
            continue
        buy = trade["buy"]
        trade["edge"] = max(prices) if buy else min(prices)
        trade["zone"] = (min(prices), max(prices))
        trade["sl"] = status.get("signal_stop_loss")
        tps = {t.get("take_profit") for t in (status.get("tickets") or {}).values() if t.get("take_profit")}
        # Solo i TP dalla parte giusta (05/10: TP 4137/4127 su un BUY 4157, errore del trader)
        tps = sorted(tp for tp in tps if (tp > trade["edge"]) == buy)
        trade["tps"] = tps if buy else tps[::-1]
        trade["label"] = first_text.get(trade["msg_id"], "")
        result.append(trade)
    return sorted(result, key=lambda t: t["open_ts"])


def excursions(trade: dict, bars: list, hours: float) -> dict:
    """Movimenti dal bordo della zona (in $, positivi = a favore / contro)."""
    buy, edge = trade["buy"], trade["edge"]
    sl_dist = abs(edge - trade["sl"]) if trade["sl"] else None
    tp1_dist = abs(trade["tps"][0] - edge) if trade["tps"] else None
    end = trade["open_ts"] + hours * 3600
    out = {"fav": 0.0, "adv": 0.0, "fav_ts": None, "sl_ts": None, "tp1_ts": None,
           "adv_before_tp1": 0.0, "beyond_sl": 0.0, "fav_after_sl": 0.0, "be_adv": None}
    for bar_ts, _open, high, low, _close, spread in bars:
        # la candela dell'apertura contiene prezzi precedenti all'ingresso
        if bar_ts < trade["open_ts"] - trade["open_ts"] % 60 + 60 or bar_ts > end:
            continue
        ask_high, ask_low = high + spread * POINT, low + spread * POINT
        fav = (high - edge) if buy else (edge - ask_low)
        adv = (edge - low) if buy else (ask_high - edge)

        if out["tp1_ts"] is None:
            out["adv_before_tp1"] = max(out["adv_before_tp1"], adv)
        if out["sl_ts"] is None and out["tp1_ts"] is None:
            if fav > out["fav"]:
                out["fav"], out["fav_ts"] = fav, bar_ts
            out["adv"] = max(out["adv"], adv)
        if sl_dist is not None and out["sl_ts"] is None and out["tp1_ts"] is None and adv >= sl_dist:
            out["sl_ts"] = bar_ts  # nella stessa candela del TP1 si assume prima lo SL
        if sl_dist is not None and out["sl_ts"] is not None:
            out["beyond_sl"] = max(out["beyond_sl"], adv - sl_dist)
            out["fav_after_sl"] = max(out["fav_after_sl"], fav)
        if tp1_dist is not None and out["tp1_ts"] is None and fav >= tp1_dist:
            out["tp1_ts"] = bar_ts
        # Dopo la richiesta di pareggio: quanto torna contro prima del TP1 (o dello SL)
        if trade["be_ts"] and bar_ts + 60 > trade["be_ts"] and out["tp1_ts"] is None and out["sl_ts"] in (None, bar_ts):
            out["be_adv"] = max(out["be_adv"] or -1e9, adv)
    return out


def hm(ts) -> str:
    return to_local(ts).strftime("%H:%M") if ts else "--:--"


def pips(dollars: float) -> str:
    return f"{dollars * PIPS_PER_DOLLAR:.0f}"


def connect_mt5():
    """Sul server passa da mt5_connection (RPyC verso mt5server), sul PC Windows dal pacchetto diretto."""
    try:
        from mt5_connection import mt5
    except ImportError:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            raise SystemExit(f"MetaTrader 5 non raggiungibile: {mt5.last_error()}")
    return mt5


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "rigioco":
        return main_rigioco(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("giorno", help="AAAA-MM-GG")
    parser.add_argument("--ore", type=float, default=4, help="ore di prezzo da guardare dopo ogni segnale (default 4)")
    parser.add_argument("--margini", default="0.5,1,2,3", help="margini sullo SL in $ da provare (default 0.5,1,2,3)")
    parser.add_argument("--offset-broker", type=float, help="ore del broker rispetto a UTC (default: stimate da MT5)")
    args = parser.parse_args()

    trades = collect_trades(load_events(args.giorno))
    if not trades:
        raise SystemExit("Nessuna operazione con prezzo di ingresso in questo giorno.")

    mt5 = connect_mt5()
    offset = int(args.offset_broker * 3600) if args.offset_broker is not None else estimate_broker_offset(mt5)
    start = min(t["open_ts"] for t in trades) - 120
    end = min(time.time(), max(t["open_ts"] for t in trades) + args.ore * 3600)
    bars = download_bars(mt5, start, end, offset)
    print(f"Orologio del broker: UTC{offset / 3600:+.1f} h | {len(trades)} operazioni | "
          f"{args.ore:g} ore di prezzo dopo ogni segnale | 1 $ = {PIPS_PER_DOLLAR} pips\n")

    margins = [float(x) for x in args.margini.split(",")]
    saved = {m: [] for m in margins}
    full_stops = []
    for trade in trades:
        x = excursions(trade, bars, args.ore)
        direction = "BUY" if trade["buy"] else "SELL"
        tps = " / ".join(f"{tp:g}" for tp in trade["tps"]) or "—"
        print(f"{hm(trade['open_ts'])} {direction} {trade['edge']:g} «{trade['label']}» · zona {trade['zone'][0]:g}–{trade['zone'][1]:g} "
              f"· SL trader {trade['sl'] or '—'} (bot {trade['bot_sl'] or '—'}) · TP {tps} · bot {trade['result']:+.2f} $")
        print(f"   a favore max {pips(x['fav'])} pips ({hm(x['fav_ts'])}), contro max {pips(x['adv'])} pips, prima di SL/TP1")
        if x["tp1_ts"] and not x["sl_ts"]:
            print(f"   TP1 raggiunto alle {hm(x['tp1_ts'])}, contro al massimo {x['adv_before_tp1']:.2f} $ prima")
        if x["sl_ts"]:
            line = f"   SL del trader toccato alle {hm(x['sl_ts'])}, oltre al massimo di {x['beyond_sl']:.2f} $"
            line += (f"; poi TP1 alle {hm(x['tp1_ts'])} (massimo contro prima del TP1 {x['adv_before_tp1']:.2f} $)"
                     if x["tp1_ts"] else f"; dopo lo SL a favore max {pips(x['fav_after_sl'])} pips, TP1 mai raggiunto")
            # Pareggio già chiesto prima: il bot non era più esposto allo stop pieno
            after_be = bool(trade["be_ts"] and trade["be_ts"] < x["sl_ts"])
            print(line + (" (dopo la richiesta di pareggio)" if after_be else ""))
            if not after_be:
                full_stops.append(trade)
            if x["tp1_ts"] and not after_be:
                sl_dist = abs(trade["edge"] - trade["sl"])
                for m in margins:
                    if x["adv_before_tp1"] < sl_dist + m:
                        saved[m].append(trade)
        if not x["sl_ts"] and not x["tp1_ts"]:
            print("   né SL né TP1 nel periodo guardato")
        if trade["be_ts"]:
            be_adv = x["be_adv"]
            text = (f"tornato contro fino a {be_adv:.2f} $ dal bordo" if be_adv is not None and be_adv > 0
                    else "mai tornato al bordo della zona")
            print(f"   dopo la richiesta di pareggio ({hm(trade['be_ts'])}): {text} prima del TP1")
        print()

    print(f"Margine sullo SL del trader: su {len(full_stops)} SL toccati prima del pareggio, "
          f"quelli evitati che poi hanno raggiunto il TP1")
    for m in margins:
        names = ", ".join(f"{hm(t['open_ts'])} {'BUY' if t['buy'] else 'SELL'} {t['edge']:g}" for t in saved[m]) or "nessuno"
        print(f"   +{m:g} $: {len(saved[m])} ({names}); gli altri {len(full_stops) - len(saved[m])} stop pieni "
              f"costano circa il {m / 10 * 100:.0f}% in più (con SL a 10 $)")


# ======================================================================
# RIGIOCO: i segnali del trader rigiocati sulle candele con regole diverse
# ======================================================================
#
# Non rigioca quello che ha fatto il bot (che ha cambiato regole quasi ogni
# giorno) ma i MESSAGGI del trader, così tutte le giornate sono confrontabili
# con le stesse regole: per ogni segnale ingresso nella zona, SL e TP scritti
# dal trader, e poi i suoi comandi (Trade Active, BE, close half, close all)
# all'ora in cui sono arrivati.
#
# Ogni strategia è la regola attuale con qualche parametro cambiato. Il rischio
# è fisso per segnale (--rischio, default 140 $ ≈ 2% di 7.000 $) così i giorni
# pesano uguale; i lotti sono calcolati come nel bot (rischio diviso fra i
# ticket, sul prezzo a cui ciascuno entra).

MAX_SIGNAL_DISTANCE = 20.0   # come risk_manager: oltre è un errore di battitura
DEFAULT_ZONE = 5.0           # zona assunta nella fase rapida
PROVISIONAL_SL = 10.0        # SL provvisorio finché il trader non scrive il suo
MIN_SL_DISTANCE = 3.0
STOPS_DISTANCE = 0.1         # distanza minima dal prezzo per spostare uno stop
REENTRY_PARENT_SECONDS = 3600
PARTIAL_CLOSE_REPEAT_SECONDS = 15 * 60  # come il bot: un secondo "close half" vicino è lo stesso comando
COMMAND_LINK_SECONDS = 6 * 3600

RE_TRADE_ACTIVE = re.compile(r"trade active|running|zero float", re.I)
RE_HIT_TP = re.compile(r"hit\s*tp", re.I)

# Tick reali dopo il segnale e dopo ogni comando del trader: le candele da un
# minuto non dicono a che prezzo il bot ha trovato il mercato, né se lo stop di
# protezione è stato toccato nei secondi successivi (06–08/10: protezioni
# colpite in 14–40 secondi). Fuori da queste finestre si usano le candele.
TICK_WINDOW_SECONDS = 180
BE_RETRY_SECONDS = 30      # il bot riprova pareggio e protezione ogni 30 secondi

BASE_RULES = dict(
    tickets=2,                 # 2 = e1/e2 con il rischio diviso a metà (dal 06/10); 4 = anche e3/e4
    deep=0.5,                  # con 4 ticket: lotto di e3/e4 rispetto a e1/e2
    entry_tol=0.0,             # e1/e2 a mercato anche se il prezzo è oltre il bordo di non più di tanto
    provisional_tp=(10.0, 20.0),  # TP provvisori dal prezzo del segnale finché il trader non scrive i suoi
    ta_close=2,                # ticket incassati al Trade Active (ne resta sempre 1)
    ta_min_gain=0.0,           # incasso al Trade Active solo con almeno questo guadagno (in $ di prezzo)
    be="trader",               # "trader" = pareggio quando lo chiede il trader, "none" = mai
    be_after_tp1=False,        # pareggio di tutti i ticket appena uno prende il TP1
    be_keys=("e1", "e2", "e3", "e4"),  # ticket che vanno a pareggio quando lo chiede il trader
    be_offset=0.5,             # pareggio = ingresso ± questo
    be_risk=None,              # invece del pareggio: stop a questa frazione della distanza dallo SL
    be_min=0.0,                # pareggio solo con almeno questo guadagno (altrimenti si aspetta)
    protect=(0.25, 0.5, 0.75), # gradini dello stop di protezione se il pareggio non è possibile
    sl_margin=0.0,             # margine oltre lo SL del trader
    expiry=30 * 60,            # ordini limite non eseguiti cancellati dopo
)

# nome: (sigla per il dettaglio, regole diverse da quelle attuali)
STRATEGIES = {
    "attuale":                    ("attuale", {}),
    "4 ticket (fino al 06/10)":   ("4 ticket", dict(tickets=4)),
    "senza protezione":           ("no prot", dict(protect=())),
    "protezione da 1/2":          ("prot 1/2", dict(protect=(0.5, 0.75))),
    "protezione solo 3/4":        ("prot 3/4", dict(protect=(0.75,))),
    "TA senza incasso":           ("TA no", dict(ta_close=0)),
    "TA incasso da +1 $":         ("TA +1$", dict(ta_min_gain=1.0)),
    "BE +1 $":                    ("BE +1$", dict(be_offset=1.0)),
    "ingresso fino a 0,5 $ oltre": ("ingr .5", dict(entry_tol=0.5)),
    "ingresso fino a 1 $ oltre":  ("ingr 1", dict(entry_tol=1.0)),
    "ingresso fino a 2 $ oltre":  ("ingr 2", dict(entry_tol=2.0)),
    "senza TP provvisori":        ("no TPprv", dict(provisional_tp=())),
    "mai BE":                     ("mai BE", dict(be="none", ta_close=0, protect=())),
}


def load_day_events(day: str) -> list:
    """
    Eventi del giorno (ora italiana) da tutti i diari: logs/<giorno>/journal*.jsonl
    e il formato fino al 23/09 (logs/journal_<giorno>.jsonl). Si leggono anche
    i giorni vicini: quando il server registrava in UTC gli eventi tra
    mezzanotte e le 02:00 italiane finivano nella cartella del giorno prima.
    """
    target = datetime.strptime(day, "%Y-%m-%d")
    paths = []
    for delta in (-1, 0, 1):
        d = (target + timedelta(days=delta)).strftime("%Y-%m-%d")
        paths += sorted(glob.glob(os.path.join(LOG_DIR, d, "journal*.jsonl")))
        paths.append(os.path.join(LOG_DIR, f"journal_{d}.jsonl"))
    seen, events = set(), []
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line in seen:
                    continue
                seen.add(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                local = to_local(datetime.fromisoformat(event["ts"]))
                if local.strftime("%Y-%m-%d") == day:
                    event["_t"] = local.timestamp()
                    events.append(event)
    events.sort(key=lambda e: e["_t"])
    return events


def _levels(data: dict):
    """Livelli scritti in un messaggio (solo quelli presenti), o None."""
    levels = {"entry_min": data.get("entry_min"), "entry_max": data.get("entry_max"),
              "sl": data.get("stop_loss"), "tps": [tp for tp in (data.get("take_profit") or []) if tp]}
    if levels["entry_min"] is None and levels["sl"] is None and not levels["tps"]:
        return None
    return levels


def levels_at(signal: dict, t: float) -> dict:
    """Livelli del segnale validi al tempo t (le modifiche si sommano)."""
    merged = {"entry_min": None, "entry_max": None, "sl": None, "tps": []}
    for vt, levels in signal["versions"]:
        if vt > t:
            break
        for key, value in levels.items():
            if value not in (None, []):
                merged[key] = value
    return merged


def extract_signals(events: list) -> list:
    """
    I segnali del trader con le loro modifiche e i comandi successivi, dalla
    classificazione dell'agente (evento CLASSIFIED, presente in tutti i diari).
    Un comando vale per il segnale a cui risponde (e per i suoi re-entry);
    senza risposta, per l'ultimo segnale nella stessa direzione.
    """
    texts, replies = {}, {}
    for e in events:
        if e["event"] == "MESSAGE":
            texts.setdefault(e.get("msg_id"), e.get("text") or "")
            replies.setdefault(e.get("msg_id"), e.get("reply_to"))

    signals, by_id, done = [], {}, set()
    for e in events:
        if e["event"] != "CLASSIFIED":
            continue
        intent, data, msg_id, t = e.get("intent"), e.get("data") or {}, e.get("msg_id"), e["_t"]
        details = data.get("update_details") or {}
        direction = data.get("direction")
        levels = _levels(data)
        text = texts.get(msg_id, "")

        if msg_id in by_id:  # modifica del messaggio del segnale (fase completa, correzioni)
            if levels:
                by_id[msg_id]["versions"].append((t, levels))
            continue

        if intent == "NEW_SIGNAL" and direction in ("BUY", "SELL"):
            signal = {"id": msg_id, "t": t, "buy": direction == "BUY", "versions": [], "events": [],
                      "label": text.split("\n")[0][:24], "parent": None}
            if data.get("entry_min") is None:
                # Re-entry ("Try buy again"): a mercato, SL e TP dell'ultimo segnale nella stessa direzione
                parents = [s for s in signals if s["buy"] == signal["buy"] and t - s["t"] <= REENTRY_PARENT_SECONDS]
                if not parents:
                    continue
                parent = parents[-1]
                inherited = levels_at(parent, t)
                signal["parent"] = parent["id"]
                signal["versions"].append((t, {"entry_min": None, "entry_max": None,
                                               "sl": inherited["sl"], "tps": inherited["tps"]}))
            else:
                signal["versions"].append((t, levels))
            signals.append(signal)
            by_id[msg_id] = signal
            continue

        # Comandi. HIT TP / HIT TP MAX = pareggio (regola dal 28/09, qualunque
        # cosa abbia deciso l'agente nei giorni prima).
        if RE_HIT_TP.search(text):
            kind = "BE"
        elif intent == "CLOSE_SIGNAL":
            kind = "CLOSE"
        elif details.get("close_percentage"):
            kind = "HALF"
        elif details.get("move_sl_to_be"):
            kind = "TA" if (data.get("running_pips") or RE_TRADE_ACTIVE.search(text)) else "BE"
        elif details.get("layer_target"):
            kind = "LAYER"  # "Secure first layer now"
        else:
            continue
        if (msg_id, kind) in done:
            continue
        done.add((msg_id, kind))

        target, ref, depth = None, replies.get(msg_id), 0
        while ref and depth < 5:
            if ref in by_id:
                target = by_id[ref]
                break
            ref, depth = replies.get(ref), depth + 1
        if target is None:
            candidates = [s for s in signals if t - s["t"] <= COMMAND_LINK_SECONDS
                          and (direction not in ("BUY", "SELL") or s["buy"] == (direction == "BUY"))]
            if not candidates:
                continue
            target = candidates[-1]
        family = [target] + [s for s in signals if s["parent"] == target["id"]]
        for s in family:
            if kind == "HALF" and any(k == "HALF" and t - et < PARTIAL_CLOSE_REPEAT_SECONDS for et, k, _ in s["events"]):
                continue
            s["events"].append((t, kind, details.get("close_percentage")))
    return signals


def _zone(signal_levels: dict, buy: bool):
    """(basso, alto) della zona; nella fase rapida 5 $ a favore del trader."""
    p, q = signal_levels["entry_min"], signal_levels["entry_max"]
    if p is None:
        return None
    if q is None:
        return (p - DEFAULT_ZONE, p) if buy else (p, p + DEFAULT_ZONE)
    return (min(p, q), max(p, q))


def _plan_levels(zone, buy: bool, deep: float) -> dict:
    """Prezzi limite come ENTRY_MODE=range: e1/e2 al bordo, e3/e4 a metà e a ¾ della zona."""
    low, high = zone
    edge, sign, width = (high, -1, high - low) if buy else (low, 1, high - low)
    plan = {"e1": edge, "e2": edge}
    if deep > 0:
        plan["e3"] = round(edge + sign * width * 0.5, 2)
        plan["e4"] = round(edge + sign * width * 0.75, 2)
    return plan


def _tp_for(key: str, tps: list, buy: bool, entry: float):
    valid = [tp for tp in tps if (tp > entry) == buy]
    if not valid:
        return None
    valid.sort(reverse=not buy)
    return valid[0] if key in ("e1", "e3") else valid[-1]


def download_ticks(mt5, start_utc: float, end_utc: float, offset: int) -> list:
    """
    Tick reali (tempo UTC, bid, ask) tra due istanti, o [] se il broker non li
    ha più (lo storico dei tick è più corto di quello delle candele).
    """
    copy_ticks = getattr(mt5, "copy_ticks_range", None)
    if copy_ticks is None:
        return []
    ticks = copy_ticks(SYMBOL, int(start_utc + offset), int(end_utc + offset) + 1, getattr(mt5, "COPY_TICKS_ALL", -1))
    if ticks is None or len(ticks) == 0:
        return []
    rows = ticks.tolist()
    try:  # via RPyC la lista è remota: la trasferiamo in un colpo solo
        import rpyc
        if isinstance(rows, rpyc.core.netref.BaseNetref):
            rows = rpyc.classic.obtain(rows)
    except ImportError:
        pass
    # campi: time, bid, ask, last, volume, time_msc, flags, volume_real
    out = []
    for r in rows:
        t = (r[5] / 1000.0 if r[5] else r[0]) - offset
        if r[1] > 0 and r[2] > 0 and start_utc <= t <= end_utc:
            out.append((t, r[1], r[2]))
    return out


def tick_windows(signals: list) -> list:
    """
    Finestre (inizio, fine) in cui servono i tick: dal minuto del segnale e di
    ogni comando del trader fino a TICK_WINDOW_SECONDS dopo, allineate ai minuti
    (la candela che contiene il comando viene sostituita per intero) e unite.
    """
    raw = []
    for signal in signals:
        for t in [signal["t"]] + [et for et, _, _ in signal["events"]]:
            start = t - t % 60
            end = (t + TICK_WINDOW_SECONDS) - (t + TICK_WINDOW_SECONDS) % 60 + 60
            raw.append((start, end))
    merged = []
    for start, end in sorted(raw):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build_timeline(bars: list, windows_ticks: list) -> list:
    """
    Punti di prezzo in ordine di tempo: le candele M1, sostituite dai tick reali
    nelle finestre in cui ci sono. Ogni punto è (tempo, durata, bid apertura,
    bid massimo, bid minimo, bid chiusura, spread): un tick ha durata 0 e i
    quattro bid uguali, con lo spread reale del momento.
    """
    covered = [(start, end) for (start, end), ticks in windows_ticks if ticks]
    points = []
    for bar_ts, o, h, l, c, spread in bars:
        if any(start <= bar_ts < end for start, end in covered):
            continue
        points.append((bar_ts, 60, o, h, l, c, spread * POINT))
    for (start, end), ticks in windows_ticks:
        for t, bid, ask in ticks:
            points.append((t, 0, bid, bid, bid, bid, ask - bid))
    points.sort(key=lambda p: p[0])
    return points


def simulate_signal(signal: dict, points: list, rules: dict, risk: float, hours: float) -> dict:
    """
    Rigioca un segnale con una strategia sui punti di prezzo (candele e tick).
    Su un tick i comandi del trader si applicano al prezzo esatto del momento;
    su una candela al prezzo di chiusura. Ritorna il risultato e i ticket.
    """
    buy = signal["buy"]
    end = signal["t"] + hours * 3600
    tickets, started, ta_done, applied = {}, False, False, 0
    events = sorted(signal["events"])
    next_event = 0
    be_wanted = be_all = False
    last_retry = None
    ticked_events = 0

    def pnl(ticket, exit_price):
        move = (exit_price - ticket["entry"]) if buy else (ticket["entry"] - exit_price)
        return move * ticket["lots"]

    def close(ticket, price, reason, fraction=1.0):
        ticket["realized"] += pnl(ticket, price) * fraction
        if fraction >= 0.999:
            ticket["state"], ticket["exit"], ticket["reason"] = "closed", price, reason
        else:
            ticket["lots"] *= (1 - fraction)

    def gain(ticket, bid, ask):
        return (bid - ticket["entry"]) if buy else (ticket["entry"] - ask)

    def stop_valid(sl, bid, ask):
        return (bid - sl >= STOPS_DISTANCE) if buy else (sl - ask >= STOPS_DISTANCE)

    def better(new_sl, old_sl):
        return new_sl > old_sl if buy else new_sl < old_sl

    def trader_sl(levels, worst_entry):
        sl = levels["sl"]
        if sl is not None and ((worst_entry - sl) if buy else (sl - worst_entry)) >= MIN_SL_DISTANCE:
            return sl - rules["sl_margin"] if buy else sl + rules["sl_margin"]
        return None

    def take_profits(levels):
        # TP del trader o, finché non li scrive, quelli provvisori dal prezzo del segnale
        if levels["tps"] or levels["entry_min"] is None or not rules["provisional_tp"]:
            return levels["tps"]
        sign = 1 if buy else -1
        return [levels["entry_min"] + sign * d for d in rules["provisional_tp"]]

    def try_breakeven(bid, ask):
        for ticket in tickets.values():
            if ticket["state"] != "open" or ticket["be"] or (not be_all and ticket["key"] not in rules["be_keys"]):
                continue
            be_price = ticket["entry"] + rules["be_offset"] if buy else ticket["entry"] - rules["be_offset"]
            if rules["be_risk"] is not None and not be_all:
                reach = rules["be_risk"] * abs(ticket["entry"] - ticket["orig_sl"])
                be_price = ticket["entry"] - reach if buy else ticket["entry"] + reach
            if gain(ticket, bid, ask) < rules["be_min"]:
                continue
            if stop_valid(be_price, bid, ask):
                if better(be_price, ticket["sl"]):
                    ticket["sl"] = be_price
                ticket["be"] = True
                continue
            distance = abs(ticket["entry"] - ticket["orig_sl"])
            for step in rules["protect"]:
                candidate = ticket["entry"] - step * distance if buy else ticket["entry"] + step * distance
                if stop_valid(candidate, bid, ask):
                    if better(candidate, ticket["sl"]):
                        ticket["sl"] = candidate
                    break

    for t, duration, o, h, l, c, sp in points:
        if t > end:
            break
        is_tick = duration == 0
        # Su una candela si entra all'apertura della prima candela intera dopo il
        # segnale; su un tick al primo prezzo dopo che il bot ha letto il messaggio.
        if t < (signal["t"] if is_tick else signal["t"] - signal["t"] % 60 + 60):
            continue
        ask_o, ask_h, ask_l, ask_c = o + sp, h + sp, l + sp, c + sp

        if not started:
            levels = levels_at(signal, t)
            market = ask_o if buy else o
            zone = _zone(levels, buy)
            if zone is not None and min(abs(market - zone[0]), abs(market - zone[1])) > MAX_SIGNAL_DISTANCE:
                if t - signal["t"] > 600:
                    break  # errore di battitura mai corretto
                continue
            keys = ("e1", "e2", "e3", "e4") if rules["tickets"] == 4 else ("e1", "e2")
            if zone is None:  # re-entry: tutti a mercato, lotto pieno
                plan = {k: None for k in keys}
            else:
                plan = _plan_levels(zone, buy, rules["deep"] if rules["tickets"] == 4 else 0)
            tol = rules["entry_tol"]

            def at_market(key, level):
                if level is None:
                    return True
                if (market <= level) if buy else (market >= level):
                    return True  # al livello o migliore
                # e1/e2 oltre il bordo dalla parte sbagliata, entro la tolleranza
                return key in ("e1", "e2") and ((market - level) if buy else (level - market)) <= tol

            prices = {k: (market if at_market(k, v) else v) for k, v in plan.items()}
            worst = min(prices.values()) if buy else max(prices.values())
            edge = (zone[1] if buy else zone[0]) if zone else market
            sl = trader_sl(levels, worst)
            if sl is None:
                sl = edge - PROVISIONAL_SL if buy else edge + PROVISIONAL_SL
            tps = take_profits(levels)
            for key, price in prices.items():
                distance = abs(price - sl)
                if distance <= 0:
                    continue
                if rules["tickets"] == 4:
                    share = 0.25 * (1.0 if key in ("e1", "e2") or zone is None else rules["deep"])
                else:
                    share = 0.5
                tickets[key] = {"key": key, "state": "open" if price == market else "pending", "price": price,
                                "entry": price, "sl": sl, "orig_sl": sl, "tp": _tp_for(key, tps, buy, price),
                                "lots": risk * share / distance, "realized": 0.0, "be": False,
                                "since": t, "reason": None, "exit": None}
            started = True
            applied = sum(1 for vt, _ in signal["versions"] if vt <= t)

        # Modifiche del trader arrivate dopo l'ingresso (fase completa, correzioni)
        while applied < len(signal["versions"]) and signal["versions"][applied][0] <= t:
            applied += 1
            levels = levels_at(signal, t)
            zone = _zone(levels, buy)
            plan = _plan_levels(zone, buy, rules["deep"] if rules["tickets"] == 4 else 0) if zone else {}
            tps = take_profits(levels)
            for key, ticket in tickets.items():
                if ticket["state"] not in ("open", "pending"):
                    continue
                if ticket["state"] == "pending" and key in plan:
                    ticket["price"] = ticket["entry"] = plan[key]
                new_sl = trader_sl(levels, ticket["entry"])
                if new_sl is not None and not ticket["be"]:
                    ticket["sl"] = ticket["orig_sl"] = new_sl
                tp = _tp_for(key, tps, buy, ticket["entry"])
                if tp is not None:
                    ticket["tp"] = tp

        # Ordini limite eseguiti o scaduti
        for ticket in tickets.values():
            if ticket["state"] != "pending":
                continue
            if t - ticket["since"] > rules["expiry"]:
                ticket["state"] = "cancelled"
            elif (ask_l <= ticket["price"]) if buy else (h >= ticket["price"]):
                ticket["state"] = "open"

        # SL e TP (nella stessa candela prima lo SL, per prudenza)
        tp1_hit = False
        for ticket in tickets.values():
            if ticket["state"] != "open":
                continue
            if (l <= ticket["sl"]) if buy else (ask_h >= ticket["sl"]):
                reason = "BE" if ticket["be"] else ("protezione" if ticket["sl"] != ticket["orig_sl"] else "SL")
                close(ticket, ticket["sl"], reason)
            elif ticket["tp"] is not None and ((h >= ticket["tp"]) if buy else (ask_l <= ticket["tp"])):
                close(ticket, ticket["tp"], "TP")
                tp1_hit = tp1_hit or ticket["key"] in ("e1", "e3")
        if tp1_hit and rules["be_after_tp1"]:
            be_wanted, be_all = True, True

        # Comandi del trader: su un tick quelli arrivati fino a quell'istante, al
        # suo prezzo; su una candela quelli arrivati durante la candela, alla chiusura.
        while next_event < len(events) and (events[next_event][0] <= t if is_tick else events[next_event][0] < t + 60):
            _t, kind, percentage = events[next_event]
            next_event += 1
            ticked_events += is_tick
            open_tickets = [x for x in tickets.values() if x["state"] == "open"]
            if kind == "CLOSE":
                for ticket in tickets.values():
                    if ticket["state"] == "pending":
                        ticket["state"] = "cancelled"
                for ticket in open_tickets:
                    close(ticket, c if buy else ask_c, "chiusura")
            elif kind == "HALF":
                for ticket in tickets.values():
                    if ticket["state"] == "pending":
                        ticket["state"] = "cancelled"
                total = sum(x["lots"] for x in open_tickets)
                left = total * (percentage or 50) / 100
                far_first = sorted(open_tickets, key=lambda x: -abs((x["tp"] or (x["entry"] + 1000)) - x["entry"]))
                for ticket in far_first:
                    if left <= 1e-9 or gain(ticket, c, ask_c) <= 0:
                        continue
                    fraction = min(1.0, left / ticket["lots"])
                    left -= ticket["lots"] * fraction
                    close(ticket, c if buy else ask_c, "metà", fraction)
            elif kind == "LAYER":
                # "Secure first layer": i ticket sul TP1, solo se in guadagno
                for ticket in open_tickets:
                    if ticket["key"] in ("e1", "e3") and gain(ticket, c, ask_c) > 0:
                        close(ticket, c if buy else ask_c, "layer")
            elif kind in ("TA", "BE") and rules["be"] == "trader":
                for ticket in tickets.values():
                    if ticket["state"] == "pending":
                        ticket["state"] = "cancelled"
                if kind == "TA" and not ta_done and rules["ta_close"] > 0:
                    # Peggiore per primo; ingressi entro 0,50 $ = stesso prezzo, prima il TP1
                    worst_first = sorted(open_tickets, key=lambda x: (-x["entry"]) if buy else x["entry"])
                    if worst_first:
                        worst_entry = worst_first[0]["entry"]
                        worst_first.sort(key=lambda x: (0, x["key"] not in ("e1", "e3"))
                                         if abs(x["entry"] - worst_entry) <= 0.5 else (1, 0))
                    count = min(rules["ta_close"], len(open_tickets) - 1)
                    for ticket in worst_first[:max(0, count)]:
                        if gain(ticket, c, ask_c) > rules["ta_min_gain"]:
                            close(ticket, c if buy else ask_c, "incasso TA")
                            ta_done = True
                be_wanted = True
                try_breakeven(c, ask_c)  # primo tentativo subito, come il bot
                last_retry = t

        # Pareggio (o stop di protezione) ritentato ogni 30 secondi, come il bot
        if be_wanted and (last_retry is None or not is_tick or t - last_retry >= BE_RETRY_SECONDS):
            try_breakeven(c, ask_c)
            last_retry = t

        if started and not any(x["state"] in ("open", "pending") for x in tickets.values()):
            break

    last = None
    for point in points:
        if point[0] <= end:
            last = point
    for ticket in tickets.values():
        if ticket["state"] == "open" and last:
            close(ticket, last[5] if buy else last[5] + last[6], "fine")
    result = sum(x["realized"] for x in tickets.values())
    full_stop = any(x["reason"] == "SL" for x in tickets.values()) and result <= -0.5 * risk
    return {"result": result, "started": started, "full_stop": full_stop, "tickets": tickets,
            "ticked_events": ticked_events, "events": len(events)}


def real_result(events: list) -> float:
    return sum(e.get("profit") or 0.0 for e in events if e["event"] == "POSITION_CLOSED")


def days_between(first: str, last: str) -> list:
    day, stop = datetime.strptime(first, "%Y-%m-%d"), datetime.strptime(last, "%Y-%m-%d")
    out = []
    while day <= stop:
        out.append(day.strftime("%Y-%m-%d"))
        day += timedelta(days=1)
    return out


def main_rigioco(argv: list) -> None:
    parser = argparse.ArgumentParser(
        prog="prezzi.py rigioco",
        description="Rigioca i segnali del trader sulle candele M1 (e sui tick reali attorno ai comandi) "
                    "con regole diverse e confronta i risultati.")
    parser.add_argument("dal", help="AAAA-MM-GG")
    parser.add_argument("al", nargs="?", help="AAAA-MM-GG (default: lo stesso giorno)")
    parser.add_argument("--strategie", default="tutte",
                        help="nomi separati da virgola, o 'tutte' (" + ", ".join(STRATEGIES) + ")")
    parser.add_argument("--rischio", type=float, default=140.0, help="rischio per segnale in $ (default 140)")
    parser.add_argument("--ore", type=float, default=8, help="ore massime di vita di un segnale (default 8)")
    parser.add_argument("--senza-tick", action="store_true", help="solo candele M1, senza scaricare i tick")
    parser.add_argument("--dettaglio", action="store_true", help="risultato di ogni segnale per ogni strategia")
    parser.add_argument("--offset-broker", type=float, help="ore del broker rispetto a UTC (default: stimate da MT5)")
    args = parser.parse_args(argv)

    names = list(STRATEGIES) if args.strategie == "tutte" else [n.strip() for n in args.strategie.split(",")]
    unknown = [n for n in names if n not in STRATEGIES]
    if unknown:
        raise SystemExit(f"Strategie sconosciute: {', '.join(unknown)}")
    rules = {n: {**BASE_RULES, **STRATEGIES[n][1]} for n in names}

    mt5 = connect_mt5()
    offset = int(args.offset_broker * 3600) if args.offset_broker is not None else estimate_broker_offset(mt5)
    days = days_between(args.dal, args.al or args.dal)
    print(f"Rigioco dal {days[0]} al {days[-1]} · rischio {args.rischio:g} $ per segnale · "
          f"vita massima {args.ore:g} ore · orologio del broker UTC{offset / 3600:+.1f} h")
    print("Regole attuali: 2 ticket (1% ciascuno), ingresso al bordo della zona, TP provvisori 10/20 $, "
          "Trade Active incassa 1 ticket, pareggio +0,5 $, protezione 1/4-1/2-3/4\n")

    totals = {n: {"days": {}, "wins": [], "losses": [], "stops": 0, "signals": 0} for n in names}
    real, tick_stats = {}, {}
    for day in days:
        events = load_day_events(day)
        signals = extract_signals(events)
        if not signals:
            continue
        real[day] = real_result(events)
        start = min(s["t"] for s in signals) - 120
        stop = min(time.time(), max(s["t"] for s in signals) + args.ore * 3600)
        bars = download_bars(mt5, start, stop, offset)
        windows = [] if args.senza_tick else tick_windows(signals)
        windows_ticks = [((a, b), download_ticks(mt5, a, b, offset)) for a, b in windows]
        tick_stats[day] = (sum(1 for _, ticks in windows_ticks if ticks), len(windows_ticks))
        points = build_timeline(bars, windows_ticks)
        if args.dettaglio:
            print(f"── {day} · {len(signals)} segnali · tick in {tick_stats[day][0]} finestre su {tick_stats[day][1]}")
        for signal in signals:
            row = []
            for name in names:
                outcome = simulate_signal(signal, points, rules[name], args.rischio, args.ore)
                tot = totals[name]
                tot["days"][day] = tot["days"].get(day, 0.0) + outcome["result"]
                if outcome["started"]:
                    tot["signals"] += 1
                    (tot["wins"] if outcome["result"] > 0 else tot["losses"]).append(outcome["result"])
                    tot["stops"] += outcome["full_stop"]
                row.append(outcome["result"] if outcome["started"] else None)
            if args.dettaglio:
                cells = " ".join(f"{v:>8.1f}" if v is not None else f"{'—':>8}" for v in row)
                print(f"   {hm(signal['t'])} {'BUY' if signal['buy'] else 'SELL':4} {signal['label']:24} {cells}")
        if args.dettaglio:
            print("   " + " " * 35 + " ".join(f"{STRATEGIES[n][0][:8]:>8}" for n in names) + "\n")

    shown = [d for d in days if d in real]
    header = f"{'strategia':28}" + "".join(f"{d[8:10] + '/' + d[5:7]:>8}" for d in shown) + \
             f"{'totale':>9}{'vinti':>7}{'persi':>7}{'stop':>6}{'media +':>9}{'media −':>9}"
    print(header + "\n" + "─" * len(header))
    print(f"{'reale (registrato)':28}" + "".join(f"{real[d]:>8.0f}" for d in shown) + f"{sum(real.values()):>9.0f}")
    for name in names:
        tot = totals[name]
        wins, losses = tot["wins"], tot["losses"]
        print(f"{name:28}" + "".join(f"{tot['days'].get(d, 0.0):>8.0f}" for d in shown)
              + f"{sum(tot['days'].values()):>9.0f}{len(wins):>7}{len(losses):>7}{tot['stops']:>6}"
              + f"{(sum(wins) / len(wins) if wins else 0):>9.1f}{(sum(losses) / len(losses) if losses else 0):>9.1f}")
    print("\nstop = segnali chiusi allo SL pieno; media + / media − = risultato medio dei segnali vinti / persi.")
    print("'reale' è la somma delle chiusure registrate dal bot (con le regole di quel giorno e i lotti veri).")
    with_ticks = sum(a for a, _ in tick_stats.values())
    all_windows = sum(b for _, b in tick_stats.values())
    print(f"Tick reali disponibili in {with_ticks} finestre su {all_windows} (segnali e comandi del trader); "
          f"altrove candele M1.")
    for d in shown:
        if tick_stats[d][1] and not tick_stats[d][0]:
            print(f"   {d}: nessun tick dal broker, solo candele")


if __name__ == "__main__":
    main()
