#!/usr/bin/env bash
# Installa, aggiorna o rimuove il caricamento automatico dei log (vedi README.md).
# Si lancia come l'utente del bot, senza root: tutto resta nella sua cartella home.
#
#   installa.sh <file di configurazione>              installa o aggiorna
#   installa.sh --rimuovi <file di configurazione>    toglie la riga di crontab
#                                                     (restano chiave e copia dei log)
#
# Alla prima installazione crea la chiave di deploy, ne mostra la parte pubblica e si
# ferma: la si aggiunge al repository dei log con scrittura, poi si rilancia lo stesso comando.

set -euo pipefail

QUI=$(cd "$(dirname "$0")" && pwd)
RIMUOVI=0
if [ "${1:-}" = "--rimuovi" ]; then
    RIMUOVI=1
    shift
fi
CONF_DATA=${1:?"uso: installa.sh [--rimuovi] <file di configurazione>"}
[ -f "$CONF_DATA" ] || { echo "File di configurazione non trovato: $CONF_DATA"; exit 1; }
[ "$(id -u)" -ne 0 ] || { echo "Non lanciarlo come root: lancialo come l'utente del bot."; exit 1; }

# shellcheck source=esempio.conf
source "$CONF_DATA"
: "${ISTANZA:?manca ISTANZA nel file di configurazione}"
: "${REPO_LOG:?manca REPO_LOG nel file di configurazione}"
: "${CARTELLA_LOG:?manca CARTELLA_LOG nel file di configurazione}"
[[ "$ISTANZA" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "ISTANZA deve contenere solo lettere minuscole, cifre e trattini: $ISTANZA"; exit 1; }
CARTELLA_COPIA=${CARTELLA_COPIA:-$HOME/$ISTANZA-log}
CHIAVE=${CHIAVE:-$HOME/.ssh/${ISTANZA}_log}
ORARIO_CRON=${ORARIO_CRON:-5 * * * *}
REGISTRO=${REGISTRO:-$HOME/.local/state/caricamento-log/$ISTANZA.log}

SCRIPT=$HOME/.local/lib/caricamento-log/caricamento-log.sh
CONF=$HOME/.config/caricamento-log/$ISTANZA.conf
SEGNO="# caricamento-log:$ISTANZA"

righe_cron_senza_questa() { (crontab -l 2> /dev/null || true) | grep -vF -- "$SEGNO" || true; }

if [ "$RIMUOVI" = 1 ]; then
    righe_cron_senza_questa | crontab -
    echo "Caricamento automatico '$ISTANZA' rimosso da crontab."
    echo "Restano: la copia dei log $CARTELLA_COPIA, la chiave $CHIAVE, la configurazione $CONF."
    exit 0
fi

for comando in git ssh ssh-keygen crontab; do
    command -v "$comando" > /dev/null || { echo "Manca il comando '$comando': va installato prima."; exit 1; }
done
[ -d "$CARTELLA_LOG" ] || { echo "La cartella dei log del bot non esiste: $CARTELLA_LOG"; exit 1; }


# 1. Chiave di deploy: una per repository dei log, senza passphrase (la usa cron).
#    La parte privata non lascia mai il server.
COMANDO_SSH="ssh -i '$CHIAVE' -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
mostra_chiave() {
    echo
    echo "Aggiungi questa chiave pubblica al repository $REPO_LOG"
    echo "(GitHub: Settings → Deploy keys → Add deploy key, spunta 'Allow write access'):"
    echo
    cat "$CHIAVE.pub"
    echo
    echo "Poi rilancia: $0 $CONF_DATA"
}
if [ ! -f "$CHIAVE" ]; then
    mkdir -p "$(dirname "$CHIAVE")"
    chmod 700 "$(dirname "$CHIAVE")"
    ssh-keygen -q -t ed25519 -N "" -C "caricamento-log-$ISTANZA@$(hostname)" -f "$CHIAVE"
    echo "Creata la chiave di deploy $CHIAVE."
    mostra_chiave
    exit 2
fi

# Su GitHub si controlla che la chiave sia proprio quella del repository dei log
if [[ "$REPO_LOG" == git@github.com:* ]]; then
    REPO_NOME=${REPO_LOG#git@github.com:}
    REPO_NOME=${REPO_NOME%.git}
    BENVENUTO=$(eval "$COMANDO_SSH -T git@github.com" 2>&1 || true)
    if [[ "$BENVENUTO" != *"Hi $REPO_NOME!"* ]]; then
        echo "GitHub non riconosce la chiave come deploy key di $REPO_NOME. Risposta: $BENVENUTO"
        mostra_chiave
        exit 2
    fi
    echo "Chiave riconosciuta da GitHub per $REPO_NOME."
fi


# 2. Copia di lavoro del repository dei log
if [ ! -d "$CARTELLA_COPIA/.git" ]; then
    [ ! -e "$CARTELLA_COPIA" ] || { echo "$CARTELLA_COPIA esiste ma non è un repository git: spostala o scegli un'altra CARTELLA_COPIA."; exit 1; }
    GIT_SSH_COMMAND=$COMANDO_SSH git clone -q "$REPO_LOG" "$CARTELLA_COPIA"
    echo "Copia del repository dei log in $CARTELLA_COPIA."
fi
cd "$CARTELLA_COPIA"
[ "$(git remote get-url origin)" = "$REPO_LOG" ] || { echo "$CARTELLA_COPIA è collegata a $(git remote get-url origin), non a $REPO_LOG."; exit 1; }
git config core.sshCommand "$COMANDO_SSH"
git config user.name "caricamento-log $ISTANZA"
git config user.email "$(id -un)@$(hostname)"
git rev-parse -q --verify HEAD > /dev/null || git symbolic-ref HEAD refs/heads/main   # repository ancora vuoto
if [ ! -f README.md ]; then
    cat > README.md << FINE
# Log del bot · $ISTANZA

Repository di soli log, aggiornato in automatico dal server (caricamento-log, $ORARIO_CRON in crontab).
Non modificare i file a mano: il server li sovrascrive.

- **STATO.md**: stato del bot all'ultimo caricamento (servizio, processi, ultimo avvio, ultimo battito, disco, errori di oggi). Se l'ora in cima è vecchia, il caricamento si è fermato.
- **logs/AAAA-MM-GG/**: i log di ogni giorno (data italiana): \`bot.log\` (racconto), \`operazioni.txt\` (una scheda per operazione), \`errors.log\`, \`dettagli.log\`, \`journal.jsonl\` (i dati esatti).
FINE
    git add README.md
    git commit -q -m "README del repository dei log"
fi


# 3. Script e configurazione copiati nella home: il caricamento non dipende dalla cartella
#    del bot, e una versione nuova si attiva solo rilanciando installa.sh.
mkdir -p "$(dirname "$SCRIPT")" "$(dirname "$CONF")" "$(dirname "$REGISTRO")"
install -m 755 "$QUI/caricamento-log.sh" "$SCRIPT"
[ "$CONF_DATA" -ef "$CONF" ] || install -m 644 "$CONF_DATA" "$CONF"


# 4. Riga di crontab (sostituisce quella di un'installazione precedente)
RIGA="$ORARIO_CRON $SCRIPT $CONF >> $REGISTRO 2>&1 $SEGNO"
{ righe_cron_senza_questa; echo "$RIGA"; } | crontab -


# 5. Primo caricamento, subito
echo "Primo caricamento..."
if ! "$SCRIPT" "$CONF"; then
    echo
    echo "Primo caricamento non riuscito (vedi sopra). Se l'errore dice 'read only', alla deploy key"
    echo "manca 'Allow write access'. La riga di crontab è installata: riproverà da sola, oppure"
    echo "rilancia: $SCRIPT $CONF"
    exit 1
fi
echo
echo "Installato. Riga di crontab:"
echo "  $RIGA"
echo "Registro dei caricamenti: $REGISTRO"
