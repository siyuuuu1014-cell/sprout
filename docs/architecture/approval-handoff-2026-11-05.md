# SEAM Sprout｜审批体验改造与 Temporal 后续实施交接

> **当前状态（2026-11-05）** PR-A1 LOCAL_COMMITTED、PUSHED，未合并。A/B 最终定向复测通过。C BLOCKED，可信 Sprout 执行入口独立保留，不阻塞 PR-A2。D 已验证单 Python 文件统计。PR-A2 IN_PROGRESS，R04 历史验收 ON_HOLD，Temporal DEFERRED。正式 CLI 恢复已在本地 Temporal 和固定 Docker 镜像上通过。双 Node 授权复用已由真实 Docker 夹具验证。正式 CLI 图完成一个 Coding Node，随后停在既有 GitBroker 审查失败。真实模型和全仓 CI 为 NOT_RUN。
>
> **当前最高优先级** 修复实际问题、完成核心功能、实现 Coding Agent 真实执行闭环。执行顺序为 PR-A2 → PR-A3 → Temporal → Coding Agent 完整功能闭环。保留 Policy.DENY、Broker、工作区范围及 Git Apply 门禁。
>
> 日期：2026-11-05（Asia/Singapore）｜仓库：`https://github.com/siyuuuu1014-cell/sprout`｜目标分支：`dev`  
> **最新已核实基线：`df4e4c5472ccf0fe536ac563a4617ad54f48502a`（PR #9，2026-11-05 14:23 SGT）**。这比先前交接 `9b536e7` 更新。后续开工前需重新 `git fetch origin` 确认 HEAD，若变化先比较。  
> 约束：**不自动修改、提交、推送或合并受保护的 `dev`；本文件是可落实的实施方案，而不是完成证明。**

## 0. 给下一轮开发者或新会话的十行速读

1. 项目定位：Python 模块化 AI coding agent/runtime；**不训练模型、不重建平行 Runtime/Security、单 Agent 真闭环优先**。
2. 两份**权威文档**：`sprout_record(3).zip` 内的 `sprout_record.md`（v1.0，设计方向）；`sprout_update(3).md`（v2.1，实施约束）。GitHub `docs/architecture/sprout-v1.md` 和 `sprout-review-v2.1.md` 为仓库对应版本，复核时注意提交差异。交接 Markdown 是历史依据，不高于这两份文档。
3. 用户本轮决定：**先做审批体验，不先修 R04 CI 两项失败**。不要把旧 CI 失败误写成这项改造的阻塞；测试报告需隔离说明。
4. 用户核心要求：**一个自然语言任务只进行必要的一次授权，不因不同命令、参数、超时等重复弹窗**；已授权 Session 的正常只读工作尽量零审批。
5. 现有 `ApprovalManager`、`ToolExecutor`、Process/File/Git/Network Broker 保留，增量实现 **Task/Turn Capability Grant + Session 只读授权复用 + 审批去重 + UI 精准展示**。
6. 模型不能自己声明授权；**DENY、HardFloor、实际 Broker/资源约束、独立 Verification 和 Git Apply 版本合同不能由一次确认绕过**。
7. 症状证据：附件 `粘贴的文本 (1)(20261105-064339).txt`，“你现在有多少行代码”引发约 13 次 `cli_tool_run` 审批，用户选“当前 Session”后仍不断询问。
8. PR #9 已新增 `workspace/code_metrics.py` 和 `agent/executor.py` 对部分重复调用的抑制；**不要重做这些改动，也不要以此宣称审批已解决**。
9. 审批完毕后做 Temporal：官方示例 `https://docs.temporal.io/develop/python/set-up-your-local-python`，将 workflows / activities / workers / client 分层；保持 Workflow/Activity Type、队列、历史重放兼容。
10. 下一步应从 **A0 审批调用轨迹实测 + A1 精准展示与待审批去重 + A2 Task/Turn 授权合同** 开始，优先给用户的代码量案例做 0–1 次审批验收。

## 1. 设计来源、权威边界与不做事项

**v1.0：** §9 “工具、权限与副作用统一边界”要求 ToolCall 不等于已授权动作；真实操作仍由 Broker 授权；有效权限为 `PrincipalScope ∩ DelegationScope ∩ TaskMode ∩ AgentProfile ∩ SecurityPolicy ∩ BrokerConstraints`，HardFloor 不受模型/Prompt/聊天确认绕过。§12 是 Local/Temporal/Effect Ledger；§15 是统一 Gateway；§17 是真实验收。

**v2.1：** 原 `security.access.ActionRequest` 必须兼容扩展，不能复制同名类型；R04/R06/R06b/R07/R08/R09/R10 等强调统一可信 InvocationContext、版本 CAS、隔离和真实任务 E2E；新增 F22 提醒 `ActionExecutor` 向 ToolExecutor 的 task_id/source 传递可能不完整，不能靠模型参数补身份。不得把审批改造变成宿主执行绕过；不要删除历史审批数据或者擅改现有状态枚举。

**产品层面新决定（用户确认、并非文档原句）：** 将日常“逐命令审批”转为“用户任务能力授权”，减少必要的用户交互；该目标以现有体系增量实现。遇到与 HardFloor、隔离、独立发布确认冲突的情况，不以一次授权覆盖未列明的危险效果。

**明确不做：** 本轮不修 R04 原有两项 CI 失败；不先进行 Temporal 重构；不改模型训练；不做全局 `auto_run=[python,pytest,git]` 这样的粗粒度放行；不删除 Broker；不把 Session ID 本身当作授权凭据；不扩大远程 MCP 或无人值守任务权限。

## 2. 代码实际情况（证据等级 A：GitHub 读取 `df4e4c5`）

### 2.1 权限模块已实现的能力

