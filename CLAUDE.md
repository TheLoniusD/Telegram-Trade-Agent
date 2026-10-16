# Telegram Trade Agent — istruzioni per le sessioni sul PC (branch `main`)

Bot di copy trading: legge i messaggi del canale Telegram **Maestro Fx** (un trader che opera su oro, **XAUUSD**), li fa classificare a un agente (Claude Haiku) e replica le operazioni su **MetaTrader 5**, su un **conto DEMO**. Il proprietario scrive in italiano: rispondi sempre in italiano.

## Chi sei

Sei una **sessione sul PC del proprietario** (Windows, cartella del progetto sul branch `main`). Servi a:
- **analizzare i log** che il bot produce ogni giorno sul server;
- **correggere la logica del bot e i bug**, con test e verifica;
- **preparare i rilasci**: unire `main` in `server` e scrivere il prompt per la sessione sul server.

Il bot **non gira qui**: gira da solo sul server Linux (branch `server`). Lì lavora un'altra sessione Claude, con un **CLAUDE.md suo** (`git show origin/server:CLAUDE.md`), che rilascia il codice preparato qui, riavvia il bot e carica i log. Le due sessioni non si parlano: si passano il lavoro con git e con il prompt che scrivi tu (skill `prompt-server`).

## Prima di tutto

- **`doc/DECISIONI.md`** è la fonte delle regole di trading in vigore e del perché sono state scelte (Regole in vigore, Risultati per giornata, Registro, Questioni aperte). Leggilo prima di proporre o cambiare qualsiasi regola: una proposta non deve contraddire una scelta già fatta, o deve farlo consapevolmente, dicendolo.
- Le regole non vanno ricopiate altrove: se servono, si leggono lì.
- A inizio sessione: `git status` e `git pull --no-rebase origin main` (il `main` locale può essere rimasto indietro), poi `git fetch origin server` per avere gli ultimi log.

## Architettura

| File | Ruolo |
|---|---|
| `telegram_listener.py` | processo principale: riceve i messaggi, esegue segnali, Trade Active, pareggio con gradini di protezione, chiusure; `monitor_loop` ogni 30 s (ordini eseguiti, posizioni orfane, riconciliazione, SL mancanti, pareggi in attesa) |
| `agent_classifier.py` | prompt e chiamata all'agente (tool `classify_signal`): NEW_SIGNAL / UPDATE_SIGNAL / CLOSE_SIGNAL / IGNORE |
| `order_manager.py` | memoria delle operazioni (`active_trades.json`), catene di messaggi, re-entry, archivio `storico/` |
| `risk_manager.py` | lotti (rischio 2% con `order_calc_profit`), livelli d'ingresso, rifiuto se il conto non è DEMO |
| `mt5_executor.py` | ordini a MT5, magic number 990011; ogni richiesta finisce nel diario |
| `journal.py` | `journal.jsonl`: **unica fonte dei fatti** (eventi con dati esatti) |
| `narrative.py` | dal diario: `bot.log` (racconto) e `operazioni.txt` (schede per operazione) |
| `prezzi.py` | analisi dei prezzi di un giorno e `rigioco` delle strategie (servono i prezzi di MT5: si lancia sul server) |
| `verifica_be.py` | rigioco delle posizioni di un giorno sulle candele M1 di MT5 (anche questo sul server) |
| `tests/` | banco di prova con broker finto (vedi *Verifica*) |

I parametri si leggono da `.env` con `os.getenv(NOME, default)`. **Il default nel codice è la regola in vigore**: una regola nuova si mette cambiando il default, mai chiedendo di modificare il `.env`.

## Branch e log

