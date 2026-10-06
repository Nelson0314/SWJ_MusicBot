import asyncio
from typing import Optional

import aiohttp

_session: Optional[aiohttp.ClientSession] = None
_lock = asyncio.Lock()


async def get_session() -> aiohttp.ClientSession:
    """共用的 aiohttp session（重複使用連線，比每次建立新的快）。"""
    global _session
    if _session is None or _session.closed:
        async with _lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"User-Agent": "SWJ-MusicBot/1.0 (Discord bot)"},
                    trust_env=True,
                )
    return _session


async def close_session():
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
