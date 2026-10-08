import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
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

# Re-entry ("Try buy again", "Lets try sell again"): non ha prezzo né livelli
# e il trader non lo corregge mai con SL/TP, quindi eredita tutto dall'ultima
# operazione classica nella stessa direzione. Di solito è ancora aperta; se nel
# frattempo si è chiusa (es. dopo un "Hit risk") la cerchiamo nello storico, ma
# solo se aperta da non più di un'ora: oltre, il rischio di ereditare i livelli
# di un'operazione che non c'entra è maggiore del vantaggio.
REENTRY_PARENT_MAX_AGE_SECONDS = 3600

# Ticket aperti per ogni segnale, alternati sui TP del trader: e1 sul TP1, e2
# sul TP2 (con 4 ticket anche e3/e4 più dentro la zona, vedi
# RiskManager._entry_levels). Dal 06/10 sono 2, con tutto il rischio: nel
# rigioco 24/09–05/10 e3/e4 aggiungevano rischio senza rendere nulla (entrano
# quasi solo quando il prezzo va contro); solo e1/e2 con lotti doppi ≈ +430 $
# contro +214 $ delle regole del 05/10.
ENTRY_TICKETS = max(1, int(os.getenv("ENTRY_TICKETS", "2")))

# TP provvisori finché il trader non scrive i suoi, come lo SL provvisorio:
# TP1 e TP2 a questa distanza dal prezzo del segnale (la struttura che il
# trader usa sempre: TP a 10 e 20 $). Senza, un segnale mai completato restava
# aperto con il solo SL (06/10 BUY 4158 allo stop; 07/10 SELL 4133 aperto per
# 6 ore, chiuso a mano). "0" = nessun TP provvisorio.
PROVISIONAL_TP_DISTANCES = [float(x) for x in os.getenv("PROVISIONAL_TP", "10,20").split(",") if float(x) > 0]


def ticket_tp_index(key: str, ticket: dict) -> int:
    """Indice del TP del trader assegnato al ticket (0 = TP1, 1 = TP2)."""
    if ticket.get("tp_index") is not None:
        return ticket["tp_index"]
    # Operazioni aperte prima dei 4 ticket: chiavi tp1/tp2
    return 1 if key == "tp2" else 0


