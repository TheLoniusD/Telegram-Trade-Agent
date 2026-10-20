"""
Rigioco (prezzi.py): comandi del trader letti dai diari.

"Gold running 75pips now, close half and BE your entry" contiene due comandi:
chiusura parziale e pareggio. Il bot li esegue entrambi (prima la chiusura, poi
il pareggio; mai un Trade Active insieme a una chiusura parziale). Fino al 20/10
il rigioco teneva solo la chiusura e perdeva il pareggio: 13/10 SELL 4187 +210 $
nel rigioco (posizioni lasciate andare al TP) contro −31 $ reali (stop di
protezione), 20/10 SELL 4128 −77 $ contro −38 $.
"""
from banco import *

import prezzi

T0 = 1_760_950_000.0   # istante qualsiasi: il rigioco usa solo le differenze


def diario(*messaggi):
    """Eventi MESSAGE + CLASSIFIED come nel journal.jsonl, con il tempo già calcolato (_t)."""
    events = []
    for msg_id, seconds, text, intent, reply_to, data in messaggi:
        t = T0 + seconds
        events.append({"event": "MESSAGE", "msg_id": msg_id, "text": text, "reply_to": reply_to, "_t": t})
        events.append({"event": "CLASSIFIED", "msg_id": msg_id, "intent": intent, "data": data, "_t": t})
    return events


def dati(direction="SELL", entry_min=None, entry_max=None, stop_loss=None, take_profit=None,
         running_pips=None, move_sl_to_be=False, close_percentage=None):
    return {"symbol": "XAUUSD", "direction": direction, "entry_min": entry_min, "entry_max": entry_max,
            "stop_loss": stop_loss, "take_profit": take_profit or [], "is_reentry": False,
            "running_pips": running_pips or [],
            "update_details": {"move_sl_to_be": move_sl_to_be, "close_percentage": close_percentage,
                               "layer_target": None}}


SEGNALE = (1, 0, "GOLD SELL NOW\n\nSell @ 4128 - 4133\n\nSL🔴4138\nTP✅4118\nTP✅4108", "NEW_SIGNAL", None,
           dati(entry_min=4128.0, entry_max=4133.0, stop_loss=4138.0, take_profit=[4118.0, 4108.0]))
META_E_PAREGGIO = (3, 2400, "Gold running 75pips now, close half and BE your entry", "UPDATE_SIGNAL", 1,
                   dati(running_pips=[75.0], move_sl_to_be=True, close_percentage=50.0))


scenario("\"Close half and BE your entry\": chiusura parziale e pareggio, nell'ordine")
signals = prezzi.extract_signals(diario(SEGNALE, META_E_PAREGGIO))
kinds = [kind for _, kind, _ in signals[0]["events"]]
check(kinds == ["HALF", "BE"], "comandi: chiusura parziale, poi pareggio (non Trade Active)", kinds)

scenario("\"Lets close half now\" e un minuto dopo \"close half and BE\" (20/10 SELL 4128): resta il pareggio")
SOLO_META = (2, 2340, "Lets close half now", "UPDATE_SIGNAL", 1, dati(close_percentage=50.0))
signals = prezzi.extract_signals(diario(SEGNALE, SOLO_META, META_E_PAREGGIO))
kinds = [kind for _, kind, _ in signals[0]["events"]]
check(kinds == ["HALF", "BE"], "la seconda chiusura parziale è ignorata, il pareggio no", kinds)

scenario("Ticket in perdita a \"close half and BE\": stop di protezione, non posizioni lasciate al TP")
# SELL a mercato a 4.128,20; al comando il prezzo è a 4.129,50 (in perdita: niente chiusura,
# pareggio rifiutato, protezione a 1/4 = 4.130,65); poi sale a 4.131 e scende fino a 4.105.
SPREAD = 0.2
prezzi_tick = [(1, 4128.2), (2400, 4129.5), (2405, 4131.0), (3000, 4115.0), (3600, 4105.0)]
points = [(T0 + s, 0, p, p, p, p, SPREAD) for s, p in prezzi_tick]
signal = prezzi.extract_signals(diario(SEGNALE, META_E_PAREGGIO))[0]
esito = prezzi.simulate_signal(signal, points, dict(prezzi.BASE_RULES), risk=140.0, hours=8)
reasons = sorted(x["reason"] for x in esito["tickets"].values())
check(reasons == ["protezione", "protezione"], "e1 ed e2 chiusi dallo stop di protezione", reasons)
check(vicino(esito["result"], -35.0, 0.5), "risultato −35 $ (1/4 del rischio), non i due TP",
      round(esito["result"], 2))

# Varianti del 20/10 per il pareggio colpito subito dopo il Trade Active (19–20/10):
# SELL a 4.128,20; Trade Active a 4.126,00 (e1 incassato), il prezzo torna a 4.128
# (colpisce il pareggio a 4.127,20) e poi scende fino a 4.105 (TP2 4.108).
TRADE_ACTIVE = (4, 600, "Trade Active ✅\nGold Sell Running 60+ Pips", "UPDATE_SIGNAL", 1,
                dati(running_pips=[60.0], move_sl_to_be=True))
