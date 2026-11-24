"""
Agente che non risponde (09–11/11).

Dal 09/11 alle 03:05 all'11/11 alle 19:37 ogni chiamata all'agente è fallita:
credito Anthropic esaurito (errore 400), poi chiave non valida (401). Il bot
scriveva "❌ Errore in process_message" per ogni messaggio, senza il testo del
trader, e andava avanti: circa 33 segnali persi, e STATO.md diceva "Tutto
regolare". Ora il primo errore apre lo stato "agente non raggiungibile"
(AGENT_DOWN), ogni messaggio perso compare nel racconto con il suo testo
(AGENT_ERROR), il battito lo ripete e la prima chiamata riuscita lo chiude
(AGENT_UP). STATO.md legge queste righe (test_stato.py).
"""
import banco
from banco import *


class BadRequestError(Exception):
    """Come anthropic.BadRequestError: il bot non deve dipendere dal tipo."""


def agente_rotto(*a, **k):
    raise BadRequestError("Error code: 400 - Your credit balance is too low to access the Anthropic API.")


agente_buono = L.agent_classify_telegram_message


def eventi(kind):
    return [e for e in diario() if e["event"] == kind]


scenario("Credito esaurito: il segnale non apre nulla, il racconto dice perché e con quale messaggio")
broker.reset(price=4125.0)
L.agent_classify_telegram_message = agente_rotto
invia(1, "Gold buy 4125", None)
check(len(broker.positions) == 0 and len(broker.orders) == 0, "nessun ordine",
      (len(broker.positions), len(broker.orders)))
down = eventi("AGENT_DOWN")
check(len(down) == 1 and down[0].get("where") == "classificazione" and "credit balance" in str(down[0].get("error"))
      and down[0].get("msg_id") == 1, "diario: AGENT_DOWN con l'errore e il messaggio", down)
check(not eventi("ERROR"), "nessun ERROR generico al posto dell'AGENT_DOWN", eventi("ERROR"))
check("📩 Trader: \"Gold buy 4125\"" in racconto(), "racconto: il testo del messaggio perso")
check("🚨 Agente non raggiungibile: messaggio non classificato · BadRequestError" in racconto(),
      "racconto: \"🚨 Agente non raggiungibile\" con il tipo di errore")

scenario("Altri messaggi persi: contati, con l'ora dell'inizio del guasto")
invia(1, "GOLD BUY NOW · Buy @ 4125 - 4120 · SL 4115 · TP 4135 · TP 4145", None, edit=True)
invia(2, "Trade Active ✅ · Gold Buy Running 50+ Pips", None, reply_to=1)
errs = eventi("AGENT_ERROR")
since = errs[-1].get("since") if errs else None
check(len(errs) == 2 and errs[-1].get("messages") == 3 and since and since[:16] == down[0]["ts"][:16],
      "diario: AGENT_ERROR con 3 messaggi persi e l'inizio del guasto", errs)
check(len(eventi("AGENT_DOWN")) == 1, "un solo AGENT_DOWN per tutto il guasto")
check("✏️ Trader modifica: \"GOLD BUY NOW" in racconto() and "(3°)" in racconto(),
      "racconto: la modifica persa e il numero dei messaggi persi")

scenario("Battito durante il guasto: lo ripete")
L.write_heartbeat(True, "MT5 operativo")
beat = eventi("HEARTBEAT")[-1]
check(beat.get("agent_down_since") and beat.get("agent_lost_messages") == 3, "diario: HEARTBEAT con il guasto", beat)
riga = [r for r in racconto().splitlines() if "💓" in r][-1]
check("🚨 Agente non raggiungibile dal" in riga and "3 messaggi non classificati" in riga,
      "racconto: il battito dice \"Agente non raggiungibile\"", riga)

scenario("Riscaldamento fallito durante il guasto: registrato, non conta come messaggio perso")
banco.L.warm_up_agent = lambda: agente_rotto()
L._last_agent_call = None
media(3, "gif")
errs = eventi("AGENT_ERROR")
check(errs[-1].get("where") == "riscaldamento" and errs[-1].get("messages") == 3,
      "diario: AGENT_ERROR del riscaldamento, sempre 3 messaggi persi", errs[-1])

scenario("Credito ricaricato: la stessa modifica viene riprovata, il guasto si chiude")
L.agent_classify_telegram_message = agente_buono
banco.L.warm_up_agent = banco._riscaldamento_finto
invia(1, "GOLD BUY NOW · Buy @ 4125 - 4120 · SL 4115 · TP 4135 · TP 4145",
      classe("NEW_SIGNAL", direction="BUY", entry_min=4120.0, entry_max=4125.0, stop_loss=4115.0,
             take_profit=[4135.0, 4145.0]), edit=True)
up = eventi("AGENT_UP")
check(len(up) == 1 and up[0].get("messages") == 3 and up[0].get("since") == eventi("AGENT_DOWN")[0].get("since"),
      "diario: AGENT_UP con i messaggi persi e l'inizio del guasto", up)
check(len(broker.positions) + len(broker.orders) == 2,
      "la modifica con lo stesso testo apre il segnale (non scartata come identica)",
      (len(broker.positions), len(broker.orders)))
check("✅ Agente di nuovo raggiungibile · non rispondeva dal" in racconto()
      and "3 messaggi non classificati" in racconto(), "racconto: \"✅ Agente di nuovo raggiungibile\"")
L.write_heartbeat(True, "MT5 operativo")
beat = eventi("HEARTBEAT")[-1]
check("agent_down_since" not in beat, "battito dopo: nessun guasto", beat)
check("🚨" not in [r for r in racconto().splitlines() if "💓" in r][-1], "racconto: battito senza 🚨")
pulisci()

scenario("Guasto aperto da un riscaldamento, chiuso dal riscaldamento dopo")
banco.L.warm_up_agent = lambda: agente_rotto()
L._last_agent_call = None
media(10, "gif")
down = eventi("AGENT_DOWN")
check(len(down) == 2 and down[-1].get("where") == "riscaldamento", "diario: AGENT_DOWN dal riscaldamento", down[-1])
check("🚨 Agente non raggiungibile: riscaldamento non riuscito" in racconto(), "racconto: riscaldamento non riuscito")
banco.L.warm_up_agent = banco._riscaldamento_finto
L._last_agent_call = None
media(11, "gif")
up = eventi("AGENT_UP")
check(len(up) == 2 and up[-1].get("messages") == 0, "diario: AGENT_UP, nessun messaggio perso", up[-1])

fine()
