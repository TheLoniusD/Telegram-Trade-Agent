"""
Racconto leggibile della giornata, costruito dagli eventi del diario (journal).

Ogni evento registrato con journal.record() passa di qui e diventa (al massimo)
una riga breve in italiano nel bot.log del giorno, con l'etichetta del segnale
a cui si riferisce ([SELL 4180]). I messaggi del canale scartati dai filtri non
compaiono: li conta l'heartbeat. I dettagli tecnici (JSON dell'agente, memoria,
richieste MT5) restano in dettagli.log e journal.jsonl.

Dopo ogni evento che riguarda un'operazione viene riscritto operazioni.txt: una
scheda per segnale con messaggi del trader, ticket, pareggio, esiti e risultato.

Un errore qui non deve mai bloccare il bot: journal.record() chiama queste
funzioni dentro un try/except.
"""

import json
import os
import re
from datetime import datetime, timedelta

from logger_config import LOG_DIR, now_local, setup_logger

logger = setup_logger("racconto")

SHEETS_FILE = "operazioni.txt"

# Eventi che cambiano lo stato di un'operazione: dopo ognuno si riscrivono le schede
SHEET_EVENTS = {"TRADE_STATUS", "PENDING_FILLED", "PENDING_CANCELLED", "POSITION_CLOSED",
                "POSITION_PARTIAL_CLOSED", "BE_PENDING_APPLIED", "MT5_ORDER"}

OPERATION_NAMES = {
    "OPEN": "l'apertura a mercato", "OPEN_LIMIT": "l'ordine limite", "CLOSE": "la chiusura",
    "PARTIAL_CLOSE": "la chiusura parziale", "BREAKEVEN": "il pareggio", "MODIFY_SL_TP": "la modifica di SL/TP",
    "MODIFY_PENDING": "la modifica dell'ordine in attesa", "CANCEL": "la cancellazione",
    "MOVE_PENDING": "lo spostamento dell'ordine in attesa",
    "PROTECTIVE_SL": "lo stop di protezione",
}

CLOSE_REASONS = {
    "TAKE_PROFIT": "a TP", "BOT": "dal bot", "STOP_OUT": "per stop out del broker",
    "MANUALE_PC": "a mano (PC)", "MANUALE_MOBILE": "a mano (telefono)", "MANUALE_WEB": "a mano (web)",
}


# ----------------------------------------------------------------------------
# Formattazione
# ----------------------------------------------------------------------------

def num(value) -> str:
    """4170.0 -> '4.170,00' (formato italiano)."""
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def money(value) -> str:
    if value is None:
        return "n/d"
    return ("+" if value >= 0 else "−") + num(abs(value)) + " $"


def level(value) -> str:
    """Prezzo del segnale nell'etichetta: 4180 invece di 4.180,00 quando è intero."""
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else num(value)


def compact_text(text: str, limit: int = 110) -> str:
    """Testo del trader su una riga: senza righe vuote, spazi doppi e firme."""
    lines = []
    for line in (text or "").replace("\xa0", " ").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line or re.search(r"(?i)money management|bullmaestro|flex me", line):
            continue
        lines.append(line)
    out = " · ".join(lines)
    return out if len(out) <= limit else out[:limit - 1] + "…"


def short_reason(reason) -> str:
    """Motivo di cancellazione di un ordine in attesa, in breve per le schede."""
    reason = str(reason or "")
    if "BE" in reason:
        return "cancellato al pareggio"
    if "chiusura" in reason:
        return "cancellato alla chiusura"
    if "entro" in reason:
        return "scaduto"
    if "broker" in reason:
        return "sparito dal broker"
    return "cancellato"


def label_for(direction, entry_min, reentry=False, entry_max=None) -> str:
    """Etichetta del segnale: direzione e prezzo del segnale (il bordo della zona da cui parte il trader)."""
    if reentry:
        return f"{direction} re-entry"
    price = entry_min
    if entry_min is not None and entry_max is not None:
        price = max(entry_min, entry_max) if direction == "BUY" else min(entry_min, entry_max)
    return f"{direction} {level(price)}".strip()


