"""工具大输出落盘：超过阈值时写文件并返回 artifact:// 引用。

工具结果仍以可读预览返回给模型；完整内容留在本地 artifact 目录，模型可以再调用
read_artifact 分段读取。目录可用 TOOL_ARTIFACT_DIR 覆盖。
"""

import re
import tempfile
import uuid
from pathlib import Path

from mentor.config.tool_settings import tool_settings


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
        preview = text[:max_chars].rstrip()
        return (
            f"{preview}\n\n[输出超过 {max_chars} 字符，已截断；完整内容见 {ref}]",
            [ref],
        )

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
