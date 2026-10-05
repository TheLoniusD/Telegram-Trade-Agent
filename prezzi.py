"""
Cosa ha fatto davvero il prezzo dopo ogni segnale di una giornata, sulle
candele a 1 minuto di MT5. Solo lettura: non invia ordini.

Per ogni operazione del diario (logs/AAAA-MM-GG/journal.jsonl), misurando dal
bordo della zona del segnale (il prezzo da cui parte il trader):
  - quanto è andato a favore e contro prima che arrivasse lo SL del trader o il TP1;
  - se lo SL del trader è stato toccato, quando e di quanto è andato oltre;
  - se dopo lo SL il prezzo è poi arrivato al TP1 (lo stop preso "per poco");
  - dopo la richiesta di pareggio (Trade Active / BE), quanto è tornato contro
    rispetto all'ingresso prima del TP1: è la misura per il primo gradino dello
    stop di protezione (oggi ¼ della distanza dallo SL, 2,5 $ su 10 $).
In fondo, per alcuni margini sullo SL (--margini), quante operazioni chiuse a
stop pieno sarebbero sopravvissute e avrebbero poi raggiunto il TP1.

Limiti: candele da 1 minuto (se nella stessa candela il prezzo tocca sia lo SL
che il TP1 si assume prima lo SL, per prudenza); lato BUY si usa il bid, lato
SELL l'ask (bid + spread della candela), come fa il broker per SL e TP.

Uso (dove MT5 è raggiungibile, cioè sul server o sul PC con MT5):
    python prezzi.py 2026-10-05
    python prezzi.py 2026-10-02 --ore 3 --margini 0.5,1,2,3
    python prezzi.py 2026-10-05 --offset-broker 3   # ore del broker rispetto a UTC
"""
import argparse
import time
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from logger_config import to_local
from verifica_be import POINT, download_bars, estimate_broker_offset, load_events

PIPS_PER_DOLLAR = 10  # oro: 1 $ = 10 pips, come li conta il trader


def ts_of(event) -> float:
    return datetime.fromisoformat(event["ts"]).timestamp()


def collect_trades(events: list) -> list:
    """Le operazioni del giorno con zona, SL e TP del trader, risultato e richieste di pareggio."""
    trades, owner, first_text = {}, {}, {}
    for e in events:
        if e["event"] == "MESSAGE" and not e.get("is_edit"):
            first_text.setdefault(e.get("msg_id"), (e.get("text") or "").split("\n")[0][:24])
        if e["event"] != "TRADE_STATUS" or not e.get("ticket_id"):
            continue
        trade = trades.setdefault(e["ticket_id"], {"id": e["ticket_id"], "open_ts": ts_of(e), "msg_id": e.get("trade_msg_id"),
                                                   "be_ts": None, "result": 0.0, "closed": 0, "bot_sl": None})
        trade["status"] = e
        for t in (e.get("tickets") or {}).values():
            if t.get("mt5_ticket"):
                owner[t["mt5_ticket"]] = trade
                trade["buy"] = t.get("direction") == "BUY"
                if t.get("stop_loss") and trade["bot_sl"] is None:
                    trade["bot_sl"] = t["stop_loss"]

    for e in events:
        trade = trades.get(e.get("ticket_id")) or owner.get(e.get("mt5_ticket"))
        if trade is None and e["event"] == "MT5_ORDER":
            trade = owner.get((e.get("request") or {}).get("position"))
        if trade is None:
            continue
        be_request = (e["event"] in ("TRADE_ACTIVE_PLAN", "BE_PENDING")
                      or (e["event"] == "MT5_ORDER" and e.get("operation") == "BREAKEVEN")
                      or (e["event"] == "PENDING_CANCELLED" and "BE" in (e.get("reason") or "")))
        if be_request and trade["be_ts"] is None:
            trade["be_ts"] = ts_of(e)
        if e["event"] == "POSITION_CLOSED" and e.get("profit") is not None:
            trade["result"] += e["profit"]
            trade["closed"] += 1

    result = []
    for trade in trades.values():
        status = trade["status"]
        prices = [p for p in (status.get("entry_min"), status.get("entry_max")) if p is not None]
        if not prices or "buy" not in trade:
            continue
        buy = trade["buy"]
        trade["edge"] = max(prices) if buy else min(prices)
        trade["zone"] = (min(prices), max(prices))
        trade["sl"] = status.get("signal_stop_loss")
        tps = {t.get("take_profit") for t in (status.get("tickets") or {}).values() if t.get("take_profit")}
        # Solo i TP dalla parte giusta (05/10: TP 4137/4127 su un BUY 4157, errore del trader)
        tps = sorted(tp for tp in tps if (tp > trade["edge"]) == buy)
        trade["tps"] = tps if buy else tps[::-1]
        trade["label"] = first_text.get(trade["msg_id"], "")
        result.append(trade)
    return sorted(result, key=lambda t: t["open_ts"])


