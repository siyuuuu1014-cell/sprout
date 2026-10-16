# Sprout 增量 PR 实施计划

日期：2026-10-16；基线：`dev@762eaf3c62476d2377302329151a759c3d297baa`。**这是计划，不是已实施清单。Phase 0 后等待用户批准。**

阅读顺序：[基线和实测](CODEX_BASELINE.md) → [23项复核及源码位置](FINDINGS_REVALIDATED.md) → 本文。文件范围为拟修改范围，实施前重新核对 dirty diff 和接口；`tests/` 均指 `src/Sprout/tests/`。新文件仅在现有模块不能承载时增加，不引入第二套框架。

## 1. 排序与依赖修订

主线：**R00 → R01 → R02 → R03 → R04 → R05 → R06 → R06b → R02b → R07 → R08 → R09 → R10b → R10 集成门禁**；之后 **R11 → R12 → R13 → R14 → R15 → R16 → R17 剩余增强 → R18**。

- R00 可先用现有安全定向集建立失败回归，不必等待全量 fixture 修正；R01 紧随完成可复现完整基线。
- R04 把 R17 的 MCP 高危操作拒绝/可信启动最小边界前移。R00 的 Skill 过滤也不等 R17；R17 仅剩供应链/生命周期增强。
- R02b 依赖 R01，可独立设计，但合并主线先完成 P0；其运行状态合同是 R07 的输入，不能等状态协议完成后才处理截断。
- R06b 依赖 R02/R03/R04/R06，复用 NodeToolInvocationContext 与 ActionRequest；R02b 工具账本消费该上下文。
- **R10 拆为 R10a 生命周期小 PR 和 R10c 真闭环门禁**。R10a 仅依赖 R01，可在 P0 后提前执行。R10b 依赖 R07/R06b 和稳定 Agent 终态；R10c 依赖 R10b，不反过来，消除原“先 E2E 后修 ACK”的验收倒置。
- R08 先固定 source/patch/approval CAS；R09 再完善冻结 Oracle 的准入及身份绑定。R08 先使用现有 verification blocker，二者共享合同草案，不制造循环。
- R13 技术上只依赖 R01，但排在交付可信闭环之后；R15 的基础语言 Adapter 不必强依赖完整 RepoMap，不过发布验收依赖 R09 和所选 ProjectProfile/检索版本。
- 所有“并行可做”只表示依赖允许；默认仍一个最小 PR 一次审查，不自动启动多 Agent。

## 2. 所有 PR 的共同完成和回滚合同

每批必须提交：针对当前缺陷的先失败后通过测试、已有相关回归结果、准确命令、未运行基础设施列表、API/序列化兼容说明。L0=导入/锁/lint；L1=离线单元；L2=真实 Docker/进程/入口；L3=真实 Temporal 故障/回放与显式授权的模型验收。跳过/未运行不是成功。

涉及存储的 PR 必须先列旧 schema/旧记录读取兼容、可选字段默认值、schema version、回填 dry-run 与恢复演练；没有必要不迁移。数据库回滚不能仅靠 revert 代码，须保留升级前备份、双读兼容或明确只可前滚。未知外部副作用不执行补偿猜测，不自动重跑。

代码回滚以独立提交 revert 为首选，**不使用 reset --hard，不回滚用户无关变更，不删除任务/审计记录**。回滚不能重新开放已关闭的危险执行路径；必要时关闭对应能力并保持只读/等待。

## 3. P0：先封闭信任与执行边界

### R00 — eager Skill 统一资格筛选（首个修复）

- 现状/位置：F16，`context/context.py:36/83` 与 `agent/loop.py:27`；已动态复现 G01/G02。
- 文件：`src/Sprout/context/context.py`、必要时 `agent/loop.py`、`tests/test_skills_disclosure.py`。
- 步骤：添加 eager/progressive 参数化拒绝回归；正文注入共用 visible_skills；保留格式和可信 Skill 正常行为；验证真实组装的 system 消息。
- 依赖：无，已有定向基线可用。测试：G01/G02、所有 trust/disabled/requires/fallback 组合及现有 disclosure/context 回归。
- 风险：此前依赖不可信 Skill 注入的行为会被拒绝，这是预期纠正；不改变存储/API。
- DoD：不合格 Skill 的正文/说明/指令不出现在模型 system 消息；可信可用 Skill 不退化。
- 回滚：独立 revert；若发现兼容问题暂时禁用 eager，不能以恢复不可信注入继续运行。

