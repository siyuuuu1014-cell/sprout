# Sprout 当前架构、功能状态与优化路线

审查日期：2026-10-12。对象：`D:\dev\projects\sprout` 当前工作区，包括未提交的修复代码；不是仅审查某个已发布版本。项目声明版本为 0.1.0。

本文以实际装配和调用路径为准。旧梳理文档及 `Repair-Progress.md` 用于追溯，不作为当前功能完成的唯一依据。本文只做架构审查和优化建议，不代表已经实施其中的新建议。

## 1. 总体结论

Sprout 是一个本地优先、可嵌入的 Agent Runtime：把消息接入、模型调用、上下文、工具授权、项目分析、编码任务、验证、人工审批、变更应用和成长候选串起来。

当前最合适的定位是：**面向可信开发环境的受控 Agent 开发工具，已经具备本地 Python 编码任务的验收闭环，但仍处于需要操作者参与的 MVP 阶段。**

不是只有聊天的 Demo：项目事实版本化落库、任务模式约束、隔离工作树、具体验收用例、独立 PoC、审批与应用门禁等都已有实现。也不能称为完整生产级自动开发平台：多语言验收、真实隔离、远程编排一致性、完整项目本体、成长实验验证、统一控制台任务视图仍有缺口。

后续重点不是继续增加入口，而是统一已有链路的事实、状态和验收标准，再逐步扩大语言和部署范围。

## 2. 架构分层与模块连接

```text
用户 / 外部系统
  ├─ CLI
  ├─ Web API + React 控制台
  ├─ MCP Server；MCP Client 扩展外部工具
  └─ Gateway：SDK / RPC / 消息渠道 / 委派
             │ 身份、范围、预算、任务类型
             ▼
       create_runtime 工厂
             ▼
          Runtime
   ┌─────────┼────────────────┐
   │         │                │
在线对话   WorkspaceService   任务执行
   │         │                │
Session    Scanner          ExecutionGraph
Context    Intelligence     Local / Temporal
AgentLoop  Graph / Facts     NodeExecutor
   │         │                │
Model      ProjectAnalysis  Strategy / Acceptance
Tools      Store            Sandbox / Verification
   │                          │
   └──── Security + Brokers ──┘
                │
         StorageBundle / Rootstock
         权威数据 + 索引缓存 + 证据

Events / Trajectory ──► Evolution
                         │ 候选、评估、人工发布
                         └─► Skills / Knowledge
```

这是职责图，不是严格无反向依赖的证明。当前仍有核心引用入口域身份类型、Runtime 同时装配大量能力等耦合，见第 8 节。

### 2.1 所有一级功能模块

下表路径相对于项目根目录。每一行说明实现方式与主要连接，不能把目录存在等同于已完成部署验证。

