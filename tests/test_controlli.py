"""
Reti di sicurezza: layer chiuso su MT5, posizioni orfane riprese, SL rimesso,
chiusure del broker registrate, racconto all'avvio.
"""
from banco import *


def layer(msg_id, text="Secure first layer now", target="LOWEST", reply_to=None):
    invia(msg_id, text, classe("UPDATE_SIGNAL", layer=target), reply_to=reply_to)


scenario("\"Secure first layer now\" in guadagno: e1 chiuso davvero su MT5 (08/10)")
broker.reset(price=4125.26)
segnale(1, "SELL", 4125, 4125, 4130, 4135, [4115, 4105])
broker.move_price(4120.0)
layer(2)
check(posizione(1, "e1") is None and ticket(1, "e1").get("closed"), "e1 chiuso su MT5 e in memoria")
check(posizione(1, "e2") is not None, "e2 resta aperto")
scenario("Layer con il prezzo in perdita: niente chiusura")
broker.move_price(4127.0)
layer(3, "close lowest layer", target="HIGHEST")
check(posizione(1, "e2") is not None and not ticket(1, "e2").get("closed"), "e2 ancora aperto, mai chiuso in perdita")
pulisci()

scenario("Ticket segnato chiuso per errore ma aperto su MT5: ripreso in gestione")
broker.reset(price=4125.26)
segnale(4, "SELL", 4125, 4125, 4130, 4135, [4115, 4105])
ticket(4, "e1")["closed"] = True
L.manager.save_state_to_file()
controllo_periodico()
check(not ticket(4, "e1").get("closed"), "e1 di nuovo seguito dal bot")
check("ripreso in gestione" in racconto(), "racconto: \"ripreso in gestione\"")
pulisci()

scenario("Operazione intera chiusa e archiviata per errore: ripresa dallo storico")
broker.reset(price=4125.26)
segnale(5, "SELL", 4125, 4125, 4130, 4135, [4115, 4105])
for t in operazione(5)["tickets"].values():
    t["closed"] = True
operazione(5)["status"] = "CLOSED"
L.manager.save_state_to_file()                   # archivia le operazioni chiuse
check(operazione(5) is None, "operazione uscita dalla memoria (archiviata)")
controllo_periodico()
check(operazione(5) is not None and operazione(5).get("status") == "ACTIVE", "ripresa dallo storico come ACTIVE")
pulisci()

scenario("Posizione del bot sconosciuta: segnalata una sola volta; posizioni manuali ignorate")
broker.reset(price=4125.26)
broker.add_position(77777, "SELL", 4125.0, sl=4135.0, tp=4115.0)
broker.add_position(88888, "SELL", 4125.0, magic=0, comment="manuale")
controllo_periodico()
controllo_periodico()
unknown = [e for e in diario() if e["event"] == "ORPHAN_UNKNOWN"]
check([e.get("mt5_ticket") for e in unknown] == [77777], "solo #77777 segnalata, una volta",
      [e.get("mt5_ticket") for e in unknown])
pulisci()

scenario("SL tolto sul broker: rimesso dal controllo periodico (07/10)")
broker.reset(price=4136.02)
segnale(6, "SELL", 4133, 4134, 4139, 4144, [4124, 4114])
for p in broker.positions.values():
    p.sl = 0.0
controllo_periodico()
check(all(p.sl == 4144.0 for p in broker.positions.values()), "SL 4144 di nuovo su tutte le posizioni",
      [p.sl for p in broker.positions.values()])
pulisci()

scenario("TP preso dal broker: chiusura registrata con il profitto")
broker.reset(price=4185.0)
segnale(7, "BUY", 4186, 4181, 4186, 4176, [4196, 4206])
broker.broker_close(ticket(7, "e1")["mt5_ticket"], "TP")
controllo_periodico()
closes = [e for e in diario() if e["event"] == "POSITION_CLOSED" and e.get("mt5_ticket") == ticket(7, "e1")["mt5_ticket"]]
check(ticket(7, "e1").get("closed"), "e1 chiuso in memoria")
check(closes and closes[0].get("close_reason") == "TAKE_PROFIT" and vicino(closes[0].get("profit"), 75.6, 0.01),
      "diario: chiusura al TP con +75,60 $", closes and (closes[0].get("close_reason"), closes[0].get("profit")))
pulisci()

scenario("Racconto all'avvio: conto DEMO o non leggibile, mai \"REALE\" per errore")
n = narrative.Narrator()
said = []
n.say = said.append
n.on_bot_start({})
n.on_bot_start({"demo": True, "balance": 7000.0, "equity": 7000.0})
check("non leggibile" in said[0] and "REALE" not in said[0], "conto non letto → \"non leggibile\"", said[0])
check("DEMO" in said[1], "conto demo → \"conto DEMO\"", said[1])

scenario("Cartelle dei log: tenute un anno sul server, non 30 giorni (il rigioco ci legge, 29/10)")
import logger_config  # noqa: E402
from datetime import timedelta  # noqa: E402


def cartella(giorni_fa):
    day = (logger_config.now_local() - timedelta(days=giorni_fa)).strftime("%Y-%m-%d")
    path = os.path.join(logger_config.LOG_DIR, day)
    os.makedirs(path, exist_ok=True)
    open(os.path.join(path, "journal.jsonl"), "w").close()
    return path


mese, anno = cartella(40), cartella(400)
logger_config.remove_old_log_folders()
check(os.path.isdir(mese), "la cartella di 40 giorni fa resta (il 28/09 cancellato il 29/10)")
check(not os.path.isdir(anno), "quella di 400 giorni fa viene cancellata")

fine()
