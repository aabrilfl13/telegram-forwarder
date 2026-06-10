import json
import os
from pathlib import Path

from logger import setup_logging
from pyrogram.types import Message
from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

logger = setup_logging("db")

DB_PATH = Path(__file__).parent / "data" / "messages.db"
_DEFAULT_URL = f"sqlite+aiosqlite:///{DB_PATH}"

_metadata = MetaData()

_chats = Table(
    "chats",
    _metadata,
    Column("id", BigInteger, primary_key=True),
    Column("title", Text),
    Column("type", String(32)),
    Column("username", String(128)),
    Column("updated_at", Text, server_default=func.current_timestamp()),
)

_topics = Table(
    "topics",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chat_id", BigInteger, ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
    Column("telegram_topic_id", Integer, nullable=False),
    Column("name", Text),
    Column("mirror_topic_id", Integer, ForeignKey("topics.id")),
    UniqueConstraint("chat_id", "telegram_topic_id"),
)

_telegram_users = Table(
    "telegram_users",
    _metadata,
    Column("id", BigInteger, primary_key=True),
    Column("username", String(128)),
    Column("first_name", Text),
    Column("last_name", Text),
    Column("is_bot", Integer, nullable=False, server_default="0"),
    Column("photo_small_file_id", Text),
    Column("photo_big_file_id", Text),
    Column("updated_at", Text, server_default=func.current_timestamp()),
)

_messages = Table(
    "messages",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("telegram_id", BigInteger, nullable=False),
    Column("chat_id", BigInteger, ForeignKey("chats.id"), nullable=False),
    Column("topic_id", Integer, ForeignKey("topics.id")),
    Column("user_id", BigInteger, ForeignKey("telegram_users.id")),
    Column("text", Text),
    Column("date", Text, nullable=False),
    Column("reply_to_telegram_id", BigInteger),
    Column("reply_to_top_telegram_id", BigInteger),
    Column("reply_to_user_id", BigInteger, ForeignKey("telegram_users.id")),
    Column("reply_to_text", Text),
    Column("has_media", Integer, nullable=False, server_default="0"),
    Column("media_type", String(32)),
    Column("raw_json", Text),
    Column("created_at", Text, server_default=func.current_timestamp()),
    UniqueConstraint("chat_id", "telegram_id"),
)

_media_files = Table(
    "media_files",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("message_id", Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
    Column("file_id", Text),
    Column("file_unique_id", Text),
    Column("media_type", String(32), nullable=False),
    Column("file_name", Text),
    Column("mime_type", String(128)),
    Column("file_size", Integer),
    Column("width", Integer),
    Column("height", Integer),
    Column("duration", Integer),
    Column("local_path", Text),
    Column("downloaded", Integer, nullable=False, server_default="0"),
    Column("created_at", Text, server_default=func.current_timestamp()),
)

_analyses = Table(
    "analyses",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("message_id", Integer, ForeignKey("messages.id")),
    Column("topic_id", Integer, ForeignKey("topics.id")),
    Column("analysis_type", String(64), nullable=False),
    Column("model", String(64), nullable=False),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("result_json", Text),
    Column("created_at", Text, server_default=func.current_timestamp()),
)


def _insert(engine: AsyncEngine, table: Table):
    """Return a dialect-aware insert that supports on_conflict_do_update."""
    if engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    return insert(table)


async def init_db(url: str | None = None) -> AsyncEngine:
    db_url = url or os.getenv("DATABASE_URL", _DEFAULT_URL)
    engine = create_async_engine(db_url)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _set_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)
        await _migrate(conn)

    logger.info(f"Database ready — {db_url}")
    return engine


async def _migrate(conn: AsyncConnection) -> None:
    """Add columns introduced after the initial schema, safe to run on existing DBs."""
    new_cols = [
        ("telegram_users", "photo_small_file_id", "TEXT"),
        ("telegram_users", "photo_big_file_id", "TEXT"),
        ("messages", "reply_to_top_telegram_id", "INTEGER"),
        ("messages", "reply_to_user_id", "INTEGER"),
        ("messages", "reply_to_text", "TEXT"),
    ]
    for table, col, col_type in new_cols:
        try:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
        except Exception:
            pass  # column already exists


