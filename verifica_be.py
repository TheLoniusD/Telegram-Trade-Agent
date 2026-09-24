"""
Verifica sullo storico dei prezzi: come sarebbero andate le operazioni di una
giornata con regole di Breakeven diverse.

Per ogni posizione aperta dal bot ricostruisce dal diario (logs/AAAA-MM-GG/
journal.jsonl) ingresso, SL/TP e loro modifiche, il momento in cui il trader
ha chiesto il BE e le chiusure comandate dal bot; poi la rigioca sulle candele
a 1 minuto scaricate da MT5 con:
  - nessun BE automatico (resta lo SL del segnale fino a TP/SL/chiusura del bot)
  - BE appena chiesto dal trader con guadagno minimo di 0, 2, 3, 5 $ (o --minimi)
e confronta il totale con il risultato reale della giornata.

Limiti: candele da 1 minuto (se SL e TP cadono nella stessa candela si assume
lo SL, per prudenza); SL/TP controllati con lo spread della candela; chiusure
parziali non simulate. È una stima per scegliere BE_MIN_PROFIT, non un
backtest esatto.

Uso (dove MT5 è raggiungibile, cioè sul server o sul PC con MT5):
    python verifica_be.py 2026-09-24
    python verifica_be.py 2026-09-24 --minimi 0,1,2,3,5,8
    python verifica_be.py 2026-09-24 --offset-broker 3   # ore del broker rispetto a UTC
"""
import argparse
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from logger_config import to_local
from report import load_events

SYMBOL = "XAUUSD"
POINT = 0.01
BE_OFFSET_DEFAULT = 0.5


def collect_positions(events: list) -> list:
    """Ricostruisce dal diario la vita di ogni posizione aperta dal bot."""
    positions = {}
    msg_text = {}
    for e in events:
        if e["event"] == "MESSAGE" and not e.get("is_edit"):
            msg_text.setdefault(e["msg_id"], (e.get("text") or "").split("\n")[0][:28])
        if e["event"] != "MT5_ORDER" or not e.get("ok"):
            continue
        req = e.get("request") or {}
        ts = datetime.fromisoformat(e["ts"]).timestamp()
        if e["operation"] == "OPEN":
            positions[e["order"]] = {
                "ticket": e["order"], "msg_id": e.get("msg_id"), "open_ts": ts,
                "buy": req.get("type") == 0, "entry": e.get("price"), "volume": e.get("volume"),
                "levels": [(ts, req.get("sl") or 0.0, req.get("tp") or 0.0)],
                "be_request_ts": None, "bot_close_ts": None,
            }
            continue
        pos = positions.get(req.get("position"))
        if pos is None:
            continue
        if e["operation"] == "MODIFY_SL_TP":
            pos["levels"].append((ts, req.get("sl") or 0.0, req.get("tp") or 0.0))
        elif e["operation"] == "CLOSE":
            pos["bot_close_ts"] = ts

    for e in events:
        ticket = e.get("mt5_ticket")
        if ticket not in positions:
            continue
        ts = datetime.fromisoformat(e["ts"]).timestamp()
        pos = positions[ticket]
        # Il trader ha chiesto il BE: primo tentativo, riuscito o messo in attesa
        if e["event"] == "BE_PENDING" or (e["event"] == "MT5_ORDER" and e.get("operation") == "BREAKEVEN"):
            if pos["be_request_ts"] is None:
                pos["be_request_ts"] = ts
        if e["event"] == "POSITION_CLOSED":
            pos["real_profit"] = e.get("profit")
            pos["real_reason"] = e.get("close_reason")

    for pos in positions.values():
        pos["label"] = msg_text.get(pos["msg_id"], "")
    return sorted(positions.values(), key=lambda p: p["open_ts"])


def estimate_broker_offset(mt5) -> int:
    """Offset (secondi) dell'orologio del broker rispetto a UTC, da un tick recente."""
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        raise SystemExit("Nessun tick disponibile: indica l'offset con --offset-broker")
    offset = round((tick.time - time.time()) / 1800) * 1800
    return int(offset)


def download_bars(mt5, start_utc: float, end_utc: float, offset: int) -> list:
    """Candele M1 (tempo del broker convertito in UTC, open, high, low, close, spread)."""
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, int(start_utc + offset), int(end_utc + offset))
    if rates is None or len(rates) == 0:
        raise SystemExit(f"MT5 non ha restituito candele: {mt5.last_error()}")
    rows = rates.tolist()
    try:  # via RPyC la lista è remota: la trasferiamo in un colpo solo
        import rpyc
        if isinstance(rows, rpyc.core.netref.BaseNetref):
            rows = rpyc.classic.obtain(rows)
    except ImportError:
        pass
    return [(r[0] - offset, r[1], r[2], r[3], r[4], r[6]) for r in rows]


