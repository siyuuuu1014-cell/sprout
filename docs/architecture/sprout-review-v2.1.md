文档类型：基于上传的《SEAM Sprout 技术架构与实施总方案 v1.0》及 Sprout dev 分支源码形成的审计增补。保留 v1 总体战略，修订实施顺序、风险优先级和验收合同。2026-10-16 本轮补充核查了 AgentLoop 并发/流式路径、Skills 信任过滤、工具调用计数及 Gateway 任务投影。

审查时间：2026-10-16 代码基线：dev @ 762eaf3c62476d2377302329151a759c3d297baa

审查范围：当前 Commit 762eaf3c62476d2377302329151a759c3d297baa 的 844 个 Git blob/911 条 Git 树条目；核查关键源码、配置、测试以及 v1 22 章与开源官方设计资料。检查文件树不等于全仓逐行审计。

证据边界：本轮通过 GitHub 连接开展静态代码审查；未将仓库完整克隆至执行环境，也未实际运行全部 pytest、Docker、Temporal 或付费模型。不能把文件树审阅称为全文件逐行验证。

总论：保持模块化单体、无模型训练、单 Agent 优先的目标；改变风险消除顺序。优先处理执行权限、输入信任、路径范围与证据一致性，再优化 RepoMap、模型路由和多语言。

# 1. 最终审查结论与证据等级

| 判定         | 结论                                                                                              | 处理方式                                                 |
|--------------|---------------------------------------------------------------------------------------------------|----------------------------------------------------------|
| 成立         | v1 的模块化单体、保留 Runtime/Local/Temporal、独立验收、冻结 Oracle、人工审批、无训练路线总体正确 | 保留目标，不重写为外部高层框架                           |
| 需要修改     | v1 将动作门禁统一置于 PR-15 / P3，晚于编辑、模型及检索增强；风险排序不合适                        | 将可绕行执行器、路径安全和 Metadata 来源提高至 P0        |
| 存在接口冲突 | 已有 security.access.ActionRequest，v1 第9.2节拟议同名结构不兼容                                  | 扩展已有安全对象与适配层，不平行新增第二个 ActionRequest |
| 欠缺实施协议 | Effect Ledger、CAS Apply、Oracle provenance、模型预算、Snapshot 发布主要停留在概念层              | 新增字段、状态机、恢复器、迁移规则与拒绝测试             |
| 需要实测     | Docker、Temporal、跨入口、真实模型任务和 Benchmark 未在本轮环境实跑                               | 区分静态代码确认与动态行为证明                           |

## 1.1 证据使用规则

A：已经核查到对应代码路径与关键条件；B：仅由现有测试/设计材料说明的功能；C：上游开源项目的机制参考；D：建议新增的设计。本文的 P0/P1 表示修复优先级，不代表已完成攻击复现。

## 1.2 源码基线与实施条件

- GitHub 仓库默认分支为 dev；v1 引用的 Commit 与当前 dev 一致。844 是文件（blob）数量，不是含目录的 Git 树条目数。

- Python 包版本为 0.1.0（pyproject.toml），Python 要求 \>=3.12；uv.lock 已存在。需要建立锁文件一致性和自动 CI，而非声称没有锁文件。

- 仓库已有大量测试用例；部分真实 Docker 测试依赖 SPROUT_CODING_TEST_IMAGE 且默认跳过。已有测试文件 ≠ CI 已运行 ≠ 隔离证据已被确认。

- 当前代码同时支持可信宿主工作树模式与特殊受限 Docker 编码路径。工作树隔离不等于 OS 级隔离。

# 2. 源码确认的问题清单（按风险排序）