async def _upsert_chat(engine: AsyncEngine, conn: AsyncConnection, chat) -> int:
    type_ = str(chat.type).split(".")[-1].lower() if getattr(chat, "type", None) else None
    ins = _insert(engine, _chats).values(
        id=chat.id,
        title=getattr(chat, "title", None),
        type=type_,
        username=getattr(chat, "username", None),
    )
    await conn.execute(
        ins.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "title": ins.excluded.title,
                "type": ins.excluded.type,
                "username": ins.excluded.username,
                "updated_at": func.current_timestamp(),
            },
        )
    )
    return chat.id


async def _upsert_topic(
    engine: AsyncEngine,
    conn: AsyncConnection,
    chat_id: int,
    telegram_topic_id: int,
    name: str | None = None,
) -> int:
    ins = _insert(engine, _topics).values(
        chat_id=chat_id,
        telegram_topic_id=telegram_topic_id,
        name=name,
    )
    result = await conn.execute(
        ins.on_conflict_do_update(
            index_elements=["chat_id", "telegram_topic_id"],
            set_={"name": func.coalesce(ins.excluded.name, _topics.c.name)},
        ).returning(_topics.c.id)
    )
    return result.scalar_one()


async def _upsert_telegram_user(
    engine: AsyncEngine,
    conn: AsyncConnection,
    telegram_user,
) -> int | None:
    if telegram_user is None:
        return None
    photo = getattr(telegram_user, "photo", None)
    ins = _insert(engine, _telegram_users).values(
        id=telegram_user.id,
        username=getattr(telegram_user, "username", None),
        first_name=getattr(telegram_user, "first_name", None),
        last_name=getattr(telegram_user, "last_name", None),
        is_bot=int(getattr(telegram_user, "is_bot", False)),
        photo_small_file_id=getattr(photo, "small_file_id", None),
        photo_big_file_id=getattr(photo, "big_file_id", None),
    )
    await conn.execute(
        ins.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "username": ins.excluded.username,
                "first_name": ins.excluded.first_name,
                "last_name": ins.excluded.last_name,
                "photo_small_file_id": func.coalesce(
                    ins.excluded.photo_small_file_id, _telegram_users.c.photo_small_file_id
                ),
                "photo_big_file_id": func.coalesce(ins.excluded.photo_big_file_id, _telegram_users.c.photo_big_file_id),
                "updated_at": func.current_timestamp(),
            },
        )
    )
    return telegram_user.id


_MEDIA_ATTRS = [
    ("photo", "photo"),
    ("video", "video"),
    ("document", "document"),
    ("audio", "audio"),
    ("voice", "voice"),
    ("sticker", "sticker"),
    ("animation", "animation"),
    ("video_note", "video_note"),
]


class _NS:
    """Recursively wraps a dict so nested keys are accessible as attributes.
    Lets save_message() work on both live Pyrogram Message objects and
    plain dicts deserialized from Redis."""

    def __init__(self, d: dict):
        for k, v in d.items():
            setattr(self, k, _NS(v) if isinstance(v, dict) else v)


def _to_msg(source) -> "_NS | Message":
    return _NS(source) if isinstance(source, dict) else source


def _extract_media(message: Message) -> tuple[str | None, object | None]:
    for attr, media_type in _MEDIA_ATTRS:
        obj = getattr(message, attr, None)
        if obj is not None:
            return media_type, obj
    return None, None


async def link_mirror_topics(
    engine: AsyncEngine,
    src_chat_id: int,
    src_telegram_topic_id: int,
    dest_chat_id: int,
    dest_telegram_topic_id: int,
) -> None:
    """Set mirror_topic_id on both source and destination topic rows."""
    async with engine.begin() as conn:
        src_id = await _upsert_topic(engine, conn, src_chat_id, src_telegram_topic_id)
        dest_id = await _upsert_topic(engine, conn, dest_chat_id, dest_telegram_topic_id)
        await conn.execute(_topics.update().where(_topics.c.id == src_id).values(mirror_topic_id=dest_id))
        await conn.execute(_topics.update().where(_topics.c.id == dest_id).values(mirror_topic_id=src_id))


