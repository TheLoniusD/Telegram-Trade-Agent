# Piano B: MT5 su VM Windows + bridge HTTP

## Perché

Il pacchetto Python `MetaTrader5` è Windows-only. Un lungo tentativo di farlo
girare sotto Wine in Docker sul laptop Linux (due immagini diverse,
`gmag11/metatrader5_vnc` e `lprett/mt5linux`) si è sempre bloccato sullo
stesso errore strutturale: login al broker riuscito (confermato dal titolo
finestra `MetaTrader 5 - Netting - ...`), ma canale IPC locale
Python↔terminale mai funzionante (`-10005 IPC timeout`), un problema noto e
irrisolto anche upstream. Inoltre l'immagine usata non sopravvive a un
riavvio in-place (crash loop su `mkfifo` residuo), quindi non era comunque
adatta a girare 24/7 senza supervisione.

Vincoli dati dall'utente:
- un solo laptop Linux always-on (nessun secondo PC dedicabile a Windows);
- nessun costo ricorrente aggiuntivo (niente VPS/Forex-VPS a canone).

Soluzione: una VM Windows **sullo stesso laptop Linux**, con MT5 e il
pacchetto `MetaTrader5` installati nativamente (nessun Wine, nessun IPC
rotto), esposta al bot Linux tramite un piccolo servizio HTTP proprietario
(`windows_bridge/mt5_bridge_server.py`). `mt5_executor.py` è già stato
adattato per usare questo bridge quando è configurato (variabile d'ambiente
`MT5_BRIDGE_URL`), altrimenti si comporta come prima (import locale del
pacchetto, utile solo se il bot girasse direttamente su Windows).

## Architettura

```
Laptop Linux (always-on)
├── Docker: Jellyfin, Radarr, Prowlarr, Bazarr, qBittorrent   (invariato)
├── Bot: telegram_listener.py, agent_classifier.py, order_manager.py
│         └── mt5_executor.py --HTTP--> bridge sulla VM
└── VM KVM/QEMU "mt5-vm" (rete NAT libvirt, isolata dalla LAN)
      └── Windows (non attivato o Server eval)
            ├── terminal64.exe (MT5, sessione desktop sempre attiva)
            └── windows_bridge/mt5_bridge_server.py (porta 8765, token)
```

Il bridge non va MAI esposto oltre la rete virtuale host↔VM: equivale ad
accesso diretto al conto di trading (apertura/chiusura ordini reali), esatta
stessa classe di rischio già discussa per RPyC/VNC nel tentativo precedente.

## 1. Scegliere e dimensionare la VM

RAM totale del laptop: 7.2 GB, già condivisi con lo stack Docker esistente.
Margine realistico per la VM: **2-3 GB RAM, 2 vCPU, ~40 GB disco (qcow2,
thin-provisioned)** — MT5 + un piccolo processo Python Flask non pesano di
più. Prima di allocare, verificare l'headroom reale:

```bash
free -h
docker stats --no-stream   # quanto stanno usando Jellyfin/radarr/ecc. ora
```

Se lo stack Docker sotto carico (transcodifica Jellyfin, download attivi) si
avvicina al limite, conviene tenere la VM spenta fuori dagli orari di
trading (vedi punto 5) invece di lasciarla sempre accesa.

## 2. Sistema operativo: zero costo

Due opzioni, entrambe a costo zero:

- **Windows 10/11 non attivato (consigliata)**: si scarica l'ISO ufficiale
  gratis da Microsoft e si installa senza inserire un product key ("Non ho
  un codice Product Key"). Resta perfettamente funzionante a tempo
  indeterminato: l'unica limitazione è cosmetica (watermark "Attiva Windows"
  e personalizzazione bloccata). API, servizi, rete, MT5 e Python
  funzionano al 100% — per un uso di automazione headless che non guarda la
  UI, il watermark è irrilevante. Nessuna scadenza, nessun reinstall
  periodico.
- **Windows Server evaluation (alternativa)**: licenza di valutazione
  gratuita per 180 giorni, estendibile con `slmgr /rearm` fino a 5 volte
  (~3 anni totali), poi richiede reinstallazione. Più adatta se si preferisce
  un'immagine "pulita" senza watermark, a costo di manutenzione periodica.

Si consiglia di partire con Windows 10/11 non attivato: meno manutenzione,
nessuna scadenza da monitorare.

## 3. Creare la VM (KVM/QEMU/libvirt)

Sul laptop Linux (richiede `qemu-kvm`, `libvirt`, `virtinst` già disponibili
o installabili via il gestore pacchetti di Ubuntu):

```bash
# Disco della VM
qemu-img create -f qcow2 /var/lib/libvirt/images/mt5-vm.qcow2 40G

# Creazione VM (adattare il percorso dell'ISO Windows scaricata)
virt-install \
  --name mt5-vm \
  --memory 3072 --vcpus 2 \
  --disk path=/var/lib/libvirt/images/mt5-vm.qcow2,format=qcow2 \
  --cdrom /percorso/a/Windows.iso \
  --os-variant win10 \
  --network network=default \
  --graphics vnc,listen=127.0.0.1 \
  --video qxl
```

