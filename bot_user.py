import asyncio
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from aiohttp import web
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename

from encoder_user import encode_file

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("user-telegram-ffmpeg")

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION = os.environ.get("TELEGRAM_SESSION", "ffmpeg_user")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/telegram-ffmpeg"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

PORT = int(os.environ.get("PORT", "8080"))

client = TelegramClient(str(WORK_DIR / SESSION), API_ID, API_HASH)

# First run: the client will ask for phone/code in the Koyeb logs.
# After successful login, the .session file is retained in WORK_DIR.
# Because Koyeb local storage is ephemeral, set TELEGRAM_SESSION_STRING instead
# for production if your platform may restart/recreate the instance.

async def health(request):
    return web.Response(text="OK")

async def start_health():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

def owner_ok(event):
    return not OWNER_ID or event.sender_id == OWNER_ID

@client.on(events.NewMessage(pattern=r"^/start$"))
async def start(event):
    if not owner_ok(event):
        return
    await event.reply(
        "🎬 FFmpeg User Bot is ready.\n\n"
        "Send me a video file and I will download it to Koyeb, "
        "encode it with FFmpeg, and send the result back."
    )

@client.on(events.NewMessage(func=lambda e: bool(e.file)))
async def media(event):
    if not owner_ok(event):
        return

    name = "input"
    for attr in (event.document.attributes if event.document else []):
        if isinstance(attr, DocumentAttributeFilename):
            name = attr.file_name
            break
    suffix = Path(name).suffix or ".mkv"
    src = WORK_DIR / f"input_{event.id}{suffix}"
    out = WORK_DIR / f"encoded_{event.id}.mkv"

    await event.reply("📥 Downloading file to Koyeb...")
    try:
        await client.download_media(event.message, file=str(src))
        await event.reply(f"📦 Downloaded: {src.stat().st_size / (1024**3):.2f} GB")
        await event.reply("⚙️ Encoding with FFmpeg...")

        # Uses the project's encoder.py. Defaults are intentionally conservative.
        result = await asyncio.to_thread(
            encode_file, str(src), str(out), {}
        )

        if result is False:
            raise RuntimeError("FFmpeg encoding failed")

        if not out.exists():
            raise RuntimeError("FFmpeg did not create an output file")

        await event.reply("📤 Uploading encoded file...")
        await client.send_file(event.chat_id, str(out), caption="✅ Encoding complete")
    except Exception as exc:
        log.exception("job failed")
        await event.reply(f"❌ Error: {exc}")
    finally:
        for p in (src, out):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

async def main():
    await start_health()
    await client.start()
    me = await client.get_me()
    log.info("Logged in as %s (%s)", getattr(me, "username", None), me.id)
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