- `src/Sprout/security/approval.py`：`ApprovalRecord` 含 `task_id`、`requested_by`、`session_id`、`source`、`resource_scope`、`action_hash`、`arguments_fingerprint`、`single_use`、`expires_at`、`approval_class`；`ApprovalManager` 含请求、审批、校验、消费、复用、过期处理。
- `src/Sprout/tools/executor.py`：ToolSpec → LayeredPolicy → REQUIRES_APPROVAL → ApprovalManager → Tool.invoke → 下游 Broker；普通 `ToolCall` 目前逐个创建审批。
- `src/Sprout/security/commands.py`：`READ_ONLY_COMMANDS`、`READ_ONLY_GIT_SUBCOMMANDS`、`auto_run`，但 `sprout` 自身的具体只读业务查询不是通用只读集合成员。
- `src/Sprout/cli/commands/chat.py`：`/permissions`、`_handle_cli_approval_prompt()`、“仅本次”“当前 Session”选项；`_approval_action_lines()` 仅按工具名匹配 `pending_tool_calls`。
- `src/Sprout/runtime/analysis_consent.py`：只读目录确认和 Session 精确目录授权桥接；不能绕开真实 Task/Scope。
- `src/Sprout/agent/executor.py`：PR #9 新加 `seen_calls` 对 `cli_tool_run/project_analyze/workspace_query` 的部分重复调用抑制，**不会为不同参数的命令复用权限**。
- `src/Sprout/workspace/code_metrics.py`：PR #9 新加已授权单个 Python 文件的统计功能；要验证 CLI 是否真的优先路由及项目范围聚合，不能假设整个仓库 LOC 已完成。

### 2.2 现有机制与用户期望之间的差距

**GAP-A / 权限粒度太细**：`ApprovalManager.session_key_for('cli_tool_run', arguments)` 使用除 `event_id` 外的完整参数指纹；`timeout=30` 与 `timeout=60`、`sprout --help` 与 `sprout info` 是不同授权。`class_key_for` 的命令类策略在有 Session 的 `request()` 中被 `session_key_for` 覆盖。当前“批准相似”实际接近“批准精确操作”。

**GAP-B / 每次先遇到一个 ToolCall 才询问**：`ToolExecutor._execute_tool()` 每个 REQUIRES_APPROVAL 独立 `request()`。已定义 `find_pending()`，但该调用路径未将它与原子去重结合；并发或模型重试仍可能出现多个待审批。

**GAP-C / 同名工具审批卡片可能显示多个操作**：`chat.py::_approval_action_lines()` 仅以工具名筛选，可能把两条不同 `cli_tool_run` 的命令同时展示在两个不同审批框，用户无法可靠知道到底批准了什么。

**GAP-D / 缺可信的一次任务授权上下文**：通用 Agent 工具调用仍需检查真正可信的 task_id/node_id/source/principal 传递。必须先查调用路径和已有 `NodeToolInvocationContext`，不要接受工具 JSON 自填 Task/Session 字段。

**GAP-E / 工具选择不经济**：用户问自身 LOC 时模型反复调用 `sprout --help`、`project workspaces`、`info` 等探索命令。先用已有 `code_metrics`/Workspace 读取能力，减少无需审批的命令链；授权机制本身不能弥补模型计划不合理。

**GAP-F / 可能重复底层审批**：`CliRunTool` → ProcessBroker 有一次性私有 handoff 逻辑。调整授权层后必须确保一次高层许可不会变成两次提示，也不能把 `upstream_approval_granted=True` 这种普通布尔值当授权证据。

## 3. 用户期望的审批行为：定稿

用户提出一个请求时，目标不是“每个命令都通过同一 fingerprint”，而是 **一次知情授权覆盖一个受限执行目的**。

| 场景 | 预期授权体验 | 实际授权边界 |
| --- | --- | --- |
| 纯聊天、已授权项目的普通目录/文件信息查询、帮助/版本/状态 | 0 次新增操作审批 | 只读范围；不允许额外写入或运行未限制代码 |
| 新工作区首次读取或目录分析 | 用户一次确认工作区/目录范围 | 当前 Principal + Session + Workspace + 规范化根路径；具体读取仍走 FileBroker |
| 同一问题的多命令检索与统计 | 0–1 次任务级授权，不按命令/timeout 分别问 | 工具类别、操作意图、资源集合及执行环境绑定；超范围重新确认 |
| 编码任务的多次生成、测试、检查 | 初始 Task 许可覆盖必要的受控操作；不逐条弹窗 | Task ID、Principal、Workspace、TaskMode、隔离环境、允许的动作类别与风险上限 |
| 冻结验收后的最终 Git Apply、push/远端发布、敏感数据外传、超出任务范围的删除 | 保留 v1/v2.1 要求的针对性最终确认/专门合同；可批量展示但不可隐性授权 | Proposal/Patch/Oracle/HEAD CAS、目标仓库、最终版本、具体外部效果 |
| 新身份、新 Session、权限撤销、Policy 变更、资源移出 Scope | 不能继承旧许可 | 原 Grant 不再适用 |
| 自动化/无人在场/远端 MCP | 走原有更严格模式；不把交互式许可推广过去 | 现有 source/Principal/Policy/HardFloor 原样执行 |

**说明**：同一问题可以发生多个工具调用；如果没有新增授权边界，**无需因不同命令、参数、超时时间重复问用户**。对于可能执行任意项目代码的 `python -c`、`pytest`、`npm test` 等，不能因为名字像“统计/测试”就视为只读；应该选择受控执行环境或事先明确包含的任务能力授权。

## 4. 推荐合同：一个审批体系中的 Task / Turn Capability Grant

### 4.1 两层并行但不冲突的授权语义

1. **Session Read Grant**：针对确认过的 Workspace/目录的只读操作，在同一身份与 Session 内可复用；由可信 Host UI 建立。目录失效、重绑定、Policy 改变时重新验证，不能直接变成写权限。
2. **Task/Turn Capability Grant**：针对单次自然语言请求或已创建 Task 的受限操作类别，允许不同参数的不同命令。优先 `task_id`，普通聊天路由尚未创建 Task 时使用 Host 生成的 `turn_id`/请求关联 ID，不能只用裸 `session_id`。
3. **Exact Effect Grant（保留）**：Git Apply、外部发布等需要版本绑定的动作仍使用现有精确审批/验收合同，不被 Task Grant 粗略覆盖。

可以在现有 `ApprovalManager`、现有 OperationalStore/ApprovalStore 内引入**可版本化的 grant 类型**，而不是增加平行的授权数据库或第二套权限引擎。不要复用旧 `session-exact-v1` 指纹去做跨命令泛化：保留旧记录解释与行为，新增 `task-capability-v1` 合同键和受控的匹配策略。

### 4.2 Grant 设计草案（实施前以现有模型和迁移成本校准）

以下是**目标合同/伪结构，非已存在代码**：

