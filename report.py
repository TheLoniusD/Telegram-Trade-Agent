"""
Riassunto leggibile del diario di una giornata (logs/AAAA-MM-GG/journal.jsonl),
con tutti gli orari in ora italiana (fuso LOG_TIMEZONE).

Pensato per quando il bot ha girato da solo: in pochi secondi mostra cosa è
arrivato dal canale, cosa ha deciso il bot, cosa è successo su MT5, come si
sono chiuse le posizioni e dove qualcosa è andato storto.

Uso:
    python report.py                 # oggi
    python report.py 2026-09-23      # un giorno specifico
"""
import json
import sys
from collections import Counter, defaultdict
import os
from datetime import datetime, timedelta

from logger_config import LOG_DIR, LOG_TIMEZONE_NAME, now_local, to_local

# Stima del costo dell'agente classificatore (Claude Haiku 4.5, $ per milione di
# token). La scrittura in cache con TTL 1h costa il doppio dell'input normale,
# la lettura un decimo. È solo un ordine di grandezza: fa fede la console Anthropic.
PRICE_INPUT = 1.00
PRICE_OUTPUT = 5.00
PRICE_CACHE_WRITE_1H = 2.00
PRICE_CACHE_READ = 0.10

# Un battito ogni ora: un buco molto più lungo indica che il bot era fermo
HEARTBEAT_GAP_SECONDS = 1.5 * 3600


