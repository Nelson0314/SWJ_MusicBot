import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.queue import queue_manager, SongInfo, LOOP_OFF, LOOP_ONE, LOOP_ALL
from src.bot.player import LOOP_LABELS
from src.bot.views import SearchView
from src.utils.youtube import youtube_searcher, is_playlist_url
from src.utils.filters import keyword_filter
from src.utils.format import format_duration, parse_timestamp, truncate
from src.utils.http import get_session
from src.data.storage import banned_keywords_manager, stats_manager

QUEUE_PAGE_SIZE = 15
PLAYLIST_IMPORT_LIMIT = 500


def song_from_data(data: dict, requester: discord.abc.User, query: str = "") -> SongInfo:
    return SongInfo(
        url=data["url"],
        title=data["title"],
        headers=data["headers"],
        requester=requester.display_name,
        requester_id=requester.id,
        query=query,
        webpage_url=data.get("webpage_url", ""),
        duration=data.get("duration", 0),
        thumbnail=data.get("thumbnail", ""),
        uploader=data.get("uploader", ""),
        expires=data.get("expires", 0),
    )


class MusicCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.player = bot.music_player

    # ------------------------------------------------------------------ helpers
    async def _require_voice(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        if not interaction.user.voice:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return None
        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            await interaction.response.send_message("機器人不在語音頻道中！")
            return None
        return voice_client

    async def _enqueue_and_start(self, interaction: discord.Interaction, voice_client: discord.VoiceClient,
                                 song: SongInfo):
        guild_id = str(interaction.guild.id)
        busy = self.player.is_busy(guild_id, voice_client)
        queue_manager.add_song_info(guild_id, song)

        if busy:
            position = queue_manager.queue_size(guild_id)
            extra = f"（第 {position} 首" + (f"，長度 {format_duration(song.duration)}）" if song.duration else "）")
            await interaction.followup.send(f"已加入待播清單: **{song.title}** {extra}")
        else:
            await interaction.followup.send(f"正在播放: **{song.title}**")
            await self.player.start_playing(voice_client, guild_id, interaction.channel)

    async def _play_playlist_url(self, interaction: discord.Interaction, voice_client: discord.VoiceClient, url: str):
        guild_id = str(interaction.guild.id)
        data, error = await youtube_searcher.extract_playlist(url, limit=PLAYLIST_IMPORT_LIMIT)
        if error:
            await interaction.followup.send(error)
            return

        songs = [
            SongInfo(
                url="",
                title=entry["title"],
                headers={},
                requester=interaction.user.display_name,
                requester_id=interaction.user.id,
                query=entry["url"],
                webpage_url=entry["url"],
                duration=entry["duration"],
            )
            for entry in data["entries"]
        ]
        busy = self.player.is_busy(guild_id, voice_client)
        queue_manager.add_songs(guild_id, songs)
        total = sum(s.duration for s in songs)
        total_text = f"，總長約 {format_duration(total)}" if total else ""
        await interaction.followup.send(
            f"📃 已從 **{truncate(data['title'], 80)}** 加入 {len(songs)} 首歌曲{total_text}！"
        )
        if not busy:
            await self.player.start_playing(voice_client, guild_id, interaction.channel)

    # ------------------------------------------------------------------ 原有指令
    @app_commands.command(name="play", description="播放音樂")
    @app_commands.describe(query="歌曲名稱或關鍵字")
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return

        await interaction.response.defer()

        guild_id = str(interaction.guild.id)
        banned = banned_keywords_manager.get_keywords(guild_id)
        if keyword_filter.contains_banned(query, banned):
            await interaction.followup.send("這個關鍵字被禁止搜尋！", ephemeral=True)
            return

        voice_client = await self.player.connect_to_channel(interaction)
        if not voice_client:
            await interaction.followup.send("無法連接到語音頻道！")
            return

        if is_playlist_url(query):
            await self._play_playlist_url(interaction, voice_client, query)
            return

        song_data, error = await youtube_searcher.search(query)
        if error:
            await interaction.followup.send(error)
            return

        await self._enqueue_and_start(interaction, voice_client, song_from_data(song_data, interaction.user, query))

    @app_commands.command(name="skip", description="跳過目前歌曲")
    async def skip(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if self.player.skip(voice_client):
            await interaction.response.send_message("已跳過！")
        else:
            await interaction.response.send_message("目前沒有在播放音樂！")

    @app_commands.command(name="pause", description="暫停播放")
    async def pause(self, interaction: discord.Interaction):
        voice_client = await self._require_voice(interaction)
        if not voice_client:
            return

        if self.player.pause(voice_client):
            await interaction.response.send_message("已暫停！")
        else:
            await interaction.response.send_message("目前沒有在播放音樂！")

    @app_commands.command(name="resume", description="繼續播放")
    async def resume(self, interaction: discord.Interaction):
        voice_client = await self._require_voice(interaction)
        if not voice_client:
            return

        if self.player.resume(voice_client):
            await interaction.response.send_message("繼續播放！")
        else:
            await interaction.response.send_message("音樂沒有暫停！")

    @app_commands.command(name="stop", description="停止播放並清空待播清單")
    async def stop(self, interaction: discord.Interaction):
        voice_client = await self._require_voice(interaction)
        if not voice_client:
            return

        guild_id = str(interaction.guild.id)
        self.player.stop(voice_client, guild_id)
        await interaction.response.send_message("已停止播放並清空待播清單！")

    @app_commands.command(name="queue", description="查看待播清單")
    @app_commands.describe(page="頁數（每頁 15 首）")
    async def show_queue(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 1000] = 1):
        guild_id = str(interaction.guild.id)
        songs = queue_manager.get_queue_list(guild_id)
        current = queue_manager.get_current_song(guild_id)

        if not songs and not current:
            await interaction.response.send_message("待播清單是空的！")
            return

        lines = []
        if current:
            position = format_duration(self.player.get_position(guild_id))
            length = format_duration(current.duration)
            lines.append(f"**正在播放:** {current.title} `{position} / {length}`")
            lines.append("")

        if songs:
            pages = max(1, (len(songs) + QUEUE_PAGE_SIZE - 1) // QUEUE_PAGE_SIZE)
            page = min(page, pages)
            start = (page - 1) * QUEUE_PAGE_SIZE
            lines.append("**待播清單:**")
            for i, song in enumerate(songs[start:start + QUEUE_PAGE_SIZE], start + 1):
                duration = f" `{format_duration(song.duration)}`" if song.duration else ""
                lines.append(f"{i}. {truncate(song.title, 80)}{duration}")

            remaining = len(songs) - (start + QUEUE_PAGE_SIZE)
            if remaining > 0:
                lines.append(f"... 還有 {remaining} 首歌曲")
            total = queue_manager.total_duration(guild_id)
            summary = f"共 {len(songs)} 首"
            if total:
                summary += f"，總長約 {format_duration(total)}"
            if pages > 1:
                summary += f"｜第 {page}/{pages} 頁"
            loop_mode = queue_manager.get_loop_mode(guild_id)
            if loop_mode != LOOP_OFF:
                summary += f"｜{LOOP_LABELS[loop_mode]}"
            lines.append(f"-# {summary}")

        await interaction.response.send_message(truncate("\n".join(lines), 2000))

    @app_commands.command(name="clear", description="清空待播清單")
    async def clear_queue(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        # 只清空待播的歌，正在播放的那首繼續播完
        queue_manager.get_queue(guild_id).clear_upcoming()
        await interaction.response.send_message("已清空待播清單！")

    @app_commands.command(name="leave", description="離開語音頻道")
    async def leave(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_connected():
            guild_id = str(interaction.guild.id)
            queue_manager.clear_queue(guild_id)
            self.player.get_state(guild_id).skip_requested = True
            await voice_client.disconnect()
            await interaction.response.send_message("已離開語音頻道！")
        else:
            await interaction.response.send_message("機器人不在語音頻道中！")

    # ------------------------------------------------------------------ 新指令
    @app_commands.command(name="nowplaying", description="查看正在播放的歌曲與進度")
    async def now_playing(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        current = queue_manager.get_current_song(guild_id)
        voice_client = interaction.guild.voice_client
        if not current or not voice_client or not (voice_client.is_playing() or voice_client.is_paused()):
            await interaction.response.send_message("目前沒有在播放音樂！")
            return
        embed = self.player.build_now_playing_embed(guild_id, current)
        if voice_client.is_paused():
            embed.set_author(name="⏸️ 已暫停")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="search", description="搜尋歌曲並從結果中選擇要播放的")
    @app_commands.describe(query="歌曲名稱或關鍵字")
    async def search(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        if keyword_filter.contains_banned(query, banned_keywords_manager.get_keywords(guild_id)):
            await interaction.response.send_message("這個關鍵字被禁止搜尋！", ephemeral=True)
            return

        await interaction.response.defer()
        results, error = await youtube_searcher.search_many(query, limit=8)
        if error:
            await interaction.followup.send(error)
            return

        async def on_choose(choice_interaction: discord.Interaction, choice: dict):
            if not choice_interaction.user.voice or not choice_interaction.user.voice.channel:
                await choice_interaction.followup.send("請先加入語音頻道！", ephemeral=True)
                return
            voice_client = await self.player.connect_to_channel(choice_interaction)
            if not voice_client:
                await choice_interaction.followup.send("無法連接到語音頻道！")
                return
            data, err = await youtube_searcher.search(choice["url"])
            if err:
                await choice_interaction.followup.send(err)
                return
            await self._enqueue_and_start(choice_interaction, voice_client,
                                          song_from_data(data, choice_interaction.user, choice["url"]))

        lines = [f"🔎 **{truncate(query, 80)}** 的搜尋結果："]
        for i, item in enumerate(results, 1):
            duration = f" `{format_duration(item['duration'])}`" if item.get("duration") else ""
            lines.append(f"{i}. {truncate(item['title'], 80)}{duration}")
        view = SearchView(interaction.user.id, results, on_choose)
        view.message = await interaction.followup.send("\n".join(lines), view=view, wait=True)

    @app_commands.command(name="volume", description="調整音量（0~200%，預設 100%）")
    @app_commands.describe(percent="音量百分比")
    async def volume(self, interaction: discord.Interaction, percent: Optional[app_commands.Range[int, 0, 200]] = None):
        guild_id = str(interaction.guild.id)
        state = self.player.get_state(guild_id)
        if percent is None:
            await interaction.response.send_message(f"🔊 目前音量：**{int(state.volume * 100)}%**")
            return
        if not interaction.user.voice:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return
        volume = self.player.set_volume(guild_id, percent / 100)
        icon = "🔇" if volume == 0 else "🔉" if volume < 0.7 else "🔊"
        await interaction.response.send_message(f"{icon} 音量已調整為 **{int(volume * 100)}%**")

    @app_commands.command(name="loop", description="設定循環模式")
    @app_commands.describe(mode="循環模式")
    @app_commands.choices(mode=[
        app_commands.Choice(name="關閉", value=LOOP_OFF),
        app_commands.Choice(name="單曲循環", value=LOOP_ONE),
        app_commands.Choice(name="清單循環", value=LOOP_ALL),
    ])
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        guild_id = str(interaction.guild.id)
        queue_manager.set_loop_mode(guild_id, mode.value)
        icons = {LOOP_OFF: "➡️", LOOP_ONE: "🔂", LOOP_ALL: "🔁"}
        await interaction.response.send_message(f"{icons[mode.value]} 循環模式：**{mode.name}**")

    @app_commands.command(name="shuffle", description="打亂待播清單")
    async def shuffle(self, interaction: discord.Interaction):
        count = queue_manager.shuffle(str(interaction.guild.id))
        if count:
            await interaction.response.send_message(f"🔀 已打亂 {count} 首待播歌曲！")
        else:
            await interaction.response.send_message("待播清單是空的！")

    @app_commands.command(name="queue-remove", description="從待播清單移除指定編號的歌曲")
    @app_commands.describe(position="歌曲在待播清單中的編號")
    async def queue_remove(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 10000]):
        song = queue_manager.remove(str(interaction.guild.id), position)
        if song:
            await interaction.response.send_message(f"🗑️ 已從待播清單移除 **{song.title}**")
        else:
            await interaction.response.send_message("找不到這個編號的歌曲！", ephemeral=True)

    @app_commands.command(name="queue-move", description="調整待播清單中歌曲的順序")
    @app_commands.describe(source="要移動的歌曲編號", target="移動到第幾首")
    async def queue_move(self, interaction: discord.Interaction,
                         source: app_commands.Range[int, 1, 10000], target: app_commands.Range[int, 1, 10000]):
        song = queue_manager.move(str(interaction.guild.id), source, target)
        if song:
            new_pos = min(target, queue_manager.queue_size(str(interaction.guild.id)))
            await interaction.response.send_message(f"↕️ 已把 **{song.title}** 移到第 {new_pos} 首")
        else:
            await interaction.response.send_message("找不到這個編號的歌曲！", ephemeral=True)

    @app_commands.command(name="skipto", description="直接跳到待播清單中的某首歌")
    @app_commands.describe(position="歌曲在待播清單中的編號")
    async def skipto(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 10000]):
        guild_id = str(interaction.guild.id)
        voice_client = interaction.guild.voice_client
        if not voice_client or not (voice_client.is_playing() or voice_client.is_paused()):
            await interaction.response.send_message("目前沒有在播放音樂！")
            return
        if not queue_manager.skip_to(guild_id, position):
            await interaction.response.send_message("找不到這個編號的歌曲！", ephemeral=True)
            return
        target = queue_manager.peek(guild_id, 1)[0]
        self.player.skip(voice_client)
        await interaction.response.send_message(f"⏭️ 跳到 **{target.title}**")

    @app_commands.command(name="seek", description="跳到歌曲的指定時間（例如 1:30）")
    @app_commands.describe(timestamp="時間，例如 90、1:30、1:02:03")
    async def seek(self, interaction: discord.Interaction, timestamp: str):
        seconds = parse_timestamp(timestamp)
        if seconds is None:
            await interaction.response.send_message("時間格式錯誤！請輸入像 `1:30` 或 `90` 這樣的格式。", ephemeral=True)
            return
        if not interaction.user.voice:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return
        await interaction.response.defer()
        ok, error = await self.player.seek(interaction.guild.voice_client, str(interaction.guild.id), seconds)
        if ok:
            await interaction.followup.send(f"⏩ 已跳到 `{format_duration(seconds) if seconds else '0:00'}`")
        else:
            await interaction.followup.send(error)

    @app_commands.command(name="replay", description="從頭重新播放目前的歌曲")
    async def replay(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return
        await interaction.response.defer()
        ok, error = await self.player.seek(interaction.guild.voice_client, str(interaction.guild.id), 0)
        await interaction.followup.send("🔄 從頭開始播放！" if ok else error)

    @app_commands.command(name="history", description="查看最近播放過的歌曲")
    async def history(self, interaction: discord.Interaction):
        songs = queue_manager.get_history(str(interaction.guild.id))
        if not songs:
            await interaction.response.send_message("還沒有播放紀錄！")
            return
        lines = ["**🕘 最近播放:**"]
        for i, song in enumerate(reversed(songs[-15:]), 1):
            who = f" — {song.requester}" if song.requester else ""
            lines.append(f"{i}. {truncate(song.title, 80)}{who}")
        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="top", description="伺服器點歌排行榜")
    async def top(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        songs = stats_manager.top_songs(guild_id, 10)
        users = stats_manager.top_users(guild_id, 5)
        if not songs:
            await interaction.response.send_message("還沒有任何播放紀錄！先 `/play` 一首歌吧～")
            return
        medals = ["🥇", "🥈", "🥉"]
        embed = discord.Embed(title="🏆 點歌排行榜", color=discord.Color.gold())
        embed.add_field(
            name="熱門歌曲",
            value="\n".join(
                f"{medals[i] if i < 3 else f'`{i + 1}.`'} {truncate(title, 60)} — {count} 次"
                for i, (title, count) in enumerate(songs)
            ),
            inline=False,
        )
        if users:
            embed.add_field(
                name="點歌王",
                value="\n".join(
                    f"{medals[i] if i < 3 else f'`{i + 1}.`'} {truncate(name, 40)} — {count} 首"
                    for i, (name, count) in enumerate(users)
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lyrics", description="查詢歌詞（預設為正在播放的歌曲）")
    @app_commands.describe(query="歌名（可留空）")
    async def lyrics(self, interaction: discord.Interaction, query: Optional[str] = None):
        if not query:
            current = queue_manager.get_current_song(str(interaction.guild.id))
            if not current:
                await interaction.response.send_message("請輸入歌名，或在播放歌曲時使用！", ephemeral=True)
                return
            query = current.title
        await interaction.response.defer()

        cleaned = re.sub(r"[\(\[【（「].*?[\)\]】）」]", " ", query)
        cleaned = re.sub(r"(?i)\b(official|music|video|mv|lyrics?|audio|hd|4k|live)\b", " ", cleaned)
        cleaned = re.sub(r"[|｜/\\]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip() or query

        try:
            session = await get_session()
            async with session.get("https://lrclib.net/api/search", params={"q": cleaned}, timeout=15) as resp:
                results = await resp.json() if resp.status == 200 else []
        except Exception:
            results = []

        match = next((r for r in results if r.get("plainLyrics")), None)
        if not match:
            await interaction.followup.send(f"找不到 **{truncate(cleaned, 80)}** 的歌詞 😢")
            return

        text = match["plainLyrics"].strip()
        if len(text) > 3900:
            text = text[:3900] + "\n…（歌詞過長，只顯示前段）"
        embed = discord.Embed(
            title=truncate(f"{match.get('trackName', '')} - {match.get('artistName', '')}", 250),
            description=text,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="歌詞來源：lrclib.net")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCommands(bot))