| 模块 | 功能与实现方式 | 主要连接 / 边界 |
|---|---|---|
| `runtime/` | 工厂组装依赖；Runtime 协调消息、任务、队列、生命周期；nodes 分发执行节点，changes 管提案，workspaces 管分析服务 | 连接大多数能力和存储，是当前最大实现级枢纽 |
| `gateway/` | 将消息、SDK、RPC、委派等请求转为统一消息或任务；传递 Principal、DelegationScope、预算和显式模式 | 调用 Runtime；普通聊天与项目任务分流，不能靠模型关键词授予写权限 |
| `cli/` | Typer 命令、聊天、项目操作、审批、诊断与国际化 | 调用工厂及公共服务；chat 命令目前承载较多业务协调 |
| `mcp/` | Server 发布工具、资源和提示；Client 将外部 MCP 能力纳入工具体系 | 附加到 Runtime；不向模型暴露人工审批决策入口 |
| `web/`（包外） | Starlette API、WebSocket、认证、React 18 + Vite 控制台；WebDatabase 管 Web 侧数据 | 项目/审批调用 Runtime；手工任务看板有独立 CRUD，不等于执行任务 |
| `agent/` | AgentLoop 在步数预算内调用模型并执行动作；ActionExecutor 转交工具；Router 选择 Agent，默认 DirectPlanner | 依赖 llm、tools、context；默认装配一个 Agent，不是完整并行多 Agent 系统 |
| `llm/` | Provider 协议、Echo、OpenAI-compatible、aiyallm、模型注册、重试与回退 | Agent 消费统一模型接口；能力依赖实际模型及配置，Echo 不证明编码能力 |
| `context/` | 构造消息、工具、技能和工作区上下文；检索并合成模型输入 | 读取会话、memory、knowledge 和项目分析，不能成为新的事实权威 |
| `memory/` | 用户/会话事实、历史检索、预算估算、压缩、快照、Blob 和 outbox 索引投递 | 通过存储契约及会话事实构造上下文；派生索引可重建 |
| `session/` | 会话、Turn 模型及会话解析管理 | 依赖 SessionStore，而非直接绑定某个数据库 |
| `rootstock/` | SessionStore 协议及 SQLite、JSONL、内存等后端；外部后端/保留后端按配置延迟加载 | 会话持久化适配层；“有后端代码”不等于所有服务已实测 |
| `message/` | 入站/出站消息及中间件模型 | Gateway、Runtime、Session 共享消息契约 |
| `task/` | Task、状态、结果、预算、code/read_only 类型 | 接入、编排、执行和存储共享；历史未知模式保守阻止执行 |
| `orchestration/` | 编译 ExecutionGraph、顺序节点执行、节点恢复、预算重试；Local 与 Temporal 终端、worker 和 workflows | Runtime 提供节点处理；当前两个实现均以顺序执行为主 |
| `strategy/` | 编码策略、计划和执行模式、验收规格设计 | 为 nodes 提供计划和 AcceptanceSpec；计划不直接授予工具权限 |
| `workspace/` | 扫描和分类、ReadPlan、AST/静态图、事实提取、版本指纹、缓存、读取边界 | 供 WorkspaceService 使用；分析不能 import/执行目标项目源码 |
| `sandbox/` | Git worktree 创建、删除、终态清理和启动残留回收 | execution 在工作树内修改和验证；它是逻辑隔离，不是 OS 隔离 |
| `execution/` | 文件、进程、应用、Git、网络、数据库 brokers；冻结验收测试、Demo 生成和统一验证判定 | 将真实 I/O 与 policy/approval 接在一起；验收应发生在待应用工作树 |
| `security/` | 硬安全底线、分层策略、审批指纹/范围/TTL、命令分类、资源分类、Secrets、脱敏、网络保护和审计 | 工具风险门禁与 Broker 动作门禁交集，不应允许上层放宽底线 |
| `tools/` | ToolSpec/Registry/Result、ToolExecutor、系统与项目查询工具 | 模型调用先授权；节点工具覆盖采用独立 scoped overlay，严格上下文禁止全局回退 |
| `skills/` | TOML/SKILL.md 加载、索引、匹配、爬取、隔离安装、内容摘要和信任恢复、渐进披露 | approved 内容摘要匹配才可信；扫描到 skill 事实不等于批准执行 |
| `capability/` | 能力描述的数据模型 | 供能力/注册体系表达能力，不独立承担执行闭环 |
| `registry/` | 通用注册、路由、Prompt 与扩展目录 | 提供能力发现/选择，不应混入任务业务状态 |
| `config/` | Settings、默认值、严格配置加载、运行与部署模板 | 工厂读取统一设置；环境变量也影响 Temporal 等选择 |
| `storage/` | Protocol 契约、StorageBundle、后端工厂、SQLite/文件/内存实现及可选索引通道 | 权威先写，缓存/索引是派生；projects 是独立事实存储契约 |
| `events/` | EventBus、标准事件目录、middleware 和观察记录 | 执行发布事件，证据/成长消费；派生观察失败与安全审计失败不能混为一谈 |
| `trajectory/` | 轨迹、事件、快照、ReplayCase 的模型和 JSONL 记录 | 保存执行证据；不是任务状态或项目结构的第二权威 |
| `artifacts/` | Artifact/GrowthCandidate 类型与生命周期数据 | Evolution 使用，metadata 保存；不同 artifact 不应套用同一验证门槛 |
| `evolution/` | 轨迹收集、候选提炼、HardGate/Utility/Replay 评估及人工发布 | 入口 attach；Runtime 不反向 import evolution。当前评估尚不等于真实实验回放 |
| `scheduler/` | 本地间隔任务与 Temporal 定时适配 | 后台执行需遵守无人值守审批规则；取消/等待/关闭需要一致的生命周期契约 |

