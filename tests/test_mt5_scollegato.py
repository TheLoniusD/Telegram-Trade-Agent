"""
Ingresso con MT5 scollegato (24/11).

Il 17/11 MT5 si è scollegato dal broker alle 09:08:11; "Gold buy 3867" è
arrivato alle 09:09 e la sua modifica ("Buy @ 3866 - 3861") alle 09:11: MT5 ha
rifiutato tutti gli ordini ("absence of network connection", retcode 10031),
l'operazione è rimasta OPEN_FAILED e il recupero ha provato di nuovo con la
modifica. MT5 è tornato alle 09:13:42, il bot non ha riprovato; il trader ha
scritto Trade Active 40 pips alle 09:15. Ora l'operazione aspetta MT5 per 5
minuti dall'arrivo del segnale e il controllo periodico la riprova con le regole
di sempre; la modifica aggiorna i livelli; un comando del trader la annulla.
"""
import time

from banco import *


def eventi(kind):
    return [e for e in diario() if e["event"] == kind]


scenario("17/11: segnale con MT5 scollegato dal broker → in attesa, nessun ordine")
broker.reset(price=3867.0)
broker.broker_online = False
invia(1, "Gold buy 3867", classe("NEW_SIGNAL", direction="BUY", entry_min=3867.0))
check(not broker.positions and not broker.orders, "nessun ordine", (len(broker.positions), len(broker.orders)))
check(operazione(1) and operazione(1)["status"] == "WAITING_MT5", "operazione in attesa di MT5",
      operazione(1) and operazione(1)["status"])
check(len(eventi("ENTRY_WAIT")) == 1, "diario: ENTRY_WAIT", eventi("ENTRY_WAIT"))
check("⏳ [BUY 3867] MT5 scollegato: ingresso in attesa" in racconto(), "racconto: ingresso in attesa")
check("nessun ordine aperto" not in racconto(), "racconto: niente \"nessun ordine aperto\" durante l'attesa")

scenario("La modifica del trader aggiorna l'operazione in attesa, nessun secondo tentativo")
invia(1, "GOLD BUY NOW · Buy @ 3866 - 3861 · SL🔴3856 · TP✅3876 · TP✅3886",
      classe("UPDATE_SIGNAL", direction="BUY", entry_min=3861.0, entry_max=3866.0, stop_loss=3856.0,
             take_profit=[3876.0, 3886.0]), edit=True)
trade = operazione(1)
check(trade["status"] == "WAITING_MT5" and trade["entry_max"] == 3866.0 and trade["stop_loss"] == 3856.0,
      "zona 3861–3866 e SL 3856 sull'operazione in attesa", (trade["status"], trade.get("entry_max"), trade.get("stop_loss")))
check(sorted(t["take_profit"] for t in trade["tickets"].values()) == [3876.0, 3886.0], "TP 3876 / 3886")
check(len(eventi("ENTRY_WAIT")) == 1 and len(L.manager.active_trades) == 1, "una sola operazione, un solo ENTRY_WAIT")
check(not broker.orders and not broker.positions, "ancora nessun ordine")

scenario("Controllo periodico con MT5 ancora scollegato: si aspetta")
controllo_periodico()
check(operazione(1)["status"] == "WAITING_MT5" and not eventi("ENTRY_RETRY"), "ancora in attesa, nessun tentativo")

scenario("MT5 torna, prezzo nella zona: ingresso a mercato con i livelli della modifica")
broker.broker_online = True
broker.move_price(3864.0)
controllo_periodico()
check(len(eventi("ENTRY_RETRY")) == 1, "diario: ENTRY_RETRY", eventi("ENTRY_RETRY"))
check(operazione(1)["status"] == "ACTIVE" and len(broker.positions) == 2, "2 posizioni a mercato",
      (operazione(1)["status"], len(broker.positions)))
check(all(vicino(posizione(1, k).sl, 3856.0) for k in ("e1", "e2")), "SL 3856 del trader",
      [posizione(1, k).sl for k in ("e1", "e2")])