def excursions(trade: dict, bars: list, hours: float) -> dict:
    """Movimenti dal bordo della zona (in $, positivi = a favore / contro)."""
    buy, edge = trade["buy"], trade["edge"]
    sl_dist = abs(edge - trade["sl"]) if trade["sl"] else None
    tp1_dist = abs(trade["tps"][0] - edge) if trade["tps"] else None
    end = trade["open_ts"] + hours * 3600
    out = {"fav": 0.0, "adv": 0.0, "fav_ts": None, "sl_ts": None, "tp1_ts": None,
           "adv_before_tp1": 0.0, "beyond_sl": 0.0, "fav_after_sl": 0.0, "be_adv": None}
    for bar_ts, _open, high, low, _close, spread in bars:
        # la candela dell'apertura contiene prezzi precedenti all'ingresso
        if bar_ts < trade["open_ts"] - trade["open_ts"] % 60 + 60 or bar_ts > end:
            continue
        ask_high, ask_low = high + spread * POINT, low + spread * POINT
        fav = (high - edge) if buy else (edge - ask_low)
        adv = (edge - low) if buy else (ask_high - edge)

        if out["tp1_ts"] is None:
            out["adv_before_tp1"] = max(out["adv_before_tp1"], adv)
        if out["sl_ts"] is None and out["tp1_ts"] is None:
            if fav > out["fav"]:
                out["fav"], out["fav_ts"] = fav, bar_ts
            out["adv"] = max(out["adv"], adv)
        if sl_dist is not None and out["sl_ts"] is None and out["tp1_ts"] is None and adv >= sl_dist:
            out["sl_ts"] = bar_ts  # nella stessa candela del TP1 si assume prima lo SL
        if sl_dist is not None and out["sl_ts"] is not None:
            out["beyond_sl"] = max(out["beyond_sl"], adv - sl_dist)
            out["fav_after_sl"] = max(out["fav_after_sl"], fav)
        if tp1_dist is not None and out["tp1_ts"] is None and fav >= tp1_dist:
            out["tp1_ts"] = bar_ts
        # Dopo la richiesta di pareggio: quanto torna contro prima del TP1 (o dello SL)
        if trade["be_ts"] and bar_ts + 60 > trade["be_ts"] and out["tp1_ts"] is None and out["sl_ts"] in (None, bar_ts):
            out["be_adv"] = max(out["be_adv"] or -1e9, adv)
    return out


def hm(ts) -> str:
    return to_local(ts).strftime("%H:%M") if ts else "--:--"


