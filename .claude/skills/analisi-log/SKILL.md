---
name: analisi-log
description: Analisi di una giornata di log del bot (bot.log, operazioni.txt, journal.jsonl sul branch server) - risultato, operazione per operazione, confronto con il trader, anomalie, bug e proposte. Da usare quando il proprietario dice di aver caricato i log o chiede di analizzare una giornata.
---

# Analisi di una giornata di log

Obiettivo: capire **cosa ha fatto il bot, se ha fatto quello che le regole prevedono, e dove abbiamo perso o guadagnato rispetto al trader**. Le perdite dove perde anche il trader sono normali; quelle dove lui vince sono il nostro problema da spiegare.

## 1. Prepararsi

1. Leggi `doc/DECISIONI.md`: *Regole in vigore*, la prima voce del *Registro*, la tabella *Risultati per giornata* e le *Questioni aperte* (soprattutto quelle "da verificare").
2. `git fetch origin server` e guarda cosa c'è: `git ls-tree --name-only origin/server logs/AAAA-MM-GG/`.
3. Numeri della giornata: `python3 .claude/skills/analisi-log/riepilogo.py AAAA-MM-GG`. La riga "differenza saldo − chiusure" deve essere circa 0. Se non lo è, c'è qualcosa da spiegare **prima di tutto il resto**: chiusure mai registrate (bug del 05/10), posizioni orfane (08/10), operazioni aperte il giorno prima, saldo letto prima dell'ultima chiusura.

## 2. Leggere

- `bot.log` per intero (`git show origin/server:logs/AAAA-MM-GG/bot.log`). È il racconto in ora italiana: 📩 messaggi del trader, 🧠 classificazione dell'agente, 📥 ordini, 🏁 chiusure, 🛡️ pareggi, 🛟 protezioni, ❌ errori e rifiuti, 🔌 MT5, ▶️ avvii, 💓 battito.
- `operazioni.txt`: una scheda per operazione, con i messaggi del trader collegati, l'esito di ogni ticket (e1 = TP1, e2 = TP2) e gli spostamenti di SL.
- `errors.log` e `dettagli.log` solo se serve capire un errore.
- `journal.jsonl` per i fatti esatti (prezzi, ticket, orari del broker UTC+3, richieste a MT5). In caso di dubbio vale il diario, non il racconto.

## 3. Controllare, operazione per operazione

Per ogni segnale:
- **Classificazione**: l'agente ha capito bene il messaggio? Direzione, prezzo, zona, SL, TP, re-entry, Trade Active, pareggio, chiusura parziale, layer. Confronta il testo 📩 con la riga 🧠.
- **Ingresso**: a mercato o limite al bordo come dicono le regole? Mai fuori zona dalla parte sbagliata. Se non è entrato, di quanto il prezzo era oltre il bordo.
- **Gestione**: Trade Active (quale ticket incassato, l'altro a pareggio), pareggio applicato, ridotto o rifiutato (con stop di protezione), chiusure parziali, layer.
- **Esito di ogni ticket**: TP, incasso, pareggio, protezione, stop pieno, chiusura del trader. Con quanto.
- **Confronto con il trader**: ha scritto HIT TP o "hit our risk"? Il nostro esito è coerente col suo? Se lui vince e noi perdiamo, trova il motivo preciso (ingresso, pareggio troppo stretto o rifiutato, classificazione, bug, MT5 scollegato).
- Il **riepilogo del trader** ("YESTERDAY SUMMARY", vinti/persi e pips per segnale) non si aspetta: di solito arriva verso le 19:08 dello stesso giorno, quello del venerdì il sabato mattina (16/10 → 17/10 alle 04:18, scartato per il weekend: è solo nel `journal.jsonl`), certi giorni manca (09/10, 15/10). Se c'è lo si usa; altrimenti il confronto si fa con i suoi Trade Active, HIT TP e "hit risk". I suoi pips sono gonfiati (contati dal bordo lontano della zona, a volte attribuiti al segnale sbagliato): servono per sapere vinto o perso, non quanto.

Poi, sulla giornata intera:
- **MT5 e bot**: disconnessioni (🔌) e cosa è successo nel frattempo, riavvii (▶️), errori (❌), conto DEMO all'avvio.
- **Reti di sicurezza**: posizioni orfane riprese o sconosciute, SL rimessi, livelli incoerenti, operazioni rimaste "in attesa" o "CLOSE_FAILED".
- **Messaggi persi**: segnali solo immagine, messaggi scartati che erano comandi, modifiche arrivate durante un riavvio.

## 4. Prima di dichiarare un bug

Leggi il codice coinvolto e **riproduci il caso con un test** in `tests/` (banco di prova: `tests/banco.py`, esempi in `tests/test_*.py`). Un comportamento è un bug solo se contraddice le regole di `doc/DECISIONI.md` o lascia il bot in uno stato sbagliato. Altrimenti è una questione di regole: si propone, non si corregge.

## 5. Rispondere (in italiano)

1. **Risultato**: saldo inizio → fine, somma delle chiusure, confronto con i giorni precedenti e con la giornata del trader.
2. **Operazioni**: tabella compatta (ora, segnale, ingresso, esito per ticket, $) e, sotto, solo i casi che meritano una spiegazione.
3. **Cosa ha funzionato / cosa no**, con le righe del log o gli eventi del diario come prova (orari e numeri esatti).
4. **Bug** (se ce ne sono): causa, effetto in $, correzione proposta.
5. **Proposte sulle regole** (se ci sono): cosa cambia, cosa si toglie, cosa resta, controindicazioni, dati a sostegno; se servono più dati, quale variante aggiungere al rigioco.
6. **Domande** al proprietario, solo se servono per decidere.

## 6. Dopo l'analisi

- Bug: correzione su `main` + test che lo riproduce + `python3 tests/run_all.py` + mostra le modifiche; push solo dopo l'ok (vedi CLAUDE.md).
- `doc/DECISIONI.md`: aggiungi la riga del giorno in *Risultati per giornata* (giorno, risultato, versione/note) e aggiorna le *Questioni aperte* toccate dalla giornata; si pubblica insieme al resto, dopo l'ok.
- Se dopo il push serve aggiornare il server: skill `prompt-server`.
