# 隔离编码边界接线

## 当前工作的所属模块

本阶段属于 evolution（成长候选验证），目前重点是其 CLI 实验入口整合，不是普通聊天任务入口改造。
目标是形成：候选收集 → 显式成对实验 → 隔离编码 → 容器外功能/回归判定
→ 可信验证记录 → 人工审批 → 发布。当前固定脚本模型下的实际 Runtime／Temporal／Docker
编码图与工具、进程审批恢复已完成专项联测。候选验证报告交接和受控发布/后续编码闭环已有测试；
普通实验入口复用专用 worker，工具/进程审批已有显式继续入口；跨 Runtime 恢复已实测，
独立 Python 进程 CLI 专项已通过：比较、审批继续、提交、测量、新 pair、显式候选验证由 28 个子进程完成。
模型/容器授权和代码审查决定仍为明确测试 fixture，不能视为生产策略、真实模型自主成长或效果收益验收。

## 显式 CLI 编码实验入口（2026-10-15）

`replay-compare` 新增 `--temporal-host`、`--temporal-namespace`。两者必须同时指定，
并搭配 `--coding-image`，仅支持显式 code 源任务。namespace 必须预先存在；命令不创建 namespace，
不默认连接 7233、不读取共享 TEMPORAL_HOST 来选择 worker，也不回退到本地执行。

```powershell
uv run python -m Sprout evolution replay-compare <candidate-id> <source-task-id> `
  --revision <immutable-commit> --output-dir <outside-source-directory> `
  --check "pytest" --regression "pytest" --oracle <frozen-test-directory> `
  --regression-spec <regression.json> --verification-image <pinned-verification-image> `
  --coding-image <pinned-coding-image> --temporal-host <dedicated-host:port> `
  --temporal-namespace <existing-experiment-namespace> --config <settings.toml>
```

这是实际执行入口，会使用配置中的模型并可能产生费用，不是演示模式。地址/namespace 缺一项时在创建实验前返回等待；
未指定这些参数仍保持既有未装配等待行为。共享 TEMPORAL_HOST 环境变量需移除后再执行。
每个基线/候选任务派生独立队列，复用 IsolatedCodingWorker、计划绑定会话及统一生命周期，
不调用通用 Runtime.start，不自动批准工具、镜像或代码变更，不自动提交 Git 或发布候选。

当前命令运行至既有等待/结束点后返回 pair manifest 和各实验 task_id/evidence_dir。
等待审批不等于实验完成：此时 measurement_status=not_measured，不能据此验证/发布成长候选。
工具/进程审批可按下节继续；代码审批/专用提交后的测量和 pair 更新使用后面的显式命令，
不可拿仅支持 pristine image-preflight 的 replay-resume 强行恢复已有执行节点。
独立 commit-* 入口继续保留，不因本次接线而自动批准。

本轮真实专项使用离线脚本模型和测试专用策略，基线/候选均经实际 Temporal、Docker、验收与 pytest，
候选通过真实 skill_view 读取正文；它证明入口接线，不证明生产策略自动放行或真实模型学习收益。

### 工具/进程审批后的显式继续（2026-10-15）

```powershell
# 在该实验库中查找当前节点的工具/进程审批请求；不是源项目任务库。
uv run python -m Sprout evolution replay-continue <isolated-task-id> `
  --database <experiment-directory>/metadata.db --config <original-settings.toml>

# 审阅返回的 tool/action_summary 后，明确批准这一项并继续。
uv run python -m Sprout evolution replay-continue <isolated-task-id> `
  --database <experiment-directory>/metadata.db --config <original-settings.toml> `
  --approve <exact-pending-approval-id>
```

没有待处理请求但已有批准决定时，第一条命令也会继续执行，可能产生模型费用。
地址、namespace、队列均取自原实验已接受的持久化提交，不另加调度覆盖参数。
重新打开五个既有实验数据库，复用公共 experiment_runtime；候选从源配置对应的权威库核对
ID、版本、正文哈希后恢复到隔离注册表，再校验原计划配置/运行时快照。
只继续原任务的 TEST_DESIGN/AGENT/EVALUATION 审批等待，复用 Runtime.resume_task 的持久化节点
和待执行工具调用，不创建新实验，不清理未知执行占用。

