# 普通任务 Worker 启动说明

## 启动

```powershell
uv run sprout orchestrator task-worker --config D:/deploy/sprout.toml --host 127.0.0.1:17233 --namespace <namespace> --task-queue <queue>
```

示例配置路径和命名空间/队列需替换；17233 是本仓库专用验证服务，不代表生产配置。
四项参数必须显式提供。配置与提交端应使用同一套 metadata、operational 等数据源、
模型/工具配置；host、namespace、queue 必须匹配提交记录，工作区路径须在 Worker
机器可访问。命令不会创建 Temporal 命名空间、拉镜像或自动拉起外部存储服务。

成功进入服务范围时 stdout 输出 task_worker_ready JSON 事件。它表示客户端已连接、
Worker 已构造并开始服务，不是模型连通性、任务验证完成或所有存储健康的证明。

## 生命周期及职责

只注册 SproutExecutionWorkflow 和 run_sprout_node 活动，活动调用真实
Runtime.execute_node。每个进程持有自己的 Runtime，不使用全局活动 Runtime。
不调用 Runtime.start，不自动做孤儿沙盒清理、审批过期处理、outbox 后台派发、
成长自动化或进程内任务队列消费；这些职责须由原有服务另行承担。

Ctrl+C 走 Worker 退出/活动收尾，再关闭存储；不会取消 workflow、删除节点占用、
批准任务或自动重跑未知副作用。活动宽限收尾为 10 秒，不是任意活动必定能在
10 秒内结束的硬保证。生产部署仍需外部进程守护和日志管理。

旧 orchestrator worker 保留原通用服务语义。隔离编码与 Git 提交仍分别使用
evolution coding-worker / commit-worker，不能用本命令替代其专用准入与队列绑定。

## 验收与后续

真实专项使用独立 SQLite、专用 Temporal 新命名空间/队列、离线 echo 和只读任务，
正式 task-worker CLI 接收 SDK 提交后完成 read/agent 两个生产节点；独立客户端
观察、显式同步到 completed，重复调用不增加活动，Worker 最后 SIGINT 正常退出。
信号由测试 stdin harness 在事件循环线程触发，未验证物理终端 Ctrl+C 按键投递。

两种真实 Worker 入口和生命周期/调度回归共 34 passed（13.23s），静态检查通过，
无付费请求。证据 .workbuddy/test-runs/task-worker-cli-final-1 下各自的
gateway-acceptance.json。没有 Docker 编码、Git 应用、生产模型或服务守护验收。

普通任务 CLI 提交/报告/同步已接入，见 Task-Lifecycle-CLI-Guide.md；后续优先
补明确的待调度恢复流程。当前重复提交只读原记录，不代表允许重试。