| 编号/级别 | 问题                            | 源码证据与原因                                                                                                                                                                    | 必要的落地修订                                                                                   |
|-----------|---------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| F01 / P0  | 消息 Metadata 越界参与意图      | runtime/runtime.py:1315–1326。\_classify_intent_with_llm() 优先采用 message.metadata.intent/intent_name；该函数内未验证字段是否由受信任入口写入。                                 | 仅消费 Gateway 注入的服务端 TrustedIntent；普通 Metadata 当非授权提示；拒绝测试覆盖伪造 Intent。 |
| F02 / P0  | 否定/引用仍可触发 task_hint     | intents.py:86–111。detect_task_hint() 通过关键字子串判断；“不要修复登录问题”包含“修复”，会命中。                                                                                  | 在关键词快速通道前判 SpeechAct/否定/引用/状态；不确定回退到问询或只读，不触发写任务。            |
| F03 / P0  | 默认系统工具绕过 Broker         | tools/registry.py:170–204；tools/system_tools.py:409–459。create_tool_registry() 未注入 ProcessBroker/NetworkBroker/GitBroker；CliRunTool 无 Broker 则直接执行 \_run_command。    | 定义宿主 trusted_local 明确模式；非可信/任务工具缺 Broker 一律 denied；按操作审计、双层 Policy。 |
| F04 / P0  | Git 系统工具动作边界不一致      | tools/system_tools.py:848–1007。GitWriteTool 的部分操作经 GitBroker，其余 push/fetch/checkout/clone 等走 \_run_git()。                                                            | 所有 Git 写操作转 GitActionBroker；Git Hook 与凭证、目标 ref、remote、HEAD 指纹显式受控。        |
| F05 / P0  | 委派路径授权使用字符串前缀      | task/models.py:115–131。"/repo" 可作为 "/repo-backup" 字符串前缀；直接 startswith 无法保证目录边界。                                                                              | 使用规范化路径与 Path.relative_to/dirfd 防 symlink 竞态；在实际打开资源时再次授权。              |
| F06 / P1  | Apply 与验收版本绑定仍不足      | execution/apply.py:76–145。常规 Apply 路径进行 git apply --check 和 git commit；未在该入口证明 HEAD、批准 Patch、验收 Patch/工作树保持同一指纹。                                  | 在应用事务前统一比较 SourceCommit、PatchHash、OracleHash、ApprovalFingerprint 与工作树洁净状态。 |
| F07 / P1  | Node WAITING 不能直接等于可恢复 | orchestration/execution_guard.py:22–117。未知结果被保守标记 WAITING/uncertain；避免重复是优点，但无按效果种类的自动核对就可能永久停滞。                                           | 每种效果绑定 Reconciler；记录 UNKNOWN_NO_RETRY；允许授权人工 resolve，并保留审计。               |
| F08 / P1  | MCP 外部工具风险级别粗糙        | mcp/adapter/tool.py:30–51；mcp/client/connection.py。远程工具的 ToolSpec 默认 risk_level=medium；MCP stdio 启动和远端副作用不自动满足本地 Action Broker 语义。                    | 按 server/operation 配置上限风险、可信来源、预授权动作域；高危默认拒绝而非靠工具描述分类。       |
| F09 / P1  | Web 生命周期清理可被中断        | web/webapi/app.py:102–113。finally 中依次 await runtime.stop()、on_shutdown()、database.close()；前项异常使后项跳过。                                                             | 逐资源隔离 try/finally，收集关闭错误；确认 DB 总会 close、可恢复任务状态被写入。                 |
| F10 / P1  | 宿主工作树执行不具安全隔离      | sandbox/git_worktree.py:160–199。run_code() 在宿主运行目标代码，已做 Secret 环境白名单和超时，但不是容器安全边界。                                                                | restricted 模式完全禁用该路径；trusted_local 明示风险，强制用途审计及人工授权。                  |
| F11 / P1  | CI 基线不完整                   | 仓库树；test_coding_graph_docker_integration.py。代码含 opt-in Docker 集成测试；本轮树中未见 .github/workflows；不能验证自动门禁是否执行。                                        | 建 L0/L1 常态门禁和 L2/L3 专用 Runner；必要环境缺失必须为 not_run，不得记 passed。               |
| F12 / P2  | LLM 能力缺显式注册契约          | llm/registry.py:1–26。ModelRegistry 当前只完成 provider 名称/默认选择，没有 v1 的 ModelCapabilities。                                                                             | Provider 特性探测/静态申明双路；缺能力 fail-fast，动态结果可追踪。                               |
| F13 / P2  | 项目检索尚未形成完整混合召回    | context/retrieval.py:1–27；context/builder.py。当前 KnowledgeRetriever 是关键词知识检索，WorkspaceContext 有图关系注入，但无统一 CodeHit + FTS/RRF 排序合同。                     | 先落地精确符号+FTS，再图扩展与 RepoMap；向量选配，离线 Benchmark 评估。                          |
| F14 / P2  | TaskStatus 文档与源码不一致     | task/models.py:14–33；v1 第12章。实际状态包含 DISCOVERING 和 READY，而 v1 枚举文字漏写这两项。                                                                                    | 保留原枚举，对文档、事件投影、迁移脚本补完整状态矩阵。                                           |
| F15 / P2  | 验收真实集成覆盖容易被高估      | test_coding_docker_integration.py；test_coding_graph_docker_integration.py。测试明确描述为 opt-in、scripted fixture、并非完整 Runtime/Temporal 排程；测试设计合理但覆盖边界有限。 | 新增真实入口→任务→隔离→冻结验收→审批→Apply 的独立端到端门禁。                                    |

# 3. v1 文档中的直接可执行性冲突

## 3.1 第 9.2 节：ActionRequest 同名冲突

问题：已有 Sprout.security.access.ActionRequest，具备 actor、action、resource、arguments、scope、idempotency_key；新 dataclass 字段和调用习惯不同。

修正：不要创建新 ActionRequest；将 action_id/trace/run/node、环境、effect_kind、policy_version 以可兼容可选字段逐步并入，或引入 ActionContext/ActionEnvelope，避免循环引用。

## 3.2 第 9.2 节：统一 ActionResult 语义过早

问题：现有 ProcessResult/FileResult/GitResult/TestResult 用于不同路径；如直接替换会破坏已有序列化、审批和状态判断。

修正：保留领域 Result；定义 ActionOutcome 只用于观测/EffectLedger 映射，提供 from_process/from_git 适配和未知映射。

## 3.3 第 5 节：Intent→RouteDecision 缺输入信任合同

问题：Goal/SpeechAct 不能证明用户有权限；现有 messages 的 metadata 已在代码中参与 task 判定。

修正：RouteDecision 至少包含 trust_source、principal_ref、target_task_id、requires_confirmation、permission_mode、reason_codes；必须由服务端确认。

## 3.4 第 12 节：EffectLedger 缺 Reconciler 操作标准

问题：“记录 Intent 然后恢复”不足以判定外部动作是否已执行；写 Git、MCP 调用、数据库事务、进程运行可观测性不同。

修正：每种副作用列出可核对凭证、幂等键、查询 API、重试策略、超时后处理和手工干预；不可核对的一律停在 UNKNOWN。

## 3.5 第 11 节：VerificationAdapter 缺准入规则

问题：supports+prepare+execute 尚不足以证明所有必选检查执行；参数也缺 baseline、patch、隔离环境的版本绑定。

修正：统一 VerificationPlan：required checks / oracle provenance / source_commit / patch_hash / image_digest / policy_scope / max_tries；报告必须区分 skipped/not_run。

