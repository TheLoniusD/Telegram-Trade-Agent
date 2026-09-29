# Come gestire i messaggi "Trade Active"

*Documento di discussione — opzioni da valutare, nessuna è ancora stata implementata oltre a quella attuale (A).*

---

## 1. Di cosa parliamo

Il trader del canale Maestro Fx, dopo un segnale, pubblica spesso messaggi come:

> **Trade Active ✅**
> Gold Sell Running 110+ Pips
> Scalpers secure ur Profits & BE+ ur entries

Significa: *l'operazione sta andando bene; chi fa scalping incassi, tutti gli altri mettano lo stop a pareggio (BE) e lascino correre.*

Il bot copia i segnali sul conto demo con **4 ordini per segnale**, distribuiti nella zona indicata dal trader (es. per "Gold sell 4180" la zona è 4180–4185: ordini a 4180 / 4181,25 / 4182,50 / 4183,75). Due ordini puntano al TP1 del trader, due al TP2.

Promemoria: sull'oro **10 pips = 1 $ di prezzo**. "Running 110+ pips" = il prezzo si è mosso di circa 11 $ a favore, **contati dagli ingressi del trader**, che di solito sono migliori dei nostri.

---

## 2. Cosa abbiamo osservato (23–29 settembre)

**Il "Trade Active" non vuol dire "non si andrà oltre".** Su 22 segnali con almeno un Trade Active (23–25/09), in 6 casi (circa 1 su 4) il trader ha poi scritto HIT TP o HIT TP MAX. Negli altri non ha scritto più nulla (probabilmente si sono chiusi a pareggio). **In nessun caso, dopo un Trade Active, è arrivato uno stop pieno.**

**Due tipi di giornata, due risultati opposti:**

| Giornata | Mercato | Cosa è successo dopo i Trade Active |
|---|---|---|
| **28/09** | trend forte (oro da 4202 a 4144) | Molte operazioni sono arrivate al TP. Ma in 2 casi (SELL 4180, SELL 4176) lo stop a pareggio è stato preso 1–2 minuti dopo il Trade Active, e poi il prezzo è arrivato al TP: circa **150 $ persi di guadagno**. |
| **29/09** | laterale (il trader stesso alle 15:06: *"Market Sideways, easy money to scalping"*) | Tutte le operazioni sono andate in buon profitto e poi **tornate indietro fino ai nostri ingressi**, spesso in pochi minuti. Esempio: alle 05:03 il BUY 4131 era a **+185 $** di flottante; si è chiuso a pareggio. Nel pomeriggio, con lo stop a ingresso +0,50 $: **+6/+10 $ per operazione**. |

**I Trade Active a più righe.** A volte il messaggio contiene più righe (*"Running 100+ / 80+ / 60+ Pips"*): è il tabellone di più segnali della stessa serie, non di uno solo. Qualunque regola deve tenerne conto.

**Il punto:** nelle giornate di trend conviene lasciare spazio per arrivare al TP; nelle giornate laterali conviene incassare qualcosa, perché il prezzo torna indietro. Il Trade Active da solo non ci dice in che tipo di giornata siamo.

---

## 3. Opzioni per PROTEGGERE le operazioni

### A. Pareggio a ingresso + 0,50 $ *(attuale)*

**Come funziona.** Al Trade Active, per ogni ordine eseguito lo stop va al suo prezzo di ingresso più 0,50 $ dalla parte del guadagno (SELL entrato a 4155 → stop a 4154,50). Gli ordini non ancora eseguiti vengono cancellati.

**Esempio reale (29/09, SELL 4155 delle 13:04).** Tre ordini eseguiti, stop a +0,50 ciascuno; il prezzo torna indietro, chiusura di tutti a pareggio: **+9,68 $**.

| Pro | Contro |
|---|---|
| Semplice, mai una perdita dopo il Trade Active | Guadagno minimo nei giorni laterali (+6/+10 $) |
| MT5 lo accetta quasi sempre (siamo già in profitto) | Nei giorni di trend lo stop è così vicino che una normale oscillazione di 1–2 $ ci chiude, mentre il trader resta dentro fino al TP |

---

### C. Stop a gradini

**Come funziona.** Al Trade Active lo stop non va all'ingresso ma **a metà strada tra il nostro ingresso e il prezzo del momento**. A ogni nuovo Trade Active dello stesso segnale (+60, poi +80, poi +110 pips...) lo stop sale di un altro gradino.

**Esempio (SELL entrato a 4155).**

| Momento | Prezzo | Stop | Guadagno garantito |
|---|---|---|---|
| 1° Trade Active | 4147 (8 $ a favore) | 4151 | +4 $ di prezzo |
| 2° Trade Active | 4143 (12 $ a favore) | 4149 | +6 $ di prezzo |

| Pro | Contro |
|---|---|
| Blocca una parte vera del profitto, non solo 0,50 $ | Se il prezzo è poco a favore, lo stop resta vicino (come oggi) |
| Segue i messaggi del trader: più lui conferma, più noi proteggiamo | Nei giorni di trend uno stop più stretto può chiudere prima del TP |
| Nessuna chiusura forzata: si chiude solo a TP o a stop | Dipende dal fatto che il trader scriva più Trade Active |

---

### D. Trailing stop automatico

**Come funziona.** Dopo il primo Trade Active il bot **segue il prezzo da solo** (controllo ogni 30 secondi) e tiene lo stop a una distanza fissa (es. 3 $) dal prezzo migliore raggiunto. Se il prezzo torna indietro, lo stop non si muove e l'operazione si chiude.

