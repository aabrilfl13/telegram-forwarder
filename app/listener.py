import asyncio
import os

from common import (
    API_HASH,
    API_ID,
    DEST_GROUP_ID,
    SESSION_STRING,
    SOURCE_GROUP_ID,
    get_mapping_entry,
    load_thread_mapping,
)
from db import init_db
from logger import setup_logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageIdInvalid
from pyrogram.handlers import MessageHandler
from save_queue import QUEUE_KEY, connect_redis, db_worker, enqueue

logger = setup_logging("listener")
logger.info(f"Loaded {len(load_thread_mapping())} thread mappings")

DRY_RUN_FORWARD = os.getenv("DRY_RUN_FORWARD", "").lower() in ("1", "true", "yes")
DRY_RUN_DB = os.getenv("DRY_RUN_DB", "").lower() in ("1", "true", "yes")

_db = None
_redis = None


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

    if DRY_RUN_DB:
        logger.info(f"[DRY_RUN_DB] Would enqueue message {message.id} (thread {src_thread_id}) for DB save")
    elif _redis is not None:
        await enqueue(_redis, message)

    if not entry.get("enabled", False):
        logger.info(f"Thread {src_thread_id} disabled — skipping message {message.id}")
        return

    dest_thread_id = entry["dest"]

    if DRY_RUN_FORWARD:
        logger.info(
            f"[DRY_RUN_FORWARD] Would forward message {message.id}: "
            f"src thread {src_thread_id} → dest thread {dest_thread_id}"
        )
        return

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
    global _db, _redis

    if DRY_RUN_FORWARD:
        logger.warning("DRY_RUN_FORWARD enabled — messages will NOT be forwarded")
    if DRY_RUN_DB:
        logger.warning("DRY_RUN_DB enabled — messages will NOT be saved to DB or Redis")

    _db = await init_db()
    _redis = await connect_redis()

    pending = await _redis.llen(QUEUE_KEY)
    if pending:
        logger.warning(f"Found {pending} unsaved messages in Redis from previous run — replaying now")

    stop = asyncio.Event()
    worker = asyncio.create_task(db_worker(_redis, _db, stop))

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
        stop.set()
        await worker
        await _redis.aclose()
        await _db.dispose()
        logger.info("Shutdown complete")


asyncio.run(main())