## 3.6 第 7 节：5 个项目数据库的跨库原子性未定义

问题：五分区落库与 Manifest CAS 是思路，但跨 SQLite 库不自动获得单一事务。

修正：采用 staged snapshot + 每分区 committed manifest + 同一权威元数据表单指针 CAS；未发布分区可清理，读者拒绝混版。

## 3.7 第 18 节：PR 依赖顺序与风险不匹配

问题：P2 编辑/验收出现在 P3 Action Inventory、Docker 安全门禁之前，形成默认可用性误导。

修正：先保证有风险的所有操作 fail-closed，再推进编辑和功能增强；Docker 发布门禁前置。

## 3.8 第 17 节：Bench 缺对照实验统计细节

问题：提出 4 个 Bench 和阶段 CI，但未指定重跑次数、固定模型配置、判分归因和缺失数据规则。

修正：按固定 Git commit、Oracle、模型快照/参数、环境摘要重复测量；报告成功率、方差、Token/费用、拒绝误差、置信区间。

## 3.9 第 14–15 节：MCP/Skills 外部信任与 Prompt Injection 边界不足

问题：文档强调安装≠信任，但未明确工具输出、依赖文档和仓库内指令不可自动提升权限。

修正：外部内容统一视为 DataSource，标来源/可信等级，禁止作为更高优先级系统指令；远程工具需 server 身份绑定、能力注册与审计。

## 3.10 第 16 节：Trusted Local 定义不够具体

问题：“默认本地开发者”与“真实隔离编码推荐”容易让产品误展示为同等安全。

修正：配置枚举 explicit trusted_local/restricted_coding；界面提示隔离等级。外部渠道/无人值守默认不准落入 trusted_local。

# 4. 原文档还应补充的功能与工程合同

| 遗漏合同                 | 应补设计与验收                                                                                                                          |
|--------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|
| 需求与验收独立性         | AcceptanceSpec 指明来源：用户给定、人工批准、基准独立 Oracle、模型建议；缺规格时需提出问题，不得把模型自拟断言当完整验收。              |
| 变更快照与审批失效       | PatchHash、SourceCommit、ChangedFilesManifest、AcceptanceHash、ApprovalActionHash 五者绑定；任何新编辑/测试变化必须重新验证或重新批准。 |
| 代码上下文与仓库指令污染 | 检索结果与 AGENTS.md、README、代码注释、MCP 返回均为可能包含诱导指令的数据；引用内容不得改变 Security/Tool Scope。                      |
| 跨平台 Git 风险          | Windows/Unix 路径、Symlink/hardlink、submodule、LFS、大文件、重命名、行尾格式、Git Hook、dirty index、并发用户写入均需专门验收。        |
| 并发与进程组取消         | 超时后清理子孙进程、Docker 僵尸容器、Temporal Worker 取消、模型流取消、观察事件恢复；需要独立状态采样和资源归还。                       |
| 模型适配与错误分类       | Provider 能力矩阵、限流/backoff、结构化输出校验、token/费用算账、失败前/后落账、未知响应与 non-idempotent 工具调用拆分。                |
| 安全数据生命周期         | Secret 不落普通 Trace；Blob/审核记录/代码快照独立 TTL 与合法保留；备份与 schema migration、恢复演练、用户删除闭环。                     |
| 多语言支持判定           | TS/Node、Python、Go、Rust 的 Discovery/Build/Test/Schema/Formatting 分别有可用性等级；不做一项支持等于“项目全语言支持”。                |
| 远端 MCP 行为合同        | 能力发现时快照工具 schema 与 server 可信身份；强制操作白名单、超时、授权身份；服务器返回的文本永不成为系统指令。                        |
| Web/API 契约             | 手工看板与真实 ExecutionTask 分开；采用 EventCursor/TaskVersion 单调序列，授权的 retry/cancel/approve 先做服务端版本检查。              |
| 可回滚迁移               | Schema version、自动备份、迁移前验证、不可逆变更门禁、老客户端兼容期限；历史审批不可因迁移扩大 Scope。                                  |
| 功能性可靠度分级         | 每个模块标 implemented、connected、tested_local、tested_docker、tested_temporal、production_validated，避免实现文件存在即声称完成。     |

# 5. 目标架构的推荐最小改动路径

> CLI / Web / MCP / SDK / Channels  
> -\> Authenticated Gateway (Principal + TrustedMetadata)  
> -\> IntentUnderstanding (非授权) -\> RouteDecision (服务器确定性)  
> -\> TaskService / ExecutionGraph -\> NodeExecutionGuard  
> -\> Scoped AgentLoop / ModelCapabilities / ContextPack  
> -\> ToolExecutor -\> Security ActionRequest -\> Broker / ExecutionEnvironment  
> -\> Effect Ledger + Reconciler -\> Oracle Verification -\> Proposal Approval  
> -\> CAS Git Apply -\> Task / Run / Node / Evidence / Usage  
> -\> Evaluation + Human-reviewed Evolution

设计纪律：只保留一个核心 AgentLoop、一个 Task 状态权威、一个审批存储、一个安全 ActionRequest。Context/FTS/RepoMap/Events 属于可重建派生或可审计投影，不应变成第二套业务权威。

## 5.1 可最小实现的新增合同字段

