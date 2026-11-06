# Sprout Temporal 架构与运行指南

适用于基于 A2 `64276f3d3cb482ebb8e93299b4c5fb1dacd9a438` 的 `codex/temporal-t0-t1` 工作树。

## 模块与兼容入口

- `workflows/{execution,task,growth,gateway,commit}.py` 保存确定性 Workflow 定义。注册名、输入输出、超时和重试见下方 T0 合同。
- `activities/{node,growth,commit}.py` 委托既有 Runtime 和 Commit 执行器。节点与 Growth 工厂绑定各 Worker 的 Runtime。隔离 Commit 执行体在调用时复用 Worker 的路由校验，无导入期循环。
- `temporal_workflows.py` 和 `commit_workflows.py` 保留为重导出层。旧调用方和进程测试仍使用旧导入，兼容 setter `set_activity_runtime()` 仍可用。通用 Worker 使用绑定工厂。
- `worker.py` 装配通用与普通 Task Worker。`isolated_worker.py` 保留隔离准入及注册，`isolated_worker_lifecycle.py` 管理已接管的隔离 Runtime。`terminal/temporal.py` 保存提交、路由与结果状态。

## 启动与关闭

| Worker | 入口示例 | 所有权 |
|---|---|---|
| 通用 | `sprout orchestrator worker --host 127.0.0.1:7233 --namespace default --task-queue sprout-tasks` | 自建并启动 Runtime，SDK Worker 退出后停止 Runtime |
| 普通 Task | `sprout orchestrator task-worker --config settings.toml --host 127.0.0.1:7233 --namespace default --task-queue sprout-tasks` | 自建 Runtime 和持久 Storage，退出 SDK 上下文后关闭 Storage |
| 隔离 Coding | `sprout evolution coding-worker TASK_ID --database metadata.db --config settings.toml` | 使用持久提交路由，预装 Runtime 通过绑定检查后转交生命周期管理 |
| 隔离 Commit | `sprout evolution commit-worker TASK_ID REQUEST_ID --database metadata.db --config settings.toml` | 使用独立 Commit 路由和原请求，关闭顺序与隔离 Coding 共用 |

通用入口可读取 `TEMPORAL_HOST`、`TEMPORAL_NAMESPACE`、`TEMPORAL_TASK_QUEUE`。部署时让提交端与 Worker 指向对应命名空间、队列和持久 Storage；隔离队列由既有 Task/plan 或 Commit 请求推导。Ctrl+C 停止 Worker，持久 Workflow 和 Guard 记录仍可供恢复。直接调用隔离 Worker 的 `run()` 时，Runtime 和 Storage 由调用方管理。

## 提交、故障与恢复

提交端先持久化冻结图、稳定 Workflow ID 和路由。Activity 可用 SDK 执行身份与冻结提交匹配，再由 SQLite 事务绑定 Run 并推进 Task。启动 ACK 不确定时，恢复使用原 Workflow ID 与拒绝重复启动策略，或直接取回已确认 Run。ACK-open 在原执行仍开放时恢复，ACK-closed 在原执行已结束后恢复。两种路径均通过原 Run 和节点尝试次数验证。

Worker 硬中断后，NodeExecutionGuard 继续复用已完成结果，并阻止不确定外部效果被盲目重放。隔离 Coding 审批通过 `sprout evolution replay-continue TASK_ID --database metadata.db --config settings.toml --approve APPROVAL_ID` 恢复，沿用原 SQLite Task、Node 和 Approval 关系。Commit 保持 120 秒超时、最多一次尝试。历史 Replay 保留两个现有 patched 标记，版本替换前应复测已保存的历史。

## 本地验证

使用项目虚拟环境与持久 SQLite，启动独立本地开发服务：

```text
temporal server start-dev --ip 127.0.0.1 --port 17233 --headless
python -m pytest -q src/Sprout/tests/test_temporal_baseline_replay.py src/Sprout/tests/test_temporal_sandbox.py src/Sprout/tests/test_temporal_submission.py
```

真实用例需设置 `SPROUT_TEMPORAL_TEST_HOST=127.0.0.1:17233`。Coding 使用经过固定摘要的本地测试镜像和确定性模型夹具。Docker outage 使用仓库隔离 Compose 项目 `sprout-temporal-validation` 和 `SPROUT_TEMPORAL_DOCKER_FAULTS=1`；它会停止该项目的 Temporal 容器，测试后恢复其初始状态。精确命令、统计与证据目录见阶段记录。

