"""LLM provider 抽象与路由：fake（内置）/ OpenAI 兼容 / 未配置三态。"""
from typing import Optional, Protocol

from .. import config
from .fake import FakeProvider
from .openai_compat import OpenAICompatProvider


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str: ...
    def embed(self, text: str) -> list[float]: ...


def get_provider() -> Optional[LLMProvider]:
    """未配置时返回 None，调用方应优雅降级（跳过而非报错）。"""
    if config.LLM_PROVIDER == "fake":
        return FakeProvider()
    if config.LLM_BASE_URL and config.LLM_API_KEY:
        return OpenAICompatProvider(
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            model=config.LLM_MODEL,
            embed_model=config.LLM_EMBED_MODEL,
        )
    return None
