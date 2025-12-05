import asyncio
from typing import Optional, Tuple, Dict, Any
import yt_dlp


class YouTubeSearcher:
    def __init__(self):
        self._base_options = {
            "format": "bestaudio[abr<=96]/bestaudio",
            "noplaylist": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False,
            "extractor_args": {
                "youtube": ["player_client=web"]
            },
            "quiet": True,
            "no_warnings": True,
        }

    def _build_options(self, cookies_file: Optional[str] = None) -> dict:
        opts = self._base_options.copy()
        if cookies_file:
            opts["cookiefile"] = cookies_file
        return opts

    def _sync_extract(self, query: str, options: dict) -> dict:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(query, download=False)

    async def search(self, query: str, cookies_file: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        options = self._build_options(cookies_file)
        search_query = f"ytsearch1: {query}"

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._sync_extract(search_query, options)
            )

            entries = result.get("entries", [])
            if not entries:
                return None, "找不到這首歌！"

            track = entries[0]
            return {
                "url": track.get("url"),
                "title": track.get("title", "未知標題"),
                "headers": track.get("http_headers", {}),
                "duration": track.get("duration", 0),
            }, None

        except Exception as e:
            return None, f"搜尋時發生錯誤：{str(e)}"


youtube_searcher = YouTubeSearcher()
