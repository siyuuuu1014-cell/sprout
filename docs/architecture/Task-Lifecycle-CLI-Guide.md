# 普通任务 CLI 使用说明

新命令组为 `sprout project task`，每个命令输出 JSON，必须明确提供 --config。
复用 Gateway 的 TaskQueries / TaskActions，不启动完整 Runtime 或 Worker。

## 使用流程

以下变量请替换为实际配置、已登记 workspace 和任务 ID。Temporal 服务和命名空间
需事先存在；17233 只是本项目专用验证端口。

```powershell
uv run sprout project workspace D:/path/to/repo --config D:/deploy/sprout.toml
uv run sprout project task create <workspace-id> "分析项目" --mode read_only --config D:/deploy/sprout.toml
uv run sprout project task submit <task-id> --config D:/deploy/sprout.toml --host 127.0.0.1:17233 --namespace <namespace> --task-queue <queue>
uv run sprout orchestrator task-worker --config D:/deploy/sprout.toml --host 127.0.0.1:17233 --namespace <namespace> --task-queue <queue>
```

Worker 在单独终端持续运行，客户端在另一个终端操作：

```powershell
uv run sprout project task report <task-id> --config D:/deploy/sprout.toml
uv run sprout project task observe <task-id> --config D:/deploy/sprout.toml --wait-seconds 30
uv run sprout project task sync <task-id> --config D:/deploy/sprout.toml
```

create 默认 mode=code；示例显式 read_only。创建使用现有策略规划服务，可能调用
所配置模型，但不执行任务图；提交不会在客户端执行模型/工具节点。上述真实验收
全部使用离线 echo，不产生付费请求。编码任务的审批与验证仍为独立步骤。

## 各命令的边界

- create：按已验证操作系统身份创建，不接受自报用户名；输出 task_id。
- submit：显式路由、持久化提交、不等待结果；已存在提交只返回原记录，不重试。
- report：读取任务、节点、预算、验证及调度证据，不连接 Temporal 或重跑验证。
- observe：观察原 workflow/run，不更新任务状态。wait_seconds 范围 0..60。
- sync：明确同步原调度结果，幂等、无自动应用或发布；结果不明不写库。

observe/sync 可用 --submission-id 指定已有提交；有多条歧义历史时必须明确选择。
任务 ID 使用完整值。客户端退出不停止 Worker，Ctrl+C 只停止该 Worker。
读取命令依旧通过常规 Runtime 工厂打开存储，可能进行常规 schema 初始化，
“只读”指不推进任务/审批/调度状态，并非承诺所有底层文件字节完全不变。

旧 project task-create/task-run 及其他命令仍兼容；历史未认证任务不能通过新入口
自动获得归属。不同身份 source 不自动关联，不能把同名 Web/CLI 用户视为同一人。
隔离成长实验仍用 evolution coding-submit/coding-worker 等专用命令。

## 验收

正式 CLI 创建、提交、观察、同步、报告和正式 task-worker 在独立进程中完成
真实 Temporal/SQLite 只读任务闭环，1 passed（7.81s）；重复操作后的历史活动
仍为两个，节点完成、源文件不变、Worker 正常 SIGINT 退出。
证据 .workbuddy/test-runs/task-cli-process-real-1 下 gateway-acceptance.json。
信号通过测试 harness 投递，不是物理终端按键验收。

另有 CLI/旧项目命令/Gateway 回归 45 passed（3.73s），静态检查通过。
尚未验收生产模型、Docker 编码、Git 应用及跨机器部署；显式待调度恢复仍待实现。

## 连接故障后的显式恢复

```powershell
uv run sprout project task retry-submission <task-id> --submission-id <original-submission-id> --config D:/deploy/sprout.toml
```

先用 report 取得原提交 ID。该命令只接受唯一的原记录、task=waiting_resource、
submission=pending/phase=connect，且无 accepted/run_id/执行节点的普通任务。
沿用原 workflow ID、原冻结 payload、原 host/namespace/queue，不重新编译，
不接受新路由、不启动 Worker、不等待结果。仍连接失败则继续等待，可由用户
再次显式调用。submit 本身仍不会自动重试。

state=uncertain、提交确认丢失、已有执行占用、缺少 phase 的历史 pending、
已接受或已完成记录，均不允许通过这个入口重试。已接受且有 run_id 时用
observe/sync；其余未知结果继续人工核对。隔离成长实验仍使用专用流程。

SDK retry_submission、RPC task.retry_submission 和 Web POST
/api/project/tasks/{task_id}/retry-submission 复用相同服务；参数 submission_id，
身份来自可信适配器，Web/RPC 不接受客户端结果或路由作为恢复依据。

真实恢复专项 1 passed（8.67s）：首次连接失败由单独测试进程注入，之后正式
CLI 连接真实专用 Temporal、独立正式 Worker 执行、观察并同步完成，原记录唯一、
任务图不变、最终只有两个活动。证据 .workbuddy/test-runs/pending-retry-real-1
下 gateway-acceptance.json。相关离线回归 49 passed（1.62s），静态检查通过，
无付费请求；未声称真实网络断网/服务器重启或未知 start 结果核对已验收。
