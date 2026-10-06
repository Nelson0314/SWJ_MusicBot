"""指令冒煙測試：用假的 Interaction 直接呼叫各個 slash 指令，確認不會出錯。

python -m unittest tests.test_commands_smoke
"""
import asyncio
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import test_player_e2e as e2e  # noqa: E402

FakeVoiceClient, FakeMessage, HAS_FFMPEG = e2e.FakeVoiceClient, e2e.FakeMessage, e2e.HAS_FFMPEG


class FakeResponse:
    def __init__(self, log):
        self.log = log
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, content=None, **kwargs):
        self._done = True
        self.log.append(("send", content, kwargs))

    async def defer(self, **kwargs):
        self._done = True

    async def edit_message(self, **kwargs):
        self._done = True
        self.log.append(("edit", kwargs.get("content"), kwargs))


class FakeFollowup:
    def __init__(self, log):
        self.log = log

    async def send(self, content=None, **kwargs):
        self.log.append(("followup", content, kwargs))
        return FakeMessage(content)


class FakeTextChannel:
    def __init__(self, guild, log):
        self.guild = guild
        self.id = 555
        self.log = log

    async def send(self, content=None, **kwargs):
        self.log.append(("channel", content, kwargs))
        return FakeMessage(content)

    def typing(self):
        class _T:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *a):
                return None
        return _T()


class FakeGuild:
    def __init__(self, gid, loop):
        self.id = gid
        self.voice_client = None
        self.me = SimpleNamespace(display_name="SWJ")
        self._loop = loop


class FakeVoiceChannel:
    def __init__(self, guild):
        self.guild = guild
        self.members = []

    async def connect(self, **kwargs):
        vc = FakeVoiceClient(self.guild._loop, self.guild)
        vc.channel = self
        self.guild.voice_client = vc
        return vc


def make_interaction(guild, channel, log, user_id=1, name="小明", in_voice=True, voice_channel=None):
    user = SimpleNamespace(
        id=user_id, display_name=name, mention=f"<@{user_id}>",
        voice=SimpleNamespace(channel=voice_channel) if in_voice else None, guild=guild,
    )
    inter = SimpleNamespace(
        user=user, guild=guild, channel=channel,
        response=FakeResponse(log), followup=FakeFollowup(log),
        namespace=SimpleNamespace(),
    )

    async def original_response():
        msg = FakeMessage("orig")

        async def add_reaction(e):
            return None

        async def edit(**kwargs):
            return None
        msg.add_reaction = add_reaction
        msg.edit = edit
        return msg
    inter.original_response = original_response
    return inter


@unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
class CommandSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        e2e.PlayerEndToEndTests.setUpClass()
        cls.base = e2e.PlayerEndToEndTests.base
        cls.cwd = os.getcwd()
        cls.datadir = tempfile.mkdtemp()
        os.chdir(cls.datadir)

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls.cwd)
        shutil.rmtree(cls.datadir, ignore_errors=True)
        e2e.PlayerEndToEndTests.tearDownClass()

    def test_commands(self):
        import discord
        from discord.ext import commands
        from src.bot.player import MusicPlayer
        from src.bot.queue import queue_manager
        from src.utils.youtube import youtube_searcher
        from src.bot.commands import music, playlist, fun, chat, moderation

        base = self.base

        def fake_extract(target, cookies_file):
            key = target.replace("ytsearch1:", "")
            return {"url": f"{base}/long.mp3", "title": f"YT {key}", "duration": 12,
                    "webpage_url": f"https://youtu.be/{abs(hash(key)) % 10000}", "http_headers": {}}

        async def fake_playlist(url, limit=500, cookies_file=None):
            return {"title": "測試清單", "entries": [
                {"title": f"清單 {i}", "url": f"https://youtu.be/p{i}", "duration": 12} for i in range(250)]}, None

        async def fake_many(query, limit=5, cookies_file=None):
            return [{"title": f"結果{i}", "url": f"https://youtu.be/s{i}", "duration": 100, "uploader": "U"}
                    for i in range(limit)], None

        orig = (youtube_searcher._extract_with_fallback, youtube_searcher.extract_playlist,
                youtube_searcher.search_many)
        youtube_searcher._extract_with_fallback = fake_extract
        youtube_searcher.extract_playlist = fake_playlist
        youtube_searcher.search_many = fake_many
        youtube_searcher._cache.clear()

        async def scenario():
            loop = asyncio.get_running_loop()
            bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
            bot.music_player = MusicPlayer(bot)
            # 測試環境沒有登入，手動提供 loop 與 user
            bot.music_player.bot = SimpleNamespace(loop=loop, user=SimpleNamespace(id=999))
            mc = music.MusicCommands(bot)
            pc = playlist.PlaylistCommands(bot)
            fc = fun.FunCommands(bot)
            cc = chat.ChatCommands(bot)
            mod = moderation.ModerationCommands(bot)
            player = bot.music_player

            log = []
            guild = FakeGuild(777, loop)
            vch = FakeVoiceChannel(guild)
            channel = FakeTextChannel(guild, log)

            def inter(**kw):
                return make_interaction(guild, channel, log, voice_channel=vch, **kw)

            def last():
                return log[-1][1]

            # 不在語音頻道
            await mc.play.callback(mc, make_interaction(guild, channel, log, in_voice=False), "x")
            self.assertEqual(last(), "請先加入語音頻道！")

            await mc.play.callback(mc, inter(), "晴天")
            self.assertTrue(any(e[1] == "正在播放: **YT 晴天**" for e in log))
            vc = guild.voice_client
            self.assertTrue(vc.is_playing())

            await mc.play.callback(mc, inter(), "倔強")
            self.assertTrue(last().startswith("已加入待播清單: **YT 倔強**"))

            # 播放清單網址：一次加入 250 首
            t0 = time.monotonic()
            await mc.play.callback(mc, inter(), "https://www.youtube.com/playlist?list=PLabc")
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertIn("250 首", last())
            self.assertEqual(queue_manager.queue_size("777"), 251)

            await mc.show_queue.callback(mc, inter(), 1)
            self.assertIn("**正在播放:** YT 晴天", last())
            self.assertLessEqual(len(last()), 2000)
            await mc.show_queue.callback(mc, inter(), 3)
            self.assertIn("第 3/17 頁", last())

            await mc.now_playing.callback(mc, inter())
            self.assertIsNotNone(log[-1][2].get("embed"))

            await mc.volume.callback(mc, inter(), 50)
            self.assertIn("50%", last())
            await mc.loop.callback(mc, inter(), SimpleNamespace(value="all", name="清單循環"))
            await mc.shuffle.callback(mc, inter())
            self.assertIn("251", last())
            await mc.queue_move.callback(mc, inter(), 5, 1)
            await mc.queue_remove.callback(mc, inter(), 1)
            self.assertTrue(last().startswith("🗑️"))
            await mc.seek.callback(mc, inter(), "0:05")
            self.assertIn("0:05", last())
            await mc.seek.callback(mc, inter(), "abc")
            self.assertIn("時間格式錯誤", last())
            log.pop()
            await mc.history.callback(mc, inter())
            await mc.top.callback(mc, inter())
            await mc.skipto.callback(mc, inter(), 3)
            self.assertTrue(last().startswith("⏭️"))
            await asyncio.sleep(1.0)
            self.assertTrue(vc.is_playing())

            # 搜尋 + 選擇
            await mc.search.callback(mc, inter(), "孤勇者")
            view = log[-1][2]["view"]
            select = view.children[0]
            select._values = ["2"]
            si = inter()
            await select.callback(si)
            self.assertTrue(any("結果2" in (e[1] or "") for e in log[-3:]))

            await mc.pause.callback(mc, inter())
            self.assertEqual(last(), "已暫停！")
            await mc.resume.callback(mc, inter())
            self.assertEqual(last(), "繼續播放！")
            await mc.skip.callback(mc, inter())
            self.assertEqual(last(), "已跳過！")
            await mc.clear_queue.callback(mc, inter())
            self.assertEqual(queue_manager.queue_size("777"), 0)

            # 播放清單
            await pc.create_playlist.callback(pc, inter(), "我的最愛")
            await pc.add_song.callback(pc, inter(), "我的最愛", "告五人 愛人錯過")
            self.assertIn("已將 **YT 告五人 愛人錯過** 加入 **我的最愛**", last())
            await pc.import_playlist.callback(pc, inter(), "大清單", "https://youtube.com/playlist?list=PLx")
            self.assertIn("匯入 250 首", last())
            await pc.import_playlist.callback(pc, inter(), "大清單", "https://youtube.com/playlist?list=PLx")
            self.assertIn("略過 250 首", last())
            await pc.show_playlist.callback(pc, inter(), "大清單")
            self.assertLessEqual(len(last()), 2000)
            await pc.list_playlists.callback(pc, inter())

            await mc.stop.callback(mc, inter())
            self.assertEqual(last(), "已停止播放並清空待播清單！")
            await asyncio.sleep(0.5)
            guild.voice_client = None  # 停止後機器人會離開

            t0 = time.monotonic()
            await pc.play_playlist.callback(pc, inter(), "大清單", True)
            self.assertLess(time.monotonic() - t0, 1.5)
            self.assertIn("250 首歌曲（隨機播放）", log[-1][1] if log[-1][0] == "followup" else
                          next(e[1] for e in reversed(log) if e[0] == "followup"))
            vc = guild.voice_client
            self.assertTrue(vc.is_playing())
            await pc.save_queue.callback(pc, inter(), "備份")
            self.assertIn("250 首", last())

            # 管理
            await mod.ban_keyword.callback(mod, inter(), "禁歌")
            await mc.play.callback(mc, inter(), "這是禁歌啦")
            self.assertEqual(last(), "這個關鍵字被禁止搜尋！")

            # 趣味指令
            for cb, args in [
                (fc.roll, (6, 3)), (fc.coin, ()), (fc.eight_ball, ("會不會下雨",)), (fc.fortune, ()),
                (fc.choose, ("麥當勞 肯德基",)), (fc.rate, ("鳳梨披薩",)), (fc.rps, ()),
                (fc.compatibility, (SimpleNamespace(id=2, display_name="小華"), None)),
                (fc.poll, ("晚餐吃什麼", "拉麵|火鍋|牛排")),
                (fc.hug, (SimpleNamespace(id=2, mention="<@2>"),)),
            ]:
                await cb.callback(fc, inter(), *args)
                self.assertTrue(log[-1][1] or log[-1][2].get("embed"))
            await fc.fortune.callback(fc, inter())
            first = log[-1][2]["embed"].title
            await fc.fortune.callback(fc, inter())
            self.assertEqual(first, log[-1][2]["embed"].title)

            # 聊天（離線模式）
            await cc.chat.callback(cc, inter(), "你好")
            self.assertIn("小明", last())
            await cc.chat_reset.callback(cc, inter())
            handler = cc._make_tool_handler(inter().user, channel)
            status = await handler("get_music_status", {})
            self.assertIn("正在播放", status)
            result = await handler("play_music", {"queries": ["稻香"]})
            self.assertIn("已加入待播：YT 稻香", result)
            self.assertIn("已跳過", await handler("control_music", {"action": "skip"}))

            player.stop(guild.voice_client, "777")
            await asyncio.sleep(0.3)
            return log

        try:
            log = asyncio.run(asyncio.wait_for(scenario(), 120))
        finally:
            (youtube_searcher._extract_with_fallback, youtube_searcher.extract_playlist,
             youtube_searcher.search_many) = orig
        errors = [e for e in log if e[1] and ("錯誤" in e[1] or "無法播放" in e[1])]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