## 已知边界

Windows L1 的 26 项失败均在 A2 同根因复现。两个 existing 隔离场景分别受准备 Scope 夹具和独立 R04 Git Review 阻塞。Growth 历史参数问题、R02/R04 和 PR-A3 属独立工作。Ubuntu 和托管 CI 未执行。T4-B 的真实 Docker 与 L1 证据对应 T5 格式调整前的代码。

## 阶段记录与证据

基线 `64276f3d3cb482ebb8e93299b4c5fb1dacd9a438`，与远端 `codex/approval-pr-a2` 一致。开发分支 `codex/temporal-t0-t1`。

## T0 合同

| Workflow 注册名 | 输入 | 调度及返回 | T1 模块 |
|---|---|---|---|
| SproutExecutionWorkflow | graph dict，包含 task_id、nodes 等 codec 字段 | 按图顺序运行 run_sprout_node。返回 status、nodes，等待时包含 node_id，异常时包含 error。预算决定 Activity 超时，缺省 3600 秒 | workflows/execution.py |
| SproutTaskWorkflow | task dict | 返回 task_id、queued | workflows/task.py |
| SproutGrowthWorkflow | workspace_id、trajectory_dir | 保留原 Activity 调用表达式和现有参数传递方式，返回 Activity 结果 | workflows/growth.py |
| SproutGatewayWorkflow | payload dict | 返回 gateway、submitted | workflows/gateway.py |
| SproutExperimentCommitWorkflow | request dict | 调度 run_experiment_commit，120 秒且最多一次尝试。错误返回 applied=false、execution_confirmed=false、commit_result_unconfirmed、automatic_retry=false | workflows/commit.py |

| Activity 注册名 | 输入与输出 | 实际执行 |
|---|---|---|
| run_sprout_node | 节点 dict → Runtime 节点结果 dict | 通用 Worker 读取兼容全局 Runtime。普通 Task Worker 使用自有 Runtime 闭包。隔离 Coding Worker 继续验证 Task/节点和路由，再调用原 Runtime |
| run_growth_automation | workspace_id、trajectory_dir → GrowthAutomation.run_once 结果 | 通用 Worker 的兼容全局 Runtime |
| run_experiment_commit | request_id、request_fingerprint → commit request 执行结果 | 隔离 Worker 检查请求、Workflow ID、指纹、队列、Task 绑定，再委托原执行器 |

## Worker 与生命周期

- 通用 Worker 使用配置队列，默认 `sprout-tasks`，注册 Execution、Task、Growth、Gateway 和两个通用 Activity。装配 storage 并启动 Runtime，退出时停止 Runtime 和清除全局引用。
- 普通 Task Worker 使用显式配置队列，注册 Execution 和节点 Activity。独占持久 Runtime，退出 Worker 后关闭 storage。
- 隔离 Coding Worker 使用 `sprout-growth-<sha256(task_id:plan_fingerprint)>`，注册 Execution、Commit 和对应的绑定 Activity。隔离 Commit Worker 使用独立 `-commit-v1` 队列。旧 commit 请求保留原队列路由。
- `coding_run.py` 继续装配既有 dispatcher、Runtime 和 Temporal 后端。`run_owned_workers` 继续等待 Worker 停止后关闭各自 storage。

## 提交、等待与 Replay

Temporal 后端持久化 Workflow ID、队列和序列化图。Workflow ID 仍由 Task ID 和 dispatch key 哈希组成。审批或资源等待返回结构化结果，恢复沿用 Runtime.resume_task 和既有持久提交路径。已完成节点跳过，历史兼容分支按现有顺序调用 `sprout-resume-skips-v1` 与 `sprout-cancel-stop-v1`。NodeExecutionGuard 继续承担持久所有权、既有结果复用和不确定结果保护。未修改队列、codec、Activity 重试或任何审批合同。

T1 保留 `temporal_workflows.py`、`commit_workflows.py` 的导入路径及 `set_activity_runtime`。普通节点 Activity 和闭包装配移至 `activities/node.py`，Growth 移至 `activities/growth.py`，隔离 commit 的共用执行体移至 `activities/commit.py`。隔离 Worker 的装饰器、Task 绑定和队列装配继续在原处。

## 定向验证

