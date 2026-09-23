import math

import MetaTrader5 as mt5


DEFAULT_SL_DIST_GOLD = 5.0

# Quota massima del margine libero impegnabile da un singolo segnale (2 ticket)
MARGIN_USAGE_LIMIT = 0.5

class RiskManager:
    """
    AGENTE 2: Valida il rischio, calcola la dimensione del lotto (Position Sizing) 
    in base alla % di capitale da rischiare e verifica i parametri prima dell'invio.
    """

# ==============================================================================
# CONFIGURAZIONE PERCENTUALE DI RISCHIO FISSO (RISK MANAGEMENT)
# ==============================================================================
# Mantenere 'risk_percent' fisso all'1.0% (o 2.0%) rende il codice del bot "universale"
# e sicuro per qualsiasi dimensione del conto:
#
# 1. Con capitali piccoli (es. 100€):
#    Il calcolo teorico (1% di 100€ = 1€) richiederebbe circa 0.002 lotti. Poiché MT5 
#    non permette di scendere sotto 0.01, la funzione intercetta il valore e imposta 
#    automaticamente l'ordine al minimo consentito dal broker (0.01 lotti).
#
# 2. Con capitali in crescita o ricaricati (es. da 500€ a 10.000€):
#    Il sistema scalerà in automatico senza dover toccare il codice: calcolerà i lotti 
#    esatti per rischiare sempre e soltanto l'1% reale del saldo disponibile per ogni trade.
# ==============================================================================

    def __init__(self, risk_percent: float = 2.0, max_open_trades: int = 3):
        self.risk_percent = risk_percent
        self.max_open_trades = max_open_trades

    def validate_and_build_order(self, trade_data: dict) -> dict:
        # L'Order Manager salva simbolo, direzione, SL e TP dentro 'tickets'
        # (uno per ogni target), non alla radice del trade: leggerli dalla radice
        # dava sempre None, quindi ogni BUY veniva aperto come SELL, sempre con
        # SL temporaneo e senza TP.
        tickets = trade_data.get("tickets", {})
        first_ticket = tickets.get("tp1", {})

        symbol = first_ticket.get("symbol") or "XAUUSD"
        direction = first_ticket.get("direction")
        stop_loss = first_ticket.get("stop_loss")
        entry_min = trade_data.get("entry_min")

        if direction not in ("BUY", "SELL"):
            return {"approved": False, "reason": f"Direzione non valida: {direction}"}

        # 1. Calcolo Prezzo d'Ingresso Stimato
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            return {"approved": False, "reason": f"Simbolo {symbol} non trovato su MT5"}

        current_price = symbol_info.ask if direction == "BUY" else symbol_info.bid
        entry_price = entry_min if entry_min is not None else current_price

        # 2. Gestione Stop Loss Mancante (Segnale Rapido)
        if not stop_loss:
            # Calcolo di uno SL temporaneo per entrare subito a mercato in sicurezza
            if direction == "BUY":
                stop_loss = entry_price - DEFAULT_SL_DIST_GOLD
            else:
                stop_loss = entry_price + DEFAULT_SL_DIST_GOLD

        # 3. Calcolo Lottaggio basato su % di Rischio, limitato dal margine libero
        total_calculated_lots = self._calculate_lot_size(symbol, entry_price, stop_loss)
        total_calculated_lots = min(total_calculated_lots, self._max_lots_by_margin(symbol, direction, current_price))

        # SDOPPIAMENTO LOTTAGGIO: dividiamo a metà arrotondando PER DIFETTO allo
        # step del broker. Arrotondare per eccesso (o forzare il minimo) può
        # superare il margine disponibile: il primo ordine passa e il secondo
        # viene rifiutato con "No money".
        step_lot = symbol_info.volume_step or 0.01
        single_ticket_lots = round(math.floor(total_calculated_lots / 2 / step_lot) * step_lot, 2)
        if single_ticket_lots < symbol_info.volume_min:
            return {"approved": False, "reason": f"Margine insufficiente per aprire 2 posizioni da almeno {symbol_info.volume_min} lotti"}

        # 4. Gestione Multi-Ticket: ogni ticket usa il TP salvato per il suo target
        # (None in fase rapida, in attesa degli update successivi)
        orders_to_execute = {}
        for key in ("tp1", "tp2"):
            orders_to_execute[key] = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": tickets.get(key, {}).get("take_profit"),
                "volume": single_ticket_lots,
                "ticket_id": trade_data.get("ticket_id"),
            }

        return {
            "approved": True,
            "orders": orders_to_execute
        }

    def _max_lots_by_margin(self, symbol: str, direction: str, price: float) -> float:
        """
        Lotti totali apribili con il margine libero del conto, lasciando un
        cuscinetto (MARGIN_USAGE_LIMIT) per non avvicinarsi allo stop-out.
        Se MT5 non fornisce i dati ritorna infinito: decide solo il rischio.
        """
        account_info = mt5.account_info()
        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        margin_per_lot = mt5.order_calc_margin(order_type, symbol, 1.0, price)
        if not account_info or not margin_per_lot:
            return float("inf")
        return (account_info.margin_free * MARGIN_USAGE_LIMIT) / margin_per_lot


    def _calculate_lot_size(self, symbol: str, entry_price: float, stop_loss: float) -> float:
        """
    CALCOLO AUTOMATICO E DINAMICO DELLA DIMENSIONE DELL'ORDINE (LOTTI)

    Prende lo Stop Loss fornito dal canale Telegram e lo usa per calcolare QUANTI LOTTI 
    aprire, garantendo di non superare mai la percentuale di rischio impostata sul capitale.

    Esempio pratico su conto da 10.000€ (Rischio impostato all'1%):
    1. Legge il saldo attuale del conto -> 10.000€.
    2. Calcola la perdita massima consentita in denaro -> 1% di 10.000€ = 100€.
    3. Misura la distanza tra Prezzo d'ingresso (es. 2640.00) e Stop Loss (es. 2635.00) -> 5.00$ di distanza.
    4. Calcola la perdita teorica con 1.00 lotto intero su XAUUSD -> 5.00$ * 100 oz = 500$.
    5. Divide il rischio consentito per la perdita teorica -> 100€ / 500$ = 0.20 lotti precisi.
    6. Normalizza il risultato sui limiti del broker (minimo 0.01, scatto 0.01) -> Invia un ordine da 0.20 lotti su MT5.
        """


        account_info = mt5.account_info()
        balance = account_info.balance if account_info else 10000.0  # Fallback a $10.000 se offline
        
        risk_amount = balance * (self.risk_percent / 100.0)
        sl_distance = abs(entry_price - stop_loss)

        if sl_distance <= 0:
            return 0.01

        # Per XAUUSD (Oro): 1 lotto standard = 100 oz. 1.0$ di movimento = $100 per lotto
        symbol_info = mt5.symbol_info(symbol)
        tick_value = symbol_info.trade_tick_value if symbol_info else 1.0
        tick_size = symbol_info.trade_tick_size if symbol_info else 0.01

        loss_per_lot = (sl_distance / tick_size) * tick_value if tick_size > 0 else sl_distance * 100.0
        
        if loss_per_lot <= 0:
            return 0.01

        raw_lots = risk_amount / loss_per_lot

        # Normalizzazione sui limiti stabiliti dal broker
        min_lot = symbol_info.volume_min if symbol_info else 0.01
        max_lot = symbol_info.volume_max if symbol_info else 100.0
        step_lot = symbol_info.volume_step if symbol_info else 0.01

        lots = round(raw_lots / step_lot) * step_lot
        return max(min_lot, min(max_lot, round(lots, 2)))