"""
GIF del trader prima dei segnali (dal 20/10).

Prima di quasi ogni segnale il trader pubblica una GIF senza testo, sempre la
stessa: dal 23/09 al 20/10 188 segnali rapidi su 196, da pochi secondi a 5
minuti prima. Il bot non la classifica e non apre nulla, ma registra nel diario
tipo di media e prezzo di quel momento e riscalda l'agente (chiamata con
max_tokens=0 e lo stesso prompt), se non è stato chiamato negli ultimi 5 minuti:
il primo segnale della mattina aspettava l'agente 7-9 secondi invece di 3.
"""
import importlib.util
import os
import sys
import types

import banco
from banco import *


def eventi(msg_id, kind=None):
    return [e for e in diario() if e.get("msg_id") == msg_id and (kind is None or e["event"] == kind)]


scenario("GIF a mercato aperto, agente fermo: prezzo nel diario, agente riscaldato, nessun ordine")
broker.reset(price=4121.40)
L._last_agent_call = None
riscaldamenti.clear()
media(100, "gif", media_id=555)
skip = eventi(100, "MESSAGE_SKIPPED")
skip = skip[0] if skip else {}
check(skip.get("media") == "gif" and skip.get("media_id") == 555, "diario: media gif con il suo id", skip)
check(vicino(skip.get("bid"), 4121.40) and vicino(skip.get("ask"), 4121.60) and skip.get("broker_time"),
      "diario: bid 4.121,40, ask 4.121,60 e ora del broker", skip)
check(skip.get("warmup") is True and len(riscaldamenti) == 1, "agente riscaldato una volta", len(riscaldamenti))
warm = eventi(100, "AGENT_WARMUP")
check(len(warm) == 1 and warm[0].get("ok") and "seconds" in warm[0], "diario: AGENT_WARMUP riuscito con i secondi", warm)
check("GIF del trader" in racconto() and "4.121,40" in racconto(), "racconto: riga della GIF con il prezzo")
check(not broker.positions and not broker.orders, "nessun ordine alla GIF")

scenario("Segnale dopo la GIF, poi un'altra GIF: agente appena chiamato, nessun nuovo riscaldamento")
segnale(101, "SELL", 4121, 4121, 4126, 4131, [4111, 4101])
check(len(broker.positions) == 2, "il segnale apre e1 ed e2 come sempre", len(broker.positions))
media(102, "gif")
skip = eventi(102, "MESSAGE_SKIPPED")[0]
check(skip.get("warmup") is False and len(riscaldamenti) == 1, "seconda GIF: nessun riscaldamento", skip)
check(not eventi(102, "AGENT_WARMUP"), "nessun AGENT_WARMUP per la seconda GIF")

scenario("GIF dopo più di 5 minuti senza chiamate all'agente: di nuovo riscaldato")
L._last_agent_call -= L.AGENT_WARMUP_MIN_GAP_SECONDS + 1
media(103, "gif")
check(eventi(103, "MESSAGE_SKIPPED")[0].get("warmup") is True and len(riscaldamenti) == 2,
      "riscaldato di nuovo", len(riscaldamenti))
pulisci()

scenario("Foto senza testo: prezzo nel diario, nessun riscaldamento, niente nel racconto")
L._last_agent_call = None
gif_lines = racconto().count("GIF del trader")
media(104, "photo", media_id=999)
skip = eventi(104, "MESSAGE_SKIPPED")[0]
check(skip.get("media") == "photo" and vicino(skip.get("bid"), 4121.40), "diario: photo con il prezzo", skip)
check("warmup" not in skip and len(riscaldamenti) == 2, "nessun riscaldamento per una foto", skip)
check(racconto().count("GIF del trader") == gif_lines, "nessuna riga nel racconto per una foto")

scenario("Riscaldamento fallito: registrato, il bot continua")


def _riscaldamento_rotto():
    raise RuntimeError("API non raggiungibile")


L.warm_up_agent = _riscaldamento_rotto
media(105, "gif")
warm = eventi(105, "AGENT_WARMUP")
check(len(warm) == 1 and warm[0].get("ok") is False and "API non raggiungibile" in str(warm[0].get("error")),
      "diario: AGENT_WARMUP fallito con l'errore", warm)