```python
@dataclass(frozen=True)
class CapabilityGrantV1:
    grant_id: str
    origin_approval_id: str
    principal_id: str
    principal_source: str           # cli/web/mcp，类型不得由 LLM 声明
    session_id: str
    task_id: str | None             # 如果 Task 存在，用它作为优先权威
    turn_id: str | None             # 仅对尚未创建 Task 的普通交互使用
    workspace_id: str
    canonical_roots: tuple[str, ...]
    action_families: tuple[str, ...]  # e.g. workspace.read, command.inspect, sandbox.check
    execution_boundary: str        # trusted_local/restricted_coding 等
    policy_version: str            # Policy 规则/配置指纹
    expires_at: datetime
    revoked: bool = False
```

**必要校验**：

- 只能由可信 CLI/Web/Gateway 在用户实际确认后创建；LLM、MCP 工具参数、Message Metadata 不得伪造。
- 每次实际操作必须重新执行 `ToolSpec + Policy + Scope + Broker`，**DENY 永远不能被 Grant 转 ALLOW**；Grant 只能满足当前 `REQUIRE_APPROVAL`，不能添加缺失的 Task/Principal/Resource 身份。
- `action_families` 为显式枚举，**不能用 `cli_tool_run:any`、`python:any`、所有 Git 命令**。`command.inspect` 不包含脚本解释器、Shell、Git write、秘密读取或任意外联。
- 授权绑定规范化路径、可辨识 Workspace ID、可信主体、场景与执行环境；每次检查是否仍在许可范围、未过期、未撤销、Policy 版本未失效。
- 不依赖 timeout 参数等非本质参数来决定“是否跨命令授权”；timeout 仍受 Broker 的独立上限限制。
- 有明确的结束/撤销/TTL 语义；默认只对当前 Task/Turn 生效；Session 只读可复用但不是无限执行许可。
- Agent 的动作及 Broker 的实际副作用仍记录 `session_id/task_id/node_id/action_id/grant_id`；敏感内容脱敏、可审计。

### 4.3 统一授权前置（目标状态）

```text
用户请求
  -> Intent / Task or Turn (可信 Host 生成身份)
  -> 判断本轮需要的动作族和 Workspace 边界（不由 LLM 单方面认定）
  -> 如果现有 Grant 足够：跳过新审批；否则展示一次聚合授权面板
  -> 同意后持久化 CapabilityGrantV1
  -> AgentLoop 多次 ToolCall（命令不同无妨）
  -> 每次 ToolExecutor 检查 ToolSpec/Policy/Scope + CapabilityGrantV1
  -> 每次 Broker 重新判断真实路径/动作/执行模式
  -> 结果/evidence/审计
  -> 最终 Apply 如需独立验收，走既有版本绑定门禁
```

**失败行为**：一次拒绝立即停止该未授权动作，明确告诉用户无法完成哪些部分；不进行多轮换命令试探式反复请求；新任务与不同身份不自动接管旧许可；取消/异常/重放的无副作用状态要可确定复现。

## 5. 可实施的开发拆分（从 02 开工；不是重排原 R04）

### A0 — 现状探针与测试夹具（只针对审批，不修历史 R04 失败）

- 为日志中的“你现在有多少行代码 / 第二种”建立确定性测试输入；记录 `LLM -> AgentLoop -> ToolExecutor -> ApprovalManager -> ProcessBroker` 的工具名、参数、来源、任务 ID、审批记录数与呈现行数。
- 保留 PR #9 的 `seen_calls` 与 `code_metrics`，确保它们的能力优先发挥作用；不把模型的重复 ToolCall 计数误当真实执行次数。
- 每项测试同时断言 **审批次数** 和 **实际执行结果**；不能为了降低提示而跳过统计/分析任务。

### A1 — 低风险快修：展示准确、去重有效、只读能力发现

- `chat.py::_approval_action_lines`：按准确的 `tool_call_id` 或记录参数指纹匹配审批；找不到时回退 `ApprovalRecord.action_summary`，不要展示同名工具的其他操作。
- `ApprovalManager` / `ToolExecutor`：对同一 Task+Principal+Session+工具+实参+范围的待审批做**原子 get-or-create**，并发/重试仅产生一个活跃记录；避免只做普通 find→request 形成竞态。已拒绝的记录不得错误复用。
- `security/commands.py` + ToolExecutor：使用**具体子命令语法表**将 `sprout --help/--version/info/project workspaces/... --help` 等已确认无副作用的业务查询归入只读候选；如果实现可能间接修改状态或泄漏秘密，维持审批/拒绝。不要给整个 `sprout` 可执行文件全局自动运行权限。
- `workspace/code_metrics.py` + `runtime/analysis_consent.py`：按 PR #9 已有结果核查路由与限制；如需统计仓库总 LOC，优先通过已授权 Workspace 读取/专用统计服务，不让模型用 `python -c` 拼脚本。
- CLI 提示信息改为真实的“覆盖操作类别/工作区/有效期”；不能继续把一次 Session 精确操作称为宽泛的“同类命令”。

**A1 DoD**：不会错显两个审批的命令；同一精确动作重复请求只有一张待审批；只读帮助/状态查询不重复问；保留 DENY、拒绝、超时、底层 Broker 拒绝等负例。

### A2 — 核心改造：一次任务授权跨不同命令

- 新增兼容 `Task/Turn CapabilityGrant` 的协议与存储，不破坏既有 ApprovalRecord、`session-exact-v1`、数据库旧记录；数据迁移需要显式 version/default。
- 从 Runtime/Gateway 将可信 `principal + session + workspace + task/node/source + turn_id + policy_revision` 传给通用 `AgentLoop -> ActionExecutor -> ToolExecutor`；复用/扩展现有 `NodeToolInvocationContext`，不要从模型 JSON 获取 authority。
- 按 `action_families` 做任务授权检查，将“命令差异”与“权限边界差异”分开；`DENY` 及底层 Broker 仍 fail-closed。
- CLI 一次审批面板列出“工作区、读取范围、将用到的能力类别、是否在隔离容器、期限、不能做的动作”，提供“仅本次 Task / 当前 Session 内只读工作区 / 拒绝”选择。
- 对于项目明确的任务阶段，可以批量汇总即将执行的 capability；若后来需求扩至危险范围，才重新申请，**同一类后续命令不重复申请**。

**A2 DoD**：同一个真实任务运行 5–10 个不同检查命令，最多一次任务授权；不同 Session/用户/工作区无法复用；权限变化/路径变化/拒绝、撤销、过期均阻断；CI 负例没有回退宿主执行。

### A3 — 跨入口及 E2E 验收

