"""主 Agent 的异步子代理使用说明；随 system prompt 自动注入。"""

ASYNC_SUBAGENT_GUIDE = """

## 异步子代理使用规则

当 `spawn_subagent` 可用时：
- 适合：只读、独立、耗时较长、可以稍后取结果的任务，例如多来源调研、按模块代码分析、多文档摘要、多视角评审。
- 不适合：写入/修改/部署/删除、必须当前轮拿到结果、强顺序依赖、需要人工审批、超长且必须可靠完成的任务。
- 子代理默认只读，write/destructive 工具会被拒绝；不要尝试绕过。
- spawn 会立即返回 job_id；必须保留 job_id，之后用 check_subagent 查询，不要编造子代理结果。
- 父级只拿摘要；不要把子代理完整 transcript 当作用户可见内容。
- 完成后不会自动唤醒父图；需要结果时调用 check_subagent，或在下一轮再查。
- 可以并行 spawn 多个独立任务，但不要为同一个任务重复 spawn。
"""


def with_async_subagent_guide(system_prompt: str) -> str:
    """在已有 system prompt 后追加异步子代理规则。"""
    return f"{system_prompt.rstrip()}{ASYNC_SUBAGENT_GUIDE}"
