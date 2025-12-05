import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

from src.utils.audio import load_opus_library
from src.bot.player import MusicPlayer
from src.bot.commands import music, playlist, moderation

load_dotenv()

load_opus_library()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
player = MusicPlayer(bot)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"音樂機器人已上線！登入為 {bot.user}")
    print(f"已連接到 {len(bot.guilds)} 個伺服器")


@bot.event
async def setup_hook():
    await music.setup(bot, player)
    await playlist.setup(bot, player)
    await moderation.setup(bot)


def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("錯誤：找不到 DISCORD_TOKEN 環境變數！")
        print("請在 .env 檔案中設定 DISCORD_TOKEN=你的機器人Token")
        return

    bot.run(token)


if __name__ == "__main__":
    main()
