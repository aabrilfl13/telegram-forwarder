import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client
from telegram_types import (
    MessageOriginChannel,
    MessageOriginChat,
    MessageOriginUser,
    TelegramMessage,
)

__all__ = [
    "API_ID",
    "API_HASH",
    "SESSION_STRING",
    "SOURCE_GROUP_ID",
    "DEST_GROUP_ID",
    "TelegramMessage",
    "load_thread_mapping",
    "get_mapping_entry",
    "load_forwarded_origins",
    "is_already_in_group_chat",
]

load_dotenv()

# ── Config ─────────────────────────────────────────────────────
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
SOURCE_GROUP_ID = int(os.environ["SOURCE_GROUP_ID"])
DEST_GROUP_ID = int(os.environ["DEST_GROUP_ID"])

MAPPING_PATH = Path(__file__).parent / "data" / "thread_mapping.jsonc"


def load_thread_mapping() -> dict[str, dict]:
    text = MAPPING_PATH.read_text(encoding="utf-8")
    text = re.sub(r"//.*", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)["thread_mapping"]


def get_mapping_entry(src_thread_id: int) -> dict | None:
    """Re-read the mapping file each time so toggling `enabled` takes effect live."""
    return load_thread_mapping().get(str(src_thread_id))


def _origin_fingerprint(origin) -> tuple | None:
    if isinstance(origin, MessageOriginChannel):
        return ("channel", origin.message_id)
    if isinstance(origin, MessageOriginUser) and origin.sender_user is not None:
        return ("user", origin.date, origin.sender_user.id)
    if isinstance(origin, MessageOriginChat) and origin.sender_chat is not None:
        return ("chat", origin.date, origin.sender_chat.id)
    return None


def _src_fingerprint(src_msg: TelegramMessage) -> tuple | None:
    if src_msg.from_user is not None:
        return ("user", src_msg.date, src_msg.from_user.id)
    if src_msg.sender_chat is not None:
        return ("chat", src_msg.date, src_msg.sender_chat.id)
    return None


async def load_forwarded_origins(app: Client, dest_thread_id: int) -> set:
    """Scan dest_thread_id once and return a set of forward-origin fingerprints."""
    seen: set = set()
    async for dest_msg in app.search_messages(
        DEST_GROUP_ID,
        message_thread_id=dest_thread_id,
    ):
        if dest_msg.forward_origin is not None:
            fp = _origin_fingerprint(dest_msg.forward_origin)
            if fp is not None:
                seen.add(fp)
    return seen


def is_already_in_group_chat(message: TelegramMessage, forwarded_origins: set) -> bool:
    """Return True if message fingerprint is present in the pre-loaded origin set."""
    fp = _src_fingerprint(message)
    return fp is not None and fp in forwarded_origins
