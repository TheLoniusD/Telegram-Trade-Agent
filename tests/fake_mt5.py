"""
Finto modulo MetaTrader5 in memoria, per testare MT5Executor e l'intera
pipeline senza broker, senza Wine/RPyC e senza token dell'agente.

Espone la stessa superficie usata dal progetto (initialize, symbol_info,
symbol_info_tick, positions_get, order_send(**kwargs), account_info e le
costanti) e simula un broker minimale:
  - apertura a mercato al prezzo ask/bid corrente
  - modifica SL/TP con la stessa validazione dei livelli di un broker reale
    (retcode 10016 "Invalid stops" se lo SL è dal lato sbagliato del prezzo)
  - chiusura di una posizione con un ordine opposto
Ogni richiesta viene registrata in `requests` per poterla ispezionare nei test.
"""
import time
from types import SimpleNamespace

# Costanti con gli stessi valori del pacchetto MetaTrader5 ufficiale
TRADE_ACTION_DEAL = 1
TRADE_ACTION_SLTP = 6
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TIME_GTC = 0
ORDER_FILLING_IOC = 1
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_INVALID_STOPS = 10016
TRADE_RETCODE_INVALID = 10013
SYMBOL_TRADE_MODE_DISABLED = 0
SYMBOL_TRADE_MODE_FULL = 4
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_REAL = 2


class FakeMT5:
    def __init__(self):
        self.reset()

    # ------------------------------------------------------------------
    # Controllo dello scenario (usato solo dai test)
    # ------------------------------------------------------------------
    def reset(self, price: float = 4380.0, spread: float = 0.20, balance: float = 10000.0):
        self.bid = price
        self.ask = round(price + spread, 2)
        self.spread = spread
        self.balance = balance
        self.trade_mode = SYMBOL_TRADE_MODE_FULL
        self.tick_time = None          # None = "adesso" ad ogni lettura
        self.tick_available = True
        self.symbol_known = True
        self.positions = {}            # ticket -> SimpleNamespace
        self.requests = []             # tutte le richieste ricevute da order_send
        self.next_ticket = 50000001
        self.force_retcode = None      # es. 10016 per simulare un rifiuto
        self.force_none = False        # order_send ritorna None (richiesta malformata)

    def move_price(self, price: float):
        self.bid = price
        self.ask = round(price + self.spread, 2)

    # ------------------------------------------------------------------
    # API MetaTrader5
    # ------------------------------------------------------------------
    def initialize(self, *args, **kwargs):
        return True

    def last_error(self):
        return (1, "Success")

    def account_info(self):
        return SimpleNamespace(balance=self.balance, trade_mode=ACCOUNT_TRADE_MODE_DEMO, login=123456, server="Fake-Demo", currency="USD")

    def symbol_info(self, symbol):
        if not self.symbol_known:
            return None
        return SimpleNamespace(
            name=symbol, ask=self.ask, bid=self.bid, trade_mode=self.trade_mode,
            trade_tick_value=1.0, trade_tick_size=0.01,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
        )

    def symbol_info_tick(self, symbol):
        if not self.tick_available:
            return None
        t = self.tick_time if self.tick_time is not None else int(time.time())
        return SimpleNamespace(bid=self.bid, ask=self.ask, time=t)

    def positions_get(self, ticket=None, symbol=None):
        if ticket is not None:
            pos = self.positions.get(ticket)
            return (pos,) if pos else ()
        return tuple(self.positions.values())

    def order_send(self, **request):
        self.requests.append(dict(request))

        if self.force_none:
            return None
        if self.force_retcode is not None:
            return self._result(self.force_retcode, comment=f"Forced retcode {self.force_retcode}")

        action = request.get("action")
        if action == TRADE_ACTION_DEAL and "position" not in request:
            return self._open(request)
        if action == TRADE_ACTION_DEAL:
            return self._close(request)
        if action == TRADE_ACTION_SLTP:
            return self._modify(request)
        return self._result(TRADE_RETCODE_INVALID, comment="Invalid request")

    # ------------------------------------------------------------------
    # Logica del broker finto
    # ------------------------------------------------------------------
    def _result(self, retcode, order=0, price=0.0, volume=0.0, comment="Request executed"):
        return SimpleNamespace(retcode=retcode, order=order, deal=order, price=price, volume=volume, comment=comment)

    def _stops_valid(self, pos_type, sl, tp):
        """SL/TP devono stare dal lato giusto del prezzo di chiusura corrente."""
        close_price = self.bid if pos_type == ORDER_TYPE_BUY else self.ask
        if pos_type == ORDER_TYPE_BUY:
            return (not sl or sl < close_price) and (not tp or tp > close_price)
        return (not sl or sl > close_price) and (not tp or tp < close_price)

    def _open(self, req):
        pos_type = req["type"]
        price = self.ask if pos_type == ORDER_TYPE_BUY else self.bid
        sl, tp = req.get("sl", 0.0), req.get("tp", 0.0)
        if not self._stops_valid(pos_type, sl, tp):
            return self._result(TRADE_RETCODE_INVALID_STOPS, comment="Invalid stops")

        ticket = self.next_ticket
        self.next_ticket += 1
        self.positions[ticket] = SimpleNamespace(
            ticket=ticket, symbol=req["symbol"], type=pos_type, volume=req["volume"],
            price_open=price, sl=sl, tp=tp, magic=req.get("magic"), comment=req.get("comment"),
        )
        return self._result(TRADE_RETCODE_DONE, order=ticket, price=price, volume=req["volume"])

    def _modify(self, req):
        pos = self.positions.get(req.get("position"))
        if pos is None:
            return self._result(TRADE_RETCODE_INVALID, comment="Position doesn't exist")
        sl, tp = req.get("sl", 0.0), req.get("tp", 0.0)
        if not self._stops_valid(pos.type, sl, tp):
            return self._result(TRADE_RETCODE_INVALID_STOPS, comment="Invalid stops")
        pos.sl, pos.tp = sl, tp
        return self._result(TRADE_RETCODE_DONE, order=pos.ticket)

    def _close(self, req):
        pos = self.positions.pop(req.get("position"), None)
        if pos is None:
            return self._result(TRADE_RETCODE_INVALID, comment="Position doesn't exist")
        price = self.bid if pos.type == ORDER_TYPE_BUY else self.ask
        return self._result(TRADE_RETCODE_DONE, order=pos.ticket, price=price, volume=pos.volume)


# Rende gli attributi accessibili come un modulo: fake_mt5.mt5.ORDER_TYPE_BUY, ecc.
for _name, _value in list(globals().items()):
    if _name.isupper():
        setattr(FakeMT5, _name, _value)

mt5 = FakeMT5()
