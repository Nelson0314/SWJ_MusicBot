import discord
from discord import app_commands
from discord.ext import commands
import random

from src.bot.queue import queue_manager
from src.bot.autocomplete import playlist_name_autocomplete, song_title_autocomplete
from src.data.storage import playlist_manager, banned_keywords_manager
from src.utils.youtube import youtube_searcher
from src.utils.filters import keyword_filter


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

        await interaction.response.send_message("\n".join(lines))

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
            lines.append(f"{i}. {song['title']}")

        if len(songs) > 20:
            lines.append(f"... 還有 {len(songs) - 20} 首歌曲")

        await interaction.response.send_message("\n".join(lines))

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

        playlist_manager.add_song(guild_id, playlist_name, song_data["title"], query)
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

        added_count = 0
        for song in songs_to_play:
            song_data, error = await youtube_searcher.search(song["query"])
            if song_data:
                queue_manager.add_song(
                    guild_id,
                    song_data["url"],
                    song_data["title"],
                    song_data["headers"],
                    interaction.user.display_name
                )
                added_count += 1

        if added_count == 0:
            await interaction.followup.send("無法載入播放清單中的歌曲！")
            return

        shuffle_text = "（隨機播放）" if shuffle else ""
        await interaction.followup.send(f"已加入 **{playlist_name}** 的 {added_count} 首歌曲{shuffle_text}！")

        if not voice_client.is_playing() and not voice_client.is_paused():
            await self.player.start_playing(voice_client, guild_id, interaction.channel)


async def setup(bot: commands.Bot):
    await bot.add_cog(PlaylistCommands(bot))
