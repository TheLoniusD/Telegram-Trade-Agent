import math
import os

from mt5_connection import mt5

import journal


# Distanza dello SL temporaneo (fase rapida) dal prezzo del segnale.
# 10$ è la distanza usata dal trader nei segnali completi osservati il 23/09
# (BUY 4302 -> SL 4292, SELL 4314 -> SL 4324): così lo SL temporaneo coincide
# quasi sempre con quello reale e il lottaggio calcolato resta valido anche
# dopo l'edit con i livelli definitivi.
DEFAULT_SL_DIST_GOLD = 10.0

# Quota massima del margine libero impegnabile da un singolo segnale (2 ticket)
MARGIN_USAGE_LIMIT = 0.5

# Ingresso nella zona del trader. Il segnale rapido "Gold buy 4304" diventa poi
# "BUY @ 4304 - 4299": la zona è sempre larga 5$ a favore del trader, che si
# posiziona al suo interno. Entrando tutto subito a mercato noi finivamo sul
# bordo peggiore o oltre (fino a 3,4$ peggio il 25/09), e quando il trader
# scriveva "Running 45 pips, BE+" eravamo ancora in pari o in perdita.
# 'zone'   = TP1 con ordine limite al prezzo del segnale, TP2 a metà zona
#            (a mercato se il prezzo è già a quel livello o migliore)
# 'market' = tutto a mercato subito, come prima
ENTRY_MODE = os.getenv("ENTRY_MODE", "zone")
ENTRY_ZONE_WIDTH = float(os.getenv("ENTRY_ZONE_WIDTH", "5.0"))

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

        # 3. Prezzi di ingresso dei due ticket (vedi ENTRY_MODE)
        entry_levels = self._entry_levels(direction, entry_min, trade_data.get("entry_max"))

        # 4. Lottaggio: metà del rischio per ticket, misurata dal prezzo a cui
        # quel ticket entrerà (il livello limite, o il prezzo corrente se a
        # mercato), poi limitata dal margine libero.
        step_lot = symbol_info.volume_step or 0.01
        lots = {}
        for key in ("tp1", "tp2"):
            level = entry_levels[key]
            price = level if level is not None else current_price
            lots[key] = self._calculate_lot_size(symbol, direction, price, stop_loss) / 2
        margin_lots = self._max_lots_by_margin(symbol, direction, current_price)
        scale = min(1.0, margin_lots / sum(lots.values())) if sum(lots.values()) > 0 else 1.0
        # Per difetto allo step del broker: per eccesso si supererebbe il rischio
        # o il margine (il secondo ordine veniva rifiutato con "No money").
        lots = {k: round(math.floor(v * scale / step_lot) * step_lot, 2) for k, v in lots.items()}

        journal.record("RISK_CALC", ticket_id=trade_data.get("ticket_id"), direction=direction,
                       price=current_price, stop_loss=stop_loss, risk_percent=self.risk_percent,
                       entry_levels=entry_levels, lots=lots, lots_by_margin=round(margin_lots, 2),
                       lots_total=round(sum(lots.values()), 2))

        if min(lots.values()) < symbol_info.volume_min:
            return {"approved": False, "reason": f"Margine insufficiente per aprire 2 posizioni da almeno {symbol_info.volume_min} lotti"}

        # 5. Gestione Multi-Ticket: ogni ticket usa il TP salvato per il suo target
        # (None in fase rapida, in attesa degli update successivi)
        orders_to_execute = {}
        for key in ("tp1", "tp2"):
            orders_to_execute[key] = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_levels[key] if entry_levels[key] is not None else entry_price,
                "limit_price": entry_levels[key],
                "stop_loss": stop_loss,
                "take_profit": tickets.get(key, {}).get("take_profit"),
                "volume": lots[key],
                "ticket_id": trade_data.get("ticket_id"),
            }

        return {
            "approved": True,
            "orders": orders_to_execute
        }

    def _entry_levels(self, direction: str, entry_min, entry_max) -> dict:
        """
        Prezzi limite dei due ticket nella zona del trader, o None (= a mercato).
        TP1 al prezzo del segnale (il bordo della zona da cui il trader parte),
        TP2 a metà zona. Senza prezzo nel segnale ("Try buy again") o con
        ENTRY_MODE=market si entra a mercato come prima.
        """
        if ENTRY_MODE != "zone" or entry_min is None:
            return {"tp1": None, "tp2": None}

        low, high = entry_min, entry_max
        if high is None:
            # Fase rapida: solo il prezzo del segnale, la zona si estende a favore
            low, high = (entry_min - ENTRY_ZONE_WIDTH, entry_min) if direction == "BUY" else (entry_min, entry_min + ENTRY_ZONE_WIDTH)
        low, high = min(low, high), max(low, high)

        signal_edge = high if direction == "BUY" else low
        return {"tp1": round(signal_edge, 2), "tp2": round((low + high) / 2, 2)}

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


    def _calculate_lot_size(self, symbol: str, direction: str, entry_price: float, stop_loss: float) -> float:
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

        # Perdita di 1 lotto se lo SL viene colpito, calcolata da MT5 stesso nella
        # valuta del conto (con la conversione USD->valuta del conto inclusa).
        # Il calcolo manuale con trade_tick_value/trade_tick_size il 23/09 ha
        # prodotto almeno 3 volte i lotti dovuti: li ha fermati solo il tetto sul
        # margine, con un rischio reale intorno al 12% del conto invece del 2%.
        symbol_info = mt5.symbol_info(symbol)
        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        loss_at_sl = mt5.order_calc_profit(order_type, symbol, 1.0, entry_price, stop_loss)
        if loss_at_sl is None:
            # MT5 non ha risposto: meglio il lotto minimo che un lotto sbagliato
            return symbol_info.volume_min if symbol_info else 0.01
        loss_per_lot = abs(loss_at_sl)

        if loss_per_lot <= 0:
            return 0.01

        raw_lots = risk_amount / loss_per_lot

        # Normalizzazione sui limiti stabiliti dal broker
        min_lot = symbol_info.volume_min if symbol_info else 0.01
        max_lot = symbol_info.volume_max if symbol_info else 100.0
        step_lot = symbol_info.volume_step if symbol_info else 0.01

        # Per difetto: arrotondare per eccesso supererebbe il rischio impostato
        lots = math.floor(raw_lots / step_lot) * step_lot
        return max(min_lot, min(max_lot, round(lots, 2)))