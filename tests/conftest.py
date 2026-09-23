"""
Configurazione dei test offline: nessun broker, nessun token, nessun Telegram.

Prima di importare i moduli del bot:
  - ci spostiamo in una cartella temporanea, così logs/, active_trades.json e
    storico/ dei test non sporcano quelli veri del progetto;
  - registriamo il finto MT5 al posto di mt5_connection (niente RPyC);
  - sostituiamo telethon e agent_classifier con moduli finti.
"""
import asyncio
import os
import sys
import tempfile
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(tempfile.mkdtemp(prefix="tta_tests_"))

from tests.fake_mt5 import mt5 as fake_mt5  # noqa: E402
from tests.harness import FakeEvent, ScriptedClassifier, install_stubs  # noqa: E402

_connection = types.ModuleType("mt5_connection")
_connection.mt5 = fake_mt5
sys.modules["mt5_connection"] = _connection
install_stubs()

import telegram_listener  # noqa: E402
from order_manager import OrderManager  # noqa: E402


@pytest.fixture
def broker():
    fake_mt5.reset()
    return fake_mt5


@pytest.fixture
def executor(broker):
    from mt5_executor import MT5Executor
    ex = MT5Executor()
    assert not ex.test_mode, "TEST_MODE deve essere False: vogliamo esercitare il codice reale contro il finto MT5"
    return ex


class Pipeline:
    """Invia messaggi fittizi a telegram_listener.process_message, come farebbe Telethon."""

    def __init__(self, broker, tmp_path, monkeypatch):
        self.broker = broker
        self.classifier = ScriptedClassifier()
        self.manager = OrderManager(storage_path=str(tmp_path / "active_trades.json"),
                                    archive_dir=str(tmp_path / "storico"))
        monkeypatch.setattr(telegram_listener, "manager", self.manager)
        monkeypatch.setattr(telegram_listener, "agent_classify_telegram_message", self.classifier)
        # Il calendario statico usa l'orologio reale: nei test il mercato è sempre aperto
        monkeypatch.setattr(telegram_listener, "is_market_time_open", lambda: (True, "test"))

    def send(self, step):
        self.classifier.next_output = step["output"]
        event = FakeEvent(step["msg_id"], step["text"], telegram_listener.OWNER_ID, reply_to=step["reply_to"])
        asyncio.run(telegram_listener.process_message(event, is_edit=step["is_edit"]))

    def trade(self, msg_id):
        return self.manager.active_trades.get(msg_id)

    def ticket_mt5(self, msg_id, key):
        return self.trade(msg_id)["tickets"][key]["mt5_ticket"]

    def posizione(self, msg_id, key):
        return self.broker.positions.get(self.ticket_mt5(msg_id, key))


@pytest.fixture
def pipeline(broker, tmp_path, monkeypatch):
    return Pipeline(broker, tmp_path, monkeypatch)
