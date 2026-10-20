"""
Riepilogo numerico di una giornata dal diario (journal.jsonl) del repository dei log.

    python3 .claude/skills/analisi-log/riepilogo.py 2026-10-09 [--log CARTELLA | --ref REF]

Legge `logs/AAAA-MM-GG/journal.jsonl` dalla copia del repository dei log
(TheLoniusD/Telegram-Trade-Agent-log): di default `Telegram-Trade-Agent-log/` dentro il
progetto (PC) o accanto al progetto (sessione nel cloud); `--log` per un'altra cartella.
Prima aggiornala con `git -C Telegram-Trade-Agent-log pull`. Con `--ref` legge invece con
`git show` da un ref del progetto (es. `origin/server`, dove sono i log fino al 20/10).
Stampa: saldo e chiusure registrate (con la differenza da spiegare), segnali e
decisioni, ingressi, pareggi e protezioni, MT5, riavvii, reti di sicurezza, errori.
È un punto di partenza: il dettaglio di ogni operazione è in operazioni.txt e bot.log.
"""
import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LOG_REPO = "Telegram-Trade-Agent-log"


def hm(event):
    return event["ts"][11:16]


def read_journal(giorno, log_dir, ref):
    if ref:
        raw = subprocess.run(["git", "show", f"{ref}:logs/{giorno}/journal.jsonl"],
                             capture_output=True, text=True, encoding="utf-8")
        if raw.returncode != 0:
            raise SystemExit(f"Diario non trovato: {raw.stderr.strip()} (hai fatto git fetch?)")
        return raw.stdout
    candidates = [Path(log_dir)] if log_dir else [ROOT / LOG_REPO, ROOT.parent / LOG_REPO]
    for folder in candidates:
        if (folder / "logs").is_dir():
            path = folder / "logs" / giorno / "journal.jsonl"
            if not path.is_file():
                raise SystemExit(f"Diario non trovato: {path} (hai aggiornato i log con git -C {folder} pull?)")
            return path.read_text(encoding="utf-8")
    raise SystemExit(f"Repository dei log non trovato in {', '.join(map(str, candidates))}: clonalo in "
                     f"{ROOT / LOG_REPO} (CLAUDE.md, \"Branch e log\") o indica --log")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("giorno", help="AAAA-MM-GG")
    parser.add_argument("--log", help="cartella del repository dei log (default: Telegram-Trade-Agent-log dentro o accanto al progetto)")
    parser.add_argument("--ref", help="leggi con git show da questo ref del progetto invece che dalla cartella dei log")
    args = parser.parse_args()

    raw = read_journal(args.giorno, args.log, args.ref)
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    by = lambda name: [e for e in events if e.get("event") == name]

    print(f"== {args.giorno} · {len(events)} eventi")

    balances = [(hm(e), e["balance"]) for e in events if e.get("event") in ("HEARTBEAT", "BOT_START") and e.get("balance") is not None]
    closes = by("POSITION_CLOSED")
    partial = by("POSITION_PARTIAL_CLOSED")
    closed_profit = sum(e.get("profit") or 0 for e in closes) + sum(e.get("profit") or 0 for e in partial)
    if balances:
        first, last = balances[0], balances[-1]
        print(f"Saldo: {first[1]:.2f} ({first[0]}) → {last[1]:.2f} ({last[0]}): {last[1] - first[1]:+.2f}")
    print(f"Chiusure registrate: {len(closes)} (+{len(partial)} parziali), totale {closed_profit:+.2f}")
    if balances:
        gap = (balances[-1][1] - balances[0][1]) - closed_profit
        print(f"  differenza saldo − chiusure: {gap:+.2f}"
              + ("  ← DA SPIEGARE (chiusure non registrate? posizioni di ieri? saldo letto prima della fine?)" if abs(gap) > 1 else ""))
    print("  per motivo:", dict(Counter(f"{e.get('closed_by')}/{e.get('close_reason')}" for e in closes)))
    print("  in perdita:", sorted(round(e.get("profit") or 0, 2) for e in closes if (e.get("profit") or 0) < 0))

    classified = Counter(e.get("intent") for e in by("CLASSIFIED"))
    print(f"\nMessaggi: {len(by('MESSAGE'))} letti, {len(by('MESSAGE_SKIPPED'))} scartati prima dell'agente, "
          f"classificati {dict(classified)}")
    decisions = Counter((e.get("action"), e.get("reason")) for e in by("DECISION"))
    for (action, reason), n in decisions.most_common():
        print(f"  decisione {action}: {n}" + (f" — {reason}" if reason else ""))
    for e in by("RISK_REJECTED"):
        print(f"  {hm(e)} RIFIUTATO dal risk manager: {e.get('reason')}")
    tokens = sum((e.get("input_tokens") or 0) + (e.get("output_tokens") or 0) for e in by("CLASSIFIER_CALL"))
    print(f"  chiamate all'agente: {len(by('CLASSIFIER_CALL'))} ({tokens} token tra input e output, senza cache)")

    print(f"\nIngressi: {len(by('RISK_CALC'))} calcoli di rischio, {len(by('ENTRY_PENDING'))} ordini limite, "
          f"{len(by('PENDING_FILLED'))} eseguiti, {len(by('PENDING_MOVED'))} spostati")
    for reason, n in Counter(e.get("reason") for e in by("PENDING_CANCELLED")).most_common():
        print(f"  cancellati: {n} — {reason}")

    protective = by("PROTECTIVE_STOP")
    print(f"\nPareggio: {len(by('BE_PENDING'))} richieste in attesa, {len(by('BE_PENDING_APPLIED'))} applicati dopo, "
          f"{sum(1 for e in protective if e.get('reduced_be'))} pareggi ridotti, "
          f"{sum(1 for e in protective if not e.get('reduced_be'))} stop di protezione")
    for e in by("TRADE_ACTIVE_PLAN"):
        print(f"  {hm(e)} Trade Active {e.get('trader_pips')} pips: incassati {e.get('closed')}, in perdita {e.get('in_loss')}"
              + (f" · {e.get('note')}" if e.get("note") else ""))
    for e in by("LAYER_CLOSE"):
        print(f"  {hm(e)} layer {e.get('key')}: chiuso={e.get('closed')} {e.get('reason') or ''}")

    print("\nMT5:")
    for e in by("MT5_CONNECTION"):
        print(f"  {hm(e)} {'collegato' if e.get('ok') else 'NON disponibile'}: {e.get('reason')}")
    failed = Counter((e.get("operation"), e.get("error")) for e in by("MT5_ORDER") if not e.get("ok"))
    for (operation, error), n in failed.most_common():
        print(f"  rifiutati {operation}: {n} — {error}")
    print(f"  avvii del bot: {[hm(e) for e in by('BOT_START')]} · arresti: {[hm(e) for e in by('BOT_STOP')]}")
    not_demo = [hm(e) for e in by("BOT_START") if not e.get("demo")]
    if not_demo:
        print(f"  ATTENZIONE: avvii senza conto DEMO leggibile alle {not_demo}")

    safety = {name: by(name) for name in ("ORPHAN_ADOPTED", "ORPHAN_UNKNOWN", "STOP_RESTORED", "LEVELS_INCOHERENT",
                                          "MEMORY_RESYNC", "PARTIAL_CLOSE_REPEATED", "ERROR")}
    print("\nReti di sicurezza ed errori:", {k: len(v) for k, v in safety.items()})
    for name in ("ORPHAN_ADOPTED", "ORPHAN_UNKNOWN", "STOP_RESTORED", "LEVELS_INCOHERENT"):
        for e in safety[name]:
            print(f"  {hm(e)} {name} #{e.get('mt5_ticket')}")
    for e in safety["ERROR"]:
        print(f"  {hm(e)} ERROR in {e.get('where')}: {str(e.get('error')).strip().splitlines()[-1][:160]}")


if __name__ == "__main__":
    main()