使用现有项目环境和本机开发服务 `127.0.0.1:17233`。

- T0 同组测试 89 passed、6 failed、2 skipped。历史 `7b377ac` Replay 四个场景通过。
- T1 同组加 Worker 生命周期测试 95 passed、6 failed、2 skipped。四个历史 Replay 仍通过。六个失败与 T0 相同，五个结果为 waiting_resource，worker-kill 用例没有到达中断边界。两个 Docker 故障注入用例需要显式 opt-in。
- 补充 SDK 沙箱、注册名、已完成跳过、审批/资源等待、取消和 Activity 异常共 15 passed。该组与前述统计有重叠。
- 全仓 CI 与 T4 全量真实集成 NOT_RUN。

## T2 接续与回滚

T2 可将节点和 Growth Activity 都绑定到 Worker 持有的 Runtime，统一通用 Worker 的注册装配并移除运行时全局读写。保留既有 callable 参数注解和注册名，继续复测两个 Worker 同进程的隔离性、缺失依赖错误、关闭顺序及历史 Replay。隔离 Worker 的生命周期和任务绑定留给后续独立阶段。

回滚时从 A2 基线恢复本次移动的旧模块与 Worker 导入，并移除新包与专项测试增量。无数据库或 Temporal 历史迁移。

## T2 Worker-owned Runtime

通用 Worker 创建并启动自己的 Runtime，再连接 Temporal 并注册 `bind_node_activity(runtime)` 和 `bind_growth_activity(runtime)`。Worker 运行结束或连接失败后调用该 Runtime 的 `stop()`。启动失败时尚未注册 Activity。普通 Task Worker 继续使用原有节点工厂及 storage 关闭顺序。

旧 Activity 和 `set_activity_runtime` 仍由兼容模块导出，供已有调用方和历史测试使用。通用 Worker 从注册到退出均不读写该全局变量。Workflow、队列、持久化合同及隔离 Coding / Commit Worker 沿用 T1。

新增回归先在 T1 复现 Worker A 的节点 Activity 使用 Runtime B，再验证 T2 的两个注册集合交错执行、关闭 A 后 B 继续工作、启动失败不注册及空 Runtime 明确失败。

回滚仅反向应用 T2 增量。T3 可集中整理隔离 Worker 的运行与存储所有权，继续验证停止顺序和恢复行为。Growth Workflow 的历史参数与超时表达式保留，单独验证和处理。

T2 定向命令（项目虚拟环境，`SPROUT_TEMPORAL_TEST_HOST=127.0.0.1:17233`）：

```text
python -m pytest -q -rs --tb=short --basetemp=.pytest_cache/t2 src/Sprout/tests/test_temporal_baseline_replay.py src/Sprout/tests/test_temporal_sandbox.py src/Sprout/tests/test_temporal_codec.py src/Sprout/tests/test_temporal_submission.py src/Sprout/tests/test_temporal_result_wait.py src/Sprout/tests/test_temporal_integration.py src/Sprout/tests/test_isolated_worker.py src/Sprout/tests/test_coding_run.py src/Sprout/tests/test_task_worker_service.py src/Sprout/tests/test_node_execution_guard.py src/Sprout/tests/test_isolated_worker_lifecycle.py
```

结果 109 PASS、6 FAIL、2 SKIPPED。四个历史 Replay 全部通过。六个失败与 T0/T1 同名同症状；两个 Docker 故障注入未启用。全仓 CI 和 T4 验收 NOT_RUN。

## T3 Worker 生命周期与恢复

- 通用 Worker 独占 Runtime 和 Storage。启动失败若发生在 Runtime 进入启动状态之前，由 Worker 关闭 Storage；进入启动状态后交回 Runtime.stop() 完成清理。正常连接、运行、退出仍由原路径管理，SDK Worker 退出后再停止 Runtime。
- 普通 Task Worker 不启动通用 Runtime。Activity 装配纳入 Storage 清理边界，退出 SDK Worker 上下文后关闭 Storage。
- 隔离 Coding 和 Commit Worker 共用原 run_owned_workers。通过绑定校验后才接管预装 Runtime，取消并等待所有 SDK Worker 后关闭各自 Storage。连接由对应 SDK Worker 持有。直接调用 Worker.run 时，Runtime 和 Storage 仍由调用方负责。队列、Task 绑定和重试合同未改。
- 新增测试覆盖启动前和部分启动后的 Storage 清理、Task Worker 绑定失败及 Commit Activity 的 120 秒超时、单次尝试和不确定结果。

