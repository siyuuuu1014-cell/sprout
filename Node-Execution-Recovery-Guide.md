# 活动节点执行占用与未知结果处理

## 当前实现范围

本轮保护 Temporal worker 调用的公开 Runtime.execute_node 活动入口。采用用户确认的保守规则：占用持久化、不因时间流逝抢占、未知副作用不自动重跑、不提供强制重跑入口。

核心逻辑位于 orchestration/execution_guard.py；Runtime 负责权限和工作区准备；MetadataStore 负责原子占用与结果提交；Temporal workflow 负责区分资源等待和审批等待。CLI/Gateway 只调用公开查询 API。

没有增加数据库、独立任务权威或运行时依赖。占用写在 sprout_core.db 的既有 execution_nodes.metadata_json.execution_guard 中，和节点状态、输出一起维护。现有 execution_node 存储拓扑及生命周期继续生效。

## 执行链路

`活动请求 → 校验任务类型/节点身份 → 查询可信完成记录 → SQLite 原子占用 → 执行节点 → 同一所有者原子提交状态与输出 → 返回 workflow`

初次占用与重试争抢使用同一条 SQLite 条件写入。两个独立进程争抢同一节点时只能一个成功。每次占用带独立 owner、审批续跑键和开始时间，只有仍持有该 owner 的执行者可以提交结果。

普通 save_execution_node 不允许覆盖已受保护的节点；本地 SequentialOrchestrator 也会检查任务下的保护记录，不能通过改配置、遗漏节点或本地重试绕过外部活动占用。

| 执行记录 | 重复请求的行为 |
| --- | --- |
| 未占用的 pending/ready 节点 | 原子争抢，只有一个执行者进入 |
| active / 历史 running | 不抢占、不再执行，返回资源等待 |
| uncertain / 历史 failed | 不再执行，等待核对副作用 |
| waiting（已确认的审批等待） | 同一决策返回原等待结果；新的持久化决策允许正常续跑 |
| completed/skipped | 返回已有结果，不重复执行 |
| 已取消/回滚的任务 | 不启动新的活动 |

未知结果不会因为新增审批决策而释放占用。普通审批以及强制应用提案也不是强制重跑节点的入口。

## 故障处理

- 工具可能已产生副作用，但执行者异常退出：节点记录 uncertain，任务等待资源/人工核对，不标成审批等待或验证完成。
- 执行被取消：传播取消信号，并尽力保存未知结果。即使再次取消、进程被强杀或保存失败，原 active 占用仍然阻止重跑。
- 工具返回成功，但完成结果未能落库：保持原占用，后续活动重试不会再次调用执行器。
- 完成状态和输出已经落库，但活动确认丢失：重试返回原结果。
- 重复活动请求先返回等待、原执行者随后补齐可信完成记录：原任务可通过 task-run 继续。恢复轮次由持久化完成证据生成，不依赖随机新 ID，不重放已完成节点。
- 任务取消不代表已经发生的文件写入、外部请求或运行中的进程副作用被撤销。

## 查询与当前操作边界

```powershell
uv run sprout project task-executions <task-id>
uv run sprout project task-submissions <task-id>
```

查询不会执行工具、访问 Temporal 或清除占用。active 对外显示为 occupied_or_unconfirmed：仅凭本地记录不能断言原执行者已死亡。uncertain 和 legacy_unconfirmed 都需要核查原进程、文件、Git revision、外部系统响应及轨迹。

未知结果没有强制重跑、清除占用或“超时即重置”API。当前尚未提供人工证据补录与结果核销入口；没有可信完成记录时，原任务继续等待，或由用户在保留证据后明确取消。不要编辑数据库来绕过保护。

## 仍然存在的边界

### 独立环境复验

使用专用 Compose 项目启动 `src/Sprout/config/docker_templates/temporal-validation/docker-compose.yml`，不要对原项目服务做故障操作。以下测试会创建独立命名空间与临时数据库，终止它自己启动的测试 Worker；启用 Docker 故障标记后还会短暂停止并恢复专用测试容器。

```powershell
$env:SPROUT_TEMPORAL_TEST_HOST = '127.0.0.1:17233'
$env:SPROUT_TEMPORAL_DOCKER_FAULTS = '1'
uv run pytest -q -p no:cacheprovider --basetemp=.workbuddy/test-runs/temporal-validation-new src/Sprout/tests/test_temporal_integration.py
```

默认未设置测试地址时全部跳过；测试拒绝连接其他地址。故障活动中的追加文件是测试探针，不是生产工具事务日志或操作系统隔离证明。

- 这是活动节点粒度的防重放，不是每次工具调用的事务日志，也不是全系统恰好一次承诺。纯本地、未经过该活动入口的既有执行/重试流程没有被替换为这套保护。
- 同一节点内部的工具调用顺序、工具自身的重试、已经执行的子进程和第三方系统需要独立幂等键、对账或事务设计；本轮不将它们一概声明安全。
- 不会自动判断“异常发生在副作用之前”，因此可能保守地阻塞实际上尚未产生副作用的节点。这是安全优先的可用性代价。
- 已在独立 Temporal 服务验证提交前服务中断恢复，以及 Worker 子进程在执行后、完成结果落库前被强杀：服务端活动重试被 active 占用拦截，节点执行次数保持一次，任务等待资源而非完成。故障仅注入测试目录，活动超时在测试中缩短到 5 秒。
- 已验证当前版本真实历史回放和不兼容节点的反向回放检查；本次又补验真实提交后客户端确认丢失、执行期间服务中断和仓库 `7b377ac` 基准历史。旧生产部署历史、未结束工作流滚动升级与生产部署仍未实测，工作流逻辑更新前仍需版本兼容与回放验收。
- 原始运行数据库损坏、外部直接篡改数据库、恶意执行器伪造结果不属于本轮可信存储和执行器假设覆盖范围。
