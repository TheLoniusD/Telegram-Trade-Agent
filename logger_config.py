"""
Configurazione centralizzata dei log.

Sostituisce i print sparsi nei moduli: quando il bot gira da solo servono log
persistenti per ricostruire cosa è successo, soprattutto in caso di errore.

Scrive contemporaneamente su:
  - console (come prima, per seguire il bot durante i test)
  - file giornaliero in logs/, ruotato a mezzanotte e conservato per N giorni
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = "logs"
LOG_FILE = "bot.log"
LOG_RETENTION_DAYS = 30


def setup_logger(name: str = "trade_bot") -> logging.Logger:
    """
    Ritorna il logger condiviso dell'applicazione, configurandolo alla prima
    chiamata. Le chiamate successive riusano la stessa istanza, così i moduli
    possono invocarla liberamente senza duplicare gli handler (e quindi le righe).
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    # Evita che i messaggi risalgano al root logger e vengano stampati due volte
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(LOG_DIR, LOG_FILE),
        when="midnight",
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
    )
    # I file ruotati diventano bot.log.2026-09-14
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