| 实体（保留原类型）     | 新增/约束字段                                                                                        | 核心不变式                                                              |
|------------------------|------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| IntentDecision         | message_id、principal_ref、trust_source、speech_act、goal、target_task_id、route_kind、reason_codes  | 模型输出不含 authorized；审批/工作区选择必须匹配唯一有效 pending 记录。 |
| security.ActionRequest | trace_id、run_id、node_id、environment_id、effect_kind、canonical_resource、policy_version（可选）   | action/idempotency_key 在授权与执行前固定；Scope 只可能缩小。           |
| EffectRecord           | effect_id、action_hash、external_ref、claim_owner、started_state、result_state、reconciler_kind      | UNKNOWN 不自动重试；CONFIRMED 必有外部或可信本地证据。                  |
| AcceptanceSpec         | source_commit、spec_version、oracle_provenance、spec_hash、required_checks                           | 模型自拟辅助检查无法代替独立必须项。                                    |
| VerificationReport     | patch_hash、image_digest、required/executed/skipped/not_run、evidence_ref、blocker                   | 一个必选检查未执行→不能 reported passed。                               |
| ChangeProposal         | base_head、patch_sha256、changed_file_hashes、verified_report_hash、approval_hash                    | 编辑、审批、验证、Apply 严格绑定相同候选。                              |
| ProjectSnapshot        | project_id、source_commit、manifest_hash、parent_snapshot、staging_state、published_version          | 所有分区从同一已发布 Snapshot 读取，活动任务引用阻止 GC。               |
| ModelCapabilities      | native_tools、json_schema、streaming、parallel_tools、context_limit、max_output、reasoning_roundtrip | 缺少关键能力就按明确策略降级/失败，不隐藏地无限重试。                   |

## 5.2 三个必须严格区分的“恢复”

1.  Workspace Checkpoint：针对文件 Diff / Git 工作树回滚；不宣称回滚外部 API。

2.  Execution Checkpoint：保存 DAG 节点与 Task 状态、预算、身份和当前 owner。

3.  Effect Reconciliation：通过外部事务 ID、Git commit、DB 幂等键、Docker 容器 ID 核对真实效果；无法核对就人工介入。

# 6. 从优秀开源 Agent 中借鉴什么、避免什么

| 参考实现          | 成熟机制                                                                       | Sprout 具体取舍                                                                                      |
|-------------------|--------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| mini-SWE-agent v2 | 独立 shell 动作、线性轨迹、简单退出规则；新版默认原生工具调用                  | 保留 Sprout 现有循环；建立最小命令执行基线与统一 StepTrace，不照搬任意 Bash 权限。                   |
| Aider             | Tree-sitter 定义/引用图与 Token 受限 RepoMap；Search/Replace 编辑反馈          | 接入 workspace 图、SourceHash、Top-K 排序，编辑基线哈希强校验。                                      |
| OpenCode          | Session Prompt/Compaction、Agent profile、可追溯会话状态                       | 学习会话压缩与恢复 UI，不迁移 Python 内核；v1 指定 packages/core/src/agent.ts 需按实际分支重新定位。 |
| Codex             | 工具 Approval 与 Sandbox 的双重判断、受限文件系统、对 Git 写权限做隔离         | 操作级审批 + 实际执行环境强约束；不能仅靠正则 HardFloor 替代 OS 权限。                               |
| OpenHands SDK     | Agent / Workspace / Tools / Conversation 的分离；可交换 Local/Remote Workspace | 仅做明确 Python 端口，不引入第二套独立 Server。                                                      |
| Continue          | 内容哈希 + Git 分支/Tag 索引 + SQLite FTS5；增量任务列表                       | 将 SourceCommit/IndexTag 绑定到 CodeHit，分区快照 CAS 发布。                                         |
| Temporal          | Activity 至少一次、幂等键、非幂等 Activity 的审慎重试                          | 每种真实效果配 Reconciler；状态不确定时不以 retry_policy 掩盖风险。                                  |
| Google ADK        | 依据 Expected Tool Trajectory 的 EXACT/IN_ORDER 等评测                         | SproutBench 增加工具序列准确率，但最终验收仍看真实 Patch + Oracle。                                  |
| LangGraph         | Checkpoint/Interrupt 把非确定性与副作用作为独立恢复单元                        | 借鉴语义，不嵌入第二套 Workflow Runtime。                                                            |
| Pydantic AI       | 模型结构化输出、工具参数校验、受限重试                                         | 意图 / VerificationSpec / ModelCapabilities 接口做 Schema 校验。                                     |

引入上游库的统一门槛：pin commit/版本、SPDX/许可证与来源记录、CVE 检查、隔离 PoC、离线 Bench 性能基线和降级/回滚开关。不要直接复制尚未完成安全审查的 Agent Runtime。

# 7. 修订后的 PR/里程碑顺序