### R01 — 可复现基线与 CI

- 现状：F11/F14；锁文件/lint 已可用，全量 collection 因两个示例模块缺失失败。
- 文件：`pyproject.toml`、`uv.lock`（仅必要时）、`tests/test_bubble_sort.py`、`tests/test_hello_world.py`、新增 `.github/workflows/`、本目录基线。
- 步骤：判定示例测试应绑定何种 fixture/独立实验仓库；建立临时 fixture 或正确测试归属，不能简单 ignore 隐藏错误；锁定 L0/L1；L2/L3 独立 opt-in job；补状态完整矩阵和环境说明。
- 依赖：无；测试：干净环境 locked sync、导入、ruff、collect、完整离线 pytest；基础设施测试显式标签分组。
- 风险：依赖解析/跨平台差异；禁止 CI 自动读取真实 API key 或默认调用模型。
- DoD：干净 checkout 可复现离线检查；完整 collection 无错误；完整运行结果如实归档，外部 job 未执行清晰显示。
- 回滚：撤回 CI/fixture 独立提交，保留日志；不删除生产数据，不无故刷新依赖。

### R02 — 可信路由、否定与唯一意图枚举

- 现状：F01/F02/F23；`runtime/runtime.py:1257/1316/1417`、`intents.py:23/86`。
- 文件：上述文件、`gateway/identity.py`、`gateway/task_adapter.py`、各输入适配处及 intents/runtime_gateway 测试。
- 步骤：盘点每个入口 metadata 来源；服务端构造可信路由输入，外部字段只作提示；先判否定/引用/状态/歧义；strategy 建明确兼容映射，不重复枚举。
- 依赖：R01。测试：CLI/Web/MCP/SDK 伪造 metadata、20–30 条中文/英文负例、正常显式编码/只读、历史策略事件。
- 风险：误拒绝原先隐式任务、事件订阅兼容；歧义询问，不自动写入。
- DoD：不受信字段不能扩权/触发写任务；正常入口仍调用现有 Task 创建链。
- 回滚：保留明确 /task 与只读入口；新路由禁用时保守确认，不恢复元数据赋权。

### R03 — Scope 路径与打开时授权

- 现状：F05，`task/models.py:114/187`；底层 FileBroker 已有 guard，须复用。
- 文件：`task/models.py`、`execution/file_broker.py` 及实际 path guard、scope/file/security 测试。
- 步骤：定义规范化和目录边界；统一 permits/narrow；保留空允许集=不限制、不可满足交集=拒绝的语义；最终资源打开处复验，测试 junction/symlink。
- 依赖：R01。测试：同名前缀兄弟目录、跨盘/大小写、相对路径、拒绝优先、过期scope、链接替换、已有文件操作。
- 风险：Windows/POSIX 差异、历史路径表示；不直接重写旧记录。
- DoD：范围只能缩小、不误放行兄弟目录；合法资源兼容。
- 回滚：禁用歧义 scope 的写入并要求重新授权；不迁移删除旧 scope。

### R04 — 动作清单与 Broker 零绕行

- 现状：F03/F04/F08，`tools/registry.py:192`、`system_tools.py:443/854`、MCP adapter:36。
- 文件：`tools/registry.py`、`tools/system_tools.py`、`tools/executor.py`、`execution/` 对应 broker、`mcp/adapter/tool.py`、`mcp/client/connection.py`、Skill 进程入口及测试。
- 步骤：列出全部直接文件/进程/网络/Git/DB/MCP/Skill I/O；区分 trusted_local/restricted；每种 Git operation 走受控动作；缺 Broker fail closed；远端高危默认拒绝，启动配置不从模型取值。
- 依赖：R01/R03；路由权限消费 R02。测试：逐操作委派 spy、无人值守审批拒绝、缺 Broker、取消/超时、remote/ref/hook、外层策略和底层策略双层回归。
- 风险：现有宿主工具兼容；只读动作也可能启动外部程序，不能按名字推断无效果。
- DoD：清单每一项有模式/授权/审计/效果定义和测试归属，无未说明宿主回退。
- 回滚：按能力开关禁用新增不兼容工具；保留已封闭的安全默认，不重开 bypass。

### R05 — 隔离运行准入和真实容器门禁

