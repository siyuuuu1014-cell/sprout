# Gateway 任务查询服务

## 当前接线

CLI / Web / RPC / SDK 的通用执行入口可使用 RuntimeGateway；本轮新增
`RuntimeGateway.queries`（TaskQueries），组合 Runtime 现有读取接口。
SDK 提供 task_report 和 observe_task。
Web 提供 GET /api/project/tasks/{task_id}/report 和 /observe；
RPC 提供 task.report 和 task.observe，使用同一 TaskQueries。
observe 支持 submission_id 与 wait_seconds（0..60 秒）。

服务不接受请求中的数据库路径，不导入 CLI，不启动 Runtime、Worker 或模型，
不提交、同步、审批、取消任务。observe_task 只观察持久化的精确 workflow/run；
历史调度结果和当前持久化任务状态分别返回，不把观察当作同步完成。

task_report 包含任务状态、执行节点、验证报告、调度记录、预算统计及阶段推断；
预算和阶段的纯投影由 task.reporting 提供，CLI coding-report 与 Gateway 共用。
它不是现有 coding-report 的完整替代，尚未合并隔离提交请求。
预算读取通过 Runtime 和存储接口进行；不支持预算读取的存储标记证据不可用，
而不是返回零消耗。历史未知节点显示等待人工核对，不自动重跑。
多个读取不是全局原子快照。

## 身份

SDK 的 principal 在嵌入应用初始化时由可信宿主注入，不来自用户请求正文。
配置后 execute 也使用同一个身份；旧 caller_id 执行调用仍兼容，但仅填写
caller_id 不构成已认证身份，不能使用新查询 API。

新查询要求任务创建者与查询者均已认证，user_id 和身份 source 一致。
不同 transport 可以复用同一个可信身份；不同身份 source 的同名账号不自动关联。
历史未认证任务保留原有入口，不通过新 API 升级权限。
CLI 隔离实验、Git 和成长发布的既有权限检查没有改变。

Web 创建任务现在将中间件 Principal 传入 Runtime，避免创建后缺少可信归属。
HTTP RPC 优先使用已认证 Web 身份；仅独立 RPC token 验证通过时，使用固定
rpc-user/source=rpc 操作者身份。用户参数不提供身份；不同来源仍不自动关联。
新查询在关闭认证时返回拒绝，不改变旧开发模式入口的行为。
HTTP 查询错误为 403/404/400；RPC 权限拒绝为 FORBIDDEN。

## 后续

1. 隔离 Git 提交请求仍走原专用报告，不绕过实验身份及数据库绑定检查。
2. 后续补充真实 Temporal 调度观察联调与客户端交互展示。
3. 再统一提交、显式结果同步和审批应用服务；Worker 继续独立部署。

## 本轮验证

离线专项 9 passed：共享 Gateway/SDK 报告一致、历史结果观察、身份拒绝、
旧 SDK 兼容、任务不存在、歧义与跨任务调度记录拒绝。
未调用付费模型；未验证新 Web/RPC 路由或真实 Temporal 观察。

第二轮补充存储预算一致性和共享阶段边界测试，范围仍为离线查询。

第三轮 Web/RPC、认证、CLI 兼容回归 57 passed（5.09s），其中真实 SQLite
Runtime 完成 Web 创建任务→Web/RPC 报告一致性读取。调度观察使用已持久化
结果替身，不代表真实 Temporal 联调；模型使用离线 echo，无付费请求。
