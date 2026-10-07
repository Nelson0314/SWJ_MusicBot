import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Callable, Awaitable

import discord

from src.bot.queue import queue_manager, SongInfo, LOOP_OFF, LOOP_ONE, LOOP_ALL
from src.data.storage import playlist_manager, stats_manager
from src.utils.audio import build_ffmpeg_options
from src.utils.format import format_duration, progress_bar, truncate
from src.utils.youtube import youtube_searcher

logger = logging.getLogger("MusicBot.player")

FRAME_SECONDS = 0.02            # discord.py 每次 read() 是 20ms 的音訊
QUICK_FAIL_SECONDS = 3.0        # 播放不到這麼久就結束，視為串流失敗
MAX_STREAM_RETRIES = 2          # 同一首歌串流失敗時重新取得網址的次數
PREMATURE_END_MARGIN = 5.0      # 比歌曲長度少這麼多秒就結束，視為中途斷線
MAX_CONSECUTIVE_FAILURES = 5    # 連續幾首無法載入就停止
PREFETCH_COUNT = 2              # 預先解析後面幾首
ALONE_TIMEOUT = 60              # 語音頻道沒人多久後離開
IDLE_TIMEOUT = 180              # 只講話 (TTS) 沒放音樂時，閒置多久後離開

LOOP_LABELS = {LOOP_OFF: "關閉", LOOP_ONE: "單曲循環", LOOP_ALL: "清單循環"}


class _TrackedMixin:
    """記錄已播放的 frame 數，用來計算播放進度。"""
    start_offset: float = 0.0
    frames: int = 0

    @property
    def position(self) -> float:
        return self.start_offset + self.frames * FRAME_SECONDS

    @property
    def played_seconds(self) -> float:
        return self.frames * FRAME_SECONDS


class TrackedVolumeSource(_TrackedMixin, discord.PCMVolumeTransformer):
    def __init__(self, original: discord.AudioSource, volume: float = 1.0, start_offset: float = 0.0):
        super().__init__(original, volume=volume)
        self.start_offset = start_offset
        self.frames = 0

    def read(self) -> bytes:
        data = super().read()
        if data:
            self.frames += 1
        return data


class TrackedPlainSource(_TrackedMixin, discord.AudioSource):
    """PCMVolumeTransformer 無法使用時（例如缺少 audioop）的備案，不支援調整音量。"""

    def __init__(self, original: discord.AudioSource, volume: float = 1.0, start_offset: float = 0.0):
        self.original = original
        self.volume = volume
        self.start_offset = start_offset
        self.frames = 0

    def read(self) -> bytes:
        data = self.original.read()
        if data:
            self.frames += 1
        return data

    def is_opus(self) -> bool:
        return self.original.is_opus()

    def cleanup(self) -> None:
        self.original.cleanup()

    @property
    def _current_error(self):
        return getattr(self.original, "_current_error", None)


def make_tracked_source(original: discord.AudioSource, volume: float, start_offset: float = 0.0):
    try:
        return TrackedVolumeSource(original, volume=volume, start_offset=start_offset)
    except Exception as e:  # pragma: no cover - 只有環境缺套件時才會發生
        logger.warning("無法使用音量控制，改用一般音源：%s", e)
        return TrackedPlainSource(original, volume=volume, start_offset=start_offset)


@dataclass
class GuildPlayerState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    volume: float = 1.0
    source: Optional[_TrackedMixin] = None
    text_channel: Optional[discord.abc.Messageable] = None
    generation: int = 0
    skip_requested: bool = False
    stream_retries: int = 0
    exclusive: Optional[str] = None      # "tts" / "quiz"：暫時佔用語音
    prefetch_task: Optional[asyncio.Task] = None
    alone_task: Optional[asyncio.Task] = None
    idle_task: Optional[asyncio.Task] = None
    transitioning: bool = False


