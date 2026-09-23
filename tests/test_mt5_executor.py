"""
Test unitari di MT5Executor contro il finto MT5: ogni metodo viene chiamato
direttamente e si verifica sia l'esito restituito sia cosa è arrivato al broker.
"""
import time

import pytest

from tests import fake_mt5 as F


def piano(direction="BUY", volume=0.10, sl=None, tp=None, entry=None):
    return {"symbol": "XAUUSD", "direction": direction, "volume": volume,
            "stop_loss": sl, "take_profit": tp, "entry_price": entry}


# ----------------------------------------------------------------------
# execute_open
# ----------------------------------------------------------------------
def test_open_buy_usa_ask_e_restituisce_valori_reali(executor, broker):
    esito = executor.execute_open(piano("BUY", sl=4370.0, tp=4390.0, entry=4379.0))

    assert esito["success"] is True
    assert esito["error"] is None
    assert esito["fill_price"] == broker.ask  # prezzo reale, non l'entry del segnale
    assert esito["volume"] == 0.10
    pos = broker.positions[esito["mt5_ticket"]]
    assert pos.type == F.ORDER_TYPE_BUY
    assert (pos.sl, pos.tp) == (4370.0, 4390.0)


def test_open_sell_usa_bid(executor, broker):
    esito = executor.execute_open(piano("SELL", sl=4390.0, tp=4370.0))

    assert esito["success"] is True
    assert esito["fill_price"] == broker.bid
    assert broker.positions[esito["mt5_ticket"]].type == F.ORDER_TYPE_SELL


def test_open_senza_tp_invia_tp_zero(executor, broker):
    executor.execute_open(piano("BUY", sl=4370.0, tp=None))
    assert broker.requests[-1]["tp"] == 0.0


def test_open_rifiutato_dal_broker(executor, broker):
    broker.force_retcode = F.TRADE_RETCODE_INVALID_STOPS
    esito = executor.execute_open(piano("BUY", sl=4370.0))

    assert esito == {"success": False, "mt5_ticket": None, "fill_price": None, "volume": None, "error": "Forced retcode 10016"}
    assert broker.positions == {}


def test_open_senza_prezzo_non_invia_nulla(executor, broker):
    broker.tick_available = False
    esito = executor.execute_open(piano("BUY", sl=4370.0))

    assert esito["success"] is False
    assert broker.requests == []


@pytest.mark.xfail(strict=True, reason="BUG: se order_send ritorna None (richiesta malformata o connessione "
                                       "persa) execute_open va in AttributeError invece di restituire un esito")
def test_open_order_send_none_non_esplode(executor, broker):
    broker.force_none = True
    esito = executor.execute_open(piano("BUY", sl=4370.0))
    assert esito["success"] is False


# ----------------------------------------------------------------------
# set_sl_to_be
# ----------------------------------------------------------------------
def test_be_sposta_sl_e_mantiene_tp(executor, broker):
    ticket = executor.execute_open(piano("BUY", sl=4370.0, tp=4395.0))["mt5_ticket"]
    broker.move_price(4388.0)  # il trade è in profitto: BE valido

    assert executor.set_sl_to_be(ticket, 4380.2) is True
    pos = broker.positions[ticket]
    assert (pos.sl, pos.tp) == (4380.2, 4395.0)


def test_be_posizione_gia_chiusa_ritorna_false(executor, broker):
    assert executor.set_sl_to_be(99999999, 4380.0) is False
    assert broker.requests == []


def test_be_senza_entry_price(executor, broker):
    assert executor.set_sl_to_be(123, None) is False


def test_be_rifiutato_se_il_prezzo_non_e_in_profitto(executor, broker):
    """Il broker rifiuta uno SL sopra il prezzo corrente di una BUY (Invalid stops)."""
    ticket = executor.execute_open(piano("BUY", sl=4370.0))["mt5_ticket"]
    broker.move_price(4375.0)  # in perdita: lo SL a 4380.2 sarebbe sopra il bid

    assert executor.set_sl_to_be(ticket, 4380.2) is False
    assert broker.positions[ticket].sl == 4370.0  # invariato
    assert ticket in broker.positions  # la posizione è ANCORA aperta


