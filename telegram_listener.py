import asyncio
import json
import os
import re
import time
import traceback

from telethon import TelegramClient, events

from dotenv import load_dotenv
load_dotenv()

import journal
from agent_classifier import agent_classify_telegram_message
from order_manager import OrderManager, CLOSABLE_STATUSES
from risk_manager import RiskManager
from mt5_executor import MT5Executor, MT5UnavailableError
from market_hours import is_market_time_open
from logger_config import setup_logger

logger = setup_logger(__name__)

manager = OrderManager()
risk_agent = RiskManager()
mt5_agent = MT5Executor()

# ==========================================
# CONFIGURAZIONI TELEGRAM
# ==========================================
# Puoi usare le variabili d'ambiente o inserire i dati direttamente qui sotto
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Può essere il link (es: 'https://t.me/tuocanale'), l'username (es: '@tuocanale') o l'ID numerico del canale/gruppo
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "")

# Inizializzazione del client Telethon (creerà una sessione locale chiamata 'session_test')
tg_client = TelegramClient('session_test', TELEGRAM_API_ID, TELEGRAM_API_HASH)

# Mittenti autorizzati: ID Telegram separati da virgola in ALLOWED_SENDER_IDS (.env).
# Vuoto = accetta tutti i messaggi di TARGET_CHANNEL: è il caso del canale
# ufficiale, dove i post arrivano con l'ID del canale e non di una persona.
ALLOWED_SENDER_IDS = {int(x) for x in os.getenv("ALLOWED_SENDER_IDS", "").replace(" ", "").split(",") if x}

# Simbolo su cui verifichiamo l'apertura del mercato prima di classificare
TRADED_SYMBOL = "XAUUSD"

# Controllo periodico mentre il bot gira da solo: posizioni chiuse dal broker
# (TP/SL scattati, chiusure manuali), stato della connessione a MT5 e un
# battito di vita nel log per capire, a posteriori, se il bot era attivo.
MONITOR_INTERVAL_SECONDS = 30
HEARTBEAT_INTERVAL_SECONDS = 3600

# I messaggi inoltrati nel canale sono testimonianze dei follower ("Thanks sir",
# "Got it..continue sell👍"), mai segnali del trader: il 23/09 lo erano tutti.
# Scartarli prima dell'agente risparmia token ed evita che un "continue sell"
# di un follower venga scambiato per un comando.
IGNORE_FORWARDED_MESSAGES = True

# Margine del BE in $ oltre il prezzo di ingresso: lo SL va a ingresso + BE_OFFSET
# per un BUY e a ingresso - BE_OFFSET per un SELL, così un ritorno al prezzo di
# ingresso chiude con un piccolo guadagno invece che a zero. 0 = BE esatto.
# Il 23/09 un SELL portato a BE esatto si è chiuso a 0,00 due minuti dopo.
BE_OFFSET = float(os.getenv("BE_OFFSET", "0.5"))

# Filtro locale PRIMA dell'agente: un messaggio senza nemmeno una parola del
# lessico operativo (solo emoji, "Another profitable day💪", "Thanks sir",
# "READY US SESSION") non può essere un comando, quindi non paghiamo una
# classificazione per sentirci dire IGNORE. L'elenco è volutamente largo:
# meglio una chiamata in più che un segnale perso. Verificato su tutti i
# messaggi del 23/09: nessun segnale o comando reale viene scartato.
OPERATIVE_KEYWORDS = re.compile(
    r"\b(buy|sell|long|short|tp\d?|sl|be\+?|b/e|break\s*even|breakeven|close[ds]?|closing|exit|cut|invalid\w*|"
    r"entry|entries|hit|protect|secure|layers?|collect|half|partial|stop|targets?|gold|xau\w*|again|"
    r"zero\s*float|trade\s*active|risk|hold|pips?)\b",
    re.IGNORECASE,
)

# Il trader tende a ripetere lo stesso comando a distanza di pochi minuti
# (23/09: "Lets close half now" alle 13:40, "Running 65 pips, close half set
# your BE" alle 13:42). Una seconda chiusura parziale sulla stessa operazione
# entro questa finestra è considerata la stessa istruzione: non si chiude altro
# volume (il BE richiesto nel messaggio viene comunque applicato).
PARTIAL_CLOSE_REPEAT_WINDOW_SECONDS = 15 * 60

