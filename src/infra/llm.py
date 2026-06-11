"""vLLM OpenAI 兼容客户端封装。

要点：
- 本机/局域网端点要绕过系统代理（trust_env=False），否则会被代理拦截返回 502。
- 兼容 reasoning 模型：思考过程在 message.reasoning_content / reasoning，
  最终答案在 message.content。我们只取 content；若 content 为空则回退到 reasoning。
"""
from __future__ import annotations

import httpx

from .config import load_config


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or load_config()
        self.c = cfg["llm"]
        # trust_env=False => 忽略 HTTP(S)_PROXY，直连局域网 vLLM
        self._client = httpx.Client(
            base_url=self.c["base_url"],
            timeout=self.c.get("timeout", 600),
            trust_env=False,
            headers={"Authorization": f"Bearer {self.c.get('api_key', 'EMPTY')}"},
        )

    def _messages(self, system: str, user: str) -> list[dict]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def chat(self, system: str, user: str, *, max_tokens: int | None = None,
             temperature: float | None = None, think: bool = True) -> str:
        payload = {
            "model": self.c["model"],
            "messages": self._messages(system, user),
            "max_tokens": max_tokens or self.c.get("max_tokens", 8000),
            "temperature": self.c.get("temperature", 0.4) if temperature is None else temperature,
        }
        if not think:
            # 关闭思考，用于 contextual-retrieval 等轻量调用，避免 reasoning 吃 token
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        resp = self._client.post("/chat/completions", json=payload)
        if resp.status_code != 200:
            raise LLMError(f"vLLM {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        choice = data["choices"][0]
        msg = choice.get("message", {})
        content = (msg.get("content") or "").strip()
        if not content:
            # content 被 reasoning 吃光（finish_reason=length）时的兜底
            content = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
        if not content:
            raise LLMError(f"空回复 (finish_reason={choice.get('finish_reason')})")
        return content

    def chat_stream(self, system: str, user: str, *, max_tokens: int | None = None,
                    temperature: float | None = None, think: bool = True):
        """流式生成。逐步 yield (kind, text)，kind ∈ {'reasoning','content'}。"""
        import json

        payload = {
            "model": self.c["model"],
            "messages": self._messages(system, user),
            "max_tokens": max_tokens or self.c.get("max_tokens", 8000),
            "temperature": self.c.get("temperature", 0.4) if temperature is None else temperature,
            "stream": True,
        }
        if not think:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        with self._client.stream("POST", "/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta.get("reasoning_content"):
                    yield ("reasoning", delta["reasoning_content"])
                if delta.get("content"):
                    yield ("content", delta["content"])

    def ping(self) -> str:
        """返回可用模型名，用于健康检查。"""
        resp = self._client.get("/models")
        resp.raise_for_status()
        return resp.json()["data"][0]["id"]

    def close(self):
        self._client.close()