def describe_classification(entry: dict) -> str:
    intent = entry.get("intent")
    data = entry.get("data") or {}
    details = data.get("update_details") or {}
    tps = " / ".join(num(t) for t in data.get("take_profit") or [])
    if intent == "NEW_SIGNAL":
        if data.get("is_reentry"):
            return f"re-entry {data.get('direction')} (rientro nel setup precedente)"
        parts = [f"nuovo segnale {data.get('direction')} {level(data.get('entry_min'))}".strip()]
        if data.get("stop_loss"):
            parts.append(f"SL {num(data['stop_loss'])}")
        if tps:
            parts.append(f"TP {tps}")
        return " · ".join(parts)
    if intent == "UPDATE_SIGNAL":
        parts = []
        if data.get("entry_min") is not None and data.get("entry_max") is not None:
            parts.append(f"zona {level(data['entry_min'])}–{level(data['entry_max'])}")
        if data.get("stop_loss"):
            parts.append(f"SL {num(data['stop_loss'])}")
        if tps:
            parts.append(f"TP {tps}")
        if details.get("close_percentage"):
            parts.append(f"chiudi il {details['close_percentage']:g}%")
        if details.get("layer_target"):
            parts.append(f"layer {details['layer_target']}")
        if details.get("move_sl_to_be"):
            parts.append("pareggio")
        return ("aggiornamento: " + " · ".join(parts)) if parts else "aggiornamento senza modifiche"
    if intent == "CLOSE_SIGNAL":
        return "chiudi tutto"
    return f"nessuna azione ({entry.get('reasoning') or 'nessun comando'})"


# ----------------------------------------------------------------------------
# Racconto (bot.log)
# ----------------------------------------------------------------------------

