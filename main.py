import os
import discord
import logging
from discord.ext import commands
from dotenv import load_dotenv

from src.utils.audio import load_opus_library
from src.bot.player import MusicPlayer

# 設定日誌
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("MusicBot")


class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.music_player = MusicPlayer(self)

    async def setup_hook(self):
        logger.info("正在載入指令擴充模組 (Cogs)...")
        await self.load_extension("src.bot.commands.music")
        await self.load_extension("src.bot.commands.playlist")
        await self.load_extension("src.bot.commands.moderation")
        
        logger.info("正在同步 Slash 指令...")
        await self.tree.sync()

    async def on_ready(self):
        logger.info(f"音樂機器人已上線！登入為 {self.user}")
        logger.info(f"已連接到 {len(self.guilds)} 個伺服器")


def main():
    load_dotenv()
    load_opus_library()
    
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("錯誤：找不到 DISCORD_TOKEN 環境變數！請在 .env 檔案中設定。")
        return

    bot = MusicBot()
    bot.run(token, log_handler=None)  # 已經設定了 root logger


if __name__ == "__main__":
    main()
