"""AI 聊天測試：用本機假的 API 伺服器驗證 Claude 請求格式與工具呼叫流程。

python -m unittest tests.test_chat
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web  # noqa: E402

from src.utils import ai_chat  # noqa: E402


def _message(content, stop_reason="end_turn"):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


class FakeAnthropicServer:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.headers = []

    async def handle(self, request: web.Request):
        self.requests.append(await request.json())
        self.headers.append(dict(request.headers))
        return web.json_response(self.responses.pop(0))

    async def __aenter__(self):
        app = web.Application()
        app.router.add_post("/v1/messages", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *exc):
        await self.runner.cleanup()


@unittest.skipIf(ai_chat.anthropic is None, "需要 anthropic 套件")
class ChatEngineTests(unittest.TestCase):
    def _engine(self, base_url):
        engine = ai_chat.ChatEngine.__new__(ai_chat.ChatEngine)
        engine._histories = {}
        engine._offline = ai_chat.OfflineResponder()
        engine.model = "claude-opus-5-5"
        engine.effort = "low"
        engine._client = ai_chat.anthropic.AsyncAnthropic(api_key="test-key", base_url=base_url, max_retries=0)
        return engine

    def test_tool_loop_and_request_shape(self):
        responses = [
            _message([
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": "好喔，馬上幫你放！"},
                {"type": "tool_use", "id": "toolu_1", "name": "play_music", "input": {"queries": ["周杰倫 晴天"]}},
            ], stop_reason="tool_use"),
            _message([{"type": "text", "text": "已經幫你點了 **晴天** 🎵"}]),
        ]
        tool_calls = []

        async def handler(name, args):
            tool_calls.append((name, args))
            return "開始播放：周杰倫 - 晴天"

        async def scenario():
            async with FakeAnthropicServer(responses) as server:
                engine = self._engine(server.base_url)
                answer = await engine.reply(7, "小明", "幫我放晴天", "SWJ", tool_handler=handler,
                                            context="目前沒有播放音樂")
                await engine._client.close()
                return server, engine, answer

        server, engine, answer = asyncio.run(scenario())
        self.assertEqual(answer, "已經幫你點了 **晴天** 🎵")
        self.assertEqual(tool_calls, [("play_music", {"queries": ["周杰倫 晴天"]})])

        first, second = server.requests
        self.assertEqual(first["model"], "claude-opus-5-5")
        self.assertEqual(first["fallbacks"], "default")
        self.assertEqual(first["output_config"], {"effort": "low"})
        self.assertNotIn("thinking", first)
        self.assertEqual({t["name"] for t in first["tools"]}, {"play_music", "control_music", "get_music_status"})
        self.assertIn("SWJ", first["system"])
        self.assertTrue(first["messages"][0]["content"].startswith("小明：幫我放晴天"))
        self.assertIn("系統資訊", first["messages"][0]["content"])
        self.assertIn("server-side-fallback-2026-07-01", server.headers[0].get("anthropic-beta", ""))

        # 第二次請求要原封不動帶回 assistant 內容（含 thinking），並附上 tool_result
        assistant = second["messages"][1]
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual([b["type"] for b in assistant["content"]], ["thinking", "text", "tool_use"])
        tool_result = second["messages"][2]["content"][0]
        self.assertEqual(tool_result["type"], "tool_result")
        self.assertEqual(tool_result["tool_use_id"], "toolu_1")

        # 記憶中只保存文字（不含系統資訊）
        history = list(engine._histories[7])
        self.assertEqual(history[0], {"role": "user", "content": "小明：幫我放晴天"})
        self.assertEqual(history[1], {"role": "assistant", "content": "已經幫你點了 **晴天** 🎵"})

    def test_refusal_and_errors(self):
        async def scenario():
            async with FakeAnthropicServer([_message([], stop_reason="refusal")]) as server:
                engine = self._engine(server.base_url)
                refused = await engine.reply(1, "A", "嗨", "SWJ")
                await engine.aclose()
            # 連不上伺服器時回覆友善訊息，且不把失敗的訊息留在記憶中
            engine = self._engine("http://127.0.0.1:9")
            failed = await engine.reply(2, "A", "嗨", "SWJ")
            await engine.aclose()
            return refused, failed, engine

        refused, failed, engine = asyncio.run(scenario())
        self.assertIn("沒辦法回答", refused)
        self.assertIn("連不上", failed)
        self.assertEqual(len(engine._histories[2]), 0)

    def test_offline_mode(self):
        engine = ai_chat.ChatEngine.__new__(ai_chat.ChatEngine)
        engine._histories = {}
        engine._offline = ai_chat.OfflineResponder()
        engine._client = None
        answer = asyncio.run(engine.reply(3, "小華", "你好", "SWJ"))
        self.assertIn("小華", answer)
        self.assertFalse(engine.online)


if __name__ == "__main__":
    unittest.main()
