"""AI 聊天引擎：使用 Claude API；沒有設定 API 金鑰時改用內建的簡易聊天。"""
import datetime
import json
import logging
import os
import random
import re
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional

logger = logging.getLogger("MusicBot.chat")

try:
    import anthropic
except ImportError:  # 沒裝 anthropic 套件時仍可使用離線聊天
    anthropic = None

DEFAULT_MODEL = "claude-opus-5-5"
HISTORY_LIMIT = 30          # 每個頻道記住的訊息數
MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """你是「{bot_name}」，一個住在 Discord 伺服器裡的音樂機器人，同時也是大家的聊天夥伴。

說話風格：
- 一律使用台灣習慣的繁體中文（除非對方用其他語言跟你聊）。
- 語氣親切、幽默、有點俏皮，像熟悉的朋友，可以適度使用顏文字或 emoji，但不要每句都用。
- 回覆精簡，通常 1～4 句話；除非對方明確要求詳細說明，否則不要超過 300 字。
- 這是群組聊天，使用者訊息的格式是「暱稱：內容」，回覆時可以直接叫對方的暱稱，但不要在開頭加上自己的名字。
- 不要使用 Markdown 標題或表格；可以用粗體強調重點。

你可以使用工具來控制音樂：
- 有人想聽歌、要你放歌或推薦並播放時，用 play_music 把歌加入待播清單（一次最多 5 首）。
- 跳過、暫停、繼續、停止、隨機播放用 control_music。
- 問現在在放什麼、待播清單有什麼時，用 get_music_status 查詢後再回答。
- 只是聊音樂、問推薦但沒有要你播放時，直接聊天就好，不要擅自播放。
- 工具回報失敗時，用輕鬆的語氣告訴對方原因（例如需要先加入語音頻道）。

其他常用指令（可以在適當時提醒大家）：/play、/search、/queue、/nowplaying、/lyrics、/loop、/volume、/music-quiz（猜歌遊戲）、/fortune（今日運勢）、/8ball（神奇海螺）。"""

TOOLS = [
    {
        "name": "play_music",
        "description": "把一首或多首歌加入目前發話者所在語音頻道的待播清單並開始播放。query 用歌名加歌手效果最好，例如「周杰倫 晴天」。",
        "input_schema": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要播放的歌曲搜尋關鍵字，最多 5 個",
                }
            },
            "required": ["queries"],
            "additionalProperties": False,
        },
    },
    {
        "name": "control_music",
        "description": "控制音樂播放：skip 跳過、pause 暫停、resume 繼續、stop 停止並清空、shuffle 打亂待播清單。",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["skip", "pause", "resume", "stop", "shuffle"]},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_music_status",
        "description": "查詢目前正在播放的歌曲、播放進度與接下來的待播清單。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

ToolHandler = Callable[[str, Dict[str, Any]], Awaitable[str]]


class OfflineResponder:
    """沒有 API 金鑰時使用的簡易聊天（規則式）。"""

    RULES = [
        (r"(你好|哈囉|嗨|hi|hello|安安|早安|午安|晚安)", [
            "嗨嗨～{name}！今天想聽什麼歌呢？🎵", "{name} 安安！我在這裡喔 (｡•ᴗ-)✧", "哈囉 {name}～要不要來一首歌？",
        ]),
        (r"(你是誰|你叫什麼|自我介紹)", [
            "我是這個伺服器的音樂機器人！可以放歌、陪聊天、玩猜歌遊戲 🎧 輸入 /play 試試看吧！",
        ]),
        (r"(謝謝|感謝|thx|thank)", ["不客氣～ (≧▽≦)", "小事一樁！有需要再叫我 🎶", "能幫上忙就好 💗"]),
        (r"(推薦|好聽|聽什麼|想聽)", [
            "推薦你聽聽 {song}！用 `/play {song}` 就能播放喔 🎵",
            "今天的心情適合 {song}，要不要來一首？",
        ]),
        (r"(幾點|時間|現在)", ["現在是台灣時間 {time} ⏰"]),
        (r"(無聊|好累|難過|心情不好|煩)", [
            "抱抱 {name} 🤗 要不要聽首歌放鬆一下？我推薦 {song}。", "辛苦了！來點音樂補血吧～ `/play {song}`",
        ]),
        (r"(笑話|好笑)", [
            "為什麼音符不能考試作弊？因為它們都會被「抓到拍子」🎼",
            "什麼歌最冷？……《冰雨》🥶",
            "鋼琴為什麼很難過？因為它有很多黑鍵（心事）🎹",
        ]),
        (r"(晚安|睡覺|去睡)", ["晚安 {name}～祝好夢 🌙", "早點休息喔！要不要來首睡前歌？🌙"]),
    ]
    FALLBACK = [
        "嗯嗯，我在聽！(｡•̀ᴗ-)✧", "真的假的！再多說一點～", "哈哈哈 {name} 好有趣", "原來如此 🤔",
        "我現在還在學說話中，不過可以幫你放歌喔！試試 `/play`",
    ]
    SONGS = ["周杰倫 晴天", "五月天 倔強", "告五人 愛人錯過", "鄧紫棋 光年之外", "田馥甄 小幸運", "茄子蛋 浪子回頭"]

    def reply(self, name: str, text: str) -> str:
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        values = {"name": name, "song": random.choice(self.SONGS), "time": now.strftime("%H:%M")}
        low = text.lower()
        for pattern, answers in self.RULES:
            if re.search(pattern, low):
                return random.choice(answers).format(**values)
        return random.choice(self.FALLBACK).format(**values)


