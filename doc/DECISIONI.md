# Decisioni e regole del bot

Documento di riferimento per non perdere il filo: **come si comporta il bot oggi** e **perché abbiamo scelto così**.

**Regola di lavoro:** a ogni sessione in cui cambiamo una regola di trading (non per le correzioni di bug ovvie) si aggiorna questo file nello stesso push del codice:
1. la sezione *Regole in vigore* si riscrive con lo stato nuovo;
2. si aggiunge in cima al *Registro* una voce con data, decisione, motivo (con i dati), alternative scartate e cosa resta da verificare.

Prima di proporre una modifica si controlla qui che non contraddica una scelta già fatta, o che, se la cambia, lo faccia consapevolmente.

---

## Regole in vigore (aggiornato al 06/10/2026)

### Segnali e classificazione
- Si ascolta solo il canale Maestro Fx; l'agente (Claude Haiku) classifica ogni messaggio in NEW_SIGNAL / UPDATE_SIGNAL / CLOSE_SIGNAL / IGNORE.
- Prima dell'agente vengono scartati: messaggi senza testo, inoltrati, senza parole operative, modifiche identiche, **falsi edit** (reazioni/visualizzazioni: senza data di modifica recente), correzioni di messaggi vecchi (> 10 minuti) che non sono segnali aperti.
- **Errore di battitura**: un segnale con prezzo a più di **20 $** dal mercato non viene aperto (`MAX_SIGNAL_DISTANCE`); la correzione del trader viene aperta dal **recupero**.
- **Recupero**: una modifica recente di un segnale mai aperto (perso durante un riavvio, o rifiutato) viene trattata come segnale nuovo; mai riaperto un segnale già chiuso.
- Una **modifica** vale solo per il proprio messaggio (o quello a cui risponde), mai per "l'ultima operazione aperta".

### Ingresso (`ENTRY_MODE=range`)
- **2 ticket per segnale** (`ENTRY_TICKETS=2`), rischio **2%** del saldo: **1% ciascuno**.
- **e1 (TP1) ed e2 (TP2) al prezzo del segnale**: subito a mercato se il prezzo è nella zona del trader (o migliore); se è oltre il bordo dalla parte sbagliata restano limite al bordo ed entrano appena il prezzo torna nella zona. **Mai ingressi fuori zona dalla parte sbagliata.**
- Niente più e3/e4 più dentro la zona (tolti il 06/10). Restano disponibili con `ENTRY_TICKETS=4`: limite a metà e a ¾ della zona, lotto ridotto da `ENTRY_DEEP_LOT_FACTOR`.
- Zona: quella scritta dal trader; nella fase rapida ("Gold buy 4186") si assume larga 5 $ a favore del trader.
- SL provvisorio a 10 $ dal segnale finché il trader non scrive il suo; SL a meno di 3 $ o dalla parte sbagliata → provvisorio.
- Ordini limite non eseguiti: cancellati dopo 30 minuti, o al primo comando di pareggio/Trade Active/chiusura.
- Prima di eseguire un comando del trader (Trade Active, pareggio, chiusure) lo stato degli ordini limite viene **riletto da MT5**: un ordine già eseguito è gestito come posizione aperta, mai segnato come cancellato.

### "Trade Active … Running N+ Pips" (con BE+)
- Si chiudono **al massimo 2 ticket, quelli entrati al prezzo peggiore**, e **ne resta sempre aperto almeno uno** (con 2 ticket: 1 chiuso, 1 a pareggio; con 1 aperto nessuno). A parità di prezzo si chiude prima il TP1 (resta il TP2).
- **Mai chiusure in perdita**; nessuna soglia sui pips. Una sola volta per operazione.
- I ticket rimasti vanno a **pareggio**; gli ordini non eseguiti vengono cancellati.
- Messaggio su più righe di pips: la regola vale per l'operazione a cui risponde (ultima riga); le altre operazioni aperte nella stessa direzione vanno solo a pareggio.

### Pareggio (BE) e protezione
- Pareggio = ingresso **± 0,50 $** dalla parte del guadagno (`BE_MODE=entry`, `BE_OFFSET=0.5`).
- **Stop di protezione**: se MT5 rifiuta il pareggio (siamo in perdita o troppo vicini) lo stop si avvicina al primo gradino accettato tra ¼, ½, ¾ della distanza ingresso–stop; ogni 30 secondi si riprova il pareggio vero e si migliora la protezione.
- Uno SL già più protettivo del pareggio non viene mai peggiorato.
- **Pareggio subito quando lo chiede il trader**, senza guadagno minimo (`BE_MIN_PROFIT=0`), e **SL esattamente quello del trader**, senza margini: entrambe le alternative verificate e scartate il 06/10.

