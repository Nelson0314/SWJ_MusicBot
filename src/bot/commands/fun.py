import asyncio
import datetime
import difflib
import hashlib
import random
import re
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.bot.autocomplete import playlist_name_autocomplete
from src.bot.player import make_tracked_source
from src.data.storage import playlist_manager, stats_manager
from src.utils.audio import build_ffmpeg_options
from src.utils.format import truncate
from src.utils.youtube import youtube_searcher

TAIPEI = datetime.timezone(datetime.timedelta(hours=8))


def _stable_random(*parts) -> random.Random:
    """同樣的輸入（例如同一個人、同一天）得到同樣的結果。"""
    seed = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return random.Random(int(seed[:16], 16))


def _today() -> str:
    return datetime.datetime.now(TAIPEI).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------- 猜歌用
_BRACKETS = re.compile(r"[\(\[【（「『〈《].*?[\)\]】）」』〉》]")
_NOISE = re.compile(
    r"(?i)(official|music|video|lyrics?|lyric video|audio|hd|4k|mv|m/v|live|ver\.?|version|feat\.?|ft\.?|"
    r"動態歌詞|歌詞版|完整版|高音質|官方|中字|字幕)"
)
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]+")


def _normalize(text: str) -> str:
    return _NON_WORD.sub("", text).lower()


def answer_candidates(title: str) -> List[str]:
    """從 YouTube 標題中抽出可以當作答案的片段（歌名、歌手）。"""
    base = _NOISE.sub(" ", _BRACKETS.sub(" ", title))
    # 中文歌常把歌名放在括號裡，例如「周杰倫【晴天】」
    inners = [_NOISE.sub(" ", t) for t in re.findall(r"[\(\[【（「『〈《](.*?)[\)\]】）」』〉》]", title)]
    candidates = {_normalize(base)}
    for text in [base] + inners:
        for part in re.split(r"\s[-–—|｜/／:：]\s?|[-–—|｜／]", text):
            candidates.add(_normalize(part))
            # 中英混合時，中文和英文分別都可以當答案
            for run in _CJK_RUN.findall(part):
                candidates.add(_normalize(run))
            candidates.add(_normalize(_CJK_RUN.sub(" ", part)))
    return [c for c in candidates if c]


def is_correct_guess(guess: str, title: str) -> bool:
    g = _normalize(guess)
    if len(g) < 2 and not re.search(r"[一-鿿]", guess):
        return False
    if not g:
        return False
    full = _normalize(_BRACKETS.sub(" ", title))
    for cand in answer_candidates(title):
        if g == cand:
            return True
        if len(cand) >= 2 and g in cand and len(g) >= max(2, int(len(cand) * 0.6)):
            return True
        if difflib.SequenceMatcher(None, g, cand).ratio() >= 0.8:
            return True
    # 猜中整個標題的大部分也算
    return len(g) >= 4 and g in full and len(g) >= len(full) * 0.4


# ---------------------------------------------------------------------- 文字素材
EIGHT_BALL = [
    "肯定是的 ✨", "毫無疑問！", "你可以相信它 👍", "我看是會的", "八九不離十",
    "前景看好 🌈", "跡象指向「是」", "再問一次看看…", "現在還不能告訴你 🤫", "專心再問一次",
    "別指望了 😅", "我的回答是「不」", "我的消息來源說不會", "前景不太妙…", "非常懷疑 🤔",
    "什麼都不做，就什麼都不會發生 🐚", "去吃個宵夜再說", "問問你的心 💗",
]

FORTUNES = [
    ("大吉", 0xFF4D4D, "今天做什麼都順！適合告白、點歌、抽卡。"),
    ("中吉", 0xFF884D, "運氣不錯，把握機會往前衝吧！"),
    ("小吉", 0xFFC14D, "小確幸會在不經意的時候出現～"),
    ("吉", 0x7ED957, "平穩的一天，照自己的步調走就好。"),
    ("末吉", 0x4DB8FF, "先苦後甘，晚上會比較順利。"),
    ("凶", 0x8A8AFF, "今天低調一點，多聽點喜歡的歌補血。"),
    ("大凶", 0x555555, "逆轉的開始！聽首熱血的歌壓壓驚吧 💪"),
]
FORTUNE_WEIGHTS = [10, 18, 22, 22, 14, 9, 5]
LUCKY_COLORS = ["紅色", "橘色", "黃色", "綠色", "藍色", "紫色", "粉紅色", "白色", "黑色", "金色", "薄荷綠", "天空藍"]
LUCKY_ITEMS = ["珍珠奶茶", "耳機", "雞排", "便利商店的御飯糰", "貓咪貼圖", "藍色原子筆", "雨傘", "鹹酥雞", "一首老歌", "熱可可"]
DEFAULT_SONGS = ["周杰倫 - 晴天", "五月天 - 倔強", "告五人 - 愛人錯過", "鄧紫棋 - 光年之外", "蔡依林 - 日不落",
                 "茄子蛋 - 浪子回頭", "陳奕迅 - 孤勇者", "林俊傑 - 江南", "田馥甄 - 小幸運", "草東沒有派對 - 山海"]

