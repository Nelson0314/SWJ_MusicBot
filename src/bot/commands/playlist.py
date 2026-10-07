import discord
from discord import app_commands
from discord.ext import commands
import random

from src.bot.queue import queue_manager, SongInfo
from src.bot.autocomplete import playlist_name_autocomplete, song_title_autocomplete
from src.data.storage import playlist_manager, banned_keywords_manager
from src.utils.youtube import youtube_searcher, is_playlist_url
from src.utils.filters import keyword_filter
from src.utils.format import truncate

PLAYLIST_IMPORT_LIMIT = 500


class PlaylistCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.player = bot.music_player

    @app_commands.command(name="create-playlist", description="建立新的播放清單")
    @app_commands.describe(name="播放清單名稱")
    async def create_playlist(self, interaction: discord.Interaction, name: str):
        guild_id = str(interaction.guild.id)
        if playlist_manager.create_playlist(guild_id, name):
            await interaction.response.send_message(f"已建立播放清單 **{name}**！")
        else:
            await interaction.response.send_message(f"播放清單 **{name}** 已經存在！")

    @app_commands.command(name="delete-playlist", description="刪除播放清單")
    @app_commands.describe(name="播放清單名稱")
    @app_commands.autocomplete(name=playlist_name_autocomplete)
    async def delete_playlist(self, interaction: discord.Interaction, name: str):
        guild_id = str(interaction.guild.id)
        if playlist_manager.delete_playlist(guild_id, name):
            await interaction.response.send_message(f"已刪除播放清單 **{name}**！")
        else:
            await interaction.response.send_message(f"找不到播放清單 **{name}**！")

    @app_commands.command(name="list-playlists", description="查看所有播放清單")
    async def list_playlists(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        playlists = playlist_manager.get_all_playlists(guild_id)

        if not playlists:
            await interaction.response.send_message("目前沒有任何播放清單！")
            return

        lines = ["**播放清單:**"]
        for name, songs in playlists.items():
            lines.append(f"- **{name}** ({len(songs)} 首歌)")

        await interaction.response.send_message(truncate("\n".join(lines), 2000))

    @app_commands.command(name="show-playlist", description="查看播放清單內容")
    @app_commands.describe(playlist_name="播放清單名稱")
    @app_commands.autocomplete(playlist_name=playlist_name_autocomplete)
    async def show_playlist(self, interaction: discord.Interaction, playlist_name: str):
        guild_id = str(interaction.guild.id)

        if not playlist_manager.playlist_exists(guild_id, playlist_name):
            await interaction.response.send_message(f"找不到播放清單 **{playlist_name}**！")
            return

        songs = playlist_manager.get_playlist(guild_id, playlist_name)
        if not songs:
            await interaction.response.send_message(f"播放清單 **{playlist_name}** 是空的！")
            return

        lines = [f"**{playlist_name}** 的歌曲:"]
        for i, song in enumerate(songs[:20], 1):
            lines.append(f"{i}. {truncate(song['title'], 90)}")

        if len(songs) > 20:
            lines.append(f"... 還有 {len(songs) - 20} 首歌曲")

        await interaction.response.send_message(truncate("\n".join(lines), 2000))

    @app_commands.command(name="add", description="新增歌曲到播放清單")
    @app_commands.describe(playlist_name="播放清單名稱", query="歌曲名稱或關鍵字")
    @app_commands.autocomplete(playlist_name=playlist_name_autocomplete)
    async def add_song(self, interaction: discord.Interaction, playlist_name: str, query: str):
        guild_id = str(interaction.guild.id)

        if not playlist_manager.playlist_exists(guild_id, playlist_name):
            await interaction.response.send_message(f"找不到播放清單 **{playlist_name}**！")
            return

        banned = banned_keywords_manager.get_keywords(guild_id)
        if keyword_filter.contains_banned(query, banned):
            await interaction.response.send_message("這個關鍵字被禁止搜尋！", ephemeral=True)
            return

        await interaction.response.defer()

        song_data, error = await youtube_searcher.search(query)
        if error:
            await interaction.followup.send(error)
            return

        playlist_manager.add_song(guild_id, playlist_name, song_data["title"], query,
                                  url=song_data.get("webpage_url"))
        await interaction.followup.send(f"已將 **{song_data['title']}** 加入 **{playlist_name}**！")

    @app_commands.command(name="remove", description="從播放清單移除歌曲")
    @app_commands.describe(playlist_name="播放清單名稱", song_title="歌曲名稱")
    @app_commands.autocomplete(playlist_name=playlist_name_autocomplete, song_title=song_title_autocomplete)
    async def remove_song(self, interaction: discord.Interaction, playlist_name: str, song_title: str):
        guild_id = str(interaction.guild.id)

        if not playlist_manager.playlist_exists(guild_id, playlist_name):
            await interaction.response.send_message(f"找不到播放清單 **{playlist_name}**！")
            return

        if playlist_manager.remove_song(guild_id, playlist_name, song_title):
            await interaction.response.send_message(f"已從 **{playlist_name}** 移除 **{song_title}**！")
        else:
            await interaction.response.send_message(f"找不到歌曲 **{song_title}**！")

    @app_commands.command(name="play-playlist", description="播放整個播放清單")
    @app_commands.describe(playlist_name="播放清單名稱", shuffle="是否隨機播放")
    @app_commands.autocomplete(playlist_name=playlist_name_autocomplete)
    async def play_playlist(self, interaction: discord.Interaction, playlist_name: str, shuffle: bool = False):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return

        guild_id = str(interaction.guild.id)

        if not playlist_manager.playlist_exists(guild_id, playlist_name):
            await interaction.response.send_message(f"找不到播放清單 **{playlist_name}**！")
            return

        songs = playlist_manager.get_playlist(guild_id, playlist_name)
        if not songs:
            await interaction.response.send_message(f"播放清單 **{playlist_name}** 是空的！")
            return

        await interaction.response.defer()

        voice_client = await self.player.connect_to_channel(interaction)
        if not voice_client:
            await interaction.followup.send("無法連接到語音頻道！")
            return

        songs_to_play = list(songs)
        if shuffle:
            random.shuffle(songs_to_play)

        # 不再逐首搜尋：直接把整份清單放進待播，播放時才解析（並預先載入下一首）
        busy = self.player.is_busy(guild_id, voice_client)
        queue_manager.add_songs(guild_id, [
            SongInfo(
                url="",
                title=song.get("title") or song.get("query", "未知標題"),
                headers={},
                requester=interaction.user.display_name,
                query=song.get("query") or song.get("title", ""),
                webpage_url=song.get("url", ""),
                source_playlist=playlist_name,
            )
            for song in songs_to_play
        ])
        added_count = len(songs_to_play)

        shuffle_text = "（隨機播放）" if shuffle else ""
        await interaction.followup.send(f"已加入 **{playlist_name}** 的 {added_count} 首歌曲{shuffle_text}！")

        if not busy and not voice_client.is_playing() and not voice_client.is_paused():
            await self.player.start_playing(voice_client, guild_id, interaction.channel)

    @app_commands.command(name="import-playlist", description="從 YouTube 播放清單網址匯入成播放清單")
    @app_commands.describe(name="要存成的播放清單名稱（不存在會自動建立）", url="YouTube 播放清單網址")
    @app_commands.autocomplete(name=playlist_name_autocomplete)
    async def import_playlist(self, interaction: discord.Interaction, name: str, url: str):
        if not is_playlist_url(url):
            await interaction.response.send_message("請提供 YouTube 播放清單網址（網址中需要有 `list=`）！", ephemeral=True)
            return
        await interaction.response.defer()
        guild_id = str(interaction.guild.id)

        data, error = await youtube_searcher.extract_playlist(url, limit=PLAYLIST_IMPORT_LIMIT)
        if error:
            await interaction.followup.send(error)
            return

        created = playlist_manager.create_playlist(guild_id, name)
        existing_urls = {s.get("url") for s in playlist_manager.get_playlist(guild_id, name) if s.get("url")}
        new_songs = [
            {"title": e["title"], "query": e["url"], "url": e["url"]}
            for e in data["entries"] if e["url"] not in existing_urls
        ]
        count = playlist_manager.add_songs(guild_id, name, new_songs)
        skipped = len(data["entries"]) - count
        verb = "建立" if created else "更新"
        extra = f"（略過 {skipped} 首重複的歌）" if skipped else ""
        await interaction.followup.send(
            f"📥 已{verb}播放清單 **{name}**，從 **{truncate(data['title'], 80)}** 匯入 {count} 首歌曲{extra}！"
        )

    @app_commands.command(name="save-queue", description="把目前播放中與待播的歌曲存成播放清單")
    @app_commands.describe(name="播放清單名稱（不存在會自動建立）")
    @app_commands.autocomplete(name=playlist_name_autocomplete)
    async def save_queue(self, interaction: discord.Interaction, name: str):
        guild_id = str(interaction.guild.id)
        songs = []
        current = queue_manager.get_current_song(guild_id)
        if current:
            songs.append(current)
        songs.extend(queue_manager.get_queue_list(guild_id))
        if not songs:
            await interaction.response.send_message("目前沒有任何歌曲可以儲存！")
            return

        playlist_manager.create_playlist(guild_id, name)
        count = playlist_manager.add_songs(guild_id, name, [
            {"title": s.title, "query": s.query or s.webpage_url or s.title, "url": s.webpage_url}
            for s in songs
        ])
        await interaction.response.send_message(f"💾 已將 {count} 首歌曲存到 **{name}**！")


async def setup(bot: commands.Bot):
    await bot.add_cog(PlaylistCommands(bot))
