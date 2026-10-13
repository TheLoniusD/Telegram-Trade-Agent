"""
Re-entry ("Lets buy again" senza prezzo).

Regole: eredita SL e TP del trader dall'ultima operazione nella stessa
direzione ed entra a mercato; ma un "again" scritto entro 5 minuti da un
segnale nuovo ancora aperto è il commento del trader a quel segnale: nessuna
nuova posizione (09/10 BUY 4201: 4 posizioni sullo stesso setup, −282 $).
"""
from banco import *


def again(msg_id, text="Lets buy again", direction="BUY"):
    invia(msg_id, text, classe("NEW_SIGNAL", direction=direction, reentry=True))


scenario("\"Gold buy 4201\" e subito dopo \"Lets buy again\": nessuna nuova posizione")
broker.reset(price=4200.61)
segnale(1, "BUY", 4201, 4196, 4201, 4191, [4211, 4221])
again(2)
check(len(broker.positions) == 2, "restano solo le 2 posizioni del segnale", len(broker.positions))
check("stesso setup" in racconto(), "racconto: \"stesso setup, nessuna nuova posizione\"")
pulisci()

scenario("Segnale già chiuso allo stop, poi \"again\" entro 5 minuti: re-entry vero")
broker.reset(price=4200.61)
segnale(3, "BUY", 4201, 4196, 4201, 4191, [4211, 4221])
for t in list(broker.positions):
    broker.broker_close(t, "SL", price=4191.0)
controllo_periodico()
again(4, "Lets try buy again")
positions = list(broker.positions.values())
check(len(positions) == 2, "2 nuove posizioni a mercato", len(positions))
check(all(p.sl == 4191.0 for p in positions), "SL del trader ereditato (4191)", [p.sl for p in positions])
check(sorted(p.tp for p in positions) == [4211.0, 4221.0], "TP del trader ereditati (4211 / 4221)",
      [p.tp for p in positions])
pulisci()

scenario("\"Again\" 6 minuti dopo con il segnale ancora aperto: re-entry vero")
broker.reset(price=4200.61)
segnale(5, "BUY", 4201, 4196, 4201, 4191, [4211, 4221])
operazione(5)["created_at"] -= 360
again(6)
check(len(broker.positions) == 4, "4 posizioni (2 del segnale + 2 del re-entry)", len(broker.positions))
pulisci()

scenario("Re-entry dopo un segnale rapido mai completato: SL provvisorio a 10 $, TP del segnale")
broker.reset(price=4136.02)
invia(7, "Gold sell 4133", classe("NEW_SIGNAL", direction="SELL", entry_min=4133.0))
for t in list(broker.positions):
    broker.broker_close(t, "SL", price=4143.0)
controllo_periodico()
again(8, "Lets sell again", direction="SELL")
positions = list(broker.positions.values())
check(len(positions) == 2, "2 posizioni a mercato", len(positions))
check(all(vicino(p.sl, 4146.02) for p in positions), "SL provvisorio a 10 $ dall'ingresso (4146,02)",
      [p.sl for p in positions])
check(sorted(p.tp for p in positions) == [4113.0, 4123.0], "TP del segnale (4123 / 4113): nessuna posizione senza uscita",
      [p.tp for p in positions])
pulisci()

fine()