check(sorted(posizione(1, k).tp for k in ("e1", "e2")) == [3876.0, 3886.0], "TP 3876 / 3886")
check("MT5 di nuovo collegato dopo" in racconto() and "nuovo tentativo d'ingresso" in racconto(),
      "racconto: nuovo tentativo")
check("📥 [BUY" in racconto(), "racconto: ordini aperti")
controllo_periodico()
check(len(broker.positions) == 2 and len(eventi("ENTRY_RETRY")) == 1, "nessun tentativo in più")
pulisci()

scenario("MT5 torna con il prezzo oltre il bordo: limite al bordo, mai a mercato oltre la zona")
broker.broker_online = False
invia(2, "Gold buy 3867", classe("NEW_SIGNAL", direction="BUY", entry_min=3867.0))
broker.broker_online = True
broker.move_price(3870.0)
controllo_periodico()
check(not broker.positions and len(broker.orders) == 2, "2 ordini limite, nessuna posizione",
      (len(broker.positions), len(broker.orders)))
check(all(o.price_open == 3867.0 for o in broker.orders.values()), "limite al bordo 3867",
      [o.price_open for o in broker.orders.values()])
pulisci()

scenario("MT5 non torna entro 5 minuti: segnale non aperto, nessun tentativo dopo")
broker.broker_online = False
invia(3, "Gold buy 3867", classe("NEW_SIGNAL", direction="BUY", entry_min=3867.0))
operazione(3)["mt5_wait_until"] = time.time() - 1
controllo_periodico()
check(operazione(3) is None, "operazione chiusa come non aperta (archivio)", operazione(3))
check(len(eventi("ENTRY_WAIT_EXPIRED")) == 1, "diario: ENTRY_WAIT_EXPIRED")
check("⌛ [BUY 3867] MT5 non tornato entro 5 minuti" in racconto(), "racconto: attesa scaduta")
broker.broker_online = True
controllo_periodico()
check(not broker.orders and not broker.positions, "MT5 tornato dopo: nessun ordine")
pulisci()

scenario("Trade Active prima che MT5 torni: ingresso annullato")
broker.broker_online = False
invia(4, "Gold buy 3867", classe("NEW_SIGNAL", direction="BUY", entry_min=3867.0))
trade_active(5, reply_to=4, direction="BUY", pips=40.0)
check(operazione(4) is None, "operazione non aperta (archivio)", operazione(4))
check(len(eventi("ENTRY_WAIT_CANCELLED")) == 1, "diario: ENTRY_WAIT_CANCELLED")
check("🗑️ [BUY 3867] ingresso in attesa annullato" in racconto(), "racconto: ingresso annullato")
broker.broker_online = True
broker.move_price(3864.0)
controllo_periodico()
check(not broker.orders and not broker.positions, "MT5 tornato: nessun ordine")
pulisci()

scenario("Cut loss senza risposta prima che MT5 torni: annulla l'ultimo ingresso in attesa")
broker.broker_online = False
invia(6, "Gold buy 3867", classe("NEW_SIGNAL", direction="BUY", entry_min=3867.0))
invia(7, "Cut lose now, ready setup recovery!", classe("IGNORE"))
check(operazione(6) is None, "operazione non aperta", operazione(6))
broker.broker_online = True
broker.move_price(3864.0)
controllo_periodico()
check(not broker.orders and not broker.positions, "nessun ordine")
pulisci()

scenario("MT5 collegato ma ordine rifiutato per altro: nessuna attesa, come prima")
broker.reset(price=4100.0)
waits = len(eventi("ENTRY_WAIT"))
invia(8, "Gold buy 4331", classe("NEW_SIGNAL", direction="BUY", entry_min=4331.0))
check(operazione(8) is None and len(eventi("ENTRY_WAIT")) == waits,
      "rifiutato subito (errore di battitura), nessuna attesa", operazione(8))
pulisci()

fine()
