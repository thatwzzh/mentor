# MinIO 与 Deploy 集成测试

按 `src/mentor/utils/minio.py` 的公开契约验收，不依赖实现细节，不使用 Mock。
Deploy 的远程用例需要同时准备真实 MinIO 和具有上传依赖的 OpenSandbox 镜像后验收。
目录按要求保留拼写 `tests/intergrations`，Makefile 命令为 `test-integrations`。

## 复现

1. 安装项目依赖：`uv sync`。
2. 准备真实 MinIO 服务。使用项目 Compose 时，可手动执行：

   ```sh
   docker compose up -d shared-minio
   ```

   该命令会创建或启动 `shared-minio`，占用主机 9002/9003 端口，数据持久化到
   Compose 管理的 `shared_minio_data` 卷。已有卷中的数据保留。
   停止服务用 `docker compose stop shared-minio`；不需要删除卷或运行 `down -v`。
   测试不会自动执行这些命令，也不会重启、重建容器。

3. 配置 `.env` 中的 `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、
   `MINIO_SECURE`，或使用同名环境变量。地址不带协议，端口使用 API 端口。
   默认项目 Compose 的独立共享 MinIO 可这样运行：

   ```sh
   MINIO_ENDPOINT=localhost:9002 MINIO_ACCESS_KEY=root MINIO_SECRET_KEY=12345678 MINIO_SECURE=false make test-integrations
   ```

   账号需要对测试桶有建桶、读写删除对象、读写桶策略、设置版本控制、列举及删除版本、
   删桶权限。无需创建账号或修改现有账号权限；请使用已有的合适测试凭据。

4. 只运行部分场景：

   ```sh
   MINIO_INTEGRATION=1 uv run pytest -q tests/intergrations -k presigned
   ```

未设置 `MINIO_INTEGRATION=1` 时跳过，避免普通 `pytest` 意外写入远程服务。
显式启用后，缺少凭据、服务不可达或权限不足会使测试失败，不会被当作通过。

## 数据影响与清理

每个场景只操作本次随机生成的 `minio-it-<UUID>` 桶。
测试会上传临时对象、设置这些桶的公共读策略或版本控制，并验证删除行为。
数据落在所配置的 MinIO 服务存储中；本地文件均在 pytest 的临时目录中。

结束时使用独立的 aiobotocore 客户端删除登记的测试桶、对象、历史版本及删除标记，
不依赖被测模块的清理功能。失败的用例也执行清理；清理失败会报错。
测试不会列举并删除其他业务桶、清空 Docker 卷或修改服务账号。
进程被强制终止时可能留下测试桶，请根据本次日志及控制台确认桶名后手动清理，
不要按通配前缀删除其他测试进程仍在使用的桶。

## 覆盖范围

- 桶存在性、重复创建、默认公共读、私有桶、不撤销已有策略、保留无关策略，
  匿名读取及匿名写入/删除/列举拒绝。
- 流当前位置、流不关闭、空数据、二进制与中文、覆盖、文件存在性、重复删除。
- 大于 5 MiB 文件上传下载、创建父目录、覆盖控制、下载失败保留目标及临时文件清理。
- 对象键前导斜线、特殊字符 URL 编码、保留 `..` 和中间斜线。
- 真实缺桶、错误凭据错误传播，禁止隐式建桶；不测试连接不上服务的场景。
- 递归上传、前缀归一化、隐藏文件、空文件、空目录、符号链接（含环）、FIFO 跳过，
  覆盖但保留额外远程对象、本地内容不变、结果计数、逐文件错误及相对路径。
- 目录扫描失败前不上传、真实单文件读取失败后继续其他文件、缺桶时汇总所有错误。
- 并发参数拒绝非法值；目录场景使用真实并发上传。
- 三种上传入口的 Cache-Control / Content-Type、大小写匹配、后策略覆盖前策略、
  90 天默认值、no-store、默认 MIME、最后一个后缀、使用目标键、不追溯修改旧对象。
- 超过 1000 条的列举及强制删除分页、嵌套及字符串前缀、排序、无匹配结果。
- 版本覆盖、删除标记、仅列举可见对象、强制删除全部版本和删除标记、暂停版本控制。
- 私有桶真实 HTTP 预签名 GET/PUT、重复 PUT、覆盖、调用方提供元数据、
  不自动应用上传策略、不修改权限、不预检查桶/对象、有效期边界与非法有效期、签名篡改。

## 验证边界

文件权限场景在能绕过 POSIX 权限的账号（如 root）下会明确跳过，推荐用普通用户运行。
超过 1000 条的场景会产生较多真实请求。

本套测试不声称验证内部并发数量的精确上限、任务取消后的连接回收、
下载中途断流、对象保留规则、真实证书下的 HTTPS 或预签名 URL 到期后的拒绝。
这些场景需要额外的可控网络/服务条件；此处不通过 Mock 或计时竞态伪造覆盖。

## Deploy 工具验收

`test_deploy.py` 经真实 `StateGraph -> ToolNode -> deploy` 调用，不需要 LLM、API Key
或 Agent Server。认证用户通过项目现有 `configurable.langgraph_auth_user_id` 传入，
用户 ID 不属于模型可填写的工具参数。源文件只在真实 OpenSandbox 中准备，
使用独立 S3 客户端核对最终对象，用真实 HTTP GET 核对交付 URL 和内容。

### 验收契约

- 使用共享固定桶：`<bucket>/<用户ID>/<target_dirname>/<相对文件路径>`。
  部署桶名固定在部署模块内部，不通过环境变量配置。
  测试仅在进程内将模块常量 `_BUCKET_NAME` 替换为随机测试桶，
  不修改生产工具、配置文件或业务桶。
- 工具使用当前认证用户在 graph store 中登记的已有沙箱。
  保留目录相对路径；单文件以 basename 上传，覆盖同名对象并保留其他对象；
  目录部署先删除目标前缀下的全部旧对象，再上传新目录。
- `entry_files` 保持给定顺序，必须实际存在且为待上传文件。
  非法参数、路径越界、缺失源或入口、空目录在上传前报可识别的工具错误，
  不得写入任何对象。目录扫描跳过符号链接（包括环和外部链接）和 FIFO。
- 任一文件上传失败时返回准确的计数和以沙箱绝对路径为键的 `failures`，
  继续处理其他文件；只返回本次上传成功的入口，不得返回失败文件的 URL。
  参数和产物路径错误返回模型可修复的工具错误。
  认证用户缺失、MinIO 配置、服务连接、认证或旧文件清理错误抛普通异常终止图运行。
- 沙箱使用 `MINIO_ENDPOINT_IN_SANDBOX` 直连 MinIO，宿主地址只用于验证和交付 URL。
  允许公共读 URL 或可实际读取的预签名 GET URL，工具返回内容不得泄露 MinIO 凭据。

如果最终桶配置或错误语义采用其他约定，应先同步这些验收契约，避免测试和实现各自猜测。

### 启用和复现

准备已经运行的 MinIO 与 OpenSandbox Server；测试不会启动、重启或重建这两个服务。
项目 Compose 的服务启动、端口和持久化说明见项目 README。
运行前配置：

- `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_SECURE`。
- `MINIO_ENDPOINT_IN_SANDBOX`，Docker Desktop 通常为 `host.docker.internal:9002`，
  地址不含协议；它必须能从沙箱访问 MinIO API。
- `OPEN_SANDBOX_DOMAIN`、`OPEN_SANDBOX_API_KEY`。
- `DEPLOY_SANDBOX_IMAGE`，须使用项目 `docker/sandbox/Dockerfile` 构建的镜像。
  当前工具使用镜像内 `/sandbox_utils` 的 uv、aiobotocore 和 MinIO 客户端。
  可手动构建：`docker build -t deal-pilot-sandbox:deploy-it -f docker/sandbox/Dockerfile .`。
  构建会下载基础镜像和依赖，并在本机保存新镜像，不启动服务或挂载业务数据卷；
  学生执行同一命令即可准备。测试不会自动构建镜像。

```sh
MINIO_INTEGRATION=1 DEPLOY_INTEGRATION=1 DEPLOY_SANDBOX_IMAGE=deal-pilot-sandbox:deploy-it uv run pytest -q tests/intergrations/test_deploy.py
```

只设置 `MINIO_INTEGRATION=1`（包括现有 `make test-integrations`）不会创建沙箱。
要通过 Makefile 同时验收两者，使用
`DEPLOY_INTEGRATION=1 DEPLOY_SANDBOX_IMAGE=deal-pilot-sandbox:deploy-it make test-integrations`。
缺少必要配置会失败；未启用时跳过远程场景，工具参数 schema 检查仍可本地运行。

### 数据影响、回滚与覆盖边界

每个远程用例创建随机私有测试桶及一个或两个独立沙箱，源文件写入沙箱的
`/tmp/deploy-integration/workspace`。沙箱 **不挂载持久卷**，不会使用已有用户沙箱。
测试把这些沙箱 ID 写入用例专属的 `InMemoryStore`；配置读取在测试进程内替换，
不依赖或修改 Nacos。若工具误走创建持久卷的路径，测试直接失败。
测试禁止常见的宿主 SDK 下载、MinIO 上传路径；这些守卫只做断言，
不伪造上传成功或 MinIO 响应。并发调用通过 ContextVar 分别标记，避免互相替换守卫。

用例结束会终止沙箱、关闭连接，再删除测试桶、对象及历史版本；异常同样执行清理。
回滚即上述清理，不改服务账号、业务桶、数据库、容器配置或已有数据卷。
强制结束进程可能留下测试桶或临时沙箱，请按本次资源 ID 确认后清理；
沙箱同时设有 10 分钟生命周期，正常退出执行显式终止。

覆盖单文件、空文件、UTF-8、二进制、6 MiB、递归目录、同名嵌套文件、隐藏文件、
中文和 URL 特殊字符、shell 元字符、空目录、链接环、FIFO、源文件不变、重复覆盖、
项目隔离、并发用户隔离、认证缺失、非法目标及入口、错误 MinIO 凭据和沙箱不可达地址。

尚不覆盖人为设置权限拒绝造成的部分或全部上传失败、上传中途断流、沙箱突然退出、
预签名 URL 真实到期、POSIX 无读权限文件、
生产 LLM 选择工具过程、完整 Agent Server 鉴权或 Nacos 配置服务。
守卫覆盖项目现有 SDK 入口，并不声称证明任意自定义 HTTP 客户端都不会中转。
工具尚未实现时，本套远程测试不使用 xfail 或自动跳过来伪装通过。