### 2.2 装配顺序

`runtime/factory.py::create_runtime` 的主要顺序：

1. 加载配置和 SQLite 参数，创建 StorageBundle。
2. 创建模型注册表和 EventBus，挂观察记录器。
3. 创建共享 SecurityLayer：policy、approval、command registry、Secrets、网络 guard、audit。
4. 创建工具与技能注册表，读取已批准技能的摘要快照。
5. 构造 Runtime、工作区服务、brokers、上下文和任务基础设施。
6. 注入 Local 或 Temporal 编排终端；默认 AgentLoop 使用 DirectPlanner 和 ToolExecutor。
7. 各入口按需 attach MCP clients 和 evolution，保持 core 不引用这些扩展包。

当前默认是 **Local 编排 + 进程内 FIFO**；设置 `TEMPORAL_HOST` 才选择 Temporal。这与 guidance 中“始终 Temporal”的部分旧描述不一致。

## 3. 核心业务流程怎么实现

### 3.1 在线对话

```text
Message → Runtime.handle → 中间件/身份与会话解析
        → AgentRouter → ContextBuilder → AgentLoop
        → ModelProvider → 工具授权/调用（如需要）
        → 会话持久化 / 大内容 Blob → OutboundMessage / Events
```

会话历史由 Rootstock/SessionStore 保存；memory 和 knowledge 为上下文提供材料；skills 默认仅注入摘要，完整说明按需读取。在线对话不是默认项目编码流程，不能因回复内容看似修改代码就视为已应用项目变更。

### 3.2 项目静态分析与落库

```text
注册 Workspace → 扫描资源与内容指纹 → 制定 ReadPlan
→ WorkspaceIntelligence：结构图 + 项目事实 + 技能事实
→ ProjectAnalysisStore：写各分区快照
→ CAS 发布 manifest 的 active_snapshot_id
→ 缓存当前版本 → 查询/上下文消费
```

本次之前已落实的修复思路：

- 项目事实不再只依赖运行时 knowledge：使用 projects 契约与五个 project 数据库分区。
- 分区写成功后才切换当前版本指针，失败不暴露半套新快照；CAS 防止并发旧结果覆盖新版本。
- 缓存按权威当前 snapshot 读取；源文件和外部 skill 支撑文件摘要参与失效判定。
- 结构、事实保留来源、位置、证据和未解析内容；不把推断当确定事实。
- Python 用 AST 提取局部 SQLAlchemy Core/ORM、FastAPI 路由；明确的本地导入与 include_router 前缀可组合。
- SQL 只提取 SOURCE 文件中的显式 CREATE TABLE 声明，不执行 SQL、不连接生产数据库、不声称已获得 live schema。

**当前覆盖范围：**资源清单、一定范围的结构/依赖图、声明式接口和数据库事实、skill 依赖等。动态注册、完整跨文件 ORM 继承、请求响应 schema、调用方、迁移实际状态、多语言语义分析和完整数据流仍不完备。

即使一条路径静态解析成功，也只代表源码声明的受支持子集，不证明部署后的 URL、代理配置、权限或响应符合预期。`complete=false` 与 unresolved 是诚实结果，不应为了“落库完整”填入猜测字段。

### 3.3 编码任务闭环

```text
显式 code 任务 + 用户已初始化 Git
  → READ → SANDBOX → PLAN → TEST_DESIGN
  → AGENT ↔ EVALUATION（预算内修复/重试）
  → APPROVAL → APPLY → 完成状态与工作树清理
```

每步分别由 ExecutionGraph 编译、NodeExecutor 处理、服务与 Broker 执行，metadata 保存节点状态供恢复。只读子任务可插入图中，但当前不是通用并行执行系统。

关键完成标准：

