import logging
import os

from pyrogram import Client

# ── Logging setup ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("forwarder")

# Silence Pyrogram's own verbose logs unless it's a warning or above
logging.getLogger("pyrogram").setLevel(logging.INFO)


# ── Config ─────────────────────────────────────────────────────
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
SOURCE_GROUP_ID = os.environ["SOURCE_GROUP_ID"]
DEST_GROUP_ID = int(os.environ["DEST_GROUP_ID"])

with Client(
    "mi_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING
) as app:
    for dialog in app.get_dialogs():
        chat = dialog.chat
        print(f"{chat.title or chat.first_name:<40} | id: {chat.id}")


# with Client("mi_session", api_id=API_ID, api_hash=API_HASH) as app:
#     print("SESSION_STRING:", app.export_session_string())
