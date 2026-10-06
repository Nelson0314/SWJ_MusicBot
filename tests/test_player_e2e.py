"""播放流程端對端測試：用真正的 discord.py 播放執行緒 + ffmpeg + 本機 HTTP 伺服器模擬 YouTube 串流。

python -m unittest tests.test_player_e2e
"""
import asyncio
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402
from discord.player import AudioPlayer  # noqa: E402

from src.bot.player import MusicPlayer  # noqa: E402
from src.bot.queue import queue_manager, SongInfo, LOOP_ONE, LOOP_OFF  # noqa: E402
from src.utils.youtube import youtube_searcher  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None


class FakeMessage:
    def __init__(self, content):
        self.content = content
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, guild):
        self.guild = guild
        self.id = 42
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)
        return FakeMessage(content)


class FakeVoiceClient:
    """模仿 discord.VoiceClient 的播放介面，但不連線 Discord。"""

    def __init__(self, loop, guild):
        self.loop = loop
        self.guild = guild
        self.channel = SimpleNamespace(members=[])
        self.client = SimpleNamespace(loop=loop)
        self.ws = SimpleNamespace(speak=self._speak)
        self._player = None
        self._connected = True
        self.packets = 0
        self.disconnected = 0

    async def _speak(self, state):
        return None

    def send_audio_packet(self, data, *, encode=True):
        self.packets += 1

    def is_connected(self):
        return self._connected

    def wait_until_connected(self, timeout):
        return self._connected

    def play(self, source, *, after=None):
        if self.is_playing():
            raise discord.ClientException("Already playing audio.")
        self._player = AudioPlayer(source, self, after=after)
        self._player.start()

    def is_playing(self):
        return self._player is not None and self._player.is_playing()

    def is_paused(self):
        return self._player is not None and self._player.is_paused()

    def pause(self):
        if self._player:
            self._player.pause()

    def resume(self):
        if self._player:
            self._player.resume()

    def stop(self):
        if self._player:
            self._player.stop()
            self._player = None

    @property
    def source(self):
        return self._player.source if self._player else None

    @source.setter
    def source(self, value):
        if self._player is None:
            raise ValueError("Not playing anything.")
        self._player.set_source(value)

    async def disconnect(self, force=False):
        self._connected = False
        self.disconnected += 1
        self.stop()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
class PlayerEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        for name, seconds in (("a", 4), ("b", 4), ("long", 12)):
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                 "-ac", "2", "-ar", "48000", os.path.join(cls.tmp, f"{name}.mp3")],
                check=True,
            )
        handler = functools.partial(_QuietHandler, directory=cls.tmp)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.calls = []
        self.fail_first = set()
        self.always_fail = set()
        self.seen_cut = set()
        self.always_404 = set()
        self.resolve_delay = 0.0
        youtube_searcher._cache.clear()
        self._orig_extract = youtube_searcher._extract_with_fallback

        def fake_extract(target, cookies_file):
            self.calls.append(target)
            time.sleep(self.resolve_delay)
            # 重新解析時會改用影片網址 https://youtu.be/<key>
            key = target.replace("ytsearch1:", "").rsplit("/", 1)[-1]
            if key in self.always_fail:
                raise Exception("ERROR: Video unavailable")
            name = "long" if key.startswith("long") else ("b" if key.endswith("b") else "a")
            url = f"{self.base}/{name}.mp3"
            if key.startswith("cut"):
                # 第一次給 4 秒的檔案（模擬播到一半斷線），之後給完整的 12 秒
                name = "long"
                url = f"{self.base}/{'a' if key not in self.seen_cut else 'long'}.mp3"
                self.seen_cut.add(key)
            if key in self.fail_first or key in self.always_404:
                self.fail_first.discard(key)
                url = f"{self.base}/missing.mp3"  # 404，模擬過期的串流網址
            return {"url": url, "title": f"歌曲 {key}", "duration": 12 if name == "long" else 4,
                    "webpage_url": f"https://youtu.be/{key}", "http_headers": {}}

        youtube_searcher._extract_with_fallback = fake_extract

    def tearDown(self):
        youtube_searcher._extract_with_fallback = self._orig_extract

    def _run(self, coro):
        return asyncio.run(asyncio.wait_for(coro, timeout=90))

    async def _setup_player(self, guild_id):
        loop = asyncio.get_running_loop()
        guild = SimpleNamespace(id=int(guild_id))
        bot = SimpleNamespace(loop=loop, user=SimpleNamespace(id=999))
        player = MusicPlayer(bot)
        channel = FakeChannel(guild)
        vc = FakeVoiceClient(loop, guild)
        queue_manager.clear_queue(guild_id)
        queue_manager.set_loop_mode(guild_id, LOOP_OFF)
        return player, channel, vc

    @staticmethod
    async def _wait_for(predicate, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.05)
        return False

    def test_long_playlist_starts_immediately_and_plays_in_order(self):
        async def scenario():
            gid = "1001"
            player, channel, vc = await self._setup_player(gid)
            self.resolve_delay = 0.3  # 模擬每首解析要 0.3 秒
            songs = [SongInfo(url="", title=f"清單歌 {i}", headers={}, query=f"song{i}{'b' if i % 2 else ''}")
                     for i in range(300)]
            queue_manager.add_songs(gid, songs)

            t0 = time.monotonic()
            started = await player.start_playing(vc, gid, channel)
            elapsed = time.monotonic() - t0
            self.assertTrue(started)
            # 舊版要逐首搜尋 300 首（約 90 秒）才開始；現在只解析第一首
            self.assertLess(elapsed, 2.0, f"啟動花了 {elapsed:.2f}s")
            self.assertTrue(vc.is_playing())
            self.assertEqual(queue_manager.get_current_song(gid).query, "song0")

            # 預先載入下一首
            self.assertTrue(await self._wait_for(lambda: queue_manager.peek(gid, 1)[0].is_resolved, 5))

            # 第一首播完後自動接下一首
            self.assertTrue(await self._wait_for(
                lambda: queue_manager.get_current_song(gid) is not None
                and queue_manager.get_current_song(gid).query == "song1b", 10))
            self.assertTrue(any(c and "正在播放" in c for c in channel.sent))
            self.assertGreater(vc.packets, 150)
            player.stop(vc, gid)
            self.assertTrue(await self._wait_for(lambda: vc.disconnected >= 1, 5))
        self._run(scenario())

    def test_expired_stream_is_retried(self):
        async def scenario():
            gid = "1002"
            player, channel, vc = await self._setup_player(gid)
            self.fail_first.add("bad")
            queue_manager.add_song_info(gid, SongInfo(url="", title="會失敗的歌", headers={}, query="bad"))
            await player.start_playing(vc, gid, channel)
            # 第一次 404 → 自動重新取得網址 → 正常播放
            self.assertTrue(await self._wait_for(lambda: player.get_state(gid).stream_retries >= 1, 10))
            self.assertTrue(await self._wait_for(lambda: player.get_position(gid) > 1.0, 10))
            self.assertEqual(queue_manager.get_current_song(gid).query, "bad")
            player.stop(vc, gid)
        self._run(scenario())

    def test_broken_song_in_loop_one_does_not_retry_forever(self):
        async def scenario():
            gid = "1009"
            player, channel, vc = await self._setup_player(gid)
            queue_manager.set_loop_mode(gid, LOOP_ONE)
            self.always_404.add("brokenloop")
            queue_manager.add_songs(gid, [
                SongInfo(url="", title="壞掉的歌", headers={}, query="brokenloop"),
                SongInfo(url="", title="好歌", headers={}, query="goodb"),
            ])
            await player.start_playing(vc, gid, channel)
            self.assertTrue(await self._wait_for(
                lambda: queue_manager.get_current_song(gid) is not None
                and queue_manager.get_current_song(gid).query == "goodb" and vc.is_playing(), 15))
            self.assertTrue(any(c and "串流連線失敗" in c for c in channel.sent))
            self.assertLessEqual(sum(1 for c in self.calls if c.endswith("brokenloop")), 3)
            player.stop(vc, gid)
        self._run(scenario())

    def test_mid_song_cut_resumes_from_position(self):
        async def scenario():
            gid = "1010"
            player, channel, vc = await self._setup_player(gid)
            queue_manager.add_song_info(gid, SongInfo(url="", title="斷線的歌", headers={}, query="cut1"))
            await player.start_playing(vc, gid, channel)
            # 4 秒時串流斷掉 → 重新取得網址並從約 4 秒處接著播
            self.assertTrue(await self._wait_for(lambda: player.get_state(gid).stream_retries == 1, 10))
            self.assertTrue(await self._wait_for(lambda: player.get_position(gid) > 5, 10))
            self.assertEqual(queue_manager.get_current_song(gid).query, "cut1")
            self.assertGreaterEqual(player.get_state(gid).source.start_offset, 3.5)
            self.assertFalse(any(c and "無法播放" in c for c in channel.sent))
            player.stop(vc, gid)
        self._run(scenario())

    def test_unplayable_song_is_skipped(self):
        async def scenario():
            gid = "1003"
            player, channel, vc = await self._setup_player(gid)
            self.always_fail.add("dead")
            queue_manager.add_songs(gid, [
                SongInfo(url="", title="下架的歌", headers={}, query="dead"),
                SongInfo(url="", title="正常的歌", headers={}, query="okb"),
            ])
            self.assertTrue(await player.start_playing(vc, gid, channel))
            self.assertEqual(queue_manager.get_current_song(gid).query, "okb")
            self.assertTrue(any(c and "無法播放" in c and "下架的歌" in c for c in channel.sent))
            player.stop(vc, gid)
        self._run(scenario())

    def test_all_fail_stops_cleanly(self):
        async def scenario():
            gid = "1004"
            player, channel, vc = await self._setup_player(gid)
            for i in range(3):
                self.always_fail.add(f"dead{i}")
            queue_manager.add_songs(gid, [SongInfo(url="", title=f"壞{i}", headers={}, query=f"dead{i}")
                                          for i in range(3)])
            self.assertFalse(await player.start_playing(vc, gid, channel))
            self.assertFalse(vc.is_playing())
            self.assertTrue(queue_manager.is_queue_empty(gid))
        self._run(scenario())

    def test_loop_one_skip_seek_volume(self):
        async def scenario():
            gid = "1005"
            player, channel, vc = await self._setup_player(gid)
            queue_manager.add_songs(gid, [
                SongInfo(url="", title="長歌", headers={}, query="long1"),
                SongInfo(url="", title="下一首", headers={}, query="nextb"),
            ])
            await player.start_playing(vc, gid, channel)
            queue_manager.set_loop_mode(gid, LOOP_ONE)

            # 跳轉
            await asyncio.sleep(0.5)
            ok, err = await player.seek(vc, gid, 8)
            self.assertTrue(ok, err)
            self.assertGreaterEqual(player.get_position(gid), 8)
            await asyncio.sleep(1.0)
            self.assertTrue(vc.is_playing())
            self.assertEqual(queue_manager.get_current_song(gid).query, "long1")

            # 音量
            self.assertEqual(player.set_volume(gid, 1.5), 1.5)
            self.assertAlmostEqual(player.get_state(gid).source.volume, 1.5)

            # 單曲循環：播完會重播同一首
            self.assertTrue(await self._wait_for(lambda: player.get_position(gid) < 2 and vc.is_playing(), 10))
            self.assertEqual(queue_manager.get_current_song(gid).query, "long1")

            # 單曲循環下 /skip 仍會換到下一首
            player.skip(vc)
            self.assertTrue(await self._wait_for(
                lambda: queue_manager.get_current_song(gid) is not None
                and queue_manager.get_current_song(gid).query == "nextb", 10))
            player.stop(vc, gid)
        self._run(scenario())

    def test_concurrent_start_does_not_double_play(self):
        async def scenario():
            gid = "1006"
            player, channel, vc = await self._setup_player(gid)
            self.resolve_delay = 0.2
            queue_manager.add_songs(gid, [SongInfo(url="", title=f"歌{i}", headers={}, query=f"c{i}")
                                          for i in range(3)])
            results = await asyncio.gather(*(player.start_playing(vc, gid, channel) for _ in range(5)))
            self.assertEqual(sum(1 for r in results if r), 1)
            self.assertEqual(queue_manager.queue_size(gid), 2)
            player.stop(vc, gid)
        self._run(scenario())

    def test_tts_is_interrupted_by_music(self):
        async def scenario():
            gid = "1007"
            player, channel, vc = await self._setup_player(gid)
            tts_source = discord.FFmpegPCMAudio(os.path.join(self.tmp, "long.mp3"))
            self.assertTrue(await player.play_exclusive(vc, gid, "tts", tts_source))
            self.assertTrue(vc.is_playing())
            self.assertFalse(player.is_busy(gid, vc))
            queue_manager.add_song_info(gid, SongInfo(url="", title="音樂", headers={}, query="musicb"))
            self.assertTrue(await player.start_playing(vc, gid, channel))
            await asyncio.sleep(0.5)
            self.assertIsNone(player.get_state(gid).exclusive)
            self.assertEqual(queue_manager.get_current_song(gid).query, "musicb")
            self.assertTrue(vc.is_playing())
            player.stop(vc, gid)
        self._run(scenario())

    def test_quiz_blocks_music_until_released(self):
        async def scenario():
            gid = "1008"
            player, channel, vc = await self._setup_player(gid)
            player.get_state(gid).text_channel = channel
            clip = discord.FFmpegPCMAudio(os.path.join(self.tmp, "a.mp3"))
            self.assertTrue(await player.play_exclusive(vc, gid, "quiz", clip))
            self.assertTrue(player.is_busy(gid, vc))
            queue_manager.add_song_info(gid, SongInfo(url="", title="排隊", headers={}, query="waitb"))
            self.assertFalse(await player.start_playing(vc, gid, channel))
            # 片段播完後仍維持 quiz 狀態，不會自動開始放歌
            self.assertTrue(await self._wait_for(lambda: not vc.is_playing(), 10))
            await asyncio.sleep(0.3)
            self.assertEqual(player.get_state(gid).exclusive, "quiz")
            self.assertFalse(vc.is_playing())
            # 遊戲結束釋放後，接著播放排隊的歌
            player.get_state(gid).exclusive = None
            await player._after_exclusive(vc, gid, "quiz", None)
            self.assertTrue(vc.is_playing())
            self.assertEqual(queue_manager.get_current_song(gid).query, "waitb")
            player.stop(vc, gid)
        self._run(scenario())


if __name__ == "__main__":
    unittest.main()