- TEST_DESIGN 冻结输入输出规格，再进入实现；不能用实现后的结果倒推期望值。
- 当前规格为 Python `module:function` 的 JSON 参数与期望输出，含 normal/boundary/invalid 类别。
- 生成具名 pytest 用例与独立最小 PoC；完成判定不仅看“有测试输出”，还要求指定用例和 PoC 的通过证据。
- 校验运行在产生变更的工作树，不在原始项目上拿旧代码测试结果充数。
- 无结果、部分验证、失败、被审批扣留/未执行等不能算 passed。
- 普通人工批准只是变更审查批准，不能代替验证；apply 和 completed 都有门禁。
- 用户保留的独立强制应用入口需记录豁免原因；强制落盘不等于验证完成。

当前限制：Python callable + JSON 比较不是 HTTP、浏览器、CLI、多语言、异步/复杂对象/异常断言的通用验收框架。即使模型自拟的用例通过，也不证明需求和期望本身正确；高风险需求还需人工确认或独立规范提供 oracle。

### 3.4 只读任务、审批与恢复

`read_only` 编译为 READ + 受限 AGENT，不创建写入工作树、不开放修改工具。ToolExecutor 的严格 scoped 模式不仅过滤展示，还拒绝模型直接调用全局未授权工具。

历史缺少有效模式的任务不推断写权限；仅无执行证据的 CREATED 任务可确认类型，已执行历史任务应重建。

审批按动作/参数指纹、task、scope、TTL 和一次性消费约束匹配。文件删除被拒绝后继续等待，用户重试或取消，不能自动删除或误标完成。CLI/Web 提供人工决策，MCP 不提供决策能力。

### 3.5 关闭与清理

Runtime 已补齐：请求队列消费者停止并等待 → 等后台写入 → flush outbox → 关闭 stores。显式停止标记防止执行适配器吞掉 CancelledError 后继续取下一任务。即使 worker 未经 Runtime.start 手动启动，也会被 join。

本地 worker 停止与业务 task cancel 是不同操作：关闭不应伪造 task cancelled，也不意味着远端 Temporal workflow 自动取消。sandbox 在 completed/failed/cancelled 时清理，在 running/waiting_approval 时保留，启动回收用于崩溃后兜底。

仍需完善：Web lifespan 中 Runtime.stop 抛错会阻止后续 WebDatabase.close；后台任务取消后的 join、关闭超时和多个资源关闭错误的报告，也应统一。

### 3.6 成长候选流程

```text
Trajectory → 收集信号 / 生成候选 → Replay/HardGate/Utility
           → VALIDATED → 人工批准 → 发布 Skill / Workflow / Knowledge
```

这是现有功能结构，**不是每条路径都有真实沙箱 replay 的证明**：

- ReplayEvaluator 当前是证据匹配、失败轨迹、内容量等规则评分。
- ReplayRunner 调用已有 task 的 Runtime.execute；它本身未建立独立基线/实验任务和 A/B 比较。
- CandidateManager.evaluate_all 调用 evaluator 时没有传测试/回归 checks；HardGate 仅显式 False 阻断，未知测量不会阻断。
- WORKFLOW artifact 发布为可注入的技能说明，不是生成并部署新的 Temporal workflow。
- 人工批准仍是信任决定，不能把候选 VALIDATED 解释为“实际回放和回归已通过”。

## 4. 存储关系：哪些是权威，哪些可重建

| 数据类别 | 当前职责 / 实现 | 应保持的规则 |
|---|---|---|
| Runtime 控制数据 | operational / metadata：任务、节点、运行、变更、Artifact、工作区桥接等 | 业务状态只有一个权威；项目完整事实不应复制到这里 |
| 会话 | Rootstock SessionStore；SQLite 对话持久化及 outbox | 对话写入与待索引事件事务一致 |
| Runtime 知识 | KnowledgeStore：运行经验及检索 | 与用户项目结构权威区分，跨域用明确引用 |
| 项目 manifest | `project_manifest.db`：Project、AnalysisManifest、当前快照指针 | 最后发布当前指针，避免部分分区成为当前版本 |
| 项目结构 | `project_structure.db`：WorktreeSnapshot、StructureNode、CodeRelation | snapshot 隔离；资源和关系保留确定身份与证据 |
| 项目架构 | `project_architecture.db`：ArchitectureFact | 当前有结构化 details/JSON，并非已实现规划中的全部独立本体表 |
| 项目数据库 | `project_database.db`：DatabaseFact | 源码声明与 live schema/迁移状态区分 |
| 项目演进 | `project_evolution.db`：ProjectVersion | 当前主要支持分析版本记录，不等于完整项目迁移/发布管理 |
| Skills | Store 中批准记录 + 磁盘 artifact + 摘要快照 | 磁盘存在与被批准分别校验；内容改变撤销信任 |
| 证据 | Trajectory JSONL、Blob、observation、安全审计 | 证据不能反向覆盖 task/项目事实权威 |
| 派生能力 | 本地缓存、FTS、vectors、可选 Redis/Milvus/Neo4j 等 | 可以退化、可重建；不能把连接成功等同于完整项目图已经同步 |

