import json
import os
from telethon import TelegramClient, events

from dotenv import load_dotenv
load_dotenv()

from agent_classifier import agent_classify_telegram_message
from order_manager import OrderManager
from risk_manager import RiskManager
from mt5_executor import MT5Executor
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

OWNER_ID = 462122085

# Simbolo su cui verifichiamo l'apertura del mercato prima di classificare
TRADED_SYMBOL = "XAUUSD"

async def process_message(event, is_edit: bool):

    sender_id = event.sender_id
    # Se il messaggio non arriva dall'owner, scartalo subito (nessuna chiamata AI)
    if sender_id != OWNER_ID:
        return

    text = event.raw_text
    if not text or not text.strip():
        return
    
    msg_id = event.id
    reply_to = event.reply_to_msg_id if hasattr(event, 'reply_to_msg_id') else None
    has_media = bool(event.media) if hasattr(event, 'media') else False
    is_forwarded = bool(event.forward) if hasattr(event, 'forward') else False
    timestamp = event.date.timestamp() if getattr(event, 'date', None) else None

    # Ignoriamo i messaggi vuoti (es. solo foto senza didascalia o sticker)
    if not text.strip():
        return

    logger.info(f"{'✏️ EDIT' if is_edit else '🆕 NUOVO'} MESSAGGIO | ID: {msg_id} | Reply-To: {reply_to} | Testo: {text!r}")

    # A mercato chiuso nessuna azione sarebbe eseguibile su MT5: scartiamo il
    # messaggio PRIMA di chiamare l'agente, così non consumiamo token inutilmente.
    # Prima il calendario statico (non richiede MT5), poi lo stato reale del simbolo.
    market_open, market_reason = is_market_time_open()
    if market_open:
        market_open, market_reason = mt5_agent.is_symbol_tradable(TRADED_SYMBOL)

    if not market_open:
        logger.info(f"⏸️ Messaggio scartato senza classificarlo: {market_reason}")
        return

    try:
        # Chiamata al tuo Agente 1 passando il testo e lo stato di modifica
        ai_output = agent_classify_telegram_message(text, is_edit=is_edit, reply_to=reply_to, has_media=has_media, is_forwarded=is_forwarded, timestamp=timestamp)
        
        logger.info(f"🧠 OUTPUT AGENTE 1: {json.dumps(ai_output, ensure_ascii=False)}")

        # 2. Passaggio all'Order Manager (il tuo file separato)
        manager_result = manager.handle_agent_output(msg_id, reply_to, ai_output)

        if manager_result:
            action = manager_result.get("action")
            trade_data = manager_result.get("trade", {})

            # CASO 1: Apertura nuovo ordine (Fase Rapida)
            if action == "OPEN":
                validated_orders = risk_agent.validate_and_build_order(trade_data)
                if validated_orders.get("approved"):
                    orders_dict = validated_orders.get("orders", {})
        
                    all_success = True
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

                        if not exec_result.get("success"):
                            all_success = False
                            logger.warning(f"⚠️ Apertura {target_key} non riuscita: {exec_result.get('error')}")

                    # Aggiorniamo lo status in base all'esito complessivo
                    trade_data["status"] = "ACTIVE" if all_success else "PENDING_FAILED"

                    # Persistiamo SUBITO: fino a questo punto il file su disco
                    # contiene ancora mt5_ticket a null. Se il bot si riavviasse
                    # ora, perderebbe il riferimento a posizioni già a mercato.
                    manager.save_state_to_file()
                    logger.info(f"✅ Memoria aggiornata con successo. Status trade: {trade_data['status']}")

                else:
                    logger.warning(f"⚠️ Risk Manager: {validated_orders.get('reason')}")
                    trade_data["status"] = "REJECTED"
                    manager.save_state_to_file()

            # CASO 2: Aggiornamento ordine esistente (SL/TP reali e/o BE+)
            elif action == "UPDATE":
                trades = manager_result.get("trades", [])
                be_candidates = manager_result.get("be_candidates", [])
                sl_tp_changed = manager_result.get("sl_tp_changed", False)

                # 2a. Breakeven, ticket per ticket. MT5 stesso verifica se la posizione
                # esiste ancora: se il TP è già scattato, il broker l'ha già chiusa e
                # set_sl_to_be non applica nulla (ritorna False).
                for candidate in be_candidates:
                    parent_trade = candidate["trade"]
                    tp_config = candidate["ticket"]
                    real_mt5_ticket = tp_config.get("mt5_ticket")
                    entry_price = tp_config.get("entry_price")

                    be_applied = mt5_agent.set_sl_to_be(ticket=real_mt5_ticket, entry_price=entry_price)
                    if be_applied:
                        tp_config["be_active"] = True
                        tp_config["stop_loss"] = entry_price
                        # Risincronizziamo anche lo stop_loss a livello radice del trade,
                        # altrimenti resta al valore pre-BE (usato per ereditarietà re-entry).
                        parent_trade["stop_loss"] = entry_price
                        logger.info(f"🎯 BE applicato al ticket MT5 {real_mt5_ticket}")
                    else:
                        # Non più aperto su MT5: il TP era già scattato prima del BE.
                        tp_config["closed"] = True
                        logger.info(f"ℹ️ Ticket MT5 {real_mt5_ticket} non più aperto (TP già raggiunto), BE non applicabile.")

                # 2b. Aggiornamento SL/TP "standard" (fase COMPLETA, invalidation, ecc.),
                # solo se il messaggio conteneva davvero nuovi valori.
                if sl_tp_changed:
                    for trade in trades:
                        for tp_key, tp_config in trade.get("tickets", {}).items():
                            real_mt5_ticket = tp_config.get("mt5_ticket")
                            if not real_mt5_ticket or tp_config.get("closed"):
                                continue

                            new_sl = trade.get("stop_loss")
                            new_tp = tp_config.get("take_profit")

                            mt5_agent.modify_order_levels(
                                ticket=real_mt5_ticket,
                                stop_loss=new_sl,
                                take_profit=[new_tp] if new_tp is not None else []
                            )

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
                        else:
                            all_closed = False

                    # Lo stato riflette l'esito REALE: se anche un solo ticket non
                    # è stato chiuso, l'operazione resta segnalata come problematica
                    # invece di risultare chiusa mentre è ancora a mercato.
                    trade["status"] = "CLOSED" if all_closed else "CLOSE_FAILED"
                    if not all_closed:
                        logger.warning(f"⚠️ Chiusura INCOMPLETA per il trade {trade.get('ticket_id')}: verificare manualmente su MT5.")

                manager.save_state_to_file()
        
        logger.info(f"📦 ESITO ORDER MANAGER: {json.dumps(manager_result, default=str, ensure_ascii=False)}")
        logger.info(f"📋 Operazioni attive in memoria: {list(manager.active_trades.keys())}")
        
    except Exception as e:
        logger.exception("❌ Errore durante l'elaborazione del messaggio")

# Listener per i NUOVI messaggi nel canale
@tg_client.on(events.NewMessage(chats=TARGET_CHANNEL))
async def handle_new_message(event):
    await process_message(event, is_edit=False)

# Listener per i MESSAGGI MODIFICATI (gli Edit) nel canale
@tg_client.on(events.MessageEdited(chats=TARGET_CHANNEL))
async def handle_edited_message(event):
    await process_message(event, is_edit=True)


# Avvio del client
if __name__ == "__main__":
    # Riconciliazione con MT5: mentre il bot era spento un TP/SL può essere
    # scattato, o un'operazione può essere stata chiusa a mano dal terminale.
    # In TEST MODE non esistono posizioni reali, quindi si salta.
    if not mt5_agent.test_mode:
        corrette = manager.reconcile_with_broker(mt5_agent.get_open_position)
        logger.info(f"🔄 Riconciliazione con MT5 completata ({corrette} valori allineati).")
    else:
        logger.info("🧪 TEST MODE: riconciliazione con MT5 saltata.")

    logger.info(f"🤖 Ascolto attivo sul canale: {TARGET_CHANNEL}")
    logger.info("In attesa di messaggi... (Premi Ctrl+C per fermare)")
    tg_client.start()
    tg_client.run_until_disconnected()