本地 Temporal 端点为 127.0.0.1:17233。命令（项目虚拟环境）：

```text
python -m pytest -q -rs --tb=short --basetemp=.pytest_cache/t3 src/Sprout/tests/test_temporal_baseline_replay.py src/Sprout/tests/test_temporal_sandbox.py src/Sprout/tests/test_temporal_codec.py src/Sprout/tests/test_temporal_submission.py src/Sprout/tests/test_temporal_result_wait.py src/Sprout/tests/test_temporal_integration.py src/Sprout/tests/test_isolated_worker.py src/Sprout/tests/test_coding_run.py src/Sprout/tests/test_task_worker_service.py src/Sprout/tests/test_node_execution_guard.py src/Sprout/tests/test_isolated_worker_lifecycle.py src/Sprout/tests/test_detached_coding_cli_integration.py -k "not (test_formal_detached_coding_flow and existing)"
python -m pytest -q --tb=short src/Sprout/tests/test_temporal_sandbox.py src/Sprout/tests/test_task_worker_service.py
```

第一组 111 PASS、6 FAIL、2 SKIPPED、1 DESELECTED。第二组 23 PASS，含第一组之后新增的 3 个用例。去重合计 114 PASS、6 FAIL、2 SKIPPED。四个历史 Replay 全部通过。首次 RED 运行验证了 T2 在两种启动失败下均未关闭 Storage。两个 Docker 故障注入未启用，全仓 CI 与 T4 全量集成 NOT_RUN。

真实隔离恢复用例使用既有确定性模型夹具、本地 Temporal 和固定镜像 `sha256:11880faa03b5dda1f3f54a915ad1b0e3d2a97664799d7a9bfcd3790a9dc7a31d`。Task `6ba34c57-1d93-411a-8492-5b0886e71f1d`、该 Task 的 `:agent` 节点和批准记录 `b833140c-01a5-4f1c-8eec-36b5395e8dc9` 跨 Worker 停止和 Runtime 重建保持一致。原 Agent 容器启动一次、退出 0、stdout 为空，Evaluation 输出含 1 passed。再次续跑未改变执行记录。恢复后的整个图为 waiting_resource，审批恢复验收不代表整个图完成。

| T0/T1/T2 已有失败 | T3 | T2/T3 SQLite 首个 READ 错误 |
|---|---|---|
| replay | 同样 waiting_resource | project_auto_discovery_unavailable |
| restart-terminal | 同样 waiting_resource | project_auto_discovery_unavailable |
| worker-kill | 同样未到达中断点 | project_auto_discovery_unavailable |
| ack-lost-open | 同样 waiting_resource | project_auto_discovery_unavailable |
| ack-lost-closed | 同样 waiting_resource | project_auto_discovery_unavailable |
| test_real_runtime_read_node_on_dedicated_worker | 同样 waiting_resource | preparation_subject_unavailable |

T3 未观察到新增失败。NodeExecutionGuard 的不确定结果禁止自动重试定向测试通过。普通真实硬中断及 ACK 丢失仍缺完整验收，T4 应先恢复这些用例的准备条件。回滚只反向应用 Temporal-T3-increment.patch，保留 T1/T2。

## T4-A

Only test fixtures changed. The ordinary READ scheduling path permits an empty plan, but Runtime._known_read_only_plan requires a finite selection on the Worker. The isolated fixture supplied required_paths without a DelegationScope path, so Runtime._capture_preparation rejected the subject. Fixtures now explicitly authorize app.py with the authenticated local principal.

Real local Temporal and SQLite targeted run: replay, restart-terminal and worker-kill passed. The hard-kill test observed the side-effect marker, killed the child, observed a Temporal activity retry and verified the durable guard prevented a second effect. ACK-open reached completed READ but AGENT waited on node_artifact_provenance_unavailable. ACK-closed stopped at Project analysis task identity unavailable before dropping the acknowledgement. Dedicated READ stopped at analysis_publication_unavailable. T4-A remains incomplete. Docker outage fault injection was not enabled. Roll back only the T4-A increment.

T3 regression rerun with local Temporal and the pinned Coding image: 118 PASS, 2 FAIL, 2 SKIPPED, 1 DESELECTED. Four historical replay cases passed. ACK-open passed in this run but failed in the targeted run, so stability is unverified. ACK-closed and dedicated READ failed both runs. T4-B gate remains open.

