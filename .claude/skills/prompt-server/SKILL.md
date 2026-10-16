---
name: prompt-server
description: Scrivere il prompt per la sessione Claude sul server del bot (rilascio di una nuova versione con riavvio, caricamento log, rigioco, controllo dello stato). Da usare dopo ogni push che il server deve ricevere, o quando serve che il server esegua qualcosa.
---

# Prompt per la sessione sul server

La sessione sul server è **nuova ogni volta**: non sa niente di questa conversazione. Ha il **CLAUDE.md del branch `server`** (`git show origin/server:CLAUDE.md`), con le procedure *Controllo dello stato*, *Caricamento dei log*, *Rilascio* e *Rigioco* e i *Dati del server* (cartella, servizio systemd, comando Python). Il prompt richiama la procedura giusta, ma deve funzionare anche da solo. Il proprietario lo copia e incolla: niente da fare a mano per lui.

## Regole per scriverlo

- **In italiano**, in un unico blocco di codice, pronto da copiare.
- Ripeti **sempre** le regole fisse: sono poche righe e proteggono dagli errori di una sessione che non conosce la storia del progetto.
- Passi **numerati**, ognuno con il comando esatto e il risultato atteso, verificabile (es. "`grep -n 'BE_OFFSET = ' telegram_listener.py` deve mostrare 1.0").
- I log si aggiungono con **`git add -f logs`**: `logs/` è nel `.gitignore` e senza `-f` le cartelle dei giorni nuovi non entrano.
- **Verifica prima del riavvio**: `tests/run_all.py` dopo il pull; se fallisce, non riavviare e riporta l'errore.
- Il riavvio serve solo se cambia il codice del bot (o `requirements.txt`, che va installato prima). Se cambiano solo documenti, skill o test basta il caricamento dei log, che fa anche il pull.
- Niente modifiche al codice sul server; niente modifiche al `.env` se non con un comando preciso che non stampi i valori (es. commentare una riga con `sed`, controllando con `grep -c`).
- In fondo chiedi una **risposta precisa**: commit in cima dopo il pull, esito della verifica, valori controllati, riga di avvio del `bot.log`, numero di processi, esito del push.
- Dopo il prompt, scrivi al proprietario cosa deve fare lui: di solito niente oltre a incollarlo.

## Modello (rilascio)

```
Sei sul server Linux del bot di copy trading Telegram → MetaTrader5 (repo Telegram-Trade-Agent, branch "server"). Sessione nuova: segui queste istruzioni insieme al CLAUDE.md della cartella del progetto (procedura "Rilascio").

REGOLE FISSE (sempre valide)
- Lavora come utente "trader", mai root, per qualsiasi file del progetto.
- Non cancellare né modificare active_trades.json.
- Non stampare, mostrare o committare il file .env (né parti dei valori).
- Mai "git push --force", mai "git pull --rebase", mai "git reset". Solo pull con merge, poi push.
- Il bot gira solo come servizio systemd, in un solo processo: mai avviare telegram_listener.py a mano.
- Il conto MT5 è e deve restare DEMO.
- Non modificare il codice: se qualcosa non va, riporta l'errore esatto.
- Non chiedere né accettare token, password o chiavi private: se il push fallisce per le credenziali, fermati e riporta l'errore.

COSA FARE
1. Nella cartella del progetto: controllo dello stato del bot (CLAUDE.md, "Controllo dello stato") e annota il commit attuale (git rev-parse --short HEAD). Poi: git status --short (solo righe sotto logs/); git add -f logs; git commit -m "Log aggiornati" (se c'è qualcosa); git pull --no-rebase origin server.
2. Verifica: git log --oneline -3 deve contenere "<titolo del commit>". <controlli specifici della modifica>
3. git diff --name-only <commit annotato> HEAD -- requirements.txt: se compare, installa i pacchetti con il Python del servizio (CLAUDE.md, "Dati del server") e riporta cosa è stato installato.
4. Verifica: <python> tests/run_all.py → deve finire con "VERIFICA RIUSCITA". Se fallisce, NON riavviare il bot e riporta l'uscita.
5. Riavvia il servizio systemd del bot (nome nei "Dati del server" del CLAUDE.md). Dopo circa 30 secondi: servizio attivo, pgrep -af telegram_listener mostra UN solo processo, in logs/<data di oggi, ora italiana>/bot.log c'è una nuova riga "▶️ Bot avviato · conto DEMO".
6. <eventuali passi extra: rigioco, controlli sul .env senza stamparlo>
7. git add -f logs && git commit -m "Log aggiornati" && git pull --no-rebase origin server && git push origin server (mai forzato).

Rispondi con: commit in cima dopo il pull, esito della verifica, <valori controllati>, riga di avvio del bot.log, numero di processi del bot, esito del push.
```

## Modello (solo caricamento dei log, o solo pull di documenti e skill)

```
Sei sul server Linux del bot di copy trading Telegram → MetaTrader5 (repo Telegram-Trade-Agent, branch "server"). Sessione nuova: segui il CLAUDE.md della cartella del progetto, procedure "Controllo dello stato" e "Caricamento dei log". Non riavviare il bot.

REGOLE FISSE (sempre valide)
<le stesse del modello sopra>

Rispondi con: stato del bot, giorni caricati, commit in cima dopo il pull, esito del push.
```
