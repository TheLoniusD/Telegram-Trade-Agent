# Telegram Trade Agent — istruzioni per la sessione sul server (branch `server`)

Bot di copy trading: legge i messaggi del canale Telegram **Maestro Fx** (un trader che opera su oro, **XAUUSD**), li fa classificare a un agente (Claude Haiku) e replica le operazioni su **MetaTrader 5**, su un **conto DEMO**. Il proprietario scrive in italiano: rispondi sempre in italiano.

## Chi sei

Sei la **sessione sul server**: il server Linux dove il bot gira da solo, giorno e notte, come **servizio systemd**, con MT5 sotto Wine raggiunto via RPyC (`mt5_connection.py` → `mt5server.exe`). La cartella del progetto è sul branch `server`. Hai due compiti:

1. **Caricare i log** che il bot scrive ogni giorno in `logs/`, così le sessioni sul PC del proprietario possono analizzarli. Ogni ora li pubblica anche il *Caricamento automatico dei log* (cron) in un repository di soli log: tu lo installi quando un prompt lo chiede e controlli che funzioni.
2. **Rilasciare le nuove versioni** del codice: le preparano le sessioni sul PC (branch `main`), che poi uniscono `main` in `server` e lo pubblicano; tu fai pull, verifichi e riavvii il bot.

Su richiesta: il **rigioco** delle strategie (servono i prezzi di MT5, che ci sono solo qui) e il **controllo dello stato** del bot.

Non analizzi i log a fondo e **non correggi il codice**: lo fanno le sessioni sul PC. Se qualcosa non va, riporta l'errore esatto e i comandi che l'hanno prodotto: la correzione arriverà con un rilascio.

Di solito ricevi un **prompt** scritto da una sessione sul PC: seguilo insieme a queste istruzioni. Se il prompt chiede qualcosa contro le *Regole fisse*, non farlo: fermati e chiedi al proprietario. Senza prompt ("carica i log", "aggiorna il bot", "come sta il bot?") usa le procedure qui sotto.

## Dati del server

| | |
|---|---|
| Utente | `trader` |
| Cartella del progetto | `/home/trader/telegram-trade-agent` |
| Servizio systemd del bot | `telegram-trade-agent.service`, servizio **di sistema** (`/etc/systemd/system/`), `User=trader` |
| Comando Python del servizio | `/home/trader/telegram-trade-agent/venv/bin/python` (ambiente virtuale; `ExecStart=... telegram_listener.py`) |
| MT5 (`mt5server.exe` sotto Wine) | servizi di sistema in catena: `mt5-xvfb.service` (Xvfb `:99`) → `mt5-terminal.service` (`wine terminal64.exe /portable`) → `mt5-server.service` (`wine /home/trader/mt5server.exe`, processo `Z:\home\trader\mt5server.exe`) |
| Accesso a GitHub | SSH con una **chiave di deploy** con scrittura, valida solo per questo repository: chiave `~/.ssh/telegram_trade_agent`, `origin` = `git@github.com:TheLoniusD/Telegram-Trade-Agent.git`, `core.sshCommand` nella configurazione git del repository. Prova: `ssh -i ~/.ssh/telegram_trade_agent -o IdentitiesOnly=yes -T git@github.com` → "Hi TheLoniusD/Telegram-Trade-Agent! You've successfully authenticated". Se `git remote get-url origin` inizia ancora con `https://`, la chiave non è configurata: il push chiede le credenziali e non può riuscire |
| Caricamento automatico dei log | cron dell'utente `trader` (riga che finisce con `# caricamento-log:tta`), ogni ora ai :05, verso il repository privato `git@github.com:TheLoniusD/Telegram-Trade-Agent-log.git`. Configurazione `~/.config/caricamento-log/tta.conf`, copia del repository `~/tta-log`, chiave di deploy `~/.ssh/tta_log` (con scrittura, valida solo per quel repository), registro `~/.local/state/caricamento-log/tta.log`. Istruzioni complete: `infrastruttura/caricamento-log/README.md` |