- **`main`**: sviluppo, codice per Windows (`import MetaTrader5`). Si lavora qui.
- **`server`**: `main` + `mt5_connection.py` (RPyC verso MT5 sotto Wine) + `order_send(**request)` + `requirements.txt` con `rpyc` + i **log** in `logs/` + il **CLAUDE.md del server**. Non si sviluppa su `server`: ci si porta `main` con un merge (vedi *Rilascio*).
- I log esistono solo sul branch `server` (su `main` `logs/`, `storico/` e `active_trades.json` sono ignorati). Si leggono senza cambiare branch:
  `git fetch origin server` poi `git show origin/server:logs/AAAA-MM-GG/bot.log` (in Git Bash vedi *Ambiente del PC*).
- Per giorno, in `logs/AAAA-MM-GG/`: `bot.log` (racconto, ora italiana), `operazioni.txt` (schede), `errors.log`, `dettagli.log`, `journal.jsonl`. L'orologio del broker è UTC+3 (campi `broker_time` nel diario). I log fino al 05/10 hanno un formato più vecchio. In `logs/` ci sono anche i risultati del rigioco (`rigioco_tick.txt`, `rigioco_tick_dettaglio.txt`).
- I log arrivano su `server` quando la sessione sul server li carica. Se la giornata richiesta non c'è (`git ls-tree --name-only origin/server logs/`), dillo e prepara il prompt per caricarli (skill `prompt-server`).

## Ambiente del PC (Windows)

- Python si lancia con **`python`**: `python3` qui è il collegamento del Microsoft Store e non funziona. Nelle skill i comandi sono scritti con `python3` (vale sul server): sul PC usa `python`.
- `.claude/settings.json` imposta `PYTHONUTF8=1`: senza, gli script che stampano simboli (→, −, emoji) si fermano con `UnicodeEncodeError` sulla console di Windows. Se lanci Python fuori da una sessione: `python -X utf8 ...`.
- **Git Bash** trasforma `origin/server:logs/...` in un percorso di Windows e `git show` fallisce: metti `MSYS_NO_PATHCONV=1` davanti (`MSYS_NO_PATHCONV=1 git show origin/server:logs/2026-10-13/bot.log`) oppure usa PowerShell. Gli script delle skill (`riepilogo.py`, `confronta.py`) non hanno il problema.
- File temporanei (estratti dei log, dettaglio del rigioco…) nella cartella scratchpad della sessione, non in `/tmp`.
- Nella cartella ci sono il `.env` e la sessione Telegram (`*.session`): **mai avviare il bot** (`telegram_listener.py`) né altro che si colleghi a Telegram. Il server usa la stessa sessione Telegram e i due non devono mai girare insieme.
- Il pacchetto `MetaTrader5` è installato, ma i test non lo usano (broker finto) e gli script che leggono i prezzi veri (`prezzi.py`, `verifica_be.py`) si lanciano sul server.

## Come lavorare con il proprietario

1. **Solo analisi richiesta** ("analizziamo", "non modificare codice") → nessuna modifica al codice. Per una giornata di log: skill `analisi-log`.
2. **Bug trovato** → correggi su `main`, aggiungi un test che riproduce il bug (fallisce prima, passa dopo), lancia la verifica, mostra cosa cambia e il risultato dei test. **Pubblica solo dopo l'ok esplicito** ("ok, pusha"). I promemoria automatici sui commit non pubblicati non sostituiscono l'ok.
3. **Regole di trading** (ingresso, pareggio, incassi, rischio…) → solo proposte finché il proprietario non approva. Ogni proposta dice: cosa cambia, cosa si toglie, cosa resta disponibile, le **controindicazioni**, e i dati su cui si basa (rigioco, log). Dopo l'approvazione aggiorna `doc/DECISIONI.md` nello stesso push (Regole in vigore, tabella parametri, nuova voce in cima al Registro con decisione, motivo con i numeri, alternative scartate, cosa verificare) e porta la regola anche in `prezzi.py` (skill `rigioco`).
4. **Mai interventi manuali per il proprietario**: niente da fare a mano sul server o nel `.env`; tutto deve funzionare con il codice e con il prompt per il server.
5. **Dopo l'ok** → *Rilascio*.

## Verifica (obbligatoria)

