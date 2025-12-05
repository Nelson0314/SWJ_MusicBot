import discord
from typing import Optional, Dict

from src.bot.queue import queue_manager, SongInfo
from src.utils.audio import build_ffmpeg_options


class MusicPlayer:
    def __init__(self, bot):
        self.bot = bot
        self._last_messages: Dict[tuple, discord.Message] = {}

    async def connect_to_channel(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        if not interaction.user.voice or not interaction.user.voice.channel:
            return None

        voice_channel = interaction.user.voice.channel
        voice_client = interaction.guild.voice_client

        if voice_client is None:
            voice_client = await voice_channel.connect()
        elif voice_channel != voice_client.channel:
            await voice_client.move_to(voice_channel)

        return voice_client

    async def play_song(self, voice_client: discord.VoiceClient, song: SongInfo, guild_id: str, channel: discord.TextChannel):
        ffmpeg_opts = build_ffmpeg_options(song.headers)
        source = discord.FFmpegPCMAudio(song.url, **ffmpeg_opts)

        def after_callback(error):
            if error:
                print(f"播放錯誤 {song.title}: {error}")
            self.bot.loop.create_task(
                self._play_next(voice_client, guild_id, channel)
            )

        voice_client.play(source, after=after_callback)
        await self._send_now_playing(channel, song.title)

    async def _play_next(self, voice_client: discord.VoiceClient, guild_id: str, channel: discord.TextChannel):
        next_song = queue_manager.get_next(guild_id)

        if next_song:
            await self.play_song(voice_client, next_song, guild_id, channel)
        else:
            if voice_client.is_connected():
                await voice_client.disconnect()

    async def start_playing(self, voice_client: discord.VoiceClient, guild_id: str, channel: discord.TextChannel):
        if voice_client.is_playing() or voice_client.is_paused():
            return False

        next_song = queue_manager.get_next(guild_id)
        if next_song:
            await self.play_song(voice_client, next_song, guild_id, channel)
            return True
        return False

    async def _send_now_playing(self, channel: discord.TextChannel, title: str):
        key = (channel.guild.id, channel.id)

        if key in self._last_messages:
            try:
                await self._last_messages[key].delete()
            except discord.NotFound:
                pass

        msg = await channel.send(f"正在播放: **{title}**")
        self._last_messages[key] = msg

    def skip(self, voice_client: discord.VoiceClient) -> bool:
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
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
            voice_client.stop()
            return True
        return False
