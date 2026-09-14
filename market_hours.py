"""
Controllo degli orari di mercato per l'oro spot (XAUUSD).

Serve a evitare di chiamare l'agente AI - e quindi consumare token - quando il
mercato è chiuso e nessuna azione sarebbe comunque eseguibile su MT5.

⚠️ Gli orari sono in UTC e vanno verificati sul proprio broker: ogni broker ha
il proprio server time e le proprie sessioni. I valori qui sotto coprono il caso
più comune (chiusura venerdì sera, riapertura domenica sera, pausa giornaliera
di rollover). Le pause specifiche del broker che questo calendario statico non
può conoscere (festività, manutenzioni impreviste) vengono intercettate dal
controllo sui tick reali in MT5Executor.is_symbol_tradable().
"""

from datetime import datetime, timezone

# Chiusura settimanale: venerdì alle 21:00 UTC
WEEKLY_CLOSE_WEEKDAY = 4  # 0 = lunedì ... 6 = domenica
WEEKLY_CLOSE_HOUR_UTC = 21

# Riapertura settimanale: domenica alle 22:00 UTC
WEEKLY_OPEN_WEEKDAY = 6
WEEKLY_OPEN_HOUR_UTC = 22

# Pausa giornaliera di rollover (manutenzione broker).
# Nota: l'oro spot NON ha una pausa pranzo - a differenza di alcuni futures o
# mercati azionari - quindi l'unica interruzione infrasettimanale è questa.
DAILY_BREAK_START_HOUR_UTC = 21
DAILY_BREAK_END_HOUR_UTC = 22


def is_market_time_open(now_utc: datetime = None) -> tuple[bool, str]:
    """
    Verifica se, secondo il calendario statico, il mercato dell'oro è aperto.
    Ritorna (aperto, motivo). Non richiede una connessione a MT5.
    """
    now = now_utc or datetime.now(timezone.utc)
    weekday = now.weekday()
    hour = now.hour

    if weekday == 5:
        return False, "weekend (sabato)"

    if weekday == WEEKLY_CLOSE_WEEKDAY and hour >= WEEKLY_CLOSE_HOUR_UTC:
        return False, f"mercato chiuso (venerdì dalle {WEEKLY_CLOSE_HOUR_UTC}:00 UTC)"

    if weekday == WEEKLY_OPEN_WEEKDAY and hour < WEEKLY_OPEN_HOUR_UTC:
        return False, f"weekend (domenica prima delle {WEEKLY_OPEN_HOUR_UTC}:00 UTC)"

    is_weekly_edge_day = weekday in (WEEKLY_CLOSE_WEEKDAY, WEEKLY_OPEN_WEEKDAY)
    if not is_weekly_edge_day and DAILY_BREAK_START_HOUR_UTC <= hour < DAILY_BREAK_END_HOUR_UTC:
        return False, f"pausa di rollover ({DAILY_BREAK_START_HOUR_UTC}:00-{DAILY_BREAK_END_HOUR_UTC}:00 UTC)"

    return True, "mercato aperto"