class Narrator:
    def __init__(self):
        self.loaded = False
        self.messages = {}        # msg_id -> ultimo MESSAGE non ancora raccontato
        self.classified = {}      # msg_id -> dati dell'ultima classificazione
        self.risk = {}            # ticket_id -> RISK_CALC
        self.trades = {}          # ticket_id -> stato per il racconto
        self.tickets = {}         # numero MT5 -> (ticket_id, chiave)
        self.skipped = 0          # messaggi scartati dall'ultimo heartbeat
        self.emit = True

    # -- utilità -------------------------------------------------------------

    def say(self, text: str) -> None:
        if self.emit:
            logger.info(text)

    def trade_of(self, mt5_ticket):
        tid, key = self.tickets.get(mt5_ticket, (None, None))
        return self.trades.get(tid), key

    def tag(self, trade) -> str:
        return f"[{trade['label']}] " if trade else ""

    def label_from_message(self, msg_id) -> str:
        data = self.classified.get(msg_id) or {}
        if not data.get("direction"):
            return ""
        return f"[{label_for(data['direction'], data.get('entry_min'), data.get('is_reentry'), data.get('entry_max'))}] "

    # -- caricamento iniziale ------------------------------------------------

    def ensure_loaded(self) -> None:
        """Ricostruisce lo stato (etichette, ticket, profitti) dal diario di ieri e di oggi."""
        if self.loaded:
            return
        self.loaded = True
        self.emit = False
        try:
            today = now_local()
            for day in ((today - timedelta(days=1)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")):
                for past in read_events(day):
                    self.handle(past)
        finally:
            self.emit = True
            self.skipped = 0

    # -- eventi --------------------------------------------------------------

    def handle(self, e: dict) -> None:
        kind = e.get("event")
        handler = getattr(self, f"on_{kind.lower()}", None) if kind else None
        if handler:
            handler(e)

    def on_bot_start(self, e):
        conto = "DEMO" if e.get("demo") else "⚠️ REALE"
        self.say(f"▶️ Bot avviato · conto {conto} · saldo {num(e.get('balance'))} $ · equity {num(e.get('equity'))} $")

    def on_bot_stop(self, e):
        self.say("⏹️ Bot fermato")

    def on_message(self, e):
        self.messages[e.get("msg_id")] = e

    def on_message_skipped(self, e):
        self.messages.pop(e.get("msg_id"), None)
        self.skipped += 1

    def on_classified(self, e):
        msg_id = e.get("msg_id")
        self.classified[msg_id] = e.get("data") or {}
        message = self.messages.pop(msg_id, None)
        if message:
            verb = "✏️ Trader modifica" if message.get("is_edit") else "📩 Trader"
            self.say(f"{verb}: \"{compact_text(message.get('text'))}\"")
        self.say(f"🧠 Agente: {describe_classification(e)}")

    def on_decision(self, e):
        if e.get("action") == "IGNORE":
            self.say(f"   ↳ nessuna azione: {e.get('reason')}")

    def on_risk_calc(self, e):
        self.risk[e.get("ticket_id")] = e

    def on_risk_rejected(self, e):
        self.say(f"⛔ {self.label_from_message(e.get('msg_id'))}non aperto: {e.get('reason')}")

    def on_trade_status(self, e):
        tid = e.get("ticket_id")
        tickets = e.get("tickets") or {}
        trade = self.trades.get(tid)
        first = trade is None
        if first:
            any_ticket = next(iter(tickets.values()), {})
            signal = self.classified.get(e.get("trade_msg_id")) or {}
            entry_min = e.get("entry_min") if e.get("entry_min") is not None else signal.get("entry_min")
            entry_max = e.get("entry_max") if e.get("entry_max") is not None else signal.get("entry_max")
            label = label_for(any_ticket.get("direction"), entry_min, e.get("reentry"), entry_max)
            # Due segnali allo stesso prezzo (il 29/09 due SELL 4156 a 15 minuti):
            # l'ora di apertura li distingue
            if any(t["label"] == label for t in self.trades.values()):
                label += f" delle {e.get('ts', '')[11:16]}"
            trade = self.trades[tid] = {
                "label": label,
                "snapshot": {}, "alive": set(), "profit": 0.0,
            }
        for key, t in tickets.items():
            if t.get("mt5_ticket"):
                self.tickets[t["mt5_ticket"]] = (tid, key)
                if not t.get("closed"):
                    trade["alive"].add(t["mt5_ticket"])

        if first:
            self.say_opening(trade, e, tickets)
        else:
            self.say_changes(trade, tickets)
            if e.get("status") == "CLOSE_FAILED":
                self.say(f"⚠️ {self.tag(trade)}chiusura incompleta: controllare le posizioni su MT5")
        trade["snapshot"] = {k: dict(v) for k, v in tickets.items()}

    def say_opening(self, trade, e, tickets):
        if e.get("status") == "OPEN_FAILED":
            self.say(f"❌ {self.tag(trade)}nessun ordine aperto")
            return
        parts = []
        for key, t in tickets.items():
            if not t.get("mt5_ticket"):
                parts.append(f"{key} rifiutato")
            elif t.get("pending"):
                parts.append(f"{key} {num(t.get('entry_price'))} in attesa")
            else:
                parts.append(f"{key} {num(t.get('entry_price'))} eseguito")
        risk = self.risk.get(e.get("ticket_id")) or {}
        sl = next((t.get("stop_loss") for t in tickets.values() if t.get("stop_loss")), None)
        sl_text = f"SL {num(sl)}"
        if str(risk.get("stop_loss_source", "")).startswith("provvisorio"):
            sl_text += " provvisorio"
        tps = sorted({t.get("take_profit") for t in tickets.values() if t.get("take_profit")},
                     reverse=next(iter(tickets.values()), {}).get("direction") == "SELL")
        tp_text = f" · TP {' / '.join(num(tp) for tp in tps)}" if tps else ""
        lots = f" · {num(risk['lots_total'])} lotti" if risk.get("lots_total") is not None else ""
        self.say(f"📥 {self.tag(trade)}{len(tickets)} ordini: {' · '.join(parts)} | {sl_text}{tp_text}{lots}")

    def say_changes(self, trade, tickets):
        old = trade["snapshot"]
        be, sl_changes, tp_changed = [], {}, False
        for key, t in tickets.items():
            before = old.get(key, {})
            if not t.get("mt5_ticket") or t.get("closed"):
                continue
            if t.get("be_active") and not before.get("be_active"):
                be.append(f"{key} SL {num(t.get('stop_loss'))}")
                continue
            if t.get("stop_loss") != before.get("stop_loss"):
                sl_changes[key] = t.get("stop_loss")
            if t.get("take_profit") != before.get("take_profit"):
                tp_changed = True
        if be:
            self.say(f"🛡️ {self.tag(trade)}pareggio: {' · '.join(be)}")
        if sl_changes or tp_changed:
            parts = []
            if sl_changes:
                values = set(sl_changes.values())
                parts.append(f"SL {num(values.pop())}" if len(values) == 1
                             else " · ".join(f"{k} SL {num(v)}" for k, v in sl_changes.items()))
            if tp_changed:
                live = [t for t in tickets.values() if t.get("mt5_ticket") and not t.get("closed")]
                tps = sorted({t.get("take_profit") for t in live if t.get("take_profit")},
                             reverse=bool(live) and live[0].get("direction") == "SELL")
                parts.append(f"TP {' / '.join(num(tp) for tp in tps)}" if tps else "TP rimossi")
            self.say(f"✏️ {self.tag(trade)}livelli aggiornati: {' · '.join(parts)}")

    def on_pending_filled(self, e):
        trade, key = self.trade_of(e.get("mt5_ticket"))
        if e.get("note"):
            self.say(f"⚡ {self.tag(trade)}{key} eseguito e chiuso subito, tra due controlli")
        else:
            self.say(f"✅ {self.tag(trade)}{key} eseguito a {num(e.get('price'))}")

    def on_pending_moved(self, e):
        trade, key = self.trade_of(e.get("mt5_ticket"))
        if e.get("new_price") is None:
            return
        if trade and key in trade["snapshot"]:
            trade["snapshot"][key]["entry_price"] = e["new_price"]
        self.say(f"↔️ {self.tag(trade)}{key} avvicinato da {num(e.get('old_price'))} a {num(e['new_price'])}: {e.get('reason')}")

    def on_pending_cancelled(self, e):
        trade, key = self.trade_of(e.get("mt5_ticket"))
        if trade:
            trade["alive"].discard(e.get("mt5_ticket"))
        self.say(f"🗑️ {self.tag(trade)}{key} cancellato, mai eseguito: {e.get('reason')}")

    def on_be_pending(self, e):
        trade, key = self.trade_of(e.get("mt5_ticket"))
        self.say(f"⏳ {self.tag(trade)}{key} pareggio a {num(e.get('entry_price'))} in attesa: "
                 f"{e.get('reason') or 'prezzo ancora troppo vicino'}, riprovo ogni 30 secondi")

    def on_protective_stop(self, e):
        trade, key = self.trade_of(e.get("mt5_ticket"))
        if trade and key in trade["snapshot"]:
            trade["snapshot"][key].update(stop_loss=e.get("stop_loss"), protected=True)
        self.say(f"🛟 {self.tag(trade)}{key} pareggio non ancora possibile (a {num(e.get('be_price'))}): "
                 f"stop di protezione da {num(e.get('previous_stop_loss'))} a {num(e.get('stop_loss'))}")

    def on_stop_restored(self, e):
        trade, key = self.trade_of(e.get("mt5_ticket"))
        if e.get("ok"):
            self.say(f"⚠️ {self.tag(trade)}{key} su MT5 era senza SL: rimesso a {num(e.get('stop_loss'))}")
        else:
            self.say(f"❌ {self.tag(trade)}{key} su MT5 è senza SL e non è stato possibile rimetterlo: verificare a mano")

    def on_be_pending_applied(self, e):
        trade, key = self.trade_of(e.get("mt5_ticket"))
        if trade and key in trade["snapshot"]:
            trade["snapshot"][key].update(be_active=True, stop_loss=e.get("entry_price"))
        self.say(f"🛡️ {self.tag(trade)}{key} pareggio applicato: SL {num(e.get('entry_price'))}")

    def on_levels_incoherent(self, e):
        trade, key = self.trade_of(e.get("mt5_ticket"))
        self.say(f"⚠️ {self.tag(trade)}{key} livelli incoerenti con un {e.get('direction')} "
                 f"(SL {num(e.get('stop_loss'))}, TP {num(e.get('take_profit'))}): non inviati")

    def on_trade_active_plan(self, e):
        trade = self.trades.get(e.get("ticket_id"))
        if e.get("note"):
            self.say(f"💰 {self.tag(trade)}Trade Active {e.get('trader_pips'):g} pips: {e['note']}")
            return
        parts = []
        if e.get("closed"):
            parts.append(f"incasso {', '.join(e['closed'])}")
        if e.get("in_loss"):
            parts.append(f"{', '.join(e['in_loss'])} in perdita, non chiusi")
        self.say(f"💰 {self.tag(trade)}Trade Active {e.get('trader_pips'):g} pips: {' · '.join(parts) or 'nulla da incassare'}")

    def on_partial_close_plan(self, e):
        skipped = [self.trade_of(t)[1] or f"#{t}" for t in e.get("skipped_in_loss") or []]
        note = f" · {', '.join(skipped)} in perdita, non chiusi" if skipped else ""
        self.say(f"✂️ Chiusura parziale del {e.get('percentage'):g}%: {num(e.get('target_volume'))} "
                 f"lotti su {num(e.get('open_volume'))}{note}")

    def on_partial_close_repeated(self, e):
        self.say(f"✂️ Chiusura parziale ripetuta dopo {e.get('minutes_since_last')} minuti: trattata come la stessa, ignorata")

    def on_position_partial_closed(self, e):
        trade, key = self.trade_of(e.get("mt5_ticket"))
        self.say(f"✂️ {self.tag(trade)}{key} chiusi {num(e.get('closed_volume'))} lotti, "
                 f"restano {num(e.get('remaining_volume'))}")

    def on_position_closed(self, e):
        mt5_ticket = e.get("mt5_ticket")
        trade, key = self.trade_of(mt5_ticket)
        reason = e.get("close_reason")
        if e.get("closed_by") == "TRADE_ACTIVE":
            how = "incassato al Trade Active a"
        elif reason == "STOP_LOSS":
            snapshot = trade["snapshot"].get(key, {}) if trade else {}
            if snapshot.get("be_active"):
                how = "a pareggio"
            elif snapshot.get("protected"):
                how = "allo stop di protezione"
            else:
                how = "a stop"
        else:
            how = CLOSE_REASONS.get(reason, "(motivo n/d)")
        line = f"🏁 {self.tag(trade)}{key or '#' + str(mt5_ticket)} chiuso {how} {num(e.get('close_price'))} · {money(e.get('profit'))}"
        if trade:
            trade["profit"] += e.get("profit") or 0
            trade["alive"].discard(mt5_ticket)
            if not trade["alive"]:
                line += f" · operazione chiusa: {money(trade['profit'])}"
        self.say(line)

    def on_mt5_order(self, e):
        if e.get("ok"):
            return
        request = e.get("request") or {}
        trade, key = self.trade_of(request.get("position") or request.get("order"))
        prefix = self.tag(trade) if trade else self.label_from_message(e.get("msg_id"))
        what = OPERATION_NAMES.get(e.get("operation"), e.get("operation"))
        self.say(f"❌ {prefix}MT5 rifiuta {what}{' di ' + key if key else ''}: {e.get('error')}")

    def on_mt5_connection(self, e):
        if e.get("ok"):
            self.say("🔌 MT5 di nuovo collegato")
        else:
            self.say(f"🔌 MT5 non disponibile: {e.get('reason')}")

    def on_heartbeat(self, e):
        state = "" if e.get("mt5_ok") else f" · ⚠️ MT5: {e.get('mt5_reason')}"
        self.say(f"💓 Bot attivo · saldo {num(e.get('balance'))} $ · equity {num(e.get('equity'))} $ · "
                 f"{e.get('live_trades', 0)} operazioni aperte · {self.skipped} messaggi scartati dall'ultimo controllo{state}")
        self.skipped = 0

    def on_error(self, e):
        last = [line for line in str(e.get("error") or "").strip().splitlines() if line.strip()]
        self.say(f"❌ Errore in {e.get('where')}: {last[-1] if last else 'sconosciuto'} (dettagli in errors.log)")


_narrator = Narrator()


def narrate(entry: dict) -> None:
    """Da chiamare PRIMA di scrivere l'evento nel diario (vedi journal.record)."""
    _narrator.ensure_loaded()
    _narrator.handle(entry)


# ----------------------------------------------------------------------------
# Schede delle operazioni (operazioni.txt)
# ----------------------------------------------------------------------------

def read_events(day: str) -> list:
    path = os.path.join(LOG_DIR, day, "journal.jsonl")
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def hhmm(ts: str) -> str:
    return ts[11:16] if ts else "--:--"


def build_sheets(day: str) -> str:
    """Testo di operazioni.txt per le operazioni aperte nel giorno indicato."""
    next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    events = read_events(day) + read_events(next_day)

    trades, order = {}, []
    ticket_owner = {}
    # Solo le versioni dei messaggi lette dall'agente (non quelle scartate dai filtri)
    messages, waiting, signal_data = {}, {}, {}
    for e in events:
        kind = e.get("event")
        if kind == "CLASSIFIED" and e.get("intent") in ("NEW_SIGNAL", "UPDATE_SIGNAL"):
            # Ultimi livelli scritti dal trader per ogni messaggio (per i giorni
            # registrati prima che le operazioni li salvassero nel diario)
            known = signal_data.setdefault(e.get("msg_id"), {})
            for field in ("entry_min", "entry_max", "stop_loss"):
                if (e.get("data") or {}).get(field) is not None:
                    known[field] = e["data"][field]
        if kind == "MESSAGE":
            waiting[e.get("msg_id")] = e
        elif kind == "MESSAGE_SKIPPED":
            waiting.pop(e.get("msg_id"), None)
        elif kind == "CLASSIFIED":
            if e.get("msg_id") in waiting:
                messages.setdefault(e.get("msg_id"), []).append(waiting.pop(e.get("msg_id")))
        elif kind == "TRADE_STATUS":
            tid = e["ticket_id"]
            if tid not in trades:
                if not e["ts"].startswith(day):
                    continue
                trades[tid] = {"first": e, "msgs": set(), "closes": {}, "cancels": {}, "be": [], "protect": [], "fills": {}}
                order.append(tid)
            trades[tid]["last"] = e
            trades[tid]["msgs"].add(e.get("msg_id"))
            for key, t in (e.get("tickets") or {}).items():
                if t.get("mt5_ticket"):
                    ticket_owner[t["mt5_ticket"]] = (tid, key)

    for e in events:
        kind = e.get("event")
        owner = None
        if kind in ("POSITION_CLOSED", "PENDING_CANCELLED", "PENDING_FILLED"):
            owner = ticket_owner.get(e.get("mt5_ticket"))
        elif kind == "MT5_ORDER" and e.get("operation") in ("BREAKEVEN", "PROTECTIVE_SL") and e.get("ok"):
            owner = ticket_owner.get((e.get("request") or {}).get("position"))
        if not owner or owner[0] not in trades:
            continue
        trade, key = trades[owner[0]], owner[1]
        if kind == "POSITION_CLOSED":
            trade["closes"][key] = e
        elif kind == "PENDING_CANCELLED":
            trade["cancels"][key] = e
        elif kind == "PENDING_FILLED":
            trade["fills"][key] = e
        elif e.get("operation") == "PROTECTIVE_SL":
            trade["protect"].append((hhmm(e["ts"]), key, (e.get("request") or {}).get("sl")))
        else:
            trade["be"].append((hhmm(e["ts"]), key, (e.get("request") or {}).get("sl")))

    # Messaggi del trader collegati: il segnale (e le sue modifiche), quelli che
    # hanno aggiornato l'operazione e quelli in risposta al segnale.
    blocks, total_closed = [], 0.0
    for tid in order:
        trade = trades[tid]
        first, last = trade["first"], trade["last"]
        root = first.get("trade_msg_id")
        tickets = last.get("tickets") or {}
        any_ticket = next(iter(tickets.values()), {})
        known = signal_data.get(root, {})
        entry_min = first.get("entry_min") if first.get("entry_min") is not None else known.get("entry_min")
        entry_max = first.get("entry_max") if first.get("entry_min") is not None else known.get("entry_max")
        zone_min = last.get("entry_min") if last.get("entry_min") is not None else known.get("entry_min")
        zone_max = last.get("entry_max") if last.get("entry_max") is not None else known.get("entry_max")
        label = label_for(any_ticket.get("direction"), entry_min, first.get("reentry"), entry_max)

        profit = sum(c.get("profit") or 0 for c in trade["closes"].values())
        alive = [k for k, t in tickets.items() if t.get("mt5_ticket") and k not in trade["closes"] and k not in trade["cancels"]]
        opened = [k for k, t in tickets.items() if t.get("mt5_ticket")]
        if last.get("status") in ("OPEN_FAILED", "REJECTED") or not opened:
            state = "NON APERTO"
        elif alive:
            state = f"IN CORSO ({money(profit)} già chiusi)" if trade["closes"] else "IN CORSO"
        else:
            state = f"CHIUSO {money(profit)}"
            total_closed += profit

        lines = [f"═══ {label} · aperto {hhmm(first['ts'])} · {state} ═══"]
        zone = ""
        if zone_min is not None and zone_max is not None:
            zone = f"Zona {level(zone_min)}–{level(zone_max)} · "
        tps = sorted({t.get("take_profit") for t in tickets.values() if t.get("take_profit")},
                     reverse=any_ticket.get("direction") == "SELL")
        sl = last.get("signal_stop_loss") or known.get("stop_loss") or any_ticket.get("stop_loss")
        lines.append(f"{zone}SL {num(sl)} · TP {' / '.join(num(tp) for tp in tps) or '—'}")

        related = []
        for msg_id, versions in messages.items():
            linked = msg_id == root or msg_id in trade["msgs"] or any(v.get("reply_to") == root for v in versions)
            if not linked:
                continue
            for v in versions:
                edit = "(modifica) " if v.get("is_edit") else ""
                related.append((v["ts"], f"  {hhmm(v['ts'])}  {edit}{compact_text(v.get('text'), 90)}"))
        if related:
            lines.append("")
            lines.append("Trader")
            lines.extend(text for _, text in sorted(related))

        lines.append("")
        lines.append(f"{'Ticket':<8}{'ingresso':<13}{'TP':<11}{'esito':<34}{'MT5'}")
        for key, t in tickets.items():
            fill = trade["fills"].get(key)
            entry = num(fill.get("price") if fill and fill.get("price") else t.get("entry_price"))
            if key in trade["cancels"] or (t.get("pending") and key not in trade["fills"]):
                entry = f"({entry})"
            close, cancel = trade["closes"].get(key), trade["cancels"].get(key)
            if not t.get("mt5_ticket"):
                outcome = "rifiutato da MT5"
            elif close:
                reason = close.get("close_reason")
                if reason == "STOP_LOSS":
                    how = "pareggio" if any(k == key for _, k, _ in trade["be"]) else \
                        "protezione" if any(k == key for _, k, _ in trade["protect"]) else "stop"
                else:
                    how = {"TAKE_PROFIT": "TP", "BOT": "bot"}.get(reason, reason or "chiuso")
                if close.get("closed_by") == "TRADE_ACTIVE":
                    how = "incasso TA"
                outcome = f"{how} {hhmm(close['ts'])} {money(close.get('profit')):>12}"
            elif cancel:
                outcome = short_reason(cancel.get("reason"))
            elif t.get("pending"):
                outcome = "in attesa"
            else:
                outcome = "aperto"
            lines.append(f"  {key:<6}{entry:<13}{num(t.get('take_profit')):<11}{outcome:<34}{t.get('mt5_ticket') or ''}")

        if trade["be"]:
            lines.append("")
            by_time = {}
            for time_, key, sl_value in trade["be"]:
                by_time.setdefault(time_, []).append(f"{key} SL {num(sl_value)}")
            for time_, parts in by_time.items():
                lines.append(f"Pareggio {time_} → {' · '.join(parts)}")
        if trade["protect"]:
            if not trade["be"]:
                lines.append("")
            for time_, key, sl_value in trade["protect"]:
                lines.append(f"Stop di protezione {time_} → {key} SL {num(sl_value)} (pareggio rifiutato da MT5)")
        blocks.append("\n".join(lines))

    header = (f"Operazioni del {day} · {len(order)} segnali · risultato delle operazioni chiuse: "
              f"{money(total_closed)} · aggiornato alle {now_local().strftime('%H:%M')}")
    return header + "\n\n" + "\n\n".join(blocks) + "\n"


def write_sheets(day: str) -> None:
    folder = os.path.join(LOG_DIR, day)
    if not os.path.isdir(folder):
        return
    text = build_sheets(day)
    tmp = os.path.join(folder, SHEETS_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, os.path.join(folder, SHEETS_FILE))


def refresh_sheets(entry: dict) -> None:
    """Da chiamare DOPO aver scritto l'evento nel diario."""
    if entry.get("event") not in SHEET_EVENTS:
        return
    today = now_local()
    write_sheets(today.strftime("%Y-%m-%d"))
    # Un'operazione aperta ieri che si chiude oggi va aggiornata nella scheda di ieri
    write_sheets((today - timedelta(days=1)).strftime("%Y-%m-%d"))
