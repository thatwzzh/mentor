# 源码共同约定

## 适用范围与职责

本文件适用于 `src` 下的源码，在根目录指令之上补充 Python 实现约定；包内职责与跨模块调用关系见 `mentor/AGENTS.md`，目录级行为见各子目录的 `AGENTS.md`。

## 导入与类型

- 通过 `mentor...` 包路径导入，不通过修改 `sys.path` 或复制实现来跨模块调用。
- 项目使用 Python 3.13，已在用 PEP 695 语法（`type IdGetter = ...`、`async def get_config[T: BaseModel]`）；不要按更旧的 Python 版本判断这些写法无效。
- 新增函数为参数和返回值标注准确类型；配置、工具输入与结构化评分沿用既有 Pydantic 模型，避免重复定义同一契约。
- `mentor/py.typed` 是包的类型标记，必须保留。第三方类型不完整时局部处理，避免大范围引入 `Any` 或 `type: ignore`。
- 类型检查用 `uv run pyright src/mentor`；仓库没有 `pyrightconfig.json` 与 formatter 配置，不要虚构 lint 命令。

## 异步、异常与资源

- 网络、模型、沙箱、对象存储优先使用异步接口；必须调用同步 SDK 时（Tavily、psycopg）用 `asyncio.to_thread` 或线程池隔离，避免阻塞事件循环。
- `asyncio.run` 只用于同步入口和独立脚本（CLI、golden、模块 `__main__`），不用于异步调用链内部；`BaseSandbox` 的同步接口就是这个模式，不要在异步代码里调它。
- 保留取消与异常传播语义：`asyncio.CancelledError` 与 `GraphInterrupt` 不得吞掉；只在有明确契约的边界转换异常，不把系统故障转换成空结果。
- `ContextVar`（预算、沙箱 profile、LiteLLM 会话 id、子代理深度）用完必须 reset；跨 `asyncio.create_task` 时确认继承的是期望的值。
- 客户端、连接池与沙箱连接的生命周期要清晰：关闭连接、终止沙箱、删除持久卷是三种不同操作，不能混用。

## 修改与验证

- 新模块先确认职责归属，再按调用链更新装配点、注册表与测试；不要把业务逻辑默认塞进 `utils`。
- 改目录、模块名或导出名时核对 `langgraph.json`、`docker/sandbox/Dockerfile` 的 `COPY`、`Makefile`、`pyproject.toml` 的脚本入口与测试引用。
- 纯源码检查不能证明远程行为：Nacos、LiteLLM、OpenSandbox、MinIO、Langfuse 都需要真实验证；测试跳过不等于通过。
- 新增源码目录按其实际职责补充 `AGENTS.md`，纯生成目录（如 `__pycache__`）不建。
