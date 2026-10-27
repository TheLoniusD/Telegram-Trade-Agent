"""Ingresso: 2 ticket al bordo della zona, mai fuori zona, SL e TP provvisori, segnali sbagliati."""
from banco import *

scenario("BUY 4186 con il prezzo nella zona: 2 ticket a mercato, rischio diviso a metà")
broker.reset(price=4185.0)
segnale(1, "BUY", 4186, 4181, 4186, 4176, [4196, 4206])
e1, e2 = posizione(1, "e1"), posizione(1, "e2")
check(e1 is not None and e2 is not None, "e1 ed e2 aperti a mercato")
check(len(broker.orders) == 0, "nessun ordine in attesa")
check(e1 and e2 and e1.volume == e2.volume, "stesso lotto su e1 ed e2", (e1 and e1.volume, e2 and e2.volume))
check(e1 and vicino(e1.price_open, 4185.20), "ingresso all'ask 4185,20", e1 and e1.price_open)
check(e1 and e1.tp == 4196.0 and e2 and e2.tp == 4206.0, "e1 sul TP1 4196, e2 sul TP2 4206")
check(e1 and e1.sl == 4176.0, "SL del trader 4176")
# rischio 2% di 7000 $ = 140 $ con lo SL a 10 $ dal segnale → 0,14 lotti in tutto
check(e1 and vicino(e1.volume + e2.volume, 0.14), "lotto totale 0,14 (2% di 7000 $ con SL a 10 $)",
      e1 and e1.volume + e2.volume)
pulisci()

scenario("SELL 4151 con il prezzo sotto la zona: 2 limiti al bordo, mai ingressi fuori zona")
broker.reset(price=4150.11)
segnale(3, "SELL", 4151, 4151, 4156, 4161, [4141, 4131])
check(len(broker.positions) == 0, "nessuna posizione a mercato")
check(len(broker.orders) == 2 and all(o.price_open == 4151.0 for o in broker.orders.values()),
      "2 ordini limite a 4151", [(o.price_open) for o in broker.orders.values()])
check(ticket(3, "e1").get("pending") and ticket(3, "e2").get("pending"), "in memoria: e1 ed e2 in attesa")
broker.move_price(4151.2)
controllo_periodico()
check(posizione(3, "e1") is not None and posizione(3, "e2") is not None, "prezzo tornato al bordo: ordini eseguiti")
check(not ticket(3, "e1").get("pending") and not ticket(3, "e2").get("pending"),
      "in memoria: e1 ed e2 aperti dopo il controllo periodico")
pulisci()

scenario("Segnale rapido mai completato: SL e TP provvisori a 10/20 $ dal segnale (07/10)")
broker.reset(price=4136.02)
invia(5, "Gold sell 4133", classe("NEW_SIGNAL", direction="SELL", entry_min=4133.0))
e1, e2 = posizione(5, "e1"), posizione(5, "e2")
check(e1 is not None and e2 is not None, "2 posizioni a mercato (prezzo sopra il segnale: migliore per un SELL)")
check(e1 and e1.tp == 4123.0 and e2 and e2.tp == 4113.0, "TP provvisori 4123 / 4113", e1 and (e1.tp, e2.tp))
check(e1 and e1.sl == 4143.0, "SL provvisorio 4143", e1 and e1.sl)
invia(5, "GOLD SELL NOW · Sell @ 4134 - 4139 · SL 4144 · TP 4124 · TP 4114",
      classe("UPDATE_SIGNAL", direction="SELL", entry_min=4134.0, entry_max=4139.0, stop_loss=4144.0,
             take_profit=[4124.0, 4114.0]), edit=True)
check(e1.tp == 4124.0 and e2.tp == 4114.0 and e1.sl == 4144.0, "completamento del trader: SL e TP sostituiti",
      (e1.sl, e1.tp, e2.tp))
pulisci()

scenario("Ordine limite rifiutato con \"Invalid price\" perché il prezzo è appena arrivato: a mercato")
broker.reset(price=4158.06)
_place_limit = L.mt5_agent._place_limit


def _limite_rifiutato(plan, price, tp):
    broker.move_price(4157.5)
    return {"success": False, "mt5_ticket": None, "fill_price": None, "volume": None,
            "error": "Invalid price (retcode 10015)"}


L.mt5_agent._place_limit = _limite_rifiutato
invia(7, "Gold buy 4158", classe("NEW_SIGNAL", direction="BUY", entry_min=4158.0))
L.mt5_agent._place_limit = _place_limit
check(len(broker.positions) == 2 and not broker.orders, "e1 ed e2 entrati a mercato")
pulisci()

