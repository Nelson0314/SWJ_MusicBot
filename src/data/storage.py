import json
import os
from typing import Dict, Set, List, Any

BANNED_KEYWORDS_FILE = "data/banned_keywords.json"
PLAYLISTS_FILE = "data/playlists.json"


def ensure_data_directory():
    os.makedirs("data", exist_ok=True)


class BannedKeywordsManager:
    def __init__(self):
        self._keywords: Dict[str, Set[str]] = {}
        self._load()

    def _load(self):
        ensure_data_directory()
        try:
            with open(BANNED_KEYWORDS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self._keywords = {
                    guild_id: set(words)
                    for guild_id, words in data.items()
                }
        except (FileNotFoundError, json.JSONDecodeError):
            self._keywords = {}

    def _save(self):
        ensure_data_directory()
        data = {
            guild_id: list(words)
            for guild_id, words in self._keywords.items()
        }
        with open(BANNED_KEYWORDS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

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
        self._load()

    def _load(self):
        ensure_data_directory()
        try:
            with open(PLAYLISTS_FILE, 'r', encoding='utf-8') as f:
                self._playlists = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._playlists = {}

    def _save(self):
        ensure_data_directory()
        with open(PLAYLISTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self._playlists, f, indent=2, ensure_ascii=False)

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

    def add_song(self, guild_id: str, playlist_name: str, title: str, query: str) -> bool:
        if not self.playlist_exists(guild_id, playlist_name):
            return False
        self._playlists[guild_id][playlist_name].append({
            "title": title,
            "query": query
        })
        self._save()
        return True

    def remove_song(self, guild_id: str, playlist_name: str, title: str) -> bool:
        if not self.playlist_exists(guild_id, playlist_name):
            return False
        playlist = self._playlists[guild_id][playlist_name]
        for i, song in enumerate(playlist):
            if song["title"] == title:
                playlist.pop(i)
                self._save()
                return True
        return False

    def get_song_titles(self, guild_id: str, playlist_name: str) -> List[str]:
        playlist = self.get_playlist(guild_id, playlist_name)
        return [song["title"] for song in playlist]


banned_keywords_manager = BannedKeywordsManager()
playlist_manager = PlaylistManager()
