# Decisioni e regole del bot

Documento di riferimento per non perdere il filo: **come si comporta il bot oggi** e **perché abbiamo scelto così**.

**Regola di lavoro:** a ogni sessione in cui cambiamo una regola di trading (non per le correzioni di bug ovvie) si aggiorna questo file nello stesso push del codice:
1. la sezione *Regole in vigore* si riscrive con lo stato nuovo;
2. si aggiunge in cima al *Registro* una voce con data, decisione, motivo (con i dati), alternative scartate e cosa resta da verificare.

Prima di proporre una modifica si controlla qui che non contraddica una scelta già fatta, o che, se la cambia, lo faccia consapevolmente.

---

## Regole in vigore (aggiornato al 20/10/2026)

### Segnali e classificazione
- Si ascolta solo il canale Maestro Fx; l'agente (Claude Haiku) classifica ogni messaggio in NEW_SIGNAL / UPDATE_SIGNAL / CLOSE_SIGNAL / IGNORE.
- Prima dell'agente vengono scartati: messaggi senza testo, inoltrati, senza parole operative, modifiche identiche, **falsi edit** (reazioni/visualizzazioni: senza data di modifica recente), correzioni di messaggi vecchi (> 10 minuti) che non sono segnali aperti.
- **Foto e GIF senza testo**: non si classificano e non aprono nulla, ma il diario registra tipo di media e prezzo di quel momento. Alla **GIF che precede i segnali** il bot controlla MT5 e, se l'agente non è stato chiamato negli ultimi 5 minuti, lo **riscalda** (chiamata a vuoto con lo stesso prompt) perché il segnale non parta da freddo. Le foto di Trade Active / HIT TP / HIT TP MAX non vengono lette.
- **Errore di battitura**: un segnale con prezzo a più di **20 $** dal mercato non viene aperto (`MAX_SIGNAL_DISTANCE`); la correzione del trader viene aperta dal **recupero**.
- **Recupero**: una modifica recente di un segnale mai aperto (perso durante un riavvio, o rifiutato) viene trattata come segnale nuovo; mai riaperto un segnale già chiuso.
- Una **modifica** vale solo per il proprio messaggio (o quello a cui risponde), mai per "l'ultima operazione aperta".

### Ingresso (`ENTRY_MODE=range`)
- **2 ticket per segnale** (`ENTRY_TICKETS=2`), rischio **2%** del saldo: **1% ciascuno**.
- **e1 (TP1) ed e2 (TP2) al prezzo del segnale**: subito a mercato se il prezzo è nella zona del trader (o migliore); se è oltre il bordo dalla parte sbagliata restano limite al bordo ed entrano appena il prezzo torna nella zona. **Mai ingressi fuori zona dalla parte sbagliata.**
- Niente più e3/e4 più dentro la zona (tolti il 06/10). Restano disponibili con `ENTRY_TICKETS=4`: limite a metà e a ¾ della zona, lotto ridotto da `ENTRY_DEEP_LOT_FACTOR`.
- Zona: quella scritta dal trader; nella fase rapida ("Gold buy 4186") si assume larga 5 $ a favore del trader.
- SL provvisorio a 10 $ dal segnale finché il trader non scrive il suo; SL a meno di 3 $ o dalla parte sbagliata → provvisorio.
- **TP provvisori** a 10 $ (TP1) e 20 $ (TP2) dal prezzo del segnale finché il trader non scrive i suoi (`PROVISIONAL_TP=10,20`): nessuna posizione resta senza uscita se il segnale non viene mai completato. Un ingresso a mercato senza prezzo che non ha TP da ereditare li prende dal prezzo d'ingresso.
- Se una posizione del bot risulta su MT5 **senza SL**, il controllo periodico (ogni 30 s) rimette lo SL che ha in memoria.
- **Posizioni orfane**: ogni 30 s le posizioni del bot su MT5 (magic number) vengono confrontate con la memoria; quelle segnate chiuse o cancellate per errore tornano in gestione, quelle sconosciute vengono segnalate.
- Ordini limite non eseguiti: cancellati dopo 30 minuti, o al primo comando di pareggio/Trade Active/chiusura.
- Prima di eseguire un comando del trader (Trade Active, pareggio, chiusure) lo stato degli ordini limite viene **riletto da MT5**: un ordine già eseguito è gestito come posizione aperta, mai segnato come cancellato.

### "Trade Active … Running N+ Pips" (con BE+)
- Si chiudono **al massimo 2 ticket, quelli entrati al prezzo peggiore**, e **ne resta sempre aperto almeno uno** (con 2 ticket: 1 chiuso, 1 a pareggio; con 1 aperto nessuno). A parità di prezzo si chiude prima il TP1 (resta il TP2); ingressi entro **0,50 $** contano come lo stesso prezzo (`TRADE_ACTIVE_TIE`).
- **Mai chiusure in perdita**; nessuna soglia sui pips. Una sola volta per operazione.
- I ticket rimasti vanno a **pareggio**; gli ordini non eseguiti vengono cancellati.
- Messaggio su più righe di pips: la regola vale per l'operazione a cui risponde (ultima riga); le altre operazioni aperte nella stessa direzione vanno solo a pareggio.

### Pareggio (BE) e protezione
- Pareggio = ingresso **± 1,00 $** dalla parte del guadagno (`BE_MODE=entry`, `BE_OFFSET=1.0`; fino all'11/10 era 0,50 $).
- **Se MT5 rifiuta il pareggio** (siamo in perdita o troppo vicini) lo stop va al primo gradino che MT5 accetta: **pareggio ridotto a ± 0,50 $** (`BE_FALLBACK_OFFSET`), poi **stop di protezione** a ¼, ½, ¾ della distanza ingresso–stop. Ogni 30 secondi si riprova il pareggio pieno e intanto si sale di gradino appena il prezzo lo permette; lo stop non viene mai peggiorato. Si continua finché il pareggio entra, la posizione si chiude o il trader scrive un nuovo SL.
- Uno SL già più protettivo del pareggio non viene mai peggiorato.
- **Pareggio subito quando lo chiede il trader**, senza guadagno minimo (`BE_MIN_PROFIT=0`), e **SL esattamente quello del trader**, senza margini: entrambe le alternative verificate e scartate il 06/10.

### Altri messaggi del trader
- **HIT TP** (con BE+): pareggio, nessuna chiusura.
- **HIT TP MAX**: è il tabellone della serie → **pareggio su tutto, nessuna chiusura**. Chiusura totale solo per comandi espliciti ("Close all", "Close now").
- **"Secure first layer now" / "close lowest layer"**: chiude su MT5 il ticket del layer indicato (il TP1) **solo se in guadagno**; mai segnato chiuso solo in memoria.
- **"Close half" e simili**: chiude la percentuale chiesta, prima i ticket con il TP più lontano, **mai ticket in perdita**; ripetizione entro 15 minuti = stesso comando.
- **Re-entry** ("Try buy again", senza prezzo): eredita SL e TP *scritti dal trader* dall'ultima operazione nella stessa direzione (aperta, o chiusa nell'ultima ora), entra a mercato; i messaggi sul segnale padre valgono anche per lui.
- **"Again" scritto entro 5 minuti da un segnale nuovo ancora aperto** nella stessa direzione è il commento del trader a quel segnale: **nessuna nuova posizione** (`REENTRY_CAPTION_SECONDS=300`). Vale per ogni ingresso senza prezzo ("one more time", "lets sell it"), anche se l'agente non lo segna come re-entry.
- Un segnale con prezzo non è mai un re-entry, anche se vicino a uno aperto.

### Sicurezza del conto
- Il bot apre ordini **solo su un conto DEMO**: con un conto reale, o se il tipo di conto non si legge, ogni segnale viene rifiutato (`ALLOW_REAL_ACCOUNT=1` per permetterlo).

