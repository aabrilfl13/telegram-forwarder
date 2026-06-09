import asyncio
import os

from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]
session_string = os.environ["SESSION_STRING"]
source_group_id = int(os.environ["SOURCE_GROUP_ID"])
dest_group_id = int(os.environ["DEST_GROUP_ID"])


async def print_topics(app: Client, chat_id: int) -> None:
    chat = await app.get_chat(chat_id)
    print(f"chat: {chat.title}  is_forum={chat.is_forum}")
    async for topic in app.get_forum_topics(chat_id):
        print(f"{topic.title:<40} | thread_id: {topic.id}")
    print()


async def main(app: Client) -> None:
    await print_topics(app, source_group_id)
    await print_topics(app, dest_group_id)


app = Client(
    "app/session/mi_session",
    api_id=api_id,
    api_hash=api_hash,
    session_string=session_string,
)


async def run():
    async with app:
        await main(app)


asyncio.run(run())
