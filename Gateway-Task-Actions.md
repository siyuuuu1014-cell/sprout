# Gateway 任务操作服务

## 已实现：显式结果同步

SDK sync_task、POST /api/project/tasks/{task_id}/sync、RPC task.sync
统一进入 TaskActions.sync。参数只有任务 ID、可选 submission_id 和 wait_seconds。
操作者身份由宿主或认证适配器提供；数据库路径、workflow 路由及结果不由请求指定。

链路：任务归属检查 → 选定已有提交 → 观察原 workflow/run → 明确结果才同步。
CLI coding-sync 保留原隔离实验检查，但观察与写入编排复用 synchronize_observed。

Runtime 将同步交给当前 metadata 存储，SQLite 复用既有原子事务：同一事务更新
task_submissions 和 tasks。可信结果冲突、绑定变化和完成节点证据不足均拒绝；
旧提交不覆盖新任务投影，不降级终态，不新增或修改执行节点。
文件型 SQLite 以外尚未实现原子同步的存储明确拒绝，不退化成两次非原子写入。

运行中、观察超时或不可用返回 synchronized=false，不写数据库。
synchronized=true 表示结果记录已协调，不等于任务状态必然改变；需同时看
task_status_updated 和 task_status。重复操作保持幂等。

该入口不会提交 workflow、启动 Worker、执行 Git、批准变更或发布成长候选。
查询与观察接口仍只读。

## 已实现：普通任务持久化提交入口

SDK submit_persisted_task、POST /api/project/tasks/{task_id}/submit、
RPC task.submit_persisted 经 TaskActions 校验任务归属后调用 Runtime。
只接受没有提交/执行节点的 created/ready 普通任务。已有提交只返回原记录 ID，
不重试、不修改调度路由。隔离 replay 实验的新提交仍要求专用 coding-submit。

Runtime 使用部署显式配置的 Temporal backend 派生单次 submit-only 客户端，
绑定当前 metadata，不改变共享 Runtime 的 backend，也不启动 Worker 或等待结果。
没有显式 Temporal backend 时拒绝，不隐式选择路由。连接失败保留原提交记录，
后续重复调用只观察已有记录，不回退本地执行。new_submission 只表示创建了
持久化提交记录，是否被调度器接受需检查 metrics/submission_state 或查询报告。

51 项离线专项/回归通过（2.22s）。真实 SQLite 与 Runtime 验证持久化、重复调用、
调度不可用不回退、准入拒绝；Temporal 客户端为替身，未验证真实 Worker 执行。

## 仍需完成

Runtime.submit_task 是旧的进程内队列，隔离编码 coding-submit 是 Temporal
持久化提交，不能视为同一个功能。旧 RPC task.submit 语义本轮未改变。
普通任务的新公共服务与隔离实验提交尚未完全合并：隔离配置绑定、候选恢复和
Worker 准入仍属专用服务。尚需真实 Temporal/独立 Worker 联调、通用 CLI
接线以及显式待调度恢复入口；本轮不把重复提交当作重试授权。

## 真实多进程链路验收（2026-10-16）

新增 test_gateway_process_integration 和 gateway_process_fixture，使用专用
127.0.0.1:17233、新命名空间/队列、独立 SQLite 与只读仓库。普通 SDK 提交进程
退出后启动独立 Worker，Worker 注册生产 SproutExecutionWorkflow/run_sprout_node，
用真实 Runtime.execute_node 和离线 echo 完成 read/agent 两个节点。

另起观察、同步、重复同步、重复提交及报告客户端，任务最终 completed；
Worker 不随这些客户端退出，最后显式正常停止。重复调用后 Temporal 历史仍只有
两个活动调度，节点占用均 completed，源文件不变。报告保存不同进程 PID。

真实专项 1 passed（6.18s）；补充历史活动计数后的真实专项与本地相关回归
23 passed（6.75s），静态检查通过，无付费 API 调用。证据位于
.workbuddy/test-runs/gateway-process-real-2 下 gateway-acceptance.json。
本轮没有使用 Docker 编码沙盒或 Git 应用；Docker 仅承载既有 Temporal 服务。
Web/RPC 复用服务的路由测试已覆盖，但本轮真实调度使用 SDK，不冒称所有
HTTP 客户端都已完成真实多进程验收。Worker 生命周期使用测试宿主管理，不代表
正式 worker CLI 部署、配置重载及服务守护已验收。

## 正式 Worker 命令补验

新增 orchestrator task-worker，显式 --config/--host/--namespace/--task-queue，
仅服务普通任务，不启动完整 Runtime。正式命令已替换测试宿主完成同样的真实
多进程只读任务链路，并通过 SIGINT 测试正常退出。两种入口及生命周期/调度
回归共 34 passed（13.23s）。部署约束及未验证范围见 Task-Worker-Operations.md。

## 显式连接失败恢复

新增 TaskActions.retry_submission 及 SDK/CLI/Web/RPC 入口。只恢复唯一原记录中
明确 pending/connect、尚未 accepted、无 run_id 且无执行节点的 waiting_resource
普通任务，沿用原冻结图/路由/workflow ID，不重新编译或更换配置。
未知 start 结果、历史不明 phase、已有执行记录均拒绝，不自动或强制重跑。
真实恢复专项 1 passed（8.67s），相关回归 49 passed（1.62s）；范围与使用方法
见 Task-Lifecycle-CLI-Guide.md。待调度连接故障的推进入口已具备，未知结果核对
仍是后续工作，不与执行节点副作用恢复混为一谈。

## 验证

48 项离线回归通过（2.22s），包括真实 SQLite Runtime 的 Web 创建、报告查询、
显式同步、RPC 重复同步，以及 CLI 原子同步和 SDK 非终态不写入测试。
原 workflow 完成结果使用已有持久化记录；未进行本轮真实 Temporal/Docker
联调，未调用付费模型。保留一个 Starlette/AnyIO 弃用警告。