# Ultimo testo visto per ogni messaggio: il canale genera molti edit che non
# cambiano il testo (il 23/09 15 su 20). Riclassificarli costa token e rischia
# di ripetere azioni già eseguite.
_last_text_by_msg: dict = {}
_LAST_TEXT_MAX_ENTRIES = 1000


def levels_match_direction(direction: str, stop_loss, take_profit) -> bool:
    """
    SL e TP devono stare ai lati opposti coerenti con la direzione: per un BUY
    SL sotto e TP sopra, per un SELL il contrario. Intercetta un messaggio con
    i livelli dell'operazione opposta (es. edit "GOLD BUY" su una posizione SELL),
    che MT5 rifiuterebbe comunque con "Invalid stops".
    """
    if stop_loss is None or take_profit is None:
        return True
    if direction == "BUY":
        return stop_loss < take_profit
    if direction == "SELL":
        return stop_loss > take_profit
    return True


def breakeven_price(tp_config: dict):
    """Prezzo dello SL di breakeven per un ticket, con l'eventuale margine BE_OFFSET."""
    entry = tp_config.get("entry_price")
    if entry is None:
        return None
    offset = BE_OFFSET if tp_config.get("direction") == "BUY" else -BE_OFFSET
    return round(entry + offset, 2)


def mark_closed_if_complete(trade: dict) -> None:
    """Un'operazione è chiusa quando lo sono tutti i suoi ticket aperti su MT5."""
    opened = [t for t in trade.get("tickets", {}).values() if t.get("mt5_ticket")]
    if opened and all(t.get("closed") for t in opened):
        trade["status"] = "CLOSED"


def execute_partial_close(trades: list, percentage: float) -> None:
    """
    "Close half" e simili: chiude circa la percentuale indicata del volume ancora
    aperto sull'intera famiglia (originale + re-entry). Chiude per primi i ticket
    con il TP più LONTANO, che raramente viene raggiunto, e tiene aperti quelli
    col TP più vicino, che hanno più probabilità di incassare; se serve, l'ultimo
    ticket viene chiuso solo in parte. Il 23/09 alle 13:40 questa scelta avrebbe
    reso circa +390 contro circa +15 chiudendo il ticket col TP vicino, che poi è
    stato raggiunto (+381), mentre l'altro è tornato al BE.
    """
    now = time.time()
    last_partial = max((t.get("partial_close_at") or 0 for t in trades), default=0)
    if now - last_partial < PARTIAL_CLOSE_REPEAT_WINDOW_SECONDS:
        minutes = int((now - last_partial) / 60)
        logger.info(f"ℹ️ Chiusura parziale già eseguita {minutes} minuti fa su questa operazione: "
                    f"considerata una ripetizione dello stesso comando, nessun altro volume chiuso.")
        journal.record("PARTIAL_CLOSE_REPEATED", minutes_since_last=minutes, percentage=percentage)
        return

    open_tickets = [(trade, t) for trade in trades for t in trade.get("tickets", {}).values()
                    if t.get("mt5_ticket") and not t.get("closed")]
    if not open_tickets:
        logger.info("ℹ️ Chiusura parziale richiesta ma nessuna posizione aperta.")
        return
    for trade in trades:
        trade["partial_close_at"] = now

    def tp_distance(item):
        tp, entry = item[1].get("take_profit"), item[1].get("entry_price")
        return abs(tp - entry) if tp is not None and entry is not None else float("inf")

    # Senza TP (fase rapida non ancora completata) la distanza è infinita:
    # quei ticket vengono chiusi per primi, come i più lontani.
    open_tickets.sort(key=tp_distance, reverse=True)
    open_volume = sum(t.get("volume") or 0 for _, t in open_tickets)
    to_close = open_volume * percentage / 100
    logger.info(f"✂️ Chiusura parziale {percentage:g}%: {to_close:.3f} lotti su {open_volume:.2f} aperti "
                f"(arrotondati per difetto allo step del broker).")
    journal.record("PARTIAL_CLOSE_PLAN", percentage=percentage, open_volume=round(open_volume, 2),
                   target_volume=round(to_close, 3))

    for trade, tp_config in open_tickets:
        if to_close < 1e-9:
            break
        mt5_ticket = tp_config["mt5_ticket"]
        volume = tp_config.get("volume") or 0

        if volume <= to_close + 1e-9:
            if mt5_agent.close_position(ticket=mt5_ticket, symbol=tp_config.get("symbol")):
                tp_config["closed"] = True
                to_close -= volume
                record_position_closed(mt5_ticket, closed_by="CHIUSURA_PARZIALE", trade=trade)
            continue

        closed_volume = mt5_agent.partial_close_position(mt5_ticket, to_close)
        if closed_volume:
            tp_config["volume"] = round(volume - closed_volume, 2)
            if tp_config["volume"] <= 0:
                tp_config["closed"] = True
                record_position_closed(mt5_ticket, closed_by="CHIUSURA_PARZIALE", trade=trade)
            else:
                journal.record("POSITION_PARTIAL_CLOSED", mt5_ticket=mt5_ticket, closed_volume=closed_volume,
                               remaining_volume=tp_config["volume"], ticket_id=trade.get("ticket_id"))
        break

    for trade in trades:
        mark_closed_if_complete(trade)