### Altri messaggi del trader
- **HIT TP** (con BE+): pareggio, nessuna chiusura.
- **HIT TP MAX**: è il tabellone della serie → **pareggio su tutto, nessuna chiusura**. Chiusura totale solo per comandi espliciti ("Close all", "Close now").
- **"Close half" e simili**: chiude la percentuale chiesta, prima i ticket con il TP più lontano, **mai ticket in perdita**; ripetizione entro 15 minuti = stesso comando.
- **Re-entry** ("Try buy again", senza prezzo): eredita SL e TP *scritti dal trader* dall'ultima operazione nella stessa direzione (aperta, o chiusa nell'ultima ora), entra a mercato; i messaggi sul segnale padre valgono anche per lui.
- Un segnale con prezzo non è mai un re-entry, anche se vicino a uno aperto.

### Log e repository
- Per giorno: `bot.log` (racconto), `operazioni.txt` (schede), `dettagli.log`, `errors.log`, `journal.jsonl` (dati). Heartbeat ogni 4 ore.
- Sul server i log si caricano con **pull e poi push, mai push forzati**.
- Branch: si sviluppa su **`main`** (Windows); **`server`** = `main` + collegamento RPyC + log, aggiornato unendo `main`. (`claude-branch` eliminato il 01/10.)

### Parametri `.env` (valore di default)
| Parametro | Default | Note |
|---|---|---|
| `ENTRY_MODE` | `range` | `zone` = ingresso in due fasi (30/09), `market` = tutto a mercato |
| `ENTRY_TICKETS` | 2 | 4 = anche e3/e4 dentro la zona (fino al 05/10); **sul server non va impostato nel `.env`** |
| `ENTRY_IMMEDIATE_TICKETS` | 2 | ticket al prezzo del segnale in modalità `range` |
| `ENTRY_DEEP_LOT_FACTOR` | 0.5 | solo con `ENTRY_TICKETS=4`: lotto di e3/e4 rispetto alla quota piena |
| `BE_MIN_PROFIT` | 0 | guadagno minimo per il pareggio: deve restare 0 |
| `ENTRY_ZONE_WIDTH` | 5 | larghezza zona in fase rapida |
| `ENTRY_ORDER_EXPIRY_MINUTES` | 30 | |
| `TRADE_ACTIVE_MAX_CLOSE` | 2 | ticket incassati al Trade Active (ne resta sempre 1) |
| `BE_MODE` / `BE_OFFSET` | `entry` / 0.5 | sul server il `.env` contiene anche `BE_MODE=entry` |
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

---

## Registro delle decisioni (dal più recente)

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
  - BE +1 $ invece di +0,5 (+278 $, meglio in 7 giorni su 8 ma di poco; con +1 $ MT5 rifiuterebbe più pareggi): da riconsiderare dopo aver contato i rifiuti.
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
- **Prezzi al minuto**: `prezzi.py` (analisi di un giorno) e `prezzi.py rigioco` (confronto di strategie) disponibili; da rilanciare man mano che si aggiungono giornate, soprattutto per BE +1 $ e stop di protezione.
- **Stop di protezione**: primo gradino (¼ = 2,5 $) forse troppo stretto, 4 su 4 scattati e poi prezzo tornato a favore (02 e 05/10).
- **Asimmetria vinti/persi**: un trade vinto rende +10–30 $, uno stop pieno costa circa −140 $.
- **Segnali solo immagine** (NFP del 02/10): invisibili al bot; possibile lettura con visione, da valutare.
- Pareggio stretto vs più largo nei giorni di trend.
- Gestione dei Trade Active su più righe oltre al pareggio.
- Gestione di HIT TP / HIT TP MAX oltre al pareggio (da rivedere solo con test positivi).
- Limiti di rischio complessivo (operazioni contemporanee, perdita giornaliera): proposti il 01/10, rimandati.
- Segnale rapido mai completato dal trader (nessuna modifica con SL e TP): oggi resta lo SL provvisorio a 10 $ e nessun TP. Decidere se chiudere o mettere a pareggio dopo un certo tempo.
- Notizie ad alto impatto (NFP, CPI…): operare o no durante la pubblicazione.
- "Cut loss if solid break X": oggi diventa uno SL rigido; valutare uno SL "morbido" che chiude solo a candela chiusa oltre il livello.
- Più esempi reali nel prompt dell'agente, man mano che arrivano messaggi nuovi.
