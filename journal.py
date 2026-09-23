"""
Diario strutturato delle operazioni: logs/journal_AAAA-MM-GG.jsonl

Mentre bot.log è pensato per essere letto a occhio, il diario registra ogni
evento rilevante come una riga JSON (un evento per riga, in append), così lo
storico di una giornata si può ricostruire e analizzare anche a posteriori,
quando il bot ha girato senza nessuno davanti al PC (vedi report.py).

Ogni evento ha: ts (ora locale con fuso), event (tipo) e i campi specifici.
Gli eventi generati mentre si elabora un messaggio Telegram portano anche il
suo msg_id, così si può seguire l'intera catena messaggio -> classificazione
-> decisione -> ordini MT5 -> esito.

Tipi di evento principali:
  BOT_START / BOT_STOP       avvio e arresto del listener
  MESSAGE                    messaggio ricevuto (testo, edit, reply)
  MESSAGE_SKIPPED            scartato prima della classificazione (motivo)
  CLASSIFIED                 output dell'agente classificatore
  CLASSIFIER_CALL            token consumati dalla chiamata all'agente
  DECISION                   esito dell'Order Manager (OPEN/UPDATE/CLOSE/IGNORE)
  RISK_CALC                  calcolo dei lotti (rischio, tetto sul margine, totale)
  RISK_REJECTED              segnale scartato dal Risk Manager
  MT5_ORDER                  ogni richiesta inviata a MT5 con il suo esito
  TRADE_STATUS               stato finale di un'operazione dopo un'azione
  LEVELS_INCOHERENT          SL/TP incoerenti con la direzione, non inviati
  MEMORY_RESYNC              memoria riallineata ai valori reali del broker
  BE_PENDING                 BE rifiutato (prezzo troppo vicino), resta in attesa
  BE_PENDING_APPLIED         BE in attesa applicato dal controllo periodico
  PARTIAL_CLOSE_PLAN         chiusura parziale richiesta: percentuale e lotti da chiudere
  POSITION_PARTIAL_CLOSED    posizione chiusa solo in parte (volume chiuso e residuo)
  POSITION_CLOSED            posizione chiusa (dal bot o dal broker: TP/SL/manuale)
  MT5_CONNECTION             connessione a MT5 persa / ripristinata
  HEARTBEAT                  battito periodico: bot vivo, saldo, operazioni
  ERROR                      eccezione con traceback
"""

import contextvars
import json
import os
from datetime import datetime

from logger_config import LOG_DIR, setup_logger

logger = setup_logger(__name__)

# msg_id del messaggio Telegram in elaborazione: viene aggiunto in automatico
# a ogni evento registrato durante la sua gestione (anche dentro MT5Executor).
_current_msg_id = contextvars.ContextVar("journal_msg_id", default=None)


def set_current_message(msg_id):
    """Da chiamare all'inizio dell'elaborazione di un messaggio; ritorna il token per reset."""
    return _current_msg_id.set(msg_id)


def clear_current_message(token):
    _current_msg_id.reset(token)


def journal_path(day: str) -> str:
    return os.path.join(LOG_DIR, f"journal_{day}.jsonl")


def record(event: str, **fields) -> None:
    """
    Aggiunge un evento al diario del giorno. Non solleva mai eccezioni: un
    problema di scrittura del diario non deve bloccare la gestione degli ordini.
    """
    now = datetime.now().astimezone()
    entry = {"ts": now.isoformat(timespec="seconds"), "event": event}

    msg_id = _current_msg_id.get()
    if msg_id is not None and "msg_id" not in fields:
        entry["msg_id"] = msg_id
    entry.update(fields)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(journal_path(now.strftime("%Y-%m-%d")), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception(f"⚠️ Impossibile scrivere l'evento {event} nel diario")
