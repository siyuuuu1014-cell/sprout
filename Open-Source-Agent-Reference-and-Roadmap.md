# Sprout 开源 Agent 借鉴清单与实施路线

调研日期：2026-10-12。目标：帮助当前 Sprout 确定下一步修改方向，而不是继续堆框架和入口。

调研方式：阅读官方 GitHub 仓库、部分源码与维护者文档，并对照本地 Runtime、ReadPlan、验收、队列、项目存储和成长发布实现。未安装这些项目、未在本机跑它们的性能榜，也未修改 Sprout 业务代码。远端链接主要指向 main，后续真正复用时需锁定具体 revision，不能当不可变版本依据。

## 1. 先明确：这个项目下一步到底做什么

我的建议是先把 Sprout 收敛为：**一个能理解已有项目、完成小范围改动、提供独立测试证据、经过人工批准且可恢复执行的开发 Agent。**

这是基于当前实现的产品建议，不是已经替用户决定长期产品定位。先以可信本地环境和 Python 项目为迭代范围；如果目标是外部用户或无人值守，真实执行隔离必须提前。

下一轮不建议同时推进“完整项目本体、多 Agent、自动进化、全语言、更多渠道”。当前缺的主要不是 Agent 名称或接口数量，而是：

1. **知道哪里失败：**缺少固定的真实任务评测和分阶段失败统计。
2. **知道应该读什么：**已有结构图，但任务定向上下文仍可提高相关性与预算利用。
3. **知道验证了什么：**Python 输入输出与 PoC 门禁已有，仍需独立回归基线、需求映射和多适配器。
4. **知道能否安全重试：**本地/Temporal 提交、恢复和成长发布存在一致性风险。
5. **知道用户看到了什么：**手工看板与执行任务分离，执行证据没有形成统一产品视图。

推荐的近期成功标准不是“模块更多”，而是：拿一组固定任务，Sprout 能反复完成或准确报告失败，且不越权、不误报通过、不重复应用。

## 2. 选了哪些项目，为什么选