def pips(dollars: float) -> str:
    return f"{dollars * PIPS_PER_DOLLAR:.0f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("giorno", help="AAAA-MM-GG")
    parser.add_argument("--ore", type=float, default=4, help="ore di prezzo da guardare dopo ogni segnale (default 4)")
    parser.add_argument("--margini", default="0.5,1,2,3", help="margini sullo SL in $ da provare (default 0.5,1,2,3)")
    parser.add_argument("--offset-broker", type=float, help="ore del broker rispetto a UTC (default: stimate da MT5)")
    args = parser.parse_args()

    trades = collect_trades(load_events(args.giorno))
    if not trades:
        raise SystemExit("Nessuna operazione con prezzo di ingresso in questo giorno.")

    # Sul server passa da mt5_connection (RPyC verso mt5server), sul PC Windows dal pacchetto diretto
    try:
        from mt5_connection import mt5
    except ImportError:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            raise SystemExit(f"MetaTrader 5 non raggiungibile: {mt5.last_error()}")
    offset = int(args.offset_broker * 3600) if args.offset_broker is not None else estimate_broker_offset(mt5)
    start = min(t["open_ts"] for t in trades) - 120
    end = min(time.time(), max(t["open_ts"] for t in trades) + args.ore * 3600)
    bars = download_bars(mt5, start, end, offset)
    print(f"Orologio del broker: UTC{offset / 3600:+.1f} h | {len(trades)} operazioni | "
          f"{args.ore:g} ore di prezzo dopo ogni segnale | 1 $ = {PIPS_PER_DOLLAR} pips\n")

    margins = [float(x) for x in args.margini.split(",")]
    saved = {m: [] for m in margins}
    full_stops = []
    for trade in trades:
        x = excursions(trade, bars, args.ore)
        direction = "BUY" if trade["buy"] else "SELL"
        tps = " / ".join(f"{tp:g}" for tp in trade["tps"]) or "—"
        print(f"{hm(trade['open_ts'])} {direction} {trade['edge']:g} «{trade['label']}» · zona {trade['zone'][0]:g}–{trade['zone'][1]:g} "
              f"· SL trader {trade['sl'] or '—'} (bot {trade['bot_sl'] or '—'}) · TP {tps} · bot {trade['result']:+.2f} $")
        print(f"   a favore max {pips(x['fav'])} pips ({hm(x['fav_ts'])}), contro max {pips(x['adv'])} pips, prima di SL/TP1")
        if x["tp1_ts"] and not x["sl_ts"]:
            print(f"   TP1 raggiunto alle {hm(x['tp1_ts'])}, contro al massimo {x['adv_before_tp1']:.2f} $ prima")
        if x["sl_ts"]:
            line = f"   SL del trader toccato alle {hm(x['sl_ts'])}, oltre al massimo di {x['beyond_sl']:.2f} $"
            line += (f"; poi TP1 alle {hm(x['tp1_ts'])} (massimo contro prima del TP1 {x['adv_before_tp1']:.2f} $)"
                     if x["tp1_ts"] else f"; dopo lo SL a favore max {pips(x['fav_after_sl'])} pips, TP1 mai raggiunto")
            # Pareggio già chiesto prima: il bot non era più esposto allo stop pieno
            after_be = bool(trade["be_ts"] and trade["be_ts"] < x["sl_ts"])
            print(line + (" (dopo la richiesta di pareggio)" if after_be else ""))
            if not after_be:
                full_stops.append(trade)
            if x["tp1_ts"] and not after_be:
                sl_dist = abs(trade["edge"] - trade["sl"])
                for m in margins:
                    if x["adv_before_tp1"] < sl_dist + m:
                        saved[m].append(trade)
        if not x["sl_ts"] and not x["tp1_ts"]:
            print("   né SL né TP1 nel periodo guardato")
        if trade["be_ts"]:
            be_adv = x["be_adv"]
            text = (f"tornato contro fino a {be_adv:.2f} $ dal bordo" if be_adv is not None and be_adv > 0
                    else "mai tornato al bordo della zona")
            print(f"   dopo la richiesta di pareggio ({hm(trade['be_ts'])}): {text} prima del TP1")
        print()

    print(f"Margine sullo SL del trader: su {len(full_stops)} SL toccati prima del pareggio, "
          f"quelli evitati che poi hanno raggiunto il TP1")
    for m in margins:
        names = ", ".join(f"{hm(t['open_ts'])} {'BUY' if t['buy'] else 'SELL'} {t['edge']:g}" for t in saved[m]) or "nessuno"
        print(f"   +{m:g} $: {len(saved[m])} ({names}); gli altri {len(full_stops) - len(saved[m])} stop pieni "
              f"costano circa il {m / 10 * 100:.0f}% in più (con SL a 10 $)")


if __name__ == "__main__":
    main()
