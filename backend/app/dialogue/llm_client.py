"""OpenAI 兼容接口的轻量封装。

- 从 config.Settings 读取 Key / Base URL / Model（默认 DeepSeek）。
- 未配置 Key 时抛出清晰的 LLMError。
- 支持按次覆盖模型（模型路由）与注入自定义 client（测试）。
- 记录 token 用量，便于成本观测。
"""

import logging

from ..config import Settings, get_settings
from ..logging_config import log_event

logger = logging.getLogger("xiaonuan.llm")


class LLMError(RuntimeError):
    """调用大模型失败（缺 Key、网络错误、接口报错等）。"""


class LLMClient:
    def __init__(self, settings: Settings | None = None, client=None):
        self.settings = settings or get_settings()
        self._client = client

    @property
    def client(self):
        if self._client is None:
            if not self.settings.has_api_key:
                raise LLMError(
                    "未配置 DEEPSEEK_API_KEY，无法调用大模型。"
                    "请设置环境变量 DEEPSEEK_API_KEY 后重试。"
                )
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - 依赖缺失
                raise LLMError("需要安装 openai 库：pip install openai") from exc
            self._client = OpenAI(
                api_key=self.settings.api_key,
                base_url=self.settings.base_url,
            )
        return self._client

    def _log_usage(self, model, usage):
        if not usage:
            return
        log_event(
            logger,
            "llm_usage",
            model=model,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
        )

    def _extra_body(self) -> dict:
        """DeepSeek 默认开启思考模式（慢且忽略 temperature），这里按配置显式开关。"""
        return {
            "thinking": {
                "type": "enabled" if self.settings.thinking_enabled else "disabled"
            }
        }

    def chat(self, messages, temperature=None, max_tokens=None, model=None) -> str:
        """调用 chat.completions，返回回复正文。"""
        model = model or self.settings.model
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=(
                    self.settings.temperature if temperature is None else temperature
                ),
                max_tokens=self.settings.max_tokens if max_tokens is None else max_tokens,
                extra_body=self._extra_body(),
            )
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"调用大模型失败：{exc}") from exc

        self._log_usage(model, getattr(response, "usage", None))
        content = response.choices[0].message.content
        return (content or "").strip()

    def chat_stream(self, messages, temperature=None, max_tokens=None, model=None):
        """流式调用，逐段 yield 回复文本。"""
        model = model or self.settings.model
        try:
            stream = self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=(
                    self.settings.temperature if temperature is None else temperature
                ),
                max_tokens=self.settings.max_tokens if max_tokens is None else max_tokens,
                stream=True,
                extra_body=self._extra_body(),
            )
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"调用大模型失败：{exc}") from exc

        try:
            for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
        except Exception as exc:
            raise LLMError(f"流式调用大模型失败：{exc}") from exc
