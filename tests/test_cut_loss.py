"""
"Cut lose … now" = chiusura dell'operazione in perdita (24/11).

Dal 23/09 al 24/11 cinque messaggi di cut loss immediato. L'agente li leggeva
come IGNORE (12/10 "this setup i cut lose 30pips", 02/11 "Cut lose now"), come
"chiudi il 20/50%" con i pips presi per la percentuale (28/10, 05/11: nessuna
chiusura, ticket in perdita) o come "aggiornamento senza modifiche" (19/11):
quattro stop pieni, circa 290 $ in più del trader. Il codice riconosce il cut
loss dal testo e chiude l'operazione a cui il messaggio risponde, o l'ultima a
mercato, solo se è in perdita. Il cut loss condizionato ("if solid break X")
resta uno SL.
"""
from banco import *
from order_manager import cut_loss_now


def mt5_tickets(msg_id):
    """I numeri MT5 dei ticket aperti (da leggere prima della chiusura: poi l'operazione va in archivio)."""
    return [t.get("mt5_ticket") for t in operazione(msg_id)["tickets"].values() if t.get("mt5_ticket")]


def chiusa(msg_id, tickets):
    """Nessuna posizione sul broker e operazione chiusa (in archivio o CLOSED)."""
    trade = operazione(msg_id)
    return not any(t in broker.positions or t in broker.orders for t in tickets) and         (trade is None or trade.get("status") == "CLOSED")

scenario("Riconoscimento sui messaggi veri del canale")
VERI = ["Gold in bearish momentum,this setup i cut lose 30pips",                                  # 12/10
        "Cut lose at 4049 20pips now, because M5 anda M15 is already breakout the resistance",   # 28/10
        "Cut lose now, ready setup recovery!",                                                    # 02/11
        "Candle break, cut lose 50pips now and ready next setup",                                 # 05/11
        "Cut lose 60pips now"]                                                                    # 19/11
CONDIZIONALI = ["I'm still hold this setup, cut lose if solid break 4188",                        # 02/10
                "Try hold it, cut lose if solid break 4185",                                      # 12/10
                "Cut lose if solid break 4124", "Cut lose if solid break 4110", "Cut lose if solid break 4052"]
ALTRI = ["Lets close half now", "Hit risk ! · Ready setup recovery!", "Close all now",
         "Running 65pips now, Lets close half now and BE your entry", "Cut loss at 4130"]
check(all(cut_loss_now(t) for t in VERI), "i 5 cut loss immediati riconosciuti",
      [t for t in VERI if not cut_loss_now(t)])
check(not any(cut_loss_now(t) for t in CONDIZIONALI + ALTRI), "condizionali e altri comandi no",
      [t for t in CONDIZIONALI + ALTRI if cut_loss_now(t)])

scenario("19/11: \"Cut lose 60pips now\" letto come aggiornamento senza modifiche → BUY 3728 chiuso, il SELL in guadagno resta")
broker.reset(price=3738.5)
segnale(1, "SELL", 3738, 3738, 3743, 3748, [3728, 3718])
broker.move_price(3727.8)
segnale(2, "BUY", 3728, 3723, 3728, 3718, [3738, 3748])
check(len(broker.positions) == 4, "4 posizioni a mercato", len(broker.positions))
broker.move_price(3722.0)
buy = mt5_tickets(2)
invia(3, "Cut lose 60pips now", classe("UPDATE_SIGNAL"))
check(chiusa(2, buy), "BUY 3728 chiuso (e1 ed e2)", operazione(2))
check(posizione(1, "e1") is not None and posizione(1, "e2") is not None, "SELL 3738 (in guadagno) ancora aperto")
check(any(e["event"] == "CUT_LOSS" and e.get("agent_intent") == "UPDATE_SIGNAL" for e in diario()),
      "diario: CUT_LOSS con la lettura dell'agente")
check("✂️ Cut loss del trader" in racconto(), "racconto: riga del cut loss")
pulisci()

scenario("05/11: \"cut lose 50pips now\" letto come \"chiudi il 50%\" → chiusura totale lo stesso")
broker.reset(price=4123.0)
segnale(10, "SELL", 4123, 4123, 4128, 4133, [4113, 4103])
broker.move_price(4127.0)
sell = mt5_tickets(10)
invia(11, "Candle break, cut lose 50pips now and ready next setup", classe("UPDATE_SIGNAL", close_percentage=50.0))
check(not broker.positions, "SELL 4123 chiuso tutto", len(broker.positions))
check(chiusa(10, sell), "operazione chiusa", operazione(10))