五库不是“五个独立服务”。当前是一个 Python 进程可使用的本地存储分区；快照发布协议提供一致可见性，不是跨数据库分布式事务。旧版本/失败分区和派生索引仍需明确保留与回收策略。

## 5. 模块耦合：合理连接与需要收敛的连接

### 5.1 合理的连接

- 入口通过 Runtime/公共服务，而不是各自重新实现编码验收。
- workspace 专注非执行式分析；storage 专注持久化；execution/security 控制副作用。
- Rootstock 为会话提供契约，避免 SessionManager 绑定某种数据库。
- evolution 和 MCP 在入口 attach，Runtime 无需直接 import 扩展包。
- node-scoped 工具通过独立覆盖层隔离，不全局注册/撤销，避免并发串工具。

### 5.2 当前耦合热点

本工作区静态 AST 统计：`src/Sprout` 非 tests Python 约 359 文件、60,096 行；29 个一级包。包含空行和注释，不含 Web 前端，不是复杂度或性能指标。

统计绝对 `Sprout.*` 导入，包含类型检查与函数内导入；不计相对导入。下表数字是引用该包的外部源文件数，不能解释为真实运行调用次数。

| 被引用包 | 外部文件数 | 说明 |
|---|---:|---|
| storage | 46 | 契约枢纽，需避免调用者依赖具体实现 |
| task | 38 | 跨入口统一业务模型，合理但变更影响面大 |
| config | 35 | 需控制 Settings 向所有业务细节扩散 |
| runtime | 30 | 入口依赖正常；能力层不宜依赖 Runtime 私有字段 |
| workspace | 29 | 共享资源模型与分析服务，应区分模型和实现依赖 |
| security | 28 | 共享安全边界，避免各路径另造授权逻辑 |

实现热点：`runtime/runtime.py` 约 2,786 行，`runtime/nodes.py` 约 1,220 行，`cli/commands/chat.py` 约 2,485 行，`tools/system_tools.py` 约 1,069 行。

Runtime 不只是薄协调器：它仍装配工具、brokers、上下文、工作区、任务、生命周期等。它还引用 `gateway.identity.Principal`，出现 core 依赖入口域模型的层级反向。工厂与构造器分担装配，重复注册工作区工具的所有权也需收敛。

建议保留模块化单体，不立即拆微服务：先明确服务契约和状态权威，才能避免把当前进程内耦合变成网络耦合。

## 6. 当前功能达到什么程度

| 能力 | 当前判断 | 尚不能据此宣称 |
|---|---|---|
| 在线对话与工具调用 | 已形成可运行基础链路 | 任意模型/工具组合均稳定 |
| 项目静态理解 | 版本化落库与部分精确事实提取已实现 | 全语言、完整 schema、所有调用关系和真实部署图 |
| 本地 Python 编码 | 受控实现、具名测试、PoC、审批、应用闭环已实现 | 无人干预地完成任意复杂业务 |
| 非 Git 项目 | 可显式只读分析 | 未经用户操作自动 Git 初始化并编码 |
| 只读与历史任务 | 显式模式与保守处理已实现 | 所有历史任务都可无损自动迁移 |
| 权限与审批 | 分层策略、作用域、严格工具限制、审批与审计已有实现 | 工作树是可信安全沙箱；所有系统工具副作用都已统一 broker 化 |
| 多入口 | CLI/MCP/Web/SDK/RPC/IM 有实现与本地测试 | 每种真实渠道、凭证、部署环境均完成联调 |
| Temporal | 有终端、worker、workflow/activity 接线 | 提交/恢复/重试与本地完全一致，故障下无重复执行 |
| 多 Agent | 有 Router 与子任务表达 | 通用并行协作、冲突合并和多 Agent 调度已完成 |
| 长期记忆 | 有会话事实、检索、摘要/预算与索引基础 | 所有配置都能获得高质量语义记忆 |
| 自成长 | 能收集、评估、审批并发布候选 | 独立实验验证、自动防回归和安全自动进化 |
| 工程化 | 有大量回归、存储契约、故障与生命周期测试 | 干净环境默认测试全绿、前后端持续集成和生产 SLO 已完善 |

