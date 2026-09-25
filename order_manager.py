import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from logger_config import setup_logger

logger = setup_logger(__name__)

# Stati in cui un'operazione è (o potrebbe essere) ancora a mercato e quindi
# va considerata chiudibile. "CLOSING" rientra per permettere di ritentare una
# chiusura interrotta a metà (es. crash del bot durante l'invio a MT5).
CLOSABLE_STATUSES = ("ACTIVE", "CLOSING", "CLOSE_FAILED")

# Stati definitivi: l'operazione non tornerà più a mercato, quindi esce dallo
# stato vivo e finisce nell'archivio giornaliero.
TERMINAL_STATUSES = ("CLOSED", "REJECTED", "OPEN_FAILED")

# Un NEW_SIGNAL con stessa direzione e stesso prezzo (entro questa tolleranza
# in $) dell'operazione ancora aperta è un secondo ingresso dello stesso setup
# (23/09: "Gold sell 4285" alle 04:20 e di nuovo alle 04:25), non un segnale
# nuovo: viene collegato al primo, così una chiusura li prende entrambi.
SAME_SETUP_PRICE_TOLERANCE = 1.0


class OrderManager:
    """
    Gestisce lo stato attivo delle operazioni in memoria.
    Mappa i messaggi di Telegram (message_id) ai ticket di trading attivi.
    """
    def __init__(self, storage_path: str = "active_trades.json", archive_dir: str = "storico"):
        # Dizionario chiave-valore: { telegram_msg_id: dict_operazione }
        self.active_trades: Dict[int, dict] = {}
        # Puntatore diretto all'ultimo msg_id registrato
        self.latest_msg_id: Optional[int] = None

        self.storage_path = storage_path
        self.archive_dir = archive_dir
        # Carica automaticamente lo stato esistente all'avvio
        self.load_state_from_file()

    def _archive_terminal_trades(self) -> int:
        """
        Sposta le operazioni concluse (CLOSED/REJECTED) dallo stato vivo a un
        archivio giornaliero in formato JSONL (un'operazione per riga, in append).

        Perché non basta cancellare il file a fine giornata: l'oro resta aperto da
        domenica sera a venerdì sera, quindi un'operazione aperta lunedì può essere
        ancora a mercato mercoledì. Azzerare il file a fine giornata farebbe perdere
        al bot le posizioni realmente aperte. Qui invece esce dallo stato vivo solo
        ciò che è definitivamente concluso, e nulla va perso perché finisce nello
        storico.
        """
        terminali = [(msg_id, trade) for msg_id, trade in self.active_trades.items()
                     if trade.get("status") in TERMINAL_STATUSES]

        if not terminali:
            return 0

        os.makedirs(self.archive_dir, exist_ok=True)
        nome_file = f"trades_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        percorso = os.path.join(self.archive_dir, nome_file)

        with open(percorso, "a", encoding="utf-8") as f:
            for msg_id, trade in terminali:
                record = dict(trade)
                record["archived_at"] = datetime.now(timezone.utc).isoformat()
                f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")

        for msg_id, _ in terminali:
            del self.active_trades[msg_id]

        logger.info(f"🗄️ Archiviate {len(terminali)} operazioni concluse in {percorso}")
        return len(terminali)

    def save_state_to_file(self, filepath: Optional[str] = None) -> None:
        """
        Salva lo stato corrente su file JSON.

        Prima archivia le operazioni concluse, così active_trades.json contiene
        sempre e solo ciò che è ancora a mercato e non cresce nel tempo.
        """
        self._archive_terminal_trades()

        target_path = filepath or self.storage_path
        state_payload = {
            "latest_msg_id": self.latest_msg_id,
            "active_trades": self.active_trades
        }

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(state_payload, f, indent=2, default=str, ensure_ascii=False)
        # debug: il salvataggio avviene più volte per messaggio (prima e dopo
        # l'invio a MT5) e a INFO riempiva il log di righe identiche.
        logger.debug(f"💾 Stato memoria salvato in: {target_path} ({len(self.active_trades)} operazioni vive)")


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
                
            logger.info(f"📂 Stato memoria caricato da: {target_path} ({len(self.active_trades)} trade attivi)")
            return True
        except Exception as e:
            logger.exception(f"⚠️ Errore nel caricamento del file di stato: {e}")
            return False


    def reconcile_with_broker(self, position_lookup) -> int:
        """
        Allinea la memoria con la realtà del broker.

        'position_lookup(ticket)' deve ritornare lo stato reale della posizione
        (dict) oppure None se non è più aperta - vedi MT5Executor.get_open_position.
        Da chiamare all'avvio: mentre il bot era spento un TP/SL può essere
        scattato, o l'operazione può essere stata chiusa a mano dal terminale.

        Ritorna il numero di ticket il cui stato è stato corretto.
        """
        corrections = 0

        for trade in self.active_trades.values():
            if trade.get("status") not in CLOSABLE_STATUSES:
                continue

            tickets = trade.get("tickets", {})
            for tp_key, tp_config in tickets.items():
                mt5_ticket = tp_config.get("mt5_ticket")
                if not mt5_ticket or tp_config.get("closed"):
                    continue
                # Ordine limite non ancora eseguito: non è una posizione, il suo
                # stato lo segue il listener (riempimento, scadenza, cancellazione)
                if tp_config.get("pending"):
                    continue

                state = position_lookup(mt5_ticket)

                if state is None:
                    tp_config["closed"] = True
                    corrections += 1
                    logger.info(f"🔄 [RICONCILIAZIONE] Ticket {mt5_ticket} non più aperto su MT5: segnato come chiuso.")
                    continue

                # La posizione esiste ancora: i valori del broker vincono sempre
                # su quelli in memoria (potrebbero essere stati modificati a mano).
                for campo_memoria, campo_broker in (("stop_loss", "stop_loss"), ("take_profit", "take_profit"), ("volume", "volume"), ("entry_price", "price_open")):
                    valore_broker = state.get(campo_broker)
                    if valore_broker and tp_config.get(campo_memoria) != valore_broker:
                        tp_config[campo_memoria] = valore_broker
                        corrections += 1

            # Se tutti i ticket sono chiusi, lo è anche l'operazione
            if tickets and all(t.get("closed") for t in tickets.values()):
                trade["status"] = "CLOSED"
                logger.info(f"🔄 [RICONCILIAZIONE] Operazione {trade.get('ticket_id')} risulta chiusa su MT5.")

        if corrections:
            self.save_state_to_file()

        return corrections


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

        root_id = target_trade.get("root_msg_id", target_trade.get("msg_id"))

        # Filtra tutti i trade attivi che condividono lo stesso root_msg_id
        matching_trades = [
            trade for trade in self.active_trades.values()
            if trade.get("status") == "ACTIVE" and trade.get("root_msg_id", trade.get("msg_id")) == root_id
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


    def _explicit_target(self, msg_id: int, reply_to: Optional[int]) -> Optional[int]:
        """
        Operazione a cui si riferisce esplicitamente un messaggio di update o
        chiusura: quella a cui risponde (reply), oppure, se è l'EDIT di un
        messaggio di segnale, quel segnale stesso. Senza riferimento esplicito
        il chiamante ricade sull'ultima operazione aperta: prima anche l'edit
        con SL/TP di un segnale più vecchio finiva sull'ultimo aperto.
        """
        if reply_to and reply_to in self.active_trades:
            return reply_to
        if msg_id in self.active_trades:
            return msg_id
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
            # Guardia: se per questo msg_id esiste già un'operazione a mercato
            # (es. un edit classificato per errore come NEW_SIGNAL), sovrascriverla
            # ci farebbe perdere i riferimenti ai ticket MT5 già aperti, lasciandoli
            # orfani sul broker senza che il bot sappia più chiuderli.
            existing_trade = self.active_trades.get(msg_id)
            if existing_trade and existing_trade.get("status") in CLOSABLE_STATUSES:
                logger.warning(f"⚠️ [NEW_SIGNAL] msg_id {msg_id} ha già un'operazione a mercato: segnale ignorato per non perdere i ticket esistenti.")
                return {"action": "IGNORE", "reason": "Operazione già esistente per questo messaggio."}

            ticket_id = str(uuid.uuid4())[:8].upper()

            # Recuperiamo il riferimento all'ultima operazione attiva (se presente)
            latest_trade = self._get_latest_trade()
            has_active_trade = latest_trade is not None and latest_trade.get("status") == "ACTIVE"

            # CONDIZIONI RE-ENTRY (secondo ingresso collegato all'operazione aperta):
            #  - manca il simbolo ("Try sell again"), oppure
            #  - stesso simbolo e manca il prezzo di ingresso, oppure
            #  - stesso simbolo, stessa direzione e stesso prezzo di ingresso.
            # Simbolo e direzione vanno letti dai ticket (_get_param): alla radice
            # del trade non esistono, e il confronto risultava sempre falso.
            # NON basta che manchi lo SL: ogni fase rapida non ha SL, e segnali
            # diversi (es. SELL 4286 e poi SELL 4284) finirebbero incatenati, con
            # gli edit dell'uno applicati anche all'altro.
            missing_symbol = data.get("symbol") is None
            missing_entry = data.get("entry_min") is None

            same_symbol = has_active_trade and data.get("symbol") == self._get_param(latest_trade, "symbol")
            latest_entry = latest_trade.get("entry_min") if has_active_trade else None
            same_setup = (
                same_symbol
                and data.get("direction") == self._get_param(latest_trade, "direction")
                and not missing_entry and latest_entry is not None
                and abs(data["entry_min"] - latest_entry) <= SAME_SETUP_PRICE_TOLERANCE
            )

            is_reentry = has_active_trade and (missing_symbol or (same_symbol and missing_entry) or same_setup)

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
            # Chiusura parziale ("close half" = 50): la esegue il listener su MT5,
            # scegliendo quali ticket chiudere in base ai volumi reali.
            close_percentage = update_details.get("close_percentage")
            partial_close = close_percentage is not None and 0 < close_percentage < 100
            layer_target = update_details.get("layer_target")

            # 1. Identificazione del Trade Bersaglio: 'reply_to' punta sempre al
            #    messaggio COMPLETO (quello con SL/TP impostati), che è la radice
            #    riconosciuta della famiglia di operazioni.
            target_msg_id = self._explicit_target(msg_id, reply_to)

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
                            # Includiamo il riferimento al trade padre per poter risincronizzare
                            # lo stop_loss a livello radice dopo un BE riuscito.
                            be_candidates.append({"trade": trade, "ticket": tp_config})

                if modified:
                    updated_trades.append(trade)

            if updated_trades or be_candidates or partial_close:
                self.save_state_to_file()
                logger.info(f"🔄 [UPDATE_SIGNAL] Applicati aggiornamenti a {len(updated_trades)} posizioni, {len(be_candidates)} candidati a BE.")
                return {
                    "action": "UPDATE",
                    "trades": trades_to_update,
                    "be_candidates": be_candidates,
                    "sl_tp_changed": bool(new_sl is not None or (new_tp_list and isinstance(new_tp_list, list))),
                    "close_percentage": close_percentage if partial_close else None,
                }

            return {"action": "IGNORE", "reason": "Nessuna modifica applicabile (nessun trade idoneo)."}

        # 3. CHIUSURA SEGNALE (per ora sempre chiusura TOTALE della catena)
        elif intent == "CLOSE_SIGNAL":
            # Stessa logica di targeting dell'UPDATE: 'reply_to' punta al messaggio
            # COMPLETO dell'operazione; in mancanza, ricadiamo sull'ultima operazione
            # ancora a mercato. Non usiamo mai msg_id del messaggio di chiusura:
            # quel messaggio non è un'operazione.
            target_msg_id = self._explicit_target(msg_id, reply_to)

            if not target_msg_id:
                latest_trade = self._get_latest_trade()
                if latest_trade and latest_trade.get("status") in CLOSABLE_STATUSES:
                    target_msg_id = latest_trade["msg_id"]

            if not target_msg_id or target_msg_id not in self.active_trades:
                return {"action": "IGNORE", "reason": "Nessuna operazione a mercato da chiudere."}

            target_trade = self.active_trades[target_msg_id]
            root_id = target_trade.get("root_msg_id", target_trade.get("msg_id"))

            # Raccogliamo tutta la famiglia collegata (originale + re-entry).
            # Lo stato diventa CLOSING, non CLOSED: la chiusura è confermata solo
            # dall'esito reale restituito da MT5 (vedi telegram_listener.py).
            closing_chain = []
            for trade in self.active_trades.values():
                if trade.get("root_msg_id", trade.get("msg_id")) == root_id and trade.get("status") in CLOSABLE_STATUSES:
                    trade["status"] = "CLOSING"
                    closing_chain.append(trade)

            if not closing_chain:
                return {"action": "IGNORE", "reason": "Nessuna operazione a mercato da chiudere."}

            self.save_state_to_file()
            logger.info(f"🔒 [CLOSE_SIGNAL] Root ID {root_id}: {len(closing_chain)} operazioni da chiudere (Originale + Re-entry).")

            return {
                "action": "CLOSE",
                "trade": target_trade,
                "closed_chain": closing_chain
            }

        return None