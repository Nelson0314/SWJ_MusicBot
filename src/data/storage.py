import json
import logging
import os
import tempfile
import threading
from typing import Dict, Set, List, Any, Optional

logger = logging.getLogger("MusicBot.storage")

BANNED_KEYWORDS_FILE = "data/banned_keywords.json"
PLAYLISTS_FILE = "data/playlists.json"
CHAT_SETTINGS_FILE = "data/chat_settings.json"
STATS_FILE = "data/stats.json"


def ensure_data_directory():
    os.makedirs("data", exist_ok=True)


def _atomic_write_json(path: str, data: Any):
    """先寫到暫存檔再取代，避免寫到一半當機導致 JSON 損毀。"""
    ensure_data_directory()
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _load_json(path: str, default):
    ensure_data_directory()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        # 檔案損毀時先備份，避免下次存檔把資料蓋掉
        backup = path + ".corrupted"
        try:
            os.replace(path, backup)
            logger.error("%s 格式錯誤，已備份為 %s", path, backup)
        except OSError:
            pass
        return default


class BannedKeywordsManager:
    def __init__(self):
        self._keywords: Dict[str, Set[str]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        data = _load_json(BANNED_KEYWORDS_FILE, {})
        try:
            self._keywords = {
                guild_id: set(words)
                for guild_id, words in data.items()
            }
        except (AttributeError, TypeError):
            self._keywords = {}

    def _save(self):
        with self._lock:
            data = {
                guild_id: list(words)
                for guild_id, words in self._keywords.items()
            }
            _atomic_write_json(BANNED_KEYWORDS_FILE, data)

    def add_keyword(self, guild_id: str, keyword: str) -> bool:
        if guild_id not in self._keywords:
            self._keywords[guild_id] = set()
        if keyword.lower() in self._keywords[guild_id]:
            return False
        self._keywords[guild_id].add(keyword.lower())
        self._save()
        return True

    def remove_keyword(self, guild_id: str, keyword: str) -> bool:
        if guild_id not in self._keywords:
            return False
        keyword_lower = keyword.lower()
        if keyword_lower not in self._keywords[guild_id]:
            return False
        self._keywords[guild_id].remove(keyword_lower)
        self._save()
        return True

    def get_keywords(self, guild_id: str) -> Set[str]:
        return self._keywords.get(guild_id, set())

    def get_keywords_list(self, guild_id: str) -> List[str]:
        return list(self._keywords.get(guild_id, set()))


class PlaylistManager:
    def __init__(self):
        self._playlists: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        data = _load_json(PLAYLISTS_FILE, {})
        self._playlists = data if isinstance(data, dict) else {}

    def _save(self):
        with self._lock:
            _atomic_write_json(PLAYLISTS_FILE, self._playlists)

    def create_playlist(self, guild_id: str, name: str) -> bool:
        if guild_id not in self._playlists:
            self._playlists[guild_id] = {}
        if name in self._playlists[guild_id]:
            return False
        self._playlists[guild_id][name] = []
        self._save()
        return True

    def delete_playlist(self, guild_id: str, name: str) -> bool:
        if guild_id not in self._playlists:
            return False
        if name not in self._playlists[guild_id]:
            return False
        del self._playlists[guild_id][name]
        self._save()
        return True

    def get_playlist(self, guild_id: str, name: str) -> List[Dict[str, str]]:
        if guild_id not in self._playlists:
            return []
        return self._playlists[guild_id].get(name, [])

    def get_all_playlists(self, guild_id: str) -> Dict[str, List[Dict[str, str]]]:
        return self._playlists.get(guild_id, {})

    def get_playlist_names(self, guild_id: str) -> List[str]:
        return list(self._playlists.get(guild_id, {}).keys())

    def playlist_exists(self, guild_id: str, name: str) -> bool:
        if guild_id not in self._playlists:
            return False
        return name in self._playlists[guild_id]

    def add_song(self, guild_id: str, playlist_name: str, title: str, query: str,
                 url: Optional[str] = None) -> bool:
        if not self.playlist_exists(guild_id, playlist_name):
            return False
        entry = {
            "title": title,
            "query": query
        }
        if url:
            # 記住影片網址，之後播放時不必重新搜尋，大幅加快載入速度
            entry["url"] = url
        self._playlists[guild_id][playlist_name].append(entry)
        self._save()
        return True

    def add_songs(self, guild_id: str, playlist_name: str, songs: List[Dict[str, str]]) -> int:
        """一次加入多首（只存檔一次）。songs: [{title, query, url?}]"""
        if not self.playlist_exists(guild_id, playlist_name):
            return 0
        playlist = self._playlists[guild_id][playlist_name]
        for song in songs:
            entry = {"title": song["title"], "query": song.get("query") or song["title"]}
            if song.get("url"):
                entry["url"] = song["url"]
            playlist.append(entry)
        if songs:
            self._save()
        return len(songs)

    def remove_song(self, guild_id: str, playlist_name: str, title: str) -> bool:
        if not self.playlist_exists(guild_id, playlist_name):
            return False
        playlist = self._playlists[guild_id][playlist_name]
        for i, song in enumerate(playlist):
            if song["title"] == title:
                playlist.pop(i)
                self._save()
                return True
        # Discord 自動完成最多 100 字，超長標題會被截斷，用開頭比對
        if len(title) >= 100:
            for i, song in enumerate(playlist):
                if song["title"].startswith(title):
                    playlist.pop(i)
                    self._save()
                    return True
        return False

    def set_song_url(self, guild_id: str, playlist_name: str, query: str, url: str) -> bool:
        """把解析到的影片網址記回播放清單（只補沒有網址的項目）。"""
        if not url or not self.playlist_exists(guild_id, playlist_name):
            return False
        changed = False
        for song in self._playlists[guild_id][playlist_name]:
            if song.get("query") == query and not song.get("url"):
                song["url"] = url
                changed = True
        if changed:
            self._save()
        return changed

    def get_song_titles(self, guild_id: str, playlist_name: str) -> List[str]:
        playlist = self.get_playlist(guild_id, playlist_name)
        return [song["title"] for song in playlist]


class ChatSettingsManager:
    """記錄哪些頻道開啟了自動聊天、語音朗讀等設定。"""

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        data = _load_json(CHAT_SETTINGS_FILE, {})
        self._data = data if isinstance(data, dict) else {}

    def _save(self):
        with self._lock:
            _atomic_write_json(CHAT_SETTINGS_FILE, self._data)

    def _guild(self, guild_id: str) -> Dict[str, Any]:
        return self._data.setdefault(guild_id, {"channels": [], "voice": False})

    def is_chat_channel(self, guild_id: str, channel_id: int) -> bool:
        return channel_id in self._data.get(guild_id, {}).get("channels", [])

    def set_chat_channel(self, guild_id: str, channel_id: int, enabled: bool) -> bool:
        guild = self._guild(guild_id)
        channels = guild["channels"]
        if enabled and channel_id not in channels:
            channels.append(channel_id)
        elif not enabled and channel_id in channels:
            channels.remove(channel_id)
        else:
            return False
        self._save()
        return True

    def voice_enabled(self, guild_id: str) -> bool:
        return bool(self._data.get(guild_id, {}).get("voice", False))

    def set_voice(self, guild_id: str, enabled: bool):
        self._guild(guild_id)["voice"] = enabled
        self._save()


class StatsManager:
    """播放統計。記錄在記憶體中，由背景工作定期寫檔。"""

    def __init__(self):
        self._data: Dict[str, Dict[str, Dict[str, int]]] = {}
        self._dirty = False
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        data = _load_json(STATS_FILE, {})
        self._data = data if isinstance(data, dict) else {}

    def record_play(self, guild_id: str, title: str, requester: str):
        guild = self._data.setdefault(guild_id, {"songs": {}, "users": {}})
        songs = guild.setdefault("songs", {})
        users = guild.setdefault("users", {})
        songs[title] = songs.get(title, 0) + 1
        if requester:
            users[requester] = users.get(requester, 0) + 1
        # 避免檔案無限長大：只保留前 500 首
        if len(songs) > 600:
            keep = sorted(songs.items(), key=lambda kv: kv[1], reverse=True)[:500]
            guild["songs"] = dict(keep)
        self._dirty = True

    def top_songs(self, guild_id: str, n: int = 10):
        songs = self._data.get(guild_id, {}).get("songs", {})
        return sorted(songs.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def top_users(self, guild_id: str, n: int = 10):
        users = self._data.get(guild_id, {}).get("users", {})
        return sorted(users.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def flush(self):
        if not self._dirty:
            return
        with self._lock:
            self._dirty = False
            try:
                _atomic_write_json(STATS_FILE, self._data)
            except OSError as e:
                self._dirty = True
                logger.warning("儲存統計失敗: %s", e)


banned_keywords_manager = BannedKeywordsManager()
playlist_manager = PlaylistManager()
chat_settings_manager = ChatSettingsManager()
stats_manager = StatsManager()