def trade_summary(trade: dict) -> dict:
    """Istantanea compatta di un'operazione per il diario."""
    fields = ("mt5_ticket", "direction", "volume", "entry_price", "stop_loss", "take_profit", "closed", "be_active")
    return {
        "ticket_id": trade.get("ticket_id"),
        "trade_msg_id": trade.get("msg_id"),
        "root_msg_id": trade.get("root_msg_id"),
        "status": trade.get("status"),
        "tickets": {key: {f: t.get(f) for f in fields} for key, t in trade.get("tickets", {}).items()},
    }


def record_position_closed(mt5_ticket: int, closed_by: str, trade: dict = None) -> None:
    """Registra come si è chiusa una posizione (motivo, prezzo, profitto netto)."""
    info = mt5_agent.get_close_info(mt5_ticket)
    logger.info(f"🏁 Posizione {mt5_ticket} chiusa ({closed_by}) | motivo: {info.get('close_reason', 'n/d')} "
                f"| prezzo: {info.get('close_price', 'n/d')} | profitto: {info.get('profit', 'n/d')}")
    journal.record("POSITION_CLOSED", mt5_ticket=mt5_ticket, closed_by=closed_by,
                   ticket_id=trade.get("ticket_id") if trade else None, **info)


def broker_position_lookup(mt5_ticket: int):
    """
    Lookup per OrderManager.reconcile_with_broker: oltre a dire se la posizione
    è ancora aperta, registra nel diario come si è chiusa quella che non lo è
    più (TP/SL scattati o chiusura manuale mentre nessuno guardava).
    """
    state = mt5_agent.get_open_position(mt5_ticket)
    if state is None:
        record_position_closed(mt5_ticket, closed_by="RILEVATA_SU_MT5")
    return state


def sync_ticket_with_broker(tp_config: dict, mt5_ticket: int) -> None:
    """
    Riallinea un ticket in memoria allo stato reale su MT5, dopo un'operazione
    rifiutata: se la posizione non esiste più viene segnata chiusa, altrimenti
    SL/TP in memoria tornano ai valori effettivamente attivi sul broker.
    """
    try:
        state = mt5_agent.get_open_position(mt5_ticket)
    except MT5UnavailableError as e:
        # Senza risposta da MT5 non sappiamo se la posizione sia aperta: meglio
        # non toccare nulla, ci penserà il controllo periodico.
        logger.error(f"❌ Impossibile verificare il ticket {mt5_ticket} su MT5 ({e}): memoria lasciata invariata.")
        return

    if state is None:
        tp_config["closed"] = True
        logger.info(f"ℹ️ Ticket MT5 {mt5_ticket} non più aperto (TP/SL già raggiunto): segnato come chiuso.")
        record_position_closed(mt5_ticket, closed_by="RILEVATA_SU_MT5")
        return

    # MT5 usa 0.0 per "nessun livello"
    tp_config["stop_loss"] = state["stop_loss"] or None
    tp_config["take_profit"] = state["take_profit"] or None
    logger.warning(f"🔄 Ticket MT5 {mt5_ticket} ancora aperto: memoria riallineata al broker "
                   f"(SL {tp_config['stop_loss']}, TP {tp_config['take_profit']}).")
    journal.record("MEMORY_RESYNC", mt5_ticket=mt5_ticket,
                   stop_loss=tp_config["stop_loss"], take_profit=tp_config["take_profit"])


