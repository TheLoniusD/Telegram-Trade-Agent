# Caricamento automatico dei log

Pubblica ogni ora i log del bot in un **repository GitHub privato di soli log**, insieme a un file `STATO.md` che dice a colpo d'occhio se il bot è in salute. Si installa su qualunque server che faccia girare il bot (il nostro o quello di un cliente), come utente del bot, **senza root**.

## Cosa fa

Ogni ora cron lancia `caricamento-log.sh`, che:

1. copia la cartella dei log del bot nella copia locale del repository dei log. Copia solo i log: mai `.env`, sessione Telegram, `active_trades.json` o codice;
2. scrive `STATO.md`: in cima "✅ Tutto regolare" oppure l'elenco di cosa controllare, poi servizio del bot (e servizi collegati, es. MT5), numero di processi del bot, ultimo avvio, ultimo battito (avviso se più vecchio di 5 ore), spazio su disco e ultime righe di `errors.log`;
3. fa un commit e lo pubblica. Se la rete manca, i commit restano sul server e partono al giro successivo.

Non scrive nella cartella del bot e non ne aggiorna il codice: il bot cambia versione solo con un rilascio.

## Perché un repository separato

- Su GitHub i permessi valgono per tutto il repository: con un repository di soli log, il cliente può vedere i suoi log senza vedere il codice.
- La chiave di deploy del caricamento può scrivere solo nel repository dei log, non nel codice.
- Il codice sul server non cambia da solo: il caricamento non fa mai `git pull` nella cartella del bot.

## Requisiti

- Linux con `git`, `ssh` e `cron` (`flock`, `pgrep` e `systemctl` ci sono di solito; se mancano, `STATO.md` lo dice e il caricamento funziona lo stesso).
- L'utente che fa girare il bot. Non serve root.

## Installazione

1. **GitHub**: crea un repository **privato** e vuoto per i log (es. `Telegram-Trade-Agent-log`).
2. **Server**, come utente del bot: crea il file di configurazione partendo da `esempio.conf`. Servono `ISTANZA` (nome breve), `REPO_LOG` (indirizzo SSH del repository dei log) e `CARTELLA_LOG` (cartella dei log del bot); gli altri valori hanno un default.
3. Lancia:
   ```bash
   bash infrastruttura/caricamento-log/installa.sh <file di configurazione>
   ```
   Crea la chiave `~/.ssh/<istanza>_log`, ne mostra la parte pubblica e si ferma.
4. **GitHub**, nel repository dei log: *Settings → Deploy keys → Add deploy key*, incolla la chiave pubblica e spunta **Allow write access**.
5. Rilancia lo stesso comando. Controlla che GitHub riconosca la chiave per quel repository, clona il repository, installa script e configurazione nella home, aggiunge la riga di crontab e fa subito il primo caricamento.

| Cosa | Dove |
|---|---|
| Script | `~/.local/lib/caricamento-log/caricamento-log.sh` |
| Configurazione | `~/.config/caricamento-log/<istanza>.conf` |
| Copia del repository dei log | `~/<istanza>-log` |
| Chiave di deploy | `~/.ssh/<istanza>_log` (la parte privata non lascia mai il server) |
| Registro dei caricamenti | `~/.local/state/caricamento-log/<istanza>.log` |
| Pianificazione | `crontab -l`, riga che finisce con `# caricamento-log:<istanza>` |

Lo script viene **copiato** nella home: una versione nuova (o una configurazione cambiata) si attiva rilanciando `installa.sh`, non da sola.

## Controllo

- **`STATO.md`** su GitHub: l'ora in cima è quella dell'ultimo caricamento. Se è vecchia di più di un'ora, il caricamento si è fermato.
- `crontab -l | grep caricamento-log`: la riga pianificata.
- `tail ~/.local/state/caricamento-log/<istanza>.log`: una riga "caricato: …" a ogni giro, oppure l'errore.

## Aggiornare e rimuovere

- Aggiornare: `bash infrastruttura/caricamento-log/installa.sh ~/.config/caricamento-log/<istanza>.conf`.
- Rimuovere: `bash infrastruttura/caricamento-log/installa.sh --rimuovi ~/.config/caricamento-log/<istanza>.conf` toglie la riga di crontab. Chiave, copia del repository e configurazione restano; la deploy key va tolta anche da GitHub.

## Problemi comuni

| Messaggio | Causa e rimedio |
|---|---|
| `GitHub non riconosce la chiave come deploy key di …` | Chiave non ancora aggiunta, o aggiunta a un altro repository. |
| `… marked as read only` | Alla deploy key manca *Allow write access*: toglila e riaggiungila con la spunta. |
| `pull non riuscito` | Qualcuno ha cambiato su GitHub un file dei log: va risolto a mano nella copia `~/<istanza>-log`. |
| `STATO.md` fermo a un'ora vecchia | Server spento, cron fermo o rete assente: guarda il registro dei caricamenti. |
