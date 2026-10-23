# 身份认证与资源授权

## 职责与入口

`auth.py:auth` 是 `langgraph.json` 的鉴权入口，通过 `Auth` 注册 `authenticate`、`store`、`assistants` 与通用处理器。当前用户来源是 `utils/mock_user.py`，生产用户中心尚未接入。

## 当前授权契约

- `authenticate` 按空格切分 Authorization，只要求 token 非空，不校验 scheme 必须为 Bearer；只有 `app/auth.py:require_user` 检查 `bearer ` 前缀。两条入口严格程度不同，不能认为平台入口也校验了 scheme。
- token 直接作为 `MOCK_USER` 的键查表，返回的 `identity` 与键不同（例如 `user_1` → `user_id_001`）；沙箱、PVC 名与对象前缀都用 `identity`。
- `get_current_permissions` 优先用请求上下文里的权限（由 `authenticate` 注入），后台任务缺权限时按 identity 重新查询，不依赖客户端单独提交的权限字段。
- `@auth.on.store` 除动作权限外要求 namespace 非空且第一段等于当前 identity；`@auth.on.assistants` 只查动作权限，没有 owner 过滤。
- 通用处理器写入 `metadata.owner` 并返回 `{"owner": identity}` 作为查询过滤条件；只有通用路径带 owner 过滤，不能声称所有资源都做了租户隔离。
- 身份缺失或 token 无效统一 401，无权限返回拒绝，不回退到默认用户或管理员。
- `MOCK_USER` 只有 4 个账号，`get_permissions_by_user_id` 是线性扫描，仅适用于本地演示。

## 修改与验证

- 接入真实用户中心时同步 token 校验、权限查询、后台任务与工具上下文；模拟数据只能用于本地场景。
- 身份变化影响 `utils/shared.get_user_id`、沙箱缓存 key、PVC 名与 Deploy 对象前缀，跨用户访问规则必须一并核对。
- `langgraph.json` 当前 `disable_studio_auth=true`、`enable_custom_route_auth=false`，Studio 与自定义路由都不受这套鉴权保护，Webhook 由 `app` 自行验签。
- 当前没有专门的授权单测，相关覆盖散落在 `test_run_projection.py`（Bearer 与用户过滤）与 `test_security_fixes.py`（Nacos 错误码、删除路径、技能名）。验证需覆盖无 token、无效 token、无权限、自己的资源、他人资源与后台权限查询。
