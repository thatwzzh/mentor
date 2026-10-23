"""可选 embedding：配置了 MEMORY_EMBEDDING_MODEL 才调用，否则 lexical fallback。"""

import logging

from langchain_openai import OpenAIEmbeddings
from pydantic import SecretStr

from mentor.config.memory_settings import memory_settings
from mentor.config.openai_settings import openai_settings

logger = logging.getLogger(__name__)


class MemoryEmbedder:
    def __init__(self) -> None:
        self._client: OpenAIEmbeddings | None = None
        self._enabled = bool(
            memory_settings.embedding_model
            and openai_settings.api_key
            and openai_settings.base_url
        )
        if self._enabled:
            self._client = OpenAIEmbeddings(
                model=memory_settings.embedding_model,
                base_url=openai_settings.base_url,
                api_key=SecretStr(openai_settings.api_key),
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def embed(self, text: str) -> list[float] | None:
        if self._client is None or not text.strip():
            return None
        try:
            vector = await self._client.aembed_query(text)
        except Exception as exc:
            logger.warning("记忆 embedding 失败，回退 lexical: %s", exc)
            return None
        if len(vector) != memory_settings.embedding_dim:
            logger.warning(
                "记忆 embedding 维度不匹配: got=%s expected=%s",
                len(vector),
                memory_settings.embedding_dim,
            )
            return None
        return vector


_embedder: MemoryEmbedder | None = None


def get_memory_embedder() -> MemoryEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = MemoryEmbedder()
    return _embedder
