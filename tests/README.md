# Test di MT5Executor con messaggi Telegram fittizi

Nessun test chiama l'agente classificatore: la classificazione di ogni messaggio
è scritta a mano in `scenari.py`, quindi **zero token consumati**.

## 1. Test offline (finto MT5, gira ovunque)

```bash
pip install pytest
python -m pytest tests -rxX
```

- `test_mt5_executor.py`: ogni metodo di `MT5Executor` chiamato da solo.
- `test_pipeline_messaggi.py`: messaggio fittizio → `process_message` → OrderManager
  → RiskManager → MT5Executor → finto broker (`fake_mt5.py`).
- I test `xfail` documentano bug reali ancora aperti: quando un bug viene corretto
  pytest segnala `XPASS` e il marcatore va tolto.

## 2. Prova live sul server (MT5 reale via RPyC, conto DEMO)

Con `mt5server.exe` attivo e il bot **fermo**:

```bash
python -m tests.live_mt5 --elenco                         # scenari e messaggi
python -m tests.live_mt5 --scenario ciclo_completo_sell   # Invio tra un passo e l'altro
python -m tests.live_mt5 --scenario reentry_sell --auto 5
```

- Si rifiuta di partire su un conto reale.
- Usa una memoria separata in `test_run/` (non tocca `active_trades.json`).
- Dopo ogni passo confronta memoria e posizioni reali su MT5 e segnala i disallineamenti.
- A fine test chiude le posizioni rimaste aperte (salvo `--lascia-aperte`).
- `--rischio` (default 0.5%) e `--ignora-orari` per il calendario statico.