def load_events(day: str) -> list:
    """
    Eventi del giorno richiesto, con gli orari nel fuso dei log (ora italiana).
    Legge anche le cartelle del giorno prima e dopo: i log scritti quando il
    server registrava in UTC hanno gli eventi tra mezzanotte e le 02:00
    italiane nella cartella del giorno precedente.
    """
    target = datetime.strptime(day, "%Y-%m-%d")
    events = []
    for offset in (-1, 0, 1):
        folder_day = (target + timedelta(days=offset)).strftime("%Y-%m-%d")
        path = os.path.join(LOG_DIR, folder_day, "journal.jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line_number, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    print(f"⚠️ Riga {line_number} di {path} illeggibile, saltata.")
                    continue
                local_ts = to_local(datetime.fromisoformat(event["ts"]))
                if local_ts.strftime("%Y-%m-%d") == day:
                    event["ts"] = local_ts.isoformat(timespec="seconds")
                    events.append(event)
    if not events:
        sys.exit(f"Nessun evento per il {day} in {LOG_DIR}/.")
    events.sort(key=lambda e: e["ts"])
    return events


def hhmmss(event: dict) -> str:
    return event["ts"][11:19]


def short(text, length=70) -> str:
    text = (text or "").replace("\n", " ⏎ ")
    return text if len(text) <= length else text[:length - 1] + "…"


def describe(event: dict) -> str:
    """Una riga leggibile per ogni tipo di evento."""
    e = event
    kind = e["event"]
    if kind == "MESSAGE":
        return f"{'✏️ EDIT' if e.get('is_edit') else '🆕 NUOVO'}{' (reply a ' + str(e['reply_to']) + ')' if e.get('reply_to') else ''}: {short(e.get('text'))!r}"
    if kind == "MESSAGE_SKIPPED":
        return f"⏭️ scartato: {e.get('reason')}"
    if kind == "CLASSIFIED":
        details = e.get("data") or {}
        extra = [f"{k}={details[k]}" for k in ("direction", "entry_min", "stop_loss", "take_profit") if details.get(k) not in (None, [])]
        if (details.get("update_details") or {}).get("move_sl_to_be"):
            extra.append("BE")
        return f"🧠 {e.get('intent')} ({e.get('reasoning')}) {' '.join(extra)}"
    if kind == "DECISION":
        return f"📦 decisione: {e.get('action')}{' - ' + e['reason'] if e.get('reason') else ''}"
    if kind == "RISK_REJECTED":
        return f"🛑 Risk Manager: {e.get('reason')}"
    if kind == "MT5_ORDER":
        req = e.get("request") or {}
        parts = [f"MT5 {e.get('operation')} #{req.get('position') or e.get('order') or ''}"]
        if e.get("operation") in ("OPEN", "OPEN_LIMIT", "CLOSE", "CLOSE_PARTIAL"):
            parts.append(f"vol {e.get('volume') or req.get('volume')}")
            parts.append(f"prezzo {e.get('price') or req.get('price')}")
        if e.get("operation") not in ("CLOSE", "CLOSE_PARTIAL", "CANCEL"):
            parts.append(f"SL {req.get('sl')} TP {req.get('tp')}")
        if e.get("broker_time"):
            parts.append(f"ora MT5 {e['broker_time'][11:]}")
        if e.get("ok"):
            return "✅ " + " | ".join(parts)
        return "❌ " + " | ".join(parts) + f" → RIFIUTATO: {e.get('error')}"
    if kind == "TRADE_STATUS":
        tickets = ", ".join(
            f"{k}:#{t.get('mt5_ticket')} SL {t.get('stop_loss')} TP {t.get('take_profit')}{' chiuso' if t.get('closed') else ''}{' BE' if t.get('be_active') else ''}{' in attesa' if t.get('pending') else ''}"
            for k, t in (e.get("tickets") or {}).items())
        return f"📋 operazione {e.get('ticket_id')} → {e.get('status')} | {tickets}"
    if kind == "LEVELS_INCOHERENT":
        return f"⚠️ livelli incoerenti con {e.get('direction')} #{e.get('mt5_ticket')}: SL {e.get('stop_loss')} TP {e.get('take_profit')} (non inviati)"
    if kind == "ENTRY_PENDING":
        return f"⏳ {e.get('key')}: ordine limite in attesa a {e.get('limit_price')} (vol {e.get('volume')}) #{e.get('mt5_ticket')}"
    if kind == "PENDING_FILLED":
        return f"✅ ordine limite #{e.get('mt5_ticket')} eseguito a {e.get('price')} (vol {e.get('volume')})"
    if kind == "PENDING_CANCELLED":
        return f"🗑️ ordine limite #{e.get('mt5_ticket')} cancellato: {e.get('reason')}"
    if kind == "PARTIAL_CLOSE_PLAN":
        return f"✂️ chiusura parziale {e.get('percentage')}%: {e.get('target_volume')} lotti su {e.get('open_volume')} aperti"
    if kind == "PARTIAL_CLOSE_REPEATED":
        return f"✂️ chiusura parziale ripetuta dopo {e.get('minutes_since_last')} min: stesso comando, nessun altro volume chiuso"
    if kind == "POSITION_PARTIAL_CLOSED":
        return f"✂️ #{e.get('mt5_ticket')}: chiusi {e.get('closed_volume')} lotti, restano {e.get('remaining_volume')}"
    if kind == "BE_PENDING":
        return f"⏳ BE di #{e.get('mt5_ticket')} in attesa ({e.get('reason') or 'prezzo ancora troppo vicino al livello di BE'}, SL di BE {e.get('entry_price')})"
    if kind == "BE_PENDING_APPLIED":
        return f"🎯 BE in attesa applicato a #{e.get('mt5_ticket')} (SL {e.get('entry_price')})"
    if kind == "RISK_CALC":
        levels = e.get("entry_levels") or {}
        entry = (f" | ingressi TP1 {levels.get('tp1') or 'mercato'} / TP2 {levels.get('tp2') or 'mercato'}"
                 if levels else "")
        return (f"🧮 lotti: rischio {e.get('risk_percent')}% → {e.get('lots') or e.get('lots_by_risk')} | tetto margine "
                f"{e.get('lots_by_margin')} | totale {e.get('lots_total')} (prezzo {e.get('price')}, SL {e.get('stop_loss')}){entry}")
    if kind == "MEMORY_RESYNC":
        return f"🔄 memoria riallineata a MT5 #{e.get('mt5_ticket')}: SL {e.get('stop_loss')} TP {e.get('take_profit')}"
    if kind == "POSITION_CLOSED":
        broker = f" | ora MT5 {e['close_time_broker'][11:]}" if e.get("close_time_broker") else ""
        return f"🏁 #{e.get('mt5_ticket')} chiusa ({e.get('closed_by')}) motivo {e.get('close_reason', 'n/d')} prezzo {e.get('close_price', 'n/d')} profitto {e.get('profit', 'n/d')}{broker}"
    if kind == "MT5_CONNECTION":
        return f"{'✅' if e.get('ok') else '❌'} MT5: {e.get('reason')}"
    if kind == "HEARTBEAT":
        return f"💓 saldo {e.get('balance')} equity {e.get('equity')} operazioni vive {e.get('live_trades')}"
    if kind == "BOT_START":
        return f"▶️ avvio bot | conto {e.get('login')} {'DEMO' if e.get('demo') else 'REALE'} | saldo {e.get('balance')} | test_mode {e.get('test_mode')}"
    if kind == "BOT_STOP":
        return "⏹️ arresto bot"
    if kind == "ERROR":
        last_line = (e.get("error") or "").strip().splitlines()[-1:] or [""]
        return f"💥 ERRORE in {e.get('where')}: {last_line[0]}"
    if kind == "CLASSIFIER_CALL":
        return f"🪙 token in {e.get('input_tokens')} out {e.get('output_tokens')} cache {e.get('cache_read_input_tokens')}"
    return f"{kind} {e}"


def is_anomaly(event: dict) -> bool:
    kind = event["event"]
    return (
        kind in ("ERROR", "RISK_REJECTED", "LEVELS_INCOHERENT", "MEMORY_RESYNC")
        or (kind == "MT5_ORDER" and not event.get("ok"))
        or (kind == "MT5_CONNECTION" and not event.get("ok"))
        or (kind == "TRADE_STATUS" and event.get("status") in ("OPEN_FAILED", "CLOSE_FAILED", "REJECTED"))
    )


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else now_local().strftime("%Y-%m-%d")
    events = load_events(day)
    if not events:
        sys.exit(f"Il diario del {day} è vuoto.")

    by_kind = defaultdict(list)
    for e in events:
        by_kind[e["event"]].append(e)

    print(f"\n==================== REPORT {day} (orari {LOG_TIMEZONE_NAME}) ====================")
    print(f"Periodo: {hhmmss(events[0])} → {hhmmss(events[-1])} | avvii: {len(by_kind['BOT_START'])} | arresti: {len(by_kind['BOT_STOP'])}")

    # --- Messaggi -------------------------------------------------------------
    messages = by_kind["MESSAGE"]
    skipped = Counter(e.get("reason") for e in by_kind["MESSAGE_SKIPPED"])
    print(f"\nMessaggi ricevuti: {len(messages)} (di cui edit: {sum(1 for e in messages if e.get('is_edit'))})")
    for reason, count in skipped.most_common():
        print(f"  scartati prima dell'agente - {reason}: {count}")
    intents = Counter(e.get("intent") for e in by_kind["CLASSIFIED"])
    print("Classificazioni: " + (" | ".join(f"{k} {v}" for k, v in intents.most_common()) or "nessuna"))
    actions = Counter(e.get("action") for e in by_kind["DECISION"])
    print("Decisioni: " + (" | ".join(f"{k} {v}" for k, v in actions.most_common()) or "nessuna"))

    # --- Token ----------------------------------------------------------------
    calls = by_kind["CLASSIFIER_CALL"]
    if calls:
        tot = {k: sum(c.get(k) or 0 for c in calls) for k in
               ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
        cost = (tot["input_tokens"] * PRICE_INPUT + tot["output_tokens"] * PRICE_OUTPUT
                + tot["cache_creation_input_tokens"] * PRICE_CACHE_WRITE_1H
                + tot["cache_read_input_tokens"] * PRICE_CACHE_READ) / 1_000_000
        print(f"Agente: {len(calls)} chiamate | token input {tot['input_tokens']} output {tot['output_tokens']} "
              f"cache letta {tot['cache_read_input_tokens']} cache scritta {tot['cache_creation_input_tokens']} "
              f"| costo stimato ${cost:.4f}")

    # --- MT5 ------------------------------------------------------------------
    orders = by_kind["MT5_ORDER"]
    per_operation = Counter((o.get("operation"), "ok" if o.get("ok") else "rifiutato") for o in orders)
    print(f"\nOrdini MT5 inviati: {len(orders)}")
    for (operation, outcome), count in sorted(per_operation.items()):
        print(f"  {operation} {outcome}: {count}")

    closed = by_kind["POSITION_CLOSED"]
    if closed:
        reasons = Counter(c.get("close_reason", "n/d") for c in closed)
        profit = sum(c.get("profit") or 0 for c in closed)
        print(f"Posizioni chiuse: {len(closed)} (" + ", ".join(f"{k} {v}" for k, v in reasons.most_common()) + f") | profitto netto: {profit:+.2f}")

    # --- Continuità del bot ---------------------------------------------------
    gaps = []
    previous_heartbeat = None
    for e in events:
        if e["event"] == "BOT_START":
            previous_heartbeat = None
        if e["event"] == "HEARTBEAT":
            current = datetime.fromisoformat(e["ts"])
            if previous_heartbeat and (current - previous_heartbeat).total_seconds() > HEARTBEAT_GAP_SECONDS:
                gaps.append(f"nessun battito tra {previous_heartbeat.strftime('%H:%M')} e {current.strftime('%H:%M')} (PC in standby o bot bloccato?)")
            previous_heartbeat = current
    running = False
    for e in events:
        if e["event"] == "BOT_START":
            if running:
                gaps.append(f"riavvio alle {hhmmss(e)} senza arresto registrato prima (crash o PC spento?)")
            running = True
        elif e["event"] == "BOT_STOP":
            running = False
    if gaps:
        print("\nContinuità:")
        for gap in gaps:
            print(f"  ⚠️ {gap}")

    # --- Anomalie ---------------------------------------------------------------
    anomalies = [e for e in events if is_anomaly(e)]
    print(f"\n==================== ANOMALIE ({len(anomalies)}) ====================")
    for e in anomalies:
        where = f"msg {e['msg_id']}" if e.get("msg_id") is not None else "controllo periodico"
        print(f"[{hhmmss(e)}] {where}: {describe(e)}")

    # --- Cronologia per messaggio ---------------------------------------------
    print("\n==================== CRONOLOGIA PER MESSAGGIO ====================")
    per_message = defaultdict(list)
    for e in events:
        if e.get("msg_id") is not None and e["event"] != "CLASSIFIER_CALL":
            per_message[e["msg_id"]].append(e)
    for msg_id, msg_events in per_message.items():
        print(f"\n— msg {msg_id}")
        for e in msg_events:
            print(f"   {hhmmss(e)} {describe(e)}")

    # --- Eventi fuori dai messaggi (controllo periodico, avvii) ---------------
    background = [e for e in events if e.get("msg_id") is None and e["event"] != "HEARTBEAT"]
    if background:
        print("\n==================== BOT E CONTROLLO PERIODICO ====================")
        for e in background:
            print(f"   {hhmmss(e)} {describe(e)}")
    print()


if __name__ == "__main__":
    main()
