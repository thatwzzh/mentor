"""读取被 ToolExecutor 截断并落盘的工具输出。"""

from langchain.tools import tool

from mentor.tools.artifacts import artifact_store
from mentor.tools.specs import register_tool


@register_tool(risk="read", timeout_s=5)
@tool
async def read_artifact(ref: str, offset: int = 0, limit: int = 8000) -> str:
    """读取 artifact:// 引用对应的完整工具输出，可按字符区间分段读取。"""
    return artifact_store.read(ref, offset=offset, limit=limit)
