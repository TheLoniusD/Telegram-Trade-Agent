"""
Prova LIVE degli scenari sul terminale MT5 del server (via RPyC), SENZA usare
l'agente classificatore: ogni messaggio Telegram fittizio ha la sua
classificazione scritta a mano in tests/scenari.py.

Esegue il flusso reale di telegram_listener.process_message, quindi apre,
modifica e chiude posizioni VERE sul conto collegato. Per sicurezza:
  - si rifiuta di partire su un conto reale (serve --consenti-conto-reale);
  - usa una memoria separata (test_run/), senza toccare active_trades.json;
  - alla fine chiude le posizioni aperte dal test rimaste a mercato.

Uso (dalla cartella del progetto, con mt5server.exe attivo e il bot FERMO):
    python -m tests.live_mt5 --elenco
    python -m tests.live_mt5 --scenario ciclo_completo_sell
    python -m tests.live_mt5 --scenario reentry_sell --auto 5
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import FakeEvent, ScriptedClassifier, install_stubs  # noqa: E402
from tests.scenari import SCENARI  # noqa: E402

CARTELLA_TEST = "test_run"
SIMBOLO = "XAUUSD"


def tipo(mt5, t):
    return "BUY" if t == mt5.ORDER_TYPE_BUY else "SELL"


def stampa_stato(mt5, manager, tickets_visti):
    """Confronta, ticket per ticket, cosa dice la memoria e cosa c'è davvero su MT5."""
    trades = list(manager.active_trades.values())
    if not trades and not tickets_visti:
        print("   (nessuna operazione in memoria)")
        return

    for trade in trades:
        print(f"   MEMORIA msg {trade['msg_id']} | status {trade['status']} | root {trade.get('root_msg_id')}")
        for key, t in trade["tickets"].items():
            ticket = t.get("mt5_ticket")
            riga = (f"     {key}: ticket {ticket} | {t.get('direction')} | SL {t.get('stop_loss')} | "
                    f"TP {t.get('take_profit')} | closed {t.get('closed')} | BE {t.get('be_active')}")
            print(riga)
            if not ticket:
                continue
            pos = mt5.positions_get(ticket=ticket)
            if not pos:
                print(f"       MT5: posizione non aperta")
                continue
            p = pos[0]
            print(f"       MT5: {tipo(mt5, p.type)} {p.volume} @ {p.price_open} | SL {p.sl} | TP {p.tp}")
            differenze = []
            if t.get("direction") and t["direction"] != tipo(mt5, p.type):
                differenze.append(f"direzione {t['direction']} vs {tipo(mt5, p.type)}")
            if (t.get("stop_loss") or 0.0) != p.sl:
                differenze.append(f"SL {t.get('stop_loss')} vs {p.sl}")
            if (t.get("take_profit") or 0.0) != p.tp:
                differenze.append(f"TP {t.get('take_profit')} vs {p.tp}")
            if t.get("closed"):
                differenze.append("segnato 'closed' ma ancora APERTO su MT5")
            if differenze:
                print(f"       ⚠️  DISALLINEAMENTO: {'; '.join(differenze)}")

    # Ticket usciti dalla memoria (archiviati) ma ancora a mercato
    in_memoria = {t.get("mt5_ticket") for tr in trades for t in tr["tickets"].values()}
    for ticket in sorted(tickets_visti - in_memoria):
        pos = mt5.positions_get(ticket=ticket)
        if pos:
            p = pos[0]
            print(f"   ⚠️  ORFANA: ticket {ticket} archiviato come chiuso ma APERTO su MT5 "
                  f"({tipo(mt5, p.type)} {p.volume} | SL {p.sl} | TP {p.tp})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=sorted(SCENARI))
    parser.add_argument("--elenco", action="store_true", help="mostra gli scenari e i messaggi, senza eseguire nulla")
    parser.add_argument("--auto", type=float, metavar="SECONDI", help="non chiede Invio tra un passo e l'altro")
    parser.add_argument("--rischio", type=float, default=0.5, help="%% di rischio per il RiskManager (default 0.5)")
    parser.add_argument("--ignora-orari", action="store_true", help="salta il calendario statico di market_hours.py")
    parser.add_argument("--lascia-aperte", action="store_true", help="a fine test NON chiude le posizioni rimaste")
    parser.add_argument("--consenti-conto-reale", action="store_true")
    args = parser.parse_args()

    if args.elenco:
        for nome, fn in SCENARI.items():
            print(f"\n=== {nome}")
            for i, s in enumerate(fn(4000.0), 1):
                print(f"  {i}. [{s['output']['intent']}] {s['text']!r}\n     -> {s['nota']}")
        return
    if not args.scenario:
        parser.error("indica --scenario (oppure --elenco)")

    install_stubs()
    try:
        import telegram_listener
    except ModuleNotFoundError as e:
        sys.exit(f"❌ Modulo mancante ({e.name}): esegui 'pip install -r requirements.txt'.")
    except (ConnectionRefusedError, OSError) as e:
        host = os.getenv("MT5_RPYC_HOST", "localhost")
        porta = os.getenv("MT5_RPYC_PORT", "18812")
        sys.exit(f"❌ Nessun mt5server.exe raggiungibile su {host}:{porta} ({e}).\n"
                 "   Lo script va lanciato dove gira mt5server.exe (il server), oppure attraverso un tunnel SSH\n"
                 f"   (ssh -L {porta}:localhost:{porta} utente@server).")
    from mt5_connection import mt5
    from order_manager import OrderManager

    # --- Controlli di sicurezza e diagnostica ---------------------------------
    account = mt5.account_info()
    if account is None:
        sys.exit(f"❌ MT5 non risponde: {mt5.last_error()}")
    demo = account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    print(f"Conto {account.login} su {account.server} | saldo {account.balance} {account.currency} | "
          f"{'DEMO' if demo else '⚠️  REALE'}")
    if not demo and not args.consenti_conto_reale:
        sys.exit("❌ Il conto non è DEMO: test annullato (usa --consenti-conto-reale solo se sei sicuro).")
    if telegram_listener.mt5_agent.test_mode:
        print("⚠️  TEST_MODE=True in mt5_executor.py: nessun ordine arriverà a MT5, vedrai solo simulazioni.")

    tick = mt5.symbol_info_tick(SIMBOLO)
    if tick is None:
        sys.exit(f"❌ Nessun tick per {SIMBOLO}: il simbolo è nel Market Watch?")
    eta = time.time() - tick.time
    print(f"{SIMBOLO} bid {tick.bid} ask {tick.ask} | età ultimo tick secondo is_symbol_tradable: {eta:+.0f}s")
    if eta < -60:
        print("⚠️  Età NEGATIVA: tick.time è nell'orario del server del broker, non in UTC. Il controllo "
              "'nessun tick da Xs' di is_symbol_tradable non scatterà mai a mercato chiuso.")
    print(f"is_symbol_tradable: {telegram_listener.mt5_agent.is_symbol_tradable(SIMBOLO)}")

    # --- Memoria separata e classificatore scritto a mano ---------------------
    os.makedirs(CARTELLA_TEST, exist_ok=True)
    stato = os.path.join(CARTELLA_TEST, "active_trades_test.json")
    if os.path.exists(stato):
        os.remove(stato)
    manager = OrderManager(storage_path=stato, archive_dir=os.path.join(CARTELLA_TEST, "storico"))
    classifier = ScriptedClassifier()
    telegram_listener.manager = manager
    telegram_listener.agent_classify_telegram_message = classifier
    telegram_listener.risk_agent.risk_percent = args.rischio
    if args.ignora_orari:
        telegram_listener.is_market_time_open = lambda: (True, "orari ignorati da --ignora-orari")

    p = round(tick.bid, 2)
    passi = SCENARI[args.scenario](p)
    tickets_visti = set()
    print(f"\nScenario '{args.scenario}' con prezzo di riferimento {p} | memoria in {stato}\n")

    try:
        for i, passo in enumerate(passi, 1):
            print("=" * 80)
            print(f"PASSO {i}/{len(passi)} | msg {passo['msg_id']} | {'EDIT' if passo['is_edit'] else 'NUOVO'} "
                  f"| reply_to {passo['reply_to']} | intent {passo['output']['intent']}")
            print(f"Testo: {passo['text']!r}")
            print(f"Atteso: {passo['nota']}")
            if args.auto is None:
                input("Invio per eseguire (Ctrl+C per interrompere)... ")
            else:
                time.sleep(args.auto)

            classifier.next_output = passo["output"]
            evento = FakeEvent(passo["msg_id"], passo["text"], telegram_listener.OWNER_ID, reply_to=passo["reply_to"])
            asyncio.run(telegram_listener.process_message(evento, is_edit=passo["is_edit"]))
            if classifier.next_output is not None:
                print("ℹ️  Messaggio scartato PRIMA della classificazione (mercato chiuso?)")
                classifier.next_output = None

            for trade in manager.active_trades.values():
                tickets_visti |= {t["mt5_ticket"] for t in trade["tickets"].values() if t.get("mt5_ticket")}
            print("\nSTATO DOPO IL PASSO:")
            stampa_stato(mt5, manager, tickets_visti)
    except KeyboardInterrupt:
        print("\nInterrotto.")
    finally:
        rimaste = [t for t in sorted(tickets_visti) if mt5.positions_get(ticket=t)]
        if rimaste and not args.lascia_aperte:
            print(f"\n🧹 Chiudo le {len(rimaste)} posizioni del test ancora aperte: {rimaste}")
            for t in rimaste:
                esito = telegram_listener.mt5_agent.close_position(t)
                print(f"   ticket {t}: {'chiusa' if esito else '❌ NON chiusa, verifica a mano'}")
        elif rimaste:
            print(f"\nPosizioni del test lasciate aperte: {rimaste}")


if __name__ == "__main__":
    main()