## 7. 验证依据与边界

本次审查包含主要装配路径、在线/任务执行、项目分析与存储、安全工具、成长发布、Web 任务 API 和工程配置的源码核对。不是人工逐行形式化证明全部代码。

回归命令：

```powershell
uv run pytest -q -p no:cacheprovider --basetemp=.workbuddy/test-runs/architecture-review-20261012 --ignore=src/Sprout/tests/test_bubble_sort.py --ignore=src/Sprout/tests/test_hello_world.py
```

本次测试结果：**1324 passed，7 skipped，2 warnings，耗时 105.59 秒**。警告来自 Starlette/AnyIO 与飞书 SDK 的弃用 API；测试结果只覆盖本次上述命令选中的范围。

必须保留的边界：

- 两个排除测试依赖根目录 `bubble_sort.py`、`hello_world.py`，当前文件不存在。不能把有排除项的回归表述成默认 `uv run pytest` 已全绿。
- Redis/Milvus/Neo4j 的真实服务测试依赖显式环境变量；跳过不是通过。
- 本次未启用真实 Temporal、外部消息渠道、付费模型和生产数据库，未做负载/渗透/灾难恢复测试。
- 前端 package.json 当前只有 dev/build/preview，没有独立 test 脚本；本次没有据此声称浏览器端 E2E 已覆盖。

## 8. 当前问题及后续优化优先级

优先级定义：P0 是进入真实无人值守/外部暴露前的阻断项，不表示当前本地环境已发现安全事故；P1 是闭环一致性与可发布性；P2 是范围和精度扩展；P3 是规模化。

