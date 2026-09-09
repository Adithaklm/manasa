import asyncio
import json
import re
import shlex
import time
from pathlib import Path

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FFPROBE = str(Path(FFMPEG).with_name("ffprobe"))

# imageio-ffmpeg does not always ship ffprobe. In that case we use ffmpeg's
# stream info fallback. For best metadata parsing, install a system ffprobe
# or point FFPROBE_PATH to one.
import os
FFPROBE = os.environ.get("FFPROBE_PATH", FFPROBE)

def format_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"

def format_duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

async def ffprobe_json(path: Path):
    cmd = [FFMPEG, "-hide_banner", "-i", str(path), "-f", "null", "-"]
    # Minimal fallback metadata. Duration is extracted from stderr.
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    text = err.decode("utf-8", "ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", text)
    duration = 0
    if m:
        duration = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
    return {"format": {"duration": duration}}

def validate_rate(value):
    if not value:
        return
    if not re.fullmatch(r"\d+(?:\.\d+)?[kKmMgG]?", value):
        raise ValueError(f"Invalid bitrate: {value}")

def validate(settings):
    codec = settings["codec"]
    if codec not in ("libx264", "libx265"):
        raise ValueError("Unsupported video codec")
    crf = int(settings["crf"])
    if not 0 <= crf <= 51:
        raise ValueError("CRF must be 0..51")
    if settings["preset"] not in {
        "ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"
    }:
        raise ValueError("Invalid preset")
    for k in ("bitrate","maxrate","bufsize"):
        validate_rate(settings[k])
    if settings["container"] not in ("mkv", "mp4"):
        raise ValueError("Invalid container")
    if settings["audio"] not in ("copy","aac","opus","flac"):
        raise ValueError("Invalid audio mode")
    if settings["subtitles"] not in ("copy","remove"):
        raise ValueError("Invalid subtitle mode")

def build_command(inp: Path, out: Path, s: dict):
    validate(s)
    cmd = [FFMPEG, "-hide_banner", "-y", "-i", str(inp)]
    cmd += ["-map", "0:v:0"]
    cmd += ["-map", "0:a?"]
    if s["subtitles"] == "copy":
        cmd += ["-map", "0:s?"]

    cmd += ["-c:v", s["codec"], "-preset", s["preset"], "-crf", str(s["crf"])]

    if s["resolution"] != "original":
        height = int(s["resolution"])
        cmd += ["-vf", f"scale=-2:{height}"]

    if s["fps"] != "original":
        cmd += ["-r", s["fps"]]

    cmd += ["-pix_fmt", s["pix_fmt"]]

    for key, flag in (("bitrate","-b:v"), ("maxrate","-maxrate"), ("bufsize","-bufsize")):
        if s[key]:
            cmd += [flag, s[key]]

    if s["profile"]:
        cmd += ["-profile:v", s["profile"]]
    if s["level"]:
        cmd += ["-level:v", s["level"]]
    if s["gop"]:
        cmd += ["-g", str(s["gop"])]
    if s["tune"]:
        cmd += ["-tune", s["tune"]]

    # Color metadata. These do not magically convert SDR to HDR; they label
    # the stream and should only be used when they match the source/transform.
    for key, flag in (
        ("colorspace","-colorspace"),
        ("color_primaries","-color_primaries"),
        ("color_trc","-color_trc"),
        ("color_range","-color_range"),
    ):
        if s[key]:
            cmd += [flag, s[key]]

    if s["audio"] == "copy":
        cmd += ["-c:a", "copy"]
    elif s["audio"] == "aac":
        cmd += ["-c:a", "aac", "-b:a", s["audio_bitrate"]]
    elif s["audio"] == "opus":
        cmd += ["-c:a", "libopus", "-b:a", s["audio_bitrate"]]
    elif s["audio"] == "flac":
        cmd += ["-c:a", "flac"]

    if s["audio_channels"] != "original":
        cmd += ["-ac", str(s["audio_channels"])]
    if s["audio_samplerate"] != "original":
        cmd += ["-ar", str(s["audio_samplerate"])]

    if s["container"] == "mp4":
        cmd += ["-movflags", "+faststart"]

    if s["extra"]:
        extra = shlex.split(s["extra"])
        # Do not allow overriding the input/output paths or arbitrary shell syntax.
        forbidden = {"-i", "-y", "-filter_complex_script"}
        if any(x in forbidden for x in extra):
            raise ValueError("Unsafe custom FFmpeg arguments")
        cmd += extra

    cmd += [str(out)]
    return cmd

async def encode_video(inp, out, settings, progress_cb):
    cmd = build_command(inp, out, settings)
    duration = 0
    start = time.monotonic()

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )

    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        text = line.decode("utf-8", "ignore")
        m = re.search(r"time=(\d+):(\d+):([\d.]+)", text)
        if m:
            current = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
            # Discover duration from first FFmpeg input banner if possible.
            if duration == 0:
                dm = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", text)
                if dm:
                    duration = int(dm.group(1))*3600 + int(dm.group(2))*60 + float(dm.group(3))
        sm = re.search(r"speed=\s*([0-9.]+)x", text)
        speed = float(sm.group(1)) if sm else 0.0
        if m and duration > 0:
            pct = min(100, current / duration * 100)
            await progress_cb(pct, time.monotonic() - start, speed)

    rc = await proc.wait()
    if rc != 0:
        raise RuntimeError(f"FFmpeg exited with code {rc}")
    await progress_cb(100, time.monotonic() - start, 0.0)