### Log e repository
- Per giorno: `bot.log` (racconto), `operazioni.txt` (schede), `dettagli.log`, `errors.log`, `journal.jsonl` (dati). Heartbeat ogni 4 ore.
- I log stanno nel repository privato **`Telegram-Trade-Agent-log`**: il server li pubblica da solo ogni ora (cron, `infrastruttura/caricamento-log/`), con `STATO.md` sullo stato del bot. Mai push forzati.
- Branch: si sviluppa su **`main`** (Windows); **`server`** = `main` + collegamento RPyC, aggiornato unendo `main` (fino al 20/10 conteneva anche i log). (`claude-branch` eliminato il 01/10.)

### Parametri `.env` (valore di default)
| Parametro | Default | Note |
|---|---|---|
| `ENTRY_MODE` | `range` | `zone` = ingresso in due fasi (30/09), `market` = tutto a mercato |
| `ENTRY_TICKETS` | 2 | 4 = anche e3/e4 dentro la zona (fino al 05/10); **sul server non va impostato nel `.env`** |
| `ENTRY_IMMEDIATE_TICKETS` | 2 | ticket al prezzo del segnale in modalità `range` |
| `ENTRY_DEEP_LOT_FACTOR` | 0.5 | solo con `ENTRY_TICKETS=4`: lotto di e3/e4 rispetto alla quota piena |
| `BE_MIN_PROFIT` | 0 | guadagno minimo per il pareggio: deve restare 0 |
| `PROVISIONAL_TP` | 10,20 | distanza dei TP provvisori dal segnale; `0` = nessun TP provvisorio |
| `TRADE_ACTIVE_TIE` | 0.5 | ingressi entro questa distanza = stesso prezzo al Trade Active (si chiude il TP1) |
| `REENTRY_CAPTION_SECONDS` | 300 | "again" entro questo tempo da un segnale aperto = stesso setup, nessuna posizione |
| `ALLOW_REAL_ACCOUNT` | 0 | `1` = permette ordini su un conto non DEMO (oggi non va usato) |
| `ENTRY_ZONE_WIDTH` | 5 | larghezza zona in fase rapida |
| `ENTRY_ORDER_EXPIRY_MINUTES` | 30 | |
| `TRADE_ACTIVE_MAX_CLOSE` | 2 | ticket incassati al Trade Active (ne resta sempre 1) |
| `BE_MODE` / `BE_OFFSET` | `entry` / 1.0 | sul server il `.env` contiene anche `BE_MODE=entry`; `BE_OFFSET` **non va impostato nel `.env`** (vale il default) |
| `BE_FALLBACK_OFFSET` | 0.5 | pareggio ridotto quando quello pieno è rifiutato, prima dello stop di protezione; `0` = nessun gradino ridotto |
| `MAX_SIGNAL_DISTANCE` | 20 | |
| `LOG_TIMEZONE` / `LOG_DETAILS_LEVEL` | Europe/Rome / INFO | |

---

## Risultati per giornata

| Giorno | Risultato | Versione / note |
|---|---|---|
| 24/09 | −29 $ | ingresso a mercato, 2 ticket |
| 25/09 | −638 $ | BE solo con 3 $ di guadagno minimo (poi tolto) |
| 28/09 | +691 $ (≈ −36 $ non registrati) | ordini limite nella zona; trend forte |
| 29/09 | −32 $ | mattina BE a metà zona (−62 $), pomeriggio BE a ingresso +0,5 (+29 $); laterale |
| 30/09 | +45 $ | e1 a mercato entro 3 $, ordini nei primi 3 $; 1 stop pieno (−152 $) |
| 01/10 | −231 $ | ingresso in due fasi; 2 stop pieni (−299 $, anche il trader "hit risk") |
| 02/10 | −258 $ | ingresso `range`; 2 stop pieni (−285 $) con tutti e 4 i ticket eseguiti; segnale NFP solo immagine, non visto |
| 05/10 | −640 $ (−105 $ non registrati) | 4 stop pieni (−554 $), tutti con 4 ticket eseguiti; bug ordini eseguiti segnati come cancellati (SELL 4151) |
| 20/10 | +213 $ (fino alle 16:51) | nessuno stop pieno; 5 stop di protezione (−164 $): 3 SELL della notte (4121, 4126, 4130) con il prezzo salito poi a 4.144, oltre i loro SL (probabilmente 3 stop pieni evitati), e SELL 4128 che il trader porta al TP; SELL 4144 e SELL 4132 e2 al TP2 (+125 / +143 $); 1 segnale mai entrato (1,10 $ oltre il bordo); bot riavviato alle 16:50 con la versione del 16/10; log della sera non ancora caricati |
| 19/10 | +152 $ | trader 10/11 (1.905 pips); 1 stop pieno (BUY 4184 −116 $, "hit risk" anche per il trader); 1 stop di protezione (−16 $, BUY 4179, trader 115 pips); SELL 4184 e2 al TP2 (+126 $); 6 ticket chiusi a pareggio 9–18 secondi dopo averlo messo, su operazioni che il trader porta a 165–420 pips; 3 segnali mai entrati (0,25 / 0,80 / 1,62 $ oltre il bordo); bot ancora con la versione di prima del 16/10 (non riavviato), nessun effetto quel giorno |
| 16/10 | +214 $ | trader 10/10 (1.065 pips); nessuno stop pieno; 1 stop di protezione (−31 $); BUY 4155 e2 al TP2 (+125 $); 2 segnali mai entrati (prezzo 1,4–1,5 $ oltre il bordo) |
| 15/10 | +12 $ | un solo segnale pubblico (giornata del trader per i VIP) |
| 14/10 | −261 $ | 3 stop pieni (−365 $): 2 anche per il trader, 1 (SELL 4147) tenuto dal trader oltre il suo SL e chiuso al TP; 2 stop di protezione (−63 $); 2 segnali mai entrati (1,6–1,7 $ oltre il bordo); un BUY perso durante un riavvio non spiegato (08:08, trader TP 175 pips) |
| 13/10 | +119 $ | trader 12/12; nessuno stop pieno; 5 stop di protezione (−125 $); SELL 4197 e2 al TP2 (+125 $); "Lets sell one more time" aperto per errore come segnale nuovo (+13 $) |
| 12/10 | −439 $ | 3 stop pieni (−411 $): 2 anche per il trader, 1 (BUY 4165) chiuso dal trader a −30 pips ("i cut lose 30pips", ignorato dall'agente); 2 stop di protezione (−125 $); 1 segnale mai entrato (1,1 $ oltre il bordo) |
| 09/10 | −250 $ | 2 ticket; BUY 4201 + re-entry "Lets buy again" 17 s dopo: 4 posizioni sullo stesso setup, entrambe allo stop (−282 $, "hit risk" anche per il trader); 1 stop di protezione (−36 $); 2 segnali mai entrati (prezzo 0,14 e 0,88 $ oltre il bordo) |
| 08/10 | +206 $ (+73 $ da una posizione persa dal bot e chiusa al TP) | 2 ticket; nessuno stop pieno, nessun "hit risk" del trader; 2 stop di protezione (−53 $); 4 segnali mai entrati (prezzo 0,2–2 $ oltre il bordo) |
| 07/10 | +770 $ (+663 $ da chiusura manuale) | primo giorno con 2 ticket; nessuno stop pieno, 8 vinti su 9 gestiti dal bot (+108 $); SELL 4133 mai completato dal trader, chiuso a mano a +663 $ |
| 06/10 | −449 $ | ancora 4 ticket (e3/e4 a metà lotto); 5 stop pieni (−416 $): 4 anche per il trader ("hit risk"), 1 per MT5 scollegato 6 minuti (pareggio perso); 4 segnali mai entrati (prezzo oltre il bordo) |

---

## Registro delle decisioni (dal più recente)

### 20/10 (sera) — Log in un repository a parte, caricati ogni ora
- **Decisione** (metodo di lavoro, nessuna regola di trading cambiata):
  - I log vanno nel repository privato `TheLoniusD/Telegram-Trade-Agent-log`. Li pubblica ogni ora, al minuto 05, uno script lanciato da cron sul server (`infrastruttura/caricamento-log/`, senza root), con `STATO.md`: servizi, processi, ultimo avvio e battito, disco, errori di oggi. Il branch `server` non li contiene più.
  - Le sessioni sul PC leggono i log da una copia del repository dei log dentro il progetto (`Telegram-Trade-Agent-log/`, ignorata da git). Dal cellulare si usa una sessione nel cloud con i due repository: provata il 20/10, legge `STATO.md`.
  - Remote Control attivo per le nuove sessioni del PC.
- **Perché**: il caricamento a mano faceva `git pull` nella cartella del bot, portando sul disco codice non ancora rilasciato, che sarebbe partito al primo riavvio automatico (codice del 17/10 rimasto così fino al 20/10). Un repository a parte permette di mostrare i log a un cliente senza mostrare il codice, e dà al server una chiave che scrive solo lì. L'infrastruttura è pensata per essere installata anche sul server di un cliente.
- **Scartati**: report giornaliero scritto in automatico da un'attività pianificata nel cloud (l'analisi resta interattiva, dal PC o dal cellulare); branch `logs` nello stesso repository (i permessi di GitHub valgono per tutto il repository).
- **Da verificare**: che `STATO.md` si aggiorni ogni ora; dopo il passaggio, valutare di rendere di sola lettura la chiave di deploy del codice sul server.

