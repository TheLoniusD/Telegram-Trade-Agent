---
name: prompt-server
description: Scrivere il prompt per la sessione Claude sul server del bot (aggiornamento del codice, riavvio, caricamento log, rigioco). Da usare dopo ogni push che il server deve ricevere, o quando serve che il server esegua qualcosa.
---

# Prompt per la sessione sul server

La sessione sul server è **nuova ogni volta**: non sa niente di questa conversazione. Ha il `CLAUDE.md` del branch `server` (con la sezione *Sessione sul server*), ma il prompt deve funzionare anche da solo. Il proprietario lo copia e incolla: niente da fare a mano per lui.

## Regole per scriverlo

- **In italiano**, in un unico blocco di codice, pronto da copiare.
- Ripeti **sempre** le regole fisse: sono poche righe e proteggono dagli errori di una sessione che non conosce la storia del progetto.
- Passi **numerati**, ognuno con il comando esatto e il risultato atteso, verificabile (es. "`grep -n 'BE_OFFSET = ' telegram_listener.py` deve mostrare 1.0").
- **Verifica prima del riavvio**: `python3 tests/run_all.py` dopo il pull; se fallisce, non riavviare e riporta l'errore.
- Niente modifiche al codice sul server; niente modifiche al `.env` se non con un comando preciso che non stampi i valori (es. commentare una riga con `sed`, controllando con `grep -c`).
- In fondo chiedi una **risposta precisa**: commit in cima dopo il pull, esito della verifica, valori controllati, riga di avvio del `bot.log`, numero di processi, esito del push.
- Dopo il prompt, scrivi al proprietario cosa deve fare lui: di solito niente oltre a incollarlo.

## Modello

```
Sei sul server Linux del bot di copy trading Telegram → MetaTrader5 (repo Telegram-Trade-Agent, branch "server"). Sessione nuova: segui queste istruzioni e la sezione "Sessione sul server" del CLAUDE.md.

REGOLE FISSE (sempre valide)
- Lavora come utente "trader", mai root, per qualsiasi file del progetto.
- Non cancellare né modificare active_trades.json.
- Non stampare, mostrare o committare il file .env (né parti dei valori).
- Mai "git push --force", mai "git pull --rebase", mai "git reset". Solo pull con merge, poi push.
- Il bot deve girare in un solo processo.
- Il conto MT5 è e deve restare DEMO.
- Non modificare il codice: se qualcosa non va, riporta l'errore esatto.

COSA FARE
1. Nella cartella del progetto: git status. Se ci sono log nuovi non committati: git add logs && git commit -m "Log aggiornati". Poi: git pull --no-rebase origin server.
2. Verifica: git log --oneline -3 deve contenere "<titolo del commit>". <controlli specifici della modifica>
3. python3 tests/run_all.py → deve finire con "VERIFICA RIUSCITA". Se fallisce, NON riavviare il bot e riporta l'uscita.
4. Riavvia il servizio systemd del bot (vedi CLAUDE.md, sezione "Sessione sul server"). pgrep -af telegram_listener deve mostrare UN solo processo; in logs/<data di oggi>/bot.log deve esserci una nuova riga "Bot avviato · conto DEMO".
5. <eventuali passi extra: rigioco, controlli sul .env senza stamparlo>
6. git add logs && git commit -m "Log aggiornati" && git pull --no-rebase origin server && git push origin server (mai forzato).

Rispondi con: commit in cima dopo il pull, esito della verifica, <valori controllati>, riga di avvio del bot.log, numero di processi del bot, esito del push.
```