# ----------------------------------------------------------------------
# close_position
# ----------------------------------------------------------------------
def test_close_buy_con_sell_al_bid(executor, broker):
    ticket = executor.execute_open(piano("BUY", sl=4370.0))["mt5_ticket"]

    assert executor.close_position(ticket, "XAUUSD") is True
    assert ticket not in broker.positions
    req = broker.requests[-1]
    assert (req["type"], req["price"], req["position"]) == (F.ORDER_TYPE_SELL, broker.bid, ticket)


def test_close_sell_con_buy_all_ask(executor, broker):
    ticket = executor.execute_open(piano("SELL", sl=4390.0))["mt5_ticket"]

    assert executor.close_position(ticket) is True
    assert broker.requests[-1]["type"] == F.ORDER_TYPE_BUY
    assert broker.requests[-1]["price"] == broker.ask


def test_close_posizione_inesistente_e_considerata_chiusa(executor, broker):
    assert executor.close_position(99999999) is True
    assert broker.requests == []


def test_close_rifiutata_ritorna_false(executor, broker):
    ticket = executor.execute_open(piano("BUY", sl=4370.0))["mt5_ticket"]
    broker.force_retcode = F.TRADE_RETCODE_INVALID

    assert executor.close_position(ticket) is False


# ----------------------------------------------------------------------
# modify_order_levels
# ----------------------------------------------------------------------
def test_modify_imposta_sl_e_primo_tp(executor, broker):
    ticket = executor.execute_open(piano("SELL", sl=4386.0))["mt5_ticket"]

    assert executor.modify_order_levels(ticket, 4390.0, [4370.0, 4360.0]) is True
    pos = broker.positions[ticket]
    assert (pos.sl, pos.tp) == (4390.0, 4370.0)


def test_modify_rifiutato_ritorna_false(executor, broker):
    ticket = executor.execute_open(piano("SELL", sl=4386.0))["mt5_ticket"]
    assert executor.modify_order_levels(ticket, 4370.0, []) is False  # SL sotto il prezzo per una SELL


def test_modify_con_sl_none_rimuove_lo_stop_loss(executor, broker):
    """Comportamento attuale da conoscere: stop_loss=None viene inviato come 0.0 = NESSUNO SL."""
    ticket = executor.execute_open(piano("SELL", sl=4386.0))["mt5_ticket"]
    executor.modify_order_levels(ticket, None, [4370.0])
    assert broker.positions[ticket].sl == 0.0


# ----------------------------------------------------------------------
# get_open_position
# ----------------------------------------------------------------------
def test_get_open_position(executor, broker):
    ticket = executor.execute_open(piano("BUY", sl=4370.0, tp=4390.0))["mt5_ticket"]

    stato = executor.get_open_position(ticket)
    assert stato == {"ticket": ticket, "symbol": "XAUUSD", "volume": 0.10, "price_open": broker.ask,
                     "stop_loss": 4370.0, "take_profit": 4390.0}
    assert executor.get_open_position(99999999) is None


# ----------------------------------------------------------------------
# is_symbol_tradable
# ----------------------------------------------------------------------
def test_tradable_con_tick_recente(executor, broker):
    assert executor.is_symbol_tradable("XAUUSD")[0] is True


def test_non_tradable_se_trading_disabilitato(executor, broker):
    broker.trade_mode = F.SYMBOL_TRADE_MODE_DISABLED
    assert executor.is_symbol_tradable("XAUUSD")[0] is False


def test_non_tradable_se_tick_vecchio(executor, broker):
    broker.tick_time = int(time.time()) - 600
    ok, motivo = executor.is_symbol_tradable("XAUUSD")
    assert ok is False and "nessun tick" in motivo


def test_tradable_se_dati_non_disponibili(executor, broker):
    broker.symbol_known = False
    assert executor.is_symbol_tradable("XAUUSD")[0] is True