- CLI / Web / SDK / MCP 只通过同一个 ApprovalManager 和 Broker 校验；MCP/unattended 不承接交互许可。
- 真实本地工作区执行代码量统计任务，验证返回准确数据且无需多次批准；编码任务验证在一次能力审批后能持续检查和修复，直到最终独立 Apply 门禁。
- 真实复测用户附件中的命令序列，记录审批次数对比；若模型无效重复调用仍很频繁，独立标记“工具路由缺陷”，不靠更多审批规避。
- 汇总指标：`approval_prompts_per_request`、`duplicate_pending_approvals`、`grant_reuse_rate`、`task_success_after_approval`、`denied_attempts`、`actual_effects`；这些数值是**待采集**不是已完成。

### 推荐 PR 边界

| PR | 内容 | 主要文件 | 依赖 |
| --- | --- | --- | --- |
| PR-A1 | 展示正确 + 请求去重 + 精确只读分类 | `cli/commands/chat.py`, `security/approval.py`, `tools/executor.py`, `security/commands.py`, tests | `df4e4c5` |
| PR-A2 | `Task/Turn CapabilityGrant` 与可信上下文 | `security/approval.py`, storage approval adapter, `agent/executor.py`, `agent/loop.py`, `runtime/runtime.py`, `tools/executor.py`, tests | PR-A1 |
| PR-A3 | 聚合授权 UI + CLI/Web/SDK 适配 + 真实验收 | `cli/commands/chat.py`, `web/webapi/routes/approvals.py`, gateway, tests | PR-A2 |

**不建议一个 PR 同时重构 Temporal 或改动 Git Apply 状态机。**

## 6. 必须通过的自动化测试矩阵（新增，而非复用旧版本 PASS）

| 用例 | 目标结果 |
| --- | --- |
| 已授权 Workspace 的单文件 Python LOC | 0 次新增操作审批，实际数据正确；无反复 `sprout --help` |
| 首次新 Workspace 读取 | 1 次明确范围确认；以后同 Session 已授权读取 0 次重复 |
| 同 Task 执行 `sprout info` / `--version` / `project workspaces` / 安全统计 | 不因子命令变化重复询问；不调用未知危险操作 |
| 同 Task 多个已列明的受控 `check/test` 命令 | 初始 1 次 Task Grant，其后不同 argv/timeout 不重复问；真实运行有证据 |
| 低风险命令的恶意参数（`--exec`、脚本、`python -c`、`git -c` 等） | 不能依赖只读自动放行，按实际风险拒绝或需明确授权 |
| 相同工具名、两个不同待审批命令 | 审批卡片只显示各自真实操作 |
| 相同 Task 与动作的并发请求 | 活跃待审批记录仅一个，已批准的单次许可不双重消费 |
| Grant 已过期/撤销/Policy 改动 | 不自动放行；有审计解释 |
| 跨 Session / 不同 Principal / 不同 Workspace / 不同 Task | 不继承一般 Task Grant |
| Broker DENY / 非法路径 / Symlink / 权限截断 | 上层确认也不能绕过底层限制 |
| Task 从等待审批继续 | 原 Task/Session/Node/执行结果正确；不重复已发生的外部效果 |
| Git Apply / push / 无法独立验证的变更 | 继续保留 v1/v2.1 对应独立合同，不能因 Task Grant 放行 |
| 拒绝后模型连续提出其他相同动作 | 不再反复骚扰；任务明确等待/拒绝/局部结束 |

建议新增：`test_approval_task_capability.py`、`test_approval_pending_dedupe.py`、`test_cli_approval_card_binding.py`、`test_permission_grant_policy_intersection.py`、`test_cli_code_metrics_approval_e2e.py`。先看现有测试中的夹具和名字，避免重命名/复制。

**专门的测试命令（建议，不代表已运行）**：

```powershell
git fetch origin
git switch dev
git pull --ff-only origin dev
uv sync --locked --group dev
uv run pytest src/Sprout/tests/test_approval_visibility.py src/Sprout/tests/test_approval_class_scope.py src/Sprout/tests/test_r04_cli_continuity.py -q
uv run pytest src/Sprout/tests/test_authz.py src/Sprout/tests/test_background_task_approvals.py src/Sprout/tests/test_r04_upstream_approval_audit.py -q
uv run ruff check src/Sprout/security src/Sprout/tools src/Sprout/agent src/Sprout/cli
```

不要自动运行完整 R04 基线作为前置；但任何**本 PR 直接造成的回归**必须修复，不能以“跳过 R04”作为拒绝验收新增权限代码的理由。

## 7. 对 R04 当前状态的处理（仅记录，不作为本轮任务）

- 交接提及 DeepSeek thinking tool history HTTP 400、ProjectAnalyze 路径等问题。它们属于其他待办，**本轮不顺手修复模型协议**。
- 先前 `9b536e7` 的 GitHub L1 两项失败，历史记录在交接文档和前一轮审查中；本轮不以修复它们为开工条件。
- **PR #9 / `df4e4c5`** 已新增 `code_metrics.py` 和工具去重，应以这个 HEAD 再测实际效果，不能据前一版本的测试结论硬判当前重复情况。
- 对新审批代码只跑必要的目标测试和关联安全回归；历史失败与新引入失败必须分列。

## 8. 下阶段 Temporal 方案（预留，不与审批 PR 混改）

用户提供官方参考：`https://docs.temporal.io/develop/python/set-up-your-local-python`。期待 Workflow、Activity、Worker 定义分文件并由 Worker 注册调用。该文件组织方式是官方示例所采用的结构，Sprout 可扩展成按职责分类的子包，而非只能存在 4 个大文件。

**当前代码形态（旧审查 + 需在正式开工时复核 HEAD）**：

- `orchestration/temporal_workflows.py` 混合 Workflow 类和 `@activity.defn`；存在模块级 `_activity_runtime` / `set_activity_runtime`。
- `orchestration/worker.py` 在 Worker 入口定义 `@activity.defn(name="run_sprout_node")`，同时有通用 Worker；`isolated_worker.py` 将 Activity 方法放在 Worker 类中，已经使用实例绑定，但文件职责混在一起。
- `orchestration/terminal/temporal.py` 已承担 Client 提交、结果查询、状态持久化；`commit_workflows.py` 已有独立提交 Workflow，可保留。
- `SproutGrowthWorkflow` 的 `execute_activity` 将两个参数组装成位置 tuple 且传 `start_to_close_timeout=3600`，疑似与 SDK 参数/`timedelta` 类型合同不匹配；**需测试复现**，不能只凭静态审查宣告真实服务失败。
- `SproutTaskWorkflow` 与 `SproutGatewayWorkflow` 仅返回排队/已提交状态，不应称已实现真实执行。

