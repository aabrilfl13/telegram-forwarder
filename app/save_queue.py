import asyncio
import json
import os

import redis.asyncio as aioredis
from db import save_message
from logger import setup_logging

QUEUE_KEY = "telegram:save_queue"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

logger = setup_logging("save_queue")


async def connect_redis() -> aioredis.Redis:
    return aioredis.from_url(REDIS_URL, decode_responses=True)


async def enqueue(r: aioredis.Redis, message) -> None:
    """Serialize a Pyrogram message and push it to the Redis save queue."""
    try:
        await r.rpush(QUEUE_KEY, str(message))
    except Exception as exc:
        logger.error(f"Redis enqueue failed for message {getattr(message, 'id', '?')}: {exc}")


async def db_worker(r: aioredis.Redis, db, stop: asyncio.Event) -> None:
    """Single consumer — pops messages from Redis and saves to DB one at a time."""
    logger.info("DB worker started")
    while not stop.is_set():
        result = await r.blpop(QUEUE_KEY, timeout=1)
        if result is None:
            continue
        _, raw = result
        try:
            data = json.loads(raw)
            await save_message(db, data)
        except Exception as exc:
            logger.error(f"DB save failed: {exc}")
    logger.info("DB worker stopped")


async def drain_and_stop(r: aioredis.Redis, stop: asyncio.Event, worker: asyncio.Task) -> None:
    """Wait for all enqueued messages to be saved, then stop the worker."""
    pending = await r.llen(QUEUE_KEY)
    if pending:
        logger.info(f"Draining {pending} remaining messages from queue before exit…")
    while await r.llen(QUEUE_KEY) > 0:
        await asyncio.sleep(0.1)
    stop.set()
    await worker
