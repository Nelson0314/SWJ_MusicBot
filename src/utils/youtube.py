import asyncio
import logging
import os
import re
import shutil
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple, Dict, Any, List
from urllib.parse import urlparse, parse_qs

import yt_dlp

logger = logging.getLogger("MusicBot.youtube")

# 串流網址通常約 6 小時後過期；拿不到確切時間時保守估計
DEFAULT_STREAM_TTL = 5 * 60 * 60
# 距離過期少於這個秒數就視為需要重新取得
EXPIRY_MARGIN = 10 * 60
# 搜尋結果快取上限
CACHE_LIMIT = 512

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# 這些錯誤重試也沒用，直接回報
_FATAL_PATTERNS = (
    "private video",
    "video unavailable",
    "has been removed",
    "copyright",
    "not available in your country",
    "members-only",
    "join this channel",
    "sign in to confirm your age",
    "this live event will begin",
    "premieres in",
)


def is_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def is_playlist_url(text: str) -> bool:
    """判斷是否為 YouTube 播放清單網址（單純帶 list= 的影片網址也算）。"""
    if not is_url(text):
        return False
    parsed = urlparse(text.strip())
    host = parsed.netloc.lower()
    if "youtube.com" not in host and "youtu.be" not in host:
        return False
    qs = parse_qs(parsed.query)
    if "list" not in qs:
        return False
    # YouTube 自動產生的 Mix (RD 開頭) 會無限延伸，當作單曲處理
    return not qs["list"][0].startswith("RD")


def stream_expiry(url: Optional[str]) -> float:
    """從串流網址解析過期時間 (epoch 秒)。"""
    if not url:
        return 0.0
    try:
        qs = parse_qs(urlparse(url).query)
        if "expire" in qs:
            return float(qs["expire"][0])
    except (ValueError, TypeError):
        pass
    match = re.search(r"/expire/(\d+)", url)
    if match:
        return float(match.group(1))
    return time.time() + DEFAULT_STREAM_TTL


def friendly_error(err: Exception) -> str:
    msg = str(err)
    low = msg.lower()
    if "private video" in low:
        return "這是私人影片，無法播放！"
    if "sign in to confirm your age" in low:
        return "這部影片有年齡限制，無法播放！"
    if "copyright" in low or "has been removed" in low or "video unavailable" in low:
        return "影片已被移除或無法觀看！"
    if "not available in your country" in low:
        return "這部影片在目前地區無法觀看！"
    if "members-only" in low or "join this channel" in low:
        return "這是會員限定影片，無法播放！"
    if "sign in to confirm you" in low and "bot" in low:
        return "YouTube 要求驗證（疑似機器人），請稍後再試或更新 cookies.txt！"
    if "http error 429" in low or "too many requests" in low:
        return "YouTube 暫時限制了請求次數，請稍後再試！"
    # yt-dlp 的錯誤訊息前面常帶有 ERROR: [youtube] xxx:
    short = re.sub(r"^ERROR:\s*", "", msg).strip()
    if len(short) > 180:
        short = short[:180] + "…"
    return f"搜尋時發生錯誤：{short}"


def _detect_js_runtimes() -> Dict[str, dict]:
    """新版 yt-dlp 需要 JS runtime 才能拿到完整的 YouTube 格式，有裝就啟用。"""
    runtimes = {}
    for name in ("deno", "node", "bun"):
        path = shutil.which(name)
        if path:
            runtimes[name] = {"path": path}
    return runtimes


class _TTLCache:
    def __init__(self, limit: int = CACHE_LIMIT):
        self._data: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._limit = limit

    def get(self, key: str):
        item = self._data.get(key)
        if not item:
            return None
        expires, value = item
        if expires <= time.time():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any, expires: float):
        self._data[key] = (expires, value)
        self._data.move_to_end(key)
        while len(self._data) > self._limit:
            self._data.popitem(last=False)

    def pop(self, key: str):
        self._data.pop(key, None)

    def clear(self):
        self._data.clear()


