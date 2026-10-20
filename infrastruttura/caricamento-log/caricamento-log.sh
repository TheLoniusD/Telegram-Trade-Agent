#!/usr/bin/env bash
# Caricamento automatico dei log del bot su un repository Git di soli log (vedi README.md).
# Lo lancia cron, installato da installa.sh: copia la cartella dei log nella copia di lavoro
# del repository dei log, scrive STATO.md con lo stato del bot, fa un commit e lo pubblica.
# Non scrive mai nella cartella del bot e non ne aggiorna il codice.
#
#   caricamento-log.sh <file di configurazione>

set -uo pipefail

CONF=${1:?"uso: caricamento-log.sh <file di configurazione>"}
# shellcheck source=esempio.conf
source "$CONF" || exit 1

: "${ISTANZA:?manca ISTANZA nel file di configurazione}"
: "${REPO_LOG:?manca REPO_LOG nel file di configurazione}"
: "${CARTELLA_LOG:?manca CARTELLA_LOG nel file di configurazione}"
CARTELLA_COPIA=${CARTELLA_COPIA:-$HOME/$ISTANZA-log}
FUSO=${FUSO:-Europe/Rome}
SERVIZIO_BOT=${SERVIZIO_BOT-telegram-trade-agent.service}
PROCESSO_BOT=${PROCESSO_BOT-telegram_listener.py}
SERVIZI_EXTRA=${SERVIZI_EXTRA:-}
RIGA_AVVIO=${RIGA_AVVIO:-Bot avviato}
RIGA_BATTITO=${RIGA_BATTITO:-Bot attivo}
ORE_BATTITO=${ORE_BATTITO:-5}
REGISTRO=${REGISTRO:-$HOME/.local/state/caricamento-log/$ISTANZA.log}
export TZ=$FUSO

adesso() { date '+%Y-%m-%d %H:%M:%S'; }
errore() { echo "$(adesso) ERRORE: $*"; exit 1; }

# Il registro (il file dove cron scrive l'uscita) resta sotto le 2000 righe. Si accorcia
# riscrivendolo sul posto: cron lo tiene aperto in aggiunta, un file nuovo non lo vedrebbe.
if [ -f "$REGISTRO" ] && [ "$(wc -l < "$REGISTRO")" -gt 2000 ]; then
    tail -n 1000 "$REGISTRO" > "$REGISTRO.tmp" && cat "$REGISTRO.tmp" > "$REGISTRO"
    rm -f "$REGISTRO.tmp"
fi

[ -d "$CARTELLA_COPIA/.git" ] || errore "manca la copia del repository dei log $CARTELLA_COPIA: rilancia installa.sh"
[ -d "$CARTELLA_LOG" ] || errore "manca la cartella dei log del bot $CARTELLA_LOG"

# Un solo caricamento alla volta
if command -v flock > /dev/null; then
    exec 9> "$CARTELLA_COPIA/.git/caricamento-log.lock"
    flock -n 9 || { echo "$(adesso) un altro caricamento è in corso: salto"; exit 0; }
fi

cd "$CARTELLA_COPIA" || errore "non riesco a entrare in $CARTELLA_COPIA"


# ---------------------------------------------------------------- STATO.md
PROBLEMI=()
RIGHE=()
aggiungi() { RIGHE+=("| $1 | ${2//|/\\|} |"); }    # una riga della tabella di STATO.md
problema() { PROBLEMI+=("$1"); }
giorno_breve() { date -d "$1" '+%d/%m'; }

controlla_servizio() {
    local nome=$1 attivo dal riavvii
    if ! command -v systemctl > /dev/null; then
        aggiungi "Servizio \`$nome\`" "non verificabile (manca systemctl)"
        return
    fi
    attivo=$(systemctl is-active "$nome" 2> /dev/null)
    if [ "$attivo" = active ]; then
        dal=$(systemctl show -p ActiveEnterTimestamp --value "$nome" 2> /dev/null)
        dal=$(date -d "$dal" '+%d/%m/%Y %H:%M' 2> /dev/null || echo "${dal:-?}")
        riavvii=$(systemctl show -p NRestarts --value "$nome" 2> /dev/null)
        aggiungi "Servizio \`$nome\`" "✅ attivo dal $dal · riavvii automatici: ${riavvii:-?}"
    else
        aggiungi "Servizio \`$nome\`" "❌ ${attivo:-sconosciuto}"
        problema "servizio $nome non attivo (${attivo:-sconosciuto})"
    fi
}

controlla_processi() {
    local quanti
    if ! command -v pgrep > /dev/null; then
        aggiungi "Processi del bot" "non verificabile (manca pgrep)"
        return
    fi
    quanti=$(pgrep -fc -- "$PROCESSO_BOT")
    case "$quanti" in
        1) aggiungi "Processi del bot" "✅ 1" ;;
        0) aggiungi "Processi del bot" "❌ nessuno"; problema "il bot non è in esecuzione" ;;
        *) aggiungi "Processi del bot" "⚠️ $quanti (deve essere uno solo)"; problema "$quanti processi del bot invece di uno" ;;
    esac
}

