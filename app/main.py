import logging
import os

from dotenv import load_dotenv
from pyrogram import Client, filters

load_dotenv()


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
SOURCE_GROUP_ID = int(os.environ["SOURCE_GROUP_ID"])
DEST_GROUP_ID = int(os.environ["DEST_GROUP_ID"])

# Obtain from https://my.telegram.org
app = Client(
    "session/mi_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)


@app.on_message(filters.chat(SOURCE_GROUP_ID) & ~filters.service)
async def resend(client, message):
    try:
        await message.forward(DEST_GROUP_ID)
        logger.info(f"Forwarded message {message.id} from '{message.chat.title}'")
    except Exception as e:
        logger.error(f"Failed to forward message {message.id}: {e}", exc_info=True)


logger.info(f"Bot starting — listening to '{SOURCE_GROUP_ID}'")
logger.info(f"Forwarding to chat id {DEST_GROUP_ID}")
app.run()