async def process_message(event, is_edit: bool):
    # Tutti gli eventi del diario registrati durante l'elaborazione (compresi
    # gli ordini inviati da MT5Executor) portano il msg_id di questo messaggio.
    token = journal.set_current_message(event.id)
    try:
        handle_message(event, is_edit)
    except Exception:
        logger.exception("❌ Errore durante l'elaborazione del messaggio")
        journal.record("ERROR", where="process_message", error=traceback.format_exc())
    finally:
        journal.clear_current_message(token)


def handle_message(event, is_edit: bool):
    sender_id = event.sender_id
    text = event.raw_text

    # Filtro mittenti (nessuna chiamata AI per i messaggi scartati)
    if ALLOWED_SENDER_IDS and sender_id not in ALLOWED_SENDER_IDS:
        logger.info(f"🚫 Messaggio {event.id} scartato: mittente {sender_id} non autorizzato.")
        journal.record("MESSAGE_SKIPPED", reason="mittente non autorizzato", sender_id=sender_id, text=text)
        return

    # Ignoriamo i messaggi vuoti (es. solo foto senza didascalia o sticker)
    if not text or not text.strip():
        journal.record("MESSAGE_SKIPPED", reason="messaggio senza testo", sender_id=sender_id)
        return

    msg_id = event.id
    reply_to = event.reply_to_msg_id if hasattr(event, 'reply_to_msg_id') else None
    has_media = bool(event.media) if hasattr(event, 'media') else False
    is_forwarded = bool(event.forward) if hasattr(event, 'forward') else False
    timestamp = event.date.timestamp() if getattr(event, 'date', None) else None
    # Firma dell'autore, presente solo nei canali con "firma i messaggi" attiva
    post_author = getattr(event, 'post_author', None)

    logger.info(f"{'✏️ EDIT' if is_edit else '🆕 NUOVO'} MESSAGGIO | ID: {msg_id} | Reply-To: {reply_to} "
                f"| Mittente: {sender_id}{f' ({post_author})' if post_author else ''} | Testo: {text!r}")
    journal.record("MESSAGE", is_edit=is_edit, reply_to=reply_to, sender_id=sender_id, post_author=post_author,
                   has_media=has_media, is_forwarded=is_forwarded, text=text)

    if is_edit and _last_text_by_msg.get(msg_id) == text:
        logger.info(f"⏭️ Edit del messaggio {msg_id} scartato: il testo non è cambiato.")
        journal.record("MESSAGE_SKIPPED", reason="edit senza modifiche al testo")
        return

    if is_forwarded and IGNORE_FORWARDED_MESSAGES:
        logger.info(f"⏭️ Messaggio {msg_id} scartato: inoltrato (testimonianza, non un segnale del trader).")
        journal.record("MESSAGE_SKIPPED", reason="messaggio inoltrato")
        return

    if not OPERATIVE_KEYWORDS.search(text):
        logger.info(f"⏭️ Messaggio {msg_id} scartato: nessuna parola operativa (emoji, celebrazione, avviso).")
        journal.record("MESSAGE_SKIPPED", reason="nessuna parola operativa")
        return

    # A mercato chiuso nessuna azione sarebbe eseguibile su MT5: scartiamo il
    # messaggio PRIMA di chiamare l'agente, così non consumiamo token inutilmente.
    # Prima il calendario statico (non richiede MT5), poi lo stato reale del simbolo.
    market_open, market_reason = is_market_time_open()
    if market_open:
        market_open, market_reason = mt5_agent.is_symbol_tradable(TRADED_SYMBOL)

    if not market_open:
        logger.info(f"⏸️ Messaggio scartato senza classificarlo: {market_reason}")
        journal.record("MESSAGE_SKIPPED", reason=market_reason)
        return

    # Chiamata al tuo Agente 1 passando il testo e lo stato di modifica
    ai_output = agent_classify_telegram_message(text, is_edit=is_edit, reply_to=reply_to, has_media=has_media, is_forwarded=is_forwarded, timestamp=timestamp)

    logger.info(f"🧠 OUTPUT AGENTE 1: {json.dumps(ai_output, ensure_ascii=False)}")

    # Memorizzato solo dopo una classificazione riuscita: se l'agente fallisce,
    # un edit successivo con lo stesso testo avrà un'altra possibilità.
    _last_text_by_msg[msg_id] = text
    if len(_last_text_by_msg) > _LAST_TEXT_MAX_ENTRIES:
        _last_text_by_msg.pop(next(iter(_last_text_by_msg)))
    journal.record("CLASSIFIED", intent=ai_output.get("intent"), is_actionable=ai_output.get("is_actionable"),
                   data=ai_output.get("data"), reasoning=ai_output.get("raw_reasoning"))

    # 2. Passaggio all'Order Manager (il tuo file separato)
    manager_result = manager.handle_agent_output(msg_id, reply_to, ai_output)
    if manager_result:
        journal.record("DECISION", action=manager_result.get("action"), reason=manager_result.get("reason"))
    else:
        journal.record("DECISION", action="NESSUNA", reason=f"intent {ai_output.get('intent')}: nessuna azione prevista")

    if manager_result:
        action = manager_result.get("action")
        trade_data = manager_result.get("trade", {})

        # CASO 1: Apertura nuovo ordine (Fase Rapida)
        if action == "OPEN":
            validated_orders = risk_agent.validate_and_build_order(trade_data)
            if validated_orders.get("approved"):
                orders_dict = validated_orders.get("orders", {})

                opened_count = 0
                for target_key, order_config in orders_dict.items():
                    # Eseguiamo l'apertura su MT5 con il volume calcolato dal Risk Manager
                    exec_result = mt5_agent.execute_open(order_config)

                    # Scriviamo in memoria i valori REALI restituiti dal broker:
                    # prezzo di riempimento e volume effettivo possono differire da
                    # quelli pianificati (slippage, riempimento parziale), e sono loro
                    # a dover guidare il Breakeven e i controlli successivi.
                    if target_key in trade_data["tickets"]:
                        ticket_data = trade_data["tickets"][target_key]
                        ticket_data["mt5_ticket"] = exec_result.get("mt5_ticket")
                        ticket_data["success"] = exec_result.get("success", False)
                        ticket_data["volume"] = exec_result.get("volume") or order_config.get("volume")

                        fill_price = exec_result.get("fill_price")
                        if fill_price:
                            ticket_data["entry_price"] = fill_price

                        # Lo SL inviato a MT5 (anche quello temporaneo della fase
                        # rapida) va in memoria: altrimenti resta None e un update
                        # successivo con soli TP invierebbe sl=0, togliendolo.
                        if exec_result.get("success"):
                            ticket_data["stop_loss"] = order_config.get("stop_loss")
                            trade_data["stop_loss"] = order_config.get("stop_loss")

                    if exec_result.get("success"):
                        opened_count += 1
                    else:
                        logger.warning(f"⚠️ Apertura {target_key} non riuscita: {exec_result.get('error')}")

                # Se anche un solo ticket è a mercato l'operazione resta ACTIVE,
                # così update e chiusure continuano a gestirlo (i ticket non
                # aperti hanno mt5_ticket a None e vengono saltati). Con uno
                # stato diverso il CLOSE la ignorerebbe, lasciando la posizione
                # aperta su MT5 senza più controllo.
                trade_data["status"] = "ACTIVE" if opened_count > 0 else "OPEN_FAILED"
                if 0 < opened_count < len(orders_dict):
                    logger.warning(f"⚠️ Apertura PARZIALE: {opened_count}/{len(orders_dict)} ticket a mercato, l'operazione resta gestita.")
                journal.record("TRADE_STATUS", **trade_summary(trade_data))

                # Persistiamo SUBITO: fino a questo punto il file su disco
                # contiene ancora mt5_ticket a null. Se il bot si riavviasse
                # ora, perderebbe il riferimento a posizioni già a mercato.
                manager.save_state_to_file()
                logger.info(f"✅ Memoria aggiornata con successo. Status trade: {trade_data['status']}")

            else:
                logger.warning(f"⚠️ Risk Manager: {validated_orders.get('reason')}")
                journal.record("RISK_REJECTED", ticket_id=trade_data.get("ticket_id"), reason=validated_orders.get("reason"))
                trade_data["status"] = "REJECTED"
                manager.save_state_to_file()

        # CASO 2: Aggiornamento ordine esistente (SL/TP reali e/o BE+)
        elif action == "UPDATE":
            trades = manager_result.get("trades", [])
            be_candidates = manager_result.get("be_candidates", [])
            sl_tp_changed = manager_result.get("sl_tp_changed", False)

            # 2.0 Chiusura parziale ("close half"), PRIMA del BE: il BE va poi
            # applicato solo a ciò che resta aperto.
            if manager_result.get("close_percentage"):
                execute_partial_close(trades, manager_result["close_percentage"])

            # 2a. Breakeven, ticket per ticket. MT5 stesso verifica se la posizione
            # esiste ancora: se il TP è già scattato, il broker l'ha già chiusa e
            # set_sl_to_be non applica nulla (ritorna False).
            for candidate in be_candidates:
                parent_trade = candidate["trade"]
                tp_config = candidate["ticket"]
                if tp_config.get("closed"):
                    continue
                real_mt5_ticket = tp_config.get("mt5_ticket")
                entry_price = breakeven_price(tp_config)

                be_applied = mt5_agent.set_sl_to_be(ticket=real_mt5_ticket, entry_price=entry_price)
                if be_applied:
                    tp_config["be_active"] = True
                    tp_config["stop_loss"] = entry_price
                    # Risincronizziamo anche lo stop_loss a livello radice del trade,
                    # altrimenti resta al valore pre-BE (usato per ereditarietà re-entry).
                    parent_trade["stop_loss"] = entry_price
                    logger.info(f"🎯 BE applicato al ticket MT5 {real_mt5_ticket}")
                else:
                    # BE non applicato: o la posizione non esiste più (TP già
                    # scattato) o il broker ha rifiutato il nuovo SL (es. prezzo
                    # non ancora in profitto). Solo MT5 sa quale dei due: segnare
                    # 'closed' a priori renderebbe una posizione aperta invisibile
                    # alla chiusura successiva.
                    sync_ticket_with_broker(tp_config, real_mt5_ticket)
                    if not tp_config.get("closed"):
                        # Il trader ha chiesto il BE ma il prezzo è ancora troppo
                        # vicino all'ingresso (il 23/09: "running 65 pips" contati
                        # dal fondo della zona). Resta in attesa: il controllo
                        # periodico lo applica appena MT5 lo accetta.
                        tp_config["be_pending"] = True
                        logger.info(f"⏳ BE del ticket {real_mt5_ticket} in attesa: verrà applicato appena il prezzo lo consente.")
                        journal.record("BE_PENDING", mt5_ticket=real_mt5_ticket, entry_price=entry_price)

            # 2b. Aggiornamento SL/TP "standard" (fase COMPLETA, invalidation, ecc.),
            # solo se il messaggio conteneva davvero nuovi valori.
            if sl_tp_changed:
                for trade in trades:
                    # Letto una volta sola: in caso di rifiuto lo SL radice viene
                    # riallineato al broker e non deve contaminare il ticket successivo.
                    new_sl = trade.get("stop_loss")
                    for tp_key, tp_config in trade.get("tickets", {}).items():
                        real_mt5_ticket = tp_config.get("mt5_ticket")
                        if not real_mt5_ticket or tp_config.get("closed"):
                            continue

                        new_tp = tp_config.get("take_profit")

                        if not levels_match_direction(tp_config.get("direction"), new_sl, new_tp):
                            logger.warning(f"⚠️ Livelli incoerenti con la posizione {tp_config.get('direction')} #{real_mt5_ticket} "
                                           f"(SL {new_sl}, TP {new_tp}): modifica NON inviata a MT5.")
                            journal.record("LEVELS_INCOHERENT", mt5_ticket=real_mt5_ticket,
                                           direction=tp_config.get("direction"), stop_loss=new_sl, take_profit=new_tp)
                            sync_ticket_with_broker(tp_config, real_mt5_ticket)
                            trade["stop_loss"] = tp_config.get("stop_loss")
                            continue

                        modified = mt5_agent.modify_order_levels(
                            ticket=real_mt5_ticket,
                            stop_loss=new_sl,
                            take_profit=[new_tp] if new_tp is not None else []
                        )
                        if not modified:
                            # MT5 ha rifiutato: la memoria deve tornare ai valori
                            # realmente attivi sul broker, non a quelli del messaggio.
                            sync_ticket_with_broker(tp_config, real_mt5_ticket)
                            trade["stop_loss"] = tp_config.get("stop_loss")
                        else:
                            # Un nuovo SL esplicito del trader sostituisce un BE
                            # ancora in attesa: non va sovrascritto più tardi.
                            tp_config["be_pending"] = False

            for trade in trades:
                journal.record("TRADE_STATUS", **trade_summary(trade))
            manager.save_state_to_file()

        # CASO 3: Chiusura posizione (totale su tutta la catena)
        elif action == "CLOSE":
            closing_chain = manager_result.get("closed_chain", [])

            for trade in closing_chain:
                all_closed = True

                for tp_key, tp_config in trade.get("tickets", {}).items():
                    real_mt5_ticket = tp_config.get("mt5_ticket")

                    # Niente ticket (mai aperto) o già chiuso: nulla da fare
                    if not real_mt5_ticket or tp_config.get("closed"):
                        continue

                    if mt5_agent.close_position(ticket=real_mt5_ticket, symbol=tp_config.get("symbol")):
                        tp_config["closed"] = True
                        record_position_closed(real_mt5_ticket, closed_by="SEGNALE_CHIUSURA", trade=trade)
                    else:
                        all_closed = False

                # Lo stato riflette l'esito REALE: se anche un solo ticket non
                # è stato chiuso, l'operazione resta segnalata come problematica
                # invece di risultare chiusa mentre è ancora a mercato.
                trade["status"] = "CLOSED" if all_closed else "CLOSE_FAILED"
                if not all_closed:
                    logger.warning(f"⚠️ Chiusura INCOMPLETA per il trade {trade.get('ticket_id')}: verificare manualmente su MT5.")
                journal.record("TRADE_STATUS", **trade_summary(trade))

            manager.save_state_to_file()

    logger.info(f"📦 ESITO ORDER MANAGER: {json.dumps(manager_result, default=str, ensure_ascii=False)}")
    logger.info(f"📋 Operazioni attive in memoria: {list(manager.active_trades.keys())}")