def first_ticket(trade: Optional[dict]) -> dict:
    """Primo ticket dell'operazione (simbolo e direzione sono uguali per tutti)."""
    tickets = (trade or {}).get("tickets") or {}
    return next(iter(tickets.values()), {})


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
        self.missing_stops = []
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

        logger.debug(f"🗄️ Archiviate {len(terminali)} operazioni concluse in {percorso}")
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
        # Posizioni che su MT5 risultano senza SL pur avendolo in memoria: il
        # listener lo rimette (vedi restore_missing_stops).
        self.missing_stops = []

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
                    logger.debug(f"🔄 [RICONCILIAZIONE] Ticket {mt5_ticket} non più aperto su MT5: segnato come chiuso.")
                    continue

                if not state.get("stop_loss") and tp_config.get("stop_loss"):
                    self.missing_stops.append((trade, tp_config))

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
                logger.debug(f"🔄 [RICONCILIAZIONE] Operazione {trade.get('ticket_id')} risulta chiusa su MT5.")

        if corrections:
            self.save_state_to_file()

        return corrections


    def adopt_orphans(self, positions: list) -> tuple:
        """
        Posizioni aperte dal bot su MT5 che la memoria considera chiuse,
        cancellate o non segue più: tornano in gestione (pareggio, Trade Active,
        registrazione della chiusura). Il 05/10 tre ordini eseguiti erano stati
        segnati come cancellati, l'08/10 un ticket come chiuso: in entrambi i
        casi la posizione è rimasta aperta senza controllo. Cerca il ticket fra
        le operazioni vive e, se serve, nello storico di oggi e di ieri (e in
        quel caso rimette l'operazione fra quelle vive).
        Ritorna (riprese: [(operazione, chiave, posizione)], sconosciute: [posizione]).
        """
        tracked = {t.get("mt5_ticket") for trade in self.active_trades.values()
                   if trade.get("status") in CLOSABLE_STATUSES
                   for t in trade.get("tickets", {}).values()
                   if t.get("mt5_ticket") and not t.get("closed") and not t.get("pending")}
        adopted, unknown, archive = [], [], None

        def owner_in(trades):
            for trade in trades:
                for key, t in (trade.get("tickets") or {}).items():
                    if t.get("mt5_ticket") == pos["ticket"]:
                        return trade, key
            return None

        for pos in positions:
            if pos["ticket"] in tracked:
                continue
            found = owner_in(self.active_trades.values())
            if found is None:
                if archive is None:
                    archive = self._recent_archive()
                found = owner_in(reversed(archive))
                if found is not None:
                    record = dict(found[0])
                    record.pop("archived_at", None)
                    msg_id = record.get("msg_id")
                    self.active_trades[msg_id] = record
                    found = (record, found[1])
            if found is None:
                unknown.append(pos)
                continue
            trade, key = found
            ticket = trade["tickets"][key]
            ticket.update(closed=False, pending=False, entry_price=pos["price_open"], volume=pos["volume"])
            if pos.get("stop_loss"):
                ticket["stop_loss"] = pos["stop_loss"]
            if pos.get("take_profit"):
                ticket["take_profit"] = pos["take_profit"]
            trade["status"] = "ACTIVE"
            adopted.append((trade, key, pos))
            logger.warning(f"⚠️ [ORFANA] Posizione {pos['ticket']} ({key}) aperta su MT5 ma non seguita: ripresa in gestione.")
        if adopted:
            self.save_state_to_file()
        return adopted, unknown


    def _get_latest_trade(self) -> Optional[dict]:
        """Recupera l'operazione più recente contrassegnata come attiva."""
        if self.latest_msg_id and self.latest_msg_id in self.active_trades:
            return self.active_trades[self.latest_msg_id]
        return None


    def _recent_archive(self) -> list:
        """Operazioni archiviate oggi e ieri (storico/trades_AAAA-MM-GG.jsonl)."""
        trades = []
        for day in (datetime.now() - timedelta(days=1), datetime.now()):
            path = os.path.join(self.archive_dir, f"trades_{day.strftime('%Y-%m-%d')}.jsonl")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        trades.append(json.loads(line))
                    except ValueError:
                        continue
        return trades


    def _find_reentry_parent(self, symbol: Optional[str], direction: Optional[str]) -> Optional[dict]:
        """
        Operazione da cui un re-entry eredita i valori: la più recente con la
        stessa direzione (e simbolo, se indicato), prima fra quelle ancora in
        memoria, poi nello storico di oggi e di ieri se chiusa da poco.
        """
        def matches(trade):
            ticket = first_ticket(trade)
            return ((direction is None or ticket.get("direction") == direction)
                    and (symbol is None or ticket.get("symbol") == symbol))

        live = [t for t in self.active_trades.values() if t.get("status") in CLOSABLE_STATUSES and matches(t)]
        if live:
            return max(live, key=lambda t: t.get("created_at") or 0)

        now = time.time()
        archived = [t for t in self._recent_archive() if t.get("status") == "CLOSED" and matches(t)
                    and now - (t.get("created_at") or 0) <= REENTRY_PARENT_MAX_AGE_SECONDS]
        return max(archived, key=lambda t: t.get("created_at") or 0) if archived else None


    def _was_closed(self, msg_id: int) -> bool:
        """True se il segnale di questo messaggio è già stato aperto e poi chiuso."""
        trade = self.active_trades.get(msg_id)
        if trade:
            return trade.get("status") == "CLOSED"
        return any(t.get("msg_id") == msg_id and t.get("status") == "CLOSED" for t in self._recent_archive())


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

    # Helper per cercare il valore prima su root, poi dentro il primo ticket
    def _get_param(self, latest_trade, key, ticket_field=None):
        if not latest_trade:
            return None
        # 1. Cerca a livello root nel vecchio trade
        if latest_trade.get(key) is not None:
            return latest_trade[key]
        # 2. Fallback sul primo ticket
        return first_ticket(latest_trade).get(ticket_field or key)


    def _explicit_target(self, msg_id: int, reply_to: Optional[int]) -> Optional[int]:
        """
        Operazione a cui si riferisce esplicitamente un messaggio di update o
        chiusura: quella a cui risponde (reply), oppure, se è l'EDIT di un
        messaggio di segnale, quel segnale stesso. Senza riferimento esplicito
        il chiamante ricade sull'ultima operazione aperta: prima anche l'edit
        con SL/TP di un segnale più vecchio finiva sull'ultimo aperto.
        """
        for ref in (reply_to, msg_id):
            if not ref:
                continue
            if ref in self.active_trades:
                return ref
            # Il segnale padre è già chiuso e archiviato ma un suo re-entry è
            # ancora aperto: "Trade Active", "HIT TP" ecc. rispondono sempre al
            # messaggio classico, e valgono per tutta la sua catena.
            for trade in reversed(list(self.active_trades.values())):
                if trade.get("root_msg_id") == ref:
                    return trade["msg_id"]
        return None


    def _resolve_layer_target(self, trade: dict, layer_target: str) -> list:
        """
        Mappa LOWEST/HIGHEST/ALL sulle chiavi reali dei ticket in base alla
        distanza del take_profit dall'entry price, non all'ordine dei ticket (che dipende
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

        # Con più ticket sullo stesso TP del trader, il layer è l'insieme dei
        # ticket con quel TP (non un ticket solo).
        by_distance = sorted(open_tickets.items(), key=lambda item: distance(item[1]))
        if layer_target == "LOWEST":
            target_tp = by_distance[0][1]["take_profit"]
        elif layer_target == "HIGHEST":
            target_tp = by_distance[-1][1]["take_profit"]
        else:
            return []
        return [key for key, cfg in open_tickets.items() if cfg["take_profit"] == target_tp]


    def handle_agent_output(self, msg_id: int, reply_to: Optional[int], ai_output: dict, is_edit: bool = False) -> Optional[dict]:
        intent = ai_output.get("intent")
        data = ai_output.get("data", {})

        # Modifica di un segnale che il bot non ha mai aperto: il 29/09 alle 09:12
        # il "Gold sell 4144" (msg 58185) è arrivato mentre il bot si riavviava e
        # sono arrivate solo le sue modifiche. È un segnale nuovo, non un
        # aggiornamento di un'altra operazione. Non vale per i segnali già chiusi
        # (una correzione tardiva non deve riaprirli); vale invece per quelli
        # rifiutati o non aperti, così una correzione del trader (es. un prezzo
        # sbagliato) viene eseguita.
        if (is_edit and intent == "UPDATE_SIGNAL" and self._explicit_target(msg_id, reply_to) is None
                and data.get("direction") and data.get("entry_min") is not None
                and not self._was_closed(msg_id)):
            logger.warning(f"🆕 [RECUPERO] Modifica del messaggio {msg_id} mai aperto come segnale: trattata come nuovo segnale.")
            intent = "NEW_SIGNAL"
    
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

            # RE-ENTRY: solo i messaggi "again" senza prezzo, segnalati dall'agente
            # con is_reentry. Un segnale con un prezzo ("Gold sell 4179") è SEMPRE
            # un'operazione nuova e indipendente, anche se vicino a una aperta: il
            # 28/09 veniva collegato al sell 4180 ed ereditava il suo stop, appena
            # spostato a pareggio (4179,5), cioè a 50 centesimi dall'ingresso.
            is_reentry = bool(data.get("is_reentry")) and data.get("entry_min") is None
            source_trade = self._find_reentry_parent(data.get("symbol"), data.get("direction")) if is_reentry else None
            root_msg_id = source_trade.get("root_msg_id", source_trade["msg_id"]) if source_trade else msg_id
            if is_reentry:
                logger.debug(f"🔁 [RE-ENTRY] Collegato all'operazione {source_trade.get('ticket_id') if source_trade else 'nessuna'}"
                            f" (root {root_msg_id}).")

            # Ereditarietà dei dati (solo se source_trade non è None)
            symbol = data.get("symbol") or self._get_param(source_trade, "symbol") or "XAUUSD"
            direction = data.get("direction") or self._get_param(source_trade, "direction")

            # Il re-entry entra a mercato ("try again" = adesso): la zona del padre
            # è già stata superata, ordini limite lì resterebbero ineseguiti.
            entry_min = data.get("entry_min")
            entry_max = data.get("entry_max")

            # Lo stop ereditato è quello scritto dal TRADER per il padre, mai
            # quello attuale del ticket (che dopo il BE è il prezzo d'ingresso).
            # Se nel frattempo è stato superato dal prezzo, il Risk Manager lo
            # sostituisce con quello provvisorio.
            stop_loss = data.get("stop_loss") if data.get("stop_loss") is not None else (source_trade or {}).get("signal_stop_loss")

            # Take Profit del trader (dal messaggio, o ereditati dal padre del re-entry)
            tp_list = data.get("take_profit", [])
            if isinstance(tp_list, list) and len(tp_list) > 0:
                trader_tps = list(tp_list[:2])
            else:
                trader_tps = [None, None]
                for key, ticket in ((source_trade or {}).get("tickets") or {}).items():
                    index = ticket_tp_index(key, ticket)
                    if index < 2 and trader_tps[index] is None:
                        trader_tps[index] = ticket.get("take_profit")
                # Fase rapida ("Gold sell 4133"): TP provvisori dal prezzo del
                # segnale, sostituiti da quelli del trader alla sua modifica.
                if entry_min is not None and PROVISIONAL_TP_DISTANCES:
                    sign = 1 if direction == "BUY" else -1
                    for index in range(2):
                        if trader_tps[index] is None:
                            distance = PROVISIONAL_TP_DISTANCES[min(index, len(PROVISIONAL_TP_DISTANCES) - 1)]
                            trader_tps[index] = round(entry_min + sign * distance, 2)

            tickets = {}
            for i in range(ENTRY_TICKETS):
                tp_index = i % 2
                tickets[f"e{i + 1}"] = {
                    "symbol": symbol,
                    "direction": direction,
                    "entry_price": entry_min,
                    "stop_loss": stop_loss,
                    "tp_index": tp_index,
                    "take_profit": trader_tps[tp_index] if tp_index < len(trader_tps) else None,
                    "volume": None,      # Sarà calcolato dal Risk Manager
                    "mt5_ticket": None,  # Sarà aggiunto dal MT5Executor
                    "success": False,    # true = ordine aperto correttamente su MT5
                    "closed": False,     # true = TP colpito / posizione non più aperta
                    "be_active": False
                }

            trade_record = {
                "ticket_id": ticket_id,
                "msg_id": msg_id,
                "root_msg_id": root_msg_id,
                "entry_min": entry_min,
                "entry_max": entry_max,
                "signal_stop_loss": stop_loss,
                "reentry": is_reentry,
                "status": "ACTIVE",
                "created_at": time.time(),
                "tickets": tickets
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
            # "Trade Active ... Running 60+ Pips ... BE+": una riga di pips per
            # ogni segnale della serie, dal più vecchio; l'ultima è il segnale a
            # cui il messaggio risponde. Non vale per "close half" espliciti.
            running_pips = [p for p in (data.get("running_pips") or []) if p]
            trade_active = bool(move_to_be and running_pips and not partial_close)

            # 1. Identificazione del Trade Bersaglio: 'reply_to' punta sempre al
            #    messaggio COMPLETO (quello con SL/TP impostati), che è la radice
            #    riconosciuta della famiglia di operazioni.
            target_msg_id = self._explicit_target(msg_id, reply_to)

            # La modifica di un messaggio vale solo per quel messaggio: mai
            # ricadere sull'ultima operazione aperta, che non c'entra.
            if not target_msg_id and not is_edit:
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
            layer_closes = []   # ticket del layer indicato dal trader, da chiudere su MT5

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
                    trade["signal_stop_loss"] = new_sl
                    modified = True
                    for tp_config in trade.get("tickets", {}).values():
                        tp_config["stop_loss"] = new_sl

                # C. Aggiornamento Take Profit (fase COMPLETA / ereditarietà re-entry)
                if new_tp_list and isinstance(new_tp_list, list):
                    for tp_key, tp_config in trade.get("tickets", {}).items():
                        index = ticket_tp_index(tp_key, tp_config)
                        if index < len(new_tp_list):
                            tp_config["take_profit"] = new_tp_list[index]
                    modified = True

                # D. Layer indicato dal trader ("Secure first layer now", "close
                #    lowest layer"): la chiusura la fa il listener su MT5, solo se
                #    in guadagno. Mai segnare 'closed' in memoria leggendo il testo:
                #    l'08/10 e1 risultava chiuso ma era ancora aperto su MT5, senza
                #    pareggio né controllo, fino al suo TP. Il mapping è basato
                #    sulla distanza take_profit-entry, non sull'ordine dei ticket.
                if layer_target:
                    for target_key in self._resolve_layer_target(trade, layer_target):
                        layer_closes.append({"trade": trade, "key": target_key})

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

            # Trade Active su più righe: parla anche dei segnali precedenti della
            # stessa direzione. Quelli ancora aperti vanno a pareggio (nessuna
            # chiusura: non sappiamo con certezza quale riga sia di quale).
            if trade_active and len(running_pips) > 1 and trades_to_update:
                direction = first_ticket(trades_to_update[0]).get("direction")
                chain_ids = {id(t) for t in trades_to_update}
                for other in self.active_trades.values():
                    if (id(other) in chain_ids or other.get("status") != "ACTIVE"
                            or first_ticket(other).get("direction") != direction):
                        continue
                    for tp_config in other.get("tickets", {}).values():
                        if tp_config.get("mt5_ticket") and not tp_config.get("be_active") and not tp_config.get("closed"):
                            be_candidates.append({"trade": other, "ticket": tp_config})

            if updated_trades or be_candidates or partial_close or trade_active or layer_closes:
                self.save_state_to_file()
                logger.debug(f"🔄 [UPDATE_SIGNAL] Applicati aggiornamenti a {len(updated_trades)} posizioni, {len(be_candidates)} candidati a BE.")
                return {
                    "action": "UPDATE",
                    "trades": trades_to_update,
                    "be_candidates": be_candidates,
                    "sl_tp_changed": bool(new_sl is not None or (new_tp_list and isinstance(new_tp_list, list))),
                    "sl_changed": new_sl is not None,
                    "close_percentage": close_percentage if partial_close else None,
                    "trade_active_pips": running_pips[-1] if trade_active else None,
                    "layer_closes": layer_closes,
                }

            return {"action": "IGNORE", "reason": "Nessuna modifica applicabile (nessun trade idoneo)."}

        # 3. CHIUSURA SEGNALE (per ora sempre chiusura TOTALE della catena)
        elif intent == "CLOSE_SIGNAL":
            # Stessa logica di targeting dell'UPDATE: 'reply_to' punta al messaggio
            # COMPLETO dell'operazione; in mancanza, ricadiamo sull'ultima operazione
            # ancora a mercato. Non usiamo mai msg_id del messaggio di chiusura:
            # quel messaggio non è un'operazione.
            target_msg_id = self._explicit_target(msg_id, reply_to)

            if not target_msg_id and not is_edit:
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
            logger.debug(f"🔒 [CLOSE_SIGNAL] Root ID {root_id}: {len(closing_chain)} operazioni da chiudere (Originale + Re-entry).")

            return {
                "action": "CLOSE",
                "trade": target_trade,
                "closed_chain": closing_chain
            }

        return None