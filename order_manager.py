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

    # Helper per cercare il valore prima su root, poi dentro tickets['tp1']
    def _get_param(self, latest_trade, key, ticket_field=None):
        if not latest_trade:
            return None
        # 1. Cerca a livello root nel vecchio trade
        if latest_trade.get(key) is not None:
            return latest_trade[key]
        # 2. Fallback su tickets -> tp1
        field = ticket_field or key
        if "tickets" in latest_trade and "tp1" in latest_trade["tickets"]:
            return latest_trade["tickets"]["tp1"].get(field)
        return None


    def _resolve_layer_target(self, trade: dict, layer_target: str) -> list:
        """
        Mappa LOWEST/HIGHEST/ALL sulle chiavi reali dei ticket (tp1/tp2) in base alla
        distanza del take_profit dall'entry price, non all'ordine tp1/tp2 (che dipende
        solo dall'ordine in cui il trader ha scritto i TP nel messaggio). Considera
        solo i ticket ancora aperti (mt5_ticket presente, non già marcati 'closed').
        """
        tickets = trade.get("tickets", {})
        open_tickets = {
            key: cfg for key, cfg in tickets.items()
            if cfg.get("mt5_ticket") and not cfg.get("closed") and cfg.get("take_profit") is not None
        }
        if not open_tickets:
            return []

        layer_target = str(layer_target).upper()
        if layer_target == "ALL":
            return list(open_tickets.keys())

        def distance(cfg):
            entry = cfg.get("entry_price")
            if entry is None:
                entry = trade.get("entry_min")
            if entry is None:
                return float("inf")
            return abs(cfg["take_profit"] - entry)

        by_distance = sorted(open_tickets.items(), key=lambda item: distance(item[1]))
        if layer_target == "LOWEST":
            return [by_distance[0][0]]
        if layer_target == "HIGHEST":
            return [by_distance[-1][0]]
        return []


    def handle_agent_output(self, msg_id: int, reply_to: Optional[int], ai_output: dict) -> Optional[dict]:
        intent = ai_output.get("intent")
        data = ai_output.get("data", {})
    
        # 1. NUOVO SEGNALE (Lo salviamo SEMPRE per tracciare futuri edit o reply)
        if intent == "NEW_SIGNAL":
            ticket_id = str(uuid.uuid4())[:8].upper()

            # Recuperiamo il riferimento all'ultima operazione attiva (se presente)
            latest_trade = self._get_latest_trade()
            has_active_trade = latest_trade is not None and latest_trade.get("status") == "ACTIVE"

            # CONDIZIONI RE-ENTRY:
            # È re-entry se c'è un trade attivo E (manca il simbolo O (stesso simbolo E manca lo stop loss))
            missing_symbol = data.get("symbol") is None
            missing_sl = data.get("stop_loss") is None
            missing_entry = data.get("entry_min") is None

            same_symbol = has_active_trade and (data.get("symbol") == latest_trade.get("symbol"))

            is_reentry = has_active_trade and (missing_symbol or (same_symbol and (missing_sl or missing_entry)))

            # Se è re-entry ereditiamo da latest_trade, altrimenti disattiviamo l'ereditarietà (source_trade = None)
            source_trade = latest_trade if is_reentry else None
            root_msg_id = latest_trade.get("root_msg_id", latest_trade["msg_id"]) if is_reentry else msg_id

            # Ereditarietà dinamica dei dati (solo se source_trade non è None)
            symbol = data.get("symbol") or self._get_param(source_trade, "symbol") or "XAUUSD"
            direction = data.get("direction") or self._get_param(source_trade, "direction")

            entry_min = data.get("entry_min") if data.get("entry_min") is not None else self._get_param(source_trade, "entry_min", "entry_price")
            entry_max = data.get("entry_max") if data.get("entry_max") is not None else self._get_param(source_trade, "entry_max")

            stop_loss = data.get("stop_loss") if data.get("stop_loss") is not None else self._get_param(source_trade, "stop_loss")

            # Gestione Take Profit (da lista se fornita, altrimenti ereditati)
            tp_list = data.get("take_profit", [])
            if isinstance(tp_list, list) and len(tp_list) > 0:
                tp1 = tp_list[0]
                tp2 = tp_list[1] if len(tp_list) > 1 else None
            else:
                tp1 = self._get_param(source_trade, "tp1", "take_profit")
                tp2 = self._get_param(source_trade, "tp2")
                if tp2 is None and source_trade and "tickets" in source_trade and "tp2" in source_trade["tickets"]:
                    tp2 = source_trade["tickets"]["tp2"].get("take_profit")

            trade_record = {
                "ticket_id": ticket_id,
                "msg_id": msg_id,
                "root_msg_id": root_msg_id,
                "entry_min": entry_min,
                "entry_max": entry_max,
                "status": "ACTIVE",
                "created_at": time.time(),
                "tickets": {
                    "tp1": {
                        "symbol": symbol,
                        "direction": direction,
                        "entry_price": entry_min,
                        "stop_loss": stop_loss,
                        "take_profit": tp1,
                        "volume": None,  # Sarà calcolato dal Risk Manager
                        "mt5_ticket": None, # Sarà aggiunto dal MT5Executor
                        "success": False,  # true = ordine aperto correttamente su MT5
                        "closed": False,   # true = TP colpito / posizione non più aperta
                        "be_active": False
                    },
                    "tp2": {
                        "symbol": symbol,
                        "direction": direction,
                        "entry_price": entry_min,
                        "stop_loss": stop_loss,
                        "take_profit": tp2,
                        "volume": None,  # Sarà calcolato dal Risk Manager
                        "mt5_ticket": None, # Sarà aggiunto dal MT5Executor
                        "success": False,  # true = ordine aperto correttamente su MT5
                        "closed": False,   # true = TP colpito / posizione non più aperta
                        "be_active": False
                    }
                }
            }

            self.active_trades[msg_id] = trade_record
            self.latest_msg_id = msg_id

            self.save_state_to_file()
            return {"action": "OPEN", "trade": trade_record}

        # 2. AGGIORNAMENTO SEGNALE (tramite Edit o Reply)
        elif intent == "UPDATE_SIGNAL":
            new_entry_min = data.get("entry_min")
            new_entry_max = data.get("entry_max")
            new_sl = data.get("stop_loss")
            new_tp_list = data.get("take_profit")  # Es: [4487.0, 4490.0]

            update_details = data.get("update_details", {})
            move_to_be = update_details.get("move_sl_to_be", False)
            layer_target = update_details.get("layer_target")

            # 1. Identificazione del Trade Bersaglio: 'reply_to' punta sempre al
            #    messaggio COMPLETO (quello con SL/TP impostati), che è la radice
            #    riconosciuta della famiglia di operazioni.
            target_msg_id = reply_to if (reply_to and reply_to in self.active_trades) else None

            if not target_msg_id:
                latest_trade = self._get_latest_trade()
                if latest_trade and latest_trade.get("status") == "ACTIVE":
                    target_msg_id = latest_trade["msg_id"]

            if not target_msg_id or target_msg_id not in self.active_trades:
                return {"action": "IGNORE", "reason": "Nessun trade attivo trovato per l'aggiornamento."}

            # 2. Recuperiamo l'intera catena legata al trade trovato (originale + re-entry)
            trades_to_update = self._get_trades_by_group(target_msg_id)
            if not trades_to_update:
                return {"action": "IGNORE", "reason": "Nessun trade attivo trovato per l'aggiornamento."}

            updated_trades = []
            be_candidates = []  # ticket ancora da proteggere in Breakeven

            for trade in trades_to_update:
                modified = False

                # A. Aggiornamento Range di Ingresso
                if new_entry_min is not None:
                    trade["entry_min"] = new_entry_min
                    modified = True
                if new_entry_max is not None:
                    trade["entry_max"] = new_entry_max
                    modified = True

                # B. Aggiornamento Stop Loss a livello radice (es. invalidation/cut loss)
                if new_sl is not None:
                    trade["stop_loss"] = new_sl
                    modified = True
                    for tp_config in trade.get("tickets", {}).values():
                        tp_config["stop_loss"] = new_sl

                # C. Aggiornamento Take Profit (fase COMPLETA / ereditarietà re-entry)
                if new_tp_list and isinstance(new_tp_list, list):
                    ticket_keys = list(trade.get("tickets", {}).keys())  # ['tp1', 'tp2']
                    for i, tp_key in enumerate(ticket_keys):
                        if i < len(new_tp_list):
                            trade["tickets"][tp_key]["take_profit"] = new_tp_list[i]
                    modified = True

                # D. Marcatura del layer colpito, SOLO se il messaggio lo indica
                #    esplicitamente (es. "close lowest layer"). Il mapping è basato
                #    sulla distanza take_profit-entry, non sull'ordine tp1/tp2 (che
                #    dipende solo dall'ordine in cui il trader ha scritto i TP).
                if layer_target:
                    for target_key in self._resolve_layer_target(trade, layer_target):
                        ticket_info = trade["tickets"][target_key]
                        if not ticket_info.get("closed"):
                            ticket_info["closed"] = True
                            modified = True

                # E. Candidati al Breakeven: SEMPRE valutato, indipendentemente da
                #    new_tp_list/layer_target (prima era annidato lì dentro e un
                #    "BE+" da solo non veniva mai applicato). Non decidiamo qui se
                #    un ticket è "ancora aperto" leggendo il testo: lo fa MT5Executor
                #    in tempo reale (fonte di verità), qui raccogliamo solo i ticket
                #    non ancora marcati come protetti o come già chiusi.
                if move_to_be:
                    for tp_key, tp_config in trade.get("tickets", {}).items():
                        if tp_config.get("mt5_ticket") and not tp_config.get("be_active") and not tp_config.get("closed"):
                            be_candidates.append(tp_config)

                if modified:
                    updated_trades.append(trade)

            if updated_trades or be_candidates:
                self.save_state_to_file()
                print(f"🔄 [UPDATE_SIGNAL] Applicati aggiornamenti a {len(updated_trades)} posizioni, {len(be_candidates)} candidati a BE.")
                return {
                    "action": "UPDATE",
                    "trades": trades_to_update,
                    "be_candidates": be_candidates,
                    "sl_tp_changed": bool(new_sl is not None or (new_tp_list and isinstance(new_tp_list, list))),
                }

            return {"action": "IGNORE", "reason": "Nessuna modifica applicabile (nessun trade idoneo)."}

        # 3. CHIUSURA SEGNALE
        elif intent == "CLOSE_SIGNAL":
            target_msg_id = reply_to if (reply_to and reply_to in self.active_trades) else msg_id

            # Se ancora non lo troviamo, cerchiamo se tra i messaggi attivi ce n'è uno valido (fallback utile se il reply_to è vuoto ma c'è un solo trade attivo)
            if not target_msg_id:
                latest_trade = self._get_latest_trade()
                if latest_trade and latest_trade.get("status") == "ACTIVE":
                    target_msg_id = latest_trade["msg_id"]

            # 2. Se abbiamo trovato un trade bersaglio, chiudiamo TUTTA la catena collegata
            if target_msg_id and target_msg_id in self.active_trades:
                target_trade = self.active_trades[target_msg_id]

                # Recuperiamo il root ID: ci serve per trovare tutte le operazioni collegate (re-entry inclusi)
                root_id = target_trade.get("root_msg_id", target_trade["msg_id"])
                closed_chain = []

                # Scansioniamo tutte le operazioni in memoria
                for m_id, trade in self.active_trades.items():
                    # Se fa parte della stessa "famiglia" (stesso root_msg_id) ed è ancora attiva
                    if trade.get("root_msg_id", trade["msg_id"]) == root_id and trade.get("status") == "ACTIVE":
                        
                        # Modifichiamo lo stato
                        trade["status"] = "CLOSED"
                        
                        # Impostiamo i ticket interni come non più aperti (ma NON svuotiamo
                        # l'mt5_ticket, altrimenti l'MT5Executor non saprà cosa chiudere sul broker)
                        if "tickets" in trade:
                            for tp_config in trade["tickets"].values():
                                tp_config["closed"] = True

                        closed_chain.append(trade)

                self.save_state_to_file()

                print(f"🔒 [CLOSE_SIGNAL] Root ID {root_id}: Chiuse e mantenute in memoria {len(closed_chain)} operazioni (Originale + Re-entry).")
                
                # Passiamo l'intera lista di trade chiusi così che l'MT5Executor 
                # possa iterare e chiudere tutto sul broker
                return {
                    "action": "CLOSE", 
                    "trade": target_trade, 
                    "closed_chain": closed_chain 
                }

        return None