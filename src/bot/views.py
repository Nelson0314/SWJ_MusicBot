from typing import List, Dict, Any, Optional

import discord

from src.bot.queue import queue_manager, LOOP_OFF, LOOP_ONE, LOOP_ALL
from src.utils.format import format_duration, truncate

_NEXT_LOOP = {LOOP_OFF: LOOP_ALL, LOOP_ALL: LOOP_ONE, LOOP_ONE: LOOP_OFF}
_LOOP_EMOJI = {LOOP_OFF: "➡️", LOOP_ALL: "🔁", LOOP_ONE: "🔂"}


def _same_voice_channel(interaction: discord.Interaction) -> Optional[str]:
    """檢查按按鈕的人是否和機器人在同一個語音頻道。回傳錯誤訊息或 None。"""
    voice_client = interaction.guild.voice_client if interaction.guild else None
    if voice_client is None or not voice_client.is_connected():
        return "機器人不在語音頻道中！"
    user_voice = getattr(interaction.user, "voice", None)
    if not user_voice or user_voice.channel != voice_client.channel:
        return "你需要和機器人在同一個語音頻道才能操作喔！"
    return None


class NowPlayingView(discord.ui.View):
    """「正在播放」訊息下方的控制按鈕。"""

    def __init__(self, player, guild_id: str):
        super().__init__(timeout=None)
        self.player = player
        self.guild_id = guild_id
        self._sync_loop_button()

    def _sync_loop_button(self):
        mode = queue_manager.get_loop_mode(self.guild_id)
        self.loop_button.emoji = _LOOP_EMOJI.get(mode, "➡️")
        self.loop_button.style = discord.ButtonStyle.secondary if mode == LOOP_OFF else discord.ButtonStyle.success

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        error = _same_voice_channel(interaction)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if self.player.pause(vc):
            await interaction.response.send_message(f"⏸️ {interaction.user.display_name} 暫停了音樂", delete_after=8)
        elif self.player.resume(vc):
            await interaction.response.send_message(f"▶️ {interaction.user.display_name} 繼續播放", delete_after=8)
        else:
            await interaction.response.send_message("目前沒有在播放音樂！", ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.player.skip(interaction.guild.voice_client):
            await interaction.response.send_message(f"⏭️ {interaction.user.display_name} 跳過了這首歌", delete_after=8)
        else:
            await interaction.response.send_message("目前沒有在播放音樂！", ephemeral=True)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.stop(interaction.guild.voice_client, self.guild_id)
        await interaction.response.send_message(f"⏹️ {interaction.user.display_name} 停止播放並清空了待播清單")

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        mode = _NEXT_LOOP[queue_manager.get_loop_mode(self.guild_id)]
        queue_manager.set_loop_mode(self.guild_id, mode)
        self._sync_loop_button()
        labels = {LOOP_OFF: "循環已關閉", LOOP_ALL: "🔁 清單循環", LOOP_ONE: "🔂 單曲循環"}
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(labels[mode], ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        count = queue_manager.shuffle(self.guild_id)
        if count:
            await interaction.response.send_message(f"🔀 {interaction.user.display_name} 打亂了 {count} 首待播歌曲", delete_after=8)
        else:
            await interaction.response.send_message("待播清單是空的！", ephemeral=True)


class SearchSelect(discord.ui.Select):
    def __init__(self, results: List[Dict[str, Any]]):
        options = []
        for i, item in enumerate(results[:25]):
            duration = format_duration(item.get("duration")) if item.get("duration") else ""
            desc = " · ".join(x for x in (item.get("uploader", ""), duration) if x)
            options.append(discord.SelectOption(
                label=truncate(item["title"], 100),
                description=truncate(desc, 100) or None,
                value=str(i),
            ))
        super().__init__(placeholder="選擇要播放的歌曲…", options=options, min_values=1, max_values=1)
        self.results = results

    async def callback(self, interaction: discord.Interaction):
        view: SearchView = self.view
        if interaction.user.id != view.author_id:
            await interaction.response.send_message("這是別人的搜尋結果喔！", ephemeral=True)
            return
        choice = self.results[int(self.values[0])]
        view.stop()
        await interaction.response.edit_message(content=f"🔎 已選擇：**{truncate(choice['title'], 150)}**", view=None)
        await view.on_choose(interaction, choice)


class SearchView(discord.ui.View):
    def __init__(self, author_id: int, results: List[Dict[str, Any]], on_choose):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.on_choose = on_choose
        self.message: Optional[discord.Message] = None
        self.add_item(SearchSelect(results))

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(content="⌛ 搜尋已逾時。", view=None)
            except discord.HTTPException:
                pass
