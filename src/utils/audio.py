import discord
import ctypes.util
import os


def load_opus_library() -> bool:
    if discord.opus.is_loaded():
        print("Opus 已載入")
        return True

    print("正在嘗試載入 Opus 函式庫...")

    library_names = [
        ctypes.util.find_library('opus'),
        'libopus-0.x64.dll',
        'libopus-0.x86.dll',
        'libopus.so.0',
        'libopus.so',
        'opus',
    ]

    for name in library_names:
        if name is None:
            continue
        try:
            discord.opus.load_opus(name)
            print(f"成功載入 Opus: {name}")
            return True
        except OSError:
            continue

    common_paths = [
        '/usr/lib/x86_64-linux-gnu/libopus.so.0',
        '/usr/lib/libopus.so.0',
        '/usr/lib/libopus.so',
    ]

    for path in common_paths:
        if os.path.exists(path):
            try:
                discord.opus.load_opus(path)
                print(f"成功載入 Opus: {path}")
                return True
            except OSError:
                continue

    print("警告：無法載入 Opus 函式庫，但部分系統可能仍可正常運作。")
    return False


def build_ffmpeg_options(headers: dict = None) -> dict:
    before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

    if headers and isinstance(headers, dict):
        headers_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        before_opts += f' -headers "{headers_str}"'

    opts = {
        "before_options": before_opts,
        "options": "-vn -filter:a 'volume=0.5'"
    }
    
    if os.path.exists("ffmpeg.exe"):
        opts["executable"] = "ffmpeg.exe"
        
    return opts
