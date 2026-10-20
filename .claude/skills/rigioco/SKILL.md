---
name: rigioco
description: Rigioco delle strategie con prezzi.py (rigiocare i messaggi del trader con regole diverse sui prezzi veri di MT5) - come farlo lanciare sul server, come leggerlo e confrontare le varianti senza farsi ingannare da un singolo caso. Da usare quando si valuta o si cambia una regola di trading, o quando il proprietario carica un rigioco.
---

# Rigioco delle strategie

`prezzi.py rigioco DAL AL` rigioca i messaggi del trader registrati nei diari (eventi CLASSIFIED) con un rischio fisso di 140 $ per segnale. Usa le candele M1 di MT5 e i **tick reali** nei 180 secondi dopo ogni segnale e comando del trader. Applica le regole in vigore ("attuale" = `BASE_RULES`) e le varianti di `STRATEGIES` in `prezzi.py`. L'orologio del broker si ricava dal diario di ogni giorno (UTC+3).

## Lanciarlo (solo sul server: servono i prezzi di MT5)

Si chiede alla sessione sul server (skill `prompt-server`) di eseguire, dalla cartella del progetto:

```
python prezzi.py rigioco 2026-09-24 AAAA-MM-GG > logs/rigioco_tick.txt
python prezzi.py rigioco 2026-09-24 AAAA-MM-GG --dettaglio > logs/rigioco_tick_dettaglio.txt
```

Poi i log si caricano sul branch `server`. Opzioni utili:
- `--strategie "attuale,BE +1,5 $"` limita le varianti;
- `--offset-broker 3` forza l'orologio del broker, solo se il diario non basta;
- `--senza-tick` usa solo le candele M1, più veloce ma indulgente con gli stop di protezione.

## Leggerlo

1. **Validità**: intestazione con "orologio del broker UTC+3" e "Tick reali disponibili in N finestre su M" (N vicino a M). Giorni saltati o senza candele vanno segnalati.
2. **Calibrazione**: le operazioni recenti fatte con le stesse regole di "attuale" devono coincidere con il reale a pochi dollari (confronta con `operazioni.txt` di quei giorni). Se non coincidono, il simulatore ha un problema: va corretto prima di trarre conclusioni.
3. **Confronto segnale per segnale**, mai solo i totali:
   ```
   git show origin/server:logs/rigioco_tick_dettaglio.txt > /tmp/det.txt
   python3 .claude/skills/rigioco/confronta.py /tmp/det.txt "attuale" "SIGLA"
   ```
   Le sigle sono quelle della riga sotto ogni giorno del dettaglio. Lo script dice in quanti segnali e giorni la variante è meglio o peggio, di quanto, e quali sono i casi più grandi.
4. **Trappole già incontrate**:
   - un totale spostato da **una sola operazione**: "senza TP provvisori" +228 $, tutti dal 07/10 SELL 4133. Non è una prova;
   - **giorni di trend**: il 28/09 da solo vale +1.420 $ e decide i totali. Una variante può essere meglio in 11 giorni su 12 e perdere tutto lì (pareggio +1,5 $);
   - **più esposizione** non vuol dire regola migliore: 2 ticket contro 4 guadagnano di più nei giorni buoni e perdono di più in quelli cattivi;
   - **stime contro misure**: un effetto stimato a mano va confermato con una variante nel rigioco prima di darlo per buono.

## Quando si cambia una regola del bot

- Si porta la stessa regola in `prezzi.py`, così "attuale" resta uguale al bot (`BASE_RULES` e, se serve, `simulate_signal`).
- La regola precedente resta come variante, con la data ("BE +0,5 $ (fino all'11/10)"), per poterla confrontare.
- Si aggiornano la riga "Regole attuali" stampata dal rigioco e `doc/DECISIONI.md` con i numeri.
- La verifica (`python3 tests/run_all.py`) prova `prezzi.py` solo su prezzi finti (`tests/test_rigioco.py`: lettura dei comandi, simulazione di un segnale): il rigioco vero va rilanciato sul server. Ogni difetto del simulatore trovato con la calibrazione diventa uno scenario lì.
- Un messaggio può contenere più comandi ("close half and BE your entry"): il simulatore deve eseguirli tutti, nell'ordine del bot (fino al 20/10 perdeva il pareggio: 13/10 SELL 4187 +210 $ nel rigioco, −31 $ reale).
