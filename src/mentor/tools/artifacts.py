"""工具大输出落盘：超过阈值时写文件并返回 artifact:// 引用。

工具结果仍以可读预览返回给模型；完整内容留在本地 artifact 目录，模型可以再调用
read_artifact 分段读取。目录可用 TOOL_ARTIFACT_DIR 覆盖。
"""

import io
import logging
import re
import tempfile
import uuid
from pathlib import Path

from mentor.config.minio_settings import minio_settings
from mentor.config.tool_settings import tool_settings
from mentor.utils.minio import ContentTypeStrategy, MinIO

logger = logging.getLogger(__name__)


class ArtifactStore:
    """本地 artifact 存储；文件名由服务端生成，不接受调用方拼路径。"""

    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or tool_settings.artifact_dir or str(
            Path(tempfile.gettempdir()) / "mentor-tool-artifacts"
        )
        self.root = Path(configured)

    def _ensure_root(self) -> None:
        if self.root.is_dir():
            return
        self.root.mkdir(parents=True, exist_ok=True)

    def maybe_offload(
        self,
        text: str,
        *,
        max_chars: int,
        tool_name: str,
        call_id: str = "",
    ) -> tuple[str, list[str]]:
        """超长输出落盘，返回 (预览, artifact 引用列表)。"""
        if len(text) <= max_chars:
            return text, []
        self._ensure_root()
        safe_tool = re.sub(r"[^a-zA-Z0-9_-]", "-", tool_name)[:40] or "tool"
        # 文件名始终带随机后缀，避免不同用户/并发调用互相覆盖或靠 call_id 猜测。
        filename = f"{safe_tool}-{uuid.uuid4().hex}.txt"
        path = self.root / filename
        path.write_text(text, encoding="utf-8")
        ref = f"artifact://{filename}"
        # 5.3：头 4KB + 尾 1KB + 省略标记；max_chars 很小时按比例收缩。
        head_chars = min(4_096, max_chars)
        tail_chars = min(1_024, max(0, max_chars - head_chars))
        omitted = len(text) - head_chars - tail_chars
        head = text[:head_chars]
        tail = text[-tail_chars:] if tail_chars else ""
        preview = (
            f"{head}\n\n"
            f"[输出超过 {max_chars} 字符，已省略约 {omitted} 字符；完整内容见 {ref}]"
            f"{('\n\n' + tail) if tail else ''}"
        )
        return preview, [ref]

    def read(self, ref: str, offset: int = 0, limit: int = 8_000) -> str:
        """读取 artifact；ref 必须是服务端生成的 artifact://<filename>。"""
        if not ref.startswith("artifact://"):
            raise ValueError("artifact 引用格式必须是 artifact://<filename>")
        filename = ref.removeprefix("artifact://")
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            raise ValueError("artifact 引用包含非法路径")
        path = self.root / filename
        if not path.is_file():
            raise FileNotFoundError(f"artifact 不存在: {ref}")
        offset = max(offset, 0)
        limit = max(limit, 0)
        return path.read_text(encoding="utf-8")[offset : offset + limit]


artifact_store = ArtifactStore()


class MinioArtifactMirror:
    """把被截断的工具全文镜像到 MinIO；未配置时静默跳过。

    本地 artifact 仍是模型可读引用；MinIO 用于持久化/审计，envelope 会额外
    返回 minio_ref。
    """

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = tool_settings.minio_artifacts if enabled is None else enabled

    async def mirror(self, text: str, *, tool_name: str, call_id: str) -> str | None:
        if not self.enabled:
            return None
        if not minio_settings.access_key or not minio_settings.secret_key:
            return None
        safe_tool = re.sub(r"[^a-zA-Z0-9_-]", "-", tool_name)[:40] or "tool"
        key = f"tool-artifacts/{safe_tool}-{uuid.uuid4().hex}.txt"
        try:
            client = MinIO(
                endpoint=minio_settings.endpoint,
                access_key=minio_settings.access_key,
                secret_key=minio_settings.secret_key,
                secure=minio_settings.secure,
                content_type_strategy=ContentTypeStrategy(
                    default_content_type="text/plain; charset=utf-8"
                ),
            )
            await client.write_file(
                bucket_name=minio_settings.bucket,
                filename=key,
                stream=io.BytesIO(text.encode("utf-8")),
            )
            return f"minio://{minio_settings.bucket}/{key}"
        except Exception as exc:
            logger.warning("工具全文镜像 MinIO 失败 tool=%s: %s", tool_name, exc)
            return None


minio_artifact_mirror = MinioArtifactMirror()


async def read_minio_ref(ref: str, offset: int = 0, limit: int = 8_000) -> str:
    """读取 minio://bucket/key 的工具全文；未配置 MinIO 时返回输入错误。"""
    if not ref.startswith("minio://"):
        raise ValueError("minio 引用格式必须是 minio://bucket/key")
    bucket, _, key = ref.removeprefix("minio://").partition("/")
    if not bucket or not key:
        raise ValueError("minio 引用缺少 bucket/key")
    if not minio_settings.access_key or not minio_settings.secret_key:
        raise ValueError("MinIO 未配置，无法读取 minio 引用")
    client = MinIO(
        endpoint=minio_settings.endpoint,
        access_key=minio_settings.access_key,
        secret_key=minio_settings.secret_key,
        secure=minio_settings.secure,
    )
    data = await client.read_file(bucket_name=bucket, filename=key)
    text = data.decode("utf-8", errors="replace")
    offset = max(offset, 0)
    limit = max(limit, 0)
    return text[offset : offset + limit]
