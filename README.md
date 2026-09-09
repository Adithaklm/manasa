# Telegram FFmpeg Movie Encoder — No Docker

A Python Telegram bot that downloads a video, encodes it with FFmpeg, and uploads the result.

## Features

- H.264 / H.265
- Adjustable CRF 0–51
- Presets from ultrafast to veryslow
- Resolution and FPS controls
- Video bitrate / maxrate / bufsize
- 8-bit / 10-bit pixel formats
- Profile / level / GOP
- Color metadata
- Audio copy / AAC / Opus / FLAC
- Subtitle copy / remove
- MKV / MP4
- Custom FFmpeg parameters
- Progress updates
- Automatic temporary-file cleanup
- No Dockerfile

## Important Telegram limitation

A normal Telegram Bot API server does **not** allow a bot to download multi-GB files. This project therefore has two modes:

1. Normal Telegram Bot API — useful for small tests.
2. Telegram Local Bot API Server — required for your 4 GB input requirement.

The Local Bot API Server also has a 2 GB upload limit, so this project defaults to a 1.9 GB output ceiling.

## Environment variables

Required:

```text
BOT_TOKEN=123456:ABC...
```

Recommended:

```text
OWNER_ID=123456789
MAX_INPUT_BYTES=4294967296
MAX_OUTPUT_BYTES=1992294400
WORK_DIR=/tmp/telegram-ffmpeg
```

For a Local Bot API Server:

```text
TELEGRAM_BASE_URL=http://YOUR_LOCAL_API_HOST:8081/bot
TELEGRAM_FILE_BASE_URL=http://YOUR_LOCAL_API_HOST:8081/file/bot
```

Do not put your bot token in GitHub.

## Local test

Create a virtual environment:

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Install:

```bash
pip install -r requirements.txt
```

Set the token:

Windows PowerShell:

```powershell
$env:BOT_TOKEN="YOUR_TOKEN"
$env:OWNER_ID="YOUR_TELEGRAM_ID"
```

Run:

```bash
python bot.py
```

## GitHub

Create a new repository and upload:

```text
bot.py
encoder.py
requirements.txt
Procfile
runtime.txt
build.sh
.gitignore
README.md
```

## Koyeb without Docker

Create a Web Service from your GitHub repository.

Use the Python/buildpack-style deployment available in your Koyeb account.

Set:

**Build command**

```bash
./build.sh
```

**Run command**

```bash
python bot.py
```

Add the environment variables in Koyeb.

### Important resource requirement

Encoding a 4 GB movie needs significantly more than 4 GB of temporary disk because input and output can coexist. A small/free instance is not appropriate for this workload.

Use a service with enough CPU, RAM and ephemeral disk. Encoding speed is primarily CPU-dependent unless you have a supported hardware encoder.

## Security

The custom FFmpeg argument field is intentionally restricted. Do not expose the bot publicly without authentication/rate limiting if you add more powerful shell-like features.

## Legal note

Only encode/upload media you have the right to process and distribute.