## T4-A2 部分验收

本地 Temporal 127.0.0.1:17233。使用项目虚拟环境执行 test_temporal_integration.py 与 test_isolated_worker.py，筛选 ack-lost 或 real_runtime_read_node_on_dedicated_worker。修复前后各五次独立 pytest，日志见 .pytest_cache/t4a2-*.log。

隔离 READ 审计显示 project_analyze 的 tool.invoke 被 Scope 拒绝，子任务未完成，外层发布校验拒绝。仅在测试夹具原 app.py 范围内补充 tool.invoke，修复后五次全部通过。

ACK-open 前后均为四次通过、一次失败。原 T4-A 失败与成功样本均有 READ 来源，失败样本 AGENT 为 uncertain，成功样本 AGENT 完成。尚未确认具体来源校验字段。ACK-closed 两组均未完成注入，夹具在 start_workflow 返回前等待结果，提交端在返回后才推进 RUNNING，而 Worker 要求持久 Task 已处于 RUNNING。生产合同未改。

历史 Replay、SDK 注册、Commit 单次尝试及 Guard 集合共 40 passed。T4-A 六项整组和 T3 完整集本轮未重跑。T4-B 尚未达到启动条件。仅反向应用 Temporal-T4-A2-increment.patch 可撤销本轮。

## T4-A3 ACK 时序专项

真实本地 Temporal 与 SQLite，五次独立运行 test_temporal_integration.py -k ack-lost。ACK-open 1 PASS、4 FAIL，ACK-closed 5 FAIL，每轮 6 DESELECTED。保留 .pytest_cache/t4a3-* 中的失败历史、日志与 SQLite。历史 Replay、SDK 沙箱、Commit 合同与 NodeExecutionGuard 定向共 40 PASS。T4-A 六项整组和 T3 完整集 NOT_RUN。

ACK-open 的测试诊断记录表明失败运行 READ 的内存产物携带 waiting_resource Task 快照，AGENT 的 Task 快照已是 running。Task 字段差异只有 status，导致 _admit_node_products 的完整 Task 比较拒绝，即使 READ 来源记录已经持久化。示例 Task 34d6f268-8e5c-4976-9a6f-c30326ab7c13，日志记录同一 Task 下的 :read 和 :agent。后续应定位 READ 入口取快照与提交端 RUNNING 写入的交错，验证可信快照取得时点，保留身份和来源校验。

ACK-closed 的真实 RPC 已启动 Workflow，但测试在返回句柄前等待完成。生产直到调用返回才保存 accepted 并推进 RUNNING，已开始的 READ 因此拒绝。将丢失点移到 result() 会测试另一类 ACK，强制写 RUNNING 会掩盖该边界。本轮未替换注入语义，未修改生产代码。两个 ACK 均未验收，T4-B 保持待定。反向应用 Temporal-T4-A3-increment.patch 仅撤回诊断与记录。

## T4-A4 ACK 恢复最小修复

ACK-open 的 READ 在恢复提交写入 RUNNING 前取得 WAITING_RESOURCE 快照。后继 AGENT 使用 RUNNING。只在这一方向将同一 Task 的已校验内存来源快照更新为 RUNNING，完整比较其余 Task 字段，并保留 Workspace、owner、输出和资源复验。缓存更新使下游仍可使用完整 Task 比较。确定性测试覆盖正例及 Task ID、Workspace ID、Source、Scope、owner 负例。

真实本地 Temporal 和 SQLite 下，最终版本五轮 ACK-open 均通过，ACK-closed 五轮均在丢弃启动确认前阻塞。每轮六项 DESELECTED。日志、完整历史和 SQLite 位于 .pytest_cache/t4a4-final-*。ACK-open 每轮单一 Workflow 提交、原 Run ID、READ/AGENT 各一次。首轮 Task 594a0170-78bf-44a6-a95b-aec25a5fa420，Run 01a1ac31-85da-7e2b-8a0a-ec6632a50c00。

ACK-closed 仍存在生产竞争窗口。服务端启动后可先派发 Activity，客户端在接收句柄后才记录 accepted 并写 RUNNING。现有夹具通过在启动调用内等待结果放大该窗口。没有安全验证可用的修复，保留启动确认、身份和提交合同。T4-B 未达到启动条件。仅反向应用 T4-A4 增量可回滚。