到达代码 APPROVAL/APPLY 时，仅返回 proposal_id 和 commit-* 指引，不自动批准或应用；
向此类等待传 --approve 会被拒绝。取消、已完成、运行中或资源等待不由此入口重新激活。
pristine image-preflight 仍属于旧 replay-resume 的窄范围，此次未扩大其恢复权限。
源候选已变更或原配置不匹配时，需要明确处理差异，不会自动用新版本替换原实验。

真实专项包含基线、候选工具审批、候选进程审批三支：原 worker/Runtime 关闭后，
重建 Runtime 完成继续与验收，前序已完成节点和原队列保持不变；模型仍是离线脚本、策略是测试 fixture。
该路径现已补充独立进程 CLI 专项（范围见下节）。继续结果及首次报告仍是 not_measured，
专用提交完成后使用下节命令补做独立测量与 pair 更新，不可手改旧报告冒充验证。
原实验仅以内存注册的候选现已由公共 experiment_runtime 重建，独立 commit-request、commit-refresh、
commit-worker 与编码恢复复用 evolution.candidate_binding 的同一装配逻辑。
commit-* 没有现成源 metadata 句柄时，按 --config 创建未启动的源 Runtime，只读取精确候选后关闭其存储；
不调用源模型、不运行源任务、不复制审批或启动后台 worker。传入的 config 必须指向原源候选库，
不能随意替换成缺少候选的实验配置；原候选缺失或正文/版本变化会阻止装配，不改写原计划快照。
真实专项已验证“内存候选 → 工具审批恢复 → 独立提交 worker → 实验 Git 提交 → 任务 completed”，
源 Git 不变、候选仍为 candidate。原专项是跨 Runtime 服务测试；后续独立进程专项另见下节，均不代表成长效果证明。

### 提交完成后的独立测量与新 pair（2026-10-15）

```powershell
uv run python -m Sprout evolution replay-measure <baseline-task-id> `
  --database <baseline-directory>/metadata.db --config <original-settings.toml>
uv run python -m Sprout evolution replay-measure <candidate-task-id> `
  --database <candidate-directory>/metadata.db --config <original-settings.toml>
uv run python -m Sprout evolution comparison-refresh <original-pair.json>
# 使用上一步返回的新 pair_manifest，而不是原等待 pair。
uv run python -m Sprout evolution candidate-validate <candidate-artifact-id> `
  --pair <new-pair.json> --config <original-settings.toml>
