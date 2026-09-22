"""
Connessione RPyC al server mt5server.exe che gira sotto Wine (vedi
DEPLOY_LOG.md, Fase 3). Espone `mt5`, il modulo MetaTrader5 remoto, cosi'
il resto del codice puo' usarlo esattamente come il pacchetto MetaTrader5
originale (mt5.initialize(), mt5.symbol_info_tick(), ecc.) senza altre
modifiche.
"""
import os

import rpyc

MT5_RPYC_HOST = os.getenv("MT5_RPYC_HOST", "localhost")
MT5_RPYC_PORT = int(os.getenv("MT5_RPYC_PORT", "18812"))

_conn = rpyc.classic.connect(MT5_RPYC_HOST, MT5_RPYC_PORT)
mt5 = _conn.modules["MetaTrader5"]
