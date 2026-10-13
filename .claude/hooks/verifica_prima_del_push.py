"""
Hook di Claude Code (PreToolUse sul tool Bash): prima di ogni "git push" lancia
la verifica completa (tests/run_all.py) e blocca il push se qualcosa fallisce.
Gli altri comandi passano subito. Configurato in .claude/settings.json.
"""
import json
import os
import re
import subprocess
import sys

data = json.load(sys.stdin)
command = (data.get("tool_input") or {}).get("command") or ""
if not re.search(r"\bgit\b[^;&|\n]*\bpush\b", command):
    sys.exit(0)

root = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
runner = os.path.join(root, "tests", "run_all.py")
if not os.path.exists(runner):
    sys.exit(0)  # branch senza test (es. vecchio server non ancora aggiornato): non blocca

run = subprocess.run([sys.executable, runner], cwd=root, capture_output=True, text=True,
                     encoding="utf-8", errors="replace")
if run.returncode != 0:
    sys.stderr.write("Push bloccato: la verifica tests/run_all.py non passa. Correggi e rilancia i test prima di pubblicare.\n\n"
                     + run.stdout[-4000:] + run.stderr[-1000:])
    sys.exit(2)
sys.exit(0)