scenario("Il messaggio ripetuto 2 secondi dopo: niente da chiudere, nessun errore")
invia(12, "Candle break, cut lose 50pips now and ready next setup", classe("IGNORE"))
check(not broker.positions, "nessuna posizione")
pulisci()

scenario("28/10: SL e percentuale dell'agente ignorati; la modifica del messaggio non chiude il segnale nuovo")
broker.reset(price=4047.0)
segnale(20, "SELL", 4047, 4047, 4052, 4057, [4037, 4027])
broker.move_price(4050.5)
sell = mt5_tickets(20)
invia(21, "Cut lose at 4049 20pips now, because M5 anda M15 is already breakout the resistance",
      classe("UPDATE_SIGNAL", stop_loss=4049.0, close_percentage=20.0))
check(chiusa(20, sell) and not broker.positions, "SELL 4047 chiuso", operazione(20))
broker.move_price(4048.8)
segnale(22, "BUY", 4049, 4044, 4049, 4039, [4059, 4069])
broker.move_price(4047.0)
invia(21, "Cut lose at 4049 20pips now, because M5 and M15 is already breakout the resistance",
      classe("UPDATE_SIGNAL", stop_loss=4049.0, close_percentage=20.0), edit=True)
check(posizione(22, "e1") is not None and posizione(22, "e2") is not None,
      "BUY 4049 (in perdita, aperto dopo) non chiuso dalla modifica")
pulisci()

scenario("Ultima operazione in guadagno: non è quella del cut loss, resta aperta con il suo SL")
broker.reset(price=3727.8)
segnale(30, "BUY", 3728, 3723, 3728, 3718, [3738, 3748])
broker.move_price(3731.0)
invia(31, "Cut lose now, ready setup recovery!", classe("IGNORE"))
check(posizione(30, "e1") is not None and posizione(30, "e2") is not None, "BUY 3728 in guadagno ancora aperto")
check(operazione(30)["status"] == "ACTIVE", "stato ACTIVE", operazione(30)["status"])
check(any(e["event"] == "DECISION" and "non è in perdita" in str(e.get("reason")) for e in diario()),
      "diario: decisione con il motivo")
pulisci()

scenario("In risposta al segnale: chiude quello, anche se non è l'ultimo")
broker.reset(price=3727.8)
segnale(40, "BUY", 3728, 3723, 3728, 3718, [3738, 3748])
broker.move_price(3725.8)
segnale(41, "BUY", 3726, 3721, 3726, 3716, [3736, 3746])
broker.move_price(3724.0)
first = mt5_tickets(40)
invia(42, "Cut lose 40pips now", classe("UPDATE_SIGNAL"), reply_to=40)
check(chiusa(40, first), "BUY 3728 (a cui risponde) chiuso", operazione(40))
check(posizione(41, "e1") is not None, "BUY 3726 ancora aperto")
pulisci()

scenario("Solo ordini in attesa: cancellati")
broker.reset(price=3730.0)
segnale(50, "BUY", 3728, 3723, 3728, 3718, [3738, 3748])
check(len(broker.orders) == 2 and not broker.positions, "2 ordini limite in attesa", (len(broker.orders), len(broker.positions)))
invia(51, "Cut lose now", classe("IGNORE"))
check(not broker.orders, "ordini cancellati", len(broker.orders))
pulisci()

scenario("Cut loss condizionato: resta uno SL, nessuna chiusura")
broker.reset(price=4188.8)
segnale(60, "BUY", 4189, 4184, 4189, 4179, [4199, 4209])
broker.move_price(4186.0)
invia(61, "I'm still hold this setup, cut lose if solid break 4183", classe("UPDATE_SIGNAL", stop_loss=4183.0),
      reply_to=60)
check(posizione(60, "e1") is not None and posizione(60, "e2") is not None, "posizioni ancora aperte")
check(vicino(posizione(60, "e1").sl, 4183.0), "SL a 4183", posizione(60, "e1").sl)
check(not any(e["event"] == "CUT_LOSS" for e in diario() if e.get("msg_id") == 61), "nessun CUT_LOSS")
pulisci()

fine()
