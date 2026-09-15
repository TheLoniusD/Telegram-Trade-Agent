"""
Bridge HTTP per MetaTrader5, da eseguire SOLO sulla VM/PC Windows dove il
terminale MT5 e il pacchetto ufficiale MetaTrader5 sono installati nativamente.

Perché esiste: il bot (Telegram listener, classificatore, order manager) gira
sul laptop Linux always-on, ma il pacchetto Python "MetaTrader5" è Windows-only
e non esiste un modo affidabile di far girare il terminale MT5 sotto Wine (vedi
doc/PLAN_B_WINDOWS_BRIDGE.md per il perché). Questo script espone via HTTP le
stesse operazioni che mt5_executor.py farebbe in locale, così il lato Linux
può restare invariato nella logica e limitarsi a chiamare questo servizio.

Deploy: copiare SOLO questo file (+ requirements.txt di questa cartella) sulla
VM Windows, installare le dipendenze e avviarlo all'accensione insieme al
terminale MT5. Nessuna altra parte del repository serve lato Windows.

Sicurezza: questo servizio può aprire/chiudere posizioni reali sul conto
collegato al terminale MT5 = è equivalente ad accesso root sul conto di
trading. Va tenuto raggiungibile SOLO dalla rete virtuale privata tra host
Linux e VM (mai esposto su LAN/Internet) e protetto con un token condiviso
(MT5_BRIDGE_TOKEN), verificato su ogni richiesta.
"""

import logging
import os
import time
from logging.handlers import TimedRotatingFileHandler

import MetaTrader5 as mt5
from flask import Flask, jsonify, request

LOG_DIR = "logs"
BRIDGE_TOKEN = os.getenv("MT5_BRIDGE_TOKEN", "")
BRIDGE_HOST = os.getenv("MT5_BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = int(os.getenv("MT5_BRIDGE_PORT", "8765"))

os.makedirs(LOG_DIR, exist_ok=True)
logger = logging.getLogger("mt5_bridge")
logger.setLevel(logging.INFO)
_formatter = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
_console = logging.StreamHandler()
_console.setFormatter(_formatter)
logger.addHandler(_console)
_file = TimedRotatingFileHandler(
    filename=os.path.join(LOG_DIR, "bridge.log"), when="midnight", backupCount=30, encoding="utf-8"
)
_file.suffix = "%Y-%m-%d"
_file.setFormatter(_formatter)
logger.addHandler(_file)

app = Flask(__name__)


@app.before_request
def check_token():
    if not BRIDGE_TOKEN:
        # Nessun token configurato: rifiutiamo tutto per non finire mai
        # per errore con un bridge aperto a chiunque raggiunga la porta.
        logger.error("MT5_BRIDGE_TOKEN non impostato: richiesta rifiutata.")
        return jsonify({"error": "bridge non configurato (token mancante)"}), 503

    if request.headers.get("X-Bridge-Token") != BRIDGE_TOKEN:
        logger.warning(f"Token non valido da {request.remote_addr} su {request.path}")
        return jsonify({"error": "token non valido"}), 401


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "mt5_initialized": mt5.terminal_info() is not None})


@app.route("/execute_open", methods=["POST"])
def execute_open():
    order_plan = request.get_json(force=True)
    symbol = order_plan["symbol"]
    direction = order_plan["direction"]
    volume = order_plan["volume"]
    sl = order_plan["stop_loss"]
    tp_value = order_plan["take_profit"]

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Nessun prezzo disponibile per {symbol}: ordine non inviato.")
        return jsonify({"success": False, "mt5_ticket": None, "fill_price": None, "volume": None, "error": "nessun prezzo disponibile"})

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "BUY" else tick.bid
    tp_primary = float(tp_value) if tp_value is not None else 0.0

    mt5_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": float(sl) if sl is not None else 0.0,
        "tp": float(tp_primary),
        "deviation": 20,
        "magic": 990011,
        "comment": f"TB_{order_plan.get('ticket_id')}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(mt5_request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(f"Errore apertura ordine MT5: {result.comment}")
        return jsonify({"success": False, "mt5_ticket": None, "fill_price": None, "volume": None, "error": result.comment})

    logger.info(f"Ordine eseguito su MT5 | Ticket: {result.order} | Volume: {result.volume} | Prezzo: {result.price}")
    return jsonify({
        "success": True,
        "mt5_ticket": result.order,
        "fill_price": result.price,
        "volume": result.volume,
        "error": None,
    })


