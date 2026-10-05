# Telegram Trade Agent

Bot che copia sul conto **demo** MetaTrader 5 i segnali sull'oro (XAUUSD) del canale Telegram **Maestro Fx**.

Come funziona, in breve:
1. **telegram_listener.py** ascolta il canale e scarta i messaggi inutili (chat, inoltri, modifiche identiche…).
2. **agent_classifier.py** fa leggere ogni messaggio a Claude (Haiku), che lo classifica: nuovo segnale, aggiornamento (SL/TP, pareggio, Trade Active, chiusura parziale), chiusura, da ignorare.
3. **order_manager.py** tiene in memoria le operazioni (`active_trades.json`) e decide cosa fare.
4. **risk_manager.py** calcola livelli di ingresso e lotti (rischio 2% per segnale).
5. **mt5_executor.py** invia gli ordini a MetaTrader 5 e controlla ogni 30 secondi lo stato delle posizioni.

Le regole di trading in vigore e il perché di ogni scelta sono in **[doc/DECISIONI.md](doc/DECISIONI.md)**.

## File

| File | A cosa serve |
|---|---|
| `telegram_listener.py` | avvio del bot, filtri dei messaggi, esecuzione delle decisioni, controllo periodico |
| `agent_classifier.py` | prompt e chiamata all'agente classificatore |
| `order_manager.py` | memoria delle operazioni, re-entry, aggiornamenti e chiusure |
| `risk_manager.py` | livelli di ingresso nella zona del trader, SL provvisorio, lottaggio |
| `mt5_executor.py` | ordini, pareggio, chiusure e stato delle posizioni su MT5 |
| `market_hours.py` | orari di apertura del mercato (niente chiamate all'agente a mercato chiuso) |
| `journal.py` | diario strutturato degli eventi (`journal.jsonl`) |
| `narrative.py` | racconto leggibile (`bot.log`) e schede delle operazioni (`operazioni.txt`) |
| `logger_config.py` | file di log per giorno, in ora italiana |
| `verifica_be.py` | rigioca le posizioni di un giorno sulle candele al minuto di MT5 (analisi) |
| `prezzi.py` | cosa ha fatto il prezzo dopo ogni segnale di un giorno, sulle candele al minuto di MT5 (analisi) |
| `doc/` | `DECISIONI.md` (regole e scelte), archivio di messaggi reali del canale |

## Branch

- **`main`**: versione per **Windows**, con il pacchetto `MetaTrader5` collegato direttamente al terminale. Si sviluppa qui.
- **`server`**: `main` più il collegamento **RPyC** a `mt5server.exe` sotto Wine (`mt5_connection.py`) e i **log** caricati dal server. Gli aggiornamenti si portano unendo `main` in `server`.

## Avvio (Windows)

```
pip install -r requirements.txt
python telegram_listener.py
```

Serve un file `.env` (mai nel repository) con almeno:

```
ANTHROPIC_API_KEY=...
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TARGET_CHANNEL=https://t.me/Maestrofxofficial
```

Tutti gli altri parametri hanno un valore predefinito; l'elenco è in `doc/DECISIONI.md`. Il bot non parte se manca uno dei quattro sopra. Il terminale MT5 deve essere aperto, collegato al conto demo e con l'Algo Trading attivo.

**Attenzione:** il PC e il server non devono mai girare insieme con la stessa sessione Telegram.

## Log

Ogni giorno in `logs/AAAA-MM-GG/`:
- `bot.log`: il racconto della giornata;
- `operazioni.txt`: una scheda per segnale, con esiti e risultato;
- `errors.log`, `dettagli.log`: errori e dettagli tecnici;
- `journal.jsonl`: i dati per le analisi.
