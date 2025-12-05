import discord
from discord import app_commands
from discord.ext import commands

from src.bot.autocomplete import banned_keyword_autocomplete
from src.data.storage import banned_keywords_manager


class ModerationCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ban-keyword", description="禁止特定關鍵字")
    @app_commands.describe(keyword="要禁止的關鍵字")
    @app_commands.default_permissions(manage_guild=True)
    async def ban_keyword(self, interaction: discord.Interaction, keyword: str):
        guild_id = str(interaction.guild.id)
        if banned_keywords_manager.add_keyword(guild_id, keyword):
            await interaction.response.send_message(f"已禁止關鍵字: **{keyword}**")
        else:
            await interaction.response.send_message(f"關鍵字 **{keyword}** 已經被禁止了！")

    @app_commands.command(name="unban-keyword", description="解除禁止關鍵字")
    @app_commands.describe(keyword="要解除禁止的關鍵字")
    @app_commands.autocomplete(keyword=banned_keyword_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def unban_keyword(self, interaction: discord.Interaction, keyword: str):
        guild_id = str(interaction.guild.id)
        if banned_keywords_manager.remove_keyword(guild_id, keyword):
            await interaction.response.send_message(f"已解除禁止關鍵字: **{keyword}**")
        else:
            await interaction.response.send_message(f"找不到禁止的關鍵字: **{keyword}**")

    @app_commands.command(name="list-banned", description="查看所有禁止的關鍵字")
    @app_commands.default_permissions(manage_guild=True)
    async def list_banned(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        keywords = banned_keywords_manager.get_keywords_list(guild_id)

        if not keywords:
            await interaction.response.send_message("目前沒有禁止的關鍵字！")
            return

        lines = ["**禁止的關鍵字:**"]
        for kw in keywords[:25]:
            lines.append(f"- {kw}")

        if len(keywords) > 25:
            lines.append(f"... 還有 {len(keywords) - 25} 個關鍵字")

        await interaction.response.send_message("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCommands(bot))