prezzi_tick = [(1, 4128.2), (600, 4126.0), (630, 4128.0), (3000, 4115.0), (3600, 4105.0)]
points = [(T0 + s, 0, p, p, p, p, SPREAD) for s, p in prezzi_tick]
signal = prezzi.extract_signals(diario(SEGNALE, TRADE_ACTIVE))[0]


def rigioca(strategia):
    rules = dict(prezzi.BASE_RULES, **prezzi.STRATEGIES[strategia][1])
    tickets = prezzi.simulate_signal(signal, points, rules, risk=140.0, hours=8)["tickets"]
    return {key: (x["reason"], x["sl"]) for key, x in tickets.items()}


scenario("Regole attuali: e1 incassato al Trade Active, e2 a pareggio colpito subito")
esito = rigioca("attuale")
check(esito["e1"][0] == "incasso TA", "e1 incassato al Trade Active", esito["e1"])
check(esito["e2"][0] == "BE" and vicino(esito["e2"][1], 4127.2), "e2 chiuso a pareggio (4.127,20)", esito["e2"])

scenario("Variante \"e2 senza pareggio\": e2 tiene lo SL del trader e arriva al TP2")
esito = rigioca("e2 senza pareggio")
check(esito["e1"][0] == "incasso TA", "e1 incassato al Trade Active", esito["e1"])
check(esito["e2"][0] == "TP" and esito["e2"][1] == 4138.0, "e2 al TP2 con lo SL del trader (4.138)", esito["e2"])

scenario("Variante \"pareggio a metà rischio\": stop di e2 a metà della distanza, arriva al TP2")
esito = rigioca("pareggio a metà rischio")
check(esito["e2"][0] == "TP" and vicino(esito["e2"][1], 4133.1), "e2 al TP2 con lo stop a 4.133,10", esito["e2"])

# Variante del 20/10 "segnale rapido senza agente": "Gold sell 4121" arriva a T0,
# l'agente lo classifica 3 secondi dopo. All'arrivo il prezzo è a 4.121,30 (nella zona),
# alla classificazione è sceso a 4.120,50 (oltre il bordo): con le regole attuali l'ordine
# resta limite a 4.121 e il prezzo non ci torna più; scende fino ai TP provvisori 4.111/4.101.
RAPIDO = [{"event": "MESSAGE", "msg_id": 7, "text": "Gold sell 4121", "reply_to": None, "_t": T0},
          {"event": "CLASSIFIED", "msg_id": 7, "intent": "NEW_SIGNAL", "_t": T0 + 3,
           "data": dati(entry_min=4121.0)}]
prezzi_tick = [(0.5, 4121.3), (3.5, 4120.5), (60, 4118.0), (600, 4110.0), (1200, 4100.0)]
points = [(T0 + s, 0, p, p, p, p, SPREAD) for s, p in prezzi_tick]
signal = prezzi.extract_signals(RAPIDO)[0]

scenario("Segnale rapido: ora di arrivo del messaggio e formato riconosciuti")
check(signal["t_msg"] == T0 and signal["t"] == T0 + 3 and signal["quick"], "arrivo T0, classificazione T0+3, rapido",
      (signal["t_msg"] - T0, signal["t"] - T0, signal["quick"]))
check(prezzi.tick_windows([signal])[0][0] <= T0, "i tick partono dall'arrivo del messaggio")

scenario("Regole attuali: prezzo oltre il bordo alla classificazione, segnale mai entrato")
esito = prezzi.simulate_signal(signal, points, dict(prezzi.BASE_RULES), risk=140.0, hours=8)
states = {key: (x["state"], x["price"]) for key, x in esito["tickets"].items()}
check(states == {"e1": ("pending", 4121.0), "e2": ("pending", 4121.0)} and esito["result"] == 0,
      "e1 ed e2 limite a 4.121, mai eseguiti", states)

scenario("Variante \"segnale rapido senza agente\": a mercato all'arrivo, e1 ed e2 ai TP")
esito = rigioca("segnale rapido senza agente")
check(esito["e1"][0] == "TP" and esito["e2"][0] == "TP", "e1 al TP1, e2 al TP2", esito)

scenario("Un segnale nel formato completo non cambia con la variante (passa comunque dall'agente)")
COMPLETO = [dict(RAPIDO[0], text="GOLD SELL NOW\n\nSell @ 4121 - 4126\n\nSL🔴4131"), RAPIDO[1]]
signal = prezzi.extract_signals(COMPLETO)[0]
check(not signal["quick"], "non è un segnale rapido")
check(rigioca("segnale rapido senza agente") == rigioca("attuale"), "stesso esito delle regole attuali")

fine()
