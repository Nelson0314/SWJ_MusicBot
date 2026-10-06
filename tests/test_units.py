"""基本單元測試：python -m unittest discover -s tests"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot.queue import QueueManager, SongInfo, LOOP_ONE, LOOP_ALL  # noqa: E402
from src.utils import youtube  # noqa: E402
from src.utils.format import format_duration, parse_timestamp, progress_bar  # noqa: E402
from src.bot.commands.fun import is_correct_guess  # noqa: E402
from src.utils.tts import clean_for_speech, _split_chunks  # noqa: E402
from src.utils.audio import build_ffmpeg_options  # noqa: E402


def song(title, url="u"):
    return SongInfo(url=url, title=title, headers={})


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.qm = QueueManager()
        self.g = "1"

    def test_legacy_add_song_signature(self):
        s = self.qm.add_song(self.g, "http://x", "標題", {"User-Agent": "a"}, "某人")
        self.assertEqual(s.requester, "某人")
        self.assertEqual(self.qm.get_next(self.g).title, "標題")
        self.assertIsNone(self.qm.get_next(self.g))
        self.assertIsNone(self.qm.get_current_song(self.g))

    def test_loop_one_and_force_advance(self):
        self.qm.add_songs(self.g, [song("A"), song("B")])
        self.qm.set_loop_mode(self.g, LOOP_ONE)
        self.assertEqual(self.qm.get_next(self.g).title, "A")
        self.assertEqual(self.qm.get_next(self.g).title, "A")
        self.assertEqual(self.qm.get_next(self.g, force_advance=True).title, "B")

    def test_loop_all(self):
        self.qm.add_songs(self.g, [song("A"), song("B")])
        self.qm.set_loop_mode(self.g, LOOP_ALL)
        titles = [self.qm.get_next(self.g).title for _ in range(5)]
        self.assertEqual(titles, ["A", "B", "A", "B", "A"])

    def test_drop_current_not_looped(self):
        self.qm.add_songs(self.g, [song("A"), song("B")])
        self.qm.set_loop_mode(self.g, LOOP_ALL)
        self.qm.get_next(self.g)
        self.qm.drop_current(self.g)
        self.assertEqual(self.qm.get_next(self.g).title, "B")
        self.assertEqual([s.title for s in self.qm.get_queue_list(self.g)], [])

    def test_remove_move_skipto(self):
        self.qm.add_songs(self.g, [song(c) for c in "ABCDE"])
        self.assertEqual(self.qm.remove(self.g, 2).title, "B")
        self.assertIsNone(self.qm.remove(self.g, 99))
        self.qm.move(self.g, 4, 1)  # E 到最前面
        self.assertEqual([s.title for s in self.qm.get_queue_list(self.g)], ["E", "A", "C", "D"])
        self.assertTrue(self.qm.skip_to(self.g, 3))
        self.assertEqual(self.qm.get_next(self.g).title, "C")

    def test_history(self):
        self.qm.add_songs(self.g, [song("A"), song("B")])
        self.qm.get_next(self.g)
        self.qm.get_next(self.g)
        self.assertEqual([s.title for s in self.qm.get_history(self.g)], ["A"])

    def test_needs_refresh(self):
        s = SongInfo(url="", title="x", headers={})
        self.assertTrue(s.needs_refresh())
        s.url = "http://a"
        s.expires = time.time() + 3600
        self.assertFalse(s.needs_refresh())
        s.expires = time.time() + 60
        self.assertTrue(s.needs_refresh())


class YouTubeHelperTests(unittest.TestCase):
    def test_playlist_url(self):
        self.assertTrue(youtube.is_playlist_url("https://www.youtube.com/playlist?list=PL123"))
        self.assertTrue(youtube.is_playlist_url("https://youtube.com/watch?v=abc&list=PLxyz"))
        self.assertFalse(youtube.is_playlist_url("https://youtube.com/watch?v=abc&list=RDabc"))
        self.assertFalse(youtube.is_playlist_url("https://youtube.com/watch?v=abc"))
        self.assertFalse(youtube.is_playlist_url("周杰倫 晴天"))

    def test_stream_expiry(self):
        self.assertEqual(youtube.stream_expiry("https://x/videoplayback?expire=1700000000&a=b"), 1700000000)
        self.assertEqual(youtube.stream_expiry("https://x/videoplayback/expire/1700000001/a"), 1700000001)
        self.assertGreater(youtube.stream_expiry("https://x/a.mp3"), time.time())

    def test_friendly_error(self):
        self.assertIn("私人", youtube.friendly_error(Exception("ERROR: Private video")))
        self.assertTrue(youtube.friendly_error(Exception("boom")).startswith("搜尋時發生錯誤"))

    def test_extract_fallback_retries(self):
        searcher = youtube.YouTubeSearcher()
        calls = []

        def fake(target, opts):
            calls.append(opts.get("extractor_args"))
            if len(calls) == 1:
                raise Exception("Requested format is not available")
            return {"entries": [{"url": "http://ok", "title": "T"}]}

        searcher._sync_extract = fake
        track = searcher._extract_with_fallback("ytsearch1:x", None)
        self.assertEqual(track["url"], "http://ok")
        self.assertEqual(len(calls), 2)
        self.assertIn("youtube", calls[1])
        self.assertIsInstance(calls[1]["youtube"], dict)

    def test_extract_fatal_no_retry(self):
        searcher = youtube.YouTubeSearcher()
        calls = []

        def fake(target, opts):
            calls.append(1)
            raise Exception("ERROR: Private video")

        searcher._sync_extract = fake
        with self.assertRaises(Exception):
            searcher._extract_with_fallback("x", None)
        self.assertEqual(len(calls), 1)


class FormatTests(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(format_duration(0), "--:--")
        self.assertEqual(format_duration(65), "1:05")
        self.assertEqual(format_duration(3725), "1:02:05")
        self.assertEqual(parse_timestamp("1:30"), 90)
        self.assertEqual(parse_timestamp("1:02:03"), 3723)
        self.assertEqual(parse_timestamp("45"), 45)
        self.assertIsNone(parse_timestamp("abc"))
        self.assertIsNone(parse_timestamp("1:2:3:4"))
        self.assertIn("🔘", progress_bar(30, 120))

    def test_ffmpeg_options(self):
        opts = build_ffmpeg_options({"User-Agent": 'a"b'}, start=12.5, duration=20)
        self.assertTrue(opts["before_options"].startswith("-ss 12.50"))
        self.assertNotIn('a"b', opts["before_options"])
        self.assertIn("-t 20.00", opts["options"])
        legacy = build_ffmpeg_options({})
        self.assertEqual(legacy["options"], "-vn -filter:a 'volume=0.5'")


class QuizMatchTests(unittest.TestCase):
    def test_guesses(self):
        title = "周杰倫 Jay Chou【晴天 Sunny Day】Official MV"
        self.assertTrue(is_correct_guess("晴天", title))
        self.assertTrue(is_correct_guess("周杰倫", title))
        self.assertFalse(is_correct_guess("天", title))
        self.assertFalse(is_correct_guess("哈哈哈", title))
        title2 = "五月天 MAYDAY - 倔強 Stubborn (Official Live Video)"
        self.assertTrue(is_correct_guess("倔強", title2))
        self.assertTrue(is_correct_guess("mayday", title2))
        self.assertFalse(is_correct_guess("ok", title2))


class TTSTests(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(clean_for_speech("**你好** 🎵 (≧▽≦) https://a.com"), "你好")
        chunks = _split_chunks("一二三，" * 100, limit=50)
        self.assertTrue(all(len(c) <= 50 for c in chunks))
        self.assertEqual("".join(chunks), "一二三，" * 100)


class StorageTests(unittest.TestCase):
    def test_atomic_and_url(self):
        from src.data import storage
        with tempfile.TemporaryDirectory() as d:
            old_cwd = os.getcwd()
            os.chdir(d)
            try:
                pm = storage.PlaylistManager()
                self.assertTrue(pm.create_playlist("g", "我的歌"))
                pm.add_song("g", "我的歌", "標題", "關鍵字")
                pm.add_song("g", "我的歌", "標題2", "關鍵字2", url="https://youtu.be/x")
                self.assertTrue(pm.set_song_url("g", "我的歌", "關鍵字", "https://youtu.be/y"))
                pm2 = storage.PlaylistManager()
                songs = pm2.get_playlist("g", "我的歌")
                self.assertEqual(songs[0], {"title": "標題", "query": "關鍵字", "url": "https://youtu.be/y"})
                self.assertEqual(songs[1]["url"], "https://youtu.be/x")
                long_title = "長" * 150
                pm2.add_song("g", "我的歌", long_title, "q")
                self.assertTrue(pm2.remove_song("g", "我的歌", long_title[:100]))
                # 損毀的檔案會被備份而不是直接覆蓋
                with open(storage.PLAYLISTS_FILE, "w", encoding="utf-8") as f:
                    f.write("{broken")
                pm3 = storage.PlaylistManager()
                self.assertEqual(pm3.get_all_playlists("g"), {})
                self.assertTrue(os.path.exists(storage.PLAYLISTS_FILE + ".corrupted"))
                stats = storage.StatsManager()
                stats.record_play("g", "A", "u1")
                stats.record_play("g", "A", "u2")
                stats.flush()
                self.assertEqual(storage.StatsManager().top_songs("g"), [("A", 2)])
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
