"""
STATO.md e l'agente che non risponde (09–11/11).

Per due giorni e mezzo ogni chiamata all'agente è fallita, con servizi, processo
e battito a posto: STATO.md diceva "✅ Tutto regolare". Ora caricamento-log.sh
legge le righe del racconto che il bot scrive durante il guasto (test_agente.py)
e dà "⚠️ Da controllare" finché l'ultima, oggi o ieri, è "Agente non
raggiungibile".

Lancia lo script vero, con bash, su una cartella di log finta e un repository
dei log locale (nessuna rete, nessun systemctl né pgrep). Su Windows serve il
bash di Git; se manca il test lo dice e non controlla nulla.
"""
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

from banco import *

SCRIPT = os.path.join(ROOT, "infrastruttura", "caricamento-log", "caricamento-log.sh")
BASE = os.path.join(WORKDIR, "stato")


def trova_bash():
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    candidates = [os.path.join(os.path.dirname(os.path.dirname(git)), "bin", "bash.exe")] if git else []
    candidates.append(r"C:\Program Files\Git\bin\bash.exe")
    return next((c for c in candidates if os.path.exists(c)), None)


def unix(path):
    return path.replace("\\", "/")


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def prepara():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(os.path.join(BASE, "log"))
    git("init", "-q", "--bare", "-b", "main", os.path.join(BASE, "remoto.git"))
    git("clone", "-q", os.path.join(BASE, "remoto.git"), os.path.join(BASE, "copia"))
    git("config", "user.name", "banco", cwd=os.path.join(BASE, "copia"))
    git("config", "user.email", "banco@example.invalid", cwd=os.path.join(BASE, "copia"))
    with open(os.path.join(BASE, "prova.conf"), "w", encoding="utf-8", newline="\n") as conf:
        conf.write(f'ISTANZA=prova\nREPO_LOG=prova\nCARTELLA_LOG="{unix(os.path.join(BASE, "log"))}"\n'
                   f'CARTELLA_COPIA="{unix(os.path.join(BASE, "copia"))}"\nFUSO=UTC\nSERVIZIO_BOT=""\n'
                   f'PROCESSO_BOT=""\nREGISTRO="{unix(os.path.join(BASE, "registro.log"))}"\n')


NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
YESTERDAY = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")


def ora(minuti_fa):
    t = NOW - timedelta(minutes=minuti_fa)
    return t.strftime("%H:%M:%S") if t.strftime("%Y-%m-%d") == TODAY else "00:00:00"


def battito(minuti_fa, guasto=""):
    return f"{ora(minuti_fa)} 💓 Bot attivo · saldo 6.145,67 $ · equity 6.145,67 $ · 0 operazioni aperte{guasto}"


def stato(bash, oggi, ieri=()):
    """Scrive i bot.log, lancia il caricamento e restituisce STATO.md."""
    for day, lines in ((TODAY, oggi), (YESTERDAY, ieri)):
        folder = os.path.join(BASE, "log", day)
        shutil.rmtree(folder, ignore_errors=True)
        if lines:
            os.makedirs(folder)
            with open(os.path.join(folder, "bot.log"), "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
    run = subprocess.run([bash, unix(SCRIPT), unix(os.path.join(BASE, "prova.conf"))],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    path = os.path.join(BASE, "copia", "STATO.md")
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if not text:
        print(run.stdout, run.stderr)
    return text


KO_MSG = "🚨 Agente non raggiungibile: messaggio non classificato · BadRequestError: Your credit balance is too low"
KO_DOPO = "🚨 Agente non raggiungibile dal 09/11 03:05: messaggio non classificato (3°)"
OK = "✅ Agente di nuovo raggiungibile · non rispondeva dal 09/11 03:05 · 33 messaggi non classificati"
PROBLEMA = "l'agente non risponde"

bash = trova_bash()
if not bash:
    scenario("bash non trovato: controllo di STATO.md saltato (sul server c'è sempre)")
    fine()
prepara()

scenario("Agente che non risponde oggi: STATO.md \"Da controllare\"")
s = stato(bash, [battito(60), f"{ora(30)} 📩 Trader: \"Gold buy 4125\"", f"{ora(30)} {KO_MSG}",
                 f"{ora(20)} {KO_DOPO}"])
check("⚠️ Da controllare" in s and PROBLEMA in s, "in cima: \"l'agente non risponde\"", s[:600])
check("| Agente | ❌" in s, "riga Agente con ❌")

scenario("Guasto finito: tutto regolare")
s = stato(bash, [battito(60), f"{ora(30)} {KO_MSG}", f"{ora(10)} {OK}"])
check("✅ Tutto regolare" in s and PROBLEMA not in s, "in cima: tutto regolare", s[:600])
check("| Agente | ✅" in s and "di nuovo raggiungibile" in s, "riga Agente con il ritorno")

scenario("Guasto iniziato ieri, oggi solo il battito che lo ripete")
s = stato(bash, [battito(30, " · 🚨 Agente non raggiungibile dal 09/11 03:05 (12 messaggi non classificati)")],
          ieri=[f"03:05:08 {KO_MSG}"])
check(PROBLEMA in s, "il battito con il guasto basta", s[:600])

scenario("Riavvio dopo il guasto (chiave nuova): niente più avviso")
s = stato(bash, [f"{ora(90)} {KO_DOPO}", f"{ora(60)} ▶️ Bot avviato · conto DEMO · saldo 6.145,67 $",
                 battito(59)])
check("✅ Tutto regolare" in s and "nessun guasto in corso" in s, "riga Agente: nessun guasto", s[:600])

scenario("Nessun guasto, come ogni giorno")
s = stato(bash, [battito(30)], ieri=[battito(600)])
check("✅ Tutto regolare" in s and "| Agente | ✅ nessun guasto in corso" in s, "tutto regolare", s[:600])

fine()