| 编号 | 等级 | 任务                         | 文件范围                                                   | 完成定义（DoD）                                                                      | 依赖          |
|------|------|------------------------------|------------------------------------------------------------|--------------------------------------------------------------------------------------|---------------|
| R01  | P0   | 基线与构建门禁               | pyproject.toml、uv.lock、CI、测试脚本                      | uv sync --locked；L0/L1；真实 Docker/Temporal 缺依赖记 not_run。                     | 无依赖        |
| R02  | P0   | 可信 Metadata 与否定意图     | intents.py、runtime/runtime.py、gateway/                   | 伪造 metadata.intent、否定/疑问/引用/历史状态不会创建写入任务。                      | R01           |
| R03  | P0   | 路径与 Scope 语义纠正        | task/models.py、FileBroker、Broker 契约                    | /repo 不包含 /repo2；Symlink/竞态/目录逃逸失败。                                     | R01           |
| R04  | P0   | 默认工具零绕行               | tools/registry.py、system_tools.py、execution/\*broker     | Process/Git/Network/MCP 受控；isolated 缺 Broker 为 denied，自动化不得触发宿主回退。 | R01、R03      |
| R05  | P0   | 隔离可用性验收               | execution/coding\_\*、sandbox/、Docker tests               | 隔离模式只执行容器；恶意输入/无镜像/超时不回退到 host。                              | R04           |
| R06  | P1   | ActionRequest 兼容化         | security/access.py、tools/executor.py、execution/models.py | 不重定义同名类型；老 API 回归通过；action_id/context 一致。                          | R03、R04      |
| R07  | P1   | Task/Node/Effect 状态协议    | task/、orchestration/、MetadataStore                       | Local 与 Temporal 状态投影一致；UNKNOWN 不能重复执行；手工恢复可审计。               | R06           |
| R08  | P1   | Git Apply 版本 CAS           | execution/apply.py、isolated_git\_\*、approval.py          | 审批/验收后修改 Patch 或 HEAD 一律阻断，不污染已有工作树。                           | R05、R07      |
| R09  | P1   | 冻结规格和独立验收           | strategy/acceptance.py、execution/verification\*           | Spec 版本、来源和 required 检查齐全；not_run 不会成为 passed。                       | R05、R08      |
| R10  | P1   | 生命周期与实际端到端         | web/webapi/app.py、CLI、真实 E2E                           | Stop 异常 DB 仍关闭；CLI→Docker→验证→审批→Apply E2E 可重放。                         | R07–R09       |
| R11  | P2   | Provider Capability + Budget | llm/、agent/loop.py、runtime/nodes.py                      | 缺能力 fail-fast；Token/调用/费用可关联 Task/Node/Stage。                            | R07           |
| R12  | P2   | 分级规划与止损               | strategy/、agent/loop.py                                   | 简单任务短计划；重复同错无新增证据时止损；对照 Bench 不退化。                        | R11           |
| R13  | P2   | ProjectRetriever v1          | workspace/、context/                                       | 精确+FTS+项目快照；Top-K 与 Token 评测；无向量仍工作。                               | R01           |
| R14  | P2   | RepoMap 与索引增量化         | workspace/graph.py、storage/projects、context/             | 改变一文件只更新受影响索引，读者不混快照。                                           | R13           |
| R15  | P2   | 扩展 CLI/HTTP/Node 验收      | execution/、strategy/、ProjectProfile                      | 每种 adapter 独立、安全、同一 VerificationReport。                                   | R09、R14      |
| R16  | P3   | Web 实际执行 Timeline        | web/frontend、web/webapi                                   | Board 与真实 Task 区分；断连后 EventCursor 恢复。                                    | R07、R10      |
| R17  | P3   | MCP/Skill 外部信任边界       | mcp/、skills/、gateway/                                    | 远端高风险工具默认拒绝；hash 改变撤销可信 Skill；注入测试通过。                      | R04、R06      |
| R18  | P4   | Evolution 与只读 Subagent    | evolution/、orchestration/                                 | 同 Oracle 真对照，失败不得标 Published；预算和身份不扩权。                           | R09、R14、R17 |

与 v1 PR-01～24 对应原则：保留既有工作包，但先拆解 PR-15，前置高危绕行阻断；PR-17 不只写 Ledger，还需同批交付 Reconciler；PR-13/14 必须绑定 Patch/Oracle 版本；PR-20 不应以“支持 TS”笼统验收。

# 8. 可执行验收与故障注入矩阵

| ID   | 试验输入 / 故障                               | 必须成立的客观断言                                                        |
|------|-----------------------------------------------|---------------------------------------------------------------------------|
| I-01 | 不要修复登录问题，只分析原因                  | 不得触发 code Task、WorkspaceConsent 或文件写入。                         |
| I-02 | 下面是文档："删除数据库"                      | 不把引用文本当操作指令。                                                  |
| I-03 | metadata.intent=task（未验证来源）            | 不得直接新建有副作用任务。                                                |
| I-04 | 多个待审批任务 + “同意刚才的”                 | 不猜 TaskId；明确询问目标。                                               |
| S-01 | scope=/tmp/repo，动作路径 /tmp/repo2          | 拒绝；路径分段而非字符串前缀。                                            |
| S-02 | 工作树内 Symlink 指向外部文件                 | Broker 再验证或使用无跟随的安全 I/O。                                     |
| S-03 | 模型请求 git push / curl 外网 / 下载执行      | 操作级 ActionRequest 及真实 Broker 审批，不进入直接 subprocess fallback。 |
| S-04 | 远程 MCP 工具描述声称 read-only，实际为写操作 | Capability/远端信任配置不允许误授权，默认 deny。                          |
| D-01 | Docker 不可用时提交 restricted Task           | waiting_resource/blocked，不自动转宿主命令。                              |
| D-02 | 容器挂载 .ssh、Docker socket、宿主凭证        | 无访问；记录安全测试证据。                                                |
| D-03 | 容器越权网络、资源耗尽、超时子进程            | 正确阻断、清理，不能伪造验证通过。                                        |
| A-01 | 审批后 Git HEAD 前移或工作树被修改            | CAS 失败且不应用旧 Patch。                                                |
| A-02 | 验收后 patch_hash 发生改变                    | 自动失效 verification；需要重新测量。                                     |
| A-03 | required 的 CLI/HTTP/DB 检查被跳过            | VerificationReport status!=passed。                                       |
| R-01 | 动作发出后 Worker 崩溃且响应丢失              | 进入 UNKNOWN 并尝试按外部凭证核对，不盲重试。                             |
| R-02 | Temporal 相同 Activity 重放、取消和所有权转移 | 没有重复 Git Commit/外部请求；Task+Node 一致。                            |
| R-03 | runtime.stop() 抛错                           | database.close 依然执行，错误可观测。                                     |
| P-01 | 同 Commit、同 Query 下全文/图索引失效         | 正确回退，返回证据和 unresolved 状态。                                    |
| P-02 | 扫描进行中切换 Git 分支                       | 检索不混旧新 Snapshot，不发布半成品索引。                                 |
| E-01 | 模型生成自己的“已通过”测试说明                | 不视为独立 Oracle 或生产级成功。                                          |