建议新增 `orchestration/workflows/{execution,task,growth,gateway,commit}.py`、`orchestration/activities/{node,growth,commit}.py`、`orchestration/workers/{task,growth,isolated_coding,commit}.py`，原模块做兼容性导出；`terminal/temporal.py` 继续负责 Client 适配。**保持 `SproutExecutionWorkflow`、`run_sprout_node`、`run_experiment_commit` 的 Type 名称、参数、TaskQueue、`workflow.patched(...)` 等历史指令兼容**，用 Replayer 验证旧历史。Activity 容纳 Runtime、Broker、文件、模型、外部副作用；Workflow 只作可重放的确定性调度。

Temporal 开工顺序：T0 固定现状 replay/queue 类型 → T1 文件与依赖注入分离 → T2 SDK 合同修正 → T3 Worker/Client/Activity 真实集成 → T4 worker 中断与任务恢复 / UNKNOWN Effect / 不重复副作用 → T5 文档/部署命令。**用户现在要求先做审批，Temporal 暂未动工。**

## 9. 历史记录（PR-A1 开工指令，已由当前路线取代）

> 请在 `siyuuuu1014-cell/sprout` 最新 `dev` 上，先做“审批体验改造”的 PR-A1（显示准确、审批去重、低风险只读分类与代码量统计入口）。遵守《SEAM Sprout 技术架构与实施总方案 v1.0》的 §9/§12/§15/§17 和《v2.1 修订版》的 R04/R06/R06b/F22，保留既有 `ApprovalManager`、ToolExecutor、Broker、Task 和受保护 `dev`。不得先修 R04 历史 CI 故障，不得重构 Temporal，不得做平行审批体系。先用源代码和测试确认 PR #9 的 `seen_calls`、`workspace/code_metrics.py` 已做了什么；针对实际日志“一个代码行数问题反复弹审批”的负例写失败测试，再以最小更改修复。
>
> PR-A1 验收：同工具名的多张卡片显示各自准确命令；同一可信 Task+Principal+Session+精确动作的并发/重复请求至多一条活跃待审批；`sprout` 的严格限定只读查询不反复索要授权，不能把 `sprout`/`python`/`git` 整体列为 auto_run；其他范围保持原有 DENY、ProcessBroker handoff 和禁宿主回退。随后输出 diff、目标测试 PASS/FAIL、尚待 PR-A2 的跨命令 Task Capability Grant 具体接口设计。未经用户明确要求，不要自行 commit、push、merge。

**PR-A1 做完之后**，推进 PR-A2：一次任务授权跨不同命令/参数、Session 已授权只读复用、可信 `InvocationContext`，保留所有 Scope/Policy/Broker 检查及最终 Apply 版本门禁。不要靠拼接命令名指纹来模拟 Task Grant。

## 10. 续做规则 / 进度账本

| 项目 | 状态（文件创建时） | 下一动作 |
| --- | --- | --- |
| `dev` 基线核查 | DONE：`df4e4c5` | 开工前再确认 SHA |
| 权威 v1/v2.1 约束映射 | DONE：已整理 | 以原文档为准 |
| 审批问题复盘 | DONE：静态源码 + 用户附件 | 补当前 HEAD 动态复现 |
| A0 审批 E2E 测量 | TODO | 创建测试轨迹/对照数据 |
| A1 UI 卡片/待审批去重/只读入口 | IN PROGRESS：审批卡片精准匹配补丁已准备，其余 TODO | 先审查和应用本地 `.patch`、运行对应测试；再做原子去重与只读能力 |
| A2 Task/Turn 能力授权 | TODO | A1 后实施 |
| A3 多入口与真实验收 | TODO | A2 后实施 |
| Temporal 模块重构 | DEFERRED | 审批稳定后单独分支 |
| R04 当前验收基线修复 | ON HOLD：用户明确要求暂不处理 | 未得到新指令不优先 |
| 真实付费模型与 Docker/Temporal 动态验证 | NOT RUN 本轮 | 根据相应任务单分别安排 |
| GitHub 源码修改/提交/推送 | NOT DONE | 只生成实施文件与可审查的初始补丁，不动远端 |

**本轮额外产物：** `SEAM_Sprout_PR-A1_审批卡片精准匹配_首批补丁.patch`（仅修复审批卡片按完整参数指纹匹配，附两条新测试；尚未应用和在真实仓库执行。不是 PR-A1 全部完成，也不涉及 Task 能力授权）。建议在最新代码工作副本使用 `git apply --check <patch>`，确认后再应用、运行 `uv run pytest src/Sprout/tests/test_approval_visibility.py -q`。

**结束提示：** 在新的会话里上传/引用本文件，说明“继续 PR-A1”，就可直接基于这里的文件索引和验收矩阵进入编码；若已完成 PR-A1，改为“继续 PR-A2”，并补充实际提交 SHA、测试命令及结果。不要将本方案写成“已实施”。


## PR-A1 — 2026-11-05 本地进度

- DONE：核实远端 dev 为 df4e4c5472ccf0fe536ac563a4617ad54f48502a，创建 codex/approval-pr-a1。
- DONE：审批卡按参数指纹匹配，缺失时回退持久化摘要。
- DONE：内存与 SQLite 对具备 Task、主体和 Session 的同一活跃待审批请求原子去重。保留已有单文件 Python 统计路由。
- DONE：定向回归 142 passed，1 skipped（当前环境无法创建符号链接）。并发夹具中，8 次申请持久化 1 条待审批。
- FAILED：锁定环境的离线安装缺少 temporalio==1.33.0 wheel。定向权限测试改用本机项目虚拟环境。
- TODO：安全的 Sprout 帮助和版本免审批需要可信的可执行文件绑定及 Broker 内的精确判定。info 读取有效配置，project workspaces 初始化 Gateway 并输出注册路径，仍保留原审批。
- TODO：无可信 Task 的普通聊天调用保持现有行为。PR-A2 负责可信 Task/Turn 上下文、能力授权和生命周期。
- NOT_RUN：真实模型对话审批计数、真实 Docker、Temporal、全仓 CI。
- 本地未提交，等待审核。

### PR-A1 阶段验收

