"""
Ordini limite eseguiti "di nascosto" e MT5 scollegato.

05/10: ordini eseguiti tra due controlli venivano segnati come cancellati e
restavano aperti senza gestione (−104,87 $). 06/10: con MT5 scollegato al
Trade Active il pareggio andava perso (stop pieno).
"""
from banco import *

scenario("Ordini eseguiti tra due controlli, poi Trade Active (05/10 SELL 4151)")
broker.reset(price=4150.11)
segnale(1, "SELL", 4151, 4151, 4156, 4161, [4141, 4131])
broker.move_price(4154.0)
broker.move_price(4146.0)                       # eseguiti sul broker, il bot non lo sa ancora
check(len(broker.positions) == 2 and not broker.orders, "sul broker: 2 posizioni, nessun ordine")
trade_active(2, reply_to=1, direction="SELL", pips=60.0)
check(posizione(1, "e1") is None and ticket(1, "e1").get("closed"), "e1 riconosciuto come eseguito e incassato")
check(posizione(1, "e2") is not None and vicino(posizione(1, "e2").sl, 4150.0), "e2 aperto a pareggio 4150",
      posizione(1, "e2") and posizione(1, "e2").sl)
check(not any(e["event"] == "PENDING_CANCELLED" for e in diario()), "nessun ordine segnato come cancellato")
pulisci()

scenario("Ordine eseguito proprio durante la cancellazione: resta una posizione gestita")
broker.reset(price=4150.11)
segnale(3, "SELL", 4151, 4151, 4156, 4161, [4141, 4131])
broker.move_price(4151.2)
L.cancel_pending_ticket(ticket(3, "e1"), "prova", operazione(3))
check(not ticket(3, "e1").get("pending") and not ticket(3, "e1").get("closed"), "e1 in memoria come aperto")
scenario("Chiusura del trader con un ordine eseguito non ancora rilevato")
invia(4, "Close all now", classe("CLOSE_SIGNAL"), reply_to=3)
check(not broker.positions and not broker.orders, "nessuna posizione e nessun ordine rimasti")
pulisci()

scenario("Modifica incoerente con ordini in attesa: non inviata, memoria uguale al broker")
broker.reset(price=4158.0)
segnale(5, "BUY", 4157, 4152, 4157, 4147, [4167, 4177])
invia(5, "GOLD BUY NOW · Buy @ 4157 - 4152 · SL 4147 · TP 4137 · TP 4127",
      classe("UPDATE_SIGNAL", direction="BUY", entry_min=4152.0, entry_max=4157.0, stop_loss=4147.0,
             take_profit=[4137.0, 4127.0]), edit=True)
orders = sorted(broker.orders.values(), key=lambda o: o.tp)
check([o.tp for o in orders] == [4167.0, 4177.0], "TP sul broker invariati (4167 / 4177)", [o.tp for o in orders])
check(ticket(5, "e1").get("take_profit") == 4167.0, "memoria riallineata al broker", ticket(5, "e1").get("take_profit"))
pulisci()

scenario("MT5 scollegato al Trade Active, ordini eseguiti nel frattempo: pareggio al ritorno (06/10 BUY 4170)")
broker.reset(price=4170.41)
segnale(6, "BUY", 4170, 4165, 4170, 4160, [4180, 4190])
check(len(broker.orders) == 2, "2 ordini limite a 4170")
broker.connected = False
trade_active(7, reply_to=6, pips=45.0)
broker.connected = True
broker.move_price(4169.5)                        # ordini eseguiti mentre MT5 era giù
broker.move_price(4172.0)
broker.connected = False
controllo_periodico()                            # ancora giù: nessun danno
broker.connected = True
controllo_periodico()
check(len(broker.positions) == 2, "2 posizioni aperte")
check(all(vicino(p.sl, 4171.0) for p in broker.positions.values()), "tutte e due a pareggio 4171",
      [p.sl for p in broker.positions.values()])
pulisci()

scenario("Come sopra ma ordini mai eseguiti: cancellati al ritorno di MT5")
broker.reset(price=4170.41)
segnale(8, "BUY", 4170, 4165, 4170, 4160, [4180, 4190])
broker.connected = False
trade_active(9, reply_to=8, pips=45.0)
broker.connected = True
controllo_periodico()
check(not broker.orders and not broker.positions, "nessun ordine e nessuna posizione")
pulisci()

fine()
