import time
from typing import Optional

import MetaTrader5 as mt5

from logger_config import setup_logger

logger = setup_logger(__name__)

# ⚠️ INTERRUTTORE UNICO TEST / REALE
# True  = nessun ordine viene realmente inviato a MT5, tutto viene solo simulato.
# False = il bot opera davvero sul conto collegato al terminale MT5.
# Prima era gestito con un 'return' anticipato dentro ogni metodo: bastava
# dimenticarne uno per ritrovarsi a metà tra simulazione e operatività reale.
TEST_MODE = True


class MT5Executor:
    """
    AGENTE 3: Connettore ed esecutore materiale su MetaTrader 5.
    Gestisce invio ordini a mercato, modifica SL/TP (BE+) e chiusura posizioni.

    Tutti i metodi che agiscono sul broker restituiscono un esito esplicito, con
    la STESSA forma sia in TEST MODE che in reale: l'Order Manager deve poter
    allineare la memoria a ciò che è realmente successo su MT5.
    """

    def __init__(self):
        self.test_mode = TEST_MODE

        if not mt5.initialize():
            logger.error(f"❌ Impossibile connettersi a MT5: {mt5.last_error()}")
        else:
            logger.info("🚀 Connessione a MetaTrader 5 riuscita!")

        if self.test_mode:
            logger.info("🧪 TEST MODE attivo: nessun ordine verrà realmente inviato a MT5.")

        # Solo per TEST MODE: contatore per generare ticket fittizi distinti
        self._test_ticket_counter = 90000000

    def execute_open(self, order_plan: dict) -> dict:
        """
        Apre una posizione a mercato.

        Ritorna SEMPRE un dizionario con la stessa struttura:
          {"success", "mt5_ticket", "fill_price", "volume", "error"}
        'fill_price' e 'volume' sono i valori REALI di esecuzione restituiti dal
        broker, che possono differire da quelli pianificati (slippage, riempimento
        parziale): vanno scritti in memoria al posto dei valori teorici.
        """
        symbol = order_plan["symbol"]
        direction = order_plan["direction"]
        volume = order_plan["volume"]
        sl = order_plan["stop_loss"]
        tp_value = order_plan["take_profit"]

        if self.test_mode:
            self._test_ticket_counter += 1
            fake_ticket = self._test_ticket_counter
            logger.info(f"🛠️ [TEST MODE] Simulazione apertura {symbol} {direction} | Volume: {volume} | Ticket fittizio: {fake_ticket}")
            return {
                "success": True,
                "mt5_ticket": fake_ticket,
                "fill_price": order_plan.get("entry_price"),
                "volume": volume,
                "error": None,
            }

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"❌ Nessun prezzo disponibile per {symbol}: ordine non inviato.")
            return {"success": False, "mt5_ticket": None, "fill_price": None, "volume": None, "error": "nessun prezzo disponibile"}

        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if direction == "BUY" else tick.bid

        # Gestione sicura del TP se è None (Fase Rapida)
        tp_primary = float(tp_value) if tp_value is not None else 0.0

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": float(sl) if sl is not None else 0.0,
            "tp": float(tp_primary),
            "deviation": 20,
            "magic": 990011,
            "comment": f"TB_{order_plan.get('ticket_id')}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"❌ Errore apertura ordine MT5: {result.comment}")
            return {"success": False, "mt5_ticket": None, "fill_price": None, "volume": None, "error": result.comment}

        logger.info(f"✅ Ordine eseguito su MT5 | Ticket: {result.order} | Volume: {result.volume} | Prezzo: {result.price}")
        return {
            "success": True,
            "mt5_ticket": result.order,
            "fill_price": result.price,
            "volume": result.volume,
            "error": None,
        }

    def set_sl_to_be(self, ticket: int, entry_price: float) -> bool:
        """
        Sposta lo Stop Loss al prezzo di ingresso (Breakeven) per UN SINGOLO ticket.
        Se la posizione non esiste più su MT5 (es. il broker l'ha già chiusa perché
        ha colpito il proprio Take Profit), non fa nulla e ritorna False: MT5 è la
        fonte di verità su quali ticket sono ancora aperti, non il testo del messaggio.
        """
        if entry_price is None:
            logger.warning(f"⚠️ BE non applicabile al ticket {ticket}: prezzo di ingresso sconosciuto.")
            return False

        if self.test_mode:
            logger.info(f"🛠️ [TEST MODE] Simulazione BE per ticket {ticket} | nuovo SL: {entry_price}")
            return True

        position = mt5.positions_get(ticket=ticket)
        if not position:
            logger.info(f"ℹ️ Ticket {ticket} non più aperto su MT5 (probabilmente TP già raggiunto).")
            return False

        pos = position[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "sl": float(entry_price),
            "tp": pos.tp
        }
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"🎯 SL spostato a BE per la posizione #{pos.ticket}")
            return True

        logger.error(f"❌ Errore spostamento BE posizione #{pos.ticket}: {result.comment}")
        return False

    def close_position(self, ticket: int, symbol: str = None) -> bool:
        """
        Chiude a mercato UNA posizione specifica, identificata dal suo ticket.
        Ritorna True solo se la posizione non è più a mercato al termine
        (chiusura confermata dal broker, o posizione già chiusa in precedenza):
        l'Order Manager deve poter distinguere una chiusura riuscita da una
        fallita, per non segnare come chiusa una posizione ancora aperta.
        """
        if self.test_mode:
            logger.info(f"🛠️ [TEST MODE] Simulazione chiusura posizione {ticket} ({symbol})")
            return True

        position = mt5.positions_get(ticket=ticket)
        if not position:
            logger.info(f"ℹ️ Ticket {ticket} non presente su MT5: nessuna chiusura necessaria.")
            return True

        pos = position[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            logger.error(f"❌ Nessun prezzo disponibile per {pos.symbol}: chiusura di {pos.ticket} non inviata.")
            return False

        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "price": price,
            "deviation": 20,
            "magic": 990011,
            "comment": "Close by Bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"❌ Errore chiusura posizione {pos.ticket}: {result.comment}")
            return False

        logger.info(f"🔒 Posizione {pos.ticket} chiusa correttamente su MT5.")
        return True

    def modify_order_levels(self, ticket: int, stop_loss: float, take_profit: list) -> bool:
        """
        Invia una richiesta TRADE_ACTION_SLTP a MT5 per aggiornare Stop Loss e Take Profit.
        """
        if self.test_mode:
            logger.info(f"🛠️ [TEST MODE] Simulazione modifica ticket {ticket} | stop_loss: {stop_loss}, take_profit: {take_profit}")
            return True

        # Se la lista TP contiene valori, prendiamo il primo (TP1)
        tp_price = take_profit[0] if take_profit else 0.0

        request = {
            "action": mt5.TRADE_ACTION_SLTP,  # Dice a MT5 che vogliamo solo modificare SL/TP
            "position": ticket,                # Il ticket dell'ordine aperto
            "sl": float(stop_loss) if stop_loss else 0.0,
            "tp": float(tp_price)
        }

        result = mt5.order_send(request)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"❌ Errore modifica ordine {ticket}: {result.comment}")
            return False

        logger.info(f"✅ Ordine {ticket} aggiornato con successo | SL: {stop_loss} | TP: {tp_price}")
        return True

    def get_open_position(self, ticket: int) -> Optional[dict]:
        """
        Stato reale di una posizione su MT5, o None se non è più aperta.
        Serve alla riconciliazione: è l'unico modo per sapere con certezza cosa
        è successo mentre il bot era spento (TP/SL scattati, chiusure manuali).

        In TEST MODE non esistono posizioni reali, quindi la riconciliazione va
        saltata dal chiamante (vedi telegram_listener.py): qui ritorniamo None.
        """
        if self.test_mode:
            return None

        position = mt5.positions_get(ticket=ticket)
        if not position:
            return None

        pos = position[0]
        return {
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "price_open": pos.price_open,
            "stop_loss": pos.sl,
            "take_profit": pos.tp,
        }

    def is_symbol_tradable(self, symbol: str, max_tick_age_seconds: int = 180) -> tuple[bool, str]:
        """
        Verifica su MT5 se il simbolo è realmente negoziabile in questo momento.
        Intercetta festività e pause specifiche del broker che il calendario
        statico di market_hours.py non può conoscere.

        In caso di incertezza (MT5 non raggiungibile, dati non disponibili)
        ritorna True: meglio una classificazione in più che perdere un segnale.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            return True, "stato del simbolo non disponibile (assumo aperto)"

        if info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
            return False, f"trading disabilitato su {symbol}"

        tick = mt5.symbol_info_tick(symbol)
        if tick is None or not tick.time:
            return True, "nessun tick disponibile (assumo aperto)"

        tick_age = time.time() - tick.time
        if tick_age > max_tick_age_seconds:
            return False, f"nessun tick da {int(tick_age)}s su {symbol} (mercato chiuso o illiquido)"

        return True, f"{symbol} negoziabile"