| 优先级 | 问题及代码依据 | 处理方式 | 验收标准 |
|---|---|---|---|
| P0（外部运行前） | worktree 与宿主共享用户、文件系统、网络和内核 | sandbox 增加可替换进程/容器后端，限挂载、Secrets、出网、资源和超时；不只靠命令名白名单 | 生成代码不能读取宿主秘密/越权写入/任意出网；超时和取消可回收进程树 |
| P1 | TemporalTaskQueue.put 先本地入队，再远端 connect/start；远端失败时本地任务可能仍执行 | 明确唯一提交权威；持久化提交记录/outbox、幂等键、已提交确认与重试；避免本地/远端双调度 | 注入 connect/start 超时、进程崩溃和重复提交，无模糊“失败但执行”、无重复副作用 |
| P1 | SproutTaskWorkflow 当前只返回 queued；真正执行是 SproutExecutionWorkflow，两个层次语义容易混淆 | 明确 admission 与 execution 状态映射；Local/Temporal 共用验收与恢复契约测试 | 同一测试场景两后端的 waiting/failed/completed、节点恢复和批准后行为一致 |
| P1 | CandidateManager.evaluate_all 未传实际 checks；HardGate 对 unknown 不阻断；ReplayEvaluator 是启发式评分 | 区分 content-reviewed 与 execution-verified；按 artifact 类型要求实际测试/回归证据，缺测量不能标验证通过 | 无证据/失败/部分验证无法发布可执行技能；基线与实验都有隔离运行结果 |
| P1 | approve 先保存 PUBLISHED 再实际发布，发布失败可能留下已发布状态 | 引入 publishing/outbox 或可重试发布记录，幂等写技能/知识，成功后确认 PUBLISHED | 任一发布步骤失败均能恢复，不留下状态成功但消费者不可用的候选 |
| P1 | PROJECT_KNOWLEDGE 发布写 runtime.storage.knowledge，缺项目分区引用；scan 仍保存完整 manifest 到 metadata | 项目内容走项目权威；Runtime 只保留桥接引用/兼容读取，制定迁移而非直接删除 | 多项目检索不串数据；桥接字段可定位项目事实；只剩一套权威 |
| P1 | Web `/api/tasks` 是 todo/doing/done 手工 CRUD，不调用 Runtime | 明确标识手工 BoardItem 与 ExecutionTask；新增执行任务查询/状态投影，禁止手工 done 冒充完成 | UI 能看实际节点、验收、审批、豁免和结果，状态来自权威 |
| P1 | GitWriteTool 只有 add/commit/pull 走注入 GitBroker，fetch/push 等仍走直接 subprocess；工具支持无 broker 回退 | 逐动作核对并迁移到 broker；正式装配 fail-closed，测试替身显式声明 | 每种特权操作都有动作授权和审计；单测移除门禁会失败 |
| P1 | Web lifespan 的 Runtime.stop 异常会跳过 database.close；本地 Scheduler.stop 取消后未 join | 所有后台所有者统一 stop/join/flush/close；finally 清理，配置有界等待并报告错误 | 故障注入下无活跃任务访问已关闭 store、无挂起或连接泄漏 |
| P1 | 默认测试的两个示例依赖缺失；requirements 锁定 aiyallm 0.1.1，与 pyproject >=0.1.3 不一致；文档有陈旧装配描述 | 分离例子与核心测试；统一 uv.lock 和导出流程；建立最小 CI 与架构文档校验 | 干净环境默认命令全绿；锁/导出一致；实际工厂与文档同一口径 |
| P2 | Runtime 与 CLI chat 过大；core 引用 gateway 身份；工厂/构造器职责交叠 | 将任务生命周期、工具装配、上下文组装等按职责收敛；身份移至中立契约层；入口调用公共 API | 保持外部 API 与测试行为，禁止反向依赖和私有字段访问的检查可自动执行 |
| P2 | HTTP 请求响应 schema、调用方、跨模块 ORM、SQL 方言/迁移分析不完整 | 逐类增加独立 AST/声明分析器，保留未知、来源、置信度；明确事实类型版本 | 正例/反例/动态未知夹具，保存-加载无信息丢失；禁止执行目标代码 |
| P2 | 验收仅 Python callable；模型自己拟期望可能偏离需求 | 引入 VerificationAdapter，先增加 CLI/HTTP，再前端与其他语言；需求映射、人工/独立 oracle | 每个需求至少有独立可复现测试；通用门禁不被新适配器绕过 |
| P2 | 快照旧版本/失败分区、内存缓存和每工作区锁缺少统一生命周期 | GC 保留策略、LRU/容量限制、增量扫描、snapshot 一致读与竞态测试 | 大项目连续变更不无限增长，GC 不删活动版本，缓存不污染版本 |
| P3 | 并行子任务、生产可观测性与多用户隔离尚不能完整证明 | 闭环稳定后再加依赖调度、预算共享、trace/metrics、访问隔离与容量规划 | 重复执行/冲突/队列压力可测；项目隔离测试、SLO 和恢复演练达标 |

以上 Git 路径仍有外层 ToolExecutor 风险门禁，不能直接断言“完全无授权”；问题在于尚未实现所有副作用统一经过动作级 broker。网络与数据库 broker **当前已装配**，不能继续沿用旧文档“未接线”的结论。

## 9. 建议的落地顺序

### 第一阶段：建立可以信任的交付基线

先处理默认回归与依赖/文档漂移，保证别人能复现；再处理 Temporal 提交幂等、Web 关闭兜底、系统工具动作边界和成长发布状态一致性。保留现有 code/read_only、审批、验证门禁，不降低标准让测试“通过”。

交付物：干净环境的一条验证命令；Local/Temporal 共用契约测试；提交与发布故障注入；受控 Python PoC 全链路样例。

### 第二阶段：统一项目事实与控制台视图

