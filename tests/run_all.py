"""
Verifica completa del bot: da lanciare dopo ogni modifica e prima di ogni push.

    python tests/run_all.py            (python3 su Linux)

1. compila tutti i file .py del progetto (errori di sintassi);
2. lancia ogni tests/test_*.py in un processo separato, con il broker finto
   (nessun MT5, Telegram, agente o .env; log e stato in una cartella
   temporanea, mai quelli veri).
Esce con codice 1 se qualcosa fallisce e mostra l'output del test fallito.
"""
import glob
import os
import re
import subprocess
import sys
import time

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
TIMEOUT_SECONDS = 300


def main() -> int:
    failed = []

    errors = []
    sources = glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(TESTS, "*.py")) \
        + glob.glob(os.path.join(ROOT, ".claude", "**", "*.py"), recursive=True)
    for path in sorted(sources):
        try:
            with open(path, encoding="utf-8") as source:
                compile(source.read(), path, "exec")    # solo controllo, nessun file scritto
        except SyntaxError as e:
            errors.append(f"{os.path.basename(path)} riga {e.lineno}: {e.msg}")
    if errors:
        failed.append("compilazione")
        print("NO  compilazione dei file .py")
        for error in errors:
            print("    " + error)
    else:
        print("ok  compilazione dei file .py")

    for path in sorted(glob.glob(os.path.join(TESTS, "test_*.py"))):
        name = os.path.basename(path)
        start = time.monotonic()
        try:
            run = subprocess.run([sys.executable, path], cwd=TESTS, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
            output, code = run.stdout + run.stderr, run.returncode
        except subprocess.TimeoutExpired as e:
            output, code = f"{e.stdout or ''}\nTEMPO SCADUTO dopo {TIMEOUT_SECONDS} s", -1
        summary = re.findall(r"(\d+) controlli riusciti, (\d+) falliti", output)
        detail = f"{summary[-1][0]} controlli" if summary else "nessun riepilogo"
        if code == 0:
            print(f"ok  {name} ({detail}, {time.monotonic() - start:.0f} s)")
        else:
            failed.append(name)
            print(f"NO  {name} ({detail})")
            lines = [line for line in output.splitlines() if line.startswith(("==", "   NO", "Traceback", "  File", "    "))
                     or "Error" in line or "falliti" in line or "Log e stato" in line]
            for line in lines[-60:]:
                print("    " + line)

    print()
    if failed:
        print(f"VERIFICA FALLITA: {', '.join(failed)}")
        return 1
    print("VERIFICA RIUSCITA: tutti i test passano")
    return 0


if __name__ == "__main__":
    sys.exit(main())
