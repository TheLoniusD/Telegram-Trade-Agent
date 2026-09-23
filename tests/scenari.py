"""
Scenari di messaggi Telegram fittizi, ciascuno con la classificazione che
l'agente dovrebbe produrre (scritta a mano: nessun token consumato).

I prezzi sono espressi rispetto a un prezzo di riferimento `p`, così lo stesso
scenario funziona sia offline (p fisso) sia sul conto demo (p = prezzo reale
al momento del test), con SL/TP sempre dal lato corretto del mercato.
"""


def classificazione(intent, symbol="XAUUSD", direction=None, entry_min=None, entry_max=None,
                    stop_loss=None, take_profit=None, move_sl_to_be=False, close_percentage=None,
                    layer_target=None, reasoning="scenario di test"):
    """Stessa struttura dello strumento 'classify_signal' di agent_classifier.py."""
    return {
        "intent": intent,
        "is_actionable": intent != "IGNORE",
        "data": {
            "symbol": symbol,
            "direction": direction,
            "entry_min": entry_min,
            "entry_max": entry_max,
            "stop_loss": stop_loss,
            "take_profit": take_profit or [],
            "update_details": {
                "move_sl_to_be": move_sl_to_be,
                "close_percentage": close_percentage,
                "layer_target": layer_target,
            },
        },
        "raw_reasoning": reasoning,
    }


def passo(msg_id, testo, output, is_edit=False, reply_to=None, nota=""):
    return {"msg_id": msg_id, "text": testo, "is_edit": is_edit, "reply_to": reply_to, "output": output, "nota": nota}


def ciclo_completo_sell(p):
    """Fase rapida -> edit con SL/TP -> BE+ -> HIT TP MAX."""
    sl, tp1, tp2 = round(p + 10, 2), round(p - 5, 2), round(p - 10, 2)
    return [
        passo(1001, "GOLD SELL NOW",
              classificazione("NEW_SIGNAL", direction="SELL", reasoning="fase rapida, nessun SL/TP"),
              nota="Apre 2 posizioni SELL a mercato con SL temporaneo (+5) e nessun TP"),
        passo(1001, f"GOLD SELL NOW\n\nSELL @ {p} - {p + 5}\n\nSL🔴{sl}\nTP✅{tp1}\nTP✅{tp2}\n\nCare Money Management 💠",
              classificazione("UPDATE_SIGNAL", direction="SELL", entry_min=p, entry_max=round(p + 5, 2),
                              stop_loss=sl, take_profit=[tp1, tp2], reasoning="fase completa, edit SL/TP"),
              is_edit=True,
              nota=f"Imposta SL {sl} su entrambe, TP {tp1} sulla prima e {tp2} sulla seconda"),
        passo(1002, "Trade Active ✅\n\nGold Sell Running 100+ Pips\n\nScalpers secure ur Profits & BE+ ur entries",
              classificazione("UPDATE_SIGNAL", direction="SELL", move_sl_to_be=True, reasoning="trade active con BE+"),
              reply_to=1001,
              nota="Sposta lo SL al prezzo di ingresso (serve che il prezzo sia sceso sotto l'ingresso)"),
        passo(1003, "HIT TP MAX⚡️⚡️\n\nGold Sell 300+ Pips ✔️\n\nCollect all or half & BE+ ur entries",
              classificazione("CLOSE_SIGNAL", direction="SELL", reasoning="HIT TP MAX, chiusura totale"),
              reply_to=1001,
              nota="Chiude tutte le posizioni della catena"),
    ]


def segnale_completo_buy(p):
    """Segnale BUY già completo di SL/TP -> taglio perdite -> chiusura manuale."""
    sl, tp1, tp2 = round(p - 10, 2), round(p + 5, 2), round(p + 10, 2)
    nuovo_sl = round(p - 6, 2)
    return [
        passo(2001, f"GOLD BUY NOW\n\nBUY @ {p} - {p - 5}\n\nSL🔴{sl}\nTP✅{tp1}\nTP✅{tp2}",
              classificazione("NEW_SIGNAL", direction="BUY", entry_min=p, entry_max=round(p - 5, 2),
                              stop_loss=sl, take_profit=[tp1, tp2], reasoning="segnale completo BUY"),
              nota=f"Apre 2 posizioni BUY con SL {sl}, TP {tp1} e {tp2}"),
        passo(2002, f"Cut loss if solid break {nuovo_sl}",
              classificazione("UPDATE_SIGNAL", direction="BUY", stop_loss=nuovo_sl, reasoning="invalidation, nuovo SL"),
              reply_to=2001,
              nota=f"Stringe lo SL a {nuovo_sl} mantenendo i TP"),
        passo(2003, "Close GOLD now",
              classificazione("CLOSE_SIGNAL", reasoning="chiusura manuale totale"),
              reply_to=2001,
              nota="Chiude tutte le posizioni"),
    ]


def reentry_sell(p):
    """Segnale completo -> 'Try sell again' (eredita i dati) -> chiusura dell'intera catena."""
    sl, tp1, tp2 = round(p + 10, 2), round(p - 5, 2), round(p - 10, 2)
    return [
        passo(3001, f"GOLD SELL NOW\n\nSELL @ {p}\n\nSL🔴{sl}\nTP✅{tp1}\nTP✅{tp2}",
              classificazione("NEW_SIGNAL", direction="SELL", entry_min=p, stop_loss=sl,
                              take_profit=[tp1, tp2], reasoning="segnale completo SELL"),
              nota="Apre 2 posizioni SELL"),
        passo(3002, "Try sell again, M15 DBD zone",
              classificazione("NEW_SIGNAL", symbol=None, direction="SELL", reasoning="re-entry imperativo"),
              nota="Re-entry: apre altre 2 SELL ereditando SL/TP"),
        passo(3003, "Close all open trades before news",
              classificazione("CLOSE_SIGNAL", reasoning="chiusura totale pre news"),
              nota="Chiude tutte e 4 le posizioni (originale + re-entry)"),
    ]


def messaggi_da_ignorare(p):
    """Nessuno di questi messaggi deve generare richieste verso MT5."""
    return [
        passo(4001, "READY US NEWS SESSION🔔\n\n🔻Non-Farm Employment Change\n\nClaim 50% Deposit Bonus",
              classificazione("IGNORE", symbol=None, reasoning="READY, nessun comando"),
              nota="Preavviso news: ignorato"),
        passo(4002, "Did u caught the move ⁉️\n\nFlex me ur Profit",
              classificazione("IGNORE", symbol=None, reasoning="celebrativo"),
              nota="Celebrativo: ignorato"),
        passo(4003, "Hit risk !\n\nReady setup recovery!",
              classificazione("IGNORE", symbol=None, reasoning="enigmatico senza direzione"),
              nota="Enigmatico: ignorato"),
    ]


SCENARI = {
    "ciclo_completo_sell": ciclo_completo_sell,
    "segnale_completo_buy": segnale_completo_buy,
    "reentry_sell": reentry_sell,
    "messaggi_da_ignorare": messaggi_da_ignorare,
}
