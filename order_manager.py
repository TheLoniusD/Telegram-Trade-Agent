import json
import os
import time
import uuid
from typing import Dict, Optional

class OrderManager:
    """
    Gestisce lo stato attivo delle operazioni in memoria.
    Mappa i messaggi di Telegram (message_id) ai ticket di trading attivi.
    """
    def __init__(self, storage_path: str = "active_trades.json"):
        # Dizionario chiave-valore: { telegram_msg_id: dict_operazione }
        self.active_trades: Dict[int, dict] = {}
        # Puntatore diretto all'ultimo msg_id registrato
        self.latest_msg_id: Optional[int] = None

        self.storage_path = storage_path
        # Carica automaticamente lo stato esistente all'avvio
        self.load_state_from_file()

    def save_state_to_file(self, filepath: Optional[str] = None) -> None:
        """Salva fisicamente lo stato corrente di active_trades e latest_msg_id su file JSON."""
        target_path = filepath or self.storage_path
        state_payload = {
            "latest_msg_id": self.latest_msg_id,
            "active_trades": self.active_trades
        }

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(state_payload, f, indent=2, default=str, ensure_ascii=False)
        print(f"💾 Stato memoria salvato in: {target_path}")


    def load_state_from_file(self, filepath: Optional[str] = None) -> bool:
        """Carica lo stato memorizzato da file JSON (convertendo le chiavi str in int)."""
        target_path = filepath or self.storage_path
        
        if not os.path.exists(target_path):
            return False

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                # I JSON convertono le chiavi int in stringhe, le ri-convertiamo in int per msg_id
                self.active_trades = {int(k): v for k, v in data.get("active_trades", {}).items()}
                self.latest_msg_id = data.get("latest_msg_id")
                
            print(f"📂 Stato memoria caricato da: {target_path} ({len(self.active_trades)} trade attivi)")
            return True
        except Exception as e:
            print(f"⚠️ Errore nel caricamento del file di stato: {e}")
            return False


    def _get_latest_trade(self) -> Optional[dict]:
        """Recupera l'operazione più recente contrassegnata come attiva."""
        if self.latest_msg_id and self.latest_msg_id in self.active_trades:
            return self.active_trades[self.latest_msg_id]
        return None


    def _get_trades_by_group(self, target_msg_id: int) -> list[dict]:
        """Restituisce tutte le operazioni attive collegate allo stesso root_msg_id o symbol/direction."""
        # Trova prima il trade target
        target_trade = self.active_trades.get(target_msg_id) or self._get_latest_trade()
        if not target_trade:
            return []

        root_id = target_trade["root_msg_id"]
        
        # Filtra tutti i trade attivi che condividono lo stesso root_msg_id
        matching_trades = [
            trade for trade in self.active_trades.values()
            if trade["status"] == "ACTIVE" and trade["root_msg_id"] == root_id
        ]
        
        return matching_trades


    def handle_agent_output(self, msg_id: int, reply_to: Optional[int], ai_output: dict) -> Optional[dict]:
        intent = ai_output.get("intent")
        data = ai_output.get("data", {})
    
        # 1. NUOVO SEGNALE (Lo salviamo SEMPRE per tracciare futuri edit o reply)
        if intent == "NEW_SIGNAL":
            ticket_id = str(uuid.uuid4())[:8].upper()

            # Recuperiamo il riferimento all'ultima operazione attiva (se presente)
            latest_trade = self._get_latest_trade()

            # Determina se è un Re-entry legato a una catena esistente o una nuova catena
            is_reentry = (data.get("symbol") is None) and (latest_trade is not None)

            # Se è re-entry eredita il root_msg_id, altrimenti questo messaggio diventa il root
            root_msg_id = latest_trade["root_msg_id"] if is_reentry else msg_id

            # Ereditarietà dinamica dei dati mancanti
            symbol = data.get("symbol") or (latest_trade.get("symbol") if latest_trade else "XAUUSD")
            direction = data.get("direction") or (latest_trade.get("direction") if latest_trade else None)

            stop_loss = data.get("stop_loss")
            if stop_loss is None and latest_trade:
                stop_loss = latest_trade.get("stop_loss")

            tp_list = data.get("take_profit", [])
            tp1 = tp_list[0] if len(tp_list) > 0 else (latest_trade.get("tp1") if latest_trade else None)
            tp2 = tp_list[1] if len(tp_list) > 1 else (latest_trade.get("tp2") if latest_trade else None)

            trade_record = {
                "ticket_id": ticket_id,
                "msg_id": msg_id,
                "root_msg_id": root_msg_id,
                "entry_min": data.get("entry_min"),
                "entry_max": data.get("entry_max"),
                "status": "ACTIVE",
                "be_active": False,
                "created_at": time.time(),
                "tickets": {
                    "tp1": {
                        "symbol": symbol,
                        "direction": direction,
                        "entry_price": data.get("entry_min"),
                        "stop_loss": stop_loss,
                        "take_profit": tp1,
                        "volume": None,  # Sarà calcolato dal Risk Manager
                        "mt5_ticket": None, # Sarà aggiunto dal MT5Executor
                        "success": False
                    },
                    "tp2": {
                        "symbol": symbol,
                        "direction": direction,
                        "entry_price": data.get("entry_min"),
                        "stop_loss": stop_loss,
                        "take_profit": tp2,
                        "volume": None,  # Sarà calcolato dal Risk Manager
                        "mt5_ticket": None, # Sarà aggiunto dal MT5Executor
                        "success": False
                    }
                }
            }

            self.active_trades[msg_id] = trade_record
            self.latest_msg_id = msg_id

            self.save_state_to_file()
            return {"action": "OPEN", "trade": trade_record}

        # 2. AGGIORNAMENTO SEGNALE (tramite Edit o Reply)
        elif intent == "UPDATE_SIGNAL":
            target_msg_id = reply_to if reply_to else msg_id
            trades_to_update = self._get_trades_by_group(target_msg_id)

            if not trades_to_update:
                return {"action": "IGNORE", "reason": "Nessun trade attivo trovato per l'aggiornamento."}

            new_entry_min = data.get("entry_min")
            new_entry_max = data.get("entry_max")
            new_sl = data.get("stop_loss")
            new_tp_list = data.get("take_profit")  # Es: [4487.0, 4490.0]
            update_details = data.get("update_details", {})
            move_to_be = update_details.get("move_sl_to_be", False)

            updated_trades = []

            for trade in trades_to_update:
                # 1. Aggiornamento Range di Ingresso
                if new_entry_min is not None:
                    trade["entry_min"] = new_entry_min
                if new_entry_max is not None:
                    trade["entry_max"] = new_entry_max

                # 2. Aggiornamento Stop Loss a livello radice
                if new_sl is not None:
                    trade["stop_loss"] = new_sl

                # 3. Aggiornamento Take Profit a livello radice (per ereditarietà re-entry)
                if new_tp_list and isinstance(new_tp_list, list):
                    if len(new_tp_list) > 0:
                        trade["tp1"] = new_tp_list[0]
                    if len(new_tp_list) > 1:
                        trade["tp2"] = new_tp_list[1]

                # 4. Mappatura sui singoli ticket MT5 (tp1, tp2, ecc.)
                if "tickets" in trade:
                    ticket_keys = list(trade["tickets"].keys())  # ['tp1', 'tp2']

                    for i, tp_key in enumerate(ticket_keys):
                        tp_config = trade["tickets"][tp_key]

                        if new_sl is not None:
                            tp_config["stop_loss"] = new_sl

                        if new_tp_list and isinstance(new_tp_list, list):
                            if i < len(new_tp_list):
                                tp_config["take_profit"] = new_tp_list[i]
                        elif new_tp_list is not None:
                            tp_config["take_profit"] = new_tp_list

                # 5. Messa a Breakeven (BE+)
                if move_to_be:
                    trade["be_active"] = True
                    if "tickets" in trade:
                        for tp_config in trade["tickets"].values():
                            entry_price = tp_config.get("entry_price") or trade.get("entry_min")
                            if entry_price is not None:
                                tp_config["stop_loss"] = entry_price
                        trade["stop_loss"] = entry_price  # Sincronizza anche la radice

                updated_trades.append(trade)

            # Persistenza dello stato aggiornato su file
            self.save_state_to_file()
            return {"action": "UPDATE", "trade": trade}

        # 3. CHIUSURA SEGNALE
        elif intent == "CLOSE_SIGNAL":
            target_msg_id = reply_to if (reply_to and reply_to in self.active_trades) else msg_id

            # Se ancora non lo troviamo, cerchiamo se tra i messaggi attivi ce n'è uno valido (fallback utile se il reply_to è vuoto ma c'è un solo trade attivo)
            if not target_msg_id and len(self.active_trades) == 1:
                target_msg_id = list(self.active_trades.keys())[0]

            if target_msg_id in self.active_trades:
                closed_trade = self.active_trades.pop(target_msg_id)
                closed_trade["status"] = "CLOSED"
                
                # Aggiorniamo lo stato dei singoli ticket interni per coerenza con la memoria
                if "tickets" in closed_trade:
                    for tp_config in closed_trade["tickets"].values():
                        tp_config["success"] = False

                print(f"🔒 [CLOSE_SIGNAL] Operazione {target_msg_id} rimossa dalla memoria attiva.")
                return {"action": "CLOSE", "trade": closed_trade}

        return None