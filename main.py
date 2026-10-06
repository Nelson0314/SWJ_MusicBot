import os
import asyncio
import discord
import logging
from discord.ext import commands
from dotenv import load_dotenv

from src.utils.audio import load_opus_library
from src.bot.player import MusicPlayer

# 設定日誌
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("MusicBot")

EXTENSIONS = (
    "src.bot.commands.music",
    "src.bot.commands.playlist",
    "src.bot.commands.moderation",
    "src.bot.commands.fun",
    "src.bot.commands.chat",
)
STATS_FLUSH_INTERVAL = 30


class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.music_player = MusicPlayer(self)
        self._stats_task = None

    async def setup_hook(self):
        logger.info("正在載入指令擴充模組 (Cogs)...")
        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
            except Exception:
                # 單一模組載入失敗不影響其他功能
                logger.exception("載入 %s 失敗", extension)

        logger.info("正在同步 Slash 指令...")
        await self.tree.sync()
        self._stats_task = self.loop.create_task(self._flush_stats_loop())

    async def _flush_stats_loop(self):
        from src.data.storage import stats_manager
        while not self.is_closed():
            await asyncio.sleep(STATS_FLUSH_INTERVAL)
            stats_manager.flush()

    async def on_ready(self):
        logger.info(f"音樂機器人已上線！登入為 {self.user}")
        logger.info(f"已連接到 {len(self.guilds)} 個伺服器")

    async def on_voice_state_update(self, member, before, after):
        try:
            await self.music_player.handle_voice_state_update(member, before, after)
        except Exception:
            logger.exception("處理語音狀態更新時發生錯誤")

    async def close(self):
        from src.data.storage import stats_manager
        from src.utils.http import close_session
        from src.utils.youtube import youtube_searcher

        if self._stats_task:
            self._stats_task.cancel()
        stats_manager.flush()
        from src.utils import ai_chat
        if ai_chat.chat_engine is not None:
            await ai_chat.chat_engine.aclose()
        await close_session()
        await super().close()
        youtube_searcher.shutdown()


async def _on_tree_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """指令發生未預期錯誤時，至少回覆使用者，不要讓他一直看到「思考中」。"""
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        message = f"太快了！請 {error.retry_after:.0f} 秒後再試。"
    elif isinstance(error, discord.app_commands.MissingPermissions):
        message = "你沒有使用這個指令的權限！"
    else:
        logger.exception("指令 %s 發生錯誤", getattr(interaction.command, "name", "?"), exc_info=error)
        message = "執行指令時發生錯誤，請稍後再試！"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


def main():
    load_dotenv()
    load_opus_library()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("錯誤：找不到 DISCORD_TOKEN 環境變數！請在 .env 檔案中設定。")
        return

    bot = MusicBot()
    bot.tree.on_error = _on_tree_error
    bot.run(token, log_handler=None)  # 已經設定了 root logger


if __name__ == "__main__":
    main()
