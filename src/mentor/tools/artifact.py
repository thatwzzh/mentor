"""读取被 ToolExecutor 截断并落盘的工具输出。"""

from langchain.tools import tool

from mentor.tools.artifacts import artifact_store, read_minio_ref
from mentor.tools.specs import register_tool


@register_tool(risk="read", timeout_s=5)
@tool
async def read_artifact(ref: str, offset: int = 0, limit: int = 8000) -> str:
    """读取 artifact:// 或 minio:// 引用，可按字符区间分段读取。"""
    if ref.startswith("minio://"):
        return await read_minio_ref(ref, offset=offset, limit=limit)
    return artifact_store.read(ref, offset=offset, limit=limit)
