"""
Configurazione centralizzata dei log.

Sostituisce i print sparsi nei moduli: quando il bot gira da solo servono log
persistenti per ricostruire cosa è successo, soprattutto in caso di errore.

Scrive contemporaneamente su:
  - console (come prima, per seguire il bot durante i test)
  - logs/bot.log: tutto, ruotato a mezzanotte e conservato per N giorni
  - logs/errors.log: solo avvisi ed errori, per trovare subito cosa è andato storto

Il diario strutturato delle operazioni (logs/journal_*.jsonl) è in journal.py.
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = "logs"
LOG_FILE = "bot.log"
ERROR_LOG_FILE = "errors.log"
LOG_RETENTION_DAYS = 30

# Logger padre condiviso: i moduli ne ottengono un figlio (trade_bot.<modulo>)
# che propaga qui. Gli handler su file esistono così una volta sola: con un
# handler per modulo sullo stesso file, su Windows la rotazione di mezzanotte
# fallisce perché il file è ancora aperto dagli altri handler.
ROOT_LOGGER_NAME = "trade_bot"


def _rotating_file_handler(filename: str, level: int, formatter: logging.Formatter) -> logging.Handler:
    handler = TimedRotatingFileHandler(
        filename=os.path.join(LOG_DIR, filename),
        when="midnight",
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
    )
    # I file ruotati diventano bot.log.2026-09-14
    handler.suffix = "%Y-%m-%d"
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


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

    os.makedirs(LOG_DIR, exist_ok=True)
    root.addHandler(_rotating_file_handler(LOG_FILE, logging.INFO, formatter))
    root.addHandler(_rotating_file_handler(ERROR_LOG_FILE, logging.WARNING, formatter))

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
