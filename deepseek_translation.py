import json
import os
import time

import httpx

HOST = "api.deepseek.com"
ENDPOINT = f"https://{HOST}/chat/completions"
MODEL = "deepseek-flash"
TIMEOUT = 2.5
SUMMARY_TIMEOUT = 12
RULES = (
    "你是日语现场字幕的翻译器。只输出当前日语片段的简体中文译文，简短自然。"
    "完整保留人名、数字、时间、否定、条件和因果，不增添事实。"
    "如果提供上一句，它仅供理解。当前句未说完时只翻译已表达的内容。"
    "无法可靠理解的片段用【原文不清】表示，不猜测缺失内容。"
)
SUMMARY_RULES = (
    "下面是最近一分钟依次出现的日语现场字幕。用简体中文概括这个时间段实际讲了什么，"
    "优先写当前话题和结论。通常输出2到3条，每条一行，以项目符号开头并尽量控制在35字内。"
    "信息少时可以只写一条。直接输出纯文本要点。"
    "保留关键人名、数字、安排和限制条件。区分提议、决定和不确定内容，不补写听不清的信息。"
)


class DeepSeekUnavailable(RuntimeError):
    pass


def load_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    raise DeepSeekUnavailable("请通过 Start.cmd 或 DEEPSEEK_API_KEY 配置 DeepSeek。")


class DeepSeekTranslator:
    def __init__(self):
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {load_key()}"},
            timeout=httpx.Timeout(TIMEOUT),
            follow_redirects=False,
        )

    def translate(self, text: str, previous: str = "") -> str:
        content = f"上一句：{previous}\n当前句：{text}" if previous else text
        return self.complete(content, RULES, TIMEOUT, 160)

    def summarize(self, transcript: str) -> str:
        return self.complete(transcript, SUMMARY_RULES, SUMMARY_TIMEOUT, 260)

    def complete(
        self, content: str, rules: str, timeout: float, max_tokens: int
    ) -> str:
        request = {
            "model": MODEL,
            "thinking": {"type": "disabled"},
            "stream": True,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": rules},
                {"role": "user", "content": content},
            ],
        }
        deadline = time.monotonic() + timeout
        pieces = []
        finish_reason = None
        try:
            with self.client.stream(
                "POST", ENDPOINT, json=request, timeout=timeout
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if time.monotonic() >= deadline:
                        raise DeepSeekUnavailable("DeepSeek 翻译超时。")
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    for choice in json.loads(data).get("choices", []):
                        delta = choice.get("delta", {}).get("content")
                        if delta:
                            pieces.append(delta)
                        finish_reason = choice.get("finish_reason") or finish_reason
        except httpx.HTTPStatusError as exc:
            raise DeepSeekUnavailable(
                f"DeepSeek 返回 HTTP {exc.response.status_code}。"
            ) from None
        except (httpx.RequestError, ValueError):
            raise DeepSeekUnavailable("DeepSeek 连接失败或响应无效。") from None

        translated = "".join(pieces).strip()
        if finish_reason != "stop" or not translated:
            raise DeepSeekUnavailable("DeepSeek 未返回完整译文。")
        return translated

    def close(self):
        self.client.close()