`--network network=default` usa la rete NAT isolata di libvirt: la VM è
raggiungibile dall'host ma non dalla LAN/Internet, esattamente come serve
per il bridge. Il collegamento a `--graphics vnc,listen=127.0.0.1` serve solo
per completare l'installazione di Windows via VNC tunnelato in SSH (stesso
approccio già usato per l'accesso senza mouse/monitor al laptop).

Dopo l'installazione: installare i virtio drivers per prestazioni disco/rete
migliori, poi recuperare l'IP interno della VM per il bridge:

```bash
virsh domifaddr mt5-vm
```

## 4. Installare MT5 + bridge sulla VM

Dentro la VM Windows:

1. Installare Python 3.11+ (da python.org, spuntando "Add to PATH").
2. Installare il terminale MT5 del broker e fare login una volta a mano
   (salva le credenziali, così non serve reinserirle a ogni avvio).
3. Copiare `windows_bridge/mt5_bridge_server.py` e
   `windows_bridge/requirements.txt` dal repository (bastano questi due
   file, non l'intero progetto).
4. `pip install -r requirements.txt`
5. Impostare le variabili d'ambiente (es. in un file `.bat` di avvio):
   ```bat
   set MT5_BRIDGE_TOKEN=<segreto-lungo-e-casuale>
   set MT5_BRIDGE_PORT=8765
   python mt5_bridge_server.py
   ```
   Generare il token con qualcosa come `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### Avvio automatico senza login manuale

MT5 richiede una sessione desktop attiva anche se pilotato via API (non è un
vero processo headless). Per farlo partire da solo al boot della VM:

1. Abilitare l'auto-logon di Windows per l'utente dedicato (`netplwiz` →
   deseleziona "Richiedi nome utente e password" per quell'utente, oppure le
   chiavi di registro `AutoAdminLogon`).
2. Mettere un file `.bat` nella cartella Startup dell'utente
   (`shell:startup`) che lancia `terminal64.exe /portable`, attende qualche
   secondo, poi lancia `mt5_bridge_server.py` con le variabili d'ambiente.

In alternativa, se si preferisce non abilitare l'auto-logon: aprire una
sessione RDP verso la VM e poi **disconnettersi senza fare logoff** — la
sessione resta attiva in background (comportamento standard di Windows) e
MT5/bridge continuano a girare.

## 5. Avvio/spegnimento della VM

Per ridurre il carico sul laptop fuori mercato e la superficie di rischio
(Windows Update, esposizione del bridge), conviene avviare la VM solo
durante l'orario di trading invece di tenerla sempre accesa:

```bash
virsh start mt5-vm      # avvio
virsh shutdown mt5-vm   # spegnimento pulito
```

Questi comandi si possono schedulare con `cron` in base agli stessi orari
già codificati in `market_hours.py`. In alternativa, per semplicità
iniziale, si può usare `virsh autostart mt5-vm` e tenerla sempre accesa: dato
il basso carico (2-3 GB RAM), è comunque un'opzione ragionevole se
l'headroom del laptop lo consente.

## 6. Collegare il bot Linux al bridge

Nel `.env` del bot (lato Linux), aggiungere:

```
MT5_BRIDGE_URL='http://<ip-interno-vm>:8765'
MT5_BRIDGE_TOKEN='<stesso-segreto-generato-sopra>'
```

Nessuna altra modifica: `mt5_executor.py` rileva `MT5_BRIDGE_URL` ed usa il
bridge per ogni operazione reale (apertura, BE, chiusura, modifica SL/TP,
stato posizione, tradability del simbolo), mantenendo lo stesso `TEST_MODE`
e le stesse strutture di ritorno di prima. In `TEST_MODE=True` (l'attuale)
il bridge viene comunque contattato per `is_symbol_tradable()` (stato reale
del mercato), ma nessun ordine reale viene mai inviato.

## 7. Verifica end-to-end

1. Avviare la VM e il bridge, controllare `logs/bridge.log` sulla VM.
2. Da Linux: `curl -H "X-Bridge-Token: <segreto>" http://<ip-vm>:8765/health`
   deve rispondere `{"ok": true, "mt5_initialized": true}`.
3. Avviare il bot Linux con `MT5_BRIDGE_URL` impostata e verificare nei log
   (`logs/bot.log`) il messaggio "🚀 Bridge Windows raggiunto, MT5 connesso".
4. Solo a questo punto, quando si deciderà di passare a `TEST_MODE=False`,
   fare un primo test con size minima sul conto demo MetaQuotes prima di
   qualunque operatività reale.

## Nota per chi implementa

I comandi di questa guida (creazione VM, installazione Windows, `virsh`,
configurazione rete) vanno eseguiti direttamente sul laptop Linux
dell'utente: richiedono accesso interattivo alla macchina (SSH/console) che
una sessione di sviluppo su repository non ha. Il lavoro fatto qui nel
repository (bridge HTTP, adattamento di `mt5_executor.py`, questa guida) è
il codice e la documentazione necessari; l'infrastruttura (VM, rete, Windows)
va provisionata dall'utente seguendo i passi sopra, eventualmente insieme in
una sessione con accesso al laptop.
