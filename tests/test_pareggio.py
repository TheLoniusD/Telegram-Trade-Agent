"""
Pareggio e Trade Active.

Regole (doc/DECISIONI.md): pareggio a ingresso ±1 $; se MT5 lo rifiuta, prima
il pareggio ridotto a ±0,5 $, poi lo stop di protezione a 1/4, 1/2, 3/4 della
distanza ingresso–stop; ogni 30 s si riprova il pareggio pieno e si sale di
gradino, mai si peggiora. Trade Active: incassa il ticket entrato peggio (a
parità entro 0,50 $ il TP1), ne lascia sempre almeno uno, mai in perdita.
Il broker finto rifiuta gli stop a meno di 0,50 $ dal prezzo, come quello vero.
"""
from banco import *

scenario("Trade Active con il prezzo lontano: incassa e1 (TP1), e2 a pareggio +1 $")
broker.reset(price=4185.0)
segnale(1, "BUY", 4186, 4181, 4186, 4176, [4196, 4206])        # ingresso 4185,20
broker.move_price(4189.0)
trade_active(2, reply_to=1)
check(posizione(1, "e1") is None and ticket(1, "e1").get("closed"), "e1 incassato")
e2 = posizione(1, "e2")
check(e2 is not None, "e2 resta aperto per il TP2")
check(e2 and vicino(e2.sl, 4186.20), "e2 a pareggio 4186,20 (ingresso +1 $)", e2 and e2.sl)
trade_active(3, reply_to=1)
check(posizione(1, "e2") is not None, "un secondo Trade Active non chiude l'ultimo ticket")
pulisci()

scenario("Pareggio con il prezzo lontano: tutti e due a ingresso +1 $")
broker.reset(price=4185.0)
segnale(4, "BUY", 4186, 4181, 4186, 4176, [4196, 4206])
broker.move_price(4189.0)
pareggio(5, reply_to=4)
check(all(vicino(posizione(4, k).sl, 4186.20) for k in ("e1", "e2")), "e1 ed e2 con SL 4186,20")
check(ticket(4, "e1").get("be_active") and not ticket(4, "e1").get("be_pending"), "in memoria: pareggio attivo")
pulisci()

scenario("Pareggio +1 $ rifiutato, +0,5 $ accettato: pareggio ridotto, poi pieno")
broker.reset(price=4185.0)
segnale(6, "BUY", 4186, 4181, 4186, 4176, [4196, 4206])
broker.move_price(4186.4)                       # 4186,20 a 0,20 $ dal prezzo: troppo vicino
pareggio(7, reply_to=6)
check(all(vicino(posizione(6, k).sl, 4185.70) for k in ("e1", "e2")), "SL 4185,70 (pareggio ridotto)",
      [posizione(6, k).sl for k in ("e1", "e2")])
check(ticket(6, "e1").get("be_pending"), "pareggio pieno ancora in attesa")
broker.move_price(4188.0)
controllo_periodico()
check(all(vicino(posizione(6, k).sl, 4186.20) for k in ("e1", "e2")), "al controllo successivo: SL 4186,20")
check(not ticket(6, "e1").get("be_pending") and ticket(6, "e1").get("be_active"), "pareggio pieno attivo")
check("pareggio ridotto" in racconto(), "racconto: riga \"pareggio ridotto\"")
pulisci()

scenario("Prezzo quasi all'ingresso: protezione 1/4, poi pareggio ridotto, poi pieno; mai peggiorato")
broker.reset(price=4185.0)
segnale(8, "BUY", 4186, 4181, 4186, 4176, [4196, 4206])
broker.move_price(4185.4)
trade_active(9, reply_to=8)
check(posizione(8, "e1") is None, "e1 incassato (in guadagno di 0,20 $)")
e2 = posizione(8, "e2")
check(e2 and vicino(e2.sl, 4182.90), "e2: stop di protezione a 1/4 (4182,90)", e2 and e2.sl)
broker.move_price(4186.3)
controllo_periodico()
check(vicino(e2.sl, 4185.70), "prezzo su di 1,10 $: pareggio ridotto 4185,70", e2.sl)
broker.move_price(4185.9)
controllo_periodico()
check(vicino(e2.sl, 4185.70), "prezzo giù di 0,40 $: lo stop resta 4185,70", e2.sl)
broker.move_price(4188.0)
controllo_periodico()
check(vicino(e2.sl, 4186.20), "prezzo a 4188: pareggio pieno 4186,20", e2.sl)
pulisci()

scenario("SELL: pareggio ridotto a ingresso -0,5 $, poi pieno a -1 $")
broker.reset(price=4151.0)
segnale(10, "SELL", 4151, 4151, 4156, 4161, [4141, 4131])      # ingresso al bid 4151,00
broker.move_price(4149.5)                                      # ask 4149,70: 4150,00 troppo vicino
pareggio(11, reply_to=10)
check(all(vicino(posizione(10, k).sl, 4150.50) for k in ("e1", "e2")), "SL 4150,50 (pareggio ridotto)",
      [posizione(10, k).sl for k in ("e1", "e2")])
broker.move_price(4148.0)
controllo_periodico()
check(all(vicino(posizione(10, k).sl, 4150.00) for k in ("e1", "e2")), "poi SL 4150,00 (pareggio pieno)")
pulisci()

scenario("Trade Active con il ticket da incassare in perdita: nessuna chiusura, protezione")
broker.reset(price=4185.0)
segnale(12, "BUY", 4186, 4181, 4186, 4176, [4196, 4206])
broker.move_price(4184.5)
trade_active(13, reply_to=12)
check(posizione(12, "e1") is not None and posizione(12, "e2") is not None, "nessun ticket chiuso in perdita")
check(all(vicino(posizione(12, k).sl, 4182.90) for k in ("e1", "e2")), "stop di protezione a 1/4 su tutti e due",
      [posizione(12, k).sl for k in ("e1", "e2")])
pulisci()

scenario("Trade Active con ingressi a 10 centesimi: si incassa il TP1, resta il TP2 (08/10)")
broker.reset(price=4131.87)
invia(14, "Gold sell 4129", classe("NEW_SIGNAL", direction="SELL", entry_min=4129.0))
ticket(14, "e2")["entry_price"] = 4131.77
posizione(14, "e2").price_open = 4131.77
broker.move_price(4129.5)
trade_active(15, reply_to=14, direction="SELL", pips=65.0)
check(posizione(14, "e1") is None, "e1 (TP1) incassato anche se entrato 10 centesimi meglio")
check(posizione(14, "e2") is not None and vicino(posizione(14, "e2").sl, 4130.77), "e2 (TP2) a pareggio 4130,77",
      posizione(14, "e2") and posizione(14, "e2").sl)
pulisci()

scenario("Trade Active con un solo ticket eseguito: niente incasso, pareggio e ordine cancellato")
broker.reset(price=4150.11)
segnale(16, "SELL", 4151, 4151, 4156, 4161, [4141, 4131])
broker.fill_order(ticket(16, "e1")["mt5_ticket"])
controllo_periodico()
broker.move_price(4147.0)
trade_active(17, reply_to=16, direction="SELL")
check(posizione(16, "e1") is not None and vicino(posizione(16, "e1").sl, 4150.0), "e1 resta aperto a pareggio 4150",
      posizione(16, "e1") and posizione(16, "e1").sl)
check(not broker.orders, "l'ordine di e2 non eseguito viene cancellato")
pulisci()

fine()
