"""
Utility condivise tra i test offline (pytest + finto MT5) e lo script live
sul server (MT5 reale via RPyC).

- install_stubs(): sostituisce telethon e agent_classifier con moduli finti,
  così importare telegram_listener non apre la sessione Telegram e non crea
  il client Anthropic. Nessun token viene MAI consumato.
- FakeEvent: evento Telethon minimale, con gli stessi attributi letti da
  process_message.
- ScriptedClassifier: al posto dell'agente, restituisce la classificazione
  già scritta nello scenario per quel messaggio.
"""
import sys
import types
from datetime import datetime, timezone


def install_stubs():
    """Da chiamare PRIMA di importare telegram_listener."""
    # --- telethon finto: TelegramClient non si connette e i decoratori sono no-op
    telethon = types.ModuleType("telethon")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def on(self, *args, **kwargs):
            return lambda func: func

        def start(self, *args, **kwargs):
            raise RuntimeError("TelegramClient finto: non avviabile nei test")

        def run_until_disconnected(self):
            pass

    events = types.SimpleNamespace(NewMessage=lambda **kw: None, MessageEdited=lambda **kw: None)
    telethon.TelegramClient = _Client
    telethon.events = events
    sys.modules["telethon"] = telethon

    # --- agente classificatore finto: se venisse chiamato per errore, esplode
    classifier = types.ModuleType("agent_classifier")

    def _no_tokens(*args, **kwargs):
        raise RuntimeError("Agente classificatore chiamato durante i test: i token non vanno usati!")

    classifier.agent_classify_telegram_message = _no_tokens
    sys.modules["agent_classifier"] = classifier


class FakeEvent:
    """Riproduce gli attributi di un evento Telethon letti da process_message."""

    def __init__(self, msg_id, text, sender_id, reply_to=None, has_media=False, forwarded=False):
        self.id = msg_id
        self.raw_text = text
        self.sender_id = sender_id
        self.reply_to_msg_id = reply_to
        self.media = object() if has_media else None
        self.forward = object() if forwarded else None
        self.date = datetime.now(timezone.utc)


class ScriptedClassifier:
    """
    Sostituisce agent_classify_telegram_message: restituisce la classificazione
    del passo corrente dello scenario e registra ogni chiamata.
    """

    def __init__(self):
        self.next_output = None
        self.calls = []

    def __call__(self, message_text, is_edit, reply_to, has_media, is_forwarded, timestamp):
        self.calls.append({"text": message_text, "is_edit": is_edit, "reply_to": reply_to})
        if self.next_output is None:
            raise RuntimeError(f"Nessuna classificazione preparata per: {message_text!r}")
        output, self.next_output = self.next_output, None
        return output
