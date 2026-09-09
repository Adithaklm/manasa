import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeFilename

from encoder_user import encode_file


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

log = logging.getLogger("telegram-ffmpeg-user")

# =========================
# ENVIRONMENT VARIABLES
# =========================

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]

OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

PORT = int(os.environ.get("PORT", "8080"))

WORK_DIR = Path(
    os.environ.get(
        "WORK_DIR",
        "/tmp/telegram-ffmpeg"
    )
)

WORK_DIR.mkdir(parents=True, exist_ok=True)

# IMPORTANT:
# Create TELEGRAM_SESSION_STRING once locally.
# Then put it in Koyeb Environment Variables.
SESSION_STRING = os.environ.get(
    "TELEGRAM_SESSION_STRING",
    ""
)

# =========================
# TELEGRAM CLIENT
# =========================

if SESSION_STRING:
    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH,
    )
else:
    # First login can create a local session file.
    client = TelegramClient(
        str(WORK_DIR / "ffmpeg_user"),
        API_ID,
        API_HASH,
    )


# =========================
# HEALTH CHECK
# =========================

async def health(request):
    return web.Response(
        text="Telegram FFmpeg User API Bot is running"
    )


async def start_health_server():
    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    log.info(
        "Health server running on port %s",
        PORT
    )


# =========================
# OWNER CHECK
# =========================

def owner_ok(event):
    if OWNER_ID == 0:
        return True

    return event.sender_id == OWNER_ID


# =========================
# START COMMAND
# =========================

@client.on(
    events.NewMessage(
        pattern=r"^/start$"
    )
)
async def start_command(event):

    if not owner_ok(event):
        return

    await event.reply(
        "🎬 FFmpeg User Bot is ready!\n\n"
        "Send me a video file."
    )


# =========================
# HELP COMMAND
# =========================

@client.on(
    events.NewMessage(
        pattern=r"^/help$"
    )
)
async def help_command(event):

    if not owner_ok(event):
        return

    await event.reply(
        "🎬 FFmpeg User Bot\n\n"
        "Send a video file and I will:\n"
        "📥 Download it\n"
        "⚙️ Encode it with FFmpeg\n"
        "📤 Send the encoded file back."
    )


# =========================
# MEDIA HANDLER
# =========================

@client.on(
    events.NewMessage(
        func=lambda event: bool(event.file)
    )
)
async def media_handler(event):

    if not owner_ok(event):
        return

    name = "input"

    try:

        if (
            event.document
            and event.document.attributes
        ):

            for attr in event.document.attributes:

                if isinstance(
                    attr,
                    DocumentAttributeFilename
                ):
                    name = attr.file_name
                    break

    except Exception:
        pass

    suffix = Path(name).suffix

    if not suffix:
        suffix = ".mkv"

    src = (
        WORK_DIR
        / f"input_{event.id}{suffix}"
    )

    output = (
        WORK_DIR
        / f"encoded_{event.id}.mkv"
    )

    try:

        await event.reply(
            "📥 Downloading file..."
        )

        log.info(
            "Downloading %s",
            name
        )

        await client.download_media(
            event.message,
            file=str(src)
        )

        if not src.exists():
            raise RuntimeError(
                "Downloaded file was not created."
            )

        size_gb = (
            src.stat().st_size
            / (1024 ** 3)
        )

        await event.reply(
            f"📦 Download complete\n"
            f"Size: {size_gb:.2f} GB"
        )

        await event.reply(
            "⚙️ Encoding started..."
        )

        log.info(
            "Encoding: %s -> %s",
            src,
            output
        )

        result = await asyncio.to_thread(
            encode_file,
            str(src),
            str(output),
            {}
        )

        if result is False:
            raise RuntimeError(
                "FFmpeg encoding failed."
            )

        if not output.exists():
            raise RuntimeError(
                "FFmpeg output file was not created."
            )

        output_size_gb = (
            output.stat().st_size
            / (1024 ** 3)
        )

        await event.reply(
            "✅ Encoding completed\n"
            f"Output: {output_size_gb:.2f} GB\n\n"
            "📤 Uploading..."
        )

        await client.send_file(
            event.chat_id,
            str(output),
            caption="✅ Encoding complete"
        )

        log.info(
            "Upload completed."
        )

    except Exception as exc:

        log.exception(
            "Encoding job failed"
        )

        try:
            await event.reply(
                f"❌ Error:\n{exc}"
            )
        except Exception:
            pass

    finally:

        try:
            if src.exists():
                src.unlink()
        except Exception:
            pass

        try:
            if output.exists():
                output.unlink()
        except Exception:
            pass


# =========================
# MAIN
# =========================

async def main():

    await start_health_server()

    log.info(
        "Starting Telegram User Client..."
    )

    await client.start()

    me = await client.get_me()

    log.info(
        "Logged in as: %s",
        getattr(me, "username", None)
    )

    log.info(
        "Telegram user ID: %s",
        me.id
    )

    log.info(
        "Bot is ready."
    )

    await client.run_until_disconnected()


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        pass