### 20/10 — GIF prima del segnale: prezzo nel diario e agente riscaldato; le foto non si leggono
- **Decisioni** (nessuna regola di trading cambiata)
  - Le **foto** dei messaggi Trade Active, HIT TP e HIT TP MAX **non si leggono**: il testo contiene già tutto quello che il bot usa.
  - Ogni messaggio senza testo finisce nel diario con tipo di media (gif, photo, sticker…), id del media e bid/ask di quel momento.
  - Alla **GIF**: controllo di MT5 e riscaldamento dell'agente (chiamata con `max_tokens=0`, stessi tools e prompt: scrive la cache del prompt senza generare nulla) se l'agente non è stato chiamato negli ultimi 5 minuti; riga 🎞️ nel racconto con il prezzo. Nessun ordine.
  - Ogni chiamata all'agente registra i **secondi di attesa** (`CLASSIFIER_CALL.seconds`, `AGENT_WARMUP`).
  - Rigioco: variante **"segnale rapido senza agente"** (sigla `rapido`): il segnale rapido "Gold buy/sell NNNN" entra all'arrivo del messaggio invece che alla classificazione, con gli stessi livelli.
- **Perché** (log 23/09–20/10)
  - Trade Active 192 su 192, HIT TP 36 su 36 e HIT TP MAX 12 su 12 (dal 28/09) classificati bene dal solo testo, quasi tutti in risposta al segnale. Leggere la foto avrebbe rallentato proprio i comandi in cui i secondi contano (pareggio colpito in 9–18 s). Gli HIT TP MAX "NEWS WIN" (NFP, CPI) sono inoltrati dal canale privato: segnali mai pubblicati qui.
  - Prima di **188 segnali rapidi su 196** c'è una GIF senza testo (per il proprietario sempre la stessa, nessun contenuto). Anticipo: fino a 5 s in 50 casi, 6–60 s in 74, 1–3 min in 49, 3–5 min in 15. Non dice la direzione: non si può entrare prima.
  - Dal messaggio all'ordine passano **3,5 s** (mediana), quasi tutti di attesa dell'agente; il **primo segnale della mattina** (~04:00, cache del prompt scaduta dopo la notte) **7–9 s** (6 casi tra il 01 e il 19/10).
  - I 196 segnali rapidi hanno tutti lo stesso formato e l'agente li ha letti sempre come direzione + prezzo: il codice potrebbe eseguirli senza aspettarlo. Prima di farlo si misura quanto vale con il rigioco (oggi il rigioco entra all'ora della classificazione, cioè con il ritardo vero).
- **Contro**: una chiamata in più all'agente per GIF, al massimo pochi centesimi al giorno (la scrittura della cache che oggi paga il primo segnale la paga la GIF; si spende in più solo per le GIF non seguite da un segnale); il prezzo alla GIF e i secondi sono solo dati, non cambiano le operazioni.
- **Scartato**: lettura delle immagini con la visione (nessuna informazione in più, più lenta); ordini alla GIF (senza direzione servirebbero i due lati, rischio doppio).
- **Da verificare**: righe 🎞️ nel racconto e campi `media` / `media_id` nel diario (la GIF deve risultare `gif`, sempre con lo stesso id); `CLASSIFIER_CALL.seconds` del primo segnale della mattina con il riscaldamento (prima 7–9 s) e `cache_read_input_tokens` > 0; prezzo alla GIF confrontato con il prezzo del segnale e con quello all'ordine (`RISK_CALC.price`) nei segnali mai entrati.
- **Rigioco del 20/10 sera** (`logs/rigioco_rapido.txt` e `_dettaglio.txt`, 24/09–20/10, tick in 393 finestre su 394, "attuale" +557 $ come il rigioco corretto): "segnale rapido senza agente" **+620 $ (+63 $)**, uguale in 155 segnali su 200, meglio in 21 (+107 $), peggio in 24 (−44 $), giorni 8 meglio e 8 peggio; 34 delle 45 differenze sotto i 3 $ (−9 $ in tutto). Il guadagno viene da 4 segnali (08/10 SELL 4125 +30 $, 08/10 SELL 4119 +14 $, 20/10 SELL 4123 +11 $, 28/09 SELL 4173 +11 $). Segnali mai entrati: **1 recuperato su 30** (08/10 SELL 4119). Tempi al secondo: la variante entra al primo tick del secondo di arrivo, quindi è un po' ottimista. **Non adottata**: circa 3 $ al giorno da due segnali, al prezzo di togliere il controllo dell'agente sul messaggio che apre le posizioni. Resta in `prezzi.py` per rifarla con più giorni.

### 16/10 — Ingresso senza prezzo subito dopo un segnale = stesso setup; TP provvisori anche a mercato
- **Correzioni**
  - Un ingresso a mercato senza prezzo scritto entro 5 minuti da un segnale nuovo ancora aperto nella stessa direzione non apre posizioni **anche se l'agente non lo segna come re-entry**. Il prompt dell'agente riconosce "one more time" come "again".
  - Un ingresso a mercato senza prezzo che non ha TP da ereditare prende i **TP provvisori** a 10/20 $ dal prezzo d'ingresso; il TP inviato a MT5 va anche in memoria.
- **Perché**: 13/10 "Lets sell one more time" 63 secondi dopo "Gold sell 4192": l'08/10 la stessa frase era stata riconosciuta come re-entry, il 13/10 come segnale nuovo senza re-entry; il bot ha aperto altre 2 posizioni a mercato (4.192,08) **senza TP** e con lo SL provvisorio. È finita a pareggio (+12,52 $), ma era il raddoppio del rischio che la regola dell'11/10 voleva evitare. Il rigioco (`prezzi.py`) trattava già così ogni ingresso senza prezzo: ora bot e rigioco coincidono.
- **Contro**: un ingresso a mercato senza prezzo voluto davvero entro 5 minuti da un segnale nella stessa direzione non viene seguito (come per "again").
- **Da verificare**: altri messaggi senza prezzo dopo un segnale ("lets sell it", "one more"), e che la riga 📥 di un ingresso senza prezzo mostri i TP.
- **In uso dal 20/10 alle 16:50**: il server aveva portato il codice il 17/10 ma non aveva riavviato il bot. Il 19 e il 20/10 non ci sono stati casi che la versione nuova avrebbe gestito diversamente ("Let sell one more time" del 20/10 alle 03:59 era a 60 secondi da un segnale: nessuna posizione in entrambe le versioni).

