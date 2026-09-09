import asyncio
import json
import os
import re
import shlex
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from encoder import encode_video, ffprobe_json, format_bytes, format_duration

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
MAX_INPUT_BYTES = int(os.environ.get("MAX_INPUT_BYTES", str(4 * 1024**3)))
MAX_OUTPUT_BYTES = int(os.environ.get("MAX_OUTPUT_BYTES", str(1900 * 1024**2)))

WORK_DIR = Path(os.environ.get("WORK_DIR", "/tmp/telegram-ffmpeg"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

# For normal Telegram Bot API use:
#   TELEGRAM_BASE_URL=https://api.telegram.org/bot
#   TELEGRAM_FILE_BASE_URL=https://api.telegram.org/file/bot
#
# For a self-hosted Local Bot API server, set these to that server's bot/file endpoints.
BASE_URL = os.environ.get("TELEGRAM_BASE_URL")
FILE_BASE_URL = os.environ.get("TELEGRAM_FILE_BASE_URL")

sessions = {}
jobs = {}
job_lock = asyncio.Lock()

DEFAULTS = {
    "codec": "libx265",
    "crf": 24,
    "preset": "medium",
    "resolution": "original",
    "fps": "original",
    "bitrate": "",
    "maxrate": "",
    "bufsize": "",
    "pix_fmt": "yuv420p",
    "profile": "",
    "level": "",
    "gop": "",
    "audio": "copy",
    "audio_bitrate": "192k",
    "audio_channels": "original",
    "audio_samplerate": "original",
    "subtitles": "copy",
    "container": "mkv",
    "tune": "",
    "colorspace": "",
    "color_primaries": "",
    "color_trc": "",
    "color_range": "",
    "extra": "",
}

def allowed(update: Update) -> bool:
    if OWNER_ID == 0:
        return True
    user = update.effective_user
    return bool(user and user.id == OWNER_ID)

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎞 Codec: H.265", callback_data="codec")],
        [InlineKeyboardButton("🎯 CRF: 24", callback_data="crf"),
         InlineKeyboardButton("⚡ Preset: medium", callback_data="preset")],
        [InlineKeyboardButton("📐 Resolution", callback_data="resolution"),
         InlineKeyboardButton("🎬 FPS", callback_data="fps")],
        [InlineKeyboardButton("📦 Bitrate", callback_data="bitrate"),
         InlineKeyboardButton("🎨 Color / 10-bit", callback_data="color")],
        [InlineKeyboardButton("🔊 Audio", callback_data="audio"),
         InlineKeyboardButton("💬 Subtitles", callback_data="subs")],
        [InlineKeyboardButton("🗃 Container", callback_data="container")],
        [InlineKeyboardButton("🧩 Advanced FFmpeg", callback_data="extra")],
        [InlineKeyboardButton("▶️ START ENCODING", callback_data="start"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])

def settings_text(s):
    return (
        "🎬 *FFmpeg Encoder Settings*\n\n"
        f"Codec: `{s['codec']}`\n"
        f"CRF: `{s['crf']}`\n"
        f"Preset: `{s['preset']}`\n"
        f"Resolution: `{s['resolution']}`\n"
        f"FPS: `{s['fps']}`\n"
        f"Bitrate: `{s['bitrate'] or 'auto'}`\n"
        f"Maxrate: `{s['maxrate'] or 'auto'}`\n"
        f"Bufsize: `{s['bufsize'] or 'auto'}`\n"
        f"Pixel format: `{s['pix_fmt']}`\n"
        f"Profile: `{s['profile'] or 'auto'}`\n"
        f"Level: `{s['level'] or 'auto'}`\n"
        f"Audio: `{s['audio']}` {s['audio_bitrate'] if s['audio'] != 'copy' else ''}\n"
        f"Subtitles: `{s['subtitles']}`\n"
        f"Container: `{s['container']}`\n"
        f"Color: `{s['colorspace'] or 'auto'} / {s['color_primaries'] or 'auto'} / {s['color_trc'] or 'auto'}`\n"
        f"Extra params: `{s['extra'] or 'none'}`\n\n"
        "Upload a video first, then adjust these settings."
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "🎬 *Telegram FFmpeg Movie Encoder*\n\n"
        "Send an MKV/MP4/MOV video. Then choose codec, CRF, bitrate, "
        "10-bit/color, audio, subtitles and advanced FFmpeg options.",
        parse_mode="Markdown",
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    sessions[update.effective_user.id] = {"settings": DEFAULTS.copy(), "input": None}
    await update.message.reply_text("♻️ Settings reset.")

async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    msg = update.message
    media = msg.document or msg.video
    size = media.file_size or 0
    if size > MAX_INPUT_BYTES:
        await msg.reply_text(f"❌ File is {format_bytes(size)}. Maximum is {format_bytes(MAX_INPUT_BYTES)}.")
        return

    user_id = update.effective_user.id
    session_dir = WORK_DIR / str(user_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    name = getattr(media, "file_name", None) or "input.mkv"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    input_path = session_dir / f"input_{int(time.time())}_{safe}"

    await msg.reply_text(f"⬇️ Downloading `{safe}`...\nSize: `{format_bytes(size)}`", parse_mode="Markdown")
    tg_file = await media.get_file()
    await tg_file.download_to_drive(custom_path=input_path)

    probe = await ffprobe_json(input_path)
    sessions[user_id] = {
        "settings": DEFAULTS.copy(),
        "input": str(input_path),
        "name": safe,
        "probe": probe,
    }

    duration = float(probe.get("format", {}).get("duration", 0) or 0)
    await msg.reply_text(
        f"✅ Video ready\n\n"
        f"📁 `{safe}`\n"
        f"📦 `{format_bytes(size)}`\n"
        f"⏱ `{format_duration(duration)}`\n\n"
        + settings_text(sessions[user_id]["settings"]),
        parse_mode="Markdown",
        reply_markup=kb_main(),
    )

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not allowed(update):
        return
    uid = q.from_user.id
    s = sessions.get(uid)
    if not s or not s.get("input"):
        await q.edit_message_text("Upload a video first.")
        return

    action = q.data
    settings = s["settings"]

    if action == "codec":
        await q.edit_message_text("Choose codec:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("H.264 / x264", callback_data="set_codec:libx264"),
             InlineKeyboardButton("H.265 / x265", callback_data="set_codec:libx265")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]))
    elif action == "crf":
        await q.edit_message_text("Choose CRF:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(str(x), callback_data=f"set_crf:{x}") for x in (18, 20, 22)],
            [InlineKeyboardButton(str(x), callback_data=f"set_crf:{x}") for x in (24, 26, 28)],
            [InlineKeyboardButton("✍️ Custom CRF", callback_data="ask_crf"),
             InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]))
    elif action == "preset":
        vals = ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"]
        await q.edit_message_text("Choose preset:", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(v, callback_data=f"set_preset:{v}") for v in vals[i:i+3]] for i in range(0, len(vals), 3)]
            + [[InlineKeyboardButton("⬅️ Back", callback_data="back")]]
        ))
    elif action == "resolution":
        await q.edit_message_text("Choose resolution:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Original", callback_data="set_resolution:original"),
             InlineKeyboardButton("2160p", callback_data="set_resolution:2160")],
            [InlineKeyboardButton("1080p", callback_data="set_resolution:1080"),
             InlineKeyboardButton("720p", callback_data="set_resolution:720")],
            [InlineKeyboardButton("480p", callback_data="set_resolution:480"),
             InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]))
    elif action == "fps":
        await q.edit_message_text("Choose FPS:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Original", callback_data="set_fps:original"),
             InlineKeyboardButton("23.976", callback_data="set_fps:24000/1001")],
            [InlineKeyboardButton("24", callback_data="set_fps:24"),
             InlineKeyboardButton("25", callback_data="set_fps:25"),
             InlineKeyboardButton("30", callback_data="set_fps:30")],
            [InlineKeyboardButton("50", callback_data="set_fps:50"),
             InlineKeyboardButton("60", callback_data="set_fps:60")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]))
    elif action == "bitrate":
        await q.edit_message_text(
            "Send a message with bitrate settings, e.g.\n\n"
            "`bitrate=5M maxrate=7M bufsize=14M`\n\n"
            "Use `bitrate=auto` to clear them.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
        context.user_data["awaiting"] = "bitrate"
    elif action == "color":
        await q.edit_message_text("Choose pixel format:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("8-bit 4:2:0", callback_data="set_pix:yuv420p"),
             InlineKeyboardButton("10-bit 4:2:0", callback_data="set_pix:yuv420p10le")],
            [InlineKeyboardButton("8-bit 4:2:2", callback_data="set_pix:yuv422p"),
             InlineKeyboardButton("10-bit 4:2:2", callback_data="set_pix:yuv422p10le")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]))
    elif action == "audio":
        await q.edit_message_text("Audio:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Copy", callback_data="set_audio:copy"),
             InlineKeyboardButton("AAC 192k", callback_data="set_audio:aac")],
            [InlineKeyboardButton("Opus 160k", callback_data="set_audio:opus"),
             InlineKeyboardButton("FLAC", callback_data="set_audio:flac")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]))
    elif action == "subs":
        await q.edit_message_text("Subtitles:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Copy", callback_data="set_subs:copy"),
             InlineKeyboardButton("Remove", callback_data="set_subs:remove")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]))
    elif action == "container":
        await q.edit_message_text("Container:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("MKV", callback_data="set_container:mkv"),
             InlineKeyboardButton("MP4", callback_data="set_container:mp4")],
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]))
    elif action == "extra":
        await q.edit_message_text(
            "Send extra FFmpeg arguments as a single line.\n\n"
            "Example:\n`-x265-params aq-mode=3:strong-intra-smoothing=0`\n\n"
            "Only options you understand should be used.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back")]])
        )
        context.user_data["awaiting"] = "extra"
    elif action == "ask_crf":
        await q.edit_message_text("Send CRF as a number from 0 to 51.")
        context.user_data["awaiting"] = "crf"
    elif action.startswith("set_"):
        key, value = action[4:].split(":", 1)
        if key == "codec":
            settings["codec"] = value
        elif key == "crf":
            settings["crf"] = int(value)
        elif key == "preset":
            settings["preset"] = value
        elif key == "resolution":
            settings["resolution"] = value
        elif key == "fps":
            settings["fps"] = value
        elif key == "pix":
            settings["pix_fmt"] = value
        elif key == "audio":
            settings["audio"] = value
        elif key == "subs":
            settings["subtitles"] = value
        elif key == "container":
            settings["container"] = value
        await q.edit_message_text(settings_text(settings), parse_mode="Markdown", reply_markup=kb_main())
    elif action == "back":
        await q.edit_message_text(settings_text(settings), parse_mode="Markdown", reply_markup=kb_main())
    elif action == "cancel":
        job = jobs.get(uid)
        if job and not job.done():
            job.cancel()
            await q.edit_message_text("🛑 Encoding cancelled.")
        else:
            await q.edit_message_text("No active encoding job.")
    elif action == "start":
        if uid in jobs and not jobs[uid].done():
            await q.edit_message_text("⚠️ You already have an encoding job running.")
            return
        jobs[uid] = asyncio.create_task(run_job(q, context, uid))