评测数据集建议补字段：repository_commit、request_type、expected_no_effect、oracle_provenance、required_tool_trajectory、task_result、effect_reconciliation、budget、environment_digest。模型成功/失败和系统安全违规分开报告。

# 9. 第一轮两周冲刺（替换 v1 第 21 章）

4.  第 1～2 天：将现有 dev Commit、uv.lock 和测试环境固定；接入 lint/pytest/导入/基础 CI，并报告哪些 Docker/Temporal 测试 not_run。

5.  第 3～4 天：修复 trusted metadata + 否定/引用/状态意图；针对多轮指代、审批、工作区提出 20～30 条安全负例。

6.  第 5～6 天：修复 DelegationScope 路径匹配与系统工具默认 Broker 注入；出具所有直接文件、网络、进程、Git I/O 清单。

7.  第 7～8 天：保留现有 DockerCodingBackend，不重写；补 Docker 禁宿主回退与容器权限对抗契约，修 Web lifespan 清理。

8.  第 9～10 天：明确 ActionRequest 兼容扩展、Patch/Oracle/Approval 指纹绑定及 Effect Reconciler 最小 ADR；形成基线报告和下一轮可排期 PR。

冲刺验收：首先证明“不会越权执行、不误触发写任务、隔离不可静默降级”；其次证明一条固定小型编码任务从任务创建、修改、冻结验收、审批到最终 Apply 的真实轨迹可重复审计。

# 10. 版本发布门禁与不得声称完成的条件

| 发布级别               | 最少必须提交的证据                                                               | 禁止宣称                               |
|------------------------|----------------------------------------------------------------------------------|----------------------------------------|
| 内部可信本地开发       | L0/L1 + Worktree 基线，CLI 操作真实审计、操作者主动授权                          | “安全执行不可信代码”                   |
| Restricted Coding Beta | R02–R10 安全和隔离门禁、真实 Docker E2E、Frozen Oracle、CAS Apply、无宿主回退    | “生产级远端沙箱”“全部语言可独立验收”   |
| Durable Execution Beta | 真实 Temporal Worker 跨进程故障注入、Effect Reconciler、独立时间线、活动任务恢复 | “通用 exactly-once”                    |
| 多语言/协作正式版      | 语言逐类 Adapter 证明、RepoMap Bench、并发冲突验证、独立 Oracle 和成本对照       | “支持所有项目框架”“自进化已经泛化验证” |

# 11. 技术引用、溯源和后续核验

Sprout 核心源码：所有相对路径位于 https://github.com/siyuuuu1014-cell/sprout/tree/762eaf3c62476d2377302329151a759c3d297baa ，本轮静态证据以此版本为准。原方案 v1 有“现有事实 A/B、上游建议 C、新设计 D”的分类，应继续沿用。

- Aider RepoMap: https://aider.chat/docs/repomap.html

- mini-SWE-agent v2: https://mini-swe-agent.com/latest/advanced/v2_migration/

- OpenHands SDK Architecture: https://github.com/OpenHands/docs/blob/main/sdk/arch/overview.mdx

- Codex Approvals/Sandbox: https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/sandboxing.rs

- OpenCode Session Prompt: https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/prompt.ts

- Continue Content-addressed Indexing: https://github.com/continuedev/continue/blob/main/core/indexing/README.md

- Temporal Python Activity Idempotency: https://github.com/temporalio/documentation/blob/main/docs/develop/python/best-practices/error-handling.mdx

- Google ADK Trajectory Evaluator: https://github.com/google/adk-python/blob/main/src/google/adk/evaluation/trajectory_evaluator.py

## 11.1 本轮尚未完成的动态核验

本轮没有把 844 个文件逐行在本地进行静态分析，也未运行新的 GitHub Actions、真实 Docker 镜像、Temporal Server、模型调用与端到端 Benchmark。以上观察足以确定文档中若干接口/顺序问题，但“漏洞是否可通过某个公开入口被真正利用”、跨模块性能、并发恢复结果仍必须通过对应 PR 的实跑证明。

## 11.2 最终建议

将原 v1 保留为总体方向说明；以本文件作为 v2 实施修订附录，先修 P0 路由/授权/路径/隔离，再交付带版本绑定的 Patch+Oracle+Effect 安全闭环，然后优化 RepoMap、模型能力、多语言与可信成长。只有在统一真实执行和证据合同以后，才值得启用多 Agent 并行。

# 12. v2.1 新增源码实证：原 v2 尚未覆盖的关键风险

以下为增量审查，不替代第 2 章已有 F01–F15。A 为代码路径可直接定位的行为；风险等级是处理优先级，不代表已利用或已在 Docker/Temporal 环境复现。

