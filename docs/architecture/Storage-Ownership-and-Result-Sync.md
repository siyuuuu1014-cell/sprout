# Storage Ownership and Result Sync

当前按数据职责路由存储，不是按“哪个模块调用就全部写到它的库”。下表为隔离实验配置；
普通运行由 StorageSettings 的 DSN 决定，不应把实验文件名视为所有部署的固定位置。

| 数据 | 存储位置 | 主要职责 |
|---|---|---|
| 任务、节点、工作流提交和结果、编码/验证占用、变更提案、Git事务证据 | metadata.db | 执行事实与任务状态权威 |
| 任务模型请求预算占用及响应结算 | metadata.db / task_model_requests | 调用前的硬次数约束，不依赖统计库 |
| 工具审批、独立Git提交请求/决策/派发记录 | operational.db | 操作者决策与操作控制 |
| 会话和对话轮次 | session.db | 会话主存储，operational保留兼容回退能力 |
| 组装后的上下文快照及全文检索 | context.db | 上下文数据，不作为执行完成证据 |
| 模型调用用量及相关统计 | usage.db | 用量记录，不等于任务级金额硬封顶 |
| 项目结构/架构/数据库分析与版本 | projects/工作区标识/project_*.db | 项目分析专用分区 |
| 轨迹、审计、容器证据、大对象 | trajectory/、audit.jsonl、coding-evidence/、blobs/ | 文件证据与内容存储 |
| 待改代码和版本历史 | repo/、worktree及Git对象 | 代码和提交，不把源码全文当数据库任务状态 |

项目分析的五分区为manifest、structure、architecture、database、evolution。
先写各版本快照，再发布manifest的active_snapshot_id；这不是跨库事务，失败时应继续读取
此前完整的已发布版本。隔离实验还将knowledge/cache/vectors设为memory、graph设为none，
不能据此认为知识、缓存、向量和图数据也都被永久落到了上述五个实验数据库。

注意metadata.db与operational.db都存在名为tasks的表，但不是一个共享任务权威。
本编码执行图的任务状态由metadata.tasks负责；operational中的兼容操作任务不能用来
覆盖它。同理提交的决策存operational，而实际Git事务/完成证据在metadata，职责不同。

## 显式工作流结果同步

`coding-status`保持只读。只有显式调用以下入口才同步：

```powershell
uv run sprout evolution coding-sync TASK --database EXP/metadata.db
```

1. CLI检查操作者及原accepted记录，选择明确submission。
2. 观察服务只读取该workflow/run的结果，不start/cancel，不拥有Worker。
3. 存储适配器在metadata.db内开启单事务，核对原记录绑定，再更新task_submissions与tasks。
4. 运行中、超时或不可访问不写；迟到历史结果不能覆盖更新提交对应的任务状态，
   已completed/cancelled不降级，已确认结果不替换，未完成图不能凭workflow completed伪造完成。

同步不清除节点unknown/uncertain占用，不改节点执行证据、不审批、不运行Git、不发布成长候选。
无需同时写session/context/usage/operational，也不对多个库宣称原子事务。

已完成任务可能仍绑定历史waiting_approval工作流结果；后续独立Git提交完成任务，
两者都是真实事实。结果同步必须保留这个阶段区别，不能回写历史工作流来使其看似一致。

## 持久化请求预算

显式正数TaskBudget.max_model_calls在所有NodeExecutor节点上绑定同一个任务预算；
底层provider适配器调用前以单条SQLite写入原子占用一次额度。ModelOrchestrator包装层
不再重复占用或重复记录usage，内部每次重试/备用适配器尝试分别计入。
次数不能因换节点、Worker重启或不同进程而重置；失败/未知/占用后中断不退还。
首次占用后的限制不能靠修改任务字段静默重置，目前没有追加额度接口。

usage.db仍是尽力记录的用量统计；预算写入不可用则在发送前阻止请求，不能以统计写入失败
不影响响应的逻辑放开预算。成功响应的token用量同时保存在预算结算中供核对，但没有
实现任务级token预留或金额硬封顶。

范围：已接入任务节点的非流式统一模型调用；max_model_calls=0仍保持原无显式上限语义，
交互/节点外调用以及流式调用尚未全面纳入。第三方适配器在内部绕过统一入口的HTTP重试
不在此层可见，不能声称统计到了所有网络请求；当前DeepSeek直连适配器本身不自动重试。