class ChatEngine:
    def __init__(self):
        self._histories: Dict[int, Deque[Dict[str, Any]]] = {}
        self._offline = OfflineResponder()
        self._client = None
        self.model = os.getenv("CHAT_MODEL", DEFAULT_MODEL)
        self.effort = os.getenv("CHAT_EFFORT", "low")
        self._init_client()

    def _init_client(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if anthropic is None:
            logger.info("未安裝 anthropic 套件，聊天功能使用離線模式")
            return
        if not api_key:
            logger.info("未設定 ANTHROPIC_API_KEY，聊天功能使用離線模式")
            return
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=60.0, max_retries=2)
        logger.info("AI 聊天已啟用（模型：%s）", self.model)

    @property
    def online(self) -> bool:
        return self._client is not None

    async def aclose(self):
        if self._client is not None:
            await self._client.close()
            self._client = None

    def reset(self, channel_id: int):
        self._histories.pop(channel_id, None)

    def _history(self, channel_id: int) -> Deque[Dict[str, Any]]:
        if channel_id not in self._histories:
            self._histories[channel_id] = deque(maxlen=HISTORY_LIMIT)
        return self._histories[channel_id]

    def remember(self, channel_id: int, speaker: str, text: str):
        """記住頻道裡的對話（不需要回覆的訊息也記下來，讓 AI 有上下文）。"""
        self._history(channel_id).append({"role": "user", "content": f"{speaker}：{text}"})

    @staticmethod
    def _build_messages(history: Deque[Dict[str, Any]]) -> List[Dict[str, Any]]:
        messages = list(history)
        # 開頭必須是 user
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        return messages

    async def reply(self, channel_id: int, speaker: str, text: str, bot_name: str,
                    tool_handler: Optional[ToolHandler] = None, context: str = "") -> str:
        history = self._history(channel_id)
        history.append({"role": "user", "content": f"{speaker}：{text}"})

        if not self.online:
            answer = self._offline.reply(speaker, text)
            history.append({"role": "assistant", "content": answer})
            return answer

        try:
            answer = await self._ask_claude(history, bot_name, tool_handler, context)
        except Exception as e:  # 確保任何 API 錯誤都不會讓機器人壞掉
            answer = self._describe_error(e)
            # 失敗的那句不要留在記憶中，避免下次又觸發同樣錯誤
            if history and history[-1]["role"] == "user":
                history.pop()
            return answer

        history.append({"role": "assistant", "content": answer})
        return answer

    async def _ask_claude(self, history, bot_name, tool_handler, context) -> str:
        messages = self._build_messages(history)
        if context:
            # 動態資訊（播放狀態等）附在最後一則使用者訊息，不放進 system prompt，讓快取有效
            last = dict(messages[-1])
            last["content"] = f"{last['content']}\n\n（系統資訊：{context}）"
            messages[-1] = last

        system = SYSTEM_PROMPT.format(bot_name=bot_name)
        tools = TOOLS if tool_handler is not None else []
        texts: List[str] = []

        for _ in range(MAX_TOOL_ROUNDS):
            kwargs = dict(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=messages,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
            )
            if tools:
                kwargs["tools"] = tools
            if self.model.startswith(("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")):
                kwargs["betas"] = ["server-side-fallback-2026-07-01"]
                kwargs["fallbacks"] = "default"
            response = await self._client.beta.messages.create(**kwargs)

            if response.stop_reason == "refusal":
                return "這個問題我沒辦法回答耶，我們聊點別的吧～ 🙏"

            round_texts = [b.text for b in response.content if b.type == "text" and b.text.strip()]
            texts.extend(round_texts)

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses or tool_handler is None:
                break

            # 回傳完整 content（包含 thinking 區塊），工具結果放在同一則 user 訊息
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in tool_uses:
                try:
                    tool_input = block.input if isinstance(block.input, dict) else json.loads(block.input)
                    output = await tool_handler(block.name, tool_input)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
                except Exception as e:
                    logger.exception("工具執行失敗 %s", block.name)
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": f"執行失敗：{e}", "is_error": True})
            messages.append({"role": "user", "content": results})
            texts = []  # 只保留最後一輪的回答文字，避免重複

        answer = "\n".join(texts).strip()
        return answer or "（思考了一下，但不知道該說什麼 😅）"

    @staticmethod
    def _describe_error(e: Exception) -> str:
        if anthropic is not None:
            if isinstance(e, anthropic.AuthenticationError):
                return "⚠️ AI 金鑰無效，請管理員檢查 `.env` 裡的 `ANTHROPIC_API_KEY`。"
            if isinstance(e, anthropic.PermissionDeniedError):
                return "⚠️ AI 金鑰沒有使用權限，請管理員檢查帳號設定。"
            if isinstance(e, anthropic.NotFoundError):
                return "⚠️ 找不到設定的 AI 模型，請管理員檢查 `CHAT_MODEL` 設定。"
            if isinstance(e, anthropic.RateLimitError):
                return "我現在有點忙不過來，等幾秒再跟我說話好嗎？😵‍💫"
            if isinstance(e, anthropic.BadRequestError):
                logger.warning("AI 請求錯誤: %s", e)
                return "⚠️ AI 請求失敗（可能是額度用完或設定錯誤），請管理員查看主控台訊息。"
            if isinstance(e, anthropic.APIStatusError):
                return "AI 伺服器好像有點狀況，等一下再試試看 🙏"
            if isinstance(e, anthropic.APIConnectionError):
                return "連不上 AI 伺服器，請檢查網路連線 🌐"
        logger.exception("AI 聊天發生未預期的錯誤", exc_info=e)
        return "我剛剛恍神了一下……再說一次好嗎？😵"


chat_engine: Optional[ChatEngine] = None


def get_chat_engine() -> ChatEngine:
    """延遲建立（確保 .env 已經載入）。"""
    global chat_engine
    if chat_engine is None:
        chat_engine = ChatEngine()
    return chat_engine