- DONE：CLI 对同批 ID 去重，跳过已决定或已过期的记录。模拟恢复返回同一已决定 ID 时只提示一次。拒绝分支执行次数为零，批准分支执行一次。
- DONE：同一请求只由 ApprovalManager 发布一次 approval.requested。并发夹具分别统计 1 条待审批、1 条请求事件、1 次 CLI 提示。SQLite 使用两个独立连接验证，内存使用同一存储实例。不同任务、主体、Session、来源和资源独立建单，终态不作为待审批复用。
- DONE：保留旧存储协议。没有原子操作的适配器保持原有建单语义。无需数据库迁移。
- DONE：项目现有虚拟环境定向回归 220 passed，1 skipped。符号链接在当前环境不可用。Windows CLI 单 Python 文件统计与路径拒绝均通过。
- FAILED（修复前回归）：同一总线收到 2 次请求事件，恢复返回旧 ID 时 CLI 递归重提示。最终回归已通过。
- 环境记录：离线 uv 缓存缺少 Temporal wheel，属于依赖准备失败。
- BLOCKED（C）：ProcessBroker 启动时向系统传递原始 argv。命令名、PATH 预解析和可执行文件后缀都不能绑定实际启动映像。固定帮助参数、版本参数保留审批，直至能在 Broker 内验证并绑定可信安装入口。info 读取配置，project workspaces 构造 Gateway 并枚举注册路径。
- TODO：全仓代码行数独立待办。现有统计适用于授权选中的单个 Python 文件。
- NOT_RUN：真实模型会话、Docker、Temporal、全仓 CI。
- PR-A2 仅规划可信 Task/Turn 上下文和能力授权的范围、期限、撤销与隔离。本轮未启动，全部修改未提交。


### PR-A1 最终审查状态

| 项目 | 状态 |
| --- | --- |
| PR-A1 | LOCAL_TESTED，等待代码审查 |
| A/B | 定向测试通过 |
| C | BLOCKED，独立后续任务。需在 Broker 验证并绑定可信执行入口 |
| D | 已验证现有授权单 Python 文件统计和拒绝路径 |
| PR-A2 | NOT_STARTED |
| 实际模型对话、全仓 CI、Temporal | NOT_RUN |

定向测试最终结果为 220 通过、1 跳过。当前环境无法创建符号链接。基线为 df4e4c5472ccf0fe536ac563a4617ad54f48502a。


### PR-A1 收尾复测

- LOCAL_TESTED，等待确认。A/B 此前定向回归通过，本轮最终复测为 224 通过、1 跳过。跳过项需要当前环境不可用的符号链接。
- DONE：同一总线收到一次审批请求事件。独立总线各收到一次。并发仍只产生一条待审批。CLI 同意后执行一次，拒绝后执行零次。
- DONE：内存与 SQLite 在原子复用时保留原 ID，持久化较早的截止时间。更长或无期限的重复请求不会延长原截止时间。SQLite 跨连接回归通过。
- BLOCKED（C）：可信 Sprout 执行入口独立保留，不阻塞 PR-A2。D 的现有授权单 Python 文件统计和路径拒绝已复测。
- PR-A2 NOT_STARTED。范围为可信 Task/Turn 上下文、同任务一次能力授权、跨命令复用，以及主体、工作区、期限和撤销约束。
- R04 历史验收 ON_HOLD。Temporal DEFERRED。真实模型对话、Docker、全仓 CI 和 Temporal 测试均为 NOT_RUN。


### PR-A2 开工记录

- PR-A1 LOCAL_COMMITTED、PUSHED，提交为 `e116657f6e9874f557afcb5d0cf50dbe53692630`。推送时 `origin/dev` 为 `df4e4c5472ccf0fe536ac563a4617ad54f48502a`。
- PR-A2 IN_PROGRESS，本地分支 `codex/approval-pr-a2` 直接继承上述 A1 提交。
- 调用链核对完成。受节点约束的 ToolExecutor 可从 Runtime 获取可信任务身份和工作区。普通 Agent 执行路径当前只传主体和 Session。CLI 的进程授权交接按实际命令绑定，验证轮次另有专用审批，冻结验证和 Git Apply 有独立门禁。
- 待实现服务端 Task/Turn 上下文、显式任务能力授权与审批呈现、期限和撤销、任务结束时失效，以及进程执行集成和负例回归。
- A2 定向测试和实际审批次数为 NOT_RUN。此前 A1 的 224 passed、1 skipped 仅属于 A1。


### PR-A2 CLI Turn 实现与复测

- IN_PROGRESS，当前分支 codex/approval-pr-a2，基线 e116657f6e9874f557afcb5d0cf50dbe53692630。A1 已推送，未合并。
- 已实现认证 CLI 普通会话的 Host Turn、任务授权选项、同一可执行文件和工作目录下跨参数和超时复用。授权基于现有 ApprovalRecord，复用资格保存在当前 Runtime。ProcessBroker 仍执行策略判定，并在消费精确调用交接时重新检查任务授权。
- 已验证确定性模型发出五个不同命令，实际子进程启动五次，CLI 提示一次。拒绝提示一次、启动零次。同 Session 新 Turn 重新要求审批，批准后取消阻断恢复。
- PASS：A2 独立最终测试 6 passed。直接回归此前 186 passed、1 skipped，符号链接不可用。新增取消用例在独立最终测试中通过。NOT_RUN：真实模型、全仓 CI、Docker 和 Temporal。
- 当前能力按可执行文件和工作目录授权，参数和超时可变，环境覆盖不在授权内。工作目录限制沿用现有进程策略，宿主进程没有新增文件系统隔离。
- 待完成：持久 Task 节点的能力授权、注册 Workspace 绑定、跨进程恢复和 CLI 撤销入口。当前撤销和取消由 Runtime 入口提供。R04 历史验收 ON_HOLD。


### PR-A2 v2 定向验收