```

replay-measure 要求可信 applied 提交及任务 completed，核对实验 Git HEAD 为完成记录中的提交且工作树干净，
恢复原候选并检查原配置/快照，再调用已有独立 Docker pytest、函数验收和回归测量；不运行模型、任务或 Git 写入。
原计划的冻结 oracle 必须已包含与 acceptance_spec 相同的生成验收测试；缺失不能事后修改冻结基准来凑成功，
应按返回的等待原因准备正确的新实验。配置或程序版本变更仍会触发既有绑定检查。

测量进程需要批准时会返回当前任务 pending_approvals 的摘要，可审阅后再次调用 replay-measure 加
`--approve <exact-process-approval-id>`。只批准指定的当前任务/操作者/workspace 的 process_run，
不批准代码或 Git。未知执行仍由既有持久化验证占用阻止自动重跑。

首次等待报告 independent-measurement.json 保留不动，完成后报告以独占创建写入
independent-measurement-completed.json。资源等待不封存为最终成功；重复已封存的测量只返回缓存，
不重新运行容器、不覆盖证据。真实失败也保留为观察结果，后续验证门槛会拒绝失败结果。
comparison-refresh 读取同一对原实验的完成后报告，生成全新 growth-comparison-*.json，旧 pair 不变；
comparison-report 接受这两个固定测量文件名，仍从原数据库重新核对证据，不信任 JSON 中的通过布尔值。

服务级真实专项已覆盖同一源任务/修订的基线与候选，分别工具审批恢复、独立提交到 completed、Docker 独立测量、
新 pair 的全部观测门槛及显式 validate_comparison 进入 validated。未调用真实付费模型，未自动发布，
不证明候选相对基线的实际改进；发布仍需单独明确操作。

### 独立 Python 进程 CLI 验收（2026-10-15）

```powershell
$env:SPROUT_TEMPORAL_TEST_HOST = '127.0.0.1:17233'
$env:SPROUT_CODING_TEST_IMAGE = 'sha256:11880faa03b5dda1f3f54a915ad1b0e3d2a97664799d7a9bfcd3790a9dc7a31d'
$env:SPROUT_VERIFICATION_TEST_IMAGE = 'sha256:0bf005068f7c22adbb6f4dcc436eef9b89d8708a5cfd993fa2fbac7480df9152'
$cliAcceptanceRun = '.workbuddy/test-runs/independent-cli-' + [guid]::NewGuid().ToString('N')
uv run pytest -q -p no:cacheprovider --basetemp=$cliAcceptanceRun src/Sprout/tests/test_evolution_cli_process_integration.py
```

要求本地已有两种固定镜像和专用 17233 Temporal；不自动 pull/build，不连接默认生产 7233。
测试新建源仓库、候选、源任务、真实 source.toml 和独立实验。每条短命令通过新 Python 子进程执行，
从文件配置和既有数据库恢复，不从父进程传递 Runtime、模型对象、内存计数器或审批对象。
28 个子进程覆盖 replay-compare、两侧 replay-continue、commit-request/review/decide/submit/worker/status/finalize、
replay-measure 及重复缓存、comparison-refresh/report、candidate-validate。
两个提交 worker 均在测试刺激的真实 SIGINT 后正常退出，退出后再用独立 CLI 检查 applied/completed。
SIGINT 刺激复用测试 harness，不等于物理终端键盘或 Windows 控制台投递已经验收。

必须区分的替身：编码/测量子进程使用 tests.offline_evolution_cli_harness，选择固定离线脚本模型和
容器测试授权策略，编码工具仍经过真实持久化审批。提交、状态、报告、候选验证运行原 CLI；
代码提案审查批准由父测试显式写 fixture 决定，不宣称代码审查交互入口或生产授权策略已验收。
独立进程只共享持久化文件/数据库和实际 Temporal 服务；不调用付费模型，不自动发布。

2026-10-15 实际结果为 1 passed（73.50s），候选 validated、published=false，源任务未执行、源 Git 未改变。
旧等待报告/旧 pair 字节保留，缓存测量不重写完成报告；仅移除精确记录的本测试已完成且停止容器。
验收记录保存在测试目录的 independent-cli-acceptance.json，记录命令、子进程 PID/退出码与替身范围。
本轮未发现需要修改生产代码的新断点；下一阶段应选择小型真实任务试用，先明确模型、任务范围和费用上限，
不默认继续扩展底层安全机制，也不把此离线专项当作真实模型能力或成长收益结论。

## Git 与成长发布的阻断范围及解除条件

### 一次运行的受控成长演示

前提：Docker 已启动，专用测试 Temporal 在 127.0.0.1:17233，下面两个固定镜像已在本机。
测试不会自动拉取镜像，不调用外部模型，不改用户实际技能目录或提交当前项目。

```powershell
$env:SPROUT_TEMPORAL_TEST_HOST = '127.0.0.1:17233'
$env:SPROUT_CODING_TEST_IMAGE = 'sha256:11880faa03b5dda1f3f54a915ad1b0e3d2a97664799d7a9bfcd3790a9dc7a31d'
$env:SPROUT_VERIFICATION_TEST_IMAGE = 'sha256:0bf005068f7c22adbb6f4dcc436eef9b89d8708a5cfd993fa2fbac7480df9152'
$growthDemoRun = '.workbuddy/test-runs/growth-demo-' + [guid]::NewGuid().ToString('N')
uv run pytest -q -p no:cacheprovider --basetemp=$growthDemoRun src/Sprout/tests/test_controlled_growth_demo.py
```

流程：受控成长信号 → 实际候选构建器 → 真实成对 Docker 测量 → 独立 CLI 验证与批准发布
→ 从该次发布文件读入技能 → 新的 Temporal/Docker 编码任务读取精确正文
→ 测试/功能/回归/demo → 审查、提交与结果查询。

运行目录的 test_controlled_growth_demo0/controlled-growth-demo.json 关联候选/信号 ID、
成对测量任务/文件、实际发布状态、后续编码任务、技能正文哈希和结果提交。
同一技能正文在发布与后续任务中哈希相同；不是把两个独立的成功标志拼在一起。
起始信号/证据引用和编码模型仍是测试夹具，不是生产轨迹自动学习或收益实验。
当前统一演示已进一步验证后续任务 task_status=completed、coding_task_completed=true。
原任务审批/应用节点与跳过的后续验证轮次已收尾；历史 Temporal 等待结果不被改写，
任务 metadata 的 isolated_commit_completion 记录是哪次提交完成了原图义务。
这是受控功能闭环验收，仍不是生产轨迹自主学习或真实模型收益证明。

### 成对测量交接到候选验证状态

```powershell
uv run sprout evolution comparison-report <growth-comparison.json>
uv run sprout evolution candidate-validate <exact-artifact-id> --pair <growth-comparison.json> --config <source-settings-file>
uv run sprout evolution candidate-report <exact-artifact-id> --config <source-settings-file>
```

前提是已有独立 baseline/candidate 两侧的完整测量，不是只有一个 Git applied 结果。
candidate-validate 复核既有计划与执行证据、功能/回归输入输出，再将验证记录写回源候选库；
--config 是源候选存储配置，不是实验 metadata.db。成功返回 validated，未自动批准或发布。
缺项返回 waiting_validation 和 blockers，继续补齐实际测量，不修改候选状态。
comparison-report 保持只读，其 validation_authorized=false 不会因新入口变成写入授权；
新入口负责显式交接，并将原只读报告作为验证记录的一部分保留。
通过只表示既定测试/功能/回归条件满足，不等于已经证明候选更优。
真实双实验 Docker 测量到独立 CLI 验证交接已通过成功/回归失败两个场景：
成功写入 validated，回归失败保留 candidate；均不自动发布。测试为预置代码与受控完成任务，
不包含模型编码，不证明技能改善效果，尚未与 Temporal 编码/提交链路合成完整成长流程。

### 验证后人工批准发布与重启加载

```powershell
uv run sprout evolution candidate-report <exact-artifact-id> --config <source-settings-file>
uv run sprout evolution approve-candidate <exact-artifact-id> --config <source-settings-file>
uv run sprout evolution candidate-report <exact-artifact-id> --config <source-settings-file>
```

注意：approve-candidate 的现有语义就是批准并发布，不是只保存审批意见；不要将它当作
预览命令。成功输出 published，candidate-report 中 publication.state=completed。
技能写到该源配置 skills_dir，下次用同一配置装配 Runtime 后可以加载，不需要自动改源码。
未验证或回归失败的候选不能发布；若 publication 状态不明，保留记录核对，不重复批准。
真实双实验联测已覆盖独立 CLI 批准、技能落盘、新 Runtime 加载及 AgentContext 可见性，
并核对重启后内容/version 不变。随后真实 Runtime.handle 对话会将 L0 技能索引交给模型，
离线脚本模型调用实际 skill_view 工具并收到精确完整正文，再生成回复；无技能对照不会
获得正文。已验证技能消费链路，但模型是脚本夹具、任务是会话，不是新的隔离编码任务，
不代表已经证明真实模型能力、编码质量或成本收益提升。

### 隔离编码节点中的技能消费

隔离 AGENT 节点现在把已加载且启用的 trusted 技能传入节点上下文，提供节点局部的
skill_view 正文读取；不继承在线会话历史，不开放技能资源目录读取。
若节点显式 tool_names 不包含 skill_view，则不注入该技能索引和工具，避免死入口。
普通任务和其他节点未在本轮扩大工具范围。

真实 Temporal/Docker 编码联测增加“已发布技能文件”夹具：模型先从技能索引发起
skill_view、收到精确正文，再调用隔离编码命令，进入原有 pytest/功能/回归/demo 与提交链路。
它与无技能场景使用相同需求和用例，但仍是固定脚本模型，不应当作质量提升的统计对比。
此测试未把前面的候选采集/成对验证/人工发布与后续编码连成同一个完整用户流程。

### 最短操作流程（从隔离编码提案已产生开始）

前提：已有 V5 code 实验、已接受的编码调度记录、完整验证证据，以及通过代码审查的
proposal。保存原实验配置；Docker 和该实验绑定的 Temporal 服务需可用。
本流程不是从零启动成长实验，也不会代替代码审查或生成成长发布报告。

1. 运行 commit-request，填写 task/proposal ID、metadata.db、原配置与提交说明。
   成功记录 request_id；若返回 waiting_*，按下表处理，不继续派发。
2. 运行 commit-review 查看该 request_id，记录 decision_fingerprint。
3. 运行 commit-decide，在终端明确输入 approve；脚本模式显式提供决定和指纹。
4. 终端 A 运行 commit-worker，保持前台运行。
5. 终端 B 运行 commit-submit，再用 commit-status 查询同一 request_id。
6. 查看 applied、result_commit 和 task_completed=true；正常提交会自动收尾任务图。
   之后可在终端 A 按 Ctrl+C。accepted 只是调度接收，applied 也不是成长验证/发布完成。
   若 applied 已确认但 task_completed=false，按下述数据库收尾入口恢复，不重复 Git。

| 遇到的情况 | 下一步 |
| --- | --- |
| git_identity_missing | 重跑创建，显式填写作者姓名/邮箱，不用改项目代码 |
| waiting_code_review | 先完成原提案的代码审查；提交待办不代替它 |
| evaluation_selection_required | 从返回候选选择节点，创建时指定 --evaluation-node-id |
| expired | 运行 commit-refresh，使用返回的新 ID 重新查看、确认和派发 |
| 配置/快照不符或缺库 | 使用原实验配置和完整实验目录，不新建空库顶替 |
| accepted_waiting_execution | 检查对应 worker 是否仍在运行；不要另建待办重复提交 |
| delivery_unconfirmed | 保留同一请求核对调度状态，不换 ID 猜测重跑 |
| needs_reconciliation | 保留实验目录与记录，核对实际结果；不能用刷新解锁 |
| applied 且 task_completed=false | 用 commit-finalize 只恢复任务图收尾，不能重跑 Git |

验证范围：六个短命令和提交 worker 已在独立 Python 进程进入真实 Temporal/Docker
联测，使用实际 TOML 文件、脚本模型与测试审批策略。仅最初过期待办用父进程测试时钟
创建；后续命令不注入配置。测试专用 harness 在 worker 子进程事件循环线程注入真实
SIGINT，正常输出 stopped 并以 0 退出，再由独立命令查询 applied。
这验证 Python 信号退出路径，不证明物理终端按键或所有生产配置/候选注册组合。

### 查看提交待办与执行状态

```powershell
uv run sprout evolution commit-status <task-id> --database <experiment>/metadata.db
uv run sprout evolution commit-status <task-id> --database <experiment>/metadata.db --request-id <request-id>
```

命令仅按当前 OS CLI 创建者读取既有 metadata.db 和同目录 operational.db，
不会创建数据库、启动 Runtime/worker、连接 Temporal/Docker 或修改任何审批。
缺少数据库/新表或身份不符会报错，不初始化或自动修复。

| 状态 | 含义 |
| --- | --- |
| awaiting_confirmation | 等待用户确认 |
| waiting_schedule | 决定已保存，未确认调度接收 |
| delivery_unconfirmed | 投递结果不明，不能理解为未执行 |
| accepted_waiting_execution | 调度已接收，数据库中尚无可信完成记录 |
| executing_or_unconfirmed | 存在活动占用，不能单靠时间判断是否仍在执行 |
| needs_reconciliation | 未知占用或完成证据不一致，需人工核对 |
| applied | 节点和 Git 事务均有匹配完成记录，仅实验提案已应用 |
| expired / rejected | 决定请求过期 / 用户拒绝 |

报告是只读观察，不是跨数据库全局原子快照，也不是重试/执行授权。
applied 不代表任务 completed，更不代表成长候选已验证或已发布。

### Git 已成功后的任务图收尾恢复

```powershell
uv run sprout evolution commit-finalize <task-id> <request-id> --database <experiment>/metadata.db
```

正常提交在可信 APPLY 与 Git 事务完成后自动调用同一收尾逻辑：复核原图、已通过验证、
已应用提案及结果提交，在一个数据库事务里结算原审批/应用节点、跳过未执行修复轮次，
再呈现任务 completed。历史 Temporal 工作流的 waiting_approval 结果保留不变。
若收尾中断，CLI 只打开已有数据库，不装配 Runtime、不连接 Temporal、不执行 Git；
重复调用复用完成记录，暂停/取消/缺少完整原图时返回未完成，不把任务强制改成 completed。
task_completed 与 applied 分开展示；此步骤不会把成长候选标为 validated/published，
也不自动删除实验目录或沙箱证据。

### 查看并确认已保存的提交待办

先从实际实验提案创建待办：

```powershell
uv run sprout evolution commit-request <task-id> <proposal-id> --database <experiment>/metadata.db --config <original-settings-file> --message "提交说明"
```

缺 Git 身份时可显式加 --author-name 和 --author-email，仅绑定本次实验，不写配置。
验证节点不唯一时按等待提示加 --evaluation-node-id。代码审查提案未批准则继续等待，
命令不批准提案、不启动 worker、不投递或提交 Git。成功返回 request_id 供下面步骤使用。
重复同一审查返回原 ID/原期限，不会自动续期；过期待办使用下述显式刷新命令。

```powershell
uv run sprout evolution commit-refresh <task-id> <old-request-id> --database <experiment>/metadata.db --config <original-settings-file> --expected-fingerprint <old-decision-fingerprint>
```

仅已过期、未拒绝、无派发记录与执行占用的待办可刷新。重新检查当前证据，
创建 supersedes 关联旧 ID 的新待办；旧记录/批准不变，新待办必须重新 review/decide。
同一旧待办最多派生一个固定新 ID，重复刷新不延长期限；新待办再次过期时，
显式刷新该新 ID。新审查内容若与已生成子待办冲突则拒绝覆盖，不自动续期。
已派发/结果未知需要核对，不能借刷新重复执行。

```powershell
uv run sprout evolution commit-review <task-id> <request-id> --database <experiment>/metadata.db
uv run sprout evolution commit-decide <task-id> <request-id> --database <experiment>/metadata.db
```

第二条命令在真实终端展示冻结的审查内容，输入 approve/reject/cancel，默认 cancel。
取消不保存决定；approve 仅保存明确决定和 waiting_schedule 意图，不派发、不启动 worker、
不在 CLI 执行 Git。reject 不生成派发意图，也不取消任务。

非交互模式必须显式提供两项，指纹使用 commit-review 返回的 decision_fingerprint：

```powershell
uv run sprout evolution commit-decide <task-id> <request-id> --database <experiment>/metadata.db --decision approve --expected-fingerprint <decision-fingerprint> --reason "审查说明"
```

审查展示采用凭据模式脱敏（display_redacted=true 表示有脱敏），原始审查内容与指纹不变。
存储仅以 mode=rw 打开已经存在的 operational.db，不创建数据库、不运行 schema 迁移。
其他操作者、错误指纹、过期/已决定待办都会拒绝；补齐/更新内容须重新生成审查待办，
不得手改数据库绕过占用。显式创建、刷新及前台提交 worker 已接入；自动派发未开放。

### 显式派发已批准的待办

```powershell
uv run sprout evolution commit-submit <task-id> <request-id> --database <experiment>/metadata.db
```

派发只能使用该实验已接收的编码提交记录中的唯一 Temporal 地址/namespace，
并核对队列必须由当前 V5 plan/任务派生。不接受 --host/--namespace/--queue 重定向参数，
没有可信记录或存在冲突就等待人工核对，不回退默认地址或本地执行。
新审查冻结 routing_profile=isolated-commit-v1，提交使用独立的 commit-v1 队列；
历史无标记待办保留原编码队列，不自动迁移，也不改写已有批准或派发记录。
可用 --expected-fingerprint 再核对批准待办的 decision_fingerprint。

未批准、过期、任务取消/暂停或未知执行占用不会产生新的投递；
已确认投递复用固定 workflow ID/run ID，重复 ID 必须核对服务端绑定和原始输入。
输出 accepted 只表示调度确认，仍须用 commit-status 查看是否已有可信 applied 结果。
命令不会启动 worker、修改源码或在本地执行 Git；worker 离线时可能被服务器接收但尚未执行。

新提交 worker 使用独立 Runtime，仅注册提交工作流和活动，不安装编码工具或会话工厂，
也不调用通用 Runtime.start（不触发其清理、巡检与 outbox 循环）。编码 worker 的
dispatcher 要求保持不变；两者复用计划、存储和证据验证核心，不共享可变执行状态。
当前已完成类和真实联测，完整双 worker 生命周期监督尚未完成。
内部已补充 run_owned_workers 的资源接管与退出管理：同实验不同队列使用独立 Runtime，
先等待 worker 退出，再关闭实验存储，拒绝重复接管与通用已启动 Runtime。
提交 worker 已接入真实 Temporal/Docker 联测，验证管理器取消退出后仍可读取
持久化 applied 结果；编码 worker 尚未接入统一管理器。提交前台入口见下节。

### 显式前台启动提交 worker

```powershell
uv run sprout evolution commit-worker <task-id> <request-id> --database <experiment>/metadata.db --config <original-settings-file>
```

这是独立提交队列的前台服务入口，保持终端运行；另一终端使用 commit-submit 投递、
commit-status 查询。Ctrl+C 只停止本地 worker 并关闭其存储，不取消服务器工作流，
不清理未知占用，不撤销已有审批。启动不代表已连接就绪，也不自动提交任何新工作流。
命令从可信记录恢复 endpoint/namespace、从冻结计划绑定镜像，不接受路由或身份覆盖；
要求五个既有实验数据库和原配置，旧无标记待办不能用该入口迁移。
仍使用通用工厂装配但不调用 Runtime.start，模型配置/凭据初始化可能仍有要求，
不会因此换成默认模型或更改配置。候选注册/运行时快照无法重建时保守拒绝，
当前真实入口验收为脚本模型的基础实验，不代表生产候选启动均可用。

### Git 作者身份缺失时如何继续

内部提交协调器现已串联占用、审批消费、一次性启动、Git 写入与联合结算，
用户提交入口见前文；真实 Runtime/Temporal/Docker 证据准入到实验提交已在
测试自建仓库通过（脚本模型、测试审批策略、固定 smoke 镜像），不是生产信任验收。
以下身份命令继续仅输出待审批预览，不会调用该协调器或自动提交。

专用提交工作流/活动现已注册在独立实验 worker，真实测试覆盖持久化决定、
服务端重复投递绑定核对与实验 Git 提交；活动关闭自动重试，未知占用不重跑。
它已接创建/刷新/只读/确认/显式派发和前台提交 worker CLI，但没有后台轮询，
也不包含从零启动编码实验，不能把注册理解为生产自动应用已开启。

也可以不修改项目配置，通过当前实验的交互入口补齐：

```powershell
sprout evolution commit-identity --replay-plan <replay.json> --git-plan <commit-plan.json> --message "说明" --interactive
```

已有姓名/邮箱优先沿用，缺失项提示输入；也支持 --author-name / --author-email 显式指定。
这些输入仅用于当前计划的待审批提交信息，不修改源码、项目或全局 Git 配置。
此命令读取已有计划文件并输出 contract 预览，不会保存授权、提交代码或恢复任务；
实际写入协调器仍须核验真实计划、消费精确审批并记录事务，不能信任预览文件作为授权。

用户已确认沿用操作者已有 Git 身份，缺失时等待，不虚构身份、不修改全局配置。
只读解析按原项目的 Git 配置层级读取 user.name/user.email，将姓名、邮箱和提交说明
冻结进精确审批的 write contract；作者或说明变更必须重新审批，旧 v1 检查不能授权新提交。

缺失不是无提示的无限等待：结构化诊断提供缺少字段、本地项目配置 argv、重新检查与
取消选项。操作者可自行在原项目执行（替换目录和姓名/邮箱）：

```powershell
git -C "原项目目录" config --local user.name "你的姓名"
git -C "原项目目录" config --local user.email "你的邮箱"
```

随后重新读取身份、重新生成精确提交请求、显式审批/继续；程序不轮询自动重试。
当前身份解析/结构化提示已实现，专用 Git 提交与 CLI 恢复入口尚未接完；不得把旧的
replay-resume 或普通强制应用当成这个入口。身份缺失检查发生在写入前，不创建 Git 写入
占用；已有 active/uncertain 副作用不能通过补身份或重新检查自动解锁。

这里的阻断不是整个项目禁用 Git 或发布。普通任务保留原应用入口；CandidateManager
也保留对满足当前可信验证记录条件的候选执行审批/发布的入口。暂未开放的是这条新的
隔离成长实验直接应用/自动发布，不能把实验 demo 的 passed 当作候选改善或发布授权。

- 隔离 Git：已有计划、原始验证证据绑定、精确审批检查和持久化事务占用，实际写入协调器
  尚未实现。下一里程碑优先连接精确审批消耗、APPLY 节点 owner/事务占用、实验副本的
  单次 Git 操作、实际 commit/tree 核验和提案 CAS 状态提交；中断结果不明保留占用并阻止
  重试。先只应用实验副本，不新增修改原项目的权限。
- 成长发布：发布管理器及部分失败占用机制存在，但独立基线/候选测量与精确候选版本、
  内容指纹及可信 source run 的验证报告交接未完成。要先证明候选改善且无回归，生成
  候选的可信验证记录，再按原审批和发布事务机制处理；不是把任务 verified=True 复制
  成候选 approved/published。
- 临时阻断应随上述实现和专项验收逐项解除，不能用全局 ready=True、force 或普通应用
  后端回退代替。此清单是工程里程碑，不是永久禁止这两项功能。

宿主保留模型调用、审批、任务/节点状态、数据库、原项目与冻结预期输出。
容器只执行不可信代码/命令，唯一可写挂载为专用 coding-input；没有网络、
API 密钥、数据库挂载或 Docker socket。文件写入仍需通过受限的宿主 broker，
不能因命令容器化就跳过路径、删除审批和产物导入检查。

## 已确认的连接点

| 位置 | 当前职责 | 隔离实验需要的处理 |
| --- | --- | --- |
| evolution/replay_runner.py | 创建全新隔离任务并执行 | 装配专用执行策略，禁止普通宿主后端回退 |
| evolution/replay_resume.py | 恢复原 image-preflight 任务 | 重验同一策略/镜像/导出，不升级旧任务证据 |
| runtime/runtime.py: run_task_process | 独立测量授权进程入口 | 区分受信任 Docker 管理命令与候选代码命令 |
| runtime/nodes.py: 评估过程 | 直接调用 ProcessBroker.run | 接入相同隔离策略，不绕过 Runtime 层的限制 |
| sandbox/git_worktree.py: run_code | 直接启动语言解释器 | 对隔离实验禁止宿主解释器，只走编码容器 |
| execution/sandbox_tool.py: SandboxGitTool | 直接调用 Git | 使用受限、无仓库自定义程序的检查适配器；不能把它当普通进程遗漏 |
| execution/file_broker.py | 受限文件读写/删除 | 保留审批，限定代码导出范围；导入容器产物前拒绝链接/私有文件 |
| execution/coding_container.py | 专用容器命令规划器 | 已有规划，未完成调度、审批、持久占用接线 |
| execution/process_broker.py | 授权与有界输出 | 保留审批与限额；Docker 客户端终止不推断容器已停止 |
| storage/local/sqlite/metadata.py | 持久任务/节点/测量占用 | 不复用 completed-only 的 verification_executions 作为编码占用 |

## 接管顺序与验收条件

1. 先定义隔离实验的统一执行契约：固定编码镜像、指定代码导出、命令分类、
   当前任务/节点身份与授权来源。没有装配完整后端必须等待，不能调用普通宿主解释器。
2. 装配节点评估、沙箱 run_code 和代码相关进程调用；保留既有节点执行防重复权威，
   审核其是否足以绑定逐命令副作用，不凭空增加另一套可相互覆盖的占用记录。
3. 审计 Git 与文件路径：候选产物可写不等于可以写数据库/冻结文件；不可信 Git 配置、
   外部 diff/filter/hooks 与链接产物必须被拒绝或隔离。
4. 在 Runtime 接线完整后，才制作并显式批准独立编码镜像。镜像下载/构建另走工具审批，
   不复用测量镜像角色或自动 pull。
5. 实际测试：候选访问宿主数据库/预期输出失败；正常写代码成功；所有代码执行路径
   都走容器；镜像不可用保持等待；中断或未知副作用不会重跑；普通任务行为不变。
6. 以上通过后，才考虑可信 record_validation 交接，并继续保留人工批准和发布占用。

## 边界声明

目前文件隔离与 Docker 测量链路已存在，编码容器只有规划器。
本文件是审计与实施计划，不代表这些接线已经完成，也不授予重跑/发布权限。
Docker/宿主管理员属于信任根；本阶段并不承诺防御宿主 OS 账户被恶意接管。