# Ultima riga del bot.log che contiene $1, oggi o ieri: stampa "AAAA-MM-GG|riga"
ultima_riga() {
    local giorno riga
    for giorno in "$(date +%F)" "$(date -d yesterday +%F)"; do
        [ -f "$CARTELLA_LOG/$giorno/bot.log" ] || continue
        riga=$(grep -F -- "$1" "$CARTELLA_LOG/$giorno/bot.log" | tail -n 1)
        if [ -n "$riga" ]; then
            echo "$giorno|$riga"
            return
        fi
    done
}

controlla_bot_log() {
    local trovata giorno riga quando ore segno
    trovata=$(ultima_riga "$RIGA_AVVIO")
    if [ -n "$trovata" ]; then
        aggiungi "Ultimo avvio" "$(giorno_breve "${trovata%%|*}") ${trovata#*|}"
    else
        aggiungi "Ultimo avvio" "non trovato oggi né ieri"
    fi

    trovata=$(ultima_riga "$RIGA_BATTITO")
    if [ -z "$trovata" ]; then
        aggiungi "Ultimo battito" "❌ non trovato oggi né ieri"
        problema "nessun battito del bot oggi né ieri"
        return
    fi
    giorno=${trovata%%|*}
    riga=${trovata#*|}
    quando=$(date -d "$giorno ${riga%% *}" +%s 2> /dev/null || echo 0)
    ore=$(( ($(date +%s) - quando) / 3600 ))
    if [ "$ore" -ge "$ORE_BATTITO" ]; then
        segno="⚠️"
        problema "ultimo battito del bot $ore ore fa"
    else
        segno="✅"
    fi
    aggiungi "Ultimo battito" "$segno $(giorno_breve "$giorno") $riga"
}

controlla_disco() {
    local riga uso
    riga=$(df -h --output=avail,pcent "$CARTELLA_LOG" 2> /dev/null | tail -n 1)
    [ -n "$riga" ] || return
    uso=$(echo "$riga" | awk '{print $2}')
    if [ "${uso%\%}" -ge 90 ] 2> /dev/null; then
        aggiungi "Disco" "⚠️ usato $uso, liberi $(echo "$riga" | awk '{print $1}')"
        problema "disco quasi pieno ($uso)"
    else
        aggiungi "Disco" "✅ usato $uso, liberi $(echo "$riga" | awk '{print $1}')"
    fi
}

scrivi_stato() {
    local errori
    echo "# Stato del bot · $ISTANZA"
    echo
    echo "Aggiornato il $(date '+%d/%m/%Y alle %H:%M') (ora di $FUSO) dal server \`$(hostname)\`."
    echo "Si aggiorna a ogni caricamento: se quest'ora è vecchia, il caricamento automatico si è fermato."
    echo
    if [ ${#PROBLEMI[@]} -eq 0 ]; then
        echo "**✅ Tutto regolare**"
    else
        echo "**⚠️ Da controllare**"
        echo
        printf -- '- %s\n' "${PROBLEMI[@]}"
    fi
    echo
    echo "| | |"
    echo "|---|---|"
    printf '%s\n' "${RIGHE[@]}"
    echo
    echo "## Ultime righe di errors.log di oggi"
    echo
    errori="$CARTELLA_LOG/$(date +%F)/errors.log"
    if [ -s "$errori" ]; then
        echo '```'
        tail -n 5 "$errori"
        echo '```'
    else
        echo "Nessun errore registrato oggi."
    fi
}


# ---------------------------------------------------------------- caricamento
# 1. I log: si copia solo il contenuto della cartella dei log, nient'altro del progetto
mkdir -p logs
cp -a "$CARTELLA_LOG/." logs/ || errore "copia dei log non riuscita"

# 2. Lo stato del bot
[ -z "$SERVIZIO_BOT" ] || controlla_servizio "$SERVIZIO_BOT"
for servizio in $SERVIZI_EXTRA; do
    controlla_servizio "$servizio"
done
[ -z "$PROCESSO_BOT" ] || controlla_processi
controlla_bot_log
controlla_disco
scrivi_stato > STATO.md || errore "STATO.md non scritto"

# 3. Commit e pubblicazione. Il push parte anche senza commit nuovi: pubblica quelli
#    rimasti indietro (es. rete assente all'ora precedente).
git add -A -- logs STATO.md || errore "git add non riuscito"
if git diff --cached --quiet; then
    echo "$(adesso) niente di nuovo"
else
    git commit -q -m "Log del $(date '+%d/%m/%Y %H:%M')" || errore "commit non riuscito"
fi

if ! uscita=$(git push -q origin HEAD:main 2>&1); then
    # Rifiutato perché il repository remoto è avanti (es. un file cambiato da GitHub):
    # ci si allinea con un merge e si riprova. Gli errori di credenziali falliscono anche qui.
    echo "$(adesso) push rifiutato, mi allineo: $uscita"
    if ! uscita=$(git pull -q --no-rebase --no-edit origin main 2>&1); then
        git merge --abort 2> /dev/null
        errore "pull non riuscito: $uscita"
    fi
    uscita=$(git push -q origin HEAD:main 2>&1) || errore "push non riuscito: $uscita"
fi
echo "$(adesso) caricato: $(git log -1 --format='%h %s') · cose da controllare: ${#PROBLEMI[@]}"
