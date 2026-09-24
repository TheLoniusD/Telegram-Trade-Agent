# Bot che copia i segnali di Maestro Fx su MT5: com'è andato il test del 23 e 24 settembre

*Documento per chi conosce il trading ma non l'informatica. Gli orari sono in **ora italiana**: nell'app MT5 le stesse operazioni compaiono **un'ora avanti**, perché MT5 mostra l'ora del server del broker. Gli importi sono nella valuta del conto demo.*

---

## 1. In breve

- Il bot **legge il canale Telegram del trader, capisce i messaggi e li esegue da solo su MetaTrader 5**, su un conto **DEMO** (soldi finti) e solo su **XAUUSD**.
- In due giorni ha gestito **15 segnali reali**. Li ha **riconosciuti tutti**: ha aperto le posizioni, messo SL e TP quando il trader li pubblicava, spostato a BE e chiuso sui "HIT TP MAX".
- **Il 23/09** ha chiuso a **+990**, ma con lotti circa **3 volte più grandi del previsto** per un errore nel calcolo del rischio. Il risultato non è quindi rappresentativo; l'errore è stato corretto.
- **Il 24/09**, con il rischio corretto al 2%, ha chiuso a **−29**. La causa principale è il **BE**: in 7 operazioni su 12 lo stop è stato spostato a BE e colpito entro **30 secondi – 3 minuti**, spesso poco prima che il prezzo andasse a TP. Il trader invece ha incassato quei TP.
- Tutti i problemi trovati sono stati corretti. Il più importante, il BE, è corretto ma **da tarare con i vostri consigli**: vedi le domande al punto 8.

---

## 2. Come funziona il bot

Il bot fa quattro passaggi, sempre uguali, per ogni messaggio del canale:

1. **Legge** il messaggio appena il trader lo pubblica o lo modifica.
2. **Lo interpreta** con un'intelligenza artificiale (Claude di Anthropic), che lo classifica in una di quattro categorie:
   - **nuovo segnale** (es. "Gold sell 4285");
   - **aggiornamento** (SL/TP definitivi, "BE+", "close half");
   - **chiusura** ("HIT TP MAX", "Close all");
   - **da ignorare** (celebrazioni, pubblicità, analisi, "READY US SESSION").
3. **Decide** cosa fare sull'operazione giusta, cioè quella a cui il messaggio risponde o, se non risponde a niente, l'ultima aperta.
4. **Esegue su MT5** e controlla l'esito: se il broker rifiuta un ordine lo registra e non finge che sia andato a buon fine.

**Regole di gestione del denaro usate nel test:**

- **Rischio per segnale: 2% del conto**, calcolato sullo stop loss del segnale.
- Ogni segnale apre **2 posizioni uguali**: la prima con il TP1, la seconda con il TP2.
- **Fase rapida.** Il trader prima scrive solo "Gold sell 4285", poi dopo circa un minuto modifica il messaggio aggiungendo SL e TP. Il bot entra subito a mercato con uno **SL provvisorio a 10$** (la distanza che questo trader usa sempre) e lo sostituisce con quello vero appena arriva.
- **BE:** su "BE+" lo stop va al prezzo di ingresso **+0,5$ di margine**, così un ritorno al prezzo chiude con un piccolo guadagno invece che a zero.
- Ogni 30 secondi il bot controlla MT5: se una posizione è stata chiusa da TP o SL lo registra con motivo e profitto, e se MT5 si scollega aspetta e riparte da solo.

---

## 3. Come vengono letti i messaggi del trader

Esempi reali dal canale e cosa ne ha fatto il bot:

| Messaggio del trader | Cosa capisce il bot | Cosa fa |
|---|---|---|
| "Gold sell 4285" | Nuovo segnale, fase rapida | Apre 2 SELL a mercato con SL provvisorio |
| (modifica) "GOLD SELL NOW · SELL @ 4285-4290 · SL 4295 · TP 4275 · TP 4265" | SL e TP definitivi | Mette SL 4295 su entrambe, TP 4275 sulla prima e 4265 sulla seconda |
| "Trade Active ✅ Gold Sell Running 95+ Pips · Scalpers secure ur Profits & BE+ ur entries" | Richiesta di BE | Sposta lo stop a BE (vedi punto 6) |
| "HIT TP⚡️ Gold Buy 140+ Pips · Collect all or half & BE+" | TP intermedio + BE | Solo BE: "Collect all or half" è un invito ai follower, non un ordine di chiudere |
| "Lets close half now" / "close half set your BE" | Chiusura parziale (+ BE) | Chiude metà della posizione (+ BE sul resto) |
| "HIT TP MAX⚡️ Gold Sell 340+ Pips" | Chiusura totale | Chiude tutto ciò che è ancora aperto su quel segnale |
| "Hit risk! Ready setup recovery!" | Da ignorare | Niente (lo SL l'ha già chiuso il broker) |
| "Another profitable day💪", "Thanks sir", emoji, "READY US SESSION" | Da ignorare | Niente, e senza nemmeno interrogare l'AI (risparmio) |

---

## 4. Giorno 1 — martedì 23 settembre (12:26 – 16:13, dal PC di casa)

| Segnale | Cosa è successo | Risultato |
|---|---|---|
| **BUY 4302** (13:35) · SL 4292 · TP 4312 / 4322 | Apertura 2 × 0,47 lotti a 4302,74. "Lets close half now" (13:40) **ignorato**, perché la chiusura parziale non esisteva ancora. BE rifiutato alle 13:42 (prezzo troppo vicino), applicato alle 13:48 su "Trade Active BE+". **TP1 a 4312** alle 14:00; la seconda torna al BE alle 14:40 | **+381** |
| **SELL 4314** (14:05) · SL 4324 · TP 4304 / 4294 | Apertura 2 × 0,41 a 4313,72. BE alle 14:22 su "Trade Active", **entrambe chiuse al BE esatto due minuti dopo** | **0** |
| **SELL 4314** (14:25) · SL 4324 · TP 4304 / 4294 | Apertura 2 × 0,40 a 4313,07. BE alle 14:32. **TP1 a 4304** alle 14:39; su "HIT TP MAX" (14:44) il bot **chiude la seconda a 4304,78** | **+609** |
| **Totale** | | **+990** |

**Problemi scoperti quel giorno, tutti corretti:**

- **Lotti troppo grandi: rischio reale circa 12% invece del 2%.** Il calcolo della perdita allo SL era sbagliato e lo limitava solo il margine disponibile. Ora il valore di ogni punto lo calcola MT5 stesso. *Con il rischio corretto quel +990 sarebbe stato circa un terzo.*
- **"Close half" ignorato:** ora il bot chiude davvero metà posizione.
- **BE esattamente al prezzo di ingresso** → chiusure a 0,00. Ora il BE ha +0,5$ di margine.
- **Moltissimi messaggi ripetuti.** Il canale ripubblica spesso i propri messaggi senza cambiarli (15 modifiche su 20 erano identiche) e inoltra le testimonianze dei follower. Ora vengono scartati prima dell'AI.

---

## 5. Giorno 2 — mercoledì 24 settembre (tutto il giorno, dal server)

Il bot ha girato da solo tutto il giorno. Numeri della giornata:

- **130 messaggi** ricevuti, più 150 solo immagini. Solo **45** sono arrivati all'AI; gli altri sono stati scartati prima: modifiche identiche, testimonianze inoltrate, celebrazioni.
- Costo dell'AI per tutta la giornata: **circa 0,12 $**.
- Due brevi disconnessioni di MT5 dal broker (09:52–09:59 e 14:44–14:49), superate da sole.

| # | Segnale del trader | Messaggi successivi del trader | Cosa ha fatto il bot | Risultato |
|---|---|---|---|---|
| 1 | **SELL 4285** (04:20) · SL 4295 · TP 4275 / 4265 | 04:25 "Running 40+ pips, BE+" | BE non applicabile: la nostra posizione non era in guadagno. **Stop loss** alle 04:39 | **−151** |
| 2 | **SELL 4285** di nuovo (04:25), secondo ingresso | 04:40 "Hit risk!" (anche il trader a SL) | **Stop loss** alle 04:39 | **−159** |
| 3 | **SELL 4295** (04:44) · SL 4305 · TP 4285 / 4275 | 04:54 "Running 95+, BE+" · 05:43 "HIT TP 140+" | BE alle 04:54. **TP1 a 4285** alle 05:35; la seconda al BE alle 07:04 | **+89** |
| 4 | **SELL 4289** (07:00) · SL 4299 · TP 4279 / 4269 | 07:17 "Running 100+, BE+" | BE alle 07:17, **chiuse al BE 3 minuti dopo** | **+11** |
| 5 | **SELL 4286** (07:39) · SL 4296 · TP 4276 / 4266 | 07:46 "Running 160+, BE+" | BE rifiutato (troppo vicino), applicato alle 07:49, **chiuse 1 minuto dopo** | **+6** |
| 6 | **SELL 4284** (07:49) · SL 4294 · TP 4274 / 4264 | 07:59 "Running 185+, BE+" | BE alle 07:59, **chiuse 35 secondi dopo** | **+6** |
| 7 | **SELL 4282** (08:02) · SL 4292 · TP 4272 / 4262 | 08:17 "Running 60+, BE+" | BE in attesa, applicato alle 08:33, **chiuse 30 secondi dopo** | **+7** |
| 8 | **SELL 4284** (08:20) · SL 4294 · TP 4274 / 4264 | 08:31 "Running 60+, BE+" · **08:40 "HIT TP 210+ pips"** | BE alle 08:31, **chiuse 30 secondi dopo**, prima del TP del trader | **+7** |
| 9 | **SELL 4283** (09:07) · SL 4293 · TP 4273 / 4263 | 09:24 "Running 70+, BE+" · 10:00 "Running 120+" · **10:30 "HIT TP MAX 340+ pips"** | BE in attesa, applicato alle 09:42, **chiuse al BE alle 09:43**. L'"HIT TP MAX" delle 10:30 non ha trovato più nulla da chiudere | **+7** |
| 10 | **BUY 4262** (13:36) · SL 4252 · TP 4272 / 4282 | 14:01 "Running 80+, BE+" | BE in attesa, applicato alle 14:02, **chiuse 30 secondi dopo** | **+7** |
| 11 | **"Gold buy 4362"** (14:02), errore di battitura per 4262 | 14:03 il trader corregge in "BUY @ 4262" · poi "Running 80+ … 135+" | Ingresso a 4362 **rifiutato dal broker** (corretto). La correzione successiva però **non è stata raccolta**: secondo ingresso perso | **0** |
| 12 | **BUY 4270** (14:32) · SL 4260 · TP 4280 / 4290 | 14:45 "HIT TP 160+, BE+" · 15:03 "HIT TP MAX 265+" | BE alle 14:49 (dopo la disconnessione). **TP1 a 4280** alle 14:55; su "HIT TP MAX" il bot **chiude la seconda a 4280,15** | **+141** |
| | **Totale** | | | **−29** |

---

## 6. L'analisi: perché il 24/09 è andato peggio del trader

**Le due perdite piene della mattina (−310) sono state perse anche dal trader** ("Hit risk!"). Fanno parte del gioco.

Il problema vero è il **Breakeven**:

- **7 operazioni su 12 sono state chiuse al BE entro 30 secondi – 3 minuti dallo spostamento dello stop**, con guadagni di +6/+11.
- In almeno 3 casi (#8, #9, #10) il trader ha poi annunciato **HIT TP / HIT TP MAX**. Con lo stop originale, quelle posizioni avrebbero potuto incassare TP1 e TP2 (ordine di grandezza **200 a operazione**). È una stima: va confermata sullo storico dei prezzi (punto 7).

**Perché succede.** Il trader e il bot non entrano allo stesso prezzo.

- Il trader scrive "SELL @ **4286 – 4291**": vende in una **zona** e conta i pips da lì.
- Il bot entra **subito a mercato**, al prezzo del primo messaggio (es. 4283,75), cioè nella parte meno favorevole della zona.
- Quando il trader scrive "Running 90 pips, BE+", **lui è in guadagno, noi siamo quasi in pari**. Il suo BE ha margine, il nostro no: basta il rimbalzo normale del prezzo per chiuderci.

Per lo stesso motivo il BE "in attesa" peggiorava le cose. Il bot lo applicava **appena il broker lo accettava**, cioè con il prezzo praticamente sullo stop: nei 6 casi in cui è successo, la posizione si è chiusa entro un minuto.

---

## 7. Le modifiche fatte dopo il test

| Problema visto | Soluzione adottata |
|---|---|
| Rischio reale ~12% invece del 2% | Lotti calcolati sul valore reale del punto fornito da MT5 |
| SL provvisorio (5$) diverso da quello vero del trader (10$) | SL provvisorio a 10$: i lotti calcolati restano validi anche dopo lo SL definitivo |
| "Close half" ignorato | Chiusura parziale reale. Chiude prima la posizione con il **TP più lontano** e tiene quella col TP vicino, più probabile da raggiungere. Se il trader ripete "close half" entro 15 minuti, non chiude un'altra metà |
| BE al prezzo esatto → chiusure a 0 | BE a ingresso **+0,5$** |
| BE colpito in pochi secondi (24/09) | **BE solo se la nostra posizione è in guadagno di almeno 3$**, altrimenti resta in attesa e lo stop originale continua a proteggere. Il valore va tarato: vedi sotto |
| Secondo "Gold sell 4285" gestito come segnale separato | Un secondo ingresso con stessa direzione e stesso prezzo è collegato al primo: un "HIT TP MAX" li chiude entrambi |
| Tanti messaggi inutili passati all'AI | Scartati prima: modifiche identiche, testimonianze inoltrate, messaggi senza parole operative. Costo sceso a **circa 0,12 $/giorno** |
| Orari dei log sfalsati | Tutto in ora italiana, con accanto l'ora MT5 per il confronto con l'app |
| Disconnessioni di MT5 | Riconnessione automatica e controllo ogni 30 secondi |

**Verifica sullo storico dei prezzi.** Per scegliere il margine del BE con i numeri e non a intuito, è pronto uno strumento che rigioca le operazioni del 24/09 sulle candele a 1 minuto di MT5 con regole diverse: nessun BE, BE con 0, 2, 3 o 5$ di margine. I risultati arriveranno nei prossimi giorni.

---

## 8. Domande per voi

Su questi punti la vostra esperienza vale più di qualsiasi calcolo:

1. **Quando spostare a BE.** Meglio aspettare un guadagno minimo (oggi 3$ = 30 pips)? Oppure spostare lo stop in modo graduale (trailing), o lasciare lo SL originale finché non viene preso il TP1?
2. **Ingresso a mercato o nella zona.** Il trader dà una zona (es. 4286–4291), il bot entra subito al primo prezzo. Converrebbe entrare metà subito e metà con un ordine limite nella parte alta della zona, come fa il trader?
3. **"Close half": quale metà chiudere?** Oggi il bot chiude la posizione con il TP lontano e tiene quella col TP vicino. Voi cosa fareste?
4. **Secondo "Gold sell 4285" pochi minuti dopo il primo.** È un secondo ingresso (layer) da aprire, o solo un promemoria per chi non era entrato?
5. **Rischio giornaliero.** Il 24/09 sono arrivati 12 segnali: al 2% ciascuno il rischio complessivo può diventare alto. Mettereste un limite di perdita giornaliera o un numero massimo di operazioni aperte insieme?
6. **Errori del trader** (come "4362" corretto un minuto dopo in "4262"): il bot deve seguire la correzione ed entrare in ritardo, o lasciar perdere?

---

## 9. Prossimi passi

- Lasciare girare il bot sul server fino a venerdì con le nuove regole.
- Leggere i risultati della verifica sullo storico e scegliere il margine del BE.
- Decidere, con le vostre risposte, ingresso a zona, chiusura parziale e limite di rischio giornaliero.
- Gestire le correzioni del trader ai segnali sbagliati (caso "4362").
