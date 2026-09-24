"""
Configurazione centralizzata dei log.

Sostituisce i print sparsi nei moduli: quando il bot gira da solo servono log
persistenti per ricostruire cosa è successo, soprattutto in caso di errore.

Scrive contemporaneamente su:
  - console (come prima, per seguire il bot durante i test)
  - una cartella per giorno, logs/AAAA-MM-GG/, con dentro:
      bot.log        tutto
      errors.log     solo avvisi ed errori, per trovare subito cosa è andato storto
      journal.jsonl  il diario strutturato delle operazioni (vedi journal.py)
    Le cartelle più vecchie di LOG_RETENTION_DAYS giorni vengono cancellate.

Tutti gli orari (righe di log, diario, cambio di cartella a mezzanotte) sono
nel fuso LOG_TIMEZONE (default Europe/Rome), indipendentemente dal fuso del
sistema: il server gira in UTC e i log risultavano 2 ore indietro rispetto
agli orari dei messaggi su Telegram.
"""

import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOG_DIR = "logs"
LOG_FILE = "bot.log"
ERROR_LOG_FILE = "errors.log"
LOG_RETENTION_DAYS = 30

# Logger padre condiviso: i moduli ne ottengono un figlio (trade_bot.<modulo>)
# che propaga qui. Gli handler su file esistono così una volta sola.
ROOT_LOGGER_NAME = "trade_bot"

LOG_TIMEZONE_NAME = os.getenv("LOG_TIMEZONE", "Europe/Rome")
try:
    LOG_TZ = ZoneInfo(LOG_TIMEZONE_NAME)
except Exception:
    # Su Windows il database dei fusi arriva dal pacchetto 'tzdata': senza,
    # si ripiega sull'ora locale del sistema.
    LOG_TZ = None


def now_local() -> datetime:
    """Adesso, nel fuso dei log (con l'offset, es. +02:00)."""
    return datetime.now(LOG_TZ) if LOG_TZ else datetime.now().astimezone()


def to_local(value) -> datetime:
    """Converte un timestamp Unix o un datetime con fuso nel fuso dei log."""
    if isinstance(value, datetime):
        return value.astimezone(LOG_TZ) if LOG_TZ else value.astimezone()
    return datetime.fromtimestamp(value, LOG_TZ) if LOG_TZ else datetime.fromtimestamp(value).astimezone()


class LocalTimeFormatter(logging.Formatter):
    """Formatter che scrive l'orario nel fuso dei log invece che in quello del sistema."""

    def formatTime(self, record, datefmt=None):
        return to_local(record.created).strftime(datefmt or "%Y-%m-%d %H:%M:%S")


_DAY_FOLDER = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def day_folder(day: str) -> str:
    """Cartella dei log di un giorno (AAAA-MM-GG), creata se non esiste."""
    folder = os.path.join(LOG_DIR, day)
    os.makedirs(folder, exist_ok=True)
    return folder


def remove_old_log_folders() -> None:
    """Cancella le cartelle giornaliere più vecchie di LOG_RETENTION_DAYS."""
    if not os.path.isdir(LOG_DIR):
        return
    limit = (now_local() - timedelta(days=LOG_RETENTION_DAYS)).strftime("%Y-%m-%d")
    for name in os.listdir(LOG_DIR):
        if _DAY_FOLDER.match(name) and name < limit:
            shutil.rmtree(os.path.join(LOG_DIR, name), ignore_errors=True)


class DailyFolderFileHandler(logging.Handler):
    """
    Scrive in logs/<giorno>/<filename> e passa alla cartella del giorno nuovo
    alla prima riga dopo mezzanotte (ora locale). Non rinomina mai file, quindi
    funziona anche su Windows, dove rinominare un file aperto fallisce.
    """

    def __init__(self, filename: str, level: int, formatter: logging.Formatter):
        super().__init__(level)
        self.filename = filename
        self.setFormatter(formatter)
        self._day = None
        self._stream = None

    def _open_day(self, day: str) -> None:
        if self._stream:
            self._stream.close()
        self._stream = open(os.path.join(day_folder(day), self.filename), "a", encoding="utf-8")
        self._day = day
        remove_old_log_folders()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = to_local(record.created).strftime("%Y-%m-%d")
            if day != self._day:
                self._open_day(day)
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream:
            self._stream.close()
            self._stream = None
        super().close()


def _configure_root_logger() -> logging.Logger:
    root = logging.getLogger(ROOT_LOGGER_NAME)
    if root.handlers:
        return root

    root.setLevel(logging.INFO)
    # Evita che i messaggi risalgano al root logger di Python e vengano stampati due volte
    root.propagate = False

    # %(module)s mostra il nome del file (es. telegram_listener anche quando è
    # avviato come script, dove il nome del logger sarebbe __main__)
    formatter = LocalTimeFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(module)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    root.addHandler(DailyFolderFileHandler(LOG_FILE, logging.INFO, formatter))
    root.addHandler(DailyFolderFileHandler(ERROR_LOG_FILE, logging.WARNING, formatter))

    return root


def setup_logger(name: str = ROOT_LOGGER_NAME) -> logging.Logger:
    """
    Ritorna il logger del modulo chiamante, configurando gli handler condivisi
    alla prima chiamata. Le chiamate successive non duplicano gli handler
    (e quindi le righe).
    """
    root = _configure_root_logger()
    if name == ROOT_LOGGER_NAME:
        return root
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