- DONE：CLI 一次审批可覆盖本批次明确展示的多个程序。每条待审批保留独立记录和决定。后续命令只在已批准的程序、Turn 和工作目录内复用。
- DONE：普通 CLI Turn 在当前目录与唯一注册 Workspace 根目录匹配时绑定其 ID 和路径，恢复时重新核对注册路径。未配置 MetadataStore 的嵌入场景保留原目录绑定。
- DONE：Python 与 Git 的确定性测试实际运行五条命令，提示 1 次，创建 2 条审批记录，成功工具执行和子进程启动均为 5 次。最终定向回归 188 passed、1 skipped，跳过项需要符号链接。
- BLOCKED：持久 Task 节点的授权接入未完成。Coding Node 使用隔离命令执行器，现有 CLI Turn 许可不能代替隔离节点的身份、工作区和恢复合同。
- NOT_RUN：持久节点跨节点复用、Runtime 重启后恢复、真实模型、全仓 CI、Docker、Temporal。跨进程恢复需持久授权与现有节点执行凭证协同，防止已执行操作被重放。当前 CLI Turn 复用资格随 Runtime 退出失效。


### PR-A2 v3 批次拒绝回归

- DONE：拒绝当前展示的 Python 和 Git 批次时，CLI 一次提示处理两条审批，状态均为 REJECTED，实际启动 0 次。修复前的同一用例观察到 2 次提示。
- PASS：最终定向回归 189 passed、1 skipped。符号链接不可用。既有跨程序批准测试继续断言 1 次提示、2 条审批、5 次真实启动和 5 个结果。
- IN_PROGRESS：持久 Coding Task 路径为 NodeExecutor → IsolatedCodingCommandTool → PlanBoundCodingCommands → CodingNodeSession → IsolatedNodeDispatcher → DockerCodingBackend。节点输出持有 pending_approvals 和 pending_tool_calls。持久任务的任务级授权、跨节点复用、审批恢复与重建验收尚未实现。
- NOT_RUN：持久 Coding Node 真执行、重建 Runtime 后的恢复、Temporal、全仓 CI。Docker 引擎检查返回 29.8.0，尚未运行容器验收。现有本地节点守卫和执行记录仍需接入并验证，当前没有证据将缺口归因于 Temporal。


### PR-A2 v4 持久 Coding Task 授权

- IN_PROGRESS。现有审批存储新增版本化操作类别 coding_task_capability_v1。授予隔离 Coding 命令和受限产物导入，绑定 Task、CLI Actor、Workspace 和冻结任务输入。不同 Node 仍分别校验持久所有权。
- 已接入 ToolExecutor 的 REQUIRE_APPROVAL 分支及现有 replay-continue --approve。审批和恢复沿用现有等待节点。独立验证与 Git Apply 使用各自的授权。
- PASS：SQLite 用例观察到 1 条初始审批，两次申请返回同一 ID。重开审批库及新建 ApprovalManager 后，第二个已认领 Node 读到同一授权。已完成 Node 无法再次取得授权。覆盖过期、撤销、拒绝、取消、完成和不同 Task。
- PASS：最终定向回归 113 passed，1 skipped。跳过的 Docker 图测试要求 SPROUT_CODING_TEST_IMAGE 指向显式固定镜像。
- NOT_RUN：持久 Coding Node 的实际容器执行、完整 Runtime 重建并恢复、审批后原 Task 端到端跑完、Temporal 和全仓 CI。SQLite 重开验证仅覆盖授权持久性和节点守卫。本轮未记录真实容器启动或完成 Node。


### PR-A2 v5 固定镜像验收

- DONE：Coding 命令适配器向 Broker 提供绑定本次调用的私有校验。Broker 先应用原策略，仅在 REQUIRE_APPROVAL 时查验当前 Task 能力。范围为后端生成的镜像检查、预检和容器启动，继续检查当前节点所有权、Actor、Workspace 和权限期限。冻结验证不消费该交接。
- PASS：固定本地镜像 sha256:11880faa03b5dda1f3f54a915ad1b0e3d2a97664799d7a9bfcd3790a9dc7a31d。Task 773f2d82-96e0-4736-a42f-0377fae51b41 使用审批 56b0bfc3-2a06-40e5-8573-0faae338033e。重开 SQLite 审批库并重建管理器、守卫和命令适配器后，等待节点及后续节点均真实运行。两个 Coding 容器退出码 0，stdout 均为 coding-node-ok。两个已完成节点重新进入守卫时未调用执行器。另有 1 次基线验证容器启动。
- 验收范围：测试用审批 API 明确批准 1 次，产生 1 条记录，没有交互提示。Coding 进程策略显式返回 REQUIRE_APPROVAL，真实 Broker 没有再建单。夹具直接用 NodeExecutionGuard 恢复，未执行完整 Runtime 重建或 replay-continue CLI。
- FAILED：原有 Docker 图用例进入最终审批节点时未找到预期的 pending ChangeProposal。该失败在 v5 Broker 修改前已复现，未对 A1 基线执行 Docker 对照。最终相关集合 68 passed、1 failed。
- NOT_RUN：完整 Runtime 重建与 CLI 续跑、真实模型、Temporal 和全仓 CI。PR-A2 保持 IN_PROGRESS。


### PR-A2 v6 基线对照与验收边界

- 对照：在独立 detached 工作树检出 A1 e116657f6e9874f557afcb5d0cf50dbe53692630，用相同固定镜像运行原始图测试。A1 与 A2 existing-graph 均在最终审批处缺少 ChangeProposal。两个 SQLite 节点记录均为 git_diff_unavailable: Task-owned GitBroker review required。该失败在 A1 已可复现。
- PASS：A2 双节点容器夹具再次通过。Task e662431c-0a9a-4421-b95f-33709a8f4eac 使用审批 220eecae-bd8a-42b1-bde6-e919aa481785。两个 Coding 节点退出码均为 0，stdout 均为 coding-node-ok，完成后重新进入守卫未增加执行器调用。
- 定向统计：68 passed、1 failed、1 skipped。失败为上述既有图用例，跳过的是要求专用 Temporal 路由的 detached CLI 集成入口。
- NOT_RUN：完整 Runtime 重建并经 replay-continue --approve 恢复。当前没有运行中的专用 Temporal 测试端点。双节点 Docker 夹具通过守卫恢复，验收范围不包含真实 CLI 续跑。PR-A2 保持 IN_PROGRESS。


### PR-A2 v7 专项验收状态

- PRE_EXISTING_FAILURE：existing-graph 在 A1 e116657f 和 A2 上使用同一固定镜像均缺少待审批 ChangeProposal。节点记录为 git_diff_unavailable: Task-owned GitBroker review required。保留原始断言。
- ENV_BLOCKED：PATH 和已检查的本地工具目录、下载目录及 SDK 常用缓存未发现可直接运行的 Temporal 开发服务器。127.0.0.1:17233 没有监听。SDK 自动下载路径未执行。
- NOT_RUN：启动专用 Temporal 服务、关闭并重建完整 Runtime、通过真实 replay-continue --approve 继续原 Task、在该路径采集 Docker 启动、输出和防重放证据。本轮没有新增或运行 CLI 集成用例。
- 沿用 v6 实测统计 68 passed、1 failed、1 skipped。失败为上述基线问题，跳过为缺少专用 Temporal 端点的 detached CLI 集成。双节点 Docker 结果仍属于守卫夹具验收。PR-A2 保持 IN_PROGRESS。


