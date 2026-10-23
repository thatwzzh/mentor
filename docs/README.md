# Mentor 文档索引

> 先看这里，再按任务进入子目录。  
> 文档按「入口 / 启动上线 / 前端接入 / 历史状态 / 资产」分层，避免同一主题散落在根目录。

## 1. 先读什么

| 你是 | 先读 |
|---|---|
| 第一次跑项目 | [project-guide.md](project-guide.md) |
| 看基础设施/端口/调用链 | [architecture.md](architecture.md) |
| 改模型/路由/预算配置 | [config-split.md](config-split.md) |
| 准备上线 | [launch/prelaunch-backlog.md](launch/prelaunch-backlog.md) |
| 做多账号/多租户 | [launch/multi-account-hardening.md](launch/multi-account-hardening.md) |
| 接 Run Events 前端 | [frontend/run-events-frontend-todo.md](frontend/run-events-frontend-todo.md) |
| 接异步子代理前端 | [frontend/async-subagent-frontend.md](frontend/async-subagent-frontend.md) |
| 查 2026-10-22 的 §4 复核快照 | [status/section4-audit-2026-10-22.md](status/section4-audit-2026-10-22.md) |
| 做 golden / 回归评测 | [golden-sessions.md](golden-sessions.md) |
| 调沙箱资源/超时/步数 | [sandbox-profiles.md](sandbox-profiles.md) |

## 2. 目录结构

```text
docs/
├─ README.md                      # 本索引
├─ project-guide.md               # 开发指南：启动、结构、链路、测试
├─ architecture.md                # 基础设施拓扑与请求链路
├─ config-split.md                # Agent 代码 vs LiteLLM 配置分工
├─ golden-sessions.md             # 可重放 golden / 真实模型录制
├─ sandbox-profiles.md            # 沙箱能力档位与护栏
├─ launch/
│  ├─ prelaunch-backlog.md        # 上线前待办
│  └─ multi-account-hardening.md  # 多账号/多租户改造清单
├─ frontend/
│  ├─ run-events-frontend-todo.md # 5.2 事件/SSE 前端待办
│  └─ async-subagent-frontend.md  # 5.7 子代理状态/结果反显
├─ status/
│  └─ section4-audit-2026-10-22.md # 历史复核快照，不当作当前事实源
└─ assets/
   └─ infrastructure-overview.jpg
```

## 3. 按任务找文档

| 任务 | 文档 |
|---|---|
| 配置 Nacos / AgentSpec / 沙箱 | [project-guide.md](project-guide.md) |
| 看 LiteLLM/模型/预算超时 | [config-split.md](config-split.md) |
| 看工具策略/审批、事件、压缩、子代理 | [project-guide.md](project-guide.md) 的 §4 |
| 跑/录 golden session | [golden-sessions.md](golden-sessions.md) |
| 接 SSE/工具卡/审批卡/成本卡 | [frontend/run-events-frontend-todo.md](frontend/run-events-frontend-todo.md) |
| 接子代理卡片/唤醒 | [frontend/async-subagent-frontend.md](frontend/async-subagent-frontend.md) |
| 查历史缺口和当时判断 | [status/section4-audit-2026-10-22.md](status/section4-audit-2026-10-22.md) |

## 4. 文档状态约定

新增或修改文档时，开头写清状态：

```text
状态：已实现 / 部分实现 / 未实现 / 历史快照
范围：...
事实源：代码路径或接口
```

- **project-guide**：面向开发者的当前实现说明。
- **architecture**：拓扑和链路，不堆功能状态。
- **config-split**：配置唯一口径，避免和 LiteLLM UI 重复。
- **status/**：历史快照，保留当时结论；当前行为以代码和 project-guide 为准。
- **frontend/**：前端接入契约与待办，不写后端实现细节。

## 5. 新增文档规则

1. 先判断属于 `launch/`、`frontend/`、`status/` 还是根目录。
2. 不要新建“XX-最终版/新版”；直接更新原文档。
3. 跨文档只保留一处详细说明，其他位置用链接。
4. 改了接口/配置/状态，至少同步：
   - 对应专题文档
   - 本索引（如果新增文件）
   - `project-guide.md` 的相关章节
5. 历史审计不追改，新增“当前状态”段落或新文档。
