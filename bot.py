import asyncio
import logging
import os
import time
from pathlib import Path

from aiohttp import web
from telethon import TelegramClient, events, Button
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
    client = TelegramClient(
        str(WORK_DIR / "ffmpeg_user"),
        API_ID,
        API_HASH,
    )


# =========================
# DOWNLOAD JOBS
# =========================

# event.id -> download information
downloads = {}


# =========================
# HELPERS
# =========================

def owner_ok(event):
    if OWNER_ID == 0:
        return True

    return event.sender_id == OWNER_ID


def format_bytes(value):
    if value is None:
        return "0 B"

    value = float(value)

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
    ]

    for unit in units:
        if value < 1024:
            return f"{value:.1f} {unit}"

        value /= 1024

    return f"{value:.1f} PB"


def format_time(seconds):
    if seconds is None or seconds < 0:
        return "--"

    seconds = int(seconds)

    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    if days:
        return f"{days}d {hours}h"

    if hours:
        return f"{hours}h {minutes}m"

    if minutes:
        return f"{minutes}m {seconds}s"

    return f"{seconds}s"


def make_progress_text(job):
    current = job["current"]
    total = job["total"]
    start_time = job["start_time"]

    if total:
        percentage = (current / total) * 100
    else:
        percentage = 0

    elapsed = time.monotonic() - start_time

    if elapsed > 0:
        speed = current / elapsed
    else:
        speed = 0

    if speed > 0 and total:
        remaining = total - current
        eta = remaining / speed
    else:
        eta = None

    return (
        f"⬇️ **Downloading**\n\n"
        f"📄 `{job['name']}`\n\n"
        f"📦 **Downloaded:** "
        f"{format_bytes(current)} / {format_bytes(total)}\n"
        f"📊 **Progress:** {percentage:.1f}%\n"
        f"🚀 **Speed:** {format_bytes(speed)}/s\n"
        f"⏱ **ETA:** {format_time(eta)}"
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
        "📥 Download it with live progress\n"
        "⚙️ Encode it with FFmpeg\n"
        "📤 Send the encoded file back.\n\n"
        "During download you can cancel the job."
    )


# =========================
# DOWNLOAD PROGRESS BUTTON
# =========================

@client.on(
    events.CallbackQuery(
        pattern=b"download_progress:"
    )
)
async def progress_button(event):

    if OWNER_ID != 0 and event.sender_id != OWNER_ID:
        await event.answer(
            "❌ You are not allowed to use this.",
            alert=True
        )
        return

    try:
        job_id = int(
            event.data.decode().split(":")[1]
        )

    except Exception:
        await event.answer(
            "❌ Invalid download.",
            alert=True
        )
        return

    job = downloads.get(job_id)

    if not job:
        await event.answer(
            "ℹ️ Download is no longer active.",
            alert=True
        )
        return

    try:
        await event.edit(
            make_progress_text(job),
            buttons=[
                [
                    Button.inline(
                        "📊 Download Progress",
                        data=f"download_progress:{job_id}"
                    ),
                    Button.inline(
                        "❌ Cancel Download",
                        data=f"cancel_download:{job_id}"
                    ),
                ]
            ]
        )

        await event.answer(
            "Progress refreshed."
        )

    except Exception as exc:

        log.warning(
            "Progress button error: %s",
            exc
        )


# =========================
# CANCEL DOWNLOAD BUTTON
# =========================

@client.on(
    events.CallbackQuery(
        pattern=b"cancel_download:"
    )
)
async def cancel_download(event):

    if OWNER_ID != 0 and event.sender_id != OWNER_ID:
        await event.answer(
            "❌ You are not allowed to use this.",
            alert=True
        )
        return

    try:
        job_id = int(
            event.data.decode().split(":")[1]
        )

    except Exception:
        await event.answer(
            "❌ Invalid download.",
            alert=True
        )
        return

    job = downloads.get(job_id)

    if not job:
        await event.answer(
            "ℹ️ Download already finished or stopped.",
            alert=True
        )
        return

    job["cancelled"] = True

    task = job.get("task")

    if task and not task.done():

        log.info(
            "Cancelling download job %s",
            job_id
        )

        task.cancel()

    try:

        await event.edit(
            "❌ **Download cancelled.**\n\n"
            f"📄 `{job['name']}`\n\n"
            "🧹 Removing partial file..."
        )

        await event.answer(
            "Download cancelled."
        )

    except Exception as exc:

        log.warning(
            "Cancel button error: %s",
            exc
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

    progress_message = None

    job = {
        "name": name,
        "current": 0,
        "total": event.file.size or 0,
        "start_time": time.monotonic(),
        "cancelled": False,
        "task": None,
        "message": None,
    }

    downloads[event.id] = job

    try:

        # =========================
        # INITIAL DOWNLOAD MESSAGE
        # =========================

        progress_message = await event.reply(
            make_progress_text(job),
            buttons=[
                [
                    Button.inline(
                        "📊 Download Progress",
                        data=f"download_progress:{event.id}"
                    ),
                    Button.inline(
                        "❌ Cancel Download",
                        data=f"cancel_download:{event.id}"
                    ),
                ]
            ]
        )

        job["message"] = progress_message

        log.info(
            "Downloading %s",
            name
        )

        # =========================
        # PROGRESS CALLBACK
        # =========================

        last_update = {
            "time": 0
        }

        async def progress_callback(current, total):

            job["current"] = current
            job["total"] = total

            now = time.monotonic()

            # Update Telegram message roughly every 2 seconds
            if now - last_update["time"] < 2:
                return

            last_update["time"] = now

            try:

                await progress_message.edit(
                    make_progress_text(job),
                    buttons=[
                        [
                            Button.inline(
                                "📊 Download Progress",
                                data=f"download_progress:{event.id}"
                            ),
                            Button.inline(
                                "❌ Cancel Download",
                                data=f"cancel_download:{event.id}"
                            ),
                        ]
                    ]
                )

            except Exception as exc:

                log.debug(
                    "Progress update failed: %s",
                    exc
                )

        # =========================
        # DOWNLOAD TASK
        # =========================

        async def do_download():

            await client.download_media(
                event.message,
                file=str(src),
                progress_callback=progress_callback
            )

        download_task = asyncio.create_task(
            do_download()
        )

        job["task"] = download_task

        try:

            await download_task

        except asyncio.CancelledError:

            job["cancelled"] = True

            log.info(
                "Download cancelled: %s",
                name
            )

            raise

        # =========================
        # CHECK CANCELLED
        # =========================

        if job["cancelled"]:

            raise asyncio.CancelledError

        # =========================
        # DOWNLOAD COMPLETE
        # =========================

        job["current"] = job["total"]

        try:

            await progress_message.edit(
                "✅ **Download completed**\n\n"
                f"📄 `{name}`\n"
                f"📦 Size: **{format_bytes(job['total'])}**",
                buttons=None
            )

        except Exception:
            pass

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

        # =========================
        # ENCODING
        # =========================

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

        # =========================
        # UPLOAD
        # =========================

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

    except asyncio.CancelledError:

        # =========================
        # CANCELLED
        # =========================

        log.info(
            "Download job cancelled: %s",
            name
        )

        try:

            if progress_message:

                await progress_message.edit(
                    "❌ **Download cancelled.**\n\n"
                    f"📄 `{name}`\n\n"
                    "🧹 Partial file removed.",
                    buttons=None
                )

        except Exception:
            pass

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

        # =========================
        # CLEANUP
        # =========================

        downloads.pop(event.id, None)

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


# =========================
# RUN
# =========================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:
        pass
