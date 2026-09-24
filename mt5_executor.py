import math
import time
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5

import journal
from logger_config import setup_logger

logger = setup_logger(__name__)

# ⚠️ INTERRUTTORE UNICO TEST / REALE
# True  = nessun ordine viene realmente inviato a MT5, tutto viene solo simulato.
# False = il bot opera davvero sul conto collegato al terminale MT5.
# Prima era gestito con un 'return' anticipato dentro ogni metodo: bastava
# dimenticarne uno per ritrovarsi a metà tra simulazione e operatività reale.
TEST_MODE = False

# Codice di esito "nessun errore" restituito da mt5.last_error()
MT5_RESULT_OK = 1


def broker_time_str(broker_epoch) -> Optional[str]:
    """
    Orari di MT5 (tick, deal) come li mostra l'app MT5: sono secondi nel fuso
    del server del broker, quindi vanno formattati "come se fossero UTC".
    """
    if not broker_epoch:
        return None
    return datetime.fromtimestamp(broker_epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class MT5UnavailableError(Exception):
    """
    MT5 non ha risposto alla richiesta (terminale chiuso, connessione persa).
    Va distinto da "posizione non trovata": scambiarli farebbe segnare come
    chiuse posizioni che in realtà sono ancora a mercato.
    """


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

    def _broker_clock(self, symbol: Optional[str]) -> Optional[str]:
        """Ora del server del broker adesso (dall'ultimo tick del simbolo)."""
        if not symbol:
            return None
        try:
            tick = mt5.symbol_info_tick(symbol)
        except Exception:
            return None
        return broker_time_str(tick.time) if tick is not None else None

    def _send(self, operation: str, request: dict, symbol: Optional[str] = None):
        """
        Unico punto di invio degli ordini a MT5: registra nel diario ogni
        richiesta con il suo esito, e gestisce il caso in cui order_send
        restituisca None (richiesta malformata o terminale non connesso), che
        prima mandava in errore l'intera elaborazione del messaggio.
        Ritorna (ok, result, error).
        """
        result = mt5.order_send(request)
        if result is None:
            ok, error = False, f"nessuna risposta da MT5 {mt5.last_error()}"
        else:
            ok = result.retcode == mt5.TRADE_RETCODE_DONE
            error = None if ok else f"{result.comment} (retcode {result.retcode})"

        journal.record(
            "MT5_ORDER", operation=operation, ok=ok, error=error, request=request,
            broker_time=self._broker_clock(symbol or request.get("symbol")),
            retcode=getattr(result, "retcode", None), order=getattr(result, "order", None),
            price=getattr(result, "price", None), volume=getattr(result, "volume", None),
        )
        return ok, result, error

    def _find_position(self, ticket: int):
        """
        La posizione aperta con questo ticket, o None se non esiste più.
        Solleva MT5UnavailableError se MT5 non risponde: in quel caso non
        sappiamo se la posizione sia aperta o chiusa.
        """
        positions = mt5.positions_get(ticket=ticket)
        if positions is None:
            code, description = mt5.last_error()
            if code != MT5_RESULT_OK:
                raise MT5UnavailableError(f"positions_get({ticket}) fallita: {code} {description}")
        return positions[0] if positions else None

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

        ok, result, error = self._send("OPEN", request)
        if not ok:
            logger.error(f"❌ Errore apertura ordine MT5: {error}")
            return {"success": False, "mt5_ticket": None, "fill_price": None, "volume": None, "error": error}

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

        try:
            pos = self._find_position(ticket)
        except MT5UnavailableError as e:
            logger.error(f"❌ BE non applicato al ticket {ticket}: {e}")
            return False
        if pos is None:
            logger.info(f"ℹ️ Ticket {ticket} non più aperto su MT5 (probabilmente TP già raggiunto).")
            return False

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": pos.ticket,
            "sl": float(entry_price),
            "tp": pos.tp
        }
        ok, result, error = self._send("BREAKEVEN", request, symbol=pos.symbol)
        if ok:
            logger.info(f"🎯 SL spostato a BE per la posizione #{pos.ticket}")
            return True

        logger.error(f"❌ Errore spostamento BE posizione #{pos.ticket}: {error}")
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

        try:
            pos = self._find_position(ticket)
        except MT5UnavailableError as e:
            logger.error(f"❌ Chiusura del ticket {ticket} non inviata: {e}")
            return False
        if pos is None:
            logger.info(f"ℹ️ Ticket {ticket} non presente su MT5: nessuna chiusura necessaria.")
            return True

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

        ok, result, error = self._send("CLOSE", request)
        if not ok:
            logger.error(f"❌ Errore chiusura posizione {pos.ticket}: {error}")
            return False

        logger.info(f"🔒 Posizione {pos.ticket} chiusa correttamente su MT5.")
        return True

    def partial_close_position(self, ticket: int, volume: float) -> Optional[float]:
        """
        Chiude a mercato solo una parte di una posizione. Il volume viene
        arrotondato PER DIFETTO allo step del broker; se il risultato è sotto il
        lotto minimo non si chiude nulla, se il residuo resterebbe sotto il
        minimo si chiude tutta la posizione. Ritorna il volume chiuso, o None
        se la chiusura non è avvenuta.
        """
        if self.test_mode:
            logger.info(f"🛠️ [TEST MODE] Simulazione chiusura parziale di {volume} lotti del ticket {ticket}")
            return volume

        try:
            pos = self._find_position(ticket)
        except MT5UnavailableError as e:
            logger.error(f"❌ Chiusura parziale del ticket {ticket} non inviata: {e}")
            return None
        if pos is None:
            logger.info(f"ℹ️ Ticket {ticket} non presente su MT5: chiusura parziale non necessaria.")
            return None

        info = mt5.symbol_info(pos.symbol)
        tick = mt5.symbol_info_tick(pos.symbol)
        if info is None or tick is None:
            logger.error(f"❌ Dati di {pos.symbol} non disponibili: chiusura parziale di {pos.ticket} non inviata.")
            return None

        step = info.volume_step or 0.01
        volume = round(math.floor(volume / step + 1e-9) * step, 2)
        if volume < info.volume_min:
            logger.warning(f"⚠️ Chiusura parziale di {pos.ticket} non eseguita: {volume} lotti è sotto il minimo del broker ({info.volume_min}).")
            return None
        if pos.volume - volume < info.volume_min - 1e-9:
            volume = pos.volume

        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": volume,
            "type": close_type,
            "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
            "deviation": 20,
            "magic": 990011,
            "comment": "Partial close by Bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        ok, result, error = self._send("CLOSE_PARTIAL", request, symbol=pos.symbol)
        if not ok:
            logger.error(f"❌ Errore chiusura parziale posizione {pos.ticket}: {error}")
            return None

        logger.info(f"✂️ Chiusi {volume} lotti su {pos.volume} della posizione {pos.ticket}.")
        return volume

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

        ok, result, error = self._send("MODIFY_SL_TP", request)
        if not ok:
            logger.error(f"❌ Errore modifica ordine {ticket}: {error}")
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

        Solleva MT5UnavailableError se MT5 non risponde: ritornare None in quel
        caso farebbe credere che la posizione sia stata chiusa.
        """
        if self.test_mode:
            return None

        pos = self._find_position(ticket)
        if pos is None:
            return None

        return {
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "price_open": pos.price_open,
            "stop_loss": pos.sl,
            "take_profit": pos.tp,
        }

    def can_move_sl(self, ticket: int, new_sl: float) -> bool:
        """
        True se MT5 accetterebbe adesso questo SL sulla posizione: dal lato
        giusto del prezzo di chiusura e oltre la distanza minima imposta dal
        broker (stops level). Evita di inviare modifiche destinate al rifiuto.
        """
        if self.test_mode or new_sl is None:
            return False
        pos = self._find_position(ticket)
        if pos is None:
            return False
        info = mt5.symbol_info(pos.symbol)
        tick = mt5.symbol_info_tick(pos.symbol)
        if info is None or tick is None:
            return False

        min_distance = info.trade_stops_level * info.point
        if pos.type == mt5.ORDER_TYPE_BUY:
            return tick.bid - new_sl > min_distance
        return new_sl - tick.ask > min_distance

    def favorable_move(self, ticket: int) -> Optional[float]:
        """
        Di quanti $ di prezzo la posizione è in guadagno rispetto al NOSTRO
        prezzo di ingresso (negativo se in perdita), misurato sul prezzo a cui
        verrebbe chiusa adesso. None se non è misurabile (TEST MODE, posizione
        chiusa, dati mancanti).
        """
        if self.test_mode:
            return None
        pos = self._find_position(ticket)
        if pos is None:
            return None
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return None
        if pos.type == mt5.ORDER_TYPE_BUY:
            return tick.bid - pos.price_open
        return pos.price_open - tick.ask

    def get_close_info(self, ticket: int) -> dict:
        """
        Esito finale di una posizione chiusa, letto dallo storico dei deal di MT5:
        motivo (TP, SL, bot, manuale...), prezzo di chiusura e profitto netto
        (profitto + commissioni + swap). Dizionario vuoto se non disponibile.
        """
        if self.test_mode:
            return {}

        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return {}
        exits = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not exits:
            return {}

        reasons = {
            mt5.DEAL_REASON_TP: "TAKE_PROFIT",
            mt5.DEAL_REASON_SL: "STOP_LOSS",
            mt5.DEAL_REASON_SO: "STOP_OUT",
            mt5.DEAL_REASON_EXPERT: "BOT",
            mt5.DEAL_REASON_CLIENT: "MANUALE_PC",
            mt5.DEAL_REASON_MOBILE: "MANUALE_MOBILE",
            mt5.DEAL_REASON_WEB: "MANUALE_WEB",
        }
        last_exit = exits[-1]
        return {
            "close_reason": reasons.get(last_exit.reason, f"ALTRO_{last_exit.reason}"),
            "close_price": last_exit.price,
            "close_time_broker": broker_time_str(getattr(last_exit, "time", None)),
            "profit": round(sum(d.profit + d.commission + d.swap for d in deals), 2),
        }

    def check_health(self) -> tuple[bool, str]:
        """
        Verifica che il terminale MT5 sia raggiungibile, collegato al server del
        broker e con l'Algo Trading attivo; se il terminale non risponde prova a
        reinizializzare la connessione. Usato dal controllo periodico del listener.
        """
        try:
            info = mt5.terminal_info()
            if info is None:
                mt5.initialize()
                info = mt5.terminal_info()
        except Exception as e:
            # Collegamento con il terminale caduto (es. connessione RPyC chiusa):
            # la prossima chiamata proverà a riaprirlo.
            return False, f"collegamento con MT5 interrotto ({type(e).__name__}: {e})"
        if info is None:
            return False, f"terminale MT5 non raggiungibile {mt5.last_error()}"
        if not info.connected:
            return False, "terminale MT5 non collegato al server del broker"
        if not info.trade_allowed:
            return False, "Algo Trading disattivato nel terminale MT5: gli ordini verrebbero rifiutati"
        return True, "MT5 operativo"

    def account_snapshot(self) -> dict:
        """Stato sintetico del conto per il battito periodico e il diario."""
        info = mt5.account_info()
        if info is None:
            return {}
        return {
            "login": info.login,
            "server": info.server,
            "demo": info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
            "balance": info.balance,
            "equity": info.equity,
            "margin_free": info.margin_free,
            "open_positions": mt5.positions_total(),
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