def retry_pending_breakeven() -> None:
    """
    Applica i BE chiesti dal trader ma rifiutati da MT5 perché il prezzo era
    ancora troppo vicino all'ingresso. Invia la modifica solo quando MT5 la
    accetterebbe, così non riempie il log di rifiuti a ogni giro.
    """
    changed = False
    for trade in manager.active_trades.values():
        if trade.get("status") != "ACTIVE":
            continue
        for tp_config in trade.get("tickets", {}).values():
            if not tp_config.get("be_pending") or tp_config.get("closed") or tp_config.get("be_active"):
                continue
            mt5_ticket = tp_config.get("mt5_ticket")
            entry_price = breakeven_price(tp_config)
            if not mt5_agent.can_move_sl(mt5_ticket, entry_price):
                continue

            # Nel diario l'evento resta legato al messaggio dell'operazione
            token = journal.set_current_message(trade.get("msg_id"))
            try:
                if mt5_agent.set_sl_to_be(ticket=mt5_ticket, entry_price=entry_price):
                    tp_config.update(be_active=True, be_pending=False, stop_loss=entry_price)
                    trade["stop_loss"] = entry_price
                    logger.info(f"🎯 BE in attesa applicato al ticket MT5 {mt5_ticket}")
                    journal.record("BE_PENDING_APPLIED", mt5_ticket=mt5_ticket, entry_price=entry_price)
                    changed = True
            finally:
                journal.clear_current_message(token)

    if changed:
        manager.save_state_to_file()


