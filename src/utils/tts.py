"""繁體中文語音合成：優先使用 edge-tts（微軟自然語音），沒有安裝時改用 Google 翻譯語音。"""
import logging
import os
import re
import tempfile
import uuid
from typing import List, Optional

from src.utils.http import get_session

logger = logging.getLogger("MusicBot.tts")

try:
    import edge_tts
except ImportError:
    edge_tts = None

TTS_VOICE = os.getenv("TTS_VOICE", "zh-TW-HsiaoChenNeural")
MAX_TTS_CHARS = 400
_TTS_DIR = os.path.join(tempfile.gettempdir(), "swj_musicbot_tts")

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF‍️]+", re.UNICODE
)
_MARKDOWN_RE = re.compile(r"(\*\*|__|`+|~~|\|\||<a?:\w+:\d+>|<[@#!&]*\d+>|https?://\S+)")


def clean_for_speech(text: str) -> str:
    text = _MARKDOWN_RE.sub(" ", text)
    text = _EMOJI_RE.sub(" ", text)
    # 顏文字裡的符號念出來很奇怪，去掉常見的
    text = re.sub(r"[()（）＾^≧≦▽ᴗ•̀ˋˊ´｡✧ε٩۶ง\\/＼／~～*＊]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_TTS_CHARS:
        text = text[:MAX_TTS_CHARS]
    return text


def _split_chunks(text: str, limit: int = 180) -> List[str]:
    """Google 語音一次最多約 200 字，依標點切段。"""
    parts = re.split(r"(?<=[。！？!?，,；;、\n])", text)
    chunks, current = [], ""
    for part in parts:
        while len(part) > limit:
            chunks.append(part[:limit])
            part = part[limit:]
        if len(current) + len(part) > limit:
            if current:
                chunks.append(current)
            current = part
        else:
            current += part
    if current.strip():
        chunks.append(current)
    return [c for c in chunks if c.strip()]


async def _edge_tts(text: str, path: str) -> bool:
    if edge_tts is None:
        return False
    try:
        communicate = edge_tts.Communicate(text, TTS_VOICE)
        await communicate.save(path)
        return os.path.exists(path) and os.path.getsize(path) > 0
    except Exception as e:
        logger.warning("edge-tts 失敗，改用 Google 語音：%s", e)
        return False


async def _google_tts(text: str, path: str) -> bool:
    session = await get_session()
    data = bytearray()
    for chunk in _split_chunks(text):
        params = {"ie": "UTF-8", "tl": "zh-TW", "client": "tw-ob", "q": chunk}
        try:
            async with session.get("https://translate.google.com/translate_tts", params=params, timeout=15) as resp:
                if resp.status != 200:
                    logger.warning("Google 語音回應 %s", resp.status)
                    return False
                data.extend(await resp.read())
        except Exception as e:
            logger.warning("Google 語音失敗：%s", e)
            return False
    if not data:
        return False
    with open(path, "wb") as f:
        f.write(data)
    return True


async def synthesize(text: str) -> Optional[str]:
    """把文字轉成 mp3 檔，回傳檔案路徑；失敗回傳 None。用完請呼叫 cleanup_file。"""
    text = clean_for_speech(text)
    if not text:
        return None
    os.makedirs(_TTS_DIR, exist_ok=True)
    path = os.path.join(_TTS_DIR, f"{uuid.uuid4().hex}.mp3")
    if await _edge_tts(text, path) or await _google_tts(text, path):
        return path
    cleanup_file(path)
    return None


def cleanup_file(path: Optional[str]):
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def ffmpeg_options() -> dict:
    opts = {"options": "-vn"}
    if os.path.exists("ffmpeg.exe"):
        opts["executable"] = "ffmpeg.exe"
    return opts
