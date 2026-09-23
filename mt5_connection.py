"""
Connessione RPyC al server mt5server.exe che gira sotto Wine (vedi
DEPLOY_LOG.md, Fase 3). Espone `mt5`, il modulo MetaTrader5 remoto, cosi'
il resto del codice puo' usarlo esattamente come il pacchetto MetaTrader5
originale (mt5.initialize(), mt5.symbol_info_tick(), ecc.) senza altre
modifiche.

La connessione si riapre da sola: se mt5server.exe viene riavviato o il
collegamento cade, una connessione aperta una volta sola all'avvio resterebbe
chiusa per sempre (EOFError a ogni chiamata) finché qualcuno non riavvia il
bot. Qui ogni accesso a `mt5` controlla che la connessione sia viva e, se non
lo è, ne apre una nuova.
"""
import os

import rpyc

from logger_config import setup_logger

logger = setup_logger(__name__)

MT5_RPYC_HOST = os.getenv("MT5_RPYC_HOST", "localhost")
MT5_RPYC_PORT = int(os.getenv("MT5_RPYC_PORT", "18812"))


class _RemoteMT5:
    def __init__(self):
        self._conn = None
        self._module = None

    def _ensure_connected(self):
        if self._conn is None or self._conn.closed:
            reconnect = self._conn is not None
            self._conn = rpyc.classic.connect(MT5_RPYC_HOST, MT5_RPYC_PORT)
            self._module = self._conn.modules["MetaTrader5"]
            if reconnect:
                logger.warning(f"🔌 Connessione RPyC a mt5server riaperta ({MT5_RPYC_HOST}:{MT5_RPYC_PORT}).")
        return self._module

    def __getattr__(self, name):
        return getattr(self._ensure_connected(), name)


mt5 = _RemoteMT5()
# Connessione subito all'avvio: se mt5server non è raggiungibile il bot si
# ferma qui con un errore chiaro, come prima.
mt5._ensure_connected()