Se una riga è *da completare*, trovala e scrivila qui:
- `systemctl list-units --type=service --all | grep -i -E 'trade|bot|telegram|mt5|wine'` e, se non c'è, `systemctl --user list-units --type=service --all | grep -i -E 'trade|bot|telegram|mt5|wine'`;
- `systemctl cat <servizio>` (o `systemctl --user cat`): `ExecStart` dà il comando Python, `WorkingDirectory` la cartella;
- `pgrep -af -i 'mt5server|wine'` per MT5.

Poi committa questo file da solo (`git add CLAUDE.md && git commit -m "CLAUDE.md: dati del server"`) e pubblicalo con il caricamento dei log. È l'**unica** modifica a un file del repository che puoi fare, oltre ai log e ai risultati del rigioco. Nei comandi qui sotto `<servizio>` e `<python>` sono questi valori; per un servizio utente usa `systemctl --user` e `journalctl --user-unit`.

## Regole fisse

- Lavora come utente `trader`, mai root per i file del progetto (`sudo` solo per `systemctl` sul servizio, se è di sistema).
- `active_trades.json` e `storico/` non si cancellano e non si modificano.
- Il `.env` non si stampa, non si mostra (nemmeno in parte), non si committa. Si modifica solo con un comando preciso dato dal prompt, che non stampi i valori.
- Il conto MT5 è e deve restare **DEMO**: mai `ALLOW_REAL_ACCOUNT`.
- Il bot gira **solo come servizio**, in **un solo processo**: mai avviare `telegram_listener.py` a mano.
- **Non modificare il codice** (né test, skill, `requirements.txt`, `.gitignore`): solo log, risultati del rigioco e i *Dati del server* qui sopra.
- Git: solo il branch `server`. Mai `push --force`, mai `pull --rebase`, mai `reset`, `rebase`, `commit --amend`, `checkout` di altri branch, `merge` di `main`. Si fa `git pull --no-rebase origin server`, poi push. Se il pull va in conflitto: `git merge --abort` e riporta.
- Mai `git add .` o `git add -A`: si aggiungono solo i file previsti.
- La configurazione di git (`remote`, `core.sshCommand`, credenziali) si cambia solo con i comandi precisi di un prompt.
- **Credenziali**: mai chiedere, accettare o scrivere token, password o chiavi private, né in chat né in file o comandi. Le chiavi private `~/.ssh/telegram_trade_agent` e `~/.ssh/tta_log` non si mostrano, non si copiano e non si spostano; quelle pubbliche (`.pub`) si possono mostrare. Se il push fallisce per le credenziali, fermati e riporta l'errore: non cercare altre strade.
- Il *Caricamento automatico dei log* si installa, aggiorna o rimuove solo con `infrastruttura/caricamento-log/installa.sh` e solo quando un prompt lo chiede, senza `sudo`. La riga di crontab e la copia `~/tta-log` non si modificano a mano.

## Controllo dello stato