def write_heartbeat(mt5_ok: bool, mt5_reason: str) -> None:
    """Battito periodico: conferma nel log che il bot è vivo e fotografa il conto."""
    account = mt5_agent.account_snapshot()
    live_trades = [t for t in manager.active_trades.values() if t.get("status") in CLOSABLE_STATUSES]
    logger.info(f"💓 Bot attivo | MT5: {mt5_reason} | operazioni vive: {len(live_trades)} "
                f"| saldo: {account.get('balance')} | equity: {account.get('equity')} "
                f"| posizioni aperte sul conto: {account.get('open_positions')}")
    journal.record("HEARTBEAT", mt5_ok=mt5_ok, mt5_reason=mt5_reason, live_trades=len(live_trades), **account)


async def monitor_loop():
    """
    Controllo periodico mentre il bot aspetta messaggi:
      - stato di MT5 (terminale raggiungibile, collegato, Algo Trading attivo),
        segnalato nel log solo quando cambia;
      - riconciliazione delle operazioni vive: registra le posizioni chiuse dal
        broker (TP/SL) o a mano, con motivo e profitto, e allinea SL/TP;
      - battito orario nel log.
    Gira nello stesso event loop di Telethon, tra un messaggio e l'altro: non
    si sovrappone mai all'elaborazione di un messaggio.
    """
    mt5_ok = True
    last_heartbeat = None

    while True:
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
        try:
            ok, reason = mt5_agent.check_health()
            if ok != mt5_ok:
                mt5_ok = ok
                if ok:
                    logger.info(f"✅ {reason}: situazione tornata normale.")
                else:
                    logger.error(f"❌ Problema MT5: {reason}")
                journal.record("MT5_CONNECTION", ok=ok, reason=reason)

            if ok and not mt5_agent.test_mode:
                manager.reconcile_with_broker(broker_position_lookup)
                retry_pending_breakeven()

            if last_heartbeat is None or time.monotonic() - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                last_heartbeat = time.monotonic()
                write_heartbeat(ok, reason)

        except MT5UnavailableError as e:
            logger.warning(f"⚠️ Controllo periodico: MT5 non ha risposto ({e}), riprovo al prossimo giro.")
        except Exception:
            logger.exception("❌ Errore nel controllo periodico")
            journal.record("ERROR", where="monitor_loop", error=traceback.format_exc())


