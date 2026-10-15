"""OpenAI Chat Model 集成，补全 reasoning_content（思维链）提取。

LangChain 官方 `ChatOpenAI` 明确不提取第三方扩展字段
（参见 langchain_openai/chat_models/base.py:628-630）：
    "Non-standard response fields added by third-party providers
    (e.g., `reasoning_content`) are not extracted."

本模块提供 `ReasoningChatModel`，继承 `ChatOpenAI` 并补全
流式和非流式场景下的 reasoning_content 提取。

提取的思维链以 ``ReasoningContentBlock(type="reasoning", ...)``
直接写入 v1 content_blocks，不经过 additional_kwargs 中转。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessageChunk


class ReasoningChatModel(ChatOpenAI):
    """继承 ChatOpenAI，自动从 API 响应中提取思维链内容。

    v1 output_mode 下思维链以 ``ReasoningContentBlock`` 存入
    ``message.content_blocks``，LangSmith 可直接渲染。

    用法::

        model = ReasoningChatModel(
            model="qwen3.7-max",
            temperature=0,
        )
        response = model.invoke("什么是量子计算？")
        # 思维链内容
        for block in response.content_blocks:
            if block["type"] == "reasoning":
                print(block["reasoning"])
        # 最终回答
        print(response.text)
    """

    output_version: str = "v1"  # type: ignore # 启用 content_blocks，LangSmith 可渲染

    # ---- 非流式 ------------------------------------------------------------

    def _create_chat_result(
        self,
        response: dict[str, Any] | Any,
        generation_info: dict[str, Any] | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)

        response_dict = (
            response if isinstance(response, dict) else response.model_dump()
        )
        choices = response_dict.get("choices", [])

        for i, choice in enumerate(choices):
            if i >= len(result.generations):
                break
            msg = choice.get("message", {})
            reasoning = msg.get("reasoning_content") or msg.get("reasoning")
            if reasoning:
                gen = result.generations[i]
                gen.message.additional_kwargs["reasoning_content"] = reasoning

        # 非流式下基类不会做 v1 转换，手动转为 content_blocks
        for gen in result.generations:
            if gen.message.response_metadata.get("output_version") != "v1":
                gen.message = gen.message.model_copy(
                    update={
                        "content": gen.message.content_blocks,
                        "response_metadata": {
                            **gen.message.response_metadata,
                            "output_version": "v1",
                        },
                    },
                )
            # content_blocks 已包含 reasoning，清理 additional_kwargs 冗余
            gen.message.additional_kwargs.pop("reasoning_content", None)

        return result

    # ---- 流式 --------------------------------------------------------------

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type[BaseMessageChunk],
        base_generation_info: dict[str, Any] | None,
    ) -> ChatGenerationChunk | None:
        gen_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen_chunk is None:
            return None

        choices = chunk.get("choices", [])
        if not choices:
            return gen_chunk

        delta = choices[0].get("delta", {})
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        text = delta.get("content", "") or ""

        blocks: list[dict[str, Any]] = []
        if reasoning:
            blocks.append({"type": "reasoning", "reasoning": reasoning, "index": 0})
        if text:
            blocks.append({"type": "text", "text": text, "index": 1})

        if blocks:
            gen_chunk.message.content = blocks  # type: ignore[assignment]
            gen_chunk.message.response_metadata["output_version"] = "v1"

        return gen_chunk


# ---- 注册到 LangChain 集成表 -----------------------------------------------

from langchain.chat_models.base import _BUILTIN_PROVIDERS, _call

_PROVIDER_KEY = "openai_reasoning"

if _PROVIDER_KEY not in _BUILTIN_PROVIDERS:
    _BUILTIN_PROVIDERS[_PROVIDER_KEY] = (
        "mentor.utils.reasoning_chat_model",
        "ReasoningChatModel",
        _call,
    )
