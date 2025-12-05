from typing import List
import discord
from discord import app_commands

from src.data.storage import playlist_manager, banned_keywords_manager


async def playlist_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    guild_id = str(interaction.guild.id)
    names = playlist_manager.get_playlist_names(guild_id)

    if not current:
        return [
            app_commands.Choice(name=n, value=n)
            for n in names[:25]
        ]

    filtered = [n for n in names if current.lower() in n.lower()]
    return [
        app_commands.Choice(name=n, value=n)
        for n in filtered[:25]
    ]


async def song_title_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    guild_id = str(interaction.guild.id)
    playlist_name = getattr(interaction.namespace, 'playlist_name', None)

    if not playlist_name:
        return []

    titles = playlist_manager.get_song_titles(guild_id, playlist_name)

    if not current:
        return [
            app_commands.Choice(name=t, value=t)
            for t in titles[:25]
        ]

    filtered = [t for t in titles if current.lower() in t.lower()]
    return [
        app_commands.Choice(name=t, value=t)
        for t in filtered[:25]
    ]


async def banned_keyword_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    guild_id = str(interaction.guild.id)
    keywords = banned_keywords_manager.get_keywords_list(guild_id)

    if not current:
        return [
            app_commands.Choice(name=k, value=k)
            for k in keywords[:25]
        ]

    filtered = [k for k in keywords if current.lower() in k.lower()]
    return [
        app_commands.Choice(name=k, value=k)
        for k in filtered[:25]
    ]