**Esempio (SELL, distanza 3 $).**

| Prezzo migliore raggiunto | Stop |
|---|---|
| 4147 | 4150 |
| 4143 | 4146 |
| 4140 | 4143 |
| il prezzo risale a 4143 | chiusura a 4143 |

| Pro | Contro |
|---|---|
| Non dipende dai messaggi del trader: protegge anche se lui non scrive più | La distanza giusta va scelta con cura: troppo stretta chiude nelle oscillazioni, troppo larga restituisce molto profitto |
| Nei giorni di trend lascia correre; in quelli laterali incassa gran parte del movimento | Lo stop si muove spesso: più ordini inviati a MT5 |
| Automatico e prevedibile | Si scosta dalla gestione del trader (lui non usa trailing) |

---

## 4. Opzioni per INCASSARE al Trade Active

### E. Chiudere i ticket destinati al TP1

**Come funziona.** Al Trade Active si chiudono subito, se sono in profitto, i due ordini che puntano al TP1 (e1, e3). I due che puntano al TP2 (e2, e4) restano aperti, protetti con una delle opzioni A, C o D.

| Ordine | TP | Al Trade Active |
|---|---|---|
| e1 | TP1 | chiuso, incassa adesso |
| e2 | TP2 | resta aperto, protetto |
| e3 | TP1 | chiuso, incassa adesso |
| e4 | TP2 | resta aperto, protetto |

| Pro | Contro |
|---|---|
| Fa esattamente quello che dice il trader: "secure profits" su una parte, BE sul resto | Nei giorni di trend metà della posizione rinuncia al TP1 (che spesso arriva) |
| Nei giorni laterali incassa metà del profitto invece di +0,50 $ | Se il Trade Active arriva quando il nostro profitto reale è piccolo, incassiamo poco |
| La metà rimasta può ancora arrivare al TP2 | |

---

### F. Chiudere solo sopra una soglia

**Come funziona.** Si incassa una parte (es. i ticket TP1, come in E) **solo se** il nostro guadagno reale supera una soglia (es. 5 $ di prezzo), oppure se il trader scrive molti pips (es. 100+). Sotto la soglia: solo protezione.

| Situazione | Azione |
|---|---|
| Guadagno reale 8 $ (≥ 5 $) | chiudi i ticket TP1, proteggi gli altri |
| Guadagno reale 2 $ (< 5 $) | solo protezione |

| Pro | Contro |
|---|---|
| Incassa solo quando c'è davvero qualcosa da incassare | Due parametri da scegliere (soglia in $ o in pips) |
| Evita chiusure a guadagno minimo | I pips del trader sono contati dai suoi ingressi: una soglia in pips può non corrispondere al nostro guadagno |

---

### H. Non incassare mai

**Come funziona.** Il Trade Active serve solo a proteggere (A, C o D). Si chiude solo ai TP, allo stop, o ai comandi espliciti del trader ("close half", "close all").

| Pro | Contro |
|---|---|
| Massima fedeltà ai segnali: prendiamo tutti i TP che il trader prende | Nei giorni laterali i profitti tornano indietro e restano solo quelli bloccati dallo stop |
| Nessuna decisione "nostra" oltre al segnale | Dipende tutto da quanto la protezione scelta è buona |

---

## 5. Come si possono combinare

Una scelta di **protezione** (A, C o D) va sempre fatta; quella di **incasso** (E, F o H) si aggiunge. Alcune combinazioni sensate:

| Combinazione | In breve | Giorno di trend (28/09) | Giorno laterale (29/09) |
|---|---|---|---|
| **A + H** *(oggi)* | pareggio stretto, niente incasso | TP presi, ma alcuni persi per stop troppo vicino | +6/+10 $ per operazione |
| **C + H** | stop a gradini, niente incasso | lascia più spazio del pareggio stretto | blocca una parte del profitto |
| **D + H** | trailing, niente incasso | segue il trend | incassa gran parte del movimento, se la distanza è giusta |
| **A + E** | metà incassata, metà a pareggio | metà rinuncia al TP1 | metà del profitto incassato |
| **C + F** | stop a gradini, incasso solo sopra soglia | poco incasso anticipato | incasso quando il profitto è buono |

---

## 6. Domande per la discussione

1. Nella vostra esperienza con questo trader, quanto spesso un "Trade Active" porta davvero al TP, e quanto spesso il prezzo torna indietro?
2. È meglio **proteggere di più** (stop a gradini, trailing) o **incassare subito una parte** (chiusura dei ticket TP1)?
3. Per il trailing (D): che distanza usereste sull'oro? 2 $, 3 $, 5 $?
4. Per la soglia (F): ha più senso ragionare sul **nostro** guadagno in dollari o sui **pips** scritti dal trader?
5. Quando il trader scrive "Market Sideways / easy money to scalping", cambiereste gestione per il resto della giornata?

---

## 7. Prossimo passo

Prima di implementare un'opzione, la **verifichiamo sui prezzi reali**: scarichiamo da MT5 le candele al minuto dei giorni già registrati (28 e 29/09 e successivi) e rigiochiamo le operazioni vere con ciascuna combinazione, per sapere quanti dollari avrebbe reso ognuna. Così la scelta si basa sui numeri, non sulle impressioni di una singola giornata.