@app.route("/set_sl_to_be", methods=["POST"])
def set_sl_to_be():
    body = request.get_json(force=True)
    ticket = body["ticket"]
    entry_price = body["entry_price"]

    position = mt5.positions_get(ticket=ticket)
    if not position:
        logger.info(f"Ticket {ticket} non più aperto su MT5 (probabilmente TP già raggiunto).")
        return jsonify({"applied": False})

    pos = position[0]
    mt5_request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": pos.ticket,
        "sl": float(entry_price),
        "tp": pos.tp,
    }
    result = mt5.order_send(mt5_request)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"SL spostato a BE per la posizione #{pos.ticket}")
        return jsonify({"applied": True})

    logger.error(f"Errore spostamento BE posizione #{pos.ticket}: {result.comment}")
    return jsonify({"applied": False})


@app.route("/close_position", methods=["POST"])
def close_position():
    body = request.get_json(force=True)
    ticket = body["ticket"]

    position = mt5.positions_get(ticket=ticket)
    if not position:
        logger.info(f"Ticket {ticket} non presente su MT5: nessuna chiusura necessaria.")
        return jsonify({"closed": True})

    pos = position[0]
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY

    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        logger.error(f"Nessun prezzo disponibile per {pos.symbol}: chiusura di {pos.ticket} non inviata.")
        return jsonify({"closed": False})

    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

    mt5_request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": pos.ticket,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": close_type,
        "price": price,
        "deviation": 20,
        "magic": 990011,
        "comment": "Close by Bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(mt5_request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(f"Errore chiusura posizione {pos.ticket}: {result.comment}")
        return jsonify({"closed": False})

    logger.info(f"Posizione {pos.ticket} chiusa correttamente su MT5.")
    return jsonify({"closed": True})


@app.route("/modify_order_levels", methods=["POST"])
def modify_order_levels():
    body = request.get_json(force=True)
    ticket = body["ticket"]
    stop_loss = body.get("stop_loss")
    tp_price = body.get("take_profit") or 0.0

    mt5_request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": float(stop_loss) if stop_loss else 0.0,
        "tp": float(tp_price),
    }

    result = mt5.order_send(mt5_request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(f"Errore modifica ordine {ticket}: {result.comment}")
        return jsonify({"success": False})

    logger.info(f"Ordine {ticket} aggiornato con successo | SL: {stop_loss} | TP: {tp_price}")
    return jsonify({"success": True})


@app.route("/get_open_position/<int:ticket>", methods=["GET"])
def get_open_position(ticket):
    position = mt5.positions_get(ticket=ticket)
    if not position:
        return jsonify(None)

    pos = position[0]
    return jsonify({
        "ticket": pos.ticket,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "price_open": pos.price_open,
        "stop_loss": pos.sl,
        "take_profit": pos.tp,
    })


@app.route("/is_symbol_tradable/<symbol>", methods=["GET"])
def is_symbol_tradable(symbol):
    max_tick_age_seconds = int(request.args.get("max_tick_age_seconds", 180))

    info = mt5.symbol_info(symbol)
    if info is None:
        return jsonify({"tradable": True, "reason": "stato del simbolo non disponibile (assumo aperto)"})

    if info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
        return jsonify({"tradable": False, "reason": f"trading disabilitato su {symbol}"})

    tick = mt5.symbol_info_tick(symbol)
    if tick is None or not tick.time:
        return jsonify({"tradable": True, "reason": "nessun tick disponibile (assumo aperto)"})

    tick_age = time.time() - tick.time
    if tick_age > max_tick_age_seconds:
        return jsonify({"tradable": False, "reason": f"nessun tick da {int(tick_age)}s su {symbol} (mercato chiuso o illiquido)"})

    return jsonify({"tradable": True, "reason": f"{symbol} negoziabile"})


if __name__ == "__main__":
    if not BRIDGE_TOKEN:
        logger.warning("MT5_BRIDGE_TOKEN non impostato: il servizio rifiuterà tutte le richieste finché non viene configurato.")

    if not mt5.initialize():
        logger.error(f"Impossibile connettersi al terminale MT5 all'avvio: {mt5.last_error()}")
    else:
        logger.info("Connessione a MetaTrader 5 riuscita.")

    logger.info(f"Bridge in ascolto su {BRIDGE_HOST}:{BRIDGE_PORT}")
    app.run(host=BRIDGE_HOST, port=BRIDGE_PORT, threaded=True)