def _to_raw_json(source) -> str | None:
    """Serialize a Pyrogram Message or plain dict to a JSON string for raw_json storage."""
    try:
        if isinstance(source, dict):
            return json.dumps(source, default=str)
        return str(source)  # Pyrogram Object.__str__ returns JSON via Object.default
    except Exception:
        return None


async def save_message(engine: AsyncEngine, message) -> int:
    """Persist a Telegram message and its media. Accepts a Pyrogram Message or a
    plain dict (e.g. deserialized from Redis). Returns the messages.id row id."""
    raw_json = _to_raw_json(message)
    message = _to_msg(message)
    media_type, media_obj = _extract_media(message)
    msg_text = getattr(message, "text", None) or getattr(message, "caption", None)
    date = getattr(message, "date", None)
    date_str = date.isoformat() if hasattr(date, "isoformat") else (str(date) if date else None)

    async with engine.begin() as conn:
        await _upsert_chat(engine, conn, message.chat)
        topic_row_id = await _upsert_topic(engine, conn, message.chat.id, message.message_thread_id or 1)
        telegram_user_row_id = await _upsert_telegram_user(engine, conn, message.from_user)

        reply_msg = getattr(message, "reply_to_message", None)
        reply_to_top_telegram_id = getattr(message, "reply_to_top_message_id", None)
        reply_to_user_row_id = None
        reply_to_text = None
        if reply_msg is not None:
            reply_to_user_row_id = await _upsert_telegram_user(engine, conn, getattr(reply_msg, "from_user", None))
            reply_to_text = getattr(reply_msg, "text", None) or getattr(reply_msg, "caption", None)

        ins = _insert(engine, _messages).values(
            telegram_id=message.id,
            chat_id=message.chat.id,
            topic_id=topic_row_id,
            user_id=telegram_user_row_id,
            text=msg_text,
            date=date_str,
            reply_to_telegram_id=message.reply_to_message_id,
            reply_to_top_telegram_id=reply_to_top_telegram_id,
            reply_to_user_id=reply_to_user_row_id,
            reply_to_text=reply_to_text,
            has_media=int(media_obj is not None),
            media_type=media_type,
            raw_json=raw_json,
        )
        result = await conn.execute(
            ins.on_conflict_do_update(
                index_elements=["chat_id", "telegram_id"],
                set_={
                    "text": func.coalesce(ins.excluded.text, _messages.c.text),
                    "user_id": func.coalesce(ins.excluded.user_id, _messages.c.user_id),
                    "reply_to_user_id": func.coalesce(ins.excluded.reply_to_user_id, _messages.c.reply_to_user_id),
                    "reply_to_text": func.coalesce(ins.excluded.reply_to_text, _messages.c.reply_to_text),
                    "has_media": ins.excluded.has_media,
                    "media_type": func.coalesce(ins.excluded.media_type, _messages.c.media_type),
                },
            ).returning(_messages.c.id)
        )
        message_row_id = result.scalar_one()

        if media_obj is not None:
            exists = await conn.execute(select(_media_files.c.id).where(_media_files.c.message_id == message_row_id))
            if exists.fetchone() is None:
                await conn.execute(
                    _media_files.insert().values(
                        message_id=message_row_id,
                        file_id=getattr(media_obj, "file_id", None),
                        file_unique_id=getattr(media_obj, "file_unique_id", None),
                        media_type=media_type,
                        file_name=getattr(media_obj, "file_name", None),
                        mime_type=getattr(media_obj, "mime_type", None),
                        file_size=getattr(media_obj, "file_size", None),
                        width=getattr(media_obj, "width", None),
                        height=getattr(media_obj, "height", None),
                        duration=getattr(media_obj, "duration", None),
                    )
                )

    logger.debug(
        f"Saved message telegram_id={message.id} chat={message.chat.id} "
        f"topic={message.message_thread_id or 1} media={media_type or 'none'} → row_id={message_row_id}"
    )
    return message_row_id
