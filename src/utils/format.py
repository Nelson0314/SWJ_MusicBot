from typing import Optional


def format_duration(seconds: Optional[float]) -> str:
    if not seconds or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_timestamp(text: str) -> Optional[int]:
    """把 '1:23'、'01:02:03'、'90' 轉成秒數。格式錯誤回傳 None。"""
    text = text.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    total = 0
    try:
        for part in parts:
            value = int(part)
            if value < 0:
                return None
            total = total * 60 + value
    except ValueError:
        return None
    return total


def progress_bar(position: float, duration: float, length: int = 16) -> str:
    if not duration or duration <= 0:
        return "🔴 直播 / 未知長度"
    ratio = max(0.0, min(1.0, position / duration))
    filled = int(round(ratio * (length - 1)))
    bar = "▬" * filled + "🔘" + "▬" * (length - 1 - filled)
    return f"{bar} `{format_duration(position)} / {format_duration(duration)}`"


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