| 参考对象 | 与 Sprout 的关系 | 已核对的机制 | 采用态度 |
|---|---|---|---|
| [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk) | 编码 Agent 内核与执行环境 | Local/Remote workspace、工具 Action/Observation、Server 与应用边界 | 借契约与边界，不替换 Runtime |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 编排框架，不是完整编码产品 | checkpoint、interrupt、task 结果恢复、副作用幂等要求 | 借恢复语义，不叠加第三个调度系统 |
| [Aider](https://github.com/Aider-AI/aider) | 代码编辑与上下文选择 | repo map 图排序、architect/editor 分工、编辑后 lint/test | 最值得借任务上下文与小步反馈 |
| [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) | 精简编码 Agent 基线 | Agent–Model–Environment 分离、步数/成本/时间限制、轨迹保存 | 借简洁执行闭环，不借无约束 shell |
| [Cline](https://github.com/cline/cline) | 开发者操作体验 | Plan/Act、动作确认、项目规则与技能 | 借用户可见的计划和控制，不整体移植 IDE/SDK |
| [SWE-bench](https://github.com/SWE-bench/SWE-bench) | 评测配套；不是 Agent | 补丁应用、容器测试、按用例判定和结果报告 | 借小型内部评测方法，不立即跑完整榜单 |

这些参考分工不同，不能横向用 star 或榜单数字判断谁的整体架构最适合。OpenHands 当前主仓库承担 Agent Canvas，编码内核应重点看 SDK；不要沿用旧文章把主仓库旧 Runtime 路径当成当前实现。[当前仓库边界](https://github.com/OpenHands/OpenHands#repository-boundaries)、[SDK 边界说明](https://github.com/OpenHands/software-agent-sdk#repository-boundaries)。

## 3. 最值得借鉴的机制，具体怎么接进 Sprout

下列“外部事实”是查到的机制；“Sprout 改法”与验收标准是本次建议，尚未实现。拟新增的类型、状态、命令和字段均是设计草案，不是现有 API。

### 3.1 Aider：预算内的项目地图与相关代码选择

外部事实：Aider 为模型提供文件与重要符号的精简地图，并按依赖图相关性和 token 预算选取内容。其 `repomap.py::get_ranked_tags` 使用带权图排序，结合当前文件、提到的路径和标识符。[机制说明](https://aider.chat/docs/repomap.html)、[排序源码](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py)。

Sprout 现状：已有 WorkspaceGraph 与 ReadPlan；`scanner.build_read_plan` 主要按文件相关性和资源类型排序、限制数量。**不应再建第二套项目图或数据库。**

建议改法：

- 在已有分析图上增加任务定向 selector，先选用户提及的符号/文件，再扩展依赖、实现、测试邻居。
- 输出“路径 + 符号签名 + 证据位置 + 入选原因”，不是直接灌全文件。
- 区分摘要预算与全文预算；请求全文仍走 ReadBroker、安全分类和任务范围。
- 缓存键包含项目 snapshot、任务范围、相关标识符、预算与 selector 版本。
- 先用现有 Python AST 图验证收益；tree-sitter 是后续多语言候选，不是第一步必需依赖。

修改位置：`workspace/scanner.py`、`workspace/graph.py`、`context/workspace.py`、`context/builder.py`，必要时在 workspace 内增加 selector。

验收：固定问题集上比较正确文件/符号命中率、上下文 token、任务成功率；必须不突破权限、不跨 snapshot。排序图不能替代精确源码事实，缺边要报告而不是猜。

### 3.2 Aider：修改后的快速 lint/test 反馈

外部事实：Aider 可配置编辑后 lint 和自动测试，将错误输出反馈给模型，也支持不同语言的 lint 命令。[官方说明](https://aider.chat/docs/usage/lint-test.html)。

Sprout 现状：已有 AGENT/EVALUATION 修复循环、冻结验收和最小 PoC，不能重新做一套“exit code 0 即完成”的弱门禁。

建议改法：把验证拆成快速反馈与最终验收：

```text
小批修改 → 语法/格式/局部测试 → 失败定位 → 下一轮小批修复
最终补丁 → 冻结需求验收 + 原项目回归 + PoC → 现有完成门禁
```

检查结果统一记录 command、cwd、源码版本/补丁摘要、退出码、具体用例、耗时与日志引用；最终判定仍使用现有共享 verification 规则。缺 runner、命令扣留、测试被跳过、输出无法解析不能冒充通过。

修改位置：`runtime/nodes.py`、`execution/acceptance.py`、`execution/verification.py`、`strategy/acceptance.py`。

收益：更早发现局部错误，减少最后才大范围返工。风险：过频测试增加成本；先选局部检查，完整回归在稳定检查点运行。

### 3.3 LangGraph：暂停、恢复与副作用的明确契约

外部事实：LangGraph 的 interrupt 恢复会重新运行所在节点；官方要求此前副作用幂等或拆到独立节点。Functional API 的 task 结果可持久化，恢复时使用保存结果，而不是把所有操作重新计算。[interrupt 规则](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/interrupts.mdx)、[task 与恢复](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/functional-api.mdx)。

Sprout 现状：已有节点存储和恢复，已有审批节点。当前 TemporalTaskQueue 先本地 put 再远端 start，可能出现“调用报告失败，本地却已入队”；仅有 checkpoint 不足以消除这个窗口。

建议改法：

- 保留 ExecutionGraph + Local/Temporal，暂不引入 LangGraph 运行依赖。
- 定义提交、执行 attempt、节点结果和外部副作用的稳定身份。
- 权威存储先记录提交意图，由可恢复 dispatcher/outbox 投递；用幂等键识别重试，明确哪个 backend 负责执行。
- APPROVAL 负责等待/决策；APPLY 等副作用独立，重复恢复需检查同一 action 的完成证据和补丁状态。
- 建 Local/Temporal 共用的恢复契约测试，覆盖“操作成功但结果未保存”的不确定窗口。

修改位置：`runtime/queue.py`、`orchestration/service.py`、`orchestration/terminal/`、`runtime/changes.py`、`storage/contracts/metadata.py`。

验收：重复提交、断连、批准后重启、activity 重试、结果写入失败都不重复应用；未完成副作用不因节点标记被直接跳过。目标是幂等的业务效果，不承诺任意外部系统天然 exactly-once。

### 3.4 OpenHands：执行环境可以替换，业务规则不跟着重写

外部事实：OpenHands SDK 将 Agent、工具、workspace 与 Agent Server 区分，支持本地和容器/远端运行；工具有明确 Action/Observation 契约。[架构](https://docs.openhands.dev/sdk/arch/overview)、[工具边界](https://docs.openhands.dev/sdk/arch/tool-system)、[workspace 工厂源码](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/workspace/workspace.py)。

Sprout 现状：Git worktree 保证改动位置和清理，但不是 OS 安全隔离；ProcessBroker 与进程运行方式仍有较强连接。

建议改法：

- 在 execution/sandbox 内定义执行环境契约：启动、执行 argv、读写受限文件、取消/终止进程树、取日志、关闭。
- broker 继续授权；环境 backend 执行，不自己授予权限。
- Local backend 保留可信本地体验；Container backend 用同一 task、验收和结果结构。
- 限制挂载、宿主 Secrets、权限、资源和出网；不能挂 Docker socket 或整个宿主目录来“方便运行”。
- 记录实际 OS、镜像摘要、依赖锁摘要；容器与 Windows 宿主的行为差异要测试，不能假设路径/换行/可执行文件相同。

修改位置：`execution/process_broker.py`、`sandbox/`、`config/settings.py`、`runtime/factory.py`、`runtime/runtime.py`。

验收：越权宿主读取、路径逃逸、出网、超时、取消和残留进程测试；停止时环境先 join，再关闭存储。容器不是自动安全，需要具体配置和对抗测试。

实施顺序：可信本地场景可在闭环之后；多人或无人值守场景必须先落实。不能照搬 mini-SWE-agent 默认 `bash -lc` 与后台 destructor 清理来削弱 Sprout 的 argv、安全和生命周期约束。[mini Docker 源码](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/environments/docker.py)。

### 3.5 OpenHands：工具输出结构化，界面只是消费者

外部事实：OpenHands 工具以 Action → Observation 明确输入/输出模型，输入验证、schema 和 executor 分工；它的应用通过 SDK/Server API 接入。[工具系统](https://docs.openhands.dev/sdk/arch/tool-system)、[schema 实现](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/tool/schema.py)。

Sprout 现状：已有 ToolSpec/ToolResult，但 `data: Any`、多个 broker result 和文本表达使上层容易只看 ok/content；Web 手工 task 状态不是 Runtime 状态。

建议改法：渐进补齐 typed payload 与公开查询契约，保留兼容层：

- 把 executed、denied、waiting_approval、timeout、failed 分清；不要让“没有执行”仅藏在文字里。
- 校验 command/path 等输入，风险事实来自服务端；MCP annotations 只能是提示，不替代安全决定。
- task/run/node/tool-call/proposal/verification 用稳定关联 ID，API 与 UI 读权威投影。
- 手工看板保持独立 BoardItem，链接 ExecutionTask；任何手工 done 都不能改变验收完成状态。

修改位置：`tools/result.py`、`tools/executor.py`、`execution/models.py`、`events/types.py`、`web/webapi/routes/project.py`、`web/webapi/routes/tasks.py`。

验收：同一任务在 CLI/Web/远程查询的状态和证据一致；结构化 denied 不被误当失败重试绕过审批。先补实际存在的 payload，不要求一次把全项目 dataclass 改成 Pydantic。

### 3.6 mini-SWE-agent：内核保持小，退出原因和轨迹保持清楚

外部事实：DefaultAgent 依赖独立 Model/Environment；有 step/cost/wall-time 限制、格式错误上限和逐步轨迹保存；返回提交/退出信息。[DefaultAgent 源码](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py)。

Sprout 现状：AgentLoop 已有模型、executor、调用预算和 step store。**预算控制不是从零新增功能。**

建议改法：统一终止原因，记录每个 attempt 的实际消耗和最后有效观察；把工具、会话、提交、后台生命周期从 Runtime 逐职责迁出，但不改用户 API。

要特别区分：模型完成回答、Agent 停止、产生补丁、验证通过、人工批准、项目任务 completed。mini 环境的 Submitted 标记并不能替代 Sprout 现有完成门禁。

修改位置：`agent/loop.py`、`agent/base.py`、`trajectory/models.py`、`runtime/runtime.py`、`runtime/nodes.py`。

验收：模型格式错误、预算耗尽、工具等待批准、执行超时都有不同原因和可读证据；中断可保留最新有效结果。不要借“简洁”删掉权限、审批、数据权威和最终验证。

### 3.7 Cline / Aider：计划是用户能审阅的产物

外部事实：Cline 提供 Plan/Act；Aider architect 模式将方案与编辑拆成两个阶段，可能使用不同模型，也可能同一模型分两次调用。[Cline 工作方式](https://github.com/cline/cline#plan-and-act)、[Aider 模式](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/usage/modes.md)。

Sprout 现状：已有 PLAN 节点与 code/read_only 类型。没有必要再新增一个混淆写权限的“plan 任务类型”。

建议改法：计划持久化为可审阅内容：目标、范围、相关文件、风险、需求到用例映射、PoC 命令、预算；小任务不额外等待，大风险/需求含糊时才要求确认。计划改变需版本化，不让实现阶段暗中改掉冻结期望。

先用同一个模型分阶段，再依据固定评测考虑 planner/editor 两模型。Plan 的批准也不替代 APPLY 前审批和验证。

修改位置：`strategy/`、`runtime/nodes.py`、`orchestration/models.py`，CLI/Web 展示层。

验收：用户能知道“为什么改这些文件”；计划范围扩大时显式处理；read_only 没有因计划中提到修改而取得写工具。

### 3.8 SWE-bench：评估真正修复了问题，而不是流程跑完了

外部事实：SWE-bench 在隔离环境应用补丁并执行项目测试；报告区分 resolved/unresolved/错误/未完成。grading 源码按用例判定，并检查缺输出、跳过和退出码等情况。[评测说明](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/evaluation.md)、[grading 源码](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/grading.py)。

Sprout 应借的不是榜单分数，而是**固定任务、独立结果、完整证据**：

- 修 bug：受影响用例在基线失败、补丁后通过；原有通过用例保持通过。
- 新功能：在适用场景下新验收能区分缺功能基线与实现；不能把任意 import 错误都当有效需求失败。
- 结构分析：人工标注的事实、位置和 unresolved，与输出做 precision/recall 或类型覆盖对照。
- 恢复/权限：看重复副作用、越权次数和状态准确性，而不是模型语言流畅度。
- 基线本身损坏时记 infrastructure/baseline-invalid；不能冒充任务改坏，也不能从结果中默默删除。

模型可看到需求和允许的测试，但独立评测期望不由被评估实现重写。冻结文件摘要仍需配合防改测试配置、运行环境和报告的边界；不能让补丁通过篡改 runner 得分。

修改位置：现有 `tests/` 中的回归基础、`execution/verification.py`、`trajectory/`；建议新增独立 `evals/` 作为任务数据与 runner，不作为新的业务编排框架。

不立即引入完整 SWE-bench 数据集/Docker 镜像集合：先 10 个内部任务即可找出瓶颈。全榜单结果不能当产品需求正确性的保证。

## 4. 哪些现在做，哪些只保留候选

| 机制 | 优先级 | 主要价值 | 实施代价（相对） | 决策 |
|---|---|---|---|---|
| 固定任务 eval + 失败分类 | 第一批 | 知道每次修改是否有效 | 中 | 现在先做 |
| 独立基线/回归/PoC 证据报告 | 第一批 | 防止“跑完即成功” | 中 | 扩展现有验收，不重写门禁 |
| 提交幂等与恢复契约 | 第一批 | 修复重复/模糊执行风险 | 中高 | 从故障测试开始 |
| 执行任务只读状态/证据视图 | 第二批 | 操作者知道停在哪里 | 中 | 先查询契约，后完整 UI |
| 预算内 repo map selector | 第二批 | 少读错代码、降低无关上下文 | 中 | 用现有项目图，评测驱动 |
| CLI/HTTP VerificationAdapter | 第二批 | 超出 Python callable 的闭环 | 中高 | 先 CLI，再按目标决定 HTTP |
| 容器执行环境 | 条件前置 | 隔离生成代码与宿主 | 高 | 外部/无人值守前必做 |
| Planner/Editor 两模型 | 后续实验 | 计划与编辑分工 | 中，调用成本增加 | 有数据再开 |
| 并行多 Agent、自动进化发布 | 暂缓 | 大任务协作/经验复用 | 高且安全复杂 | 先修好恢复与独立评测 |

排序按 Sprout 当前缺口，不是按外部项目名气。代价不是工期承诺；实现前需按具体范围拆任务。

## 5. 第一批评测：只选 10 个，不求大

以下是拟建数据集，不是已有测试或已经通过的指标。

| 编号 | 任务 | 评测重点 |
|---|---|---|
| A01 | 分析一个有模块依赖的 Python 小项目 | 目标符号/导入边与来源正确，缓存 round-trip 一致 |
| A02 | 分析 include_router 前缀与动态未知路由 | 静态路径正确；动态项保持 unresolved |
| A03 | 分析 ORM 和 SQL 声明 | 不混淆源码/live schema，不伪造字段，不执行项目代码 |
| C01 | 修复一个函数边界条件 bug | 基线 fail→patch pass，原回归不退化 |
| C02 | 新增 JSON 输入输出函数 | 冻结用例、正常/边界/非法输入和独立 PoC |
| C03 | 跨两个模块做小改动 | 文件/接口定位准确，变更范围符合计划 |
| C04 | 面对错误实现和缺失测试输出 | 准确失败，不能 completed |
| C05 | 未验证变更经人工强制应用 | 有原因与证据，applied 但不宣称 verified/completed |
| R01 | 审批等待时重启并多次恢复 | 节点可恢复、批准范围正确、不重复应用 |
| R02 | 只读任务诱导写入与拒绝删除 | 零越权写入；删除拒绝维持等待可重试/取消 |

分析任务需要人工整理的 ground truth；编码任务的期望测试独立于 Agent 补丁；所有写入只作用于专用 fixture/worktree。不能拿真实用户项目当破坏性评测场。

需要两类运行：

- FakeModel/固定工具输出：CI 中便宜、稳定地检测状态和安全链路。
- 真实模型：显式启动、有成本上限，用于测代码定位和解题能力；记录模型/提示版本，多次运行，不能用单次成功宣称稳定。

建议报告：任务总数、有效执行数、完成数、验收通过数、基线无效数、等待批准数、阶段失败原因、平均成本/耗时、模型/环境/源码版本。发布样例应做到安全违规与假阳性完成为零；其他质量指标先测基线，再确定目标。

## 6. 后续修改顺序：按里程碑，不再按“继续加功能”

### M0：可复现的可信本地基线

只做：默认测试/依赖漂移整理、10 个 eval fixture 的契约和报告、Python 编码 Demo 的完整证据包。

证据包包含：输入需求、项目 revision、计划、冻结验收摘要、补丁摘要、测试逐项结果、PoC、审批、应用/豁免状态、轨迹。源码补丁、验收和实际执行结果必须能对应同一工作树版本。

通过标准：干净环境可运行；已有 pytest 不退化；固定场景可复现；故意错误实现不能被标完成。此阶段不扩数据库、不新增 Agent 框架、不自动批准成长候选。

### M1：把已有链路的一致性补齐

只做：提交幂等、批准后恢复、成长缺测量/发布失败处理、项目知识权威纠正、生命周期兜底、统一 Task 查询。

成长暂作为“轨迹→建议→人工审阅”，可执行产物必须有对应验证；unknown 不代表 validated。真正独立 replay 应创建新 run 与隔离环境，不能复用原任务已完成节点来假装实验已跑。

通过标准：断连/崩溃/重试无重复应用；发布失败不留下 PUBLISHED 幻象；项目知识不串项目；CLI/Web 同一任务口径一致。

### M2：用数据选择能力扩展

看 M0 报告再决定：

- 定位错误多 → 先 repo map selector 与精确分析。
- 代码经常编译/测试失败 → 先快速反馈和错误定位。
- 任务被验收类型限制 → 先 CLI/HTTP adapter，不放宽门禁。
- 状态混乱/重复动作 → 继续恢复与幂等，不做并行 Agent。
- 外部/无人值守需求确定 → 隔离 backend 前置。

每次只改变一个主要变量，用同一 fixture 和模型配置对照。架构拆分服务是为这些职责服务，不以新建目录数量作为成果。

## 7. 下一次可以直接开工的三个任务

### T01：统一 VerificationReport 与失败分类

修改 `execution/models.py`、`execution/verification.py` 和节点结果记录，兼容现有 TestResult；先添加 tests，覆盖“未执行、跳过、runner 异常、期望失败、PoC 失败、基线损坏、强制应用”。

交付：可查询结构化验收报告；不改变用户已确认的豁免和完成标准。无需先讨论模型选择。

### T02：建立 10 个内部 eval 任务与单个 Python 闭环样例

实现独立 fixture/runner，把现有公开 Runtime API、项目分析和验证报告串起来；输出摘要与证据，不访问真实账号、不自动安装项目依赖、不污染项目根目录。

交付：一条显式 eval 命令（名称实施时再确定）、基线结果、失败阶段分布。这是评测工具，不再造第二条编码业务链路。

### T03：Temporal 提交失败窗口与恢复测试

先用 fake Temporal client 注入 connect/start 超时与重复调用，证明当前问题；再引入持久提交与幂等 dispatch。单元契约通过后才申请真实服务联调。

交付：提交返回值、权威状态和实际执行一致；本地与 Temporal 不同时担当同一任务的执行权威。

依赖关系：T01 → T02；T03 的测试可独立设计，但实施要遵守同一报告/状态契约。这里是任务拆分建议，不代表授权同时开多个 Agent 或已开始改代码。

## 8. 明确不直接照搬的内容

- 不把 LangGraph 加进现有 Local/Temporal 上方；增加框架不自动解决副作用幂等。
- 不整体替换为 OpenHands；可先通过环境适配器验证必要性，避免丢失 Sprout 的项目权威和审批语义。
- 不把 mini Agent 的“提交最终输出”当验证成功，也不移植任意 shell 作为默认受信工具。
- 不照搬自动批准、自动发布技能；沿用现有人工决定，并修补独立实验验证。
- 不让 repo map 成为第二项目事实权威；它只是现有图的任务定向视图。
- 不马上追求通用多 Agent 团队或完整 SWE-bench 分数；先证明单 Agent 小改动可靠。
- 不立即更换 SQLite/加入新的向量或图数据库：当前问题优先在事实精度、权威与消费链路。

其他开源项目并没有一套可直接拷入、恰好匹配 Sprout 五库项目本体的方案。接口 schema、ORM/SQL 完整分析仍需领域分析器；不能用语言模型摘要或 repo map 替代精确结构落库。

## 9. 复用方式与许可记录

默认采用“机制借鉴 + Sprout 内实现”，本次未复制外部业务代码、安装依赖或导入用户数据。确需摘取源码时，再核对具体 revision、文件头、第三方依赖与 NOTICE，保存来源和变更记录。

当前核对的仓库主许可：OpenHands SDK、LangGraph、mini-SWE-agent、SWE-bench 为 MIT；Aider、Cline 为 Apache-2.0。主许可不能代替逐文件/数据集/依赖核对。[OpenHands LICENSE](https://github.com/OpenHands/software-agent-sdk/blob/main/LICENSE)、[LangGraph LICENSE](https://github.com/langchain-ai/langgraph/blob/main/LICENSE)、[mini LICENSE](https://github.com/SWE-agent/mini-swe-agent/blob/main/LICENSE.md)、[SWE-bench LICENSE](https://github.com/SWE-bench/SWE-bench/blob/main/LICENSE)、[Aider LICENSE](https://github.com/Aider-AI/aider/blob/main/LICENSE.txt)、[Cline LICENSE](https://github.com/cline/cline/blob/main/LICENSE)。

## 10. 本地对应入口

- [已有架构审查](D:/dev/projects/sprout/Current-Architecture-and-Optimization-Review.md)：当前实现和剩余问题的完整基线。
- [ReadPlan](D:/dev/projects/sprout/src/Sprout/workspace/scanner.py)、[WorkspaceContext](D:/dev/projects/sprout/src/Sprout/context/workspace.py)、[项目存储契约](D:/dev/projects/sprout/src/Sprout/storage/contracts/projects.py)。
- [AgentLoop](D:/dev/projects/sprout/src/Sprout/agent/loop.py)、[验收规格](D:/dev/projects/sprout/src/Sprout/strategy/acceptance.py)、[共享完成门禁](D:/dev/projects/sprout/src/Sprout/execution/verification.py)。
- [队列](D:/dev/projects/sprout/src/Sprout/runtime/queue.py)、[执行图模型](D:/dev/projects/sprout/src/Sprout/orchestration/models.py)、[变更服务](D:/dev/projects/sprout/src/Sprout/runtime/changes.py)。
- [ToolResult](D:/dev/projects/sprout/src/Sprout/tools/result.py)、[轨迹模型](D:/dev/projects/sprout/src/Sprout/trajectory/models.py)、[候选发布](D:/dev/projects/sprout/src/Sprout/evolution/candidate_manager.py)。

一句话行动建议：**先做验收报告和固定任务评测，用数据找瓶颈；同时补安全与状态一致性，再借 Aider 的上下文选择和 OpenHands 的环境边界扩展能力。不要从全面重构或新增多 Agent 开始。**
