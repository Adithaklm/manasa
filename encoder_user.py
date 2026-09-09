import subprocess
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

def encode_file(src, dst, settings=None):
    settings = settings or {}
    crf = str(settings.get("crf", "23"))
    preset = str(settings.get("preset", "medium"))
    codec = str(settings.get("codec", "libx264"))

    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-i", src,
        "-map", "0:v:0?", "-map", "0:a:0?",
        "-c:v", codec, "-preset", preset, "-crf", crf,
        "-c:a", "aac", "-b:a", "160k",
        "-map_metadata", "0",
        "-y", dst
    ]
    p = subprocess.run(cmd)
    return p.returncode == 0