class YouTubeSearcher:
    def __init__(self, max_workers: int = 4):
        self._base_options = {
            "format": "bestaudio[abr<=96]/bestaudio",
            "noplaylist": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False,
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "socket_timeout": 15,
            "retries": 3,
            "extractor_retries": 2,
            "cachedir": False,
        }
        js_runtimes = _detect_js_runtimes()
        if js_runtimes:
            self._base_options["js_runtimes"] = js_runtimes
            self._base_options["remote_components"] = ["ejs:github"]

        # 第一次用原本的設定，失敗時依序換其他 player client / 格式重試
        self._fallback_variants: List[dict] = [
            {},
            {
                "format": "bestaudio/best",
                "extractor_args": {"youtube": {"player_client": ["default", "-android_sdkless"]}},
            },
            {
                "format": "bestaudio/best",
                "extractor_args": {"youtube": {"player_client": ["tv", "web_safari", "mweb"]}},
            },
        ]
        # 專用執行緒池，避免大量載入時塞滿預設 executor
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ytdlp")
        self._cache = _TTLCache()
        self._inflight: Dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------------ options
    def _build_options(self, cookies_file: Optional[str] = None) -> dict:
        opts = self._base_options.copy()

        if not cookies_file and os.path.exists("cookies.txt"):
            cookies_file = "cookies.txt"

        if cookies_file:
            opts["cookiefile"] = cookies_file
        return opts

    def _sync_extract(self, query: str, options: dict) -> dict:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(query, download=False)

    async def _run(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _track_to_dict(track: dict) -> Dict[str, Any]:
        url = track.get("url")
        webpage_url = track.get("webpage_url") or track.get("original_url") or ""
        if not webpage_url and track.get("id") and track.get("extractor_key", "").lower().startswith("youtube"):
            webpage_url = f"https://www.youtube.com/watch?v={track['id']}"
        return {
            "url": url,
            "title": track.get("title", "未知標題"),
            "headers": track.get("http_headers", {}) or {},
            "duration": int(track.get("duration") or 0),
            "webpage_url": webpage_url,
            "thumbnail": track.get("thumbnail") or "",
            "uploader": track.get("uploader") or track.get("channel") or "",
            "expires": stream_expiry(url),
            "is_live": bool(track.get("is_live")),
        }

    def _extract_with_fallback(self, target: str, cookies_file: Optional[str]) -> dict:
        base = self._build_options(cookies_file)
        last_error: Optional[Exception] = None
        for attempt, variant in enumerate(self._fallback_variants):
            opts = dict(base)
            opts.update(variant)
            try:
                result = self._sync_extract(target, opts)
                if result is None:
                    raise yt_dlp.utils.DownloadError("沒有取得任何資料")
                if "entries" in result:
                    entries = [e for e in (result.get("entries") or []) if e]
                    if not entries:
                        return {"entries": []}
                    track = entries[0]
                else:
                    track = result
                if not track.get("url"):
                    raise yt_dlp.utils.DownloadError("找不到可播放的音訊格式")
                return track
            except Exception as e:  # yt-dlp 會丟出各式各樣的例外
                last_error = e
                low = str(e).lower()
                if any(p in low for p in _FATAL_PATTERNS):
                    break
                logger.warning("解析失敗 (第 %d 次) %s: %s", attempt + 1, target, e)
        raise last_error or RuntimeError("未知錯誤")

    # ------------------------------------------------------------------ public
    async def search(self, query: str, cookies_file: Optional[str] = None,
                     force_refresh: bool = False) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """搜尋或解析單一歌曲。query 可以是關鍵字或網址。"""
        query = query.strip()
        if not query:
            return None, "找不到這首歌！"
        target = query if is_url(query) else f"ytsearch1:{query}"
        key = f"{cookies_file or ''}|{target}"

        if not force_refresh:
            cached = self._cache.get(key)
            if cached:
                return dict(cached), None
        else:
            self._cache.pop(key)

        # 同一首歌同時被要求多次時只解析一次
        pending = self._inflight.get(key)
        if pending is not None and not force_refresh:
            try:
                data = await asyncio.shield(pending)
                return dict(data), None
            except Exception as e:
                return None, friendly_error(e)

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        try:
            track = await self._run(self._extract_with_fallback, target, cookies_file)
            if "entries" in track and not track.get("url"):
                fut.set_exception(LookupError("找不到這首歌！"))
                return None, "找不到這首歌！"
            data = self._track_to_dict(track)
            if not data["is_live"]:
                self._cache.set(key, data, data["expires"] - EXPIRY_MARGIN)
                if data["webpage_url"] and data["webpage_url"] != query:
                    self._cache.set(f"{cookies_file or ''}|{data['webpage_url']}", data, data["expires"] - EXPIRY_MARGIN)
            fut.set_result(data)
            return dict(data), None
        except Exception as e:
            if not fut.done():
                fut.set_exception(e)
            logger.warning("搜尋失敗 %s: %s", query, e)
            return None, friendly_error(e)
        finally:
            self._inflight.pop(key, None)
            # 避免沒有人等待時出現 "Future exception was never retrieved"
            if fut.done() and not fut.cancelled():
                fut.exception()

    async def search_many(self, query: str, limit: int = 5,
                          cookies_file: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """快速搜尋多筆結果（只取標題/網址，不解析串流）。"""
        opts = self._build_options(cookies_file)
        opts["extract_flat"] = "in_playlist"
        target = f"ytsearch{limit}:{query.strip()}"
        try:
            result = await self._run(self._sync_extract, target, opts)
        except Exception as e:
            return [], friendly_error(e)
        items = []
        for entry in (result or {}).get("entries") or []:
            if not entry:
                continue
            url = entry.get("url") or entry.get("webpage_url")
            if url and not is_url(url) and entry.get("id"):
                url = f"https://www.youtube.com/watch?v={entry['id']}"
            if not url:
                continue
            items.append({
                "title": entry.get("title") or "未知標題",
                "url": url,
                "duration": int(entry.get("duration") or 0),
                "uploader": entry.get("uploader") or entry.get("channel") or "",
            })
        if not items:
            return [], "找不到這首歌！"
        return items, None

    async def extract_playlist(self, url: str, limit: int = 500,
                               cookies_file: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """用 flat 模式快速取得播放清單內容（不逐首解析，速度快很多）。"""
        opts = self._build_options(cookies_file)
        opts["noplaylist"] = False
        opts["extract_flat"] = "in_playlist"
        opts["playlistend"] = limit
        try:
            result = await self._run(self._sync_extract, url.strip(), opts)
        except Exception as e:
            return None, friendly_error(e)
        if not result:
            return None, "無法讀取這個播放清單！"
        entries = []
        for entry in result.get("entries") or []:
            if not entry:
                continue
            title = entry.get("title") or ""
            # 被刪除 / 私人的影片會出現在清單裡但無法播放
            if title in ("[Deleted video]", "[Private video]"):
                continue
            video_url = entry.get("url") or entry.get("webpage_url")
            if video_url and not is_url(video_url) and entry.get("id"):
                video_url = f"https://www.youtube.com/watch?v={entry['id']}"
            if not video_url:
                continue
            entries.append({
                "title": title or "未知標題",
                "url": video_url,
                "duration": int(entry.get("duration") or 0),
            })
        if not entries:
            return None, "播放清單是空的或無法讀取！"
        return {"title": result.get("title") or "YouTube 播放清單", "entries": entries}, None

    def invalidate(self, query: str, cookies_file: Optional[str] = None):
        query = query.strip()
        target = query if is_url(query) else f"ytsearch1:{query}"
        self._cache.pop(f"{cookies_file or ''}|{target}")

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)


youtube_searcher = YouTubeSearcher()
