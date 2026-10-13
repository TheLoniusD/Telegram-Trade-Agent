# Telegram Trade Agent — istruzioni per Claude

Bot di copy trading: legge i messaggi del canale Telegram **Maestro Fx** (un trader che opera su oro, **XAUUSD**), li fa classificare a un agente (Claude Haiku) e replica le operazioni su **MetaTrader 5**, su un **conto DEMO**. Il proprietario scrive in italiano: rispondi sempre in italiano.

Le sessioni sono di due tipi:
- **sessione di analisi e sviluppo** (cloud): analizza i log, corregge il codice, prepara i prompt per il server;
- **sessione sul server** (Linux dove gira il bot): aggiorna il codice, riavvia il bot, carica i log, lancia il rigioco. Vedi *Sessione sul server* in fondo.

## Prima di tutto

- **`doc/DECISIONI.md`** è la fonte delle regole di trading in vigore e del perché sono state scelte (Regole in vigore, Risultati per giornata, Registro, Questioni aperte). Leggilo prima di proporre o cambiare qualsiasi regola: una proposta non deve contraddire una scelta già fatta, o deve farlo consapevolmente, dicendolo.
- Le regole non vanno ricopiate altrove: se servono, si leggono lì.

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
| `prezzi.py` | analisi dei prezzi di un giorno e `rigioco` delle strategie (servono i prezzi di MT5: gira solo sul server) |
| `tests/` | banco di prova con broker finto (vedi *Verifica*) |

I parametri si leggono da `.env` con `os.getenv(NOME, default)`. **Il default nel codice è la regola in vigore**: una regola nuova si mette cambiando il default, mai chiedendo di modificare il `.env`.

## Branch e log

- **`main`**: sviluppo, codice per Windows (`import MetaTrader5`).
- **`server`**: `main` + `mt5_connection.py` (RPyC verso MT5 sotto Wine) + `order_send(**request)` + i **log** in `logs/`. Non si sviluppa su `server`: ci si porta `main` con un merge.
- I log esistono solo sul branch `server` (su `main` `logs/`, `storico/` e `active_trades.json` sono ignorati). Si leggono senza cambiare branch:
  `git fetch origin server` poi `git show origin/server:logs/AAAA-MM-GG/bot.log`
- Per giorno, in `logs/AAAA-MM-GG/`: `bot.log` (racconto, ora italiana), `operazioni.txt` (schede), `errors.log`, `dettagli.log`, `journal.jsonl`. L'orologio del broker è UTC+3 (campi `broker_time` nel diario). I log fino al 05/10 hanno un formato più vecchio.

## Come lavorare con il proprietario

1. **Solo analisi richiesta** ("analizziamo", "non modificare codice") → nessuna modifica al codice.
2. **Bug trovato** → correggi su `main`, aggiungi un test che riproduce il bug (fallisce prima, passa dopo), lancia la verifica, mostra cosa cambia e il risultato dei test. **Pubblica solo dopo l'ok esplicito** ("ok, pusha"). I promemoria automatici sui commit non pubblicati non sostituiscono l'ok.
3. **Regole di trading** (ingresso, pareggio, incassi, rischio…) → solo proposte finché il proprietario non approva. Ogni proposta dice: cosa cambia, cosa si toglie, cosa resta disponibile, le **controindicazioni**, e i dati su cui si basa (rigioco, log). Dopo l'approvazione aggiorna `doc/DECISIONI.md` nello stesso push (Regole in vigore, tabella parametri, nuova voce in cima al Registro con decisione, motivo con i numeri, alternative scartate, cosa verificare).
4. **Mai interventi manuali per il proprietario**: niente da fare a mano sul server o nel `.env`; tutto deve funzionare con il codice e con il prompt per il server.
5. **Dopo l'ok**: verifica → push di `main` → merge di `main` in `server` (le differenze RPyC del server restano) → verifica di nuovo → push di `server` → prompt per la sessione sul server (skill `prompt-server`).

## Verifica (obbligatoria)

- Dopo **ogni** modifica al codice: `python3 tests/run_all.py` (su Windows `python`). Deve finire con "VERIFICA RIUSCITA".
- Comportamento nuovo o corretto → nuovo scenario (o controllo) in `tests/test_*.py`, usando `tests/banco.py`. Mai indebolire o togliere un controllo per far passare la verifica senza dirlo al proprietario.
- Il banco di prova usa un broker finto (`tests/fake_mt5.py`), una cartella temporanea e i default del codice: non legge il `.env`, non tocca log, `active_trades.json` o MT5 veri, quindi si può lanciare anche sul server.
- Un hook (`.claude/settings.json`) lancia la verifica prima di ogni `git push` e lo **blocca** se fallisce.
- `prezzi.py` non è coperto dai test (serve MT5): dopo una modifica almeno compila (lo fa `run_all.py`) e va provato con il rigioco sul server.

## Regole fisse di sicurezza

- Solo conto **DEMO**: mai `ALLOW_REAL_ACCOUNT=1`, mai togliere il controllo in `risk_manager.py`.
- Il `.env` non si stampa, non si mostra, non si committa.
- `active_trades.json` non si cancella e non si modifica a mano.
- Git: mai `push --force`, mai `pull --rebase`, mai `reset` su `main` o `server`; si fa pull con merge e poi push.
- Il bot non va mai avviato da una sessione cloud; il PC fisso e il server non devono mai girare insieme con la stessa sessione Telegram.
- Sul server tutto come utente `trader`, mai root per i file del progetto.

## Skill del progetto (`.claude/skills/`)

- `analisi-log` — analisi di una giornata di log (procedura completa e formato della risposta).
- `rigioco` — lanciare e leggere il rigioco delle strategie.
- `prompt-server` — scrivere il prompt per la sessione sul server.

## Sessione sul server

Se sei la sessione che gira sul server del bot (cartella del progetto sul branch `server`, MT5 sotto Wine):
- lavori come utente `trader`; valgono tutte le regole fisse qui sopra;
- il bot gira come **servizio systemd**: il riavvio si fa con `systemctl restart` sul suo servizio, mai lanciando `telegram_listener.py` a mano accanto al servizio. Nome del servizio e comando esatto: *da completare* (trovali con `systemctl list-units --type=service --all | grep -i -E 'trade|bot|telegram'` e, se è un servizio utente, `systemctl --user list-units --type=service --all`; poi riportali nella risposta perché vengano scritti qui); gli errori di avvio si leggono con `journalctl -u <servizio> -n 50`;
- **aggiornamento del codice**: `git status` (log nuovi → commit "Log aggiornati") → `git pull --no-rebase origin server` → `python3 tests/run_all.py` (se fallisce non riavviare: riporta l'errore) → riavvio del servizio → controlla che giri **un solo** processo (`pgrep -af telegram_listener`) e che nel `bot.log` di oggi ci sia "Bot avviato · conto DEMO";
- **caricamento dei log**: `git add logs && git commit -m "Log aggiornati" && git pull --no-rebase origin server && git push origin server`;
- **rigioco**: vedi la skill `rigioco`;
- non modificare il codice: le correzioni si fanno su `main` dalla sessione di analisi; se trovi un problema, riportalo con l'errore esatto.