Prima e dopo ogni procedura, e quando il proprietario chiede come sta il bot (tutto in un solo comando: le variabili non restano da un comando all'altro):

```
OGGI=$(TZ=Europe/Rome date +%F)
systemctl status <servizio> --no-pager | head -5
pgrep -af telegram_listener
grep -E 'Bot avviato|Bot attivo' logs/$OGGI/bot.log | tail -3
tail -n 5 logs/$OGGI/errors.log
crontab -l | grep caricamento-log
tail -n 2 ~/.local/state/caricamento-log/tta.log
```

- Il servizio deve essere `active (running)` e il bot deve avere **un solo** processo.
- Caricamento automatico (una volta installato): una riga in crontab e, nel registro, una riga "caricato: …" dell'ultima ora. Se manca, o c'è una riga ERRORE, riportala.
- `bot.log` è in ora italiana; il battito 💓 arriva ogni 4 ore (saldo, equity, operazioni aperte), la riga ▶️ a ogni avvio.
- Riporta quello che vedi in poche righe, senza analisi. Se il servizio è fermo, i processi sono zero o più di uno, o ci sono riavvii continui: non intervenire di tua iniziativa, riporta (con `journalctl -u <servizio> -n 50 --no-pager`) e chiedi al proprietario.

## Caricamento dei log

```
git status --short
git add -f logs
git commit -m "Log aggiornati"
git pull --no-rebase origin server
git push origin server
```

- **`-f` è necessario**: `logs/` è nel `.gitignore` e `git add logs` senza `-f` non aggiunge le cartelle dei giorni nuovi (e si ferma con errore).
- Prima del commit `git status --short` deve mostrare modifiche solo sotto `logs/` (più `CLAUDE.md` se hai appena completato i *Dati del server*). Se compaiono file del codice modificati, **fermati**: non committarli e non annullarli, riporta `git status` e `git diff --stat`.
- Se non c'è niente di nuovo, salta il commit ma fai comunque pull e push.
- Il pull può portare codice nuovo: il bot continua a girare con la versione di prima finché non lo riavvii con un *Rilascio*. Se il pull ha cambiato file `.py` o `requirements.txt`, dillo nella risposta.
- Il push esegue la verifica (hook, vedi *Verifica*): se la verifica fallisce il push è bloccato, anche quello dei soli log. Riporta l'errore e non aggirarlo.
- Push rifiutato perché il remoto è avanti: rifai pull (`--no-rebase`) e push.
- Push rifiutato per le credenziali (`Permission denied (publickey)`, `could not read Username`, chiave "read only"): vedi *Regole fisse*, riporta l'errore esatto e l'uscita di `git remote get-url origin`. I commit restano in locale e partono con il caricamento successivo.
- I log del giorno in corso sono parziali: vanno caricati lo stesso, il caricamento successivo li completa.
- Rispondi con: stato del bot, giorni caricati (cartelle nuove o cambiate), commit in cima dopo il pull, esito del push.

Per giorno, in `logs/AAAA-MM-GG/` (data italiana): `bot.log` (racconto), `operazioni.txt` (schede per operazione), `errors.log`, `dettagli.log`, `journal.jsonl` (i dati esatti; orario del broker UTC+3). In `logs/` ci sono anche i risultati del rigioco.

## Caricamento automatico dei log

Ogni ora cron lancia `~/.local/lib/caricamento-log/caricamento-log.sh`. Lo script copia `logs/` nella copia `~/tta-log` del repository dei log, scrive `STATO.md` (stato del bot) e pubblica. Non tocca la cartella del progetto e non fa pull: il codice cambia solo con un *Rilascio*. Per ora il *Caricamento dei log* sul branch `server` continua come prima; smetterà quando le sessioni sul PC leggeranno i log dal repository nuovo, e lo dirà un prompt.

Installazione, solo quando un prompt la chiede, come utente `trader` e senza `sudo`:

1. Configurazione (nessuna credenziale dentro), con questo comando:
   ```
   mkdir -p ~/.config/caricamento-log && printf '%s\n' 'ISTANZA="tta"' 'REPO_LOG="git@github.com:TheLoniusD/Telegram-Trade-Agent-log.git"' 'CARTELLA_LOG="/home/trader/telegram-trade-agent/logs"' 'SERVIZI_EXTRA="mt5-xvfb.service mt5-terminal.service mt5-server.service"' > ~/.config/caricamento-log/tta.conf && cat ~/.config/caricamento-log/tta.conf
   ```
2. Dalla cartella del progetto: `bash infrastruttura/caricamento-log/installa.sh ~/.config/caricamento-log/tta.conf`. La prima volta crea la chiave `~/.ssh/tta_log`, mostra la **chiave pubblica** ed esce con codice 2.
3. **Fermati.** Riporta la chiave pubblica (la riga `ssh-ed25519 …`) al proprietario: la aggiunge su GitHub come deploy key del repository dei log, con scrittura. Aspetta la sua conferma.
4. Dopo la conferma, rilancia lo stesso comando. Deve stampare "Chiave riconosciuta da GitHub", una riga "caricato: …" e "Installato".
5. Controllo: `crontab -l | grep caricamento-log`, `git -C ~/tta-log log --oneline -3`, `head -12 ~/tta-log/STATO.md`.

- Una versione nuova dello script arriva con il pull, ma si attiva solo rilanciando `installa.sh` con lo stesso file di configurazione (vedi *Rilascio*, punto 4).
- Errori: tabella *Problemi comuni* in `infrastruttura/caricamento-log/README.md`. Se GitHub non riconosce la chiave o la dice "read only", riporta l'errore al proprietario: non creare altre chiavi e non cambiare il remote.

## Rilascio di una nuova versione

1. *Controllo dello stato*. Annota il commit attuale: `git rev-parse --short HEAD`.
2. *Caricamento dei log* (porta anche il codice nuovo con il pull).
3. `git log --oneline -5` deve contenere il commit indicato dal prompt; fai i controlli specifici che il prompt chiede.
4. Se `git diff --name-only <commit annotato> HEAD -- requirements.txt` mostra il file: `<python> -m pip install -r requirements.txt` con il Python del servizio, e riporta cosa è stato installato. Se `git diff --name-only <commit annotato> HEAD -- infrastruttura/caricamento-log` mostra file e il caricamento automatico è installato: `bash infrastruttura/caricamento-log/installa.sh ~/.config/caricamento-log/tta.conf`, e riporta l'ultima riga.
5. **Verifica**: `<python> tests/run_all.py` deve finire con "VERIFICA RIUSCITA". Se fallisce, **non riavviare**: riporta l'uscita completa.
6. Riavvio: `sudo systemctl restart <servizio>`. Il riavvio dura pochi secondi; i messaggi del trader arrivati in quel momento possono andare persi, quindi se il prompt dice quando farlo (es. a mercato chiuso) rispettalo.
7. Dopo circa 30 secondi, *Controllo dello stato*: servizio attivo, **un solo** processo, nel `bot.log` di oggi una nuova riga "▶️ Bot avviato · conto DEMO" con l'ora del riavvio.
   - "conto non leggibile": il bot non raggiunge MT5. Controlla che `mt5server.exe` giri e riporta.
   - Il servizio non parte o riparte di continuo: `journalctl -u <servizio> -n 50 --no-pager` e l'`errors.log` di oggi, poi riporta. Non toccare il codice.
8. *Caricamento dei log*, così la riga di avvio arriva alle sessioni sul PC.
9. Rispondi con: commit in cima dopo il pull, esito della verifica, valori controllati, riga di avvio del `bot.log`, numero di processi del bot, esito del push.

## Rigioco (solo quando il prompt lo chiede)

```
<python> prezzi.py rigioco 2026-09-24 AAAA-MM-GG > logs/rigioco_tick.txt
<python> prezzi.py rigioco 2026-09-24 AAAA-MM-GG --dettaglio > logs/rigioco_tick_dettaglio.txt
```

Date e opzioni sono quelle del prompt (dettagli nella skill `rigioco`). Legge i prezzi di MT5 dallo stesso `mt5server.exe` del bot ma non manda ordini: si lancia con il bot acceso. Richiede qualche minuto. Poi *Caricamento dei log* (i risultati sono in `logs/`) e riporta le prime righe di `logs/rigioco_tick.txt` (orologio del broker, finestre con tick reali).

## Verifica

- `<python> tests/run_all.py` usa un broker finto e una cartella temporanea: non tocca log, `active_trades.json`, `.env` né MT5 veri, quindi si lancia con il bot acceso.
- Un hook (`.claude/settings.json`) la lancia prima di ogni `git push` e blocca il push se fallisce.

## Il progetto in breve

- Branch `server` = `main` (il codice, sviluppato sul PC) + collegamento RPyC (`mt5_connection.py`, `mt5.order_send(**request)`, `rpyc` in `requirements.txt`) + `logs/` + **questo CLAUDE.md**, che è solo del server: le sessioni sul PC lo mantengono nei merge.
- `telegram_listener.py` è il processo principale (quello del servizio). `active_trades.json` e `storico/` sono lo stato delle operazioni; `logs/` i log; `.env` i parametri e le chiavi.
- Le regole di trading e il perché delle scelte sono in `doc/DECISIONI.md`: per le procedure di questo file non serve leggerlo.