### 13/10 — Verifica obbligatoria e istruzioni per le sessioni di Claude
- **Decisione** (metodo di lavoro, nessuna regola di trading cambiata):
  - `tests/`: banco di prova con broker finto (`tests/fake_mt5.py`, `tests/banco.py`) e scenari con controlli automatici su ingresso, pareggio e gradini, Trade Active, ordini eseguiti di nascosto, MT5 scollegato, re-entry, posizioni orfane, layer, SL rimesso. Si lancia con `python tests/run_all.py` (circa 1 secondo, nessun MT5/Telegram/.env, cartella temporanea).
  - Regola: **ogni correzione si fa con un test e si verifica**; il push si fa solo con la verifica riuscita (hook in `.claude/settings.json` che blocca `git push`) e dopo l'ok del proprietario.
  - `CLAUDE.md` e skill di progetto (`analisi-log`, `rigioco`, `prompt-server`) per sessioni nuove con memoria limitata, anche sul server.
- **Perché**: le prove sul broker finto vivevano solo nella cartella temporanea di una sessione e il controllo dei risultati era a occhio; le regole di lavoro erano ripetute a ogni prompt.
- **Da verificare**: che la sessione sul server lanci la verifica prima di ogni riavvio; aggiungere uno scenario a ogni bug trovato nei log.

### 11/10 (rigioco) — Pareggio a +1 $; protezione, ingresso al bordo, incasso al Trade Active e TP provvisori confermati
- **Decisione**: pareggio a ingresso **± 1 $** invece di ± 0,50 $ (`BE_OFFSET` di default 1.0, nessuna modifica al `.env`); se MT5 lo rifiuta, **prima della protezione si prova il pareggio ridotto a ± 0,50 $** (`BE_FALLBACK_OFFSET=0.5`). Tutto il resto resta com'è: stop di protezione ¼-½-¾, mai ingressi fuori zona, incasso al Trade Active con qualsiasi guadagno, TP provvisori 10/20 $, 2 ticket.
- **Perché**: rigioco dei messaggi del trader dal 24/09 al 09/10 **con i tick reali** (`logs/rigioco_tick.txt` e `rigioco_tick_dettaglio.txt`): 134 segnali, rischio fisso 140 $ per segnale, orologio del broker UTC+3 in tutti i giorni, tick in 263 finestre su 264. Coincide con il reale delle regole attuali a pochi dollari: 08/10 SELL 4121 −35,0 (reale −35,22), BUY 4134 −16,9 (−15,57); 09/10 BUY 4194 −35,0 (−35,62), BUY 4181 +24,5 (+22,71), BUY 4182 +16,5 (+16,77), SELL 4172 +7,0 (+7,12).

  | Variante | Totale | Rispetto alle regole del 09/10 | Dettaglio |
  |---|---|---|---|
  | regole del 09/10 | +265 $ | — | reale registrato nello stesso periodo −747 $ (regole e lotti di quei giorni, bug compresi) |
  | **pareggio +1 $** | **+437 $** | **+172 $** | meglio in 9 giorni su 12; 65 segnali migliorano (quasi sempre +3,5 $: il ticket chiuso a pareggio incassa 7 $ invece di 3,5), 4 peggiorano di 21 $ (pareggio rifiutato perché il prezzo era troppo vicino, protezione a ¼ colpita); nessun vincente tagliato |
  | senza protezione | +83 $ | −182 $ | la protezione evita lo stop pieno in 6 segnali (+542 $) e chiude 8 posizioni che poi si sarebbero riprese (−360 $) |
  | protezione da ½ / solo ¾ | +236 / +275 $ | −29 / +10 $ | "solo ¾" è rumore e le perdite peggiori diventano −105 $ invece di −35 $ |
  | ingresso fino a 0,5 / 1 / 2 $ oltre il bordo | +207 / −55 / −3 $ | −58 / −320 / −268 $ | con 0,5 $: 4 segnali recuperati (+58 $), 22 peggiorati (−116 $) perché si entra subito a un prezzo peggiore quando il prezzo sarebbe tornato al bordo |
  | Trade Active senza incasso | −72 $ | −337 $ | meglio in 12 segnali (+388 $), peggio in 54 (−725 $) |
  | incasso al Trade Active solo da +1 $ | +241 $ | −24 $ | |
  | senza TP provvisori | +493 $ | +228 $ | tutta la differenza da un'operazione (07/10 SELL 4133: +585 invece di +358), le altre 133 identiche |
  | 4 ticket (regole fino al 06/10) | +165 $ | −100 $ | meglio in 8 giorni su 12: il vantaggio dei 2 ticket viene dal 28/09 (+1.409 contro +718); più esposizione, non più resa (perdita media −60 contro −42 $) |
  | mai pareggio | −1.083 $ | −1.348 $ | 71 stop pieni |
