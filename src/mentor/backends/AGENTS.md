# OpenSandbox 后端适配

## 职责与入口

`sandbox_backend.py:SandboxBackend` 实现 Deep Agents `BaseSandbox` 协议（`execute`、`upload_files`、`download_files`、`adelete` 及异步版本）；连接、创建与复用逻辑在 `utils/sandbox.py`。

## 身份、缓存与卷

- `id` 默认取 `utils/shared.get_user_id()`，即认证用户的 identity；它是连接缓存 key 与 PVC 名的一部分，不是 OpenSandbox 的 `sandbox_id`。
- 连接缓存 key 为 `(user_id, thread_id or "")`，`aclose_run` 在 run 结束时弹出并关闭，`aclose_all` 供测试与运维使用。
- 私有卷挂 `/root` 可写，PVC 名 `<tenant>-<sha256(user_id)[:12]>`；共享卷 `sandbox-shared-data` 挂 `/shared` 只读。
- PVC 的 `deleteOnSandboxTermination=False`，关闭连接不会删除用户工作区；容器与卷的生命周期仍由 OpenSandbox TTL 管理。
- `id_getter` 只能由可信调用方注入，不能暴露成模型可填写的工具参数；认证失败不回退到公共 key。

## 执行与返回契约

- `aexecute` 的 timeout 为 `None` 或负数时取 `command_timeout_seconds`：有 profile 用 profile 值，否则读 Nacos `sandbox_config`。生命周期超时、就绪超时与命令超时是三个不同参数。
- 命令通道异常时先 `aclose_run()` 丢弃坏连接再抛错，避免坏连接被后续工具复用。
- 上传与下载逐项返回成功或失败，单项失败不影响其他项；两者当前都不做路径收敛。
- `adelete` 先做词法校验（绝对路径、无 `..`、无换行与 NUL、必须位于 `/root/` 下且不等于 `/root`），再用 `realpath -m` 解析父目录，防止符号链接逃逸；删除不存在的路径视为幂等成功。
- 同步接口用 `asyncio.run` 包装，不能在运行中的事件循环里调用。
- 空闲回收只在创建新连接的路径上触发；`OPEN_SANDBOX_IDLE_TIMEOUT_SECONDS<=0` 表示不主动回收。

## 修改与验证

- 协议调整同步检查 `agents/agent_factory.py:get_backend`、`middlewares/sandbox_lifecycle.py`、`tools/deploy.py`（它自己 new 一个 backend 并在 finally 关闭）与 `utils/sandbox.py`。
- 改卷名映射时核对名称碰撞与存量用户数据，不能只改一半就声称隔离仍成立。
- 单测覆盖 `tests/units/test_security_fixes.py` 的删除路径校验与 `test_sandbox_profile.py` 的 profile 超时；`tests/intergrations/test_deploy.py` 需要真实 MinIO 与沙箱镜像。其余文件操作、空闲回收与多租户隔离没有自动化验证。
