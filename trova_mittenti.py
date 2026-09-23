"""
Mostra chi scrive in TARGET_CHANNEL, per capire cosa mettere in ALLOWED_SENDER_IDS.

Per ogni messaggio recente stampa l'ID del mittente, il nome e l'inizio del
testo, poi un riepilogo per mittente. Dice anche se la chat è un CANALE (in cui
i post arrivano tutti con l'ID del canale, quindi il filtro non serve) o un
GRUPPO (in cui scrivono più persone e conviene filtrare sul trader).
Non chiama l'agente classificatore e non tocca MT5.

Uso, con il listener FERMO (usano la stessa sessione Telegram):
    python trova_mittenti.py          # ultimi 30 messaggi
    python trova_mittenti.py 100      # ultimi 100
"""
import asyncio
import os
import sys
from collections import Counter

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Channel, User

load_dotenv()

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "")


def describe_sender(sender) -> str:
    if isinstance(sender, User):
        name = " ".join(filter(None, [sender.first_name, sender.last_name])) or "(senza nome)"
        return f"{name} (@{sender.username})" if sender.username else name
    if isinstance(sender, Channel):
        return f"{sender.title} [il canale stesso]"
    return "sconosciuto"


async def main(limit: int):
    if not TARGET_CHANNEL:
        sys.exit("TARGET_CHANNEL non impostato nel .env")

    async with TelegramClient("session_test", TELEGRAM_API_ID, TELEGRAM_API_HASH) as client:
        chat = await client.get_entity(TARGET_CHANNEL)
        is_broadcast = isinstance(chat, Channel) and chat.broadcast
        kind = "CANALE (pubblicano solo gli amministratori)" if is_broadcast else "GRUPPO (possono scrivere più persone)"
        print(f"\nChat: {getattr(chat, 'title', TARGET_CHANNEL)} | tipo: {kind}\n")

        senders = Counter()
        async for msg in client.iter_messages(chat, limit=limit):
            sender = await msg.get_sender()
            name = describe_sender(sender)
            senders[(msg.sender_id, name)] += 1
            signature = f" | firma: {msg.post_author}" if msg.post_author else ""
            text = (msg.raw_text or "(solo media)").replace("\n", " ⏎ ")[:60]
            print(f"{msg.date:%Y-%m-%d %H:%M} | ID mittente {msg.sender_id} | {name}{signature} | {text}")

        print("\nRiepilogo mittenti:")
        for (sender_id, name), count in senders.most_common():
            print(f"  {sender_id}  {name}: {count} messaggi")

        if is_broadcast:
            print("\n👉 È un canale: i post arrivano tutti con l'ID del canale. Lascia ALLOWED_SENDER_IDS vuoto,\n"
                  "   il bot ascolta già solo questa chat.")
        else:
            print("\n👉 È un gruppo: copia l'ID del trader dal riepilogo e mettilo nel .env, es.\n"
                  "   ALLOWED_SENDER_IDS=123456789")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 30))
