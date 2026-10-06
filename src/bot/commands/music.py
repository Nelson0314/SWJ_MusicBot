import discord
from discord import app_commands
from discord.ext import commands

from src.bot.queue import queue_manager
from src.utils.youtube import youtube_searcher
from src.utils.filters import keyword_filter
from src.data.storage import banned_keywords_manager


class MusicCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.player = bot.music_player

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

        song_data, error = await youtube_searcher.search(query)
        if error:
            await interaction.followup.send(error)
            return

        queue_manager.add_song(
            guild_id,
            song_data["url"],
            song_data["title"],
            song_data["headers"],
            interaction.user.display_name
        )

        if voice_client.is_playing() or voice_client.is_paused():
            await interaction.followup.send(f"已加入待播清單: **{song_data['title']}**")
        else:
            await interaction.followup.send(f"正在播放: **{song_data['title']}**")
            await self.player.start_playing(voice_client, guild_id, interaction.channel)

    @app_commands.command(name="skip", description="跳過目前歌曲")
    async def skip(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if self.player.skip(voice_client):
            await interaction.response.send_message("已跳過！")
        else:
            await interaction.response.send_message("目前沒有在播放音樂！")

    @app_commands.command(name="pause", description="暫停播放")
    async def pause(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return

        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("機器人不在語音頻道中！")
            return

        if self.player.pause(voice_client):
            await interaction.response.send_message("已暫停！")
        else:
            await interaction.response.send_message("目前沒有在播放音樂！")

    @app_commands.command(name="resume", description="繼續播放")
    async def resume(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return

        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("機器人不在語音頻道中！")
            return

        if self.player.resume(voice_client):
            await interaction.response.send_message("繼續播放！")
        else:
            await interaction.response.send_message("音樂沒有暫停！")

    @app_commands.command(name="stop", description="停止播放並清空待播清單")
    async def stop(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return

        voice_client = interaction.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            await interaction.response.send_message("機器人不在語音頻道中！")
            return

        guild_id = str(interaction.guild.id)
        self.player.stop(voice_client, guild_id)
        await interaction.response.send_message("已停止播放並清空待播清單！")

    @app_commands.command(name="queue", description="查看待播清單")
    async def show_queue(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        songs = queue_manager.get_queue_list(guild_id)
        current = queue_manager.get_current_song(guild_id)

        if not songs and not current:
            await interaction.response.send_message("待播清單是空的！")
            return

        lines = []
        if current:
            lines.append(f"**正在播放:** {current.title}")
            lines.append("")

        if songs:
            lines.append("**待播清單:**")
            for i, song in enumerate(songs[:15], 1):
                lines.append(f"{i}. {song.title}")

            if len(songs) > 15:
                lines.append(f"... 還有 {len(songs) - 15} 首歌曲")

        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="clear", description="清空待播清單")
    async def clear_queue(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        queue_manager.clear_queue(guild_id)
        await interaction.response.send_message("已清空待播清單！")

    @app_commands.command(name="leave", description="離開語音頻道")
    async def leave(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client and voice_client.is_connected():
            guild_id = str(interaction.guild.id)
            queue_manager.clear_queue(guild_id)
            await voice_client.disconnect()
            await interaction.response.send_message("已離開語音頻道！")
        else:
            await interaction.response.send_message("機器人不在語音頻道中！")


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCommands(bot))
