import asyncio

from common import (
    API_HASH,
    API_ID,
    DEST_GROUP_ID,
    SESSION_STRING,
    SOURCE_GROUP_ID,
    get_mapping_entry,
    load_thread_mapping,
)
from db import init_db, save_message
from logger import setup_logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageIdInvalid
from pyrogram.handlers import MessageHandler

logger = setup_logging("listener")
logger.info(f"Loaded {len(load_thread_mapping())} thread mappings")

_db = None


def _message_summary(message) -> str:
    sender = (
        (
            getattr(message.from_user, "username", None)
            or getattr(message.from_user, "first_name", None)
            or getattr(message.sender_chat, "title", None)
            or "unknown"
        )
        if (message.from_user or message.sender_chat)
        else "unknown"
    )
    text = (message.text or message.caption or "")[:80]
    preview = f" | {text!r}" if text else " | [no text]"
    return f"id={message.id} thread={message.message_thread_id} from={sender}{preview}"


async def resend(client, message):
    logger.debug(f"Received: {_message_summary(message)}")
    src_thread_id = message.message_thread_id or 1
    entry = get_mapping_entry(src_thread_id)

    if entry is None:
        logger.warning(f"No mapping for source thread {src_thread_id} (message {message.id}) — skipping")
        return

    if _db is not None:
        try:
            await save_message(_db, message)
        except Exception as exc:
            logger.error(f"DB save failed for message {message.id}: {exc}")

    if not entry.get("enabled", False):
        logger.info(f"Thread {src_thread_id} disabled — skipping message {message.id}")
        return

    dest_thread_id = entry["dest"]
    logger.debug(f"Routing message {message.id}: src thread {src_thread_id} → dest thread {dest_thread_id}")

    try:
        await client.forward_messages(
            chat_id=DEST_GROUP_ID,
            from_chat_id=SOURCE_GROUP_ID,
            message_ids=message.id,
            message_thread_id=dest_thread_id,
        )
        logger.info(f"Forwarded message {message.id}: src thread {src_thread_id} → dest thread {dest_thread_id}")
    except MessageIdInvalid:
        logger.warning(f"Message {message.id} no longer exists — skipping")
    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s for message {message.id} — waiting and retrying")
        await asyncio.sleep(e.value)
        try:
            await client.forward_messages(
                chat_id=DEST_GROUP_ID,
                from_chat_id=SOURCE_GROUP_ID,
                message_ids=message.id,
                message_thread_id=dest_thread_id,
            )
            logger.info(
                f"Forwarded message {message.id} (after FloodWait): "
                f"src thread {src_thread_id} → dest thread {dest_thread_id}"
            )
        except Exception as retry_e:
            logger.error(f"Failed to forward message {message.id} after FloodWait retry: {retry_e}", exc_info=True)
    except Exception as e:
        logger.error(f"Failed to forward message {message.id}: {e}", exc_info=True)


async def main():
    global _db
    _db = await init_db()

    app = Client(
        "session/mi_session",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
    )
    app.add_handler(MessageHandler(resend, filters.chat(SOURCE_GROUP_ID) & ~filters.service))

    try:
        async with app:
            logger.info(f"Listener starting — source chat {SOURCE_GROUP_ID} → dest chat {DEST_GROUP_ID}")
            await asyncio.Event().wait()
    finally:
        await _db.dispose()
        logger.info("Database closed")


asyncio.run(main())