最终 T3 定向集加现有恢复测试：125 PASS、1 FAIL（ACK-closed）、2 SKIPPED（Docker outage 注入未启用）、1 DESELECTED（existing 隔离图）。历史 Replay 四项、Worker 硬中断、隔离 READ 和隔离 Coding 审批恢复通过。全仓 CI、T4-B NOT_RUN。

## T4-A5 提交协议核对

基线 64276f3d3cb482ebb8e93299b4c5fb1dacd9a438。T4-A4 增量反向检查通过。以下是现有协议，状态并非新设计。

| 情况 | 持久提交和 Task | Workflow 和执行 |
|---|---|---|
| A 正常确认 | pending → accepted → resolved，收到句柄后 Task 才进入 RUNNING | 使用既有 Workflow ID，保存句柄 Run ID。Worker 可能先于 RUNNING 写入进入 READ |
| B 启动未接受 | 连接失败为 pending，启动阶段异常统一为 uncertain。Task 尝试进入 WAITING_RESOURCE | 没有已证实 Run ID，不能凭 pending 或 uncertain 允许执行 |
| C 服务端接受且确认丢失 | 启动异常按 uncertain 保存，Task 尝试进入 WAITING_RESOURCE | 服务端已有原 Workflow 和 Run，客户端尚未保存 Run ID。Activity 可能遇到本地状态门禁 |
| D 进程重建 | 复用 unresolved 记录及其原始路由和 payload | 未确认时以原 Workflow ID 和 REJECT_DUPLICATE 调用启动，重复错误返回原 Run ID，再获取既有句柄。已确认时直接取得句柄。NodeExecutionGuard 继续决定是否可执行 |

ACK-closed 当前夹具在启动调用内等待完整结果，夸大但未创造服务端接受与本地 RUNNING 写入之间的窗口。启动调用内立即抛弃句柄可测试启动 ACK 丢失，但如果直接沿用 ACK-open 的 Worker 启动顺序，只会重复 open 场景，无法证明在客户端恢复前由 Worker 执行并关闭的情况。

本轮未实现 Worker 端可信接纳或测试注入修复。候选方案需从 SDK Activity 上下文验证 Workflow ID、Run ID、队列、节点与持久提交关联，并处理 Task 状态更新与提交端异常回写之间的竞争。仅靠 pending 记录或任意 payload 无法证明服务端接受。生产代码和测试未改，本轮真实 ACK 五轮、定向回归及 Replay 均 NOT_RUN。ACK-closed 继续 BLOCKED，T4-B 未达到启动条件。

## T4-A6 Worker 接纳与 ACK-closed

SDK Activity 上下文提供 workflow_id、workflow_run_id、workflow_type、namespace、task_queue、activity_id、activity_type、attempt 和 is_local。服务地址不在 Activity Info 中。节点 Activity 使用 SDK 身份匹配现有 SQLite 提交的 Workflow、Run、命名空间、队列和冻结节点载荷。未登记提交的旧入口沿用原 Runtime 门禁。已有提交但 Workflow 不匹配则拒绝。现有 Runtime 继续校验 Task、Workspace、Scope 和 Guard。

SQLite 原子批次只在匹配记录和启动窗口时确认 Run 并推进 Task。迟到的启动异常不能撤销已确认记录，也不能将其 Task 写回等待资源。提交标识、队列、Workflow 历史和重试策略均沿用原合同。无 schema 变更。

真实 ACK 注入在启动 RPC 已返回给测试边界时记录 Run ID，并立即向提交端丢弃确认。ACK-closed 随后通过保留的观察句柄等待 Workflow 完成，才停止原 Runtime 并恢复。五轮 ACK-open 和 ACK-closed 均通过。原 Workflow/Run、单条提交及 READ/AGENT 各一次的 SQLite 证据位于 .pytest_cache/t4a6-final-*。首轮 closed Task 0a7da051-f445-4a73-b384-b2bc02c5e887，Run 01a1ac40-c86a-75e8-bfa4-66be23cc8e71。定向负例检查错误 Workflow、Run、命名空间、队列、Activity 类型和节点载荷，以及迟到失败回写。

回滚仅反向应用 Temporal-T4-A6-increment.patch。全仓 CI、T4-B 和 T5 未执行。