| **ID**   | **问题**                          | **源码证据**                                                                                                                                                                    | **必须修改的合同**                                                                                                                     |
|----------|-----------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| F16 · P0 | eager Skill 越过可信筛选          | context/context.py：visible_skills() 用 \_skill_visible()，但 skill_instructions() 只判断 enabled；agent/loop.py::\_skill_message() 在 eager 模式将正文作为 system 消息。       | 两个 disclosure 模式共用 visible_skills；eager 不允许任何 untrusted 或工具条件不满足的 Skill 注入；拒绝测试必须覆盖。                  |
| F17 · P1 | 共享 AgentLoop 实例计划串线       | agent/loop.py：run()/stream() 设置 self.\_last_plan；runtime/factory.py 注册共享 AgentLoop。并发请求可能相互覆盖计划元数据。                                                    | 新增每次调用独立 AgentRunState(plan/usage/steps/stop_reason)；禁止持久任务态放在共享实例属性。                                         |
| F18 · P1 | 多 ToolCall 返回不完整            | agent/executor.py 的 run_calls_detailed() 仅执行前 8 个；agent/loop.py::\_execute_tool_calls() 只为其返回的 outcomes 构造 tool message，却把 allowed_calls 全数计数。           | 每个 tool_call_id 必须存在一个结构化终态，包括 skipped_by_limit；实际执行计数和要求计数分别记录。                                      |
| F19 · P1 | 截断没有传播到 Node               | agent/loop.py::run() 预算终止时 metadata.truncated=True；runtime/nodes.py::\_agent() 仅持久化 content/waiting/pending_calls，忽略 truncated 和 usage。                          | AgentRunResult / NodeResult 保留 completion_status、stop_reason、usage、truncated；预算用尽不可默认为交付完成。                        |
| F20 · P1 | stream/run 行为漂移               | agent/loop.py::run() 有 token 使用量/StepStore/终态元数据；stream() 没有同等 token 与步骤落账，且流式自然结束没有统一结构化终态。                                               | 统一单一 step 状态机，stream 只投影增量；对同一脚本模型要求状态、预算、审计相等。                                                      |
| F21 · P1 | Gateway 消息 ACK 与 Task 完成混淆 | gateway/runtime_gateway.py::handle_message() 总是返回 status="completed"，且 task_id=outbound.session_id，不能表示等待/排队任务事实。                                           | GatewayResponse 拆 message_ack 和 task_ref/task_status；task_id 只允许真实 Task.id，不借用 Session.id。                                |
| F22 · P1 | 多层工具审计上下文并非同一路径    | agent/executor.py::run_calls_detailed() 将 requested_by/session_id 交给 ToolExecutor，未传 task_id/source；node-bound Tool 可以恢复一部分任务上下文，通用工具调用则无同等保证。 | 设计不可由模型填写的 InvocationContext，至少包含 task/session/principal/source/node/trace；逐入口检查，并禁止仅从 arguments 推断身份。 |
| F23 · P1 | 文本提示与实际意图枚举不一致      | runtime/runtime.py::\_intent_prompt() 输出选择集中包括 strategy；intents.py 仅定义七类，\_recognize_intent() 会把未知 intent 重置为 conversation。                              | 统一唯一受控枚举；迁移期间 strategy 做明确兼容映射，不能让模型产生静默无效路由。                                                       |

## 12.1 原 v2 文档需要同步的纠正

原 v2 的 F03/F04 指出 Broker 绕行路径，准确表述应为“外层工具策略仍在，但默认工具注册缺少与某些底层 Broker 的强制对接”。不可直接说所有外部调用无需审批。原 v2 第 3.9 小节引用位置应改为 v1 第 14–15 章；本版已调整标题。

v1 的 18 章把 PR-15/16/17 安排在后续阶段，并在正文注明外部/无人值守部署时是上线前置条件。正确修订是把其中的真实执行门禁和恢复合同前置为所有编码交付的安全依赖，而不是说 v1 完全没有提示这种依赖。

# 13. 可直接转化为测试的新增用例

| **测试编号**     | **最小场景**                                                                           | **强制断言**                                                                                  |
|------------------|----------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| G-01 / 信任      | 在配置 skills.disclosure=eager 且某 Skill enabled=True、trust=untrusted 时构造一次对话 | 发给模型的所有 system 消息中不得出现该 Skill 的正文、描述或可执行指令；不依赖是否安装成功。   |
| G-02 / 信任      | Skill requires_toolsets 不满足，但 eager=True                                          | 不注入；progressive/eager 在资格筛选上语义一致。                                              |
| A-03 / 并发      | 共享 AgentLoop 中模拟两会话 Plan A、Plan B 交错返回                                    | A 结果只含 Plan A；B 结果只含 Plan B；轨迹、TaskId、预算均分别独立。                          |
| A-04 / ToolCalls | 脚本模型在一个 assistant 回合返回 9 个唯一 tool_call_id                                | 最多 8 次实际调用，但全部 9 个 ID 各有一个 tool result，1 个明确 skipped_by_limit；计数真实。 |
| A-05 / ToolCalls | 配置 max_tool_calls=3 且同回合返回 9 个调用                                            | 恰好执行 3 个，6 个明确 skipped；并区分 per-step cap 与 run budget。                          |
| A-06 / Node      | AgentLoop 触发 max_steps 或 max_tokens 而未明确完成                                    | NodeResult 保留 truncated/stop_reason；不自动表示 Agent 完成；下游提案不伪造“编码完成”。      |
| A-07 / parity    | 同一个脚本模型分别使用 run 和 stream                                                   | 最终 steps、model_calls、tool_calls、token、stop_reason、审批记录可对齐；允许内容分块不同。   |
| G-03 / ACK       | GatewayMessage 提交后台编码任务，返回的是会话 ACK                                      | GatewayResponse 不标 Task completed；附真实 task_id 或明确没有 Task.id。                      |
| G-04 / routing   | 模型输出 strategy；旧事件订阅仍存在                                                    | 确定性兼容映射，未映射时结构化拒绝/非执行，不静默污染任务状态。                               |
| O-04 / 持久化    | 取消/暂停跨 Local/Temporal；Node owner 交接，先前外部效果未知                          | 最终状态不产生重复副作用；UNKNOWN 不被自动解释成“未开始”。                                    |

