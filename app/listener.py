from common import (
    API_HASH,
    API_ID,
    DEST_GROUP_ID,
    SESSION_STRING,
    SOURCE_GROUP_ID,
    get_mapping_entry,
    is_already_in_group_chat,
    load_forwarded_origins,
    load_thread_mapping,
)
from logger import setup_logging
import asyncio

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageIdInvalid

logger = setup_logging("listener")
logger.info(f"Loaded {len(load_thread_mapping())} thread mappings")

app = Client(
    "session/mi_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)


@app.on_message(filters.chat(SOURCE_GROUP_ID) & ~filters.service)
async def resend(client, message):
    src_thread_id = message.message_thread_id
    entry = get_mapping_entry(src_thread_id)

    if entry is None:
        logger.warning(f"No mapping for source thread {src_thread_id} (message {message.id}) — skipping")
        return

    if not entry.get("enabled", False):
        logger.info(f"Thread {src_thread_id} disabled — skipping message {message.id}")
        return

    dest_thread_id = entry["dest"]

    forwarded_origins = await load_forwarded_origins(client, dest_thread_id)
    if is_already_in_group_chat(message, forwarded_origins):
        logger.info(f"Skipping message {message.id} — already in dest thread {dest_thread_id}")
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
            logger.info(f"Forwarded message {message.id} (after FloodWait): src thread {src_thread_id} → dest thread {dest_thread_id}")
        except Exception as retry_e:
            logger.error(f"Failed to forward message {message.id} after FloodWait retry: {retry_e}", exc_info=True)
    except Exception as e:
        logger.error(f"Failed to forward message {message.id}: {e}", exc_info=True)


logger.info(f"Listener starting — source chat {SOURCE_GROUP_ID} → dest chat {DEST_GROUP_ID}")
app.run()
