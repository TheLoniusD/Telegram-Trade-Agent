"""
Broker finto in memoria al posto del pacchetto MetaTrader5 (o del collegamento
RPyC del server), per provare il bot senza MT5, senza Windows e senza conto.

Si comporta come un broker vero nei punti che contano per il bot:
  - apertura a mercato al prezzo ask (BUY) / bid (SELL);
  - ordini limite: BUY_LIMIT solo sotto l'ask, SELL_LIMIT solo sopra il bid
    (altrimenti "Invalid price" 10015), eseguiti quando il prezzo li raggiunge;
  - SL e TP accettati solo dal lato giusto del prezzo di chiusura e ad almeno
    la distanza minima del broker (stops level, 0,50 $), altrimenti
    "Invalid stops" 10016, come il pareggio rifiutato dal broker vero;
  - chiusure totali o parziali con i deal nello storico.
SL e TP non scattano da soli: un test che li vuole usa broker_close().
Ogni richiesta finisce in `requests` per poterla controllare.
"""
import time
from types import SimpleNamespace as NS

# Costanti con gli stessi valori del pacchetto MetaTrader5 ufficiale
TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 8
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TIME_GTC = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_INVALID = 10013
TRADE_RETCODE_INVALID_PRICE = 10015
TRADE_RETCODE_INVALID_STOPS = 10016
SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_FULL = 4
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_REAL = 2
DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY = 0, 1, 3
DEAL_REASON_CLIENT, DEAL_REASON_MOBILE, DEAL_REASON_WEB, DEAL_REASON_EXPERT = 0, 1, 2, 3
DEAL_REASON_SL, DEAL_REASON_TP, DEAL_REASON_SO = 4, 5, 6

CONTRACT_SIZE = 100        # 1 lotto di XAUUSD = 100 once: 1 $ di prezzo = 100 $ a lotto
STOPS_LEVEL_POINTS = 50    # distanza minima di SL/TP dal prezzo: 50 punti = 0,50 $
POINT = 0.01
BOT_MAGIC = 990011


