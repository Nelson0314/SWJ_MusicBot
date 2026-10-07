import random
import time
from collections import deque
from typing import Dict, Optional, List
from dataclasses import dataclass

# 循環模式
LOOP_OFF = "off"
LOOP_ONE = "one"
LOOP_ALL = "all"
LOOP_MODES = (LOOP_OFF, LOOP_ONE, LOOP_ALL)


@dataclass
class SongInfo:
    url: str
    title: str
    headers: dict
    requester: str = ""
    # 以下為新增欄位，皆有預設值，舊的建立方式不受影響
    query: str = ""             # 尚未解析時用來搜尋的關鍵字或網址
    webpage_url: str = ""       # 影片頁面網址（重新取得串流時使用）
    duration: int = 0           # 秒
    thumbnail: str = ""
    uploader: str = ""
    expires: float = 0.0        # 串流網址過期時間 (epoch)
    source_playlist: str = ""   # 從哪個已儲存播放清單加入

    def needs_refresh(self, margin: float = 600) -> bool:
        """串流網址不存在或快過期時需要重新解析。"""
        if not self.url:
            return True
        if self.expires and self.expires - margin < time.time():
            return True
        return False

    @property
    def lookup(self) -> str:
        """重新解析時優先使用影片網址，其次才是搜尋關鍵字。"""
        return self.webpage_url or self.query or self.title

    def apply_resolved(self, data: dict):
        self.url = data.get("url") or ""
        self.title = data.get("title") or self.title
        self.headers = data.get("headers") or {}
        self.duration = int(data.get("duration") or self.duration or 0)
        self.webpage_url = data.get("webpage_url") or self.webpage_url
        self.thumbnail = data.get("thumbnail") or self.thumbnail
        self.uploader = data.get("uploader") or self.uploader
        self.expires = float(data.get("expires") or 0)


class GuildMusicQueue:
    def __init__(self):
        self._queue: deque = deque()
        self._current: Optional[SongInfo] = None
        self._history: deque = deque(maxlen=50)
        self.loop_mode: str = LOOP_OFF

    def add(self, song: SongInfo):
        self._queue.append(song)

    def add_many(self, songs: List[SongInfo]):
        self._queue.extend(songs)

    def next(self, force_advance: bool = False) -> Optional[SongInfo]:
        current = self._current
        if current is not None and not force_advance and self.loop_mode == LOOP_ONE:
            return current
        if current is not None:
            self._history.append(current)
            if self.loop_mode == LOOP_ALL:
                self._queue.append(current)
        if self._queue:
            self._current = self._queue.popleft()
            return self._current
        self._current = None
        return None

    def clear(self):
        self._queue.clear()
        self._current = None

    def clear_upcoming(self):
        """只清空待播歌曲，保留正在播放的那首。"""
        self._queue.clear()

    def drop_current(self):
        """目前歌曲無法播放時丟掉它（不放進歷史、不參與循環）。"""
        self._current = None

    def get_current(self) -> Optional[SongInfo]:
        return self._current

    def get_list(self) -> List[SongInfo]:
        return list(self._queue)

    def peek(self, n: int = 1) -> List[SongInfo]:
        return [self._queue[i] for i in range(min(n, len(self._queue)))]

    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def size(self) -> int:
        return len(self._queue)

    def shuffle(self) -> int:
        items = list(self._queue)
        random.shuffle(items)
        self._queue = deque(items)
        return len(items)

    def remove(self, index: int) -> Optional[SongInfo]:
        """index 從 1 開始。"""
        if index < 1 or index > len(self._queue):
            return None
        song = self._queue[index - 1]
        del self._queue[index - 1]
        return song

    def move(self, src: int, dst: int) -> Optional[SongInfo]:
        if src < 1 or src > len(self._queue):
            return None
        dst = max(1, min(dst, len(self._queue)))
        song = self._queue[src - 1]
        del self._queue[src - 1]
        self._queue.insert(dst - 1, song)
        return song

    def skip_to(self, index: int) -> bool:
        """丟掉 index 之前的歌曲，讓第 index 首成為下一首。"""
        if index < 1 or index > len(self._queue):
            return False
        for _ in range(index - 1):
            skipped = self._queue.popleft()
            if self.loop_mode == LOOP_ALL:
                self._queue.append(skipped)
        return True

    def history(self) -> List[SongInfo]:
        return list(self._history)

    def total_duration(self) -> int:
        return sum(s.duration for s in self._queue)


class QueueManager:
    def __init__(self):
        self._queues: Dict[str, GuildMusicQueue] = {}

    def get_queue(self, guild_id: str) -> GuildMusicQueue:
        if guild_id not in self._queues:
            self._queues[guild_id] = GuildMusicQueue()
        return self._queues[guild_id]

    def add_song(self, guild_id: str, url: str, title: str, headers: dict, requester: str = "", **extra):
        queue = self.get_queue(guild_id)
        song = SongInfo(url=url, title=title, headers=headers, requester=requester, **extra)
        queue.add(song)
        return song

    def add_song_info(self, guild_id: str, song: SongInfo) -> SongInfo:
        self.get_queue(guild_id).add(song)
        return song

    def add_songs(self, guild_id: str, songs: List[SongInfo]) -> int:
        self.get_queue(guild_id).add_many(songs)
        return len(songs)

    def get_next(self, guild_id: str, force_advance: bool = False) -> Optional[SongInfo]:
        queue = self.get_queue(guild_id)
        return queue.next(force_advance=force_advance)

    def clear_queue(self, guild_id: str):
        queue = self.get_queue(guild_id)
        queue.clear()

    def get_queue_list(self, guild_id: str) -> List[SongInfo]:
        queue = self.get_queue(guild_id)
        return queue.get_list()

    def get_current_song(self, guild_id: str) -> Optional[SongInfo]:
        queue = self.get_queue(guild_id)
        return queue.get_current()

    def is_queue_empty(self, guild_id: str) -> bool:
        queue = self.get_queue(guild_id)
        return queue.is_empty()

    def queue_size(self, guild_id: str) -> int:
        queue = self.get_queue(guild_id)
        return queue.size()

    def peek(self, guild_id: str, n: int = 1) -> List[SongInfo]:
        return self.get_queue(guild_id).peek(n)

    def shuffle(self, guild_id: str) -> int:
        return self.get_queue(guild_id).shuffle()

    def remove(self, guild_id: str, index: int) -> Optional[SongInfo]:
        return self.get_queue(guild_id).remove(index)

    def move(self, guild_id: str, src: int, dst: int) -> Optional[SongInfo]:
        return self.get_queue(guild_id).move(src, dst)

    def skip_to(self, guild_id: str, index: int) -> bool:
        return self.get_queue(guild_id).skip_to(index)

    def drop_current(self, guild_id: str):
        self.get_queue(guild_id).drop_current()

    def get_loop_mode(self, guild_id: str) -> str:
        return self.get_queue(guild_id).loop_mode

    def set_loop_mode(self, guild_id: str, mode: str) -> str:
        if mode not in LOOP_MODES:
            mode = LOOP_OFF
        self.get_queue(guild_id).loop_mode = mode
        return mode

    def get_history(self, guild_id: str) -> List[SongInfo]:
        return self.get_queue(guild_id).history()

    def total_duration(self, guild_id: str) -> int:
        return self.get_queue(guild_id).total_duration()


queue_manager = QueueManager()
