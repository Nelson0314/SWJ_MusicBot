import discord
import subprocess
import os


def load_opus_library() -> bool:
    if discord.opus.is_loaded():
        print("Opus 已載入")
        return True

    print("正在載入 Opus 函式庫...")

    try:
        result = subprocess.run(
            ['pkg-config', '--libs', 'opus'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            lib_info = result.stdout.strip()
            print(f"找到 Opus: {lib_info}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    library_names = [
        'libopus.so.0',
        'libopus.so',
        'opus',
    ]

    for name in library_names:
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
        '/nix/store/*/lib/libopus.so.0',
    ]

    for path in common_paths:
        if os.path.exists(path):
            try:
                discord.opus.load_opus(path)
                print(f"成功載入 Opus: {path}")
                return True
            except OSError:
                continue

    print("無法載入 Opus 函式庫，語音功能可能無法正常運作")
    return False


def build_ffmpeg_options(headers: dict = None) -> dict:
    before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

    if headers and isinstance(headers, dict):
        headers_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        before_opts += f' -headers "{headers_str}"'

    return {
        "before_options": before_opts,
        "options": "-vn -filter:a 'volume=0.5'"
    }
