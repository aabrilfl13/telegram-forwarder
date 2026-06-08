import os

from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

with Client("app/session/mi_session", api_id=api_id, api_hash=api_hash) as app:
    session_string = app.export_session_string()

print("\nSESSION_STRING:")
print(session_string)
