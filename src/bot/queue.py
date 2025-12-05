from collections import deque
from typing import Dict, Optional, Tuple, Any, List
from dataclasses import dataclass


@dataclass
class SongInfo:
    url: str
    title: str
    headers: dict
    requester: str = ""


class GuildMusicQueue:
    def __init__(self):
        self._queue: deque = deque()
        self._current: Optional[SongInfo] = None

    def add(self, song: SongInfo):
        self._queue.append(song)

    def next(self) -> Optional[SongInfo]:
        if self._queue:
            self._current = self._queue.popleft()
            return self._current
        self._current = None
        return None

    def clear(self):
        self._queue.clear()
        self._current = None

    def get_current(self) -> Optional[SongInfo]:
        return self._current

    def get_list(self) -> List[SongInfo]:
        return list(self._queue)

    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def size(self) -> int:
        return len(self._queue)


class QueueManager:
    def __init__(self):
        self._queues: Dict[str, GuildMusicQueue] = {}

    def get_queue(self, guild_id: str) -> GuildMusicQueue:
        if guild_id not in self._queues:
            self._queues[guild_id] = GuildMusicQueue()
        return self._queues[guild_id]

    def add_song(self, guild_id: str, url: str, title: str, headers: dict, requester: str = ""):
        queue = self.get_queue(guild_id)
        song = SongInfo(url=url, title=title, headers=headers, requester=requester)
        queue.add(song)
        return song

    def get_next(self, guild_id: str) -> Optional[SongInfo]:
        queue = self.get_queue(guild_id)
        return queue.next()

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


queue_manager = QueueManager()
