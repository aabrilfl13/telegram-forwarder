import argparse
import asyncio
from pathlib import Path

from common import (
    API_HASH,
    API_ID,
    DEST_GROUP_ID,
    SESSION_STRING,
    SOURCE_GROUP_ID,
    TelegramMessage,
    is_already_in_group_chat,
    load_forwarded_origins,
    load_thread_mapping,
)
from logger import setup_logging
from pyrogram import Client
from pyrogram.errors import FloodWait

logger = setup_logging("populate")

MESSAGES_PER_THREAD = 500
SLEEP_BETWEEN_FORWARDS = 0.75  # seconds
DEBUG_DUMP_PATH = Path(__file__).parent / "data" / "messages_debug.log"


def load_enabled_mapping() -> dict[int, int]:
    """Return {src_thread_id: dest_thread_id} for entries with enabled=true."""
    return {int(k): int(v["dest"]) for k, v in load_thread_mapping().items() if v.get("populate", False)}


async def forward_one(app: Client, message_id: int, dest_thread_id: int):
    """Forward a single source message into the destination thread.

    Returns the resulting Message in the destination, or None on failure.
    """
    result = await app.forward_messages(
        chat_id=DEST_GROUP_ID,
        from_chat_id=SOURCE_GROUP_ID,
        message_ids=message_id,
        message_thread_id=dest_thread_id,
    )
    # forward_messages returns Message | list[Message] depending on input
    return result[0] if isinstance(result, list) else result


async def pin_in_destination(app: Client, dest_message_id: int) -> bool:
    """Pin a message in the destination chat. Returns True on success."""
    try:
        await app.pin_chat_message(
            chat_id=DEST_GROUP_ID,
            message_id=dest_message_id,
            disable_notification=True,
        )
        return True
    except FloodWait as e:
        logger.warning(f"⚠️  FloodWait {e.value}s while pinning {dest_message_id}")
        await asyncio.sleep(e.value + 1)
        try:
            await app.pin_chat_message(
                chat_id=DEST_GROUP_ID,
                message_id=dest_message_id,
                disable_notification=True,
            )
            return True
        except Exception as e2:
            logger.error(f"Pin retry failed for {dest_message_id}: {e2}")
            return False
    except Exception as e:
        logger.error(f"Failed to pin {dest_message_id}: {e}")
        return False


async def populate_thread(app: Client, src_thread_id: int, dest_thread_id: int, stats: dict, debug_fh=None) -> int:
    pending: list[TelegramMessage] = []
    if debug_fh:
        debug_fh.write(f"\n{'=' * 60}\n")
        debug_fh.write(f"THREAD {src_thread_id} → {dest_thread_id}\n")
        debug_fh.write(f"{'=' * 60}\n")
    async for message in app.search_messages(
        SOURCE_GROUP_ID,
        message_thread_id=src_thread_id,
        limit=MESSAGES_PER_THREAD,
    ):
        if debug_fh:
            debug_fh.write(
                f"\n--- msg id={message.id} service={message.service} pinned={getattr(message, 'pinned', None)} ---\n"
            )
            debug_fh.write(repr(message) + "\n")
            debug_fh.flush()

        if message.service is not None:
            # service messages (topic created/renamed, pins, etc.) can't be forwarded
            continue
        pending.append(message)

    if not pending:
        logger.info(f"No messages in source thread {src_thread_id}")
        return 0

    forwarded_origins = await load_forwarded_origins(app, dest_thread_id)
    logger.info(f"Found {len(forwarded_origins)} already-forwarded messages in dest thread {dest_thread_id}")

    forwarded = 0
    pinned_count = 0
    for message in reversed(pending):
        if is_already_in_group_chat(message, forwarded_origins):
            logger.debug(f"Skipping message {message.id} — already in dest thread {dest_thread_id}")
            continue

        new_message = None
        try:
            new_message = await forward_one(app, message.id, dest_thread_id)
            forwarded += 1
        except FloodWait as e:
            stats["floodwait_count"] += 1
            stats["floodwait_total_seconds"] += e.value
            stats["floodwait_max_seconds"] = max(stats["floodwait_max_seconds"], e.value)
            logger.warning(
                f"⚠️  FloodWait #{stats['floodwait_count']}: {e.value}s on message {message.id} "
                f"(thread {src_thread_id}) — sleeping. "
                f"Total wait so far: {stats['floodwait_total_seconds']}s"
            )
            await asyncio.sleep(e.value + 1)
            try:
                new_message = await forward_one(app, message.id, dest_thread_id)
                forwarded += 1
            except FloodWait as e2:
                stats["floodwait_count"] += 1
                stats["floodwait_total_seconds"] += e2.value
                stats["floodwait_max_seconds"] = max(stats["floodwait_max_seconds"], e2.value)
                logger.warning(f"⚠️  FloodWait on retry: {e2.value}s — giving up on message {message.id}")
            except Exception as e2:
                logger.error(f"Retry failed for {message.id}: {e2}")
        except Exception as e:
            stats["other_errors"] += 1
            logger.error(
                f"Failed to forward {message.id} (src thread {src_thread_id} → dest thread {dest_thread_id}): {e}"
            )

        if message.pinned and new_message is not None:
            if await pin_in_destination(app, new_message.id):
                pinned_count += 1
                logger.info(
                    f"📌 Pinned {new_message.id} in dest thread {dest_thread_id} (was pinned in source as {message.id})"
                )

        await asyncio.sleep(SLEEP_BETWEEN_FORWARDS)

    if pinned_count:
        logger.info(f"Pinned {pinned_count} messages in dest thread {dest_thread_id}")

    return forwarded