### PR-A2 v8 正式 CLI 入口验收

- 环境已就绪。Windows amd64 官方 Temporal CLI v1.10.0，内置 Server 1.32.2。发行 ZIP 与官方 checksums.txt 匹配。二进制 SHA-256 为 4CFD15F9753EB6AA47CA2AF00467C087F4AFC389C650B0B50A40982656057075。服务仅监听 127.0.0.1:17233，测试后已关闭。
- A1 原始 detached CLI 用例和 A2 首次尝试均在 coding-submit 返回 preparation_subject_unavailable。仅给 A2 测试任务补齐显式工作区范围后，真实 CLI 提交与 Worker 启动通过。
- FAILED：Task a80bce59-70f5-4af4-bb32-e152ff139c6a 的 agent 节点进入 waiting_resource。SQLite 记录 PermissionError: node_artifact_provenance_unavailable。该错误发生在创建任务能力审批前。补齐范围后的失败尚未在 A1 对照。
- NOT_RUN：真实 replay-continue --approve、原任务重建后续跑、该路径的审批 ID、后续节点授权复用和防重放。既有 Docker 图失败仍标记 PRE_EXISTING_FAILURE。本轮没有修改审批或 Temporal 生产代码。PR-A2 保持 IN_PROGRESS。


### PR-A2 v9 来源与正式恢复验收

- A1 和 A2 均在显式工作区范围下复现隔离 TEST_DESIGN 来源缺失。READ、SANDBOX、PLAN 的来源记录存在，输出摘要匹配，父节点凭据齐全。TEST_DESIGN 已完成，但 _remember_node_product 的 host_acceptance_v1 分支不识别隔离基线结果，未生成待持久化的来源记录。
- 独立 R04 衔接补丁复用现有产物校验及来源持久化，验证隔离基线的上游来源，并登记真实执行结果。恢复只接纳当前持久守卫节点的待执行调用。Coding 工具绑定与 FileBroker 导入使用实时 Task、Node、Sandbox 和具体请求路径。
- PASS：正式 coding-submit 和独立 Worker 到达任务能力等待。原 Worker 退出后，真实 replay-continue --approve 重新组装 Runtime/Worker。Task 3f83971f-00fb-46fe-a0ca-f4e987a1ffe9 及其 :agent 节点保持不变。审批 10094896-0c85-46ce-a7aa-509325eabae3 是唯一记录。原 Node 完成，一次 Coding 容器启动，退出码 0，stdout 为空。再次 CLI 续跑被拒绝，持久执行记录相同。
- PRE_EXISTING_FAILURE：任务随后停在 GitBroker 审查，最终状态 waiting_resource。正式图本次只执行一个 Coding Node，跨第二 Node 仅有 v5/v6 的双节点容器夹具证据。
- 最终测试：正式 CLI 1 passed、1 deselected；审批、来源和守卫定向回归 56 passed。专项定位过程中旧 A1/A2 图在来源缺失处失败，另一次真实续跑的导入结果保持 uncertain，未重放。Temporal 测试服务由每次调用的 finally 关闭。


### PR-A2 v10 提交前审查

- 正式 replay-continue 使用生产 CLI main 和命令处理器。offline_evolution_cli_harness 替换模型、ToolExecutor Policy、ProcessBroker 和 FileBroker 策略，真实 Temporal、SQLite、Docker 与恢复入口参与验收。生产默认策略和真实模型未验收。
- ContextBuilder 复制经当前守卫校验的待执行调用，避免接纳后的可变别名。隔离导入在 Broker 判定前显式检查当前 Task 和具体文件 Scope。
- PASS：83 项定向回归覆盖审批、恢复输入、Scope、执行记录和来源校验。正式 CLI 1 passed、1 deselected。Task 89afc110-147e-4612-a325-6405463aa4c1 使用唯一审批 00e51ad0-c097-482e-9421-40492560aed9，原 Agent 完成，Coding 容器启动 1 次，退出码 0，stdout 为空。再次续跑未增加执行记录。
- PRE_EXISTING_FAILURE：最终 GitBroker 审查在 A1 已复现。正式 CLI 图仅有一个 Coding Node，双节点复用证据来自独立真实 Docker 夹具。
- 补丁基线为 A1 e116657f6e9874f557afcb5d0cf50dbe53692630。完整 v10 包含 R04 衔接修复。拆分应用时先应用独立 R04，再应用 A2 增量。


### PR-A2 v11 CLI Git 副作用复核

- v10 的 Git 程序级任务授权可将 git push 送达被拦截的子进程入口。v11 将 Git 任务授权限制为固定的 --version 和 --exec-path。普通 push 和 commit 需要单独的精确审批。
- 已有强推拒绝规则对实际解析后的 git 可执行文件名匹配。测试对强推提供精确审批，Broker 仍拒绝。子进程全部拦截，仅观察到两次安全查询尝试。
- 最终 Git 定向回归 143 passed、1 skipped。跳过项需要符号链接。Python/Git 批量批准与拒绝继续通过。v10 正式 CLI 恢复结果沿用。


### PR-A2 v11 最终边界复核

- PASS。拦截式 Broker 测试覆盖 git -C <repo> push --force、git push origin branch --force 和末尾 -f。即使取得精确审批，默认 DENY 仍拒绝启动。实际 Git 写入和网络发布均为零。
- 普通 CLI 的程序级任务授权允许 Python 等获批程序在同一 Turn 使用其他参数，可能间接修改文件或启动 Git。该授权没有操作系统隔离，和隔离 Coding Task 授权分别处理。提示已说明这一范围。直接 Git 任务授权限于 --version 和 --exec-path。
- 最终定向结果 143 passed、1 skipped。跳过项需要符号链接。正式 CLI 恢复沿用 v10 已通过结果。GitBroker 图失败仍为 PRE_EXISTING_FAILURE。
- A2 功能冻结，等待提交审查。最终 v11 增量以完整 v10 工作树为基线。
