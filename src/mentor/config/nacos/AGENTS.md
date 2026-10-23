# Nacos 资源获取与同步

## 职责与入口

- `nacos_access_token.py` 取登录令牌；`nacos_agent_spec.py` 组装 AgentSpec；`nacos_config.py` 读 YAML 通用配置。
- `nacos_prompt.py` 按声明顺序拼接 Prompt；`nacos_mcp.py` 解析 MCP 端点与工具开关；`nacos_skill.py` 把技能包落到共享卷。
- `nacos_schemas.py` 定义 `AgentSpec`、`AgentModelConfig`、`MCPServerConfig`、`SandboxProfile`、`OpenSandboxConfig`。

## 资源契约

- 所有请求带 `namespaceId=NACOS_NAMESPACE_ID`：AgentSpec 用 `name` + `label`，通用配置用 `groupName=mentor` + `dataId=<config_name>`，Prompt 用 `promptKey` + `label`。
- `ConfigName` 当前是 `Literal["sandbox_config"]`；新增通用配置要同时扩展该别名与对应 Pydantic 模型，否则读不到。
- AgentSpec 的 `content` 是 JSON；通用配置 `content` 是 YAML，先 `yaml.safe_load` 再 `model_validate`，两种格式不互换。
- 系统指令来自资源键 `instruction_<agent_name>/AGENTS__MD`，再拼接 `link_prompts`。这份远程指令服务于运行时 Agent，与仓库里的开发用 `AGENTS.md` 无关。
- MCP 读的是 admin 接口 `/nacos/v3/admin/ai/mcp`（需要管理员权限），只取 `backendEndpoints[0]`；`toolsMeta` 里 `enabled=false` 进禁用列表，`allowedTools`、`exposure`、`risk`、`timeoutSeconds`、`auth` 一并映射。
- 错误语义：HTTP 404 与业务码 404/40400 抛 `AgentSpecNotFound`；5xx、401/403、非法 JSON、校验失败抛 `AgentSpecUnavailable`。通用配置同理，内容为空按「不存在」处理。
- Prompt 查询失败返回空串并仍参与拼接，`system_prompt` 会多出空行而不报错；MCP 查询没有异常包装，网络错误会直接上抛，不降级。

## 缓存与降级

- 令牌用进程内 `asyncio.Lock` 双重检查，单调时间提前 30 秒过期，不跨进程。
- `get_agent_spec`、`get_config`、Prompt、MCP 都用 `func_cache(ttl=NACOS_TTL, lazy=True)`：到期先返回旧值再后台刷新，刷新失败保留旧值。TTL 在导入时求值，运行期改环境变量不生效。
- `get_agent_spec` 只做「读取 + override 合并」，不做技能同步；技能刷新由 `AgentFactory.factory` 调 `ensure_skills` 触发。

## 技能下发

- 目录为 `/shared/skills/<agent>/current -> <version>`，`<version>/.manifest.json` 记录技能名、内容哈希与更新时间；Agent 只读 `current`。
- 下载、哈希、解压与符号链接切换都在共享沙箱内的脚本里完成，宿主不落文件；脚本固定使用 `label=production`，因此 `NACOS_LABEL=staging` 不改变技能版本。
- 进程内 60 秒确认一次，manifest 300 秒过期；刷新用 `fcntl` 文件锁 + staging 目录 + 原子 rename，失败保留上一版本并抛 `SkillRefreshError(has_current=True)`。
- 只保留最近两个版本；技能名必须匹配 `^[A-Za-z0-9][A-Za-z0-9._-]*$`；只有从未成功过时才向上抛错。

## 修改与验证

- 新资源类型同步 schema、查询参数、缓存装饰器与调用方；接口字段以实际响应或官方文档为依据，不凭名称推断。
- 本地可用假响应验证组装、降级与缓存语义；真实验证需要 Nacos，技能场景还要 OpenSandbox 且会写共享卷。
- 各模块的 `__main__` 会访问远程服务或触发技能刷新，不能当作无副作用的检查命令。