- 现状：F10/F15；已有 DockerCodingBackend、宿主 GitWorktree，不重新设计 sandbox。
- 文件：`execution/coding_backend.py` 及既有 coding 模块、`sandbox/git_worktree.py`、Docker 集成测试与配置。
- 步骤：统一 restricted 准入；明确镜像/挂载/网络/用户/资源限制；镜像不可用仅等待/失败；验证容器可用和执行证据，拒绝 host fallback。
- 依赖：R04。测试：真实固定镜像、无镜像、启动失败、超时/取消、越界挂载、秘密环境隔离；trusted_local 正常兼容。
- 风险：Docker 平台差异、镜像供应；测试镜像/网络操作另行获批，不自动拉取未知镜像。
- DoD：真实 L2 证明 restricted 只在容器执行；缺基础设施是 not_run，不能发布为通过。
- 回滚：关闭 restricted 编码入口并显示原因，保留只读；绝不改为宿主执行。

## 4. P1：运行状态、恢复与可靠交付

### R06 — 现有 ActionRequest 的兼容关联字段

- 现状：`security/access.py:52` 已有类型，不能按 v1 新建同名对象。
- 文件：`security/access.py`、`tools/executor.py`、`execution/models.py`、审批/序列化测试。
- 步骤：定义 action/run/node/trace 关联的最小可选字段或适配信封；沿用领域 Result，提供观测映射；检查审批指纹不被默认值改变。
- 依赖：R03/R04。测试：旧构造器、旧 JSON/DB 读写、旧审批恢复与新关联 ID。
- 风险：持久格式与循环依赖；DoD：旧客户端/记录可读、只有一个 ActionRequest、每个动作可关联。
- 回滚：兼容字段双读保留，关闭新写入；如需迁移先备份，不能直接降级丢字段。

### R06b — 可信 InvocationContext 贯穿

- 现状：F22，`tools/context_keys.py:39` 已有 NodeToolInvocationContext；通用 ActionExecutor 缺 task/source。
- 文件：`agent/executor.py`、`agent/loop.py`、`tools/executor.py`、`tools/context_keys.py`、入口适配器及 Broker。
- 步骤：复用/扩展现有绑定上下文；从服务端 Task/Principal 取得身份；逐层传递 task/session/node/source/trace，不从工具 arguments 推断。
- 依赖：R02/R03/R04/R06。测试：CLI/Web/MCP/SDK/后台任务相同审计关联、伪造上下文、scope 收窄、旧审批恢复。
- 风险：共享 agent 泄漏上下文；DoD：通用与 node-bound 工具同等可追踪且无跨会话污染。
- 回滚：保留可选默认读兼容，缺上下文的高危调用拒绝；不降级成 system 身份执行。

### R02b — AgentLoop 每次调用状态和终态（拆成四个小 PR）

- 现状：F17–F20；现有 AgentResult/预算/StepStore 可复用。
- 文件：`agent/loop.py`、`agent/executor.py`、必要时 `agent/base.py`、`runtime/nodes.py`、对应 tests。
- 步骤顺序：① local RunState 替代 self._last_plan；② 每个 tool_call_id 终态齐全，分开 requested/executed/skipped；③ 截断/stop_reason/usage 透传 Node，不误报完成；④ 同一内部 step 状态机驱动 run/stream，增量输出只做投影。
- 依赖：R01；主线合并在 P0/R06b 后；R07 依赖前三步的稳定输出。
- 测试：屏障控制并发、9调用/8执行/9结果、预算3、executor=None、模型/工具预算、审批/取消/异常、Node 停止、run/stream parity。先 scripted，再显式授权真实 Provider。
- 风险：stream 客户端兼容、usage 重复记账；不同时修改模型路由和编排。
- DoD：四个子 PR 分别具备失败回归和回归证据；每次运行独立、终态诚实、账本唯一。
- 回滚：按子 PR 逆序撤回；旧消费者保持默认字段兼容；必要时禁用流式任务执行，不能将 truncated 当完成。

### R07 — Task/Node/Effect 状态与受控恢复

- 现状：F07；`orchestration/execution_guard.py:39` 已实现防重复执行，不能重写为自动 lease 超时重跑。
- 文件：`task/models.py`、`orchestration/execution_guard.py`、`result_sync.py`、`service.py`、`storage/contracts/`、`storage/local/` 对应存储及恢复接口。
- 步骤：梳理状态权威和投影；列各 effect 外部凭证/可查询性；先实现可核对的 Git/DB 子集；UNKNOWN_NO_RETRY 保留；人工 resolve 需证据、身份和审计。
- 依赖：R06/R06b/R02b。测试：Local/Temporal 等价状态、重复 activity、取消、响应丢失、崩溃、旧记录、人工确认拒绝/成功。
- 风险：重复副作用、迁移和永久等待；不承诺通用 exactly-once。
- DoD：每类效果都有明确可恢复/不可核对矩阵，未知不自动重跑、可信完成可复用、状态跨入口一致。
- 回滚：停止新调度/恢复写入，保留占用与审计；备份/双读迁移，不清除 uncertain。