class MusicPlayer:
    def __init__(self, bot):
        self.bot = bot
        self._last_messages: Dict[tuple, discord.Message] = {}
        self._last_views: Dict[tuple, discord.ui.View] = {}
        self._states: Dict[str, GuildPlayerState] = {}

    # ------------------------------------------------------------------ state
    def get_state(self, guild_id: str) -> GuildPlayerState:
        state = self._states.get(guild_id)
        if state is None:
            state = GuildPlayerState()
            self._states[guild_id] = state
        return state

    def is_busy(self, guild_id: str, voice_client: Optional[discord.VoiceClient]) -> bool:
        """是否正在播放（或正在切換到下一首）。"""
        state = self.get_state(guild_id)
        if state.transitioning or state.exclusive == "quiz":
            return True
        return bool(voice_client and (voice_client.is_playing() or voice_client.is_paused())
                    and state.exclusive is None)

    def get_position(self, guild_id: str) -> float:
        state = self.get_state(guild_id)
        return state.source.position if state.source else 0.0

    # ------------------------------------------------------------------ connect
    async def connect_to_channel(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        return await self.connect_member(interaction.user)

    async def connect_member(self, member: discord.Member) -> Optional[discord.VoiceClient]:
        """連到該成員所在的語音頻道（必要時移動過去）。"""
        if not getattr(member, "voice", None) or not member.voice.channel:
            return None

        guild = member.guild
        voice_channel = member.voice.channel
        voice_client = guild.voice_client

        try:
            if voice_client is None or not voice_client.is_connected():
                if voice_client is not None:
                    # 殘留的斷線 client，先清掉再重新連線
                    try:
                        await voice_client.disconnect(force=True)
                    except Exception:
                        pass
                voice_client = await voice_channel.connect(timeout=20, reconnect=True, self_deaf=True)
            elif voice_channel != voice_client.channel:
                await voice_client.move_to(voice_channel)
        except (asyncio.TimeoutError, discord.ClientException, discord.HTTPException) as e:
            logger.warning("連接語音頻道失敗: %s", e)
            voice_client = guild.voice_client
            if voice_client is None or not voice_client.is_connected():
                return None

        self._cancel_task(self.get_state(str(guild.id)), "idle_task")
        return voice_client

    # ------------------------------------------------------------------ resolve
    async def prepare_song(self, song: SongInfo, guild_id: str, force: bool = False):
        """確保歌曲有可用的串流網址。回傳 (成功與否, 錯誤訊息)。"""
        if not force and not song.needs_refresh():
            return True, None

        data, error = await youtube_searcher.search(song.lookup, force_refresh=force)
        if error and song.query and song.query != song.lookup:
            # 影片網址失效時退回用關鍵字搜尋
            data, error = await youtube_searcher.search(song.query, force_refresh=force)
        if error or not data:
            return False, error or "找不到這首歌！"

        song.apply_resolved(data)
        if song.source_playlist and song.query and song.webpage_url:
            try:
                playlist_manager.set_song_url(guild_id, song.source_playlist, song.query, song.webpage_url)
            except OSError as e:
                logger.warning("無法更新播放清單網址: %s", e)
        return True, None

    def _schedule_prefetch(self, guild_id: str):
        state = self.get_state(guild_id)
        if state.prefetch_task and not state.prefetch_task.done():
            return

        async def _prefetch():
            for song in queue_manager.peek(guild_id, PREFETCH_COUNT):
                if song.needs_refresh():
                    ok, err = await self.prepare_song(song, guild_id)
                    if not ok:
                        logger.info("預先載入失敗 %s: %s", song.title, err)

        state.prefetch_task = self.bot.loop.create_task(_prefetch())

    # ------------------------------------------------------------------ playback
    def _start_source(self, voice_client: discord.VoiceClient, song: SongInfo, guild_id: str,
                      channel, start_at: float = 0.0):
        state = self.get_state(guild_id)
        ffmpeg_opts = build_ffmpeg_options(song.headers, start=start_at)
        source = make_tracked_source(discord.FFmpegPCMAudio(song.url, **ffmpeg_opts), state.volume, start_at)

        state.generation += 1
        generation = state.generation
        state.source = source
        state.skip_requested = False

        loop = self.bot.loop

        def after_callback(error):
            # 這裡在音訊執行緒中執行，必須用 thread-safe 的方式回到事件迴圈
            if error:
                logger.warning("播放錯誤 %s: %s", song.title, error)
            coro = self._on_track_end(voice_client, guild_id, channel, generation, song, source, error)
            try:
                asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:
                coro.close()  # 事件迴圈已關閉（機器人關機中）

        voice_client.play(source, after=after_callback)

    async def _advance(self, voice_client: discord.VoiceClient, guild_id: str, channel, force: bool = False) -> bool:
        """從待播清單取出下一首並播放，跳過無法載入的歌曲。"""
        state = self.get_state(guild_id)
        failures = 0
        state.transitioning = True
        try:
            while True:
                song = queue_manager.get_next(guild_id, force_advance=force)
                force = False
                if not song:
                    state.source = None
                    return False

                ok, error = await self.prepare_song(song, guild_id)
                if not voice_client.is_connected():
                    return False
                if ok:
                    try:
                        self._start_source(voice_client, song, guild_id, channel)
                    except discord.ClientException as e:
                        ok, error = False, str(e)
                if ok:
                    state.stream_retries = 0
                    stats_manager.record_play(guild_id, song.title, song.requester)
                    await self._send_now_playing(channel, song.title, song)
                    self._schedule_prefetch(guild_id)
                    return True

                failures += 1
                queue_manager.drop_current(guild_id)
                await self._safe_send(channel, f"⚠️ 無法播放 **{truncate(song.title, 80)}**：{error}，已自動跳過。")
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    await self._safe_send(channel, "連續多首歌曲無法載入，已停止播放。請稍後再試！")
                    return False
        finally:
            state.transitioning = False

    async def _on_track_end(self, voice_client, guild_id, channel, generation, song, source, error):
        state = self.get_state(guild_id)
        if generation != state.generation:
            return  # 已經有新的歌開始播了

        # 串流剛開始就結束 → 多半是網址過期或被擋；播到一半斷掉 → 多半是網路問題。
        # 兩種情況都重新取得網址，從中斷的位置接著播。
        quick_fail = source.played_seconds < QUICK_FAIL_SECONDS and (
            song.duration == 0 or source.position < song.duration - 1.5
        )
        cut_short = song.duration > 0 and source.position < song.duration - PREMATURE_END_MARGIN
        still_current = queue_manager.get_current_song(guild_id) is song
        if ((quick_fail or cut_short) and not state.skip_requested and still_current
                and voice_client.is_connected()):
            if state.stream_retries < MAX_STREAM_RETRIES:
                state.stream_retries += 1
                logger.info("串流提早結束（%.0f 秒），重新載入 %s (第 %d 次)",
                            source.position, song.title, state.stream_retries)
                async with state.lock:
                    if voice_client.is_playing() or voice_client.is_paused() or generation != state.generation:
                        return
                    ok, err = await self.prepare_song(song, guild_id, force=True)
                    if ok and voice_client.is_connected():
                        try:
                            self._start_source(voice_client, song, guild_id, channel, start_at=source.position)
                            return
                        except discord.ClientException:
                            pass
            if quick_fail and source.start_offset == 0:
                # 重試後還是完全播不出來：丟掉這首，避免單曲 / 清單循環時無限重試
                queue_manager.drop_current(guild_id)
                state.skip_requested = True
                await self._safe_send(channel, f"⚠️ 無法播放 **{truncate(song.title, 80)}**（串流連線失敗），已自動跳過。")

        await self._play_next(voice_client, guild_id, channel)

    async def _play_next(self, voice_client: discord.VoiceClient, guild_id: str, channel: discord.TextChannel):
        state = self.get_state(guild_id)
        async with state.lock:
            if voice_client.is_playing() or voice_client.is_paused():
                return
            if state.exclusive == "quiz":
                return
            force = state.skip_requested
            state.skip_requested = False
            played = await self._advance(voice_client, guild_id, channel, force=force)

        if not played:
            await self._clear_now_playing(channel)
            if voice_client.is_connected():
                await voice_client.disconnect()
            self._reset_state(guild_id)

    async def start_playing(self, voice_client: discord.VoiceClient, guild_id: str, channel: discord.TextChannel):
        state = self.get_state(guild_id)
        state.text_channel = channel
        if state.exclusive == "tts" and voice_client.is_playing():
            # 音樂優先：打斷正在朗讀的語音
            await self.stop_exclusive(voice_client, guild_id)
        if state.exclusive == "quiz":
            return False

        async with state.lock:
            if voice_client.is_playing() or voice_client.is_paused():
                return False
            self._cancel_task(state, "idle_task")
            return await self._advance(voice_client, guild_id, channel)

    # ------------------------------------------------------------------ now playing UI
    def build_now_playing_embed(self, guild_id: str, song: SongInfo, include_progress: bool = True) -> discord.Embed:
        embed = discord.Embed(
            title=truncate(song.title, 250),
            url=song.webpage_url or None,
            color=discord.Color.from_rgb(255, 82, 82),
        )
        embed.set_author(name="🎵 正在播放")
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        if include_progress:
            embed.description = progress_bar(self.get_position(guild_id), song.duration)
        elif song.duration:
            embed.description = f"長度 `{format_duration(song.duration)}`"
        if song.uploader:
            embed.add_field(name="頻道", value=truncate(song.uploader, 100), inline=True)
        if song.requester:
            embed.add_field(name="點歌者", value=truncate(song.requester, 100), inline=True)
        state = self.get_state(guild_id)
        loop_mode = queue_manager.get_loop_mode(guild_id)
        upcoming = queue_manager.peek(guild_id, 1)
        footer = f"音量 {int(state.volume * 100)}% · 循環 {LOOP_LABELS.get(loop_mode, '關閉')} · 待播 {queue_manager.queue_size(guild_id)} 首"
        if upcoming:
            footer += f" · 下一首：{truncate(upcoming[0].title, 40)}"
        embed.set_footer(text=footer)
        return embed

    async def _send_now_playing(self, channel: discord.TextChannel, title: str, song: Optional[SongInfo] = None):
        if channel is None:
            return
        key = (channel.guild.id, channel.id)

        await self._clear_now_playing(channel)

        try:
            if song is not None:
                from src.bot.views import NowPlayingView  # 避免循環匯入
                guild_id = str(channel.guild.id)
                embed = self.build_now_playing_embed(guild_id, song, include_progress=False)
                view = NowPlayingView(self, guild_id)
                msg = await channel.send(f"正在播放: **{title}**", embed=embed, view=view)
                self._last_views[key] = view
            else:
                msg = await channel.send(f"正在播放: **{title}**")
        except discord.HTTPException as e:
            logger.warning("無法發送正在播放訊息: %s", e)
            return
        self._last_messages[key] = msg

    async def _clear_now_playing(self, channel):
        if channel is None or getattr(channel, "guild", None) is None:
            return
        key = (channel.guild.id, channel.id)
        view = self._last_views.pop(key, None)
        if view is not None:
            view.stop()  # 釋放按鈕監聽，避免長時間播放後佔用記憶體
        old = self._last_messages.pop(key, None)
        if old is not None:
            try:
                await old.delete()
            except discord.HTTPException:
                pass

    @staticmethod
    async def _safe_send(channel, content: str):
        if channel is None:
            return
        try:
            await channel.send(content)
        except discord.HTTPException as e:
            logger.warning("發送訊息失敗: %s", e)

    # ------------------------------------------------------------------ controls
    def skip(self, voice_client: discord.VoiceClient) -> bool:
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            if voice_client.guild:
                self.get_state(str(voice_client.guild.id)).skip_requested = True
            voice_client.stop()
            return True
        return False

    def pause(self, voice_client: discord.VoiceClient) -> bool:
        if voice_client and voice_client.is_playing():
            voice_client.pause()
            return True
        return False

    def resume(self, voice_client: discord.VoiceClient) -> bool:
        if voice_client and voice_client.is_paused():
            voice_client.resume()
            return True
        return False

    def stop(self, voice_client: discord.VoiceClient, guild_id: str) -> bool:
        queue_manager.clear_queue(guild_id)
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            self.get_state(guild_id).skip_requested = True
            voice_client.stop()
            return True
        return False

    def set_volume(self, guild_id: str, volume: float) -> float:
        volume = max(0.0, min(2.0, volume))
        state = self.get_state(guild_id)
        state.volume = volume
        if state.source is not None and hasattr(state.source, "volume"):
            state.source.volume = volume
        return volume

    async def seek(self, voice_client: discord.VoiceClient, guild_id: str, position: float):
        """跳到指定秒數。回傳 (成功與否, 訊息)。"""
        state = self.get_state(guild_id)
        song = queue_manager.get_current_song(guild_id)
        if not voice_client or song is None or state.source is None or state.exclusive:
            return False, "目前沒有在播放音樂！"
        if not (voice_client.is_playing() or voice_client.is_paused()):
            return False, "目前沒有在播放音樂！"
        if song.duration and position >= song.duration:
            return False, f"超過歌曲長度（{format_duration(song.duration)}）！"

        async with state.lock:
            if song.needs_refresh():
                ok, err = await self.prepare_song(song, guild_id, force=True)
                if not ok:
                    return False, err
            was_paused = voice_client.is_paused()
            new_source = make_tracked_source(
                discord.FFmpegPCMAudio(song.url, **build_ffmpeg_options(song.headers, start=position)),
                state.volume,
                position,
            )
            old_source = state.source
            try:
                voice_client.source = new_source
            except (ValueError, TypeError) as e:
                new_source.cleanup()
                return False, f"無法跳轉：{e}"
            state.source = new_source
            if was_paused:
                voice_client.pause()
            # 延遲清理舊的 ffmpeg，避免播放執行緒還在讀取時被關掉
            if old_source is not None:
                self.bot.loop.call_later(1.0, old_source.cleanup)
        return True, None

    # ------------------------------------------------------------------ exclusive audio (TTS / 猜歌)
    async def play_exclusive(self, voice_client: discord.VoiceClient, guild_id: str, kind: str,
                             source: discord.AudioSource,
                             on_end: Optional[Callable[[], Awaitable[None]]] = None) -> bool:
        """暫時佔用語音頻道播放非音樂的聲音，結束後若有待播歌曲會自動接著播。"""
        state = self.get_state(guild_id)
        if voice_client.is_playing() or voice_client.is_paused():
            source.cleanup()
            return False
        state.exclusive = kind
        state.generation += 1
        self._cancel_task(state, "idle_task")
        loop = self.bot.loop

        def after_callback(error):
            if error:
                logger.warning("播放 %s 錯誤: %s", kind, error)
            coro = self._after_exclusive(voice_client, guild_id, kind, on_end)
            try:
                asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError:
                coro.close()

        try:
            voice_client.play(source, after=after_callback)
        except discord.ClientException:
            state.exclusive = None
            source.cleanup()
            return False
        return True

    async def _after_exclusive(self, voice_client, guild_id, kind, on_end):
        state = self.get_state(guild_id)
        if state.exclusive == kind and kind != "quiz":
            # 猜歌遊戲整場都佔用語音，由遊戲結束時自行釋放
            state.exclusive = None
        if on_end is not None:
            try:
                await on_end()
            except Exception:
                logger.exception("on_end 執行失敗")
        if state.exclusive is not None or not voice_client.is_connected():
            return
        if voice_client.is_playing() or voice_client.is_paused():
            return
        if not queue_manager.is_queue_empty(guild_id):
            await self.start_playing(voice_client, guild_id, state.text_channel)
        else:
            self._schedule_idle_disconnect(voice_client, guild_id)

    async def stop_exclusive(self, voice_client: discord.VoiceClient, guild_id: str, release: bool = True):
        state = self.get_state(guild_id)
        if state.exclusive and voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            if release:
                state.exclusive = None
            voice_client.stop()
            for _ in range(50):
                if not voice_client.is_playing():
                    break
                await asyncio.sleep(0.02)

    # ------------------------------------------------------------------ auto leave
    @staticmethod
    def _cancel_task(state: GuildPlayerState, attr: str):
        task = getattr(state, attr)
        if task and not task.done():
            task.cancel()
        setattr(state, attr, None)

    def _schedule_idle_disconnect(self, voice_client: discord.VoiceClient, guild_id: str):
        state = self.get_state(guild_id)
        self._cancel_task(state, "idle_task")

        async def _idle():
            await asyncio.sleep(IDLE_TIMEOUT)
            if voice_client.is_connected() and not voice_client.is_playing() and not voice_client.is_paused():
                await voice_client.disconnect()
                self._reset_state(guild_id)

        state.idle_task = self.bot.loop.create_task(_idle())

    def _reset_state(self, guild_id: str):
        state = self.get_state(guild_id)
        state.source = None
        state.exclusive = None
        state.skip_requested = False
        state.stream_retries = 0
        for attr in ("prefetch_task", "alone_task", "idle_task"):
            self._cancel_task(state, attr)

    async def handle_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        guild = member.guild
        guild_id = str(guild.id)
        voice_client = guild.voice_client
        state = self.get_state(guild_id)

        # 機器人自己被踢出 / 斷線：清空狀態
        if self.bot.user and member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                # 等一下再確認，避免網路短暫重連時誤清空待播清單
                await asyncio.sleep(2)
                vc = guild.voice_client
                if vc is not None and vc.is_connected():
                    return
                queue_manager.clear_queue(guild_id)
                await self._clear_now_playing(state.text_channel)
                self._reset_state(guild_id)
            return

        if voice_client is None or not voice_client.is_connected():
            return
        channel = voice_client.channel
        humans = [m for m in channel.members if not m.bot]
        if humans:
            self._cancel_task(state, "alone_task")
            return
        if state.alone_task and not state.alone_task.done():
            return

        async def _leave_when_alone():
            await asyncio.sleep(ALONE_TIMEOUT)
            vc = guild.voice_client
            if vc is None or not vc.is_connected():
                return
            if any(not m.bot for m in vc.channel.members):
                return
            queue_manager.clear_queue(guild_id)
            state.skip_requested = True
            await self._safe_send(state.text_channel, "👋 語音頻道裡沒有人了，我先離開囉！")
            await self._clear_now_playing(state.text_channel)
            await vc.disconnect()
            self._reset_state(guild_id)

        state.alone_task = self.bot.loop.create_task(_leave_when_alone())
