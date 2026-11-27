import json
import os
import unittest
from unittest.mock import patch

import httpx
from deepseek_translation import (
    ENDPOINT,
    DeepSeekTranslator,
    DeepSeekUnavailable,
    load_key,
)


class DeepSeekTests(unittest.TestCase):
    def test_requires_a_key_from_the_standalone_launcher(self):
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": " "}),
            self.assertRaises(DeepSeekUnavailable),
        ):
            load_key()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": " test-key "}):
            self.assertEqual(load_key(), "test-key")

    def test_uses_summary_instructions_for_the_minute_transcript(self):
        translator = DeepSeekTranslator.__new__(DeepSeekTranslator)
        sentences = [
            {"ja": "受付は９時", "zh": "9点签到"},
            {"ja": "二階で集合", "zh": "在二楼集合"},
        ]
        with patch.object(
            translator, "complete", return_value="• 9点签到。"
        ) as complete:
            self.assertEqual(
                translator.summarize(sentences), "• 9点签到。"
            )
        content, instructions, timeout, _ = complete.call_args.args
        self.assertEqual(json.loads(content), sentences)
        self.assertIn("最近一分钟", instructions)
        self.assertIn("只输出中文", instructions)
        self.assertGreater(timeout, 2.5)

    def test_translates_current_sentence_using_one_sentence_of_context(self):
        def reply(request):
            self.assertEqual(str(request.url), ENDPOINT)
            data = json.loads(request.content)
            self.assertEqual(data["model"], "deepseek-flash")
            self.assertEqual(data["thinking"], {"type": "disabled"})
            self.assertEqual(
                data["messages"][-1]["content"],
                "上一句：会場です。\n当前句：撮影禁止です。",
            )
            return httpx.Response(
                200,
                text='data: {"choices":[{"delta":{"content":"禁止"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"拍摄。"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n",
            )

        client = httpx.Client(transport=httpx.MockTransport(reply))
        with (
            patch("deepseek_translation.load_key", return_value="test-key"),
            patch("deepseek_translation.httpx.Client", return_value=client),
        ):
            translator = DeepSeekTranslator()
        try:
            self.assertEqual(
                translator.translate("撮影禁止です。", "会場です。"), "禁止拍摄。"
            )
        finally:
            translator.close()

    def test_network_timeout_surfaces_a_safe_error(self):
        def timeout(request):
            raise httpx.ReadTimeout("private request details", request=request)

        client = httpx.Client(transport=httpx.MockTransport(timeout))
        with (
            patch("deepseek_translation.load_key", return_value="test-key"),
            patch("deepseek_translation.httpx.Client", return_value=client),
        ):
            translator = DeepSeekTranslator()
        try:
            with self.assertRaises(DeepSeekUnavailable) as raised:
                translator.translate("撮影禁止です。")
            self.assertEqual(str(raised.exception), "DeepSeek 连接失败或响应无效。")
        finally:
            translator.close()


if __name__ == "__main__":
    unittest.main()