最终定向集（test_temporal_*.py、isolated_worker、isolated_worker_lifecycle、coding_run、task_worker_service、node_execution_guard、detached_coding_cli_integration，筛除 existing 图）：133 PASS、0 FAIL、2 SKIPPED、1 DESELECTED。六项真实恢复及四项历史 Replay 通过。两个 Docker outage 注入未启用。

## T4-B 集成验收

Activity 校验发现两个改造回归：缺少 Workflow/Run 身份时绕过已有提交校验，SQLite 条件更新零行时仍进入 Runtime。修复要求匹配提交必须具备 SDK 身份并实际确认成功。SQLite 批处理返回行数，Task 晋升受首条语句成功约束；事务失败回滚。不同 Run 并发只能一个成功，同 Run 重复可成功，resolved 和终态不重新接纳。

固定本地 Docker Coding 镜像和 Temporal 127.0.0.1:17233。集成组 pytest -q --tb=short --basetemp=.pytest_cache/t4b-int，范围 test_temporal_*.py、test_isolated_worker.py、test_isolated_worker_lifecycle.py、test_coding_run.py、test_task_worker_service.py、test_node_execution_guard.py、test_detached_coding_cli_integration.py、test_coding_graph_docker_integration.py。137 PASS、2 FAIL、2 SKIPPED，退出 1。六项真实恢复、两个 ACK、四个历史 Replay、硬中断、隔离 READ、隔离 Coding 审批恢复及 Commit 合同通过。SQLite 与 Activity 专项 test_temporal_submission.py 共 43 PASS，退出 0。

此前跳过的两种 outage 在隔离 Compose 项目 sprout-temporal-validation 上单独启用 SPROUT_TEMPORAL_DOCKER_FAULTS=1，使用 postgres:13 与 temporalio/auto-setup:1.26.2。pytest -q --tb=short --basetemp=.pytest_cache/t4b-outage src/Sprout/tests/test_temporal_integration.py -k outage，2 PASS、6 DESELECTED，退出 0。容器恢复停止状态。

两个 existing 用例在 T4-B 和 A2 64276f3 对照均失败。detached existing 缺准备 Scope，coding-submit 报 preparation_subject_unavailable。existing-graph 的 SQLite Approval 节点均记录 git_diff_unavailable: Task-owned GitBroker review required，归属独立 R04。未修改这两个业务路径。

仓库命令 python scripts/ci_baseline.py --output .pytest_cache/t4b-ci 首次退出 1。脚本的索引清单未包含未跟踪的新包，collect 和 L1 均退出 2。使用隔离临时 Git 索引补足源码清单后运行相同脚本，输出 .pytest_cache/t4b-ci-complete，真实索引保持原样。回滚仅反向应用 Temporal-T4-B-increment.patch，保留 T4-A6。

T4-B SQLite 抽查：ACK-open Task 554950a3-ccc1-4fde-9ef6-cb9a670eb0e8，Run 01a1ae73-c0f0-79f5-8f07-229514800833；ACK-closed Task ddee8ffc-04d0-4af0-b093-d46e35183eef，Run 01a1ae73-c55b-72d8-b207-3f00efc4f2af。各保留一条 Workflow 提交与 READ/AGENT 各一次执行。真实硬中断 Task b8b80964-9782-4e30-b897-8afce3956d4a，原 Run 01a1ae73-7b5a-7133-93f7-54f8f977f181，READ 持久尝试一次，测试副作用日志一行，重试由 Guard 阻止。

最终新增按 SDK Workflow ID 反查提交，阻止错 Task ID 落入无提交兼容分支。最终真实集成同上命令，仅 basetemp 改为 .pytest_cache/t4b-final，139 PASS、2 FAIL（同两个 existing）、2 SKIPPED，退出 1。最终 SQLite/Activity 专项 44 PASS、退出 0；最终 Compose outage 2 PASS、6 DESELECTED、退出 0。四个本轮修改 Python 文件的 Ruff 检查通过。

Windows Python 3.13.9、uv 0.11.25。完整源码快照收集 6576，L1 6373 PASS、26 FAIL、0 ERROR、117 SKIPPED、60 DESELECTED，脚本及 L1 均退出 1。锁定安装、导入、收集、examples、R00 退出 0。脚本 Ruff 虽退出 0 但警告未找到文件，不能视为有效全仓 lint。全仓直接 Ruff 另报告既有及前序问题，本轮改动四个 Python 文件定向 Ruff 通过。