- **Gradino a +0,5 $** (deciso dopo il rigioco, **verificato con un secondo rigioco lo stesso giorno**): pareggio +1 $ con gradino **+497 $**, senza gradino +437 $, regola di prima (+0,5 $) +265 $. Il gradino recupera i 4 segnali che finivano sulla protezione a ¼ (+84 $) e ne peggiora 5 di 3,5–7 $ (−24 $: lo stop a +0,5 $ preso dove senza gradino sarebbe arrivato il pareggio pieno). **Rispetto alla regola di prima: meglio in 60 segnali (+231 $), peggio in nessuno.**
- **Pareggio più largo** (secondo rigioco, sempre con il gradino a +0,5 $): +1,5 $ → +520 $ (+23 $), meglio in 11 giorni su 12 ma il 28/09 lo stop a +1,5 $ chiude in anticipo un SELL che andava al TP2 (SELL 4179: +80 $ invece di +210 $); +2 $ → +324 $ (−173 $), tre vincenti del 28/09 tagliati (−434 $ quel giorno). Ogni 0,5 $ in più rende 3,5 $ a ticket nei giorni normali ma taglia i vincenti nei giorni di trend, che sono quelli che fanno il guadagno: **resta +1 $**, l'unico livello che nel periodo non ha tagliato nessun vincente.
- **Contro**: con +1 $ MT5 rifiuta il pareggio più spesso (il prezzo deve essere oltre l'ingresso di almeno 1 $ più la distanza minima del broker): in quei casi entra il pareggio ridotto o, se non passa nemmeno quello, lo stop di protezione (nel rigioco senza gradino 4 volte, −84 $). Lo stop sta 0,5 $ più vicino al prezzo: in 12 giorni non ha mai chiuso un'operazione che con +0,5 $ sarebbe arrivata al TP, ma può succedere.
- **Nota**: il periodo è positivo solo grazie al 28/09 (+1.409 $, 11 SELL in un calo continuo). Negli altri 11 giorni le regole attuali fanno −1.144 $ (con il pareggio +1 $: −962 $). Le regole spostano centinaia di dollari, la qualità dei segnali del trader migliaia.
- **Scartato**: tolleranza d'ingresso oltre il bordo; protezione da ½ o solo ¾; incasso al Trade Active solo da +1 $; togliere o allargare i TP provvisori (un solo caso); tornare a 4 ticket (vedi *Questioni aperte*).
- **Da verificare**: quante volte MT5 rifiuta il pareggio a +1 $ e quante volte entra il pareggio ridotto (righe "pareggio ridotto" e "stop di protezione" nel racconto); pareggio +1,5 $ di nuovo quando ci saranno più giorni di trend nel rigioco.

### 11/10 — "Again" subito dopo un segnale = stesso setup; ordini solo su conto DEMO
- **Decisioni**
  - Un re-entry ("Lets buy again") scritto entro **5 minuti** da un segnale nuovo nella stessa direzione **ancora aperto** non apre nuove posizioni: è il commento del trader a quel segnale. Se il segnale è già chiuso (stop) o sono passati più di 5 minuti, resta un re-entry vero (a mercato, con SL e TP del segnale).
  - **Solo conto DEMO**: il risk manager rifiuta ogni ordine se il terminale non è su un conto DEMO o se il conto non si legge (`ALLOW_REAL_ACCOUNT=1` per permetterlo).
  - Racconto: all'avvio con MT5 non autorizzato scriveva "conto ⚠️ REALE"; ora "conto non leggibile".
- **Perché**
  - Dal 07 al 09/10 i re-entry riconosciuti dal bot sono stati 3, **tutti scritti da 6 a 60 secondi dopo un segnale** nella stessa direzione ("Gold sell 4133" + "Lets sell again One more time"; "Gold sell 4130" + "Lets sell One more time"; "Gold buy 4201" + "Lets buy again"): il bot apriva altre 2 posizioni a mercato, raddoppiando il rischio sullo stesso setup. Risultato dei 3 re-entry: +5 $, +38 $, −144 $ (09/10, insieme al −138 $ del segnale). Nessun "again" isolato dopo uno stop in tutti i log dal 23/09.
  - 10/10 (sabato): dalle 17:30 server senza Telegram e MT5 ("Authorization failed"), bot riavviato 41 volte in 30 minuti, tornato normale alle 18:04; con il conto non leggibile il racconto diceva "REALE".
- **Contro**: se un giorno il trader vuole davvero aggiungere un secondo ingresso subito dopo il segnale, non lo seguiamo (resta il 2% su quel setup). L'08/10 il re-entry era l'unica posizione aperta (gli ordini del segnale non sono mai stati eseguiti) e ha preso il TP1 (+38 $): con la nuova regola non ci sarebbe stato.
- **Da verificare**: "again" isolati (dopo uno stop o a distanza dal segnale), che restano re-entry veri.

### 08/10 — Layer chiuso su MT5, posizioni orfane riprese, parità al Trade Active entro 0,50 $
- **Correzioni**
  - "Secure first layer now" (classificato come layer LOWEST) segnava e1 come chiuso **solo in memoria**: su MT5 la posizione restava aperta, senza pareggio al Trade Active successivo e senza registrazione. È finita al TP (+73 $ mai registrati), ma con lo SL pieno ancora attivo per ore. Ora il layer viene chiuso su MT5 se in guadagno, mai in perdita.
  - **Controllo delle posizioni orfane** ogni 30 s: confronto tra le posizioni del bot su MT5 (magic number) e la memoria; quelle che la memoria non segue più tornano in gestione (anche dallo storico di oggi e di ieri), quelle sconosciute vengono segnalate. Avrebbe intercettato anche il caso del 05/10 (ordini eseguiti segnati come cancellati) e quello di oggi (alle 11:57 MT5 aveva 1 posizione aperta, il bot 0).
- **Regola**: al Trade Active ingressi entro 0,50 $ contano come lo stesso prezzo, quindi si chiude il TP1 e resta il TP2. Oggi SELL 4129: e2 entrato 10 centesimi peggio di e1 è stato chiuso al posto del TP1.
- **Rigioco aggiornato** alle regole attuali (2 ticket, TP provvisori) e con i **tick reali** attorno a ogni segnale e comando del trader: prezzo esatto del momento e stop toccati nei secondi dopo, che le candele da un minuto non vedono. Nuove varianti: protezione (senza, da ½, solo ¾), incasso al Trade Active solo da +1 $, tolleranza d'ingresso oltre il bordo (0,5 / 1 / 2 $), senza TP provvisori.
- **Da decidere con il rigioco** (decisi l'11/10, vedi sopra: tutto confermato):
  - **Stop di protezione**: oggi 2 casi (BUY 4134: Trade Active arrivato con il prezzo già tornato all'ingresso, protezione colpita in 1 minuto, poi il prezzo sale di 11 $; SELL 4121: colpita in 14 secondi). In totale 8 protezioni colpite dal 02/10: in 6 il prezzo è poi andato a favore, in 1 (06/10 BUY 4165) ha evitato uno stop pieno, 1 incerta (08/10 SELL 4121).
  - **Ingresso con il prezzo appena oltre il bordo**: oggi 3 segnali vinti persi con il prezzo 0,19 / 0,69 / 0,91 $ oltre il bordo (SELL 4119, 4120, 4127). La regola "mai fuori zona" del 01/10 nasceva da un ingresso 2,32 $ oltre.
  - **Incasso al Trade Active quasi a zero**: e1 chiusi a +0,04 $ e +0,32 $ di prezzo (il Trade Active arriva quando il prezzo è già tornato indietro).

### 07/10 — TP provvisori e SL sempre presente
- **Decisioni**
  - **TP provvisori** a ±10/±20 $ dal prezzo del segnale rapido, sostituiti da quelli del trader alla sua modifica (come lo SL provvisorio).
  - **SL rimesso** se una posizione del bot risulta su MT5 senza SL (evento `STOP_RESTORED` nel diario e riga nel racconto).
- **Perché**: due segnali rapidi mai completati dal trader in due giorni. 06/10 BUY 4158: solo SL provvisorio, preso (−51 $). 07/10 SELL 4133 delle 09:32: aperto a 4136 con SL 4143 (accettato da MT5 secondo il diario) e senza TP, mai più seguito dal trader; resta aperto 6 ore finché non viene chiuso a mano a 4083 (+663 $). Il trader usa sempre TP a 10 e 20 $ dal segnale.
- **Contro**: con i TP provvisori il SELL 4133 avrebbe chiuso a 4123/4113 (circa +225 $ invece di +663 $), ma senza bisogno di nessuno davanti allo schermo. Lo SL rimesso annulla anche una rimozione fatta a mano da MT5.
- **Scartato**: lasciare le posizioni senza TP e chiuderle a mano (rischioso quando nessuno guarda).
- **Da verificare**: quante volte il trader non completa un segnale; se i TP provvisori vengono presi prima della sua modifica.

### 06/10 (sera) — Pareggio non perso con MT5 scollegato, limite rifiutato → a mercato
- **Correzioni**
  - Se al Trade Active/pareggio un ordine in attesa non si può cancellare perché MT5 è scollegato, la richiesta di pareggio resta in memoria: se l'ordine risulta poi eseguito va a pareggio, se è ancora in attesa viene cancellato.
  - Ordine limite rifiutato con "Invalid price" perché il prezzo è appena arrivato al livello: si entra a mercato.
- **Perché**: 06/10 BUY 4170: MT5 scollegato dalle 15:11 alle 15:17, Trade Active e "Hold it with BE" arrivati in quei minuti, ordini eseguiti nel frattempo, al ritorno di MT5 nessun pareggio → stop pieno alle 15:39 (−83 $). BUY 4158 delle 15:45: e1 rifiutato ("Invalid price"), e2 entrato a mercato un istante dopo.
- **Nota**: il 06/10 il server ha fatto un push forzato che ha cancellato da GitHub il merge dei 2 ticket (ripristinato con un nuovo merge). I push dal server vanno fatti solo con pull + push, mai forzati.
- **Da verificare**: altre disconnessioni di MT5 durante un comando del trader; anche la chiusura totale ("Close all") con MT5 scollegato resta da gestire (oggi l'operazione resta "CLOSE_FAILED").

### 06/10 — Solo e1/e2 con tutto il rischio; nessun margine sullo SL né minimo sul pareggio
- **Decisioni**
  - **2 ticket per segnale** (`ENTRY_TICKETS=2`): e1 sul TP1 ed e2 sul TP2, al bordo della zona, **1% di rischio ciascuno** (prima 4 ticket, e3/e4 a metà lotto, rischio 1,5%).
  - **Confermati e messi per iscritto**: pareggio subito alla richiesta del trader (nessun guadagno minimo) e SL esattamente quello del trader (nessun margine). Il codice era già così: nessuna modifica.
- **Perché**: rigioco dei messaggi del trader dal 24/09 al 05/10 (`prezzi.py rigioco`, 86 segnali, rischio fisso 140 $ per segnale, candele M1 di MT5; `logs/rigioco.txt`). Il rigioco riproduce bene il reale del 02 e del 05/10 (a parte gli stop di protezione, su cui è più indulgente).

  | Strategia | Totale | Rischio max per segnale | Resa per 1 $ rischiato |
  |---|---|---|---|
  | solo e1/e2 | +217 $ | 70 $ | 3,1 |
  | regole del 05/10 (e3/e4 a metà lotto) | +214 $ | 105 $ | 2,0 |
  | e3/e4 lotto pieno | +224 $ | 140 $ | 1,6 |

  e3/e4 aggiungono rischio senza rendere (entrano quasi solo quando il prezzo va contro): con tutto il rischio su e1/e2 il periodo avrebbe reso circa **+430 $**.
  - Pareggio con minimo: 3 $ → +60 $, 5 $ → +139 $ (peggio, come il 25/09). Margine sullo SL: +1 $ → +119 $, +2 $ → −63 $ (salva lo stop "per poco" del 02/10 BUY 4188, ma costa su tutti gli altri).
  - Senza pareggio su richiesta: "mai BE" −749 $, "BE al TP1" −655 $, "TP1 senza BE" −272 $: **il pareggio su richiesta è la regola che conta di più**.
- **Scartato / rimandato**
  - BE +1 $ invece di +0,5 (+278 $, meglio in 7 giorni su 8 ma di poco; con +1 $ MT5 rifiuterebbe più pareggi): da riconsiderare dopo aver contato i rifiuti. **Adottato l'11/10** con il rigioco sui tick.
  - BE a metà rischio (+380 $ ma tutto dal 24/09: senza quel giorno peggio in 6 giorni su 8).
  - Stop di protezione da ½ o tolto: il rigioco non lo misura bene, resta com'è.
- **Contro**: si entra solo al bordo (niente ingressi migliori dentro la zona); ogni stop pieno costa di nuovo il 2% (−140 $ invece di −105 $); in compenso i ticket entrano tutti al prezzo migliore della zona.
- **Da verificare**: risultato per segnale con 2 ticket; numero di stop pieni rispetto ai giorni con 4 ticket.

### 05/10 — Ordini eseguiti mai più "cancellati", e3/e4 a metà lotto, script dei prezzi
- **Decisioni**
  - **Correzione**: prima di Trade Active, pareggio e chiusure gli ordini limite vengono riletti da MT5; se una cancellazione trova l'ordine già eseguito, il ticket resta aperto e viene gestito (incasso, pareggio o chiusura). Dopo una modifica scartata perché incoerente anche gli ordini in attesa riallineano SL/TP alla memoria.
  - **e3/e4 a metà lotto** (`ENTRY_DEEP_LOT_FACTOR=0.5`): rischio totale 1,5% invece del 2%.
  - **`prezzi.py`**: script in sola lettura sulle candele M1 (escursioni dopo ogni segnale, SL del trader toccato "per poco", ritorno contro dopo la richiesta di pareggio, effetto di un margine sullo SL).
- **Perché**
  - 05/10 SELL 4151: e1–e3 eseguiti tra due controlli (ogni 30 s); al Trade Active delle 08:22 il bot li ha "cancellati" (MT5 non li trovava più fra gli ordini in attesa e rispondeva ok) → 3 posizioni senza gestione finite allo stop 4161: **−104,87 $ mai registrati** (saldo −640 $ contro −535 $ delle schede).
  - 02 e 05/10: i **6 stop pieni avevano tutti e 4 i ticket eseguiti**, mentre nei trade vinti e3/e4 venivano quasi sempre cancellati al pareggio: entrano soprattutto quando il prezzo va contro. A metà lotto quei 6 stop sarebbero costati circa 200 $ in meno; i guadagni persi sui trade vinti con e3/e4 eseguiti sarebbero stati circa 11 $ il 05/10.
- **Scartato / rimandato**
  - Eliminare e3/e4 (≈ −400 $ di perdite in meno, ma niente più riempimenti in zona).
  - Primo gradino dello stop di protezione più largo (4 protezioni su 4 scattate il 02 e il 05/10 e poi prezzo tornato a favore): si decide con i dati di `prezzi.py`.
  - Margine sullo SL del trader (BUY 4188 del 02/10 e BUY 4157 del 05/10: nostro stop, il trader dichiara vittoria): si decide con i dati di `prezzi.py`.
- **Da verificare**: rischio effettivo per operazione (~1,5%); se e3/e4 a metà lotto riducono davvero le perdite degli stop pieni; nessun nuovo caso di ordini segnati come cancellati senza un CANCEL su MT5 nel diario.

### 01/10 (sera) — Pulizia del repository
- `main` allineato al codice per Windows e usato per lo sviluppo; `claude-branch` eliminato; `server` = `main` + RPyC + log.
- Eliminati `doc/TODO` (punti ancora aperti spostati qui sotto in *Questioni aperte*), `doc/requirements.txt` (doppione: ora un solo `requirements.txt` in radice) e `report.py` (sostituito da `bot.log` e `operazioni.txt`); scritto il `README.md`; eliminato anche `doc/Opzioni_gestione_Trade_Active.md` (opzioni del 29/09, superate dalle decisioni del 30/09 e 01/10).

### 01/10 — Ingresso sempre nella zona, Trade Active che lascia un ticket, stop di protezione
- **Decisioni**
  - Ingresso `range`: e1/e2 subito al prezzo del segnale (o in attesa al bordo), e3/e4 a metà e ¾ di zona. Mai fuori zona.
  - Trade Active: chiusi al massimo 2 ticket, quelli entrati peggio; ne resta sempre almeno uno; nessuna soglia pips; mai in perdita.
  - Stop di protezione a gradini quando MT5 rifiuta il pareggio.
  - Niente invio di modifiche SL/TP identiche ("No changes").
- **Perché**: 01/10 e1 entrato a mercato 2,32 $ fuori zona (SELL 4155) → in perdita al Trade Active, pareggio rifiutato, stop pieno (−32,64 $). La soglia "metà dei pips" non veniva quasi mai raggiunta (un solo incasso su 9 Trade Active). Il pareggio rifiutato lasciava lo stop pieno: 23 rifiuti dal 24/09.
- **Scartato**: tutti e 4 subito al bordo (rischio pieno su ogni operazione, anche le perdenti); chiudere anche l'ultimo ticket (nessuna possibilità di TP); chiudere i più in profitto (con il nuovo ingresso hanno lo stesso prezzo); limite di perdita giornaliera e di operazioni contemporanee (proposto, **non voluto per ora**).
- **Da verificare**: quante volte entrano e3/e4 e se lo fanno soprattutto quando il prezzo va contro; quanto rende l'incasso al Trade Active rispetto al solo pareggio; se lo stop di protezione crea piccole perdite su operazioni che poi sarebbero tornate; segnali persi perché il prezzo era già oltre il bordo (~1 su 7).

### 30/09 — Ingresso più vicino al segnale e prima regola sul Trade Active
- **Decisioni**: e1 a mercato se il prezzo era entro 3 $ oltre il segnale; e2–e4 prima su tutta la zona, poi avvicinati ai primi 3 $ dopo 3 minuti; Trade Active: incasso del peggiore sul TP1 e del peggiore sul TP2 se guadagnano metà dei pips del trader; "close half" mai su ticket in perdita; segnali a più di 20 $ dal mercato non aperti.
- **Perché**: 30/09 BUY 4168 perso (prezzo 1,24 $ sopra il segnale, ordini tutti limite) e arrivato a HIT TP; gli ordini a 2,5 e 3,75 $ entravano nel 55% e 35% dei casi; "close half" aveva chiuso un ticket in perdita; "Gold buy 4285" con l'oro a 4185.
- **Superato il 01/10**: la tolleranza di 3 $ faceva entrare fuori zona; la soglia pips era troppo alta.

### 29/09 — Pareggio a ingresso +0,5 come default, modifiche solo sul proprio messaggio
- **Decisioni**: `BE_MODE=entry` di default (pareggio a metà zona solo su richiesta); una modifica vale solo per il proprio messaggio; recupero dei segnali persi o rifiutati; falsi edit delle reazioni ignorati; il bot non parte se mancano chiavi nel `.env`.
- **Perché**: mattina del 29/09 il pareggio a metà zona ha chiuso 5 operazioni su 5 in perdita (−11 $ ciascuna) in giornata laterale; alle 09:13 le modifiche di un segnale arrivato durante un riavvio sarebbero finite sull'operazione aperta riportando il suo SL da pareggio a 4154.
- **Da verificare**: il pareggio stretto (+0,5) chiude spesso operazioni che poi vanno al TP nei giorni di trend (28/09: ~150 $; 30/09 BUY 4185: ~130 $).

### 28/09 — HIT TP MAX non chiude, 4 ticket, re-entry corretti
- **Decisioni**: HIT TP MAX → pareggio invece di chiusura; 4 ticket per segnale; re-entry solo per "again" senza prezzo, con lo SL originale del trader; SL troppo vicini sostituiti; ordini limite eseguiti e chiusi tra due controlli registrati; log riscritti (racconto + schede).
- **Perché**: HIT TP MAX è il tabellone della serie, pubblicato più volte mentre il prezzo continua: chiudere ha fatto perdere ~195 $ il 28/09. "Gold sell 4179" collegato al sell 4180 ne ereditava lo SL già a pareggio (stop a 0,5 $, lotti ×10).
- **Scartato**: pareggio a metà zona (provato il 29/09 mattina, peggiorativo).

### 25/09 — Ingresso con ordini limite, nessun margine minimo sul BE
- **Decisioni**: ordini limite nella zona del trader invece di tutto a mercato; `BE_MIN_PROFIT=0`; correzioni di messaggi vecchi ignorate.
- **Perché**: entravamo in media 0,8 $ peggio del trader (fino a 3,4 $) e al "Trade Active" eravamo in pari o in perdita; il BE con 3 $ di guadagno minimo ha portato −638 $ (22 stop su 24 chiusure). **Lezione: le modifiche di strategia vanno verificate sui dati prima di metterle in produzione.**

### 23–24/09 — Prima messa a punto
- Rischio 2% calcolato con `order_calc_profit` (prima ~12% reale), tetto sul margine, SL provvisorio 10 $; chiusura parziale; `BE_OFFSET=0.5` (un BE esatto si chiudeva a 0,00); filtro mittenti; log per giorno in ora italiana; cache del prompt (~0,13 $/giorno di agente).

---

## Questioni aperte
- **Rigioco** (`prezzi.py rigioco`, con i tick): da rilanciare man mano che si aggiungono giornate (secondo rigioco dell'11/10: regole attuali +497 $ dal 24/09 al 09/10, `logs/rigioco_tick.txt`). Il primo rigioco del 20/10 ha trovato con la calibrazione un **difetto del simulatore**: i messaggi "close half and BE" (10 nel periodo) perdevano il pareggio (13/10 SELL 4187: +210 $ nel rigioco, −31 $ reale), corretto con uno scenario in `tests/test_rigioco.py`. **Rigioco corretto del 20/10** (24/09–20/10, 200 segnali, tick in 394 finestre su 395; calibrazione: 13/10 +118 $ contro +119 $ reali, 19/10 +159 / +152 $, 20/10 +214 / +213 $): regole attuali **+557 $** (+611 $ fino al 09/10, −52 $ dal 12/10). Confermati: pareggio +1 $ (+1,5 $ → −202 $, +2 $ → −392 $), mai ingressi oltre il bordo (0,5 $ → −191 $), protezione (vedi sotto); "TA senza incasso" (+15 $) e "protezione solo ¾" (+83 $) sono rumore; "senza TP provvisori" +228 $ sempre e solo per il 07/10 SELL 4133. Le due varianti nuove ("e2 senza pareggio" +1.641 $, "pareggio a metà rischio" +1.207 $): vedi *Pareggio colpito subito dopo il Trade Active*.
- **Pareggio +1 $ rifiutato e stop di protezione** (dati 12–16/10, prima settimana con +1 $): MT5 ha rifiutato il pareggio su **35 ticket su 48** (73%), quasi sempre perché al Trade Active il prezzo era già tornato all'ingresso (e1 in perdita in 10 Trade Active su 37). Dei 35: 11 pareggi pieni entrati poco dopo (+70 $), 6 pareggi ridotti (+20 $), **18 stop di protezione colpiti (−344 $, 10 segnali)**, tutti su segnali che il trader dà vinti. Il rigioco dell'11/10 dava la protezione a +182 $ rispetto a niente protezione; resta da vedere con il rigioco quanti di questi 10 sarebbero finiti allo stop pieno senza protezione. 19/10: pareggio rifiutato su 5 ticket su 9: 2 pareggi pieni entrati dopo un minuto e mezzo (+12 $), 2 pareggi ridotti (+6 $), 1 stop di protezione colpito dopo 41 secondi (BUY 4179, −16 $; poi il prezzo sale di 16 $, il trader 115 pips). 20/10: pareggio rifiutato su 11 ticket su 15 (e1 in perdita in 5 Trade Active su 10): 1 pareggio pieno entrato dopo un minuto e mezzo, **10 stop di protezione colpiti (−164 $, 5 segnali)**. Qui la protezione ha probabilmente evitato 3 stop pieni: SELL 4121, 4126 e 4130 della notte, con il prezzo salito poi a 4.144 oltre i loro SL (4.131 / 4.136 / 4.140) senza nessun HIT TP del trader in mezzo; è costata invece sul SELL 4128 (−38 $, il trader lo porta al TP). La protezione vince o perde a seconda del giorno: va decisa con il rigioco, non con un caso. **Rigioco corretto del 20/10: la protezione resta.** Senza protezione −444 $ (peggio in entrambe le metà del periodo: −156 $ fino al 09/10, −290 $ dal 12/10): 12 segnali passano da −35 a −140 $ (tra cui i 3 SELL della notte del 20/10, 2 del 14/10 e 1 del 13/10), 20 migliorano di 42–147 $. Da ½: −507 $. Solo ¾: +83 $, rumore (20 segnali meglio, 15 peggio, perdite da −105 $ invece di −35 $).
- **Pareggio colpito subito dopo il Trade Active** (19/10): il Trade Active arriva con il prezzo già a 1–2 $ dall'ingresso (il trader sembra contare i pips dal bordo lontano della zona: BUY 4179, zona 4174–4179, "Running 60+ pips" con il prezzo a 4.179–4.180; 20/10 SELL 4121, 4126, 4130: "Running 40–50+ pips" con il nostro e1 in perdita), il pareggio a +1 $ viene colpito in 9–18 secondi su 6 ticket (SELL 4171, 4166, 4167, BUY 4176) e poi il prezzo va al TP2 di tutti (il trader chiude le vendite a 250–420 pips). 20/10 di nuovo su 3 ticket (SELL 4129 dopo 31 s, 4123 dopo 66 s, 4122 dopo 2,8 minuti), con il trader a HIT TP MAX. Un pareggio più largo qui non entrerebbe nemmeno (sarebbe oltre il prezzo). Il pareggio solo con un guadagno minimo è stato scartato il 06/10 (candele M1, 4 ticket) e il 25/09 (−638 $). **Rigioco corretto del 20/10**, rispetto alle regole attuali (+557 $):

  | Variante | Totale | fino al 09/10 | dal 12/10 | giorni meglio / peggio | stop pieni | segnali vinti | peggior giorno | calo massimo | senza i 3 giorni migliori |
  |---|---|---|---|---|---|---|---|---|---|
  | regole attuali | +557 $ | +611 $ | −52 $ | — | 24 | 110 su 200 | −616 $ | −1.785 $ | −1.605 $ |
  | **e2 senza pareggio** (e1 incassato, e2 tiene lo SL del trader) | **+1.641 $** | +1.014 $ | +627 $ | 7 / 12 | 47 | 62 | −995 $ | −2.754 $ | −2.843 $ |
  | pareggio a metà rischio | +1.207 $ | +502 $ | +705 $ | 8 / 11 | 24 | 51 | −838 $ | −2.368 $ | −2.279 $ |
  | mai pareggio | +457 $ | −1.084 $ | +1.541 $ | 9 / 10 | 99 | 75 | −1.465 $ | −4.723 $ | −5.103 $ |

  "e2 senza pareggio" è avanti in tutte e due le metà del periodo, ma è una scommessa a ogni Trade Active: 48 segnali meglio (quasi sempre +133 / +157 $, e2 al TP2 invece che a pareggio), 81 peggio (−77 $: e2 allo stop pieno invece che a pareggio; −52,5 $: allo stop pieno invece che alla protezione). Il punto di pareggio della scommessa è e2 al TP2 in circa il 37% dei casi, ed è proprio quello osservato (48 su 129): il guadagno viene dai giorni di trend (24/09 +1.052 $, 07/10 +1.153 $, 13/10 +966 $) e dai TP del trader oltre i 20 $. Peggio in 12 giorni su 19, il doppio degli stop pieni, calo massimo più grande del 50%. Le varianti sono state pensate guardando il 19–20/10: **prova fuori campione** sui giorni dal 21/10 prima di proporre di adottarla.
- **Riavvii non spiegati** il 14/10 alle 08:07 e 08:11 e il 15/10 alle 08:03 (06:03–06:11 UTC, senza arresto registrato né rilascio): il 14/10 si è perso un BUY (msg 59121, con la sua modifica) che il trader ha chiuso al TP (175 pips). Da cercare la causa sul server (`journalctl`); valutare la lettura all'avvio degli ultimi messaggi del canale.
- **"Cut loss" dichiarato dal trader**: 12/10 "this setup i cut lose 30pips" classificato IGNORE; il trader ha chiuso il BUY 4165 a −30 pips, noi 4 minuti dopo allo stop pieno (−125 $ invece di circa −42 $). Unico caso nei log (gli altri "cut lose if solid break X" sono condizionati e diventano SL).
- **Risultato legato ai giorni di trend**: dal 24/09 al 09/10 tutto il guadagno viene dal 28/09 (+1.420 $ con le regole attuali); negli altri 11 giorni le regole perdono 923 $ (vedi 11/10).
- **2 o 4 ticket**: i 2 ticket rendono di più in totale ma solo per il 28/09; i 4 ticket sono andati meglio in 8 giorni su 12 (meno esposti). Da rivedere con più giorni.
- **Asimmetria vinti/persi**: un trade vinto rende +10–30 $, uno stop pieno costa circa −140 $.
- **Segnali solo immagine** (NFP del 02/10): invisibili al bot; unico caso noto. I segnali delle notizie (NFP, CPI) sono dati nel canale privato: qui arriva solo il risultato inoltrato ("NEWS WIN"). Lettura delle immagini scartata il 20/10 per le foto dei comandi.
- Pareggio più largo di +1 $: +1,5 $ quasi uguale in totale (+23 $) ma taglia i vincenti nei giorni di trend, +2 $ peggio (vedi 11/10); da rivedere con più giorni di trend.
- Gestione dei Trade Active su più righe oltre al pareggio.
- Gestione di HIT TP / HIT TP MAX oltre al pareggio (da rivedere solo con test positivi).
- Limiti di rischio complessivo (operazioni contemporanee, perdita giornaliera): proposti il 01/10, rimandati.
- **Riavvii a raffica senza rete** (10/10: 41 riavvii in 30 minuti, nessun danno a mercato chiuso): valutare un'attesa crescente tra un tentativo e l'altro per non riempire i log.
- Rischio per segnale: il 2% è fisso nel codice. Con 4 ticket l'arrotondamento dei lotti lo riduceva a circa 1,2% effettivo (06/10: stop pieni da −83 $); con 2 ticket è circa 1,8% (−125 $). Valutare un parametro `RISK_PERCENT`.
- Segnale rapido mai completato dal trader: dal 07/10 ha SL e TP provvisori. Resta da decidere se gestirlo anche nel tempo (es. pareggio dopo un certo guadagno, visto che nessun Trade Active arriverà).
- **MT5 scollegato spesso** (06/10 una volta, 07/10 quattro volte, da 1 a 7 minuti; dal 12 al 16/10 nove volte, da 30 secondi a 8,5 minuti, senza danni; 17/10, sabato a mercato chiuso, cinque volte, fino a 12,5 minuti; 19/10 due volte, 4,5 minuti e 30 secondi, senza operazioni aperte; 20/10 un minuto alle 00:26): controllare la connessione del terminale sul server.
- **Segnali mai entrati**: dal 12 al 16/10 cinque, con il prezzo da 1,1 a 1,7 $ oltre il bordo, tutti dati vinti dal trader (Trade Active 45–60 pips); 19/10 tre, con il prezzo 0,25 / 0,80 / 1,62 $ oltre il bordo, tutti vinti dal trader (50 pips poi pareggio, 100 pips, HIT TP 180 pips); 20/10 uno (SELL 4121 delle 14:20, 1,10 $ oltre, Trade Active 70 pips). Il rigioco dell'11/10 scartava le tolleranze da 1 e 2 $ (−320 / −268 $). **Il ritardo non è del bot**: nel rigioco del 20/10 sera, entrando all'arrivo del messaggio invece che dopo l'agente (~3,5 s prima), dei 30 segnali mai entrati dal 24/09 se ne recupera 1 (08/10 SELL 4119); gli altri 29, compresi quelli del 12–20/10, avevano il prezzo oltre il bordo già quando il messaggio è arrivato. Da misurare con il prezzo alla GIF che precede il segnale (diario dal 20/10 sera), confrontato con il prezzo del segnale e quello all'ordine (`RISK_CALC.price`): se il prezzo del segnale è quello della GIF, il trader scrive il prezzo di quando ha deciso e il ritardo è suo.
- Notizie ad alto impatto (NFP, CPI…): operare o no durante la pubblicazione.
- "Cut loss if solid break X": oggi diventa uno SL rigido; valutare uno SL "morbido" che chiude solo a candela chiusa oltre il livello. 14/10 SELL 4147: stop pieno a 4157 (−125 $), il trader "still hold it, it still not solid break 4157" e poi lo chiude al TP (210 pips).
- Più esempi reali nel prompt dell'agente, man mano che arrivano messaggi nuovi.
