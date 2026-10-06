import asyncio
import logging
import time
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.commands.music import song_from_data
from src.bot.queue import queue_manager
from src.data.storage import banned_keywords_manager, chat_settings_manager
from src.utils.ai_chat import get_chat_engine
from src.utils.filters import keyword_filter
from src.utils.format import format_duration, truncate
from src.utils.tts import synthesize, cleanup_file, ffmpeg_options
from src.utils.youtube import youtube_searcher

logger = logging.getLogger("MusicBot.chat")

USER_COOLDOWN = 2.5        # 同一個人連續觸發聊天的最短間隔（秒）
MAX_INPUT_CHARS = 1500


class ChatCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.player = bot.music_player
        self.engine = get_chat_engine()
        self._channel_locks: Dict[int, asyncio.Lock] = {}
        self._last_trigger: Dict[int, float] = {}
        self._offline_hint_shown: set = set()

    # ------------------------------------------------------------------ helpers
    def _lock(self, channel_id: int) -> asyncio.Lock:
        if channel_id not in self._channel_locks:
            self._channel_locks[channel_id] = asyncio.Lock()
        return self._channel_locks[channel_id]

    def _bot_name(self, guild: Optional[discord.Guild]) -> str:
        if guild and guild.me:
            return guild.me.display_name
        return self.bot.user.name if self.bot.user else "音樂機器人"

    def _music_context(self, guild: discord.Guild) -> str:
        guild_id = str(guild.id)
        current = queue_manager.get_current_song(guild_id)
        voice_client = guild.voice_client
        if current and voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            status = "暫停中" if voice_client.is_paused() else "正在播放"
            return f"{status}「{current.title}」，待播 {queue_manager.queue_size(guild_id)} 首"
        return "目前沒有播放音樂"

    def _make_tool_handler(self, member: discord.Member, channel):
        guild = member.guild
        guild_id = str(guild.id)

        async def handler(name: str, args: Dict[str, Any]) -> str:
            voice_client = guild.voice_client
            if name == "get_music_status":
                current = queue_manager.get_current_song(guild_id)
                if not current or not voice_client or not (voice_client.is_playing() or voice_client.is_paused()):
                    return "目前沒有播放任何歌曲。"
                pos = format_duration(self.player.get_position(guild_id))
                lines = [f"正在播放：{current.title}（{pos} / {format_duration(current.duration)}，點歌者 {current.requester}）"]
                upcoming = queue_manager.peek(guild_id, 5)
                if upcoming:
                    lines.append("接下來：" + "、".join(s.title for s in upcoming))
                lines.append(f"待播共 {queue_manager.queue_size(guild_id)} 首")
                return "\n".join(lines)

            if name == "control_music":
                action = args.get("action")
                if not voice_client or not voice_client.is_connected():
                    return "機器人不在語音頻道中，沒有音樂可以控制。"
                if action == "skip":
                    return "已跳過目前歌曲。" if self.player.skip(voice_client) else "目前沒有在播放音樂。"
                if action == "pause":
                    return "已暫停。" if self.player.pause(voice_client) else "目前沒有在播放音樂。"
                if action == "resume":
                    return "繼續播放。" if self.player.resume(voice_client) else "音樂沒有暫停。"
                if action == "stop":
                    self.player.stop(voice_client, guild_id)
                    return "已停止播放並清空待播清單。"
                if action == "shuffle":
                    count = queue_manager.shuffle(guild_id)
                    return f"已打亂 {count} 首待播歌曲。" if count else "待播清單是空的。"
                return f"不支援的動作：{action}"

            if name == "play_music":
                queries = [q for q in (args.get("queries") or []) if isinstance(q, str) and q.strip()][:5]
                if not queries:
                    return "沒有提供歌曲。"
                if not member.voice or not member.voice.channel:
                    return f"{member.display_name} 不在語音頻道中，需要先加入語音頻道才能放歌。"
                banned = banned_keywords_manager.get_keywords(guild_id)
                vc = await self.player.connect_member(member)
                if not vc:
                    return "無法連接到語音頻道。"
                results = []
                for query in queries:
                    if keyword_filter.contains_banned(query, banned):
                        results.append(f"「{query}」含有被禁止的關鍵字，已略過")
                        continue
                    data, error = await youtube_searcher.search(query)
                    if error:
                        results.append(f"「{query}」失敗：{error}")
                        continue
                    busy = self.player.is_busy(guild_id, vc)
                    queue_manager.add_song_info(guild_id, song_from_data(data, member, query))
                    if busy:
                        results.append(f"已加入待播：{data['title']}")
                    else:
                        results.append(f"開始播放：{data['title']}")
                        await self.player.start_playing(vc, guild_id, channel)
                return "\n".join(results)

            return f"未知的工具：{name}"

        return handler

    async def _speak(self, guild: discord.Guild, text: str, member: Optional[discord.Member] = None) -> str:
        """在語音頻道朗讀文字。回傳空字串表示成功，否則為錯誤訊息。"""
        guild_id = str(guild.id)
        voice_client = guild.voice_client
        if voice_client is None or not voice_client.is_connected():
            if member is None:
                return "機器人不在語音頻道中！"
            voice_client = await self.player.connect_member(member)
            if voice_client is None:
                return "請先加入語音頻道！"
        state = self.player.get_state(guild_id)
        if state.exclusive == "quiz":
            return "猜歌遊戲進行中，等遊戲結束再說話吧！"
        if voice_client.is_playing() or voice_client.is_paused():
            if state.exclusive == "tts":
                await self.player.stop_exclusive(voice_client, guild_id)
            else:
                return "正在播放音樂中，不打擾大家聽歌 🎵"

        path = await synthesize(text)
        if not path:
            return "語音合成失敗了，等一下再試試看 😢"

        try:
            source = discord.FFmpegPCMAudio(path, **ffmpeg_options())
            source = discord.PCMVolumeTransformer(source, volume=min(1.0, max(0.1, state.volume)))
        except Exception as e:
            cleanup_file(path)
            return f"無法播放語音：{e}"

        async def on_end():
            cleanup_file(path)

        if not await self.player.play_exclusive(voice_client, guild_id, "tts", source, on_end=on_end):
            cleanup_file(path)
            return "現在不方便說話（正在播放其他聲音）。"
        return ""

    async def _respond(self, channel, guild: discord.Guild, author: discord.Member, text: str) -> str:
        text = text.strip()[:MAX_INPUT_CHARS]
        async with self._lock(channel.id):
            answer = await self.engine.reply(
                channel.id,
                author.display_name,
                text,
                self._bot_name(guild),
                tool_handler=self._make_tool_handler(author, channel),
                context=self._music_context(guild),
            )
        if chat_settings_manager.voice_enabled(str(guild.id)):
            vc = guild.voice_client
            if vc and vc.is_connected() and author.voice and author.voice.channel == vc.channel:
                error = await self._speak(guild, answer)
                if error:
                    logger.debug("朗讀略過：%s", error)
        return answer

    def _offline_hint(self, guild_id: int) -> str:
        if self.engine.online or guild_id in self._offline_hint_shown:
            return ""
        self._offline_hint_shown.add(guild_id)
        return "\n-# 💡 目前是簡易聊天模式。管理員在 `.env` 加入 `ANTHROPIC_API_KEY=...` 並重新啟動後，就能使用 AI 聊天！"

    # ------------------------------------------------------------------ slash commands
    @app_commands.command(name="chat", description="跟機器人聊天 💬")
    @app_commands.describe(message="想說的話")
    async def chat(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer(thinking=True)
        answer = await self._respond(interaction.channel, interaction.guild, interaction.user, message)
        header = f"> **{interaction.user.display_name}**：{truncate(message, 200)}\n"
        await interaction.followup.send(
            truncate(header + answer + self._offline_hint(interaction.guild.id), 2000),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="chat-reset", description="清除機器人在這個頻道的聊天記憶")
    async def chat_reset(self, interaction: discord.Interaction):
        self.engine.reset(interaction.channel.id)
        await interaction.response.send_message("🧹 好的，我把這個頻道的對話忘光光了！")

    @app_commands.command(name="chat-channel", description="設定這個頻道是否自動回覆所有訊息（不需 @機器人）")
    @app_commands.describe(enabled="開啟或關閉")
    @app_commands.default_permissions(manage_channels=True)
    async def chat_channel(self, interaction: discord.Interaction, enabled: bool):
        chat_settings_manager.set_chat_channel(str(interaction.guild.id), interaction.channel.id, enabled)
        if enabled:
            await interaction.response.send_message("💬 已開啟！在這個頻道說話我都會回應，大家來聊天吧～")
        else:
            await interaction.response.send_message("🔕 已關閉自動聊天。想找我的話可以 @我 或用 `/chat`！")

    @app_commands.command(name="chat-voice", description="設定聊天回覆是否同時在語音頻道念出來（繁中語音）")
    @app_commands.describe(enabled="開啟或關閉")
    @app_commands.default_permissions(manage_channels=True)
    async def chat_voice(self, interaction: discord.Interaction, enabled: bool):
        chat_settings_manager.set_voice(str(interaction.guild.id), enabled)
        if enabled:
            await interaction.response.send_message(
                "🗣️ 已開啟語音回覆！當我在語音頻道且沒在放歌時，會把聊天回覆念給你聽。"
            )
        else:
            await interaction.response.send_message("🔇 已關閉語音回覆。")

    @app_commands.command(name="say", description="讓機器人在語音頻道用繁體中文念出一段話 🗣️")
    @app_commands.describe(text="要念的內容")
    async def say(self, interaction: discord.Interaction, text: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        if keyword_filter.contains_banned(text, banned_keywords_manager.get_keywords(guild_id)):
            await interaction.response.send_message("內容包含被禁止的關鍵字！", ephemeral=True)
            return
        await interaction.response.defer()
        error = await self._speak(interaction.guild, text, interaction.user)
        if error:
            await interaction.followup.send(error)
        else:
            await interaction.followup.send(f"🗣️ {interaction.user.display_name} 讓我說：{truncate(text, 300)}",
                                            allowed_mentions=discord.AllowedMentions.none())

    # ------------------------------------------------------------------ listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not message.content:
            return
        if message.content.startswith(("!", "/")):
            return
        fun = self.bot.get_cog("FunCommands")
        if fun is not None and fun.is_quiz_channel(message.channel.id):
            return  # 猜歌遊戲中不要插話

        me = message.guild.me
        mentioned = me is not None and me.mentioned_in(message) and not message.mention_everyone
        replied_to_me = (
            message.reference is not None
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.bot.user.id
        )
        auto_channel = chat_settings_manager.is_chat_channel(str(message.guild.id), message.channel.id)
        if not (mentioned or replied_to_me or auto_channel):
            return

        text = message.clean_content
        if me is not None:
            text = text.replace(f"@{me.display_name}", "").strip()
        if not text:
            text = "（叫了你一聲）"

        now = time.monotonic()
        if now - self._last_trigger.get(message.author.id, 0) < USER_COOLDOWN:
            # 太快了就先記下來當上下文，不回覆
            self.engine.remember(message.channel.id, message.author.display_name, text)
            return
        self._last_trigger[message.author.id] = now

        try:
            async with message.channel.typing():
                answer = await self._respond(message.channel, message.guild, message.author, text)
            answer += self._offline_hint(message.guild.id)
            await message.reply(truncate(answer, 2000), mention_author=False,
                                allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            logger.warning("聊天回覆失敗: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChatCommands(bot))