同锁定环境在 A2 逐项复跑 L1 失败，26 个均以相同错误复现：1 项 task_scope 缺六种翻译，23 项 R02 CLI fixture 的 SimpleNamespace 缺 list_workspaces，1 项 R04 source budget 未抛 ValueError，1 项 R04 policy-deny 展示加入原因后与旧精确字符串断言不符。详见 t4b-ci-complete/l1.xml 与 t4b-ci-baseline-failures.xml。

Windows L1 快照早于最终错 Task 反查与回滚负例增量，后者由最终集成和专项覆盖。最终工作树未再做完整 L1，Ubuntu/托管 CI 未运行。T4-B 为 PARTIAL，未见最终定向新增失败，T5 门禁未关闭。

### T4-B 最终冻结验收

Windows Python 3.13.9 与 uv 0.11.25，运行 python scripts/ci_baseline.py --output .pytest_cache/t4b-final-l1。临时 Git 索引保留实际索引原样。23 个新增或修改 Python 文件均进入源码清单，SHA-256 全部与最终工作树一致。6578 项收集，6375 PASS、26 FAIL、0 ERROR、117 SKIPPED、60 DESELECTED。L1 与脚本退出 1。JUnit 逐项对照 A2 复跑，失败身份及首行错误全部一致，新增 Temporal 回归 0。

仓库 Ruff 步骤提示没有 Python 文件。显式扫描全部 23 个累计修改 Python 文件得到 6 项格式问题（4 个 I001、2 个 E501）。T4-B 修改的 4 个 Python 文件显式扫描通过。冻结验收无生产源码变更。

六项真实恢复、四个历史 Replay 和两项 Docker outage 沿用最终 T4-B 集成证据。两个 existing 失败仍属 A2 准备 Scope 和独立 R04 GitBroker。Ubuntu 和托管 CI NOT_RUN。T4-B 在 Temporal 范围内验收通过，保留外部失败和平台覆盖缺口，具备进入 T5 的条件，等待授权。最终报告、JUnit 和日志位于 .pytest_cache/t4b-final-l1。

## T5 最终整理与验收

保留旧导入和 setter，未移除任何入口。调整四处 import 排序和两条长行，未改变 Workflow、Activity、Runtime、Worker 或持久状态行为。Ruff 显式扫描累计 23 个 Python 文件，从 4 I001、2 E501 到 0 问题。单独导入检查确认 9 个旧导出与新定义对象一致。

测试使用本地 Temporal 127.0.0.1:17233 与项目虚拟环境。执行 pytest -q --tb=short --basetemp=.pytest_cache/t5，范围 test_temporal_*.py、test_isolated_worker.py、test_isolated_worker_lifecycle.py、test_coding_run.py、test_task_worker_service.py、test_coding_worker_cli.py、test_commit_worker_cli.py、test_node_execution_guard.py。150 PASS、0 FAIL、2 SKIPPED，退出 0。四个历史 Replay 均通过，两项 Docker outage 未启用。Docker 与 Windows L1 本轮不重跑，沿用 T4-B 证据。Ubuntu 和托管 CI NOT_RUN。

| 阶段 | 阶段验收 |
|---|---|
| T0 | 89 PASS、6 FAIL、2 SKIPPED；历史 Replay 4/4 |
| T1 | 95 PASS、6 FAIL、2 SKIPPED；补充 SDK 15 PASS |
| T2 | 109 PASS、6 FAIL、2 SKIPPED |
| T3 | 114 PASS、6 FAIL、2 SKIPPED（两次执行去重） |
| T4-A 系列 | 最终六项真实恢复通过，A6 两个 ACK 各 5/5 |
| T4-B | 真实定向 139 PASS、2 既有 FAIL、2 SKIPPED；outage 独立 2/2 PASS；Windows L1 6375 PASS、26 既有 FAIL、117 SKIPPED、60 DESELECTED |
| T5 | 150 PASS、2 SKIPPED；历史 Replay 4/4；9 个兼容导出及 23 文件 Ruff 检查通过 |

最终待代码审查。回滚 T5 时反向应用 Temporal-T5-increment.patch；回滚全部 Temporal 改造时反向应用 Temporal-final-from-A2.patch。无数据迁移。两个补丁保存在 .pytest_cache。