def simulate(pos: dict, bars: list, min_profit, be_offset: float):
    """
    Rigioca una posizione. min_profit None = nessun BE automatico.
    Ritorna (prezzo di uscita, motivo).
    """
    buy, entry = pos["buy"], pos["entry"]
    levels = list(pos["levels"])
    sl, tp = levels[0][1], levels[0][2]
    be_done = False
    be_price = entry + be_offset if buy else entry - be_offset

    for bar_ts, _open, high, low, close, spread in bars:
        # la candela dell'apertura contiene prezzi precedenti all'ingresso
        if bar_ts < pos["open_ts"] - pos["open_ts"] % 60 + 60:
            continue
        bar_end = bar_ts + 60
        while len(levels) > 1 and levels[1][0] <= bar_end:
            levels.pop(0)
            if not be_done:
                sl = levels[0][1]
            tp = levels[0][2]

        if pos["bot_close_ts"] and pos["bot_close_ts"] <= bar_end:
            return close, "BOT"

        ask_high, ask_low = high + spread * POINT, low + spread * POINT
        if buy:
            sl_hit = sl and low <= sl
            tp_hit = tp and high >= tp
        else:
            sl_hit = sl and ask_high >= sl
            tp_hit = tp and ask_low <= tp
        if sl_hit:
            return sl, "BE" if be_done else "SL"
        if tp_hit:
            return tp, "TP"

        # BE: solo dopo la richiesta del trader e con il guadagno minimo
        if (min_profit is not None and not be_done and pos["be_request_ts"]
                and bar_end >= pos["be_request_ts"]):
            move = (high - entry) if buy else (entry - ask_low)
            if move >= min_profit and ((close > be_price) if buy else (close + spread * POINT < be_price)):
                sl, be_done = be_price, True

    return bars[-1][4], "APERTA"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("giorno", help="AAAA-MM-GG")
    parser.add_argument("--minimi", default="0,2,3,5", help="guadagni minimi in $ da provare (default 0,2,3,5)")
    parser.add_argument("--be-offset", type=float, default=BE_OFFSET_DEFAULT, help="margine del BE in $ (default 0.5)")
    parser.add_argument("--offset-broker", type=float, help="ore del broker rispetto a UTC (default: stimate da MT5)")
    args = parser.parse_args()

    positions = collect_positions(load_events(args.giorno))
    if not positions:
        raise SystemExit("Nessuna posizione aperta dal bot in questo giorno.")

    from mt5_connection import mt5  # import qui: serve MT5 solo per le candele e i profitti
    offset = int(args.offset_broker * 3600) if args.offset_broker is not None else estimate_broker_offset(mt5)
    print(f"Orologio del broker: UTC{offset / 3600:+.1f} h | BE a ingresso {args.be_offset:+}$ | "
          f"{len(positions)} posizioni")

    start = min(p["open_ts"] for p in positions) - 120
    bars = download_bars(mt5, start, time.time(), offset)

    scenarios = [("nessun BE", None)] + [(f"BE min {m:g}$", m) for m in (float(x) for x in args.minimi.split(","))]

    def profit(pos, exit_price):
        order_type = mt5.ORDER_TYPE_BUY if pos["buy"] else mt5.ORDER_TYPE_SELL
        return mt5.order_calc_profit(order_type, SYMBOL, pos["volume"], pos["entry"], exit_price) or 0.0

    header = f"{'apertura':8} {'segnale':28} {'dir':4} {'reale':>16}" + "".join(f"{name:>18}" for name, _ in scenarios)
    print("\n" + header + "\n" + "-" * len(header))
    totals = {name: 0.0 for name, _ in scenarios}
    real_total = 0.0
    for pos in positions:
        real = pos.get("real_profit") or 0.0
        real_total += real
        row = (f"{to_local(pos['open_ts']).strftime('%H:%M'):8} {pos['label']:28} "
               f"{'BUY' if pos['buy'] else 'SELL':4} {real:>8.2f} {pos.get('real_reason', 'n/d')[:7]:>7}")
        for name, minimum in scenarios:
            exit_price, reason = simulate(pos, bars, minimum, args.be_offset)
            value = profit(pos, exit_price)
            totals[name] += value
            row += f"{value:>11.2f} {reason:>6}"
        print(row)
    print("-" * len(header))
    print(f"{'TOTALE':42} {real_total:>8.2f}        " + "".join(f"{totals[name]:>11.2f}       " for name, _ in scenarios))


if __name__ == "__main__":
    main()