# Listener per i NUOVI messaggi nel canale
@tg_client.on(events.NewMessage(chats=TARGET_CHANNEL))
async def handle_new_message(event):
    await process_message(event, is_edit=False)

# Listener per i MESSAGGI MODIFICATI (gli Edit) nel canale
@tg_client.on(events.MessageEdited(chats=TARGET_CHANNEL))
async def handle_edited_message(event):
    await process_message(event, is_edit=True)


async def main():
    await tg_client.start()
    logger.info(f"🤖 Ascolto attivo sul canale: {TARGET_CHANNEL}")
    logger.info("In attesa di messaggi... (Premi Ctrl+C per fermare)")
    # Il riferimento al task va tenuto: asyncio conserva solo riferimenti deboli
    monitor_task = asyncio.create_task(monitor_loop())
    try:
        await tg_client.run_until_disconnected()
    finally:
        monitor_task.cancel()


# Avvio del client
if __name__ == "__main__":
    account = mt5_agent.account_snapshot()
    journal.record("BOT_START", test_mode=mt5_agent.test_mode, channel=TARGET_CHANNEL,
                   allowed_senders=sorted(ALLOWED_SENDER_IDS), **account)
    if account and not account.get("demo"):
        logger.warning("⚠️ ATTENZIONE: il terminale MT5 è collegato a un conto REALE.")
    if ALLOWED_SENDER_IDS:
        logger.info(f"👤 Accetto solo i messaggi dei mittenti: {sorted(ALLOWED_SENDER_IDS)}")
    else:
        logger.info("👥 Filtro mittenti disattivato: accetto tutti i messaggi del canale.")

    # Riconciliazione con MT5: mentre il bot era spento un TP/SL può essere
    # scattato, o un'operazione può essere stata chiusa a mano dal terminale.
    # In TEST MODE non esistono posizioni reali, quindi si salta.
    if not mt5_agent.test_mode:
        try:
            corrette = manager.reconcile_with_broker(broker_position_lookup)
            logger.info(f"🔄 Riconciliazione con MT5 completata ({corrette} valori allineati).")
        except MT5UnavailableError as e:
            logger.error(f"❌ Riconciliazione iniziale non eseguita, MT5 non risponde ({e}): riproverà il controllo periodico.")
    else:
        logger.info("🧪 TEST MODE: riconciliazione con MT5 saltata.")

    try:
        tg_client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot fermato manualmente.")
    except Exception:
        logger.exception("❌ Il bot si è fermato per un errore imprevisto")
        journal.record("ERROR", where="main", error=traceback.format_exc())
    finally:
        journal.record("BOT_STOP")