### R08 — Apply 同版本 CAS

- 现状：F06，普通 Apply 缺完整版本比较；隔离 Git evidence/coordinator 已有保护。
- 文件：`execution/apply.py`、`execution/isolated_git_evidence.py`、`isolated_git_coordinator.py`、`runtime/changes.py`、审批/提案模型及测试。
- 步骤：复用 source/patch/spec/report/approval identity；预检目标干净状态和预期 HEAD；原子占用/CAS 后应用；精确回滚仅本操作差异，禁止伤及用户工作树。
- 依赖：R05/R07。测试：批准后改 patch、验收后改 HEAD/spec、并发 apply、脏工作树、提交失败/响应丢失、现有隔离边界。
- 风险：Git hook/凭证、副作用补偿；DoD：所有写路径绑定同一身份，失败不污染用户修改。
- 回滚：关闭 Apply 写入口，保留提案/审批/证据；不自动 reset 仓库或重试未知提交。

### R09 — 冻结规格与独立验收准入

- 现状：已有 verification blocker/report/replay oracle，不重造报告。
- 文件：`strategy/acceptance.py`、`execution/verification.py` 及 verification 模块、`evolution/replay_oracle.py`、规格/报告测试。
- 步骤：固定 required checks、oracle provenance、source/patch/image/policy identity；区分 failed/skipped/not_run；候选自产测试不能单独成为可信 Oracle。
- 依赖：R05/R08。测试：缺检查、跳过、篡改规格、污染 Oracle、历史缺证据、普通批准和独立强制豁免语义。
- 风险：旧报告发布受阻；DoD：只有必选检查真实通过且身份相同才能 verified/completed；强制应用不变成验证完成。
- 回滚：旧报告保留可读，缺新证据等待补验，不批量伪造 passed。

### R10a/R10c — 生命周期与真实闭环（原 R10）

- 文件：`web/webapi/app.py`、现有 CLI/Web/SDK/MCP 集成测试、真实 Docker/Temporal fixture。
- R10a 步骤：逐资源安全启动/关闭，收集多个错误，保障数据库关闭；依赖 R01，可提前。测试 start/stop/callback 故障，DoD：每项已获取资源都有清理保障。
- R10c 步骤：固定独立小型多文件仓库和 Oracle，按真实入口→调度→容器→验证→审批→Apply→查询执行；分别 Local、Temporal，再做断线/恢复。依赖 R05/R07/R08/R09/R10a/R10b/R02b。
- 风险：真实服务和费用；先 scripted/echo，live 需用户明确预算；不得擅用此前额度。
- DoD：一条完整正向轨迹和关键失败轨迹可重复，源版本/容器/报告/批准/提交/Task 投影可关联；没跑的入口单列。
- 回滚：关闭存在问题的入口/写能力，不删除轨迹或恢复为绕过验证；生命周期小 PR 可独立 revert。

### R10b — 会话 ACK 与 Task 结果分离

- 现状：F21；`gateway/runtime_gateway.py:70` 错误，execute/TaskQueries/TaskActions 已有可复用接口。
- 文件：`gateway/runtime_gateway.py`、`gateway/task_adapter.py`、消费 GatewayResponse 的 transport/SDK/Web 展示及测试。
- 步骤：定义兼容 ACK 字段和真实 task_ref/status 投影；无 Task 不填 session 当 task_id；旧字段兼容期明确，查询仍走既有 owner 检查。
- 依赖：R07/R06b/R02b；**先于 R10c 验收**。
- 测试：排队/等待批准/等待资源/真正完成/无Task、旧客户端、所有入口 owner 拒绝。
- 风险：客户端误依赖 completed；DoD：消息接收成功不代表任务完成，原显式 execute 不退化。
- 回滚：兼容响应投影开关，保留新字段读取；必要时停止错误客户端写任务，不继续虚报完成。

## 5. P2/P3/P4：闭环稳定后再扩展