async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return
    text = update.message.text.strip()
    uid = update.effective_user.id
    s = sessions.get(uid)
    if not s:
        return
    try:
        if awaiting == "crf":
            value = int(text)
            if not 0 <= value <= 51:
                raise ValueError
            s["settings"]["crf"] = value
        elif awaiting == "extra":
            # Stored as text; encoder validates shell-like tokens with shlex.
            s["settings"]["extra"] = text
        elif awaiting == "bitrate":
            parts = dict(item.split("=", 1) for item in text.split() if "=" in item)
            for key in ("bitrate", "maxrate", "bufsize"):
                value = parts.get(key)
                if value is not None:
                    s["settings"][key] = "" if value.lower() == "auto" else value
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(settings_text(s["settings"]), parse_mode="Markdown", reply_markup=kb_main())
    except Exception:
        await update.message.reply_text("❌ Invalid value. Please try again.")

async def run_job(query, context, uid):
    s = sessions[uid]
    inp = Path(s["input"])
    settings = s["settings"].copy()
    out = inp.parent / f"encoded_{int(time.time())}.{settings['container']}"

    status = await query.message.reply_text("⏳ Starting FFmpeg...")
    last = 0

    async def progress(pct, elapsed, speed):
        nonlocal last
        now = time.time()
        if now - last < 3 and pct < 100:
            return
        last = now
        bar_n = 20
        filled = int(pct / 100 * bar_n)
        bar = "█" * filled + "░" * (bar_n - filled)
        try:
            await status.edit_text(
                f"🎬 *Encoding...*\n\n`{bar}` {pct:.1f}%\n"
                f"⏱ {format_duration(elapsed)}\n⚡ {speed:.2f}x",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    try:
        await encode_video(inp, out, settings, progress)
        if out.stat().st_size > MAX_OUTPUT_BYTES:
            await status.edit_text(
                f"❌ Output is {format_bytes(out.stat().st_size)}, above the configured "
                f"limit of {format_bytes(MAX_OUTPUT_BYTES)}."
            )
            return

        await status.edit_text("📤 Uploading encoded file...")
        await query.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
        await query.message.reply_document(
            document=out,
            caption=(
                f"✅ Encoding complete\n"
                f"📦 Output: {format_bytes(out.stat().st_size)}\n"
                f"🎯 CRF: {settings['crf']}\n"
                f"🎞 Codec: {settings['codec']}"
            ),
            read_timeout=3600,
            write_timeout=3600,
            connect_timeout=60,
            pool_timeout=60,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await status.edit_text(f"❌ Encoding failed:\n`{str(e)[:3500]}`", parse_mode="Markdown")
    finally:
        for p in (inp, out):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        sessions.pop(uid, None)
        jobs.pop(uid, None)

def build_app():
    builder = Application.builder().token(BOT_TOKEN)
    if BASE_URL:
        builder = builder.base_url(BASE_URL)
    if FILE_BASE_URL:
        builder = builder.base_file_url(FILE_BASE_URL)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, on_video))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input))
    return app

if __name__ == "__main__":
    build_app().run_polling(allowed_updates=Update.ALL_TYPES)