scenario("Errore di battitura: segnale a più di 20 $ dal mercato non aperto")
broker.reset(price=4185.0)
invia(9, "Gold buy 4285", classe("NEW_SIGNAL", direction="BUY", entry_min=4285.0))
check(not broker.positions and not broker.orders, "nessuna posizione né ordine")

scenario("SL scritto male nel completamento (22/10 \"SL 4176\" su Sell @ 4066 - 4071): non applicato")
broker.reset(price=4064.08)
invia(15, "Gold sell 4064", classe("NEW_SIGNAL", direction="SELL", entry_min=4064.0))
e1, e2 = posizione(15, "e1"), posizione(15, "e2")
check(e1 is not None and e2 is not None and e1.sl == 4074.0, "2 posizioni a mercato, SL provvisorio 4074",
      e1 and e1.sl)
invia(15, "GOLD SELL NOW · Sell @ 4066 - 4071 · SL 4176 · TP 4056 · TP 4046",
      classe("UPDATE_SIGNAL", direction="SELL", entry_min=4066.0, entry_max=4071.0, stop_loss=4176.0,
             take_profit=[4056.0, 4046.0]), edit=True)
check(e1.sl == 4074.0 and e2.sl == 4074.0, "su MT5 resta lo SL 4074, non 4176", (e1.sl, e2.sl))
check(ticket(15, "e1")["stop_loss"] == 4074.0 and operazione(15)["signal_stop_loss"] != 4176.0,
      "in memoria resta lo SL 4074", (ticket(15, "e1")["stop_loss"], operazione(15)["signal_stop_loss"]))
check(e1.tp == 4056.0 and e2.tp == 4046.0, "i TP del trader sono applicati", (e1.tp, e2.tp))
eventi = [e for e in diario() if e["event"] == "SL_IGNORED"]
check(len(eventi) == 1 and vicino(eventi[0].get("distance"), 110.0), "diario: SL_IGNORED a 110 $ dal segnale",
      eventi)
check("SL 4.176,00 non applicato" in racconto(), "racconto: SL non applicato, probabile errore di battitura")
invia(15, "GOLD SELL NOW · Sell @ 4066 - 4071 · SL 4076 · TP 4056 · TP 4046",
      classe("UPDATE_SIGNAL", direction="SELL", entry_min=4066.0, entry_max=4071.0, stop_loss=4076.0,
             take_profit=[4056.0, 4046.0]), edit=True)
check(e1.sl == 4076.0 and e2.sl == 4076.0, "la correzione del trader (SL 4076) è applicata", (e1.sl, e2.sl))
pulisci()

scenario("SL scritto male in un segnale completo: SL provvisorio, lotti sul rischio del 2%")
broker.reset(price=4064.9)
invia(17, "GOLD SELL NOW · Sell @ 4065 - 4070 · SL 4175 · TP 4055 · TP 4045",
      classe("NEW_SIGNAL", direction="SELL", entry_min=4065.0, entry_max=4070.0, stop_loss=4175.0,
             take_profit=[4055.0, 4045.0]))
ordini = list(broker.orders.values())
check(len(ordini) == 2 and all(o.sl == 4075.0 for o in ordini), "2 limiti a 4065 con lo SL provvisorio 4075",
      [o.sl for o in ordini])
check(vicino(sum(o.volume_current for o in ordini), 0.14), "lotto totale 0,14 (2% di 7000 $ con SL a 10 $)",
      [o.volume_current for o in ordini])
check(operazione(17) is not None and operazione(17)["signal_stop_loss"] is None,
      "lo SL sbagliato non resta in memoria (re-entry)", operazione(17) and operazione(17)["signal_stop_loss"])
pulisci()

scenario("Conto non DEMO: nessun ordine")
broker.reset(price=4200.61)
broker.account_mode = broker.ACCOUNT_TRADE_MODE_REAL
segnale(11, "BUY", 4201, 4196, 4201, 4191, [4211, 4221])
check(not broker.positions and not broker.orders, "nessuna posizione né ordine su un conto reale")
broker.account_mode = broker.ACCOUNT_TRADE_MODE_DEMO
broker.connected = False
invia(13, "Gold buy 4201", classe("NEW_SIGNAL", direction="BUY", entry_min=4201.0))
broker.connected = True
check(not broker.positions and not broker.orders, "nessun ordine se il tipo di conto non si legge")
pulisci()

fine()