L.warm_up_agent = banco._riscaldamento_finto
segnale(106, "BUY", 4122, 4117, 4122, 4112, [4132, 4142])
check(len(broker.positions) == 2, "il segnale dopo apre normalmente", len(broker.positions))
pulisci()

scenario("GIF con MT5 scollegato: nessun prezzo (con il motivo), agente riscaldato lo stesso")
L._last_agent_call = None
broker.connected = False
media(107, "gif")
broker.connected = True
skip = eventi(107, "MESSAGE_SKIPPED")[0]
check("bid" not in skip and "non collegato" in str(skip.get("no_price_reason")), "diario: motivo del prezzo mancante", skip)
check(skip.get("warmup") is True, "agente riscaldato", skip)
check("prezzo non letto" in racconto(), "racconto: \"prezzo non letto\"")

scenario("Modifica di un messaggio senza testo: scartata come prima, nessun riscaldamento")
L._last_agent_call = None
before = len(riscaldamenti)
media(108, "gif", edit=True)
skip = eventi(108, "MESSAGE_SKIPPED")[0]
check("media" not in skip and len(riscaldamenti) == before, "nessun prezzo, nessun riscaldamento", skip)

scenario("GIF a mercato chiuso: nessuna chiamata a MT5 né all'agente")
L.is_market_time_open = lambda: (False, "mercato chiuso (weekend)")
media(109, "gif")
L.is_market_time_open = lambda: (True, "")
skip = eventi(109, "MESSAGE_SKIPPED")[0]
check(skip.get("no_price_reason") == "mercato chiuso (weekend)" and "warmup" not in skip
      and len(riscaldamenti) == before, "solo il motivo, nessun riscaldamento", skip)

scenario("Agente vero: il riscaldamento usa lo stesso prompt e gli stessi tools della classificazione")
calls = []


class _Messages:
    def create(self, **kwargs):
        calls.append(kwargs)
        usage = types.SimpleNamespace(input_tokens=500, output_tokens=0, cache_read_input_tokens=5658,
                                      cache_creation_input_tokens=0)
        content = [] if kwargs["max_tokens"] == 0 else [
            types.SimpleNamespace(type="tool_use", name="classify_signal", input={"intent": "IGNORE"})]
        return types.SimpleNamespace(usage=usage, content=content, _request_id="req_finto",
                                     stop_reason="max_tokens" if not content else "tool_use")


class _Anthropic:
    def __init__(self, *a, **k):
        self.messages = _Messages()


_fake_anthropic = types.ModuleType("anthropic")
_fake_anthropic.Anthropic = _Anthropic
_saved_anthropic = sys.modules.get("anthropic")
sys.modules["anthropic"] = _fake_anthropic
try:
    _spec = importlib.util.spec_from_file_location("agent_classifier_vero", os.path.join(ROOT, "agent_classifier.py"))
    vero = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(vero)
finally:
    if _saved_anthropic is not None:
        sys.modules["anthropic"] = _saved_anthropic
    else:
        sys.modules.pop("anthropic", None)

result = vero.warm_up_agent()
vero.agent_classify_telegram_message("Gold sell 4121", is_edit=False, reply_to=None, has_media=False,
                                     is_forwarded=False, timestamp=None)
warm_call, real_call = calls
check(warm_call["max_tokens"] == 0 and "tool_choice" not in warm_call,
      "riscaldamento: max_tokens 0, senza tool_choice forzato (rifiutato dall'API con max_tokens 0)")
check(warm_call["model"] == real_call["model"] and warm_call["system"] == real_call["system"]
      and warm_call["tools"] == real_call["tools"], "stessi modello, prompt di sistema e tools: la cache è la stessa")
check(real_call["system"][0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}, "cache di 1 ora sul prompt")
check(result.get("cache_read_input_tokens") == 5658, "il riscaldamento riporta i token della cache", result)
call_event = [e for e in diario() if e["event"] == "CLASSIFIER_CALL"]
check(call_event and "seconds" in call_event[-1], "diario: la classificazione registra i secondi di attesa",
      call_event[-1] if call_event else None)

fine()
