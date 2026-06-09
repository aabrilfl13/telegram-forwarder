import asyncio
import json
import os

import redis.asyncio as aioredis
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

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
QUEUE_KEY = "telegram:save_queue"

_db = None
_redis: aioredis.Redis | None = None
_running = True


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


async def _db_worker() -> None:
    """Single consumer — pops messages from Redis and saves to DB one at a time."""
    logger.info("DB worker started")
    while _running:
        result = await _redis.blpop(QUEUE_KEY, timeout=1)
        if result is None:
            continue
        _, raw = result
        try:
            data = json.loads(raw)
            await save_message(_db, data)
        except Exception as exc:
            logger.error(f"DB save failed: {exc}")
    logger.info("DB worker stopped")


async def resend(client, message):
    logger.debug(f"Received: {_message_summary(message)}")
    src_thread_id = message.message_thread_id or 1
    entry = get_mapping_entry(src_thread_id)

    if entry is None:
        logger.warning(f"No mapping for source thread {src_thread_id} (message {message.id}) — skipping")
        return

    if _redis is not None:
        try:
            await _redis.rpush(QUEUE_KEY, json.dumps(message.to_dict(), default=str))
        except Exception as exc:
            logger.error(f"Redis enqueue failed for message {message.id}: {exc}")

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
    global _db, _redis, _running

    _db = await init_db()
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)

    pending = await _redis.llen(QUEUE_KEY)
    if pending:
        logger.warning(f"Found {pending} unsaved messages in Redis from previous run — replaying now")

    worker = asyncio.create_task(_db_worker())

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
        _running = False
        await worker
        await _redis.aclose()
        await _db.dispose()
        logger.info("Shutdown complete")


asyncio.run(main())
