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
"""

import logging
import os
import re
import shutil
from datetime import datetime, timedelta

LOG_DIR = "logs"
LOG_FILE = "bot.log"
ERROR_LOG_FILE = "errors.log"
LOG_RETENTION_DAYS = 30

# Logger padre condiviso: i moduli ne ottengono un figlio (trade_bot.<modulo>)
# che propaga qui. Gli handler su file esistono così una volta sola.
ROOT_LOGGER_NAME = "trade_bot"

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
    limit = (datetime.now() - timedelta(days=LOG_RETENTION_DAYS)).strftime("%Y-%m-%d")
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
            day = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d")
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
    formatter = logging.Formatter(
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
