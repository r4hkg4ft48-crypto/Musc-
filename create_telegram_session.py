"""One-time helper: creates TELEGRAM_SESSION. Never commit the produced value."""
import asyncio
from getpass import getpass
from telethon import TelegramClient
from telethon.sessions import StringSession

async def main():
    print("Volna — Telegram StringSession")
    api_id=int(input("TELEGRAM_API_ID: ").strip())
    api_hash=getpass("TELEGRAM_API_HASH (hidden): ").strip()
    phone=input("Telegram phone (+7...): ").strip()
    client=TelegramClient(StringSession(),api_id,api_hash)
    await client.connect()
    try:
        sent=await client.send_code_request(phone)
        code=input("Code from Telegram: ").strip().replace(" ","")
        try:
            await client.sign_in(phone=phone,code=code,phone_code_hash=sent.phone_code_hash)
        except Exception as exc:
            if "password" not in exc.__class__.__name__.lower(): raise
            await client.sign_in(password=getpass("Telegram 2FA password: "))
        print("\nTELEGRAM_SESSION=")
        print(client.session.save())
        print("\nPut this into the backend secret TELEGRAM_SESSION. Do not publish it.")
    finally:
        await client.disconnect()

if __name__=="__main__":
    asyncio.run(main())