- Dopo **ogni** modifica al codice: `python tests/run_all.py`. Deve finire con "VERIFICA RIUSCITA".
- Comportamento nuovo o corretto → nuovo scenario (o controllo) in `tests/test_*.py`, usando `tests/banco.py`. Mai indebolire o togliere un controllo per far passare la verifica senza dirlo al proprietario.
- Il banco di prova usa un broker finto (`tests/fake_mt5.py`, messo da `tests/banco.py` al posto sia di `MetaTrader5` sia di `mt5_connection`), una cartella temporanea e i default del codice: non legge il `.env`, non tocca log, `active_trades.json` o MT5 veri. Funziona allo stesso modo sul codice di `main` e su quello di `server`.
- Un hook (`.claude/settings.json`) lancia la verifica prima di ogni `git push`, da Bash o da PowerShell, e lo **blocca** se fallisce.
- `prezzi.py` non è coperto dai test (serve MT5): dopo una modifica almeno compila (lo fa `run_all.py`) e va provato con il rigioco sul server.

## Rilascio (solo dopo l'ok esplicito)

1. `python tests/run_all.py` → commit su `main` → `git push origin main`.
2. Merge di `main` in `server`, nella cartella del progetto (prima tutto committato, `git status` pulito):
   ```
   git checkout server
   git pull --no-rebase origin server
   git merge --no-ff --no-commit main
   git checkout HEAD -- CLAUDE.md
   python tests/run_all.py
   git commit -m "Merge branch 'main' into server"
   git push origin server
   git checkout main
   ```
   - `git checkout HEAD -- CLAUDE.md` tiene il **CLAUDE.md del server** com'è, anche se il merge l'avrebbe sovrascritto con questo o è andato in conflitto. Se il CLAUDE.md del server va cambiato, si fa con un commit a parte sul branch `server`, mai portandoci questo.
   - In caso di conflitti sul codice restano le differenze del server: `from mt5_connection import mt5`, `mt5.order_send(**request)`, `rpyc` in `requirements.txt` al posto di `MetaTrader5`.
   - Su `server` compare la cartella `logs/` (è tracciata lì) e sparisce tornando su `main`: è normale. Non modificare né committare log da qui.
   - Se `main` cambia `requirements.txt`, il prompt per il server deve far installare il nuovo pacchetto (sul server non c'è `MetaTrader5`, c'è `rpyc`).
3. Prompt per la sessione sul server (skill `prompt-server`): rilascio con riavvio se cambia il codice del bot; se cambiano solo documenti, skill o test basta che il server faccia pull (lo fa anche caricando i log).
4. Alla prima analisi successiva controlla nel `bot.log` la riga "▶️ Bot avviato · conto DEMO" della nuova versione e le *Questioni aperte* "da verificare".

## Regole fisse di sicurezza

- Solo conto **DEMO**: mai `ALLOW_REAL_ACCOUNT=1`, mai togliere il controllo in `risk_manager.py`.
- Il `.env` non si stampa, non si mostra, non si committa.
- `active_trades.json` non si cancella e non si modifica a mano.
- Git: mai `push --force`, mai `pull --rebase`, mai `reset` su `main` o `server`; si fa pull con merge e poi push.
- Il bot non si avvia mai dal PC: il PC e il server non devono mai girare insieme con la stessa sessione Telegram.
- Sul server si interviene solo con il prompt per la sessione sul server, mai chiedendo al proprietario comandi a mano.
- Credenziali (token GitHub, password, chiavi private): mai chiederle al proprietario né farle passare in chat o nei prompt. Il server pubblica su GitHub con la sua chiave di deploy SSH; se un suo push fallisce per le credenziali, si sistema la configurazione del server con un prompt, non con un token.

## Skill del progetto (`.claude/skills/`)

- `analisi-log` — analisi di una giornata di log (procedura completa e formato della risposta).
- `rigioco` — lanciare (sul server) e leggere (qui) il rigioco delle strategie.
- `prompt-server` — scrivere il prompt per la sessione sul server.