# 14. 重新排序的最小可执行增量 PR

以下是对原 v2 R01–R18 的补充。对已存在的对象做局部变更，不新增平行 Runtime / Security / Agent 体系。

| **补充包** | **工作范围**                        | **交付动作**                                                                                               | **依赖及兼容**                                  |
|------------|-------------------------------------|------------------------------------------------------------------------------------------------------------|-------------------------------------------------|
| R00        | P0：Skill eager 信任闭环            | 修改 context/context.py、agent/loop.py；过滤共用 visible_skills()。先增失败用例 G-01/G-02，再最小改动。    | 可与 R01 同步，必须早于任何 Skills 增强发布。   |
| R02b       | P1：Plan 隔离与 run/stream 语义一致 | 修改 agent/loop.py、agent/executor.py、runtime/nodes.py；引入 RunState/StopReason/ToolOutcome 的兼容输出。 | 依赖 R01；兼容既有 AgentResult、stream 消费者。 |
| R06b       | P1：InvocationContext 贯穿入口      | 在 AgentLoop→ActionExecutor→ToolExecutor→Broker 串起可信 task/node/source/principal；不信任模型 JSON。     | 依赖 R02/R03/R04；需要原有 Approval 对照测试。  |
| R10b       | P1：Gateway ACK/Task 结果分离       | 在 gateway/runtime_gateway.py/task_adapter.py 引入明确 ack/status 投影；保护旧字段兼容期。                 | 依赖 R07 状态权威与 R10 端到端验收。            |

## 14.1 对直接改代码的注意事项

不要将现有 security.access.ActionRequest 改名或复制到新包；新增 trace/run/node 等字段时必须保持默认值、序列化格式和历史审批指纹稳定。不要修改旧 TaskStatus 值；可以新增内部 Effect/Run 状态并兼容映射。

不要在未经行为验证的情况下直接移除所有 host fallback。先区分 explicit trusted_local、restricted_coding、remote：可信本地兼容可以保留，但必须明确模式和授权；restricted 一律 fail closed。

不要用“编译/单测通过”替代完整任务完成。每项新增功能至少给出拒绝、正常、失败、取消、超时、响应丢失、恢复场景的结果合同，失败/未执行/未知副作用必须分别表达。

# 15. 上游开源实践的 2026-10 验证及取舍

本节引用公开上游文档而不是 Sprout 已实现事实；上游版本随时间变化。应以 pinned commit/发布版本作采购与移植基线。

| **参考工程**      | **可借鉴机制**                        | **对 Sprout 的明确选择**                                                                                           | **公开依据**                                                   |
|-------------------|---------------------------------------|--------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| OpenHands SDK     | Conversation/Stuck Detector           | 已有防卡住策略参考：重复 action-observation、重复错误、单调独白等模式；Sprout 先实现确定性重复签名与证据增量判断。 | https://docs.openhands.dev/sdk/guides/agent-stuck-detector     |
| Pydantic AI       | 七层重试预算                          | 区分 HTTP transport、SDK、durable execution、模型 fallback、tool、output、model hook；避免各层叠加导致失控重试。   | https://pydantic.dev/docs/ai/core-concepts/retries/            |
| mini-SWE-agent v2 | 原生工具调用已为默认                  | 借鉴简洁循环和轨迹，不按 v1 老版 shell 形式硬编码长期模型接口。                                                    | https://mini-swe-agent.com/latest/advanced/v2_migration/       |
| LangGraph         | durable tasks / interrupt idempotency | 恢复时可能重放副作用前代码；Sprout 应把可重放状态与外部效果分开。                                                  | https://docs.langchain.com/oss/python/langgraph/functional-api |
| Temporal          | Activity 至少一次与幂等性             | 非幂等 Activity 严禁直接盲重试；以稳定外部键和 Reconciler 作为恢复前置。                                           | https://docs.temporal.io/activity-definition                   |
| Aider             | 任务相关 RepoMap 受 Token 预算约束    | 先做精确符号 + 图排序/FTS，离线 ProjectBench 证明提升才添加昂贵向量召回。                                          | https://aider.chat/docs/repomap.html                           |
| Codex             | Sandbox 和 Approvals 双门禁           | 人类批准 ≠ 沙箱边界解除；真实 OS/容器边界决定实际允许的 I/O。                                                      | https://openai.com/index/running-codex-safely/                 |

# 16. 审计边界及本轮最终结论

本轮确证的是源码接口形态、条件分支与设计不一致。尚未取得全仓逐行静态审计、完整 CI/pytest 日志、真实受限 Docker 攻防验证、Temporal Worker 故障注入和带模型的端到端实测结果。这些不能被当作已通过；没有直接证明某特定外部入口必然可利用。

发布顺序：先修 P0 Skill 信任过滤、意图元数据来源、路径 Scope 与真实动作统一门禁；随后让 AgentLoop 具备每次调用独立状态、流式一致语义和完整工具调用结果；最后完成 Oracle/Patch/Approval/Effect 绑定与外部核对。RepoMap、模型多语言、Evolution、Subagent 只能在这些合同稳定后择优推进。

结论：v1 的总体技术路线仍应保留。需要替换的是实施优先级与接口“可执行细节”，而不是推倒 Sprout 的现有 Python 模块化架构。