| 包 / 现状 | 文件与实现步骤 | 依赖 / 回归测试 / 完成定义 | 风险与回滚 |
|---|---|---|---|
| R11 能力+预算；F12，已有持久预算 | `llm/registry.py`、`llm/client.py`、`llm/task_budget.py`、配置/agent。给 provider 增加兼容能力声明；调用前 fail-fast；复用唯一账本，不再实现模型调度系统。 | R07/R02b；mock 不支持 tools/stream/thinking、token/call 预算、旧配置、跨进程账本；DoD=能力不匹配明确拒绝、run/stream 同账本。 | Provider 差异；新增字段默认兼容，关闭新特性而不取消预算；保留历史账本。 |
| R12 分级规划/止损；已有 planner | `strategy/`、`agent/planner.py`、`agent/loop.py`；简单任务短计划，按重复动作/错误与证据增量止损，分清传输/模型/工具重试预算。 | R11；固定脚本重复、可恢复错误、正常长任务及费用对照；DoD=不叠加无限重试、成功率不退化。 | 误停；独立策略开关回旧 planner，保留硬预算/审计。 |
| R13 ProjectRetriever；F13 部分已有 | `context/retrieval.py`、`context/builder.py`、`workspace/`、`storage/`；先盘点现有图/全文/知识合同，再加统一命中结构、精确符号+FTS、可解释排序。 | 技术依赖 R01，排期在 R10c 后；固定 commit/query Top-K/无向量/索引失效回退；DoD=证据有源位置、离线基准有改善。 | 重复索引、召回退化；feature flag 回既有检索，保留旧索引可读。 |
| R14 RepoMap/快照增量 | `workspace/graph.py`、`storage/` 项目分区、`context/`；复用图和分区，staged snapshot +权威 manifest 指针 CAS，增量失效。 | R13；改单文件、扫描切分支、分区写失败/崩溃、读者混版；DoD=不发布半快照、只读一致版本。 | 跨库无天然原子事务；保留旧 manifest 指针，仅清理可证未引用分区，迁移先备份。 |
| R15 多语言验收 Adapter | `execution/` verification、`strategy/acceptance.py`、既有 ProjectProfile；先选一种 CLI/HTTP/Node Adapter，复用 VerificationReport、同版 Oracle 和隔离执行。 | R09；按语言新增固定独立 fixture、失败/缺工具/not_run；Profile版本稳定，使用检索时依赖 R13/R14。DoD=逐语言有真实结果，不宣称全语言。 | 平台/依赖网络；禁用新增 Adapter，旧报告仍可读，不退化为“模型说通过”。 |
| R16 Web Timeline | `web/frontend/`、`web/webapi/routes/tasks.py`、WebSocket；以现有权威事件与 task query 投影替代假进度，cursor 断线续读。 | R07/R10b/R10c；断连/重复/乱序/owner、Board/Task区分；DoD=UI显示与持久事实一致。 | 客户端缓存漂移；回旧只读视图，不改任务事实或删除事件。 |
| R17 MCP/Skill 剩余信任增强 | `mcp/`、`skills/`、`gateway/`；在 R00/R04 已拒绝越界基础上补身份/来源、内容hash改变撤销信任、输出data provenance、安装/启用审批。 | R00/R04/R06/R06b；hash变化、服务器冒名、注入、旧skill升级；DoD=安装不自动可信，远端输出不赋权。 | 供应链/旧插件中断；隔离插件并保留配置证据，不关闭策略强行兼容。 |
| R18 Evolution/只读 Subagent | `evolution/isolated_replay.py`、`replay_oracle.py`、`candidate_manager.py`、`orchestration/`；复用显式隔离实验/候选发布占用，先真实基线/候选同Oracle对照，再只读委派。 | R09/R14/R17 和 R10c；缺revision/报告、失败、不明发布、预算/身份不扩权、真实成对实验；DoD=只有可信比较才允许晋级，未知发布不重试。 | 实验成本/自证循环；关闭成长发布/委派，保留候选与实验记录，不能抹掉未知效果。 |

## 6. 进入第一批开发前的检查单

先确认本 Phase 0 文档；保持用户的 8 个删除不动。第一批只执行 R00，不搭车修复 F01–F23。开始前重新显示分支/SHA/dirty；记录失败回归，最小补丁，运行相关回归；汇报准确通过数字、剩余风险和差异，然后停止供检查。R01 再修测试基线。真实 Docker/Temporal/模型调用和任何迁移必须按各批权限与环境单独确认，不沿用历史聊天的支付/部署授权。
