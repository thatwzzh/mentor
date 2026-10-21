"""Agent 装配层：按 Nacos 规格构建主 Agent 与子 Agent。"""

# 导入内置子 Agent 模块以触发 @register_subagent 注册；新增子 Agent 时在此追加导入。
from mentor.agents import web as _web  # noqa: F401
