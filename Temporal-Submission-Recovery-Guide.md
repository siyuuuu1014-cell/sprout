# Temporal 提交与恢复

本轮实现客户端提交的持久化和安全重试，不自动切回本地执行。真实 Temporal 服务联调、任意工具副作用的恰好一次执行、跨版本工作流回放仍需后续验收。

## 单一执行链路

`Runtime.submit_task → TemporalTaskQueue → Runtime.execute → 编译/恢复执行图 → TemporalOrchestratorBackend → SproutExecutionWorkflow → run_sprout_node → Runtime.execute_node`

Temporal 队列不再同时向本地 FIFO 放任务，也不再启动仅返回 queued 的 SproutTaskWorkflow。旧工作流定义保留兼容，但不用于新提交。真正执行图只由 Temporal 调度；客户端 handler 不会作为降级执行器调用。

## 状态与持久化

提交记录由 MetadataStore 管理，位于既有 sprout_core.db 的 task_submissions 表，不引入新的数据库或任务权威。首次网络调用前冻结执行图、工作流 ID、服务地址、namespace 和 task queue。

| 提交状态 | 含义 | 任务表现 |
| --- | --- | --- |
| pending | 尚未确认提交；连接失败仍可重试 | waiting_resource，等待调度 |
| accepted | 已获得工作流句柄，正在等待结果 | running |
| uncertain | 启动或结果确认失败，不能断言执行失败 | waiting_resource，结果待确认 |
| resolved | 获得有效工作流结果 | 使用结果中的任务状态，包括 waiting_approval |

连接和启动各等待最多 10 秒，客户端一次结果等待最多 30 秒。客户端停止等待不代表取消远端工作流。已确认的工作流失败才记 failed；异常格式的响应保留待确认状态。

重试优先恢复未确认记录，不能因配置或重新编译的执行图发生变化而提交另一份任务。工作流使用 REJECT_DUPLICATE；遇到 AlreadyStarted 时接回已有 run。已确认结果持久缓存，晚到的超时不能覆盖它。

已确认启动的记录在重试时只接回已有工作流，不再次调用启动接口。独立构造且未配置 MetadataStore 的后端仅提供进程内缓存（用于隔离测试）；产品 Runtime 的 Temporal 队列要求持久化 MetadataStore，不能将该测试模式作为生产持久化方案。

## 查询与重试

```powershell
uv run sprout project task-submissions <task-id>
uv run sprout project task-run <task-id>
```

查询仅读取本地记录，不连接 Temporal。恢复服务后，可通过原任务的 task-run 重试；本轮不增加自动轮询守护程序。completed、cancelled、rolled_back 的权威任务不会重新提交。

审批等待沿用既有“工作流返回等待状态”模式。续跑身份由任务的持久化人工决策生成；同一决策重复调用不重启，新决策开启新的工作流轮次。未确认提交始终先确认旧轮次。已完成/跳过节点不会重放；已通过验收的后续编辑测试轮次也会跳过。审批拒绝仍保留等待，不自动取消。

## 保证边界

- 提交幂等不等于任意工具副作用恰好一次。活动入口现已增加不自动过期的持久化占用；工具已执行、节点完成尚未落库时保持待核对，不重跑。完成结果已落库时返回原输出。范围与剩余工具级边界见 [Node-Execution-Recovery-Guide.md](Node-Execution-Recovery-Guide.md)。
- 启动确认丢失后的安全重试依赖 Temporal 保留相应工作流历史。超过服务 retention、历史被管理员删除或远端数据库丢失时，REJECT_DUPLICATE 无法证明历史不存在副作用；此类情况应人工核查，不应宣称自动恢复具备无限期恰好一次保证。
- 已在独立服务验证提交前和执行期间服务停止恢复、Worker 子进程强杀，以及真实提交成功后丢弃客户端确认的恢复（原工作流未执行/已完成两种情况）。确认丢失由客户端边界故障注入实现，不是物理网络丢包；重建 Runtime 后仍恢复原 Workflow ID/Run ID，各节点不重复执行。
- 仓库提交 `7b377ac` 的普通、已完成评估、skipped 节点、取消结果四类真实测试历史已通过当前版本回放；固定活动输出用于隔离调度兼容性，不是旧部署业务工具验收。生产部署历史仍需另行验证。
- 提交记录是状态快照，不是完整审计事件流；没有扩大权限、审批豁免或 OS 隔离能力。

## 工作流版本标记

`sprout-resume-skips-v1` 控制 skipped 节点和已通过评估的续跑跳过规则；`sprout-cancel-stop-v1` 控制收到取消结果后停止调度。旧基准历史回放保留原命令顺序，新工作流使用修复后的规则；Runtime 的任务权限、取消状态与持久化节点占用门禁不因回放兼容分支而被关闭。

不要直接删除这些标记或回放兼容分支。基准测试文件 `temporal_baseline_7b377ac.py` 是冻结测试快照，不应注册到产品 Worker，也不应跟随产品修复修改。仍需补验生产历史和未结束工作流的滚动升级；本轮没有证明修复过程中所有未带标记的中间版本历史都兼容。

独立复验（仅专用 `17233` 测试服务，故障标记允许停止恢复测试容器）：

```powershell
$env:SPROUT_TEMPORAL_TEST_HOST = '127.0.0.1:17233'
$env:SPROUT_TEMPORAL_DOCKER_FAULTS = '1'
uv run pytest -q -p no:cacheprovider --basetemp=.workbuddy/test-runs/temporal-validation-new src/Sprout/tests/test_temporal_integration.py src/Sprout/tests/test_temporal_baseline_replay.py
```

不设置测试地址时两组真实服务测试全部跳过。测试生成的当前和基准历史保存在各自临时证据目录；历史含输入和输出，生产历史另行提供前需检查敏感数据，不能拿未脱敏的业务历史直接提交进仓库。
