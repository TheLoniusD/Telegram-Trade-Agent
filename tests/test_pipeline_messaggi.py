"""
Test end-to-end: messaggi Telegram fittizi -> process_message -> OrderManager
-> RiskManager -> MT5Executor -> finto MT5.

La classificazione di ogni messaggio è scritta a mano in tests/scenari.py:
l'agente non viene mai chiamato. I test marcati xfail documentano bug reali
trovati con questi scenari: quando verranno corretti, pytest segnalerà
XPASS (strict) e basterà togliere il marcatore.
"""
import glob
import json

import pytest

from tests import fake_mt5 as F
from tests.scenari import ciclo_completo_sell, classificazione, messaggi_da_ignorare, passo, reentry_sell, segnale_completo_buy

P = 4380.0  # prezzo di riferimento (bid iniziale del finto broker)


def posizioni(broker):
    return sorted(broker.positions.values(), key=lambda p: p.ticket)


# ----------------------------------------------------------------------
# Ciclo completo SELL: fase rapida -> edit -> BE+ -> HIT TP MAX
# ----------------------------------------------------------------------
def test_fase_rapida_apre_due_sell_con_sl_temporaneo(pipeline):
    pipeline.send(ciclo_completo_sell(P)[0])

    pos = posizioni(pipeline.broker)
    assert len(pos) == 2
    for p in pos:
        assert p.type == F.ORDER_TYPE_SELL
        assert p.sl == P + 5.0  # DEFAULT_SL_DIST_GOLD sopra il prezzo di ingresso
        assert p.tp == 0.0
    trade = pipeline.trade(1001)
    assert trade["status"] == "ACTIVE"
    assert {t["mt5_ticket"] for t in trade["tickets"].values()} == {p.ticket for p in pos}
    assert all(t["entry_price"] == P for t in trade["tickets"].values())  # fill reale del broker


@pytest.mark.xfail(strict=True, reason="BUG: lo SL temporaneo calcolato dal RiskManager viene inviato a MT5 ma non "
                                       "scritto in memoria (resta None): memoria e broker non sono allineati")
def test_fase_rapida_memoria_conosce_lo_sl_temporaneo(pipeline):
    pipeline.send(ciclo_completo_sell(P)[0])
    for key in ("tp1", "tp2"):
        assert pipeline.trade(1001)["tickets"][key]["stop_loss"] == pipeline.posizione(1001, key).sl


def test_edit_fase_completa_imposta_sl_e_tp(pipeline):
    rapida, completa, *_ = ciclo_completo_sell(P)
    pipeline.send(rapida)
    pipeline.send(completa)

    assert (pipeline.posizione(1001, "tp1").sl, pipeline.posizione(1001, "tp1").tp) == (P + 10, P - 5)
    assert (pipeline.posizione(1001, "tp2").sl, pipeline.posizione(1001, "tp2").tp) == (P + 10, P - 10)


def test_be_con_prezzo_in_profitto_e_poi_hit_tp_max(pipeline, tmp_path):
    rapida, completa, be, chiusura = ciclo_completo_sell(P)
    pipeline.send(rapida)
    pipeline.send(completa)

    pipeline.broker.move_price(P - 3)  # SELL in profitto, TP non ancora toccati
    pipeline.send(be)
    for key in ("tp1", "tp2"):
        assert pipeline.posizione(1001, key).sl == P  # SL al prezzo di ingresso
        assert pipeline.trade(1001)["tickets"][key]["be_active"] is True

    pipeline.send(chiusura)
    assert pipeline.broker.positions == {}
    assert 1001 not in pipeline.manager.active_trades  # archiviato
    archivio = [json.loads(r) for f in glob.glob(str(tmp_path / "storico" / "*.jsonl")) for r in open(f)]
    assert [t["status"] for t in archivio] == ["CLOSED"]


@pytest.mark.xfail(strict=True, reason="BUG: se il broker rifiuta il BE (es. prezzo non ancora in profitto), il "
                                       "listener segna il ticket 'closed' come se il TP fosse scattato; la "
                                       "successiva chiusura lo salta e la posizione resta ORFANA su MT5")
def test_be_rifiutato_non_fa_perdere_le_posizioni(pipeline):
    rapida, completa, be, chiusura = ciclo_completo_sell(P)
    pipeline.send(rapida)
    pipeline.send(completa)
    pipeline.send(be)        # prezzo fermo all'ingresso: il broker rifiuta (Invalid stops)
    pipeline.send(chiusura)  # HIT TP MAX

    assert pipeline.broker.positions == {}, "posizioni ancora aperte su MT5 ma segnate CLOSED in memoria"