class FakeMT5:
    def __init__(self):
        self.next_ticket = 50000001
        self.reset()

    # ------------------------------------------------------------------
    # Controllo dello scenario (solo per i test)
    # ------------------------------------------------------------------
    def reset(self, price: float = 4300.0, spread: float = 0.20, balance: float = 7000.0):
        """Mercato nuovo. I numeri dei ticket continuano a crescere: mai riusati."""
        self.spread = spread
        self.bid = price
        self.ask = round(price + spread, 2)
        self.balance = balance
        self.account_mode = ACCOUNT_TRADE_MODE_DEMO
        self.symbol_mode = SYMBOL_TRADE_MODE_FULL
        self.connected = True
        self.positions = {}
        self.orders = {}
        self.deals = {}
        self.requests = []

    def move_price(self, price: float):
        """Nuovo prezzo (bid); gli ordini limite raggiunti diventano posizioni."""
        self.bid = price
        self.ask = round(price + self.spread, 2)
        for ticket, order in list(self.orders.items()):
            buy = order.type == ORDER_TYPE_BUY_LIMIT
            if (buy and self.ask <= order.price_open) or (not buy and self.bid >= order.price_open):
                self.fill_order(ticket)

    def fill_order(self, ticket: int):
        """Esegue un ordine limite al suo prezzo (anche senza muovere il mercato)."""
        order = self.orders.pop(ticket)
        buy = order.type == ORDER_TYPE_BUY_LIMIT
        self.positions[ticket] = NS(ticket=ticket, symbol=order.symbol, type=ORDER_TYPE_BUY if buy else ORDER_TYPE_SELL,
                                    volume=order.volume_initial, price_open=order.price_open, sl=order.sl, tp=order.tp,
                                    magic=order.magic, comment=order.comment)
        self.deals[ticket] = [NS(entry=DEAL_ENTRY_IN, reason=DEAL_REASON_EXPERT, price=order.price_open,
                                 profit=0.0, commission=0.0, swap=0.0)]

    def broker_close(self, ticket: int, reason: str = "SL", price: float = None):
        """Il broker chiude la posizione da solo (SL o TP preso) e lo registra nello storico."""
        pos = self.positions.pop(ticket)
        price = price if price is not None else (pos.sl if reason == "SL" else pos.tp)
        self._record_close(pos, pos.volume, price, DEAL_REASON_SL if reason == "SL" else DEAL_REASON_TP)

    def add_position(self, ticket: int, direction: str, price_open: float, sl: float = 0.0, tp: float = 0.0,
                     volume: float = 0.05, magic: int = BOT_MAGIC, comment: str = ""):
        """Posizione aperta da fuori (a mano o da un'altra sessione del bot)."""
        self.positions[ticket] = NS(ticket=ticket, symbol="XAUUSD", type=ORDER_TYPE_BUY if direction == "BUY" else ORDER_TYPE_SELL,
                                    volume=volume, price_open=price_open, sl=sl, tp=tp, magic=magic, comment=comment)

    # ------------------------------------------------------------------
    # API MetaTrader5 usata dal bot
    # ------------------------------------------------------------------
    def initialize(self, *args, **kwargs):
        return self.connected

    def last_error(self):
        return (1, "Success") if self.connected else (-10004, "No IPC connection")

    def terminal_info(self):
        return NS(connected=self.connected, trade_allowed=True)

    def account_info(self):
        if not self.connected:
            return None
        return NS(balance=self.balance, equity=self.balance, margin_free=self.balance, trade_mode=self.account_mode,
                  login=123456, server="Fake-Demo", currency="USD")

    def symbol_info(self, symbol):
        if not self.connected:
            return None
        return NS(name=symbol, ask=self.ask, bid=self.bid, trade_mode=self.symbol_mode, digits=2,
                  trade_tick_value=1.0, trade_tick_size=POINT, trade_stops_level=STOPS_LEVEL_POINTS, point=POINT,
                  volume_min=0.01, volume_max=100.0, volume_step=0.01)

    def symbol_info_tick(self, symbol):
        if not self.connected:
            return None
        return NS(bid=self.bid, ask=self.ask, time=int(time.time()))

    def positions_get(self, ticket=None, symbol=None):
        if not self.connected:
            return None
        if ticket is not None:
            return (self.positions[ticket],) if ticket in self.positions else ()
        return tuple(self.positions.values())

    def positions_total(self):
        return len(self.positions)

    def orders_get(self, ticket=None, symbol=None):
        if not self.connected:
            return None
        if ticket is not None:
            return (self.orders[ticket],) if ticket in self.orders else ()
        return tuple(self.orders.values())

    def history_deals_get(self, position=None, *args, **kwargs):
        return tuple(self.deals.get(position, ()))

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        move = price_close - price_open if order_type == ORDER_TYPE_BUY else price_open - price_close
        return round(move * CONTRACT_SIZE * volume, 2)

    def order_calc_margin(self, order_type, symbol, volume, price):
        return round(price * CONTRACT_SIZE * volume / 100, 2)   # leva 1:100

    def order_send(self, request=None, **kwargs):
        # main passa un dict, il server (RPyC) gli argomenti con nome
        req = dict(request or kwargs)
        self.requests.append(req)
        if not self.connected:
            return None
        action = req.get("action")
        if action == TRADE_ACTION_DEAL:
            return self._close(req) if req.get("position") else self._open(req)
        if action == TRADE_ACTION_SLTP:
            return self._modify_position(req)
        if action == TRADE_ACTION_PENDING:
            return self._place_pending(req)
        if action == TRADE_ACTION_MODIFY:
            return self._modify_pending(req)
        if action == TRADE_ACTION_REMOVE:
            if self.orders.pop(req.get("order"), None) is None:
                return self._result(TRADE_RETCODE_INVALID, comment="Order doesn't exist")
            return self._result(TRADE_RETCODE_DONE, order=req.get("order"))
        return self._result(TRADE_RETCODE_INVALID, comment="Invalid request")

    # ------------------------------------------------------------------
    # Logica del broker finto
    # ------------------------------------------------------------------
    def _result(self, retcode, order=0, price=0.0, volume=0.0, comment="Request executed"):
        return NS(retcode=retcode, order=order, deal=order, price=price, volume=volume, comment=comment)

    def _stops_valid(self, buy: bool, sl, tp, reference: float = None) -> bool:
        """SL/TP dal lato giusto del prezzo di chiusura (o dell'ordine) e oltre la distanza minima."""
        price = reference if reference is not None else (self.bid if buy else self.ask)
        gap = STOPS_LEVEL_POINTS * POINT
        if buy:
            return (not sl or price - sl >= gap) and (not tp or tp - price >= gap)
        return (not sl or sl - price >= gap) and (not tp or price - tp >= gap)

    def _new_ticket(self) -> int:
        ticket = self.next_ticket
        self.next_ticket += 1
        return ticket

    def _open(self, req):
        buy = req["type"] == ORDER_TYPE_BUY
        price = self.ask if buy else self.bid
        if not self._stops_valid(buy, req.get("sl"), req.get("tp")):
            return self._result(TRADE_RETCODE_INVALID_STOPS, comment="Invalid stops")
        ticket = self._new_ticket()
        self.positions[ticket] = NS(ticket=ticket, symbol=req["symbol"], type=req["type"], volume=req["volume"],
                                    price_open=price, sl=req.get("sl") or 0.0, tp=req.get("tp") or 0.0,
                                    magic=req.get("magic"), comment=req.get("comment"))
        self.deals[ticket] = [NS(entry=DEAL_ENTRY_IN, reason=DEAL_REASON_EXPERT, price=price, profit=0.0,
                                 commission=0.0, swap=0.0)]
        return self._result(TRADE_RETCODE_DONE, order=ticket, price=price, volume=req["volume"])

    def _place_pending(self, req):
        buy = req["type"] == ORDER_TYPE_BUY_LIMIT
        price = req["price"]
        if (buy and price >= self.ask) or (not buy and price <= self.bid):
            return self._result(TRADE_RETCODE_INVALID_PRICE, comment="Invalid price")
        if not self._stops_valid(buy, req.get("sl"), req.get("tp"), reference=price):
            return self._result(TRADE_RETCODE_INVALID_STOPS, comment="Invalid stops")
        ticket = self._new_ticket()
        self.orders[ticket] = NS(ticket=ticket, symbol=req["symbol"], type=req["type"], volume_initial=req["volume"],
                                 volume_current=req["volume"], price_open=price, sl=req.get("sl") or 0.0,
                                 tp=req.get("tp") or 0.0, magic=req.get("magic"), comment=req.get("comment"))
        return self._result(TRADE_RETCODE_DONE, order=ticket, price=0.0, volume=req["volume"])

    def _modify_pending(self, req):
        order = self.orders.get(req.get("order"))
        if order is None:
            return self._result(TRADE_RETCODE_INVALID, comment="Order doesn't exist")
        price = req.get("price") or order.price_open
        if not self._stops_valid(order.type == ORDER_TYPE_BUY_LIMIT, req.get("sl"), req.get("tp"), reference=price):
            return self._result(TRADE_RETCODE_INVALID_STOPS, comment="Invalid stops")
        order.price_open, order.sl, order.tp = price, req.get("sl") or 0.0, req.get("tp") or 0.0
        return self._result(TRADE_RETCODE_DONE, order=order.ticket)

    def _modify_position(self, req):
        pos = self.positions.get(req.get("position"))
        if pos is None:
            return self._result(TRADE_RETCODE_INVALID, comment="Position doesn't exist")
        if not self._stops_valid(pos.type == ORDER_TYPE_BUY, req.get("sl"), req.get("tp")):
            return self._result(TRADE_RETCODE_INVALID_STOPS, comment="Invalid stops")
        pos.sl, pos.tp = req.get("sl") or 0.0, req.get("tp") or 0.0
        return self._result(TRADE_RETCODE_DONE, order=pos.ticket)

    def _close(self, req):
        pos = self.positions.get(req.get("position"))
        if pos is None:
            return self._result(TRADE_RETCODE_INVALID, comment="Position doesn't exist")
        price = self.bid if pos.type == ORDER_TYPE_BUY else self.ask
        volume = min(req.get("volume") or pos.volume, pos.volume)
        if volume < pos.volume - 1e-9:
            pos.volume = round(pos.volume - volume, 2)
        else:
            self.positions.pop(pos.ticket)
        self._record_close(pos, volume, price, DEAL_REASON_EXPERT)
        return self._result(TRADE_RETCODE_DONE, order=pos.ticket, price=price, volume=volume)

    def _record_close(self, pos, volume, price, reason):
        profit = self.order_calc_profit(pos.type, pos.symbol, volume, pos.price_open, price)
        self.balance = round(self.balance + profit, 2)
        self.deals.setdefault(pos.ticket, []).append(NS(entry=DEAL_ENTRY_OUT, reason=reason, price=price,
                                                        profit=profit, commission=0.0, swap=0.0))


# Le costanti sono raggiungibili anche come attributi dell'oggetto, come nel modulo vero
for _name, _value in list(globals().items()):
    if _name.isupper():
        setattr(FakeMT5, _name, _value)