纠正成长项目知识写入路径，Runtime workspace 只桥接；控制台明确区分手工看板和真正执行任务。增加项目分析质量指标：资源覆盖、事实类型覆盖、unresolved、证据缺失和解析警告，不能用“数据库有多少行”代替准确率。

交付物：项目权威迁移方案、跨项目隔离测试、任务节点/验证/审批可视化、可重复分析样例。

### 第三阶段：扩展精确分析和验收适配器

先补 API 请求/响应 schema、调用方和跨文件 ORM 的高价值静态子集；验收先扩 CLI/HTTP，再决定前端与其他语言。每次扩展都添加明确支持边界、持久化 round-trip 与未知情形测试。

交付物：每类分析的支持矩阵、带证据的事实查询；同一任务完成门禁下的多适配器测试。

### 第四阶段：安全隔离和工程规模化

准备外部用户或无人值守执行时，真实隔离升级为前置要求，而不是最后补丁；受控本地使用可先完成前述阶段。随后再优化大项目增量分析、快照 GC、Runtime 拆分、并行子任务和观测指标。

暂不建议：立即拆微服务、替换全部数据库、放宽验证以支持更多任务、自动批准成长候选。这些会扩大当前未闭合的一致性问题。

## 10. 下一轮改动前需要用户决定的事项

- 部署目标：仅可信本地开发，还是多人/外部/无人值守？这决定真实隔离的实施顺序。
- 验收范围：下一种优先适配 CLI、HTTP API，还是前端页面？不要同时铺开全部语言。
- Web 手工看板是否保留？建议保留但明确命名并链接执行任务，不直接混用状态。
- 历史项目知识迁移、分析快照保留期限及数据清理权限，需先确认再移动/删除数据。
- 成长发布验证强度需按 artifact 类型定义；已有人工强制应用豁免不能自动扩展为成长免验证发布授权。

已有用户决定继续沿用：独立强制应用保留原因但不算验证完成；删除拒绝保持等待；显式任务类型、历史任务保守处理。

## 11. 核对代码入口

以下为本机可点击的代表性实现位置；路径映射随项目移动需更新。

- [Runtime 工厂](D:/dev/projects/sprout/src/Sprout/runtime/factory.py)、[Runtime](D:/dev/projects/sprout/src/Sprout/runtime/runtime.py)、[节点执行](D:/dev/projects/sprout/src/Sprout/runtime/nodes.py)、[变更管理](D:/dev/projects/sprout/src/Sprout/runtime/changes.py)。
- [工作区服务](D:/dev/projects/sprout/src/Sprout/runtime/workspaces.py)、[分析组合](D:/dev/projects/sprout/src/Sprout/workspace/intelligence.py)、[项目存储](D:/dev/projects/sprout/src/Sprout/storage/local/sqlite/projects.py)。
- [验收规格](D:/dev/projects/sprout/src/Sprout/strategy/acceptance.py)、[测试和 Demo 生成](D:/dev/projects/sprout/src/Sprout/execution/acceptance.py)、[完成门禁](D:/dev/projects/sprout/src/Sprout/execution/verification.py)。
- [队列](D:/dev/projects/sprout/src/Sprout/runtime/queue.py)、[Local 终端](D:/dev/projects/sprout/src/Sprout/orchestration/terminal/local.py)、[Temporal workflows](D:/dev/projects/sprout/src/Sprout/orchestration/temporal_workflows.py)。
- [工具授权](D:/dev/projects/sprout/src/Sprout/tools/executor.py)、[系统工具](D:/dev/projects/sprout/src/Sprout/tools/system_tools.py)、[候选管理](D:/dev/projects/sprout/src/Sprout/evolution/candidate_manager.py)、[成长硬门禁](D:/dev/projects/sprout/src/Sprout/evolution/hard_gate.py)。
- [Web 应用](D:/dev/projects/sprout/web/webapi/app.py)、[手工看板 API](D:/dev/projects/sprout/web/webapi/routes/tasks.py)、[前端工程配置](D:/dev/projects/sprout/web/frontend/package.json)。

最终原则：**分析事实可以不完整，但不能伪精确；变更可以人工豁免落盘，但不能伪验证；模块可以共享契约，但不能共享不明确的状态权威。**