ACTIONS = {
    "hug": ("🤗", ["{a} 給了 {b} 一個大大的擁抱！", "{a} 緊緊抱住了 {b}，好溫暖～", "{a} 撲上去抱住 {b}！(つ≧▽≦)つ"]),
    "pat": ("🫳", ["{a} 摸了摸 {b} 的頭，乖～", "{a} 溫柔地拍拍 {b}", "{b} 被 {a} 摸頭了，看起來很開心 (๑•̀ㅂ•́)و✧"]),
    "poke": ("👉", ["{a} 戳了戳 {b}！", "{a}：「欸欸 {b}，在嗎？」(戳戳)", "{a} 偷偷戳了 {b} 一下就跑走了"]),
    "slap": ("🖐️", ["{a} 用一條鹹魚拍了 {b}！🐟", "{a} 賞了 {b} 一個（輕輕的）巴掌", "{a} 拿拖鞋飛向 {b}！🩴"]),
}

RPS = {"rock": ("✊", "石頭"), "paper": ("✋", "布"), "scissors": ("✌️", "剪刀")}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


class RPSView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("這是別人的對戰喔！自己用 `/rps` 開一局吧～", ephemeral=True)
            return False
        return True

    async def _play(self, interaction: discord.Interaction, choice: str):
        bot_choice = random.choice(list(RPS))
        if choice == bot_choice:
            result = "平手！再來一局？🤝"
        elif RPS_BEATS[choice] == bot_choice:
            result = "你贏了！🎉"
        else:
            result = "我贏了！嘿嘿 😎"
        self.stop()
        await interaction.response.edit_message(
            content=f"你出 {RPS[choice][0]} {RPS[choice][1]}，我出 {RPS[bot_choice][0]} {RPS[bot_choice][1]}\n**{result}**",
            view=None,
        )

    @discord.ui.button(emoji="✊", label="石頭", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._play(interaction, "rock")

    @discord.ui.button(emoji="✋", label="布", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._play(interaction, "paper")

    @discord.ui.button(emoji="✌️", label="剪刀", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._play(interaction, "scissors")

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(content="⌛ 太久沒出拳，對戰取消～", view=None)
            except discord.HTTPException:
                pass


class FunCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.player = bot.music_player
        self.active_quizzes: Dict[int, bool] = {}   # guild_id -> 是否要求停止
        self._quiz_channels: set = set()

    def is_quiz_channel(self, channel_id: int) -> bool:
        return channel_id in self._quiz_channels

    # ------------------------------------------------------------------ 小遊戲
    @app_commands.command(name="roll", description="擲骰子 🎲")
    @app_commands.describe(sides="骰子面數", count="骰子數量")
    async def roll(self, interaction: discord.Interaction,
                   sides: app_commands.Range[int, 2, 1000] = 6, count: app_commands.Range[int, 1, 20] = 1):
        rolls = [random.randint(1, sides) for _ in range(count)]
        if count == 1:
            text = f"🎲 {interaction.user.display_name} 擲出了 **{rolls[0]}**（d{sides}）"
        else:
            text = f"🎲 {interaction.user.display_name} 擲了 {count}d{sides}：{' + '.join(map(str, rolls))} = **{sum(rolls)}**"
        if sides == 6 and count == 1 and rolls[0] == 6:
            text += " 🔥"
        await interaction.response.send_message(text)

    @app_commands.command(name="coin", description="擲硬幣 🪙")
    async def coin(self, interaction: discord.Interaction):
        if random.random() < 0.01:
            await interaction.response.send_message("🪙 硬幣……立起來了！？這是奇蹟 ✨")
            return
        await interaction.response.send_message(f"🪙 是 **{random.choice(['正面', '反面'])}**！")

    @app_commands.command(name="8ball", description="問問神奇海螺 🐚")
    @app_commands.describe(question="你想問的問題")
    async def eight_ball(self, interaction: discord.Interaction, question: str):
        await interaction.response.send_message(
            f"❓ **{truncate(question, 200)}**\n🐚 神奇海螺說：{random.choice(EIGHT_BALL)}"
        )

    @app_commands.command(name="fortune", description="抽今日運勢 🔮（每人每天固定）")
    async def fortune(self, interaction: discord.Interaction):
        rng = _stable_random("fortune", interaction.user.id, _today())
        idx = rng.choices(range(len(FORTUNES)), weights=FORTUNE_WEIGHTS)[0]
        name, color, advice = FORTUNES[idx]

        top = [title for title, _ in stats_manager.top_songs(str(interaction.guild.id), 20)]
        song = rng.choice(top or DEFAULT_SONGS)

        embed = discord.Embed(title=f"🔮 {interaction.user.display_name} 的今日運勢：{name}", color=color,
                              description=advice)
        embed.add_field(name="幸運色", value=rng.choice(LUCKY_COLORS))
        embed.add_field(name="幸運數字", value=str(rng.randint(0, 99)))
        embed.add_field(name="幸運物", value=rng.choice(LUCKY_ITEMS))
        embed.add_field(name="今日推薦歌曲", value=f"🎵 {truncate(song, 100)}", inline=False)
        embed.set_footer(text=f"{_today()} · 每天只能抽一次，明天再來吧！")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="choose", description="選擇困難？讓我幫你選！")
    @app_commands.describe(options="選項，用空白、逗號或 | 分隔")
    async def choose(self, interaction: discord.Interaction, options: str):
        items = [o.strip() for o in re.split(r"[,，、|｜\s]+", options) if o.strip()]
        if len(items) < 2:
            await interaction.response.send_message("至少要給我兩個選項喔！例如：`麥當勞 肯德基 摩斯`", ephemeral=True)
            return
        pick = random.choice(items)
        await interaction.response.send_message(
            f"🤔 在 {' / '.join(truncate(i, 30) for i in items[:20])} 之中……\n👉 我選 **{truncate(pick, 100)}**！"
        )

    @app_commands.command(name="rps", description="跟機器人猜拳 ✊✋✌️")
    async def rps(self, interaction: discord.Interaction):
        view = RPSView(interaction.user.id)
        await interaction.response.send_message("剪刀、石頭、布！出拳吧 👇", view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="rate", description="讓機器人幫任何東西打分數")
    @app_commands.describe(thing="要評分的東西")
    async def rate(self, interaction: discord.Interaction, thing: str):
        score = _stable_random("rate", thing.strip().lower()).randint(0, 100)
        if score >= 90:
            comment = "神作！👑"
        elif score >= 70:
            comment = "很不錯喔 👍"
        elif score >= 40:
            comment = "還可以啦 🙂"
        elif score >= 15:
            comment = "嗯……有進步空間 😅"
        else:
            comment = "這個……我們換個話題好嗎 🫠"
        await interaction.response.send_message(f"📊 我給 **{truncate(thing, 100)}** 打 **{score}/100** 分，{comment}")

    @app_commands.command(name="compatibility", description="測測兩個人的契合度 💞")
    @app_commands.describe(user1="第一個人", user2="第二個人（留空則為你自己）")
    async def compatibility(self, interaction: discord.Interaction, user1: discord.Member,
                            user2: Optional[discord.Member] = None):
        user2 = user2 or interaction.user
        a, b = sorted([user1.id, user2.id])
        score = _stable_random("love", a, b).randint(0, 100)
        if user1.id == user2.id:
            score = 100
        hearts = "💗" * (score // 20) + "🤍" * (5 - score // 20)
        if score >= 90:
            comment = "天生一對！快去合唱一首情歌 🎤"
        elif score >= 70:
            comment = "默契十足～"
        elif score >= 40:
            comment = "還不錯的朋友！"
        else:
            comment = "可能需要一起多聽幾首歌培養感情 🎧"
        await interaction.response.send_message(
            f"💞 **{user1.display_name}** × **{user2.display_name}**\n{hearts} **{score}%**\n{comment}"
        )

    async def _action(self, interaction: discord.Interaction, kind: str, target: discord.Member):
        emoji, templates = ACTIONS[kind]
        if target.id == interaction.user.id:
            text = f"{emoji} {interaction.user.mention} 對自己做了這件事……需要抱抱嗎？"
        elif self.bot.user and target.id == self.bot.user.id:
            text = f"{emoji} {interaction.user.mention} 對我出手了！(〃∀〃) 那我就回敬一首歌吧 🎵"
        else:
            text = f"{emoji} " + random.choice(templates).format(a=interaction.user.mention, b=target.mention)
        await interaction.response.send_message(text, allowed_mentions=discord.AllowedMentions(users=[target]))

    @app_commands.command(name="hug", description="抱抱某人 🤗")
    @app_commands.describe(target="要抱的人")
    async def hug(self, interaction: discord.Interaction, target: discord.Member):
        await self._action(interaction, "hug", target)

    @app_commands.command(name="pat", description="摸摸頭 🫳")
    @app_commands.describe(target="要摸頭的人")
    async def pat(self, interaction: discord.Interaction, target: discord.Member):
        await self._action(interaction, "pat", target)

    @app_commands.command(name="poke", description="戳一下 👉")
    @app_commands.describe(target="要戳的人")
    async def poke(self, interaction: discord.Interaction, target: discord.Member):
        await self._action(interaction, "poke", target)

    @app_commands.command(name="slap", description="用鹹魚拍人 🐟")
    @app_commands.describe(target="要拍的人")
    async def slap(self, interaction: discord.Interaction, target: discord.Member):
        await self._action(interaction, "slap", target)

    @app_commands.command(name="poll", description="發起投票 📊")
    @app_commands.describe(question="投票問題", options="選項，用 | 分隔（最多 10 個，留空則為是/否投票）")
    async def poll(self, interaction: discord.Interaction, question: str, options: Optional[str] = None):
        items = [o.strip() for o in (options or "").split("|") if o.strip()]
        if len(items) == 1 or len(items) > 10:
            await interaction.response.send_message("選項需要 2~10 個，用 `|` 分隔！", ephemeral=True)
            return
        embed = discord.Embed(title=f"📊 {truncate(question, 250)}", color=discord.Color.teal())
        if items:
            embed.description = "\n".join(f"{NUMBER_EMOJI[i]} {truncate(item, 100)}" for i, item in enumerate(items))
            reactions = NUMBER_EMOJI[:len(items)]
        else:
            embed.description = "👍 贊成　👎 反對"
            reactions = ["👍", "👎"]
        embed.set_footer(text=f"由 {interaction.user.display_name} 發起")
        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        for emoji in reactions:
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                break

    # ------------------------------------------------------------------ 猜歌遊戲
    @app_commands.command(name="music-quiz", description="猜歌遊戲！從播放清單隨機播放片段，搶先在聊天室打出歌名 🎧")
    @app_commands.describe(playlist_name="題庫播放清單", rounds="題數", clip_seconds="每題播放秒數")
    @app_commands.autocomplete(playlist_name=playlist_name_autocomplete)
    async def music_quiz(self, interaction: discord.Interaction, playlist_name: str,
                         rounds: app_commands.Range[int, 1, 15] = 5,
                         clip_seconds: app_commands.Range[int, 10, 40] = 20):
        guild_id = str(interaction.guild.id)
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("請先加入語音頻道！", ephemeral=True)
            return
        if interaction.guild.id in self.active_quizzes:
            await interaction.response.send_message("已經有一場猜歌遊戲在進行中了！", ephemeral=True)
            return
        songs = playlist_manager.get_playlist(guild_id, playlist_name)
        if len(songs) < 2:
            await interaction.response.send_message("題庫播放清單至少要有 2 首歌喔！", ephemeral=True)
            return
        voice_client = interaction.guild.voice_client
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            await interaction.response.send_message("正在播放音樂中，請先 `/stop` 再開始猜歌遊戲！", ephemeral=True)
            return

        await interaction.response.defer()
        voice_client = await self.player.connect_to_channel(interaction)
        if not voice_client:
            await interaction.followup.send("無法連接到語音頻道！")
            return

        self.active_quizzes[interaction.guild.id] = False
        self._quiz_channels.add(interaction.channel.id)
        state = self.player.get_state(guild_id)
        state.exclusive = "quiz"
        state.text_channel = interaction.channel
        try:
            await interaction.followup.send(
                f"🎧 **猜歌遊戲開始！** 題庫：**{playlist_name}**，共 {min(rounds, len(songs))} 題\n"
                f"每題播放約 {clip_seconds} 秒，直接在這個頻道打出歌名或歌手，最快答對的人得分！"
            )
            await self._run_quiz(interaction, voice_client, songs, rounds, clip_seconds)
        finally:
            self.active_quizzes.pop(interaction.guild.id, None)
            self._quiz_channels.discard(interaction.channel.id)
            await self._release_quiz(voice_client, guild_id)

    async def _release_quiz(self, voice_client: discord.VoiceClient, guild_id: str):
        state = self.player.get_state(guild_id)
        if state.exclusive == "quiz":
            if voice_client.is_playing() or voice_client.is_paused():
                await self.player.stop_exclusive(voice_client, guild_id, release=False)
            state.exclusive = None
        # 遊戲結束後，如果有人點了歌就接著播
        await self.player._after_exclusive(voice_client, guild_id, "quiz", None)

    async def _run_quiz(self, interaction, voice_client, songs, rounds, clip_seconds):
        guild_id = str(interaction.guild.id)
        channel = interaction.channel
        scores: Dict[int, int] = {}
        names: Dict[int, str] = {}
        pool = list(songs)
        random.shuffle(pool)
        played = 0
        failures = 0

        while played < rounds and pool and failures < 5:
            if self.active_quizzes.get(interaction.guild.id):
                await channel.send("🛑 猜歌遊戲已被停止。")
                break
            if not voice_client.is_connected():
                break
            entry = pool.pop()
            data, error = await youtube_searcher.search(entry.get("url") or entry.get("query") or entry["title"])
            if error or not data:
                failures += 1
                continue
            answer_title = entry.get("title") or data["title"]
            duration = data.get("duration") or 0
            start = 0
            if duration > clip_seconds * 2:
                start = random.randint(int(duration * 0.2), max(int(duration * 0.2), int(duration * 0.6) - clip_seconds))

            source = make_tracked_source(
                discord.FFmpegPCMAudio(data["url"], **build_ffmpeg_options(data.get("headers"), start=start,
                                                                            duration=clip_seconds)),
                self.player.get_state(guild_id).volume,
                start,
            )
            if not await self.player.play_exclusive(voice_client, guild_id, "quiz", source):
                failures += 1
                continue
            played += 1
            await channel.send(f"🎵 **第 {played} 題** — 這是哪首歌？（{clip_seconds + 5} 秒內作答）")

            def check(message: discord.Message) -> bool:
                return (message.channel.id == channel.id and not message.author.bot
                        and is_correct_guess(message.content, answer_title))

            try:
                winner_msg = await self.bot.wait_for("message", check=check, timeout=clip_seconds + 5)
            except asyncio.TimeoutError:
                winner_msg = None

            if voice_client.is_playing():
                await self.player.stop_exclusive(voice_client, guild_id, release=False)

            if winner_msg is not None:
                uid = winner_msg.author.id
                scores[uid] = scores.get(uid, 0) + 1
                names[uid] = winner_msg.author.display_name
                try:
                    await winner_msg.add_reaction("✅")
                except discord.HTTPException:
                    pass
                await channel.send(f"🎉 {winner_msg.author.mention} 答對了！答案是 **{truncate(answer_title, 150)}**")
            else:
                await channel.send(f"⌛ 時間到！答案是 **{truncate(answer_title, 150)}**")
            await asyncio.sleep(2)

        if not scores:
            await channel.send("🏁 猜歌遊戲結束！這次沒有人得分，下次加油 💪")
            return
        ranking = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏁 **猜歌遊戲結束！最終排名：**"]
        for i, (uid, score) in enumerate(ranking[:10]):
            lines.append(f"{medals[i] if i < 3 else f'{i + 1}.'} {names[uid]} — {score} 分")
        await channel.send("\n".join(lines))

    @app_commands.command(name="music-quiz-stop", description="停止進行中的猜歌遊戲")
    async def music_quiz_stop(self, interaction: discord.Interaction):
        if interaction.guild.id not in self.active_quizzes:
            await interaction.response.send_message("目前沒有進行中的猜歌遊戲！", ephemeral=True)
            return
        self.active_quizzes[interaction.guild.id] = True
        voice_client = interaction.guild.voice_client
        if voice_client:
            await self.player.stop_exclusive(voice_client, str(interaction.guild.id), release=False)
        await interaction.response.send_message("🛑 好的，這題結束後就停止遊戲！")


async def setup(bot: commands.Bot):
    await bot.add_cog(FunCommands(bot))