async def main(debug: bool = False):
    mapping = load_enabled_mapping()
    logger.info(f"Loaded {len(mapping)} enabled thread mappings")

    stats = {
        "floodwait_count": 0,
        "floodwait_total_seconds": 0,
        "floodwait_max_seconds": 0,
        "other_errors": 0,
    }
    total_forwarded = 0

    debug_fh = open(DEBUG_DUMP_PATH, "w", encoding="utf-8") if debug else None
    if debug:
        logger.info(f"Debug mode ON — dumping messages to {DEBUG_DUMP_PATH}")

    try:
        async with Client("session/mi_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING) as app:
            # Warm up the peer cache by walking dialogs first.
            target = None
            async for dialog in app.get_dialogs():
                if dialog.chat.id == SOURCE_GROUP_ID:
                    target = dialog.chat
                    break

            if target is None:
                logger.error(f"Chat {SOURCE_GROUP_ID} not found in dialogs.")
                return

            logger.info(f"Source chat: {target.title}")

            for src_thread_id, dest_thread_id in mapping.items():
                logger.info(f"Populating: src thread {src_thread_id} → dest thread {dest_thread_id}")
                count = await populate_thread(app, src_thread_id, dest_thread_id, stats, debug_fh)
                total_forwarded += count
                logger.info(f"Done: forwarded {count} messages to dest thread {dest_thread_id}")
    finally:
        if debug_fh:
            debug_fh.close()

    logger.info("=" * 60)
    logger.info(f"Summary — forwarded: {total_forwarded}")
    logger.info(f"FloodWait hits: {stats['floodwait_count']}")
    logger.info(f"FloodWait total wait: {stats['floodwait_total_seconds']}s")
    logger.info(f"FloodWait worst wait: {stats['floodwait_max_seconds']}s")
    logger.info(f"Other errors: {stats['other_errors']}")
    if stats["floodwait_count"] > 0:
        logger.warning("⚠️  Got FloodWaits — consider increasing SLEEP_BETWEEN_FORWARDS to reduce ban risk")


parser = argparse.ArgumentParser(description="Populate destination chat threads with forwarded messages")
parser.add_argument(
    "--debug",
    action="store_true",
    help=f"Dump every fetched message's repr to {DEBUG_DUMP_PATH}",
)
args = parser.parse_args()

asyncio.run(main(debug=args.debug))