# ----------------------------------------------------------------------
# Segnale BUY completo
# ----------------------------------------------------------------------
@pytest.mark.xfail(strict=True, reason="BUG CRITICO: RiskManager legge direction/stop_loss/take_profit dalla radice "
                                       "del trade, dove non esistono (stanno in tickets): ogni BUY viene aperto come "
                                       "SELL, con SL temporaneo e senza TP")
def test_segnale_buy_apre_due_buy_con_sl_e_tp_del_messaggio(pipeline):
    pipeline.send(segnale_completo_buy(P)[0])

    tp1, tp2 = pipeline.posizione(2001, "tp1"), pipeline.posizione(2001, "tp2")
    assert tp1.type == tp2.type == F.ORDER_TYPE_BUY
    assert (tp1.sl, tp1.tp) == (P - 10, P + 5)
    assert (tp2.sl, tp2.tp) == (P - 10, P + 10)


def test_close_manuale_chiude_tutto(pipeline):
    apertura, _, chiusura = segnale_completo_buy(P)
    pipeline.send(apertura)
    pipeline.send(chiusura)

    assert pipeline.broker.positions == {}
    assert 2001 not in pipeline.manager.active_trades


# ----------------------------------------------------------------------
# Aggiornamenti SL/TP
# ----------------------------------------------------------------------
@pytest.mark.xfail(strict=True, reason="BUG: un update con soli TP (dopo la fase rapida) invia sl=0.0 a MT5, "
                                       "RIMUOVENDO lo stop loss temporaneo dalla posizione")
def test_update_solo_tp_non_rimuove_lo_sl(pipeline):
    pipeline.send(ciclo_completo_sell(P)[0])
    pipeline.send(passo(1001, "TP✅4370\nTP✅4360",
                        classificazione("UPDATE_SIGNAL", direction="SELL", take_profit=[P - 10, P - 20]),
                        is_edit=True))

    assert pipeline.posizione(1001, "tp1").sl == P + 5


@pytest.mark.xfail(strict=True, reason="BUG: l'esito di modify_order_levels viene ignorato: se MT5 rifiuta il "
                                       "nuovo SL, la memoria riporta comunque il valore mai applicato")
def test_modifica_rifiutata_non_altera_la_memoria(pipeline):
    rapida, completa, *_ = ciclo_completo_sell(P)
    pipeline.send(rapida)
    pipeline.send(completa)
    # SL sotto il prezzo per una SELL: il broker lo rifiuta
    pipeline.send(passo(1004, f"Cut loss if solid break {P - 2}",
                        classificazione("UPDATE_SIGNAL", stop_loss=P - 2), reply_to=1001))

    for key in ("tp1", "tp2"):
        assert pipeline.trade(1001)["tickets"][key]["stop_loss"] == pipeline.posizione(1001, key).sl


# ----------------------------------------------------------------------
# Re-entry
# ----------------------------------------------------------------------
def test_reentry_apre_altre_due_e_la_chiusura_prende_tutta_la_catena(pipeline):
    originale, reentry, chiusura = reentry_sell(P)
    pipeline.send(originale)
    pipeline.send(reentry)

    assert len(pipeline.broker.positions) == 4
    assert pipeline.trade(3002)["root_msg_id"] == 3001

    pipeline.send(chiusura)
    assert pipeline.broker.positions == {}
    assert pipeline.manager.active_trades == {}


# ----------------------------------------------------------------------
# Rifiuti, filtri e messaggi da ignorare
# ----------------------------------------------------------------------
def test_apertura_rifiutata_dal_broker(pipeline):
    pipeline.broker.force_retcode = F.TRADE_RETCODE_INVALID_STOPS
    pipeline.send(ciclo_completo_sell(P)[0])

    trade = pipeline.trade(1001)
    assert trade["status"] == "PENDING_FAILED"
    assert all(t["mt5_ticket"] is None and t["success"] is False for t in trade["tickets"].values())


def test_messaggi_da_ignorare_non_toccano_mt5(pipeline):
    for step in messaggi_da_ignorare(P):
        pipeline.send(step)

    assert len(pipeline.classifier.calls) == 3
    assert pipeline.broker.requests == []
    assert pipeline.manager.active_trades == {}


def test_mercato_chiuso_non_chiama_l_agente(pipeline):
    pipeline.broker.tick_time = 1  # ultimo tick vecchissimo
    pipeline.send(ciclo_completo_sell(P)[0])

    assert pipeline.classifier.calls == []
    assert pipeline.broker.requests == []


def test_messaggio_di_altro_utente_scartato(pipeline):
    import telegram_listener
    from tests.harness import FakeEvent
    import asyncio

    evento = FakeEvent(9001, "GOLD SELL NOW", sender_id=telegram_listener.OWNER_ID + 1)
    asyncio.run(telegram_listener.process_message(evento, is_edit=False))

    assert pipeline.classifier.calls == []
