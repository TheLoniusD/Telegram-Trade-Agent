import time

import MetaTrader5 as mt5

class MT5Executor:
    """
    AGENTE 3: Connettore ed esecutore materiale su MetaTrader 5.
    Gestisce invio ordini a mercato, modifica SL/TP (BE+) e chiusura posizioni.
    """
    def __init__(self):
        if not mt5.initialize():
            print(f"❌ Impossibile connettersi a MT5: {mt5.last_error()}")
        else:
            print("🚀 Connessione a MetaTrader 5 riuscita!")

        # Solo per TEST MODE: contatore per generare ticket fittizi distinti
        # (in produzione ogni execute_open ritorna il vero ticket assegnato dal broker).
        self._test_ticket_counter = 90000000

    def execute_open(self, order_plan: dict) -> dict:

        self._test_ticket_counter += 1
        fake_ticket = self._test_ticket_counter
        print(f"🛠️ [TEST MODE] Simulazione apertura per {order_plan['symbol']} | Volume: {order_plan['volume']} | Ticket fittizio: {fake_ticket}")
        return fake_ticket

        symbol = order_plan["symbol"]
        direction = order_plan["direction"]
        volume = order_plan["volume"]
        sl = order_plan["stop_loss"]
        tp_value = order_plan["take_profit"]

        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        price = mt5.symbol_info_tick(symbol).ask if direction == "BUY" else mt5.symbol_info_tick(symbol).bid

        # Gestione sicura del TP se è None (Fase Rapida)
        tp_primary = float(tp_value) if tp_value is not None else 0.0

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": float(sl),
            "tp": float(tp_primary),
            "deviation": 20,
            "magic": 990011,
            "comment": f"TB_{order_plan.get('ticket_id')}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"❌ Errore apertura ordine MT5: {result.comment}")
            return {"success": False, "error": result.comment}

        print(f"✅ Ordine Eseguito su MT5 | Ticket: {result.order} | Volume: {volume}")
        return {"success": True, "mt5_ticket": result.order}

    def set_sl_to_be(self, ticket: int, entry_price: float) -> bool:
        """
        Sposta lo Stop Loss al prezzo di ingresso (Breakeven) per UN SINGOLO ticket.
        Se la posizione non esiste più su MT5 (es. il broker l'ha già chiusa perché
        ha colpito il proprio Take Profit), non fa nulla e ritorna False: MT5 è la
        fonte di verità su quali ticket sono ancora aperti, non il testo del messaggio.
        """
        print(f"🛠️ [TEST MODE] Simulazione BE per ticket {ticket} | nuovo SL: {entry_price}")
        return True

        position = mt5.positions_get(ticket=ticket)
        if not position:
            print(f"ℹ️ Ticket {ticket} non più aperto su MT5 (probabilmente TP già raggiunto).")
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
            print(f"🎯 SL spostato a BE per la posizione #{pos.ticket}")
            return True

        print(f"❌ Errore spostamento BE posizione #{pos.ticket}: {result.comment}")
        return False

    def close_position(self, ticket: int, symbol: str = None) -> bool:
        """
        Chiude a mercato UNA posizione specifica, identificata dal suo ticket.
        Ritorna True solo se la posizione non è più a mercato al termine
        (chiusura confermata dal broker, o posizione già chiusa in precedenza):
        l'Order Manager deve poter distinguere una chiusura riuscita da una
        fallita, per non segnare come chiusa una posizione ancora aperta.
        """
        print(f"🛠️ [TEST MODE] Simulazione chiusura posizione {ticket} ({symbol})")
        return True

        position = mt5.positions_get(ticket=ticket)
        if not position:
            print(f"ℹ️ Ticket {ticket} non presente su MT5: nessuna chiusura necessaria.")
            return True

        pos = position[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            print(f"❌ Nessun prezzo disponibile per {pos.symbol}: chiusura di {pos.ticket} non inviata.")
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
            print(f"❌ Errore chiusura posizione {pos.ticket}: {result.comment}")
            return False

        print(f"🔒 Posizione {pos.ticket} chiusa correttamente su MT5.")
        return True

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

    def modify_order_levels(self, ticket: int, stop_loss: float, take_profit: list) -> bool:
        """
        Invia una richiesta TRADE_ACTION_SLTP a MT5 per aggiornare Stop Loss e Take Profit.
        """
        print(f"🛠️ [TEST MODE] Simulazione apertura per {ticket} | stop_loss: {stop_loss}, take_profit: {take_profit}")
        return
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
            print(f"❌ Errore modifica ordine {ticket}: {result.comment}")
            return False

        print(f"✅ Ordine {ticket} aggiornato con successo | SL: {stop_loss} | TP: {tp_price}")
        return True