# 修复进度与验收边界

## 目标项目策略适配：移除 Sprout 路径/命令硬编码（2026-10-16）

- 新增只读 project_profile，Pipeline 统一供给路径与验证规划；默认测试不再写 src/Sprout/tests，slug 清洗，Ruff 仅有配置时建议检查目标根。Node 仅采用声明脚本，Go/Rust 依清单推导，未知项目提示确认配置。
- 修复平铺源文件、自定义目录测试与 .test.* 识别；排除规则使用目标根相对路径，.workbuddy 内独立实验不会因祖先名被全部排除。数据库专用规划仍不扫文件系统。
- 不再伪造 pytest -k API/数据库过滤条件，完整套件不冒称需求专项验证；Gateway/Worker/执行和审批边界未改。
- 83 项离线回归通过（5.27s），静态检查通过；无付费调用、未重跑旧节点、未提交项目。基础适配不代表所有多语言/包管理器/框架已支持。说明 Target-Project-Strategy-Adaptation.md；规划请求挤占下一步处理。

## 正常策略真实 DeepSeek 续验：预算止损与策略耦合定位（2026-10-16）

- 用户授权使用剩余五次，正常工厂/ModelOrchestrator/权限策略、正式 CLI/task-worker、专用 Temporal/独立 Git/SQLite；无模型或授权适配器。任务执行前设持久化预算5，输出上限8192，不改用户配置。
- 五次均 responded，PLAN4+TEST_DESIGN1，输入7030/输出2104 tokens；下一次调用被预算本地拒绝。任务 waiting_resource/budget_stopped，审批为空、编辑未执行、源文件不变，无Git应用/成长发布，原授权总额度已用完。
- 真实诊断 1 passed（23.40s），不是编码任务成功。发现策略默认测试路径硬编码 src/Sprout/tests、slug不清洗，以及默认 ruff check src 不基于目标仓库结构；规划已诱导模型准备给独立样例创建不必要的 Sprout 包/重复测试。
- 下一步先离线修目标项目适配/验证规划及规划调用占用，再考虑新的真实模型审批验收。证据 .workbuddy/test-runs/normal-policy-deepseek-1，说明 Normal-Policy-Usability-Check.md。旧未知占用不重跑，未提交当前项目。

## 正常生产策略下的正式 CLI 初检（2026-10-16）

- 不安装实验授权策略，正式 CLI/Worker、真实专用 Temporal/独立 SQLite/Git，以离线 echo 执行明确 increment 编码需求。
- read/sandbox/plan 正常，test_design 因 echo 不产出 cases JSON 而等待，报告/同步为 waiting_resource，审批列表为空、源文件不变。不是权限失败，也不是编码闭环成功。
- 诊断专项 1 passed（6.80s），付费请求 0，无审批决定/应用/发布；证据 .workbuddy/test-runs/normal-policy-cli-final-1，说明 Normal-Policy-Usability-Check.md。
- 暴露模型结构化输出错误诊断、可选验收输入与真实生产审批体验的后续工作；旧未知占用不自动恢复，真实模型付费续验需明确授权。

## 本轮分进程真实编码的隔离 Git 收尾（2026-10-16）

- 用户确认继续后复核 app.py/test_generated.py、验证证据及既有 Git 身份，通过已有 review/decision/submission/commit worker 服务完成，不直接 Git 提交或改库状态。
- task 6c6394b1-17f7-4f04-8f14-95a431807ae8 当前 completed，request 171017ea-e583-529a-9c5e-643d00cf92cd applied；结果提交 3fe2e3f1285b49130fabcbae401d99840c0ffe87。
- 目标实验仓库干净，original 仍为基线 94480eb6595b38814971a745a95bcba09c9368ca 且干净；当前项目未提交。新增模型请求 0，整轮 5/10 次，没有成长发布。
- commit-closure-report.json 保留新结果，原 live-coding-report.json 历史 waiting_approval 不改；validation_committed=false 不冒充新增验证，依据此前真实 evaluation 证据。说明见 Detached-Real-Coding-Acceptance.md。

## 真实 DeepSeek 独立进程编码验收（2026-10-16）

- 本轮用户授权最多 10 次、每次输出最多 8192 tokens；实际 5 次成功请求，输入 9725/输出 1123 tokens，剩余 5 次不自动使用。无金额硬封顶或账单核验。
- 真实 CLI coding-submit/worker/status/sync 分进程，专用 Temporal、真实 DeepSeek/AgentLoop/Docker、独立验收与 SQLite；1 passed（24.71s），Worker SIGINT 正常退出码 0，任务 waiting_approval，未应用 Git/发布。
- 模型修复 app.add 并新增三组七断言测试，报告五项检查通过、无失败，原样例仓库不变；已检查实际差异。仍使用显式实验授权策略，不冒称生产策略或普通 task-worker 编码验收。
- 证据 .workbuddy/test-runs/deepseek-detached-live-1/test_real_deepseek_minimal_cod0/live-coding-report.json；说明 Detached-Real-Coding-Acceptance.md。新增测试适配器与分进程模式，离线回归 15 passed、1 skipped，静态检查通过。

## 普通任务显式连接故障恢复（2026-10-16）

- 新增 retry-submission 公共操作及 SDK/CLI/Web/RPC 接线；只接受唯一 pending/connect、未确认接受、无执行节点的 waiting_resource 普通任务，使用原冻结 payload、workflow ID 与调度路由。
- 不重编任务、不接受新路由、不启动 Worker；连接仍失败保持等待。未知 start、无 phase 历史、已接受/完成及隔离实验拒绝，不把调度恢复变成执行副作用重跑。
- 真实专项 1 passed（8.67s），首次连接故障为测试进程注入，随后正式 CLI 到真实专用 Temporal/正式独立 Worker/观察/同步 completed，唯一原记录和两个活动；证据 .workbuddy/test-runs/pending-retry-real-1。
- 相关离线回归 49 passed（1.62s），静态检查通过，无付费请求；保留依赖弃用警告。未验证真实断网/服务重启、未知提交结果人工核对或隔离编码恢复，未提交项目。

## 普通任务 CLI 生命周期入口（2026-10-16）

- 新增 project task create/submit/report/observe/sync，显式 --config、JSON 输出；复用 Gateway 公共服务，create 绑定可信 OS 身份，submit 显式路由，不启动 Runtime/Worker，finally 关闭存储。
- 旧命令和历史身份规则保持不变，新入口不认领历史未认证任务，不自动关联 Web/CLI 身份；隔离成长实验仍走专用入口。
- 独立 CLI 全链路真实验收 1 passed（7.81s），专用 Temporal/SQLite/离线 echo，生产 task-worker、真实节点、重复调用不增加活动；SIGINT harness 正常收尾。证据 .workbuddy/test-runs/task-cli-process-real-1。
- 本地参数/错误/关闭/旧命令与 Gateway 回归 45 passed（3.73s），静态检查通过。无付费请求，未做生产模型/Docker 编码/Git 应用验收；待调度显式恢复尚未完成。

## 独立普通任务 Worker 正式入口（2026-10-16）

- 新增 orchestrator task-worker，强制显式配置和 Temporal 路由；仅注册普通执行 workflow/activity，不启动完整 Runtime、成长自动化、进程内队列或自动基础设施部署。
- 活动绑定进程私有 Runtime，不修改全局活动 Runtime；Worker 退出后关闭存储，连接失败同样关闭。不接受易失 metadata。旧通用 worker 与隔离 coding/commit worker 保持兼容。
- 正式 CLI 在真实专用 Temporal 上完成多进程提交/执行/观察/同步和 SIGINT 正常退出；两种 Worker 入口与相关回归 34 passed（13.23s），静态检查通过，无付费请求。
- 证据 .workbuddy/test-runs/task-worker-cli-final-1 下 gateway-acceptance.json；离线 echo/只读任务，不冒称 Docker 编码、Git 应用、物理键盘信号或生产服务守护验收。未提交项目。

## Gateway 真实多进程提交/执行/观察/同步验收（2026-10-16）

- 专用 17233 Temporal、新命名空间和队列、独立 SQLite/只读样例，SDK 提交进程先退出，独立 Worker 注册生产 workflow/activity，经真实 Runtime 执行 read/agent，后续独立客户端观察并显式同步 completed。
- 重复同步不推进、重复提交不重跑，真实调度历史恰好两个活动，节点占用均 completed；Worker 存活至显式停止且正常退出，源样例文件不变，保留 PID 与完整报告证据。
- 首轮真实专项 1 passed（6.18s）；带历史计数复验及相关回归 23 passed（6.75s），静态检查通过。证据 .workbuddy/test-runs/gateway-process-real-2 下 gateway-acceptance.json。
- 模型为离线 echo、付费请求为零；未运行 Docker 编码/Git 应用。Worker 是测试宿主管理的真实生产活动，不冒称正式 worker CLI/所有 HTTP 客户端的部署验收。原有一个 Starlette/AnyIO 弃用警告保留。

## 普通任务 Gateway 持久化提交（2026-10-16）

- SDK/Web/RPC 新增显式持久化提交入口，TaskActions 校验归属，Runtime 使用当前部署 Temporal backend 的 submit-only 派生客户端，不修改共享 backend，不启动 Worker/等待结果。
- 已有提交只返回原记录，不自动重试；无显式 backend、既有节点、非 created/ready 或隔离 replay 新提交拒绝。隔离 CLI coding-submit 与旧 RPC 进程内 task.submit 语义不变。
- 51 项离线回归通过（2.22s）：真实 SQLite/Runtime 与模拟 Temporal 客户端覆盖持久化、重复调用、不可用等待、准入及 HTTP 适配器。未调用付费模型，未做本轮真实 Temporal/Worker 联调。
- 尚未完成普通任务通用 CLI 接线、隔离提交服务完全抽离、显式待调度恢复；不把现有查询/重复提交冒称恢复入口。

## Gateway 显式结果同步（2026-10-16）

- 新增 TaskActions.sync，SDK/Web/RPC 接入，同步先校验任务归属再观察原提交；CLI 保留专用检查并复用共同观察/写入编排。
- Runtime/SQLite 复用已有 submission_sync 原子事务，不接受客户端结果，不启动 Worker/Git/发布，不改执行节点；不支持的存储明确拒绝同步。
- 48 项离线回归通过（2.22s），静态检查通过；真实 SQLite 验证同步和重复收尾，运行中/不可用/超时不写库。无付费模型、无本轮真实 Temporal/Docker 联调。
- 统一持久化提交尚未实现；旧 Runtime.submit_task / RPC task.submit 仍为进程内排队，不能冒称 Temporal 提交。

## Web/RPC 共享只读查询入口（2026-10-16）

- 新增 Web report/observe 路由及 RPC task.report/task.observe，共用 TaskQueries，不提交、同步或取消任务。
- Web 任务创建传递中间件身份；HTTP RPC 传递已认证 Web 身份或独立 RPC token 的固定操作者身份，不信任 JSON 自报身份。保留旧未认证开发入口行为，新查询单独拒绝未认证调用。
- 真实 SQLite Runtime 验证创建后两种 HTTP 入口报告一致；路由替身验证调度结果只读观察、外部身份拒绝和参数错误。相关离线回归 57 passed（5.09s），存在一个 Starlette/AnyIO 依赖弃用警告。
- 没有付费调用，没有本轮真实 Temporal/Docker 验收；未改变隔离 Git/成长发布边界，未提交当前项目。

## Gateway 共享预算与阶段投影（2026-10-16）

- 提取 task.reporting 纯投影，CLI coding-report 与 Gateway 复用预算统计、阶段及下一步提示；未知历史节点保守显示待核对。
- Runtime 新增只读预算报告接口，SQLite 提供同口径账本读取；缺少读取能力或账本时证据不可用，不视为零消耗。Gateway 报告补充 budget、stage、next_action 和无执行权限标志。
- 隔离 Git 提交请求仍走原专用报告，没有修改其身份、审批和应用边界；尚未增加 Web/RPC 对外路由。
- 离线专项 24 passed（0.86s），涵盖 CLI/共享存储预算一致、旧账本、身份、调度观察和阶段优先级。无付费模型调用、无真实 Temporal/Docker 验收，未提交当前项目。

## DeepSeek 非思考模式接线与受限连通探针（2026-10-15）

- ModelSettings 增加可选 thinking_mode、max_output_tokens，OpenAI-compatible 普通/文本流/结构化工具流共享同一请求组装，显式 disabled 映射为 thinking.type=disabled，输出上限映射为 max_tokens；默认未配置不改变旧请求。暂不声称支持思考内容回传，显式 enabled 拒绝使用。
- 工厂接通参数，试验配置增加非思考模式和本轮 256 输出 token 上限。未读取/展示用户密钥内容；用户随后自行编辑的凭证配置不自动覆盖。这个上限仅用于连通测试，不代表真实编码预算或金额硬封顶。
- 新增仅手工启动的 tests.deepseek_connectivity_probe，最多两次直接 provider 请求，绕过 ModelOrchestrator 自动重试/回退，不启动 Runtime/编码任务，报告只包含通过标志、token 用量及净化后的错误类别/状态码。不使用其他提供商凭证回退，不记录密钥、请求头或原始错误正文。
- 用户已允许最多两次各 256 输出 token 的小额测试，费用目标 1 元（非硬封顶）。实际执行在本地配置检查阶段停止：api_key_env 不是预期的环境变量名，专用 DEEPSEEK_API_KEY 未被识别；真实 API 请求数为 0、未产生调用费用，尚未确认账号余额或真实模型连通性。等待用户修正 .env 中的赋值及 TOML 中的变量名，再继续既定范围。
- 普通/两种流式协议、模拟工具结果往返、参数工厂接线、探针上限/错误不重试/不泄漏凭证和既有配置/恢复回归 86 passed（2.64s）；Ruff 与限定代码文件的 diff 检查通过。未提交当前项目。

## 独立 Python 进程 CLI 整链路验收（2026-10-15）

- 新增 tests.offline_evolution_cli_harness（仅显式测试进程入口安装，导入不修改生产工厂）、适配器测试及独立进程集成专项。使用真实 source.toml、五类源/实验 SQLite、专用 Temporal/Docker/Git；不向子进程传入父 Runtime/模型计数器，不调用付费模型。
- 实际 1 passed（73.50s）：28 个独立子进程贯通 replay-compare、基线/候选两侧审批继续、提交请求/审阅/明确决定/提交 worker/调度、worker 正常退出后状态及重复收尾、独立测量与缓存、新 pair/报告、显式 candidate-validate。最终 persisted validated、published=false，源任务仍 created 且无执行节点，源 Git HEAD/工作树不变，旧等待证据和旧 pair 字节不变。
- 明确替身：编码/测量使用离线脚本模型与 fixture 容器授权；工具请求仍经真实落库审批。代码审查批准是父测试明确 fixture 决定，不冒称代码审查交互 UI/CLI 已验收。提交等其余入口走原 CLI；提交 worker 通过已有测试 SIGINT harness 正常结束，不代表物理控制台键盘投递验收。
- 证据 .workbuddy/test-runs/independent-cli-real-1/test_independent_cli_processes0/independent-cli-acceptance.json，记录各命令 PID/退出码与替身范围；只移除精确记录的已完成/停止测试容器，保留实验数据库和报告。
- 离线适配器、测量报告、候选验证、恢复与提交收尾定向回归 81 passed（10.04s）；Ruff、diff 检查通过。本轮没有为通过验收而修改生产代码，未提交当前项目。
- 阶段判断：离线受控的服务级和独立进程功能闭环已具备可重复证据，停止默认扩展底层机制。下一阶段为小型真实任务试用，必须先确认任务范围、模型和费用上限；真实模型质量、生产授权体验、自动候选收集质量及实际成长收益仍未证明。

## 提交完成后的独立测量、新 pair 与显式候选验证（2026-10-15）

- 新增 replay-measure：复用公共实验 Runtime、候选恢复及原计划检查，要求可信 applied 与任务 completed，核对干净的完成 Git 提交，再调用已有 Docker pytest/函数验收/冻结回归测量。不运行任务或模型，不写 Git，不自动验证/发布。
- 首次等待报告保持不动，完成后独占创建 independent-measurement-completed.json；资源等待返回原因与当前任务进程审批摘要，不封存为成功；--approve 可明确批准当前任务/操作者/workspace 的 process_run。重复封存报告返回缓存，不自动重跑或覆盖；现有验证占用继续保护未知执行。
- 新增 comparison-refresh，读取同一对原实验完成后报告并生成新 pair；旧 pair 和原测量不变。comparison-report 仅增加这个固定完成报告文件名，继续核对原数据库、计划、测试覆盖与执行证据。显式 candidate-validate 仍独立于测量命令，发布更不自动触发。
- 本地真实整链路专项 1 passed、4 deselected（63.50s）：同一源任务/修订下基线和候选都实际编码、工具审批恢复、专用提交到 completed、独立 Docker 测量及缓存读取；新 pair 全部观测门槛通过，随后单独调用真实 validate_comparison 进入 validated，published=false。源 Git 不变、旧等待报告/旧 pair 字节保持不变；仅清理精确记录且已停止的本测试容器。
- 使用真实专用 Temporal/Docker/Git，模型和授权策略是离线 fixture；此处证明服务级功能闭环，不冒称独立进程 CLI 已验收、生产模型自主学习或候选收益。冻结 oracle 缺少验收绑定等历史输入不自动修补。
- 测量/报告/验证证据回归 73 passed、1 skipped（1.36s），旧装配/恢复/提交/收尾兼容回归 125 passed（23.11s）；合计 198 passed，1 项需显式环境的测试默认跳过。两个新 CLI help 已确认注册；Ruff 和 diff 检查通过，未提交当前项目。下一步应收口到独立进程 CLI 验收，再在用户明确任务/模型/费用范围后做真实小样本试用，不默认继续扩展安全基础设施。

## 候选实验的独立提交重建与 completed 闭环（2026-10-15）

- 根因已修复：编码恢复会重建内存候选，但独立提交请求/刷新/worker 原先只加载隔离技能文件，因缺失同一候选而触发 skills_fingerprint_changed。候选恢复提为 evolution.candidate_binding，公共 experiment_runtime 在 worker 装配前统一恢复原候选，编码继续不再另有一份实现。
- 已传入源 metadata 时复用调用方句柄，不接管其关闭；否则仅候选分支按原 settings 创建未启动的源 Runtime，精确读取原 ID/版本/正文哈希后关闭其存储。不调用源模型、源任务或通用 start，不复制源审批，不修改原计划或自动采用新版候选。读取/恢复失败会关闭两侧自有存储，不进入 Git 执行。
- 本地真实专项 5 passed（45.06s，含两项参数缺失测试）：基线、候选工具审批、候选进程审批仍通过；候选工具分支额外经过实际 create_cli_commit_request、持久化显式决定、submit_cli_commit、独立 IsolatedCommitWorker，再由实际 Git 写入实验仓库并收尾任务 completed。源 Git/源任务不变、源候选仍 candidate，未自动发布。
- 该专项使用真实专用 Temporal/Docker/Git、离线脚本模型和测试策略；代码审查批准是明确 fixture 决定，与单独提交批准区分。证明跨 Runtime 真实服务链路，不冒称独立进程 CLI 已验收、自主学习或生产收益。
- 125 项装配/恢复/提交/收尾定向回归（23.04s）与 36 项请求刷新/服务/存储回归（1.01s）通过；Ruff 和 diff 检查通过。未提交当前项目。
- 下一步聚焦提交完成后的显式独立测量与 pair 报告更新：目前首次等待报告仍 not_measured，任务 completed 不自动等于候选验证完成，不手改旧报告为成功，也不自动发布。

## 隔离编码的工具/进程审批显式继续入口（2026-10-15）

- 新增 replay-continue：从指定既有实验 metadata.db 和原 accepted submission 获取任务/Temporal 路由，列出当前等待节点的审批摘要；--approve 只批准被该节点引用的当前任务、操作者、workspace、工具/进程请求，随后用 Runtime.resume_task 继续原节点图。
- 编码/提交共同使用的未启动 Runtime 装配提为 cli.experiment_runtime，保留 commit_runtime 导入别名。复用独立队列、计划绑定会话和统一 worker 生命周期，不新建实验、不调用通用 start、不覆盖原调度地址、不清除未知占用或自动重跑。
- 候选分支按源权威库中的 ID/版本/正文哈希恢复原隔离注册；变更时阻止继续，不悄悄使用新候选。APPROVAL/APPLY 只指引独立 commit-*，不批准代码或写 Git；取消/已完成/运行中/资源等待不重新激活。
- 本地真实专项 5 passed（35.13s，含两项参数缺失测试）：基线、候选工具审批与候选编码镜像检查进程审批均经实际专用 Temporal/Docker；原 worker/Runtime 关闭后重建继续，已完成节点未重跑、工具请求只产生一次、源任务/Git 不变。模型/策略为离线 fixture，无外部 API 费用。此处证明跨 Runtime，不冒称独立进程 CLI 已验收。
- 定向回归 122 passed（22.68s），涵盖 CLI 资源关闭、错误审批/任务状态、候选正文变化、公共装配、旧提交/收尾与恢复链路；实际 CLI help 已确认注册，Ruff 和 diff 检查通过。未提交当前项目。
- 已识别下一处接线差异：replay-continue 显式恢复内存注册候选，但独立 commit-* 的公共 Runtime 装配尚未恢复此类候选，可能因 skills_fingerprint_changed 被阻止。先补齐候选提交阶段的同一候选重建，再处理专用提交完成后的独立测量和对比报告更新；不能把现有已发布文件技能的提交测试当作此分支验收。
- 本轮不扩大到独立测量补录或 pair 更新：任务到代码提案审批后仍 not_measured，不算验证完成。

## 显式 replay-compare 接通专用隔离编码 worker（2026-10-15）

- 新增共享 coding_execution 前台装配：复用 IsolatedCodingWorker、计划绑定会话、独立任务队列和 run_owned_workers；执行提交与 worker 故障竞争等待，退出时取消/等待本进程任务，保留持久化历史，不调用通用 Runtime.start 或回退本地。
- replay-compare 新增 --temporal-host/--temporal-namespace，与固定编码镜像、回归规格共同显式选择编码路径；缺项在创建实验前返回等待，不创建 namespace、不默认连接共享调度、不批准工具/镜像/代码、不自动 Git 提交或发布。未指定新参数保持旧行为。
- ReplayRunner 保持 worker/存储直到测量和证据写出结束，未转移生命周期的提前返回路径显式关闭未启动存储；CLI 源 Runtime 同样显式关闭，避免 stop 对未启动实例不关库。
- 真实专项基线/候选两支均经实际 Temporal 17233、分别固定的 Docker 编码/验证镜像、真实验收/pytest/回归/PoC，到达 waiting_approval；候选被实际 skill_view 读取，源任务未执行、源 Git 未修改、候选未发布。测试模型和策略明确为离线 fixture，无外部 API 费用。
- 初轮失败分别定位为测试混用了镜像角色、缺少 pytest 样例；修正 fixture 后真实专项 3 passed（24.05s，含参数缺项检查）。补齐缺少回归规格的提前等待后，最终独立回归 76 passed、2 skipped（17.20s）；CLI help 已确认参数注册，Ruff 与 diff 检查通过。
- 边界：这只是首次启动到等待点的用户入口接线。跨进程审批后继续编码、完成后的独立测量补录与 pair 更新仍待整合；等待时 not_measured 不允许视为验证完成。未做真实付费模型试用、自主学习或效果收益证明。

## 用户入口检查与固定技能注册修复（2026-10-15）

- 入口检查发现：ReplayRunner 的通用 Runtime 执行路径尚未装配受控编码演示使用的专用 dispatcher/worker；现有受控演示通过不代表普通 isolated-compare 已具备完整编码闭环。后续优先整合用户入口，真实模型任务和费用范围需显式确认。
- 修复 NodeExecutor.pin_snapshot 对同版本、同正文的已加载技能重复注册：现在复用原对象，保留信任、工具依赖、来源和启用状态，不再在任务结束时删除原技能。同名不同版本临时覆盖后，Runtime finally 恢复原注册；无原注册时仍删除临时技能。
- 固定快照提供正文哈希时检查一致性，失败不注册也不记录成功固定事件。固定技能、快照和实际技能消费测试 9 passed，Runtime/隔离回放/技能装配回归 69 passed；不代表真实模型成长效果验证。

## 专用提交回接原任务图并结算 completed（2026-10-15）

- 根因：专用提交只完成请求派生 APPLY，原图 APPROVAL 仍 waiting，原 APPLY 未落库，Temporal 跳过的后续修复轮次仅存在于历史结果；任务被激活为 running 后无人收尾。新增数据库收尾组件，绑定同任务的完成 APPLY/Git 事务、已应用提案、通过验证和原始已接收图，在同一事务中写原图完成/跳过与任务 completed。
- 原 Temporal 等待结果不改写、不重新执行；任务 metadata.isolated_commit_completion 记录请求、指纹、实际提交节点、验证节点、事务、提交与原 workflow ID。正常提交及可信缓存路径均尝试收尾；收尾异常不把已完成 Git 误标 uncertain，也不重新写 Git。
- 新增 commit-finalize 显式数据库恢复入口，复用同一逻辑，只打开既有库，不装配 Runtime 或连接调度；缺图/未应用/未知节点/暂停取消不强制收尾。commit-status 新增请求级 task_completed，applied 与任务完成继续区分。普通审批、强制应用及成长验证语义未改变。
- 单独真实基础场景 1 passed（30.62s）；最终统一成长演示含独立进程重复收尾命令 1 passed（46.14s）。实际结果 .workbuddy/test-runs/controlled-growth-completed-2/test_controlled_growth_demo0/controlled-growth-demo.json 中后续编码 task_status=completed、coding_task_completed=true，技能正文哈希一致、提交 applied。
- 完成/重复恢复、SQL 故障全回滚、快照变化不部分落库、取消/暂停保留、收尾错误不重复 Git、只读状态与既有提交/成长回归共 118 passed（13.08s），Ruff 通过。模型/起始信号仍受控，无自主学习/收益结论，未提交当前项目。
- 受控“候选构建 → 实际成对测量 → 验证 → 显式发布 → 新编码任务消费技能 → 验收/提交 → 任务 completed”功能闭环本轮完成验收。下一阶段应转向真实用户任务试用、入口整合和效果测量，不再默认扩展底层安全机制。

## 单次受控成长演示串联实际发布与后续编码（2026-10-15）

- 新增 test_controlled_growth_demo，复用并串联真实成对测量/发布和 Temporal/Docker 编码场景。候选改由 GrowthCandidateBuilder 根据明确标注的受控 GrowthSignal 创建，不再直接构造“已验证/已发布”状态；起始信号与 evidence 引用仍是测试输入，不代表生产轨迹提取。
- 实际 CLI 发布后输出候选/版本/技能路径/内容哈希与 pair 引用，后续任务从该次真实文件读取并原样导入自己的实验技能目录，核对名称/version/正文哈希。编码节点实际 skill_view 后才进行隔离命令与功能/回归/demo 检查，再完成审批/提交/结果查询。
- 单次统一真实演示 1 passed（45.46s），结果保留在 .workbuddy/test-runs/controlled-growth-demo-1/test_controlled_growth_demo0/controlled-growth-demo.json；成长/验证交接/消费/编码节点回归 79 passed（1.54s），Ruff 通过。未调用付费模型、未写用户实际技能目录或提交当前项目。
- 汇总明确 autonomous_learning_proven=false、improvement_proven=false。当前后续编码 task_status=running，虽然实验提交 applied/验证项通过，但尚未完成任务工作流收尾；不能称为完整任务 completed 或自主成长。下一优先级调整为编码任务完成态回写与收尾，而不是继续扩展底层机制或先做收益宣传。

## 隔离编码 AGENT 节点接入技能正文消费（2026-10-15）

- 定位并修复会话与任务节点接线差异：此前在线 Runtime.handle 有技能上下文，而隔离 AGENT 的 build_node_context 未传 skills，也无 skill_view。现在该节点传入已加载/启用/trusted 的技能副本，并注册节点局部 SkillViewTool；仅正文、不读取源技能目录，不注册全局工具、不继承在线会话历史。
- 保留显式 tool_names 的选择：排除 skill_view 时不注入技能/工具；未扩大普通任务及其他节点工具范围。已有 V5 计划/Runtime 快照校验不变。节点测试覆盖索引与工具同步、未可信技能排除及正文读取。
- 真实 Runtime/AgentLoop/Temporal/Docker 原三场景增加“已发布文件夹具”场景：编码模型看到索引后调用实际 skill_view，精确正文进入工具结果后才调用隔离编码命令，再经既有 pytest/JSON 功能/回归/demo、审批、独立提交 worker 和结果查询。四场景 4 passed（92.60s）；节点装配回归先行 36 passed（7.41s），Ruff 通过。
- 编码/worker/会话消费/任务类型/执行入口兼容性回归 82 passed、4 skipped（30.45s，其他 opt-in worker 用例未启用），git diff --check 通过。
- 无技能和有技能场景均完成相同加法需求与验收，后者明确多一步技能正文读取。两侧仍是固定脚本模型，不证明能力、成功率或成本提升；此处文件为已发布状态夹具，不等于已经把候选收集/真实成对编码/发布/下一任务串成统一端到端用户流程。
- 下一步以已有命令与实验入口收敛完整成长演示，明确展示技能如何影响实际编码决策/验收结果；不新增安全治理或提交基础设施。未提交当前项目，未调用付费模型。

## 发布后技能进入下一次 Runtime 对话并被实际读取（2026-10-15）

- 新增离线消费模型夹具，通过真实 Runtime.handle → session/context → AgentLoop → ActionExecutor/ToolExecutor → skill_view → 工具结果回传 → 最终回复验证链路，不直接调用工具替代 agent 流程。默认 progressive 模式先看到 L0 索引，正文中的独立 marker 不出现在索引，模型发起 skill_view 后才读取完整原文。
- 无技能场景一轮回复“无已发布流程”，有技能场景两次模型调用且拿到精确正文；扩展既有真实成对 Docker/独立 CLI 验证与发布场景，重启后执行同一消费链路，证实测试发布的技能参与后续实际 Runtime 对话，而不只是存在于注册表。
- 真实成对场景 2 项与离线消费 2 项合跑 4 passed（27.09s），Ruff 通过。仅测试代码和验收接线，无新生产机制或外部付费模型调用。
- 技能消费/Runtime 接线/技能工具/内容往返/成长与验证交接回归 75 passed（10.17s），git diff --check 通过。
- 边界：验证的是会话/工具消费，不是新隔离编码任务，不是“真实模型按技能完成任务”的质量收益实验。脚本模型根据工具返回给固定标记，不能拿输出差异当能力提升；后续应把相同任务/验收用例的基线与候选效果对比接到实际编码实验，沿用现有测量流程。

## 真实测量、人工批准发布与重启技能可用闭环（2026-10-15）

- 扩展真实双实验联测：成功 validated 后，另起独立 CLI 进程显式 approve-candidate；核对 published、publication_guard completed、批准者、技能 TOML 文件，再用源配置创建全新 Runtime，核对技能内容/version/trust 与 AgentContext.visible_skills。回归失败侧显式批准失败、候选保持 candidate、无技能文件。
- 首轮发现 skill_to_toml 会追加尾换行并替换三引号，重启内容与已验证候选不一致。改为 TOML 基本字符串兼容的 JSON 转义，保留原始 instructions/name/version/工具名称；补尾空格、换行、三引号、反斜线、中文与 CRLF 往返测试。没有放宽发布条件。
- 修复后真实 Docker 两场景 2 passed（25.75s），Ruff 通过。模型代码与任务完成状态仍为预置测试条件，不是自主生成候选/编码实验效果证明；实际测量、验证交接、独立进程批准、文件写入和新 Runtime 加载均未替换。
- 技能往返/重启信任/运行时接线/发布与成长/验证交接回归 94 passed（10.68s），git diff --check 通过。
- 最小受控“成对测量 → validated → 显式批准并发布 → 重启可用”子链路已验收。尚未完成候选采集/实际模型编码成对实验/报告产出的统一用户流程，也未证明新技能能改善下一次任务；下一步应验证技能实际参与任务及效果，而非继续扩展底层安全治理。未提交当前项目，未发布到用户实际技能目录。

## 真实成对 Docker 测量到独立 CLI 候选验证交接（2026-10-15）

- 新增 opt-in 集成测试，实际创建两个独立实验仓库/数据库，以真实 Runtime 与测试专用 Docker 授权执行两侧 pytest、功能 JSON 输入输出、冻结回归 JSON 输入输出；候选侧注册实际技能，保存真实运行时快照、原始持久化执行结果和成对测量报告，不替换 comparison_report/来源复核或候选写入。
- 独立 python -m Sprout evolution candidate-validate 进程加载实际源候选 TOML 配置，将成功测量写入 validated、关联两侧 task IDs 与对比证据；源库重新查询确认验证当前有效，publication 为空，原项目 Git HEAD 不变。
- 增加实际候选代码的受控回归故障：普通功能/pytest 通过但零值回归失败，CLI 返回 waiting_validation 与 paired_regression_cases_not_all_passed，候选仍 candidate、不发布。真实两场景 2 passed（24.28s）；专用固定验证镜像，无生产模型调用。
- 验收边界：使用预置代码与测试显式完成任务，不包含模型编码或 Temporal 编码图，也不证明候选技能带来改进；本轮证明独立实际测量到候选验证的成功/失败交接。此前单任务编码/提交联测与本次成对测量尚未合并为完整自动成长流程。
- 下一阶段按既有人工审批/发布流程补实际技能发布与重启加载验收，不扩展提交基础设施或新安全治理。未提交当前项目。

## 对比报告到候选验证状态的显式交接入口（2026-10-15）

- 新增 candidate-validate <exact-artifact-id> --pair <growth-comparison.json> [--config <source-settings>]。从源配置装配候选存储，不调用 Runtime.start；重新计算既有对比、计划、pytest/功能/回归来源校验，按当前候选内容/version 匹配原快照，不接受用户传 passed 标志，不自动批准/发布。
- observation_gates_passed 满足时复用 CandidateManager.record_validation 与原有数据库 CAS，写入 validated、来源实验 task IDs、pair 指纹/路径、测量哈希与重算报告；否则返回 waiting_validation/blockers，候选与旧验证记录不修改。增加 expected_fingerprint 参数防止候选在复核后变化却套用旧结果。
- 仍区分测试通过和改进效果：该记录证明既定成对测试/功能/回归条件，不证明质量或成本提升；Git applied 不能代替完整成对测量。只读 comparison-report 继续保持不写状态。
- 候选验证交接/CLI 等待与资源关闭/重启报告读取/候选变化拒绝，以及既有成长/发布/对比/准备度回归 88 passed（1.58s），Ruff 通过。成功交接测试使用完整结果的单元夹具；真实不完整报告返回等待已验证，尚未做真实基线/候选双实验成功交接，不把既有单任务 Git 联测算入这项。
- 下一步先补真实成对测量到候选 validated 的端到端验收，再接人工批准发布；不再扩展提交基础设施或新安全机制。未提交当前项目、未调用生产模型或发布技能。

## 独立 CLI／worker 进程、配置文件与信号退出验收（2026-10-15）

- 除最初需要模拟过期时间的 fixture 创建外，创建复用、刷新、查看、确认、派发与查询都改用 sys.executable -m Sprout 独立进程，创建/刷新使用实际 TOML 文件；提交 worker 同样独立进程装配，不注入配置、不替换审批、存储或 Git 执行。
- 新增仅测试使用的 commit_worker_signal_harness，stdin 控制命令使子进程事件循环线程触发真实 SIGINT，走 asyncio.run/CLI 的中断、worker join、存储关闭路径；核对 stopped 输出与退出码 0，随后独立状态命令仍查询可信 applied。不是物理键盘/Windows 控制台按键投递测试，不加入生产关闭后门。
- 独立进程暴露 Git 配置探测超时；run_capture 原先继承父 stdin，现非交互 helper 使用 DEVNULL，不读取前台 worker 的输入。补 EOF 回归；没有改变超时保护或放宽提交准入。
- 首两轮独立 worker 因上述 Git 子进程超时失败；修复后真实基础场景 1 passed、2 deselected（30.06s）。进程/前台装配/生命周期回归 17 passed（6.90s），Ruff 通过。脚本模型与 smoke 镜像，不代表生产模型/所有候选组合或成长发布验收。
- 最小实验提交子链路本轮收口，不再以基础设施扩展为默认下一步；下一阶段进入可信成长验证报告交接。编码 worker 双队列统一监督和物理终端体验暂作为后续工程完善，不阻止先推进功能闭环。

## 六个提交命令真实 CLI 链路验收与操作收口（2026-10-15）

- 既有真实联测从直接调用服务改为 CliRunner 实际执行 commit-request/refresh/review/decide/submit/status，核对参数解析、asyncio.run、退出码与 JSON 输出；保留真实数据库、审批决定、证据准入、Temporal 服务端绑定、Git 写入与历史回放，不替换这些路径。
- 基础真实 Temporal/Docker 场景 1 passed、2 deselected（24.24s），覆盖过期待办创建/重复读取/刷新/重新决定/派发/提交/退出后查询，原项目不变。配置加载注入测试原 settings，worker 仍在测试进程内运行；独立多终端、文件配置与真实 Ctrl+C 组合尚未验收。
- 在 Isolated-Coding-Boundary-Plan.md 补最短六步操作和等待状态下一步说明，明确起点是已产生的隔离编码提案、终点是实验提交 applied，不再把这部分称为尚无用户入口，也不把它称为完整成长发布闭环。
- 本轮不新增安全机制、没有生产模型调用或当前项目提交。后续优先补进程级运行验收，再接成长验证报告交接；不继续扩展提交基础设施功能。

## 提交待办显式刷新与重新确认闭环（2026-10-15）

- 按用户要求优先完成功能闭环，不再扩展新的安全治理机制；保留既有审批/持久化执行保护。新增 commit-refresh，用户提供旧待办和原指纹，重新核验原实验配置/当前证据，以 supersedes 记录前驱，不延长或修改旧记录，不继承批准。
- 已过期的 pending/approved 且无派发记录、APPLY 节点或提案 Git 事务可以刷新；拒绝/未过期/已派发/有执行记录不刷新。每个前驱固定派生一个子 ID，重复请求复用子待办及原期限；后续再次过期显式刷新子 ID，内容冲突拒绝覆盖。
- supersedes 为冻结记录的可选字段，Memory/SQLite 同步校验，沿用 JSON 存储而无需新增表；状态报告展示前驱。新待办仍经现有明确确认、worker、派发与执行路径。
- 刷新/存储/状态/执行/创建/派发定向回归 54 passed（7.92s）；真实 Temporal/Docker 基础场景用测试时钟创建过期待办，再从 CLI 服务刷新、重新批准并实际提交/回放/退出/查询，1 passed、2 deselected（25.74s）。首次联测因漏传时间模拟夹具失败，修正后重跑通过；Ruff 通过。
- 本轮完成服务级最小提交闭环（含过期处理），不等于已做独立多终端/Ctrl+C 进程级验收，也不等于成长候选已验证发布。下一步优先完整命令验收与操作说明，不新增安全功能分支。

## 从持久化实验创建提交待办入口（2026-10-15）

- 新增 commit-request <task-id> <proposal-id> --database <experiment>/metadata.db --message <text> [--config ...] [--author-name ... --author-email ...] [--evaluation-node-id ...]。复用提交 worker 的配置/任务装配、创建者及可信路由检查，再调用既有 ExperimentCommitService.request；不运行 worker、不连接 Temporal、不批准或提交 Git。作者输入仅冻结于实验 contract，不修改 Git 配置。
- 抽取共享 commit_runtime 资源上下文，未转移的 Runtime 在成功/拒绝/异常后关闭存储；提交 worker 仍交由生命周期管理器关闭，避免重复关闭。构造专用 worker 只做绑定校验，不启动执行。
- 相同审查重用原待办与期限，输出仅 ID/指纹/期限及下一步提示，不把完整原始 diff/邮箱返回到终端。缺身份、提案未批准、需选择验证节点继续返回等待，不自动填造。
- 定向创建/确认/派发/装配回归 21 passed（4.94s）；真实基础场景由新创建入口生成并重复读取待办，再执行确认/服务/派发/提交/查询，1 passed、2 deselected（23.95s），Ruff 通过。使用脚本模型/测试策略，非独立进程交互验收。
- 过期相同审查不能通过重复调用续期；显式刷新/版本关联与重新确认尚未实现，不应改 message 绕过期限或未知占用。用户命令的进程级全流程验收、编码 worker 统一监督及成长报告交接仍待完成。

## 提交 worker 显式前台入口（2026-10-15）

- 新增 commit-worker <task-id> <request-id> --database <experiment>/metadata.db [--config <original-settings>]。按当前 CLI 创建者读取既有待办，限定新版本提交队列，从可信编码路由恢复地址；检查五个既有实验数据库和配置契约后装配独立 Runtime，由生命周期管理器接管。无通用 start、无调度提交、无新授权或本地回退。
- 配置不符/旧待办/缺库在装配前拒绝，worker 构造失败关闭未转移存储；Ctrl+C 停止服务但保留持久化工作流和占用。通用工厂仍可能初始化模型凭据，未提供候选注册自动恢复；快照不符不能跳过校验。
- 入口/生命周期/派发回归 16 passed（4.36s）；真实 Temporal/Docker 基础场景改用 serve_commit_worker 从已有数据库与配置装配，实际提交、历史回放、安全退出和只读结果核对 1 passed、2 deselected（20.55s）。脚本模型/测试策略，不代表生产信任验收。
- 前台命令已接通，尚未执行独立终端 Ctrl+C 的进程级测试；编码 worker 统一监督与交互创建/刷新待办仍待完成。现有待办用户可以分别启动服务、决定、派发、查询，不等于完整成长验证发布闭环。

## 提交 worker 生命周期真实接线验收（2026-10-15）

- 真实 Runtime/Temporal/Docker 基础场景改为 run_owned_workers 接管独立提交 Runtime，实际提交、原始工作流历史回放之后取消管理器并等待退出，再从只读数据库报告核对 applied/result_commit。确认未启动通用 Runtime；清理阶段不二次关闭已被接管的存储。
- 专用测试端点 127.0.0.1:17233 与固定 smoke 镜像下 1 passed、2 deselected（20.82s）；Ruff 通过。使用脚本模型和测试审批策略，原项目不变；没有生产模型或自动成长发布验收。
- 此次仅提交 worker 生命周期进入真实联测。编码 worker 仍用既有手工启动/关闭；完整双 worker 统一监督、前台启动命令、交互创建/刷新待办仍未完成。下一验收目标保持为用户可操作的最小闭环，不扩大自动成长范围。

## 专用 worker 资源所有权与退出顺序（2026-10-15）

- 新增 run_owned_workers，显式接管同实验、同 endpoint/namespace、不同队列与独立 Runtime 的已装配 worker；拒绝已通用启动或已接管的 Runtime，失败检查不转移资源所有权。
- 任一 worker 退出/失败或调用者取消时，停止并等待所有 worker 退出之后，才逐个尝试关闭实验存储。未启动的 Runtime 直接关闭 storage，避免 Runtime.stop 在未 start 情况下不关闭连接；不启动清理/outbox，不取消持久化工作流、不清理未知副作用占用、不重跑活动。
- 取消退出顺序、单次所有权、连接失败及关闭异常保留原错误、错误 scope/重复队列/共享 Runtime/通用已启动拒绝已加入单元测试。该管理器尚未接入 CLI，也尚未替换真实 Temporal 联测的手工资源管理；不能视为前台生产启动入口已完成。
- 本轮生命周期与既有 worker 定向测试 19 passed、4 skipped（11.38s，未启用其他 opt-in 测试）；Ruff 与 git diff --check 通过。未运行新的真实 Docker/Temporal 场景，未提交当前项目。

## 编码与提交 worker 分队列、共享验证边界（2026-10-15）

- 新提交审查冻结 routing_profile=isolated-commit-v1，提交队列由原任务专用队列派生为独立 commit-v1 队列，路由标记纳入审查指纹和批准决定。历史无标记待办仍走原队列，不自动迁移或改写已保存路由。
- 新增 IsolatedCommitWorker，使用独立 Runtime，只注册提交工作流与活动，不安装编码命令或编码会话工厂，也不调用通用 Runtime.start，因此不会由该入口启动孤儿清理、审批巡检或 outbox 后台循环。复用实验存储、镜像、计划与证据检查；普通编码 worker 仍强制要求编码 dispatcher，未以空实现绕过。
- CLI 从可信编码提交记录保留原 endpoint/namespace，再按冻结 profile 派生提交队列；不允许用户覆盖路由。历史请求重新审查保留原结构，避免引入新标记使旧批准失效。
- 定向回归 48 passed、4 skipped（18.41s，其他 opt-in 用例未启用）；真实独立 Runtime/Temporal/Docker 基础场景 1 passed、2 deselected（20.75s），覆盖无编码 dispatcher、未通用启动、新队列投递、实验提交与历史回放，原测试项目不变。
- 本轮完成 worker 类与队列分离，不等于生产启动入口已完成。统一生命周期监督、前台按需启动、交互创建/刷新待办、生产配置与信任仍待接线；未启用自动成长发布，未提交当前项目。

## CLI 显式可靠派发接线（2026-10-14）

- 新增 commit-submit <task-id> <request-id> --database <experiment>/metadata.db，只投递持久化批准决定，不接受用户提供新 payload/creator/Temporal 地址或队列，不启动 Runtime/worker、不执行本地 Git。可额外指定 expected-fingerprint。
- 用实际 V5 creator plan、实验数据库位置和已接收的编码 task_submissions 定位唯一原始 endpoint/namespace；queue 必须等于任务/plan 派生的专用队列，run ID 必须存在。来源缺失/冲突、身份/指纹不符、过期或不可续行任务在联网前拒绝；未知执行占用要求人工核对，不强制重新投递。
- 复用现有 mode=rw 不初始化数据库的 CLI adapter，加入 execute_batch 支持原子状态更新，调用已有持久化 Temporal 投递组件。相同请求固定 workflow ID，已 accepted 复用确认；网络失败保持 pending/uncertain，没有默认路由或本地回退。
- CLI 原路由派发/确认缓存、取消任务、路由冲突、指纹不符与既有决定/报告/投递回归 26 passed（4.27s），Ruff 通过。真实 Runtime/Temporal/Docker 基础场景改用 CLI relay 核对已经在服务器上的精确工作流，实验提交/历史回放/原项目不变/状态报告通过，1 passed（21.79s）。未提交当前项目。
- 查看 → 明确决定 → 显式派发 → 只读状态的 CLI 链路已具备（针对已保存待办）。交互创建/刷新待办、worker 按需装配与生命周期、生产运行配置/镜像信任仍待完善；CLI 不承诺 worker 已在线或 accepted 已完成，也未开启后台自动执行或成长发布。

## CLI 冻结审查展示与显式决定保存（2026-10-14）

- 新增 commit-review 与 commit-decide，均接受实际任务/已保存待办 ID。review 只读展示冻结内容和 decision_fingerprint，凭据模式脱敏仅影响展示，不改原始审查/指纹。
- decide 的真实 TTY 模式显示内容后明确选择 approve/reject/cancel，默认取消；非 TTY 必须同时提供 --decision approve/reject 和 --expected-fingerprint，不提供 --yes/force/actor 入口。取消不写状态，拒绝不产生派发意图、不取消任务。
- 抽取 decide_commit_request_for_principal，共享 Runtime 和 CLI 的创建者检查与同一存储 CAS。CLI 先按真实任务身份只读复核，再用 mode=rw 只打开现有 operational.db，通过原决定接口保存；没有 Runtime、迁移、调度、容器或 Git 执行。错误/过期/已决定/漂移请求要求重新审查，不续期或解锁占用。
- 展示脱敏但绑定原指纹、非交互完整参数、错误指纹拒绝、默认取消、交互批准/拒绝、重复决定失败和无 grant/派发覆盖；与只读报告/服务/待办/投递回归 40 passed（1.39s）。真实 Runtime/Temporal/Docker 基础场景改用 CLI 保存适配器记录显式决定，再走专用提交工作流与提交核验，1 passed（21.12s）；Ruff 与 diff 检查通过。
- 已保存待办可以由用户查看并确认，尚未接通用户交互创建新待办、可靠投递调用和按需 worker 生命周期；确认成功显示 waiting_schedule，不保证已运行。过期待办刷新/重新创建也未提供完整交互入口，不应修改数据库或更换说明来绕过保护。未提交当前项目、未开启后台自动执行或成长发布。

## CLI 提交待办与执行状态只读展示（2026-10-14）

- 新增 sprout evolution commit-status <task-id> --database <experiment>/metadata.db，可用 --request-id 过滤。读取同目录 operational.db，按任务真实 actor/source 和当前 OS CLI 身份限制范围，不接受 --user，不启动 Runtime/worker、不连接 Temporal/Docker。
- 数据库用 mode=ro/query_only 打开，缺失/旧表不初始化。报告区分待确认、等待调度、投递未知、已接收未见完成、活动占用、人工核对、过期/拒绝；不把 accepted 当作 completed。只有匹配的完成 APPLY 节点、Git 事务、精确审批参数和观察结果才显示 applied/result_commit，始终 validation_committed=false、write_authorized=false。
- 输出只含任务/请求/提案、期限、调度/run ID 和匹配提交结果，不输出完整差异、身份邮箱、配置或过程日志；最多 200 条并标记截断。报告是观察，不是跨库原子执行判断，不清理占用、不允许强制重跑。
- CLI/数据库字节不变、缺失库不创建、创建者限制、调度 ACK 不代表提交、active/uncertain 状态覆盖；相关回归 35 passed（1.00s）。真实 Temporal/Docker 提交后只读报告核验实际 result_commit，1 passed（20.81s），Ruff 与 diff 检查通过。
- CLI 只读入口已可用；交互创建/确认待办、实际可靠派发接线和 worker 按需装配/生命周期尚未形成完整用户会话。未提交当前项目，也未开启后台轮询或成长发布。

## 专用提交工作流、受约束执行活动与服务端投递核对（2026-10-14）

- 注册 SproutExperimentCommitWorkflow 与实例绑定 run_experiment_commit 活动，仅专用实验 worker 使用，不修改共享 worker/global Runtime。工作流明确 RetryPolicy(maximum_attempts=1)，失败/超时返回结果不明，不自动重试变更。
- 活动核验请求归属、审查指纹、真实 workflow ID、endpoint/namespace/queue 和当前 worker 绑定，再读取可信创建者的持久化批准决定。执行处理器重新审查请求内容，先占用请求派生的固定 APPLY 节点，才按原决定生成精确单次 grant 并调用 Git 协调器；审批有效期不能超过剩余决定有效期。
- 已完成结果只有匹配的 completed 节点、Git 事务与审批参数均存在时才复用；active/uncertain 占用不重复执行。取消/失败保留 uncertain 节点，数据库不可用时原 active owner 仍保留。成功仍 validation_committed=false，不验证/发布成长候选。
- 显式持久化决定通过条件更新仅继续 waiting_approval/running 的同 replay code 任务；暂停/取消/完成/失败/其他资源等待状态均不复活。之后的准入、节点/事务/写入 CAS 仍检查实时状态，避免普通无条件更新覆盖取消。
- Temporal 重复 ID 响应现在核对服务器 workflow 类型、namespace、queue、memo 指纹和历史中原始启动参数后才记录 accepted，不能仅凭 ID 或 memo 认定相同请求；不匹配保持 uncertain。
- 真实 Temporal/Docker 三场景 3 passed（41.98s），专用提交历史回放、单次活动 schedule/retry policy 和原项目不变均验证；补充 waiting_approval 续行后基础场景再次 1 passed（20.66s）。定向 worker/执行/待办/协调器回归 46 passed、4 skipped（未启用其他 opt-in 用例），额外服务端错误绑定/执行回归 20 passed（2.88s），Ruff 通过。
- 本轮只向测试 namespace/自建仓库显式投递，不提交当前项目、不启用后台轮询。注册和执行链路已具备，CLI 会话、待办列表/执行状态展示、按需 worker 装配与生命周期仍未接完；生产镜像/模型信任、生产历史以及成长验证发布桥接继续未验收。

## 实验提交的持久化 Temporal 投递组件（2026-10-14）

- 在现有 operational 数据库增加 experiment_commit_dispatches；只有已批准且创建者决定匹配的待办能创建投递记录。工作流 ID 强制由待办 ID/审查指纹派生，冻结 endpoint/namespace/queue，重试不新建执行身份、不更换目标队列。
- 新增内部 commit_dispatch 投递组件，先保存绑定再连接 Temporal，使用 REJECT_DUPLICATE 和调度绑定 memo；确认 accepted/run_id 后复用持久化结果。连接失败保持 pending，启动结果未知保持 uncertain，无本地执行分支。重复 ID 响应尚不能证明服务端 payload 一致，因此保守保留 uncertain，等待后续服务端精确核对。
- 调度状态与待办显示状态在同一个 SQLite execute_batch 事务中更新；accepted 不被晚到的 unknown 覆盖。调度确认仅代表服务端接收，execution_confirmed=false，不表示 Git 已提交或成长候选已验证。
- Memory/SQLite 都覆盖先持久化再连接、确认缓存、超时同 ID 重试、目标漂移拒绝、未批准拒绝、取消保留、状态不回退；测试注入第二条 SQL 失败确认两处状态一起回滚。与待办/审查/精确审批/提交协调器回归共 42 passed（5.32s），Ruff 与 diff 检查通过。
- 本轮 Temporal 客户端使用模拟网络，未向真实服务器派发提交工作流，也未执行当前项目 Git 提交。匹配的 SproutExperimentCommitWorkflow/专用 worker 活动尚未注册，应用服务/CLI 尚不调用投递组件，后台轮询与自动执行都未开启。下一步注册受约束的提交工作流/活动，验证服务端既有工作流绑定与真实投递，再接 CLI 状态展示。

## 持久化实验提交待办与原子决策派发意图（2026-10-14）

- OperationalStore 增加提交待办保存/读取/决定接口，Memory 与 SQLite 同步实现；SQLite 在现有 operational 数据库增加 experiment_commit_requests，冻结实际审查内容、创建者、指纹及有效期，不覆盖同 ID 的绑定内容。同一审查生成稳定 ID，重复请求不续期。
- ExperimentCommitService.request 仅对已有 approved 代码审查提案生成待办，pending 提案仍可查看但返回 waiting_code_review，不自动批准。创建者只能来自当前 OS CLI 身份；decide 不接受外部 actor，以待办创建者和 expected_fingerprint 绑定显式决定，允许离线保存意图。
- 决定与 dispatch_state 在同一条 SQLite CAS 更新中保存：批准进入 waiting_schedule，拒绝不生成派发意图；这是持久化的用户意图，不是可执行 Git grant，不消费已有授权，不修改任务/提案、不调度也不写代码。后续 worker 必须重审并建立精确执行授权。
- 并发决定、创建者/指纹不匹配、过期、拒绝、冻结内容不可覆盖、关闭重开后保留、不续期和应用服务身份检查覆盖；定向回归 51 passed（0.76s）。真实 Runtime/Temporal/Docker 基础场景新增真实 SQLite 待办/决定、未写入及参数一致性断言，1 passed（18.89s）；Ruff 与 diff 检查通过。
- 持久化结构与服务已实现，派发消费/占用及 Temporal 可靠投递尚未接通，不存在后台自动执行。CLI 会话、待办列表/状态展示、worker 按需启动与生产信任配置继续待实现；不会因该决定直接开启成长发布。

## 统一实验提交审查应用服务（2026-10-14）

- 按用户确认的“统一任务会话＋持久化审批待办＋按需专用 worker”方向，首先增加 Runtime 层 ExperimentCommitService.review。入口接受任务/提案 ID、提交说明与可选用户输入的姓名邮箱，不接受用户导入计划/actor/image；从实际持久化任务、提案和 worker Runtime 定位计划与证据。
- 复用 coordinator 的 Runtime/计划/原始证据检查；review_only 允许 pending 提案展示，但实际写入仍只能 approved。审查没有审批创建/消费、Git 占用或写入；已有身份优先，缺失返回交互字段提示，不修改配置。唯一 completed guarded evaluation 可自动定位，多候选/无候选要求明确选择，不随意复用历史节点。
- 返回原目录与实验目标、基线、文件/差异、contract、精确审批参数及审查指纹；展示来自最终持久化提案快照。协调器补充核验最终快照状态与差异指纹，避免测量检查期间的提案变化被当作有效审查。
- 单元/协调器/身份/消费回归 21 passed（8.09s）；真实 Runtime/Temporal/Docker 三场景 3 passed（38.80s），增加待审提案审查且状态不变的断言；最终快照校验后基础真实场景再次 1 passed（17.44s）。Ruff 通过，未提交当前项目。
- 这是统一只读审查服务，不是已经可用的一键提交 CLI。持久化审批待办/决策派发记录、CLI 会话接入和按需 worker 生命周期还待实现；原 commit-identity 仍是计划文件预览入口，不能直接触发提交，也不开放自动应用或成长发布。

## 真实 Runtime／Temporal／Docker 证据准入到实验 Git 提交（2026-10-14）

- 扩展既有全图集成测试，基础场景不替换协调器准入：真实 Runtime、专用 Temporal worker、Docker 编码/pytest/JSON 验收/回归/demo 产生原始证据后，读取真实提案与沙箱、冻结提交信息，进入 _admit 和 _commit_experiment。
- 明确测试审批下，真实指定审批消费、持久化一次性写入、实验仓库 Git 提交/核验、提案与事务联合结算全部通过。原测试项目 HEAD/文件不变；任务仍 running、validation_committed=false，没有发布/验证成长候选，也没有额外模型调用。
- 提交前增加真实来源代码漂移与实验仓库脏状态拒绝；恢复时必须保持原始换行字节，Windows 换行转换同样导致旧提交 contract 失效。测试过程中捕获并修复断言/路径类型/恢复换行问题，没有弱化准入规则。
- 已打开的 Docker、本地固定 smoke 镜像、独立 Temporal 127.0.0.1:17233；基础提交/工具审批/进程审批三个场景共 3 passed（37.61s），Ruff 通过。每次创建独立测试 namespace（历史保留一天），按原规则只清理已完成且停止的测试自有容器；未操作当前项目提交。
- 这证明测试环境下真实证据可以通过新准入，不代表生产模型、生产镜像供应链、真实历史源任务或用户交互审批均已验收。下一步接显式用户提交请求/审批入口和对应恢复状态展示，自动应用与成长发布不因此自动开启。

## 内部实验 Git 提交协调器串联（2026-10-14）

- 新增内部 isolated_git_coordinator，不注册 CLI/模型工具，也未替换普通 ApplyBroker。准入读取持久化任务与 approved 提案，核验当前 Runtime 配置/快照与专用 worker 绑定、当前 Git 计划、提案差异和写入 contract，并重新检查原始测量证据；重新构建精确审批参数，不信任传入报告的 passed 标记。
- 调用顺序为事务占用 → 再次准入/证据复核 → 指定 v2 审批消费 → 一次性 write_started → 实际 Git 写入与提交后核验 → 提案/事务联合结算。contract 在入口复制冻结。任何占用后异常（含取消）尝试记录 uncertain，记录失败时 active 占用仍保留；没有释放/强制重跑或自动回滚分支。
- 成功只表示实验提案 applied 与 Git 事务 completed，返回 validation_committed=false，不完成任务、不验证/发布成长候选。拒绝竞争占用不消耗后续写入权限。
- 控制流程测试使用替换的前置准入，实际执行 SQLite 事务/启动/联合结算、单次审批消费与测试临时仓库的真实 Git 提交。并发两次只有一次写入；注入失败/取消后不重复写入。与关联回归共 70 passed（10.15s），Ruff 通过。未提交当前项目。
- 尚未证明真实 Runtime/Docker 原始证据通过该新准入的端到端成功；也没有用户提交 CLI、生产镜像信任/commissioning、结果到成长验证桥接。下一步应先补真实准入证据链集成测试和拒绝路径，再接明确用户审批入口，不能把上述控制流程测试宣传为完整生产链路验收。

## 实验 Git 事务的一次性写入启动标记（2026-10-14）

- MetadataStore 增加 begin_git_commit_write，SQLite 在 active commit 事务上以完整 record_json CAS 持久化 write_started（所有者和时间）。启动前再次核验完整 approved 提案快照、running 任务/APPLY 节点、节点 owner、实验工作区和 replay 指纹；已有启动标记一律拒绝，不设置过期租约或重置入口。
- 标记必须在 Git 子进程启动前写入；即使在启动前崩溃，仍保守保持占用等待核对，不自动重跑。它不是审批或验证证据，不能单独解锁实际写入。
- claim_transaction 拒绝调用者注入 state/result/proposal_snapshot/write_started，生命周期字段由存储拥有。旧事务没有标记时也必须通过实时绑定检查，不能简单补字段视为已授权。
- 并发双连接、数据库重新打开、同 owner 重入、其他 owner、提案漂移、任务取消、节点完成、uncertain，以及生命周期字段注入均覆盖；与事务、精确审批消费和真实 Git 写入回归共 37 passed（5.07s）。没有提交当前项目。
- 本轮完成持久化启动接口，尚未把整个协调器接到运行入口；下一步需把源绑定证据与 Runtime 校验、事务占用、精确审批消费、一次性启动、写入、联合结算串联。自动应用仍关闭。

## 实验仓库实际提交与提交后核验适配器（2026-10-14）

- 新增内部 isolated_git_writer，不注册 CLI/工具、不接普通 ApplyBroker、不开放自动应用。调用者必须先建立持久化占用并消费精确审批；该适配器本身不是授权入口。
- 写入前重新生成只读计划，核验非空差异指纹，先 apply --check --index 再复核计划，随后仅向独立实验仓库 apply --index 并提交。姓名/邮箱通过单次命令配置传入，不写 Git 配置；沿用隔离 Git 的 hooks/filter/global config 防护，关闭签名和自动维护。
- 提交后实际读取完整 HEAD、单父提交、作者/提交者姓名邮箱与说明，核验提交树相对基线的二进制差异指纹、干净工作区与源文件清单。任何失败不 reset、自动回滚或重试，保留暂存区/现场给后续协调器记录 uncertain。
- 测试自建临时仓库真实提交、来源漂移写前拒绝、提交失败保留已暂存差异；原目录与 Git 配置保持不变。写入/计划/审批消费/事务回归共 60 passed（6.90s），Ruff 通过，未提交当前项目。
- 完整协调器仍未接通：下一步需持久化一次性写入启动标记，阻止同 owner 重入，再连接证据检查、审批消费、适配器和联合结算。生产自动入口保持关闭；不能把这些测试解读为成长发布已获授权。

## 实验提交的指定审批原子消费（2026-10-14）

- 接通内部 consume_commit_approval：仅读取指定审批 ID，复核 v2 提交计划/作者/说明指纹、当前创建者和有效期，不走通用条件查找或可复用审批降级。
- OperationalStore 增加 consume_approval_exact，Memory/SQLite 都比较完整检查快照再单次消费；SQLite 用一条 UPDATE 的固定字段 CAS，避免检查后作用域/审批内容漂移或多个 worker 重复消费。普通审批流程不变。
- 验证指定 ID 缺失不借用匹配授权、说明漂移保留审批、快照变化拒绝消费，以及独立 SQLite 连接竞争只有一个成功。审批/事务定向回归共 77 passed（0.84s），Ruff 通过。
- 本轮未执行 Git 提交。消费接口是内部协调器组件，必须在证据检查和持久化写入占用之后调用；消费成功本身不代表测量可信或有权调用 Git。实际 Git 写入、提交树核验与事务联合结算的完整协调器仍待接通，不开放 CLI 写入或自动重试。

## 当前实验 Git 身份的交互补齐（2026-10-14）

- 用户确认可通过交互补齐而非修改源码。新增 evolution commit-identity CLI：优先读取已有身份，缺失时可用 --interactive 输入，或显式 --author-name/--author-email；仅生成当前计划绑定的待审批 contract，不写任何 Git 配置，不提交、不消费审批、不自动恢复任务。
- 仍先验证冻结 replay plan、当前 CLI 创建者和实验目标；显式输入同样走姓名/邮箱/提交说明校验及精确审批指纹，不能借补身份绕过原授权。缺失时非交互退出码 2，取消输入不会重试。
- 定向测试 36 passed（3.69s）：已有身份、显式身份不改配置、交互输入、非交互等待、取消以及精确审批绑定。实际 Git 写入协调器和自动关联任务/审批入口仍未完成；CLI 输出只是待审批预览，不能当作完成证据。

## 提交身份/说明的精确审批与可行动的配置等待（2026-10-14）

- 用户确认沿用已有 Git 身份，缺失等待，不虚构、不写全局配置；随后询问怎样向下推进。新增只读 git_identity 解析原项目的 user.name/user.email，仅当前 CLI 实验创建者可调用；命令固定为 config --get，两次读取均有 15s/4KiB 限额，身份不会自动写回任何配置。
- git_transaction_contract 新增提交 write contract，将姓名、邮箱、提交说明与只读 Git 计划指纹冻结。精确审批参数增加 v2 write_contract fingerprint，新的 commit_approval_blockers 拒绝旧 v1 授权以及作者/说明/计划漂移；v1 只读检查保留，不自动升级。没有实际 Git 写入 API/授权开启。
- 缺失配置返回 GitIdentityRequired 的结构化 guidance：缺少字段、项目级 config --local argv、重新检查/显式继续与取消说明，不自动轮询或重试。此阶段没有提交写入 claim，补齐身份不等于消费审批，也不能解锁已有 active/uncertain Git 事务。
- 测试确认读取现有本地身份不改 .git/config、缺失不合成值、其他操作者不读取身份、姓名/邮箱/说明漂移不能复用审批，以及事务回归。定向共 47 passed（3.04s），Ruff 与 diff 检查通过。配置写入仅为测试自建原仓库准备身份，未修改当前项目/用户全局 Git 配置，未执行当前项目提交。
- 用户操作说明补入 Isolated-Coding-Boundary-Plan.md。身份解析与结构化诊断已完成，但 Git 提交/恢复 CLI、精确审批消费、实际提交/commit-tree 核验与联合状态提交的协调器尚未接完；不能宣称用户配置后已有一键继续，也不能调用旧 replay-resume 绕过这一缺口。

## 普通回滚移除自动 hard reset 兜底（2026-10-14）

- 处理上一轮 Git 使用点核对发现的风险：ApplyBroker.rollback 对已有 applied_commit 使用 git revert；失败后不再自动 reset --hard 到 base_commit，不尝试清理或修复仓库，而返回 applied=False 和人工核对提示，保留现场。正常 revert 和没有提交记录时的旧 patch reversal 路径保持不变。
- 新增命令级反例：模拟 revert 冲突，断言只调用一次 revert、没有 reset 或虚假成功。新增真实 test-owned 仓库反例：已有 applied commit 后操作者修改同一 tracked 文件并创建 untracked notes；实际回滚失败后文件内容、HEAD、暂存区 diff 和提案 APPLIED 状态保持原样。
- 普通 apply/revert、隔离应用边界和 Git 联合状态事务定向回归 23 passed（1.90s），Ruff 与 diff 检查通过。真实 Git 操作仅发生在 .workbuddy/test-runs/git-rollback-no-hard-reset-1 的测试自建仓库，没有回滚/提交当前项目，也没有运行 hard reset。
- 本轮修复普通回滚的数据保留行为，不代表隔离写入协调器已完成或所有失败回滚自动恢复。已有冲突/序列状态仍需人工核对，不自动 abort/checkout/清理；下一步继续精确审批到实际提交及 commit/tree 核验的独立协调器实现。

## Git 使用范围与协调器的原子状态提交（2026-10-14）

- 用户询问 Git 的实际用途：项目识别/revision、普通任务 worktree 沙箱、diff/changed-files 提案、批准后的普通 git apply/add/commit、回滚，以及成长实验独立 clone/指定 revision/worktree 和证据基线绑定均存在。Git 管理代码版本和变更，不提供不可信程序的 OS 隔离；后者由 Docker 承担。没有 push/PR 发布行为。
- 继续推进隔离 Git 协调器的存储部分：新增内部 settle_git_commit_proposal，在现有 BEGIN IMMEDIATE 批事务中联合 CAS 冻结提案为 applied、Git 事务为 completed，保存 base/applied commit 与事务引用。前提仍是同一 active owner、RUNNING APPLY 节点、RUNNING code/V5 任务、匹配 workspace/replay 指纹及完整原提案 snapshot。
- 该内部 API 不验证文件系统中的 commit/tree，不证明审批、不执行 Git、不设置任务 completed，也不创建成长验证记录；未来写入协调器必须先核验真实 Git 结果再调用，不能从模型提供的 hash 或 approved 标记调用。原 finish_git_transaction 仍为独立低层记录接口，不作为提案应用权威。
- 新增状态联合提交、旧 owner/提案漂移/取消/节点结束拒绝，以及测试数据库触发器注入第二步失败的原子回滚测试。定向事务/审批/计划/隔离应用边界 43 passed（2.96s），Ruff 与 diff 检查通过。所有 commit hashes 均为存储单元 fixture 占位值，无实际项目 Git 提交；原项目/工作区现有改动保留。
- 实际提交/commit-tree 核验/精确审批消费与协调器接线仍待实现，隔离应用门禁未解除。本轮另发现普通 ApplyBroker.rollback 仍有 revert 失败后的 reset --hard 兜底，可能覆盖用户未提交改动；未运行此命令、未变更普通回滚行为，需单独安全修正。隔离回滚规划仍要求新 revert commit，不使用 reset-hard。

## 阻断范围澄清与进程审批的工作区绑定（2026-10-14）

- 用户询问 Git 应用/成长发布为何持续禁用。纠正此前简称：普通任务应用和合格候选的发布入口并非全局关闭；新的隔离实验 Git 协调器与实验到候选可信验证记录交接尚未实现，不能将整图 smoke/verified=True 当成写入或候选改善的授权。下一里程碑优先独立 Git 写入协调器，再接候选验证报告；具体解除条件补入 Isolated-Coding-Boundary-Plan.md。
- 扩展完整图进程审批场景时发现实际 Runtime.run_task_process/authorize_task_process 未传 workspace resource_scope，首轮真实专项因此失败（审批范围为空）。已在执行和预检同时传入持久化 workspace.id，并新增五种 source/两入口的归属断言。不自动升级/复用缺少工作区范围的旧审批。
- 实际 ProcessBroker+ApprovalManager 场景从 TEST_DESIGN 镜像 inspect 处等待：代码 execution claim 尚不存在；显式 fixture 决定后恢复，已完成节点保持原记录，pytest/JSON 验收/回归/demo 通过，停在提案审批。工作流历史回放及重复提交去重通过，APPLY 未运行、源目录/HEAD 未变。
- 修复后的实际专项 1 passed（11.45s）；任务进程归属/进程预检/编码 backend/隔离应用边界/候选发布门禁回归 40 passed（0.54s），Ruff 与 diff 检查通过。证据保留 .workbuddy/test-runs/isolated-process-approval-real-2，首轮失败证据不覆盖。
- 进程审批决定仍由测试模拟操作者，ProcessBroker 使用既有 task/workspace-bound 可复用 verification_commands 审批类，不声称是精确 argv 的生产 commissioning。fixture 策略仍只放行已声明的 Docker 镜像检查/run；固定模型、临时项目，无生产权限、Git 应用、候选批准或发布。本轮完整仓库套件未跑。

## 节点工具审批归属与实际 Temporal 审批恢复（2026-10-14）

- 审查发现 ActionExecutor 仅转发 requested_by/session，通用 ToolExecutor 的 task_id/source/resource_scope 仍可能使用默认值。新增 frozen NodeToolInvocationContext，由 IsolatedCodingCommandTool 的真实任务/创建者/工作区在宿主构造；仅 strict node-local 工具分派采用此上下文，覆盖调用方默认/伪造归属。不得从模型参数反序列化；从全局 registry 分派绑定工具会被拒绝，普通无绑定工具行为不变。
- 扩展实际 Runtime/AgentLoop/Temporal/Docker 整图测试为外层 high-risk 工具审批场景：不设置该工具 always_allow，使用真正 runtime.approvals 与持久化 operational store。首先停在 AGENT 工具审批，修改 command execution 记录不存在、EVALUATION 未执行；审批的 task/source/requested_by/resource_scope 均精确绑定实验。
- 对新建 fixture 审批 ID 显式调用真实 decide（CLI 创建者、理由、无生产授权），再从 Runtime.resume_task 恢复。已完成节点记录保持完全一致，原 pending tool call 经批准只执行一次；随后 pytest/JSON 验收/回归/demo 通过，停在代码提案 pending 审批，APPLY 未执行。两个工作流历史分别回放通过，后续重复 Runtime.execute 不新增模型/命令/节点/提案/提交记录。
- 首轮实际审批专项 1 passed（11.34s）；复验原整图与审批恢复共 2 passed（21.49s）。归属防覆盖、全局 dispatch 拒绝及现有审批/进程预检/worker 定向回归 41 passed、6 skipped（10.01s），Ruff 与 diff 检查通过。证据保留 .workbuddy/test-runs/isolated-tool-approval-real-1、isolated-tool-approval-real-2。
- “真实审批”指实际高风险门禁、审批存储/决定/消耗和恢复链路，决定由测试显式模拟操作者；不代表生产 UI/CLI 用户交互已验收。模型仍为脚本，进程/FileBroker 授权仍为测试策略，并未覆盖 Docker image/run 的生产审批或已运行容器后的导入审批恢复。没有批准生产任务、Git 应用或成长发布，CLI 生命周期与成长报告权威交接仍待实现。

## 完整编码图：实际 Runtime／AgentLoop／Temporal／Docker 联测（2026-10-14）

- 新增明确 opt-in test_isolated_coding_temporal_integration.py。使用实际 create_runtime/start、扫描器、ExecutionGraphBuilder、Runtime.execute/execute_node、NodeExecutor、AgentLoop/ActionExecutor/ToolExecutor、独立实例 worker、SQLite 节点占用与真正 Docker/文件导入；不再用固定节点处理回调代替 Runtime 或 AgentLoop。
- 模型为固定 ScriptedAdditionModel（不调用 API），工具/进程策略为明确测试专用 CodingFixturePolicy；命令工具 high-risk 生产审批不因这个测试改变。使用既有固定本地镜像，不 build/pull；新建源代码/实验 clone 和专用 17233 namespace/队列，不运行历史任务。V5 source ID 是逻辑 fixture ID，不是已核验历史业务源任务。
- 实际执行 READ → SANDBOX → PLAN → TEST_DESIGN（冻结旧代码 JSON 基线不通过）→ AGENT（AgentLoop 发工具调用、容器修改、受控导入）→ EVALUATION（保留真实扫描所得 pytest 辅助检查 + JSON 验收/回归/demo）→ APPROVAL pending/waiting。APPLY 未执行，任务未标记 completed，原项目和实验主仓库 app.py/HEAD 未应用变更；提案只含 app.py。
- 两轮实际专项分别 1 passed（11.16s）和 1 passed（11.24s）。第二轮记录 3 次固定模型调用、1 次修改命令；重复 Runtime.execute 没有增加模型调用、命令、节点执行、提案或 Temporal 提交记录。实际服务历史回放通过。
- 成功完成的测试容器按 exact recorded name、completed 记录、stopped 状态和角色核验清理；未知/活动记录不删除。证据保留 .workbuddy/test-runs/isolated-full-temporal-1、isolated-full-temporal-2，包含 SQLite、轨迹、history.json、容器检查和 temporal-coding-smoke.json。
- 默认入口/工具/worker 回归 68 passed、5 skipped（9.90s），真实环境选项缺失时不会连接服务或启动容器；Ruff 与 diff 检查通过。完整仓库套件本轮未跑。
- 该证据证明固定脚本模型和测试策略下的实际整图链路，不证明生产模型效果、真实人工审批/命令导入恢复、镜像 commissioning/供应链、CLI 进程托管或候选验证报告权威交接。通用旧验证 dispatcher 在此 fixture 是明确拒绝映射且调用次数为 0；实际命令/JSON/pytest 分别走专用会话，不把空回调称为生产就绪。Git 应用与成长发布继续禁用。

## 隔离 AGENT 的节点级命令工具（2026-10-14）

- 新增 IsolatedCodingCommandTool：仅公开 bounded structured argv/受限 relative_cwd，不允许模型传 task、node、镜像、slot 或权限字段；固定位 evaluation-0，每个工具实例一次尝试，不动态增加槽位。cwd 经过冻结 oracle/private 路径保护，实际执行仍由 PlanBoundCodingCommands 校验持久化沙箱、任务和 owner。
- NodeExecutor 只在 isolated AGENT、已装配 host 命令接口且节点工具 allowlist 允许时加入节点 context；PLAN/SUBTASK/普通任务不新增该工具，无全局注册。Runtime 显式转发接口，独立 worker 在通过装配检查后安装真正计划绑定命令映射。既有验证 dispatcher 仍为单独的必要接入组件，未被此工具替代。
- 工具保持 high risk，普通 ToolExecutor policy 与 ProcessBroker 实际命令审批仍生效，不存在自动批准。成功要求可信 process 结果及 imported=True，返回 validation_committed=False；退出码 0 但未导入不算成功，审批 ID 原样走既有等待机制。
- 引入窄类型 ToolExecutionUnconfirmed。未知命令/导入异常不会被通用 ToolExecutor 吞为普通 tool failure，会发布不确定执行事件并抛到节点占用层；定向测试证明持久化 guard=uncertain，换 resume key 仍不重复调用。普通工具异常的原有转换行为不变。
- Docker 固定图 AGENT 回调通过真实 tool.invoke → 计划映射 → 会话/Docker/受控导入；真实专项 1 passed（8.33s），验收/回归/demo 通过后 pending 审批，原项目未变，重复图进入无新命令/提案。此回调没有经过生产模型和实际 AgentLoop/Temporal 整图，不宣称完整编码 E2E。
- 工具/冻结上下文/worker/节点占用/执行门禁回归 78 passed、4 skipped（13.33s）；Ruff 与 diff 检查通过，容器证据保留 .workbuddy/test-runs/coding-tool-docker-1。成功后的多次修改仍需拆分节点；命令已完成但导入等待批准的单独恢复握手尚未实现，不能重跑原命令。Git 应用、成长验证交接和发布禁用。

## 计划绑定的编码修改命令映射（2026-10-14）

- 新增 execution/coding_commands.py 的 PlanBoundCodingCommands，独立于验证命令 dispatcher。仅接受与构造时输入指纹一致的任务、RUNNING AGENT 节点、持久化 active owner、host evaluation-N slot 和结构化 argv；从同任务唯一 completed/guarded SANDBOX 输出解析目录，cwd 必须为沙箱内现有目录。
- 映射复用实际 build_coding_session_factory/CodingNodeSession 的冻结导出、固定镜像、Docker 节点占用和 FileBroker 导入，不自行执行 host 命令或放宽策略。完整返回 process/import/imported，不把容器 exit 0 等同于导入、验收或成长验证完成。验收和辅助构建检查仍使用既有隔离验证接口，不通过修改接口导入其产物。
- 编译图 Docker smoke 的固定 AGENT 回调改为调用这个真实映射。使用已有固定本地镜像真实专项 1 passed（8.42s）：容器修改 → 受控导入 → JSON 验收/回归/demo → 审批 pending，原项目/实验主仓库未应用；重复图进入没有新增命令或提案。测试仅清理自身记录中明确 completed、已停止且角色核验的容器，保留证据。
- 命令映射/会话装配/dispatcher/import/隔离节点会话定向回归 35 passed（12.12s），Ruff 与 diff 检查通过。边界单元测试的 session.execute_command 是明确测试替身；实际容器证据在 .workbuddy/test-runs/coding-command-mapping-docker-1。
- 未自动安装 model-visible 命令工具、普通 Runtime 入口或生产 dispatcher；本轮真实 Docker 图仍由测试驱动编排，不是完整 Runtime/Temporal 编码 E2E。后续要把此受控接口接到实际 AGENT 工具与独立 worker，并校验审批等待/恢复；Git 应用、成长验证交接及发布禁用。

## 独立 worker 连接实际 Runtime 的读取节点（2026-10-14）

- 新增专用 17233 服务 opt-in 场景：使用真正 create_runtime/start、独立实验 settings/SQLite、真实 CLI OS 身份、工具与技能运行时快照、独立 clone 和冻结 oracle，装配 IsolatedCodingWorker 后以真实 TemporalOrchestratorBackend 提交固定 READ 图。没有替换 Runtime.execute_node、NodeExecutor、NodeExecutionGuard 或读取代理。
- 实际读取 app.py 内容落库，节点 attempts=1，持久化 execution_guard=completed；重复提交节点记录及提交记录完全不变，历史回放通过，没有变更提案，源项目与实验文件未变。服务端测试历史保留于实验 history.json。
- 此图只包含 READ，不是完整编译编码图。编码镜像 ID 是明确未 commissioning 的绑定占位符；调度组件采用真正 IsolatedNodeDispatcher，但命令映射是显式拒绝测试配置，调用次数为 0。没有 inspect/run Docker 或调用模型，不可据此宣称容器命令路径就绪或编码/成长验收完成。
- 真实专项 1 passed（3.28s）；默认独立 worker、会话装配与隔离准入定向回归 64 passed、4 skipped（15.47s），真实服务 opt-in 项未配置时跳过。Ruff 与 diff 检查通过，证据保留 .workbuddy/test-runs/isolated-worker-real-runtime-1。
- 实验 worker 运行结束后已取消/等待 SDK worker 退出并停止 Runtime；未操作应用服务、镜像或其他容器。下一步仍要实现受控命令映射，连接固定 Docker 镜像及完整编码图；Git 应用、成长验证交接及发布保持关闭。

## 独立 worker 的真实 SDK 注册与准入拒绝（2026-10-14）

- 通过 IsolatedCodingWorker.run 启动真实 SDK Worker，注册实例绑定 run_sprout_node，连接限定专用 17233 服务、独立 namespace/专属队列。新增 bound、foreign、drift 三场景；节点 Runtime.execute_node 与 dispatcher 为明确测试替身，不运行模型或容器。
- 其他实验 task ID 抛出 non_retryable ApplicationError（IsolatedWorkerScopeMismatch），避免 SDK 默认重试明确范围错误。当前实验任务/配置/冻结回归等准入异常返回资源等待，未知业务执行异常不在该捕获范围内，仍交由原节点占用规则处理；不暴露任意异常详细信息或配置内容。
- 实际服务历史确认每个场景仅调度一项活动：bound 调用目标一次；foreign 不调用目标且服务端失败标记不可重试；镜像绑定漂移不调用目标、返回 isolated_worker_binding_changed 等待。三份历史均回放通过。退出时取消/等待 SDK worker 结束，不触碰共享 activity Runtime 或应用服务。
- 默认边界/会话专项 18 passed、2 skipped（14.99s，新增第三个 opt-in 场景前）；首轮真实 SDK 2 passed（4.20s），补齐 drift 后真实 SDK 3 passed（5.10s），Ruff 与 diff 检查通过。当前完整单元套件未重跑。证据保留 .workbuddy/test-runs/isolated-worker-sdk-real-2，服务端 namespace 历史按 1 天保留。
- 这证明实例注册、单实验路由和无效准入不重试，不是实际 Runtime/Docker 编码成功证明。受控命令调度器、完整计划图在真实 Runtime 中执行、显式恢复握手和 CLI/lifecycle 托管仍待接入，Git 应用和成长发布禁用。

## 单实验独立 worker 装配入口（2026-10-14）

- 按用户确认的专属队列/独立 Runtime 方案新增 orchestration/isolated_worker.py：队列由 task ID 与 V5 计划指纹确定；SDK 活动绑定 worker 实例，不使用 set_activity_runtime 全局变量，只注册执行图工作流和 run_sprout_node，不注册成长/发布活动。
- 构造校验显式 endpoint/namespace、专属队列、实验目录内 operational/metadata/session/context/usage 独立 SQLite、禁止外部 MCP、callable 调度器和固定编码镜像。通过实际 build_coding_session_factory 的身份、配置和运行时快照检查后才安装真实会话工厂；Runtime 排他绑定单实验，禁止第二个 worker 覆盖。
- 共享 coding_task_binding 与会话装配一致的输入指纹，活动在节点执行前拒绝其他 task ID、任务输入/计划漂移、配置漂移、镜像/存储漂移及 Runtime 所有权变化。会话 prepare 仍独立校验持久化任务、活跃节点 owner、仓库及冻结回归。
- 定向边界、会话装配和只读 readiness 测试 25 passed（17.77s），Ruff 与 diff 检查通过。测试中命令调度器和 Runtime.execute_node 是明确测试替身，不能据此证明生产调度器已实现或真实 SDK/Docker 编码闭环已通过。
- 入口要求调用方提供已启动、独占、已显式装配 dispatcher 的 Runtime，调用方负责停止/关闭；本轮未新增 CLI 自动启动、进程后台托管、候选技能恢复或新的 dispatch key。没有运行真实编码 worker、生产模型、Git 应用或成长发布。下一步需要真实 SDK 对此实例绑定活动的注册联测，再连接受控命令调度及显式恢复握手。

## 隔离 worker 等待原因的持久化（2026-10-14）

- Runtime 拒绝未装配编码实验时输出稳定 waiting_reason=isolated_worker_not_assembled；Temporal backend 仅从实际等待 node_id 对应的资源等待输出提取该白名单原因，保存在 resolved 提交结果中。其他节点、审批等待、任意原因和畸形 nodes 不作为该诊断。
- 此字段仅用于解释已知拒绝，不授权重试、不自动生成新 dispatch key、不释放已有执行占用；未知副作用依旧不得自动重跑。没有改变工作流活动调度序列或增加回放 marker。
- 定向 worker 准入与 Temporal 提交回归 68 passed（0.75s）；专用 17233 真实拒绝/重启去重专项 1 passed（1.05s），确认原因实际落库并跨 Runtime 重启保留，节点无新增执行、提交记录无新增。Ruff 与 diff 检查通过，证据在 .workbuddy/test-runs/worker-wait-reason-real-1。
- 成功编码图及可信会话生产装配仍未完成。等待原因解决可观测性，不代表组件已就绪；完整装配和明确的恢复握手仍需后续实现，Git 应用与成长发布继续禁用。

## 真实 Temporal：未装配编码 worker 的拒绝与持久化等待（2026-10-14）

- 新增 opt-in 真实服务场景：专用 127.0.0.1:17233、独立 namespace/queue、临时 Git 项目与新建 code 任务。刻意缺失的 replay_plan 是拒绝输入，不是可信执行计划；直接通过真实 TemporalOrchestratorBackend 提交固定 AGENT → APPLY 图，独立验证 worker 活动入口，不绕过任何实际执行授权。
- 真实 run_sprout_node/Runtime.execute_node 返回 waiting_resource。节点持久化记录与执行前完全相同（未 claim、attempts 不增加），没有变更提案；服务端历史仅调度一项活动，APPLY 未调度，历史回放通过。
- 关闭并重建 Runtime、重开 SQLite，在没有运行 Worker 的情况下再次提交同图，返回原有等待结果；提交记录与节点记录完全不变，原文件未变。此项证明已知拒绝结果可去重恢复，不代表后来安装组件后自动重新执行已经关闭的等待工作流。
- 真实专项 1 passed（1.06s）；默认环境 worker 准入、Temporal 提交和联测文件回归 63 passed、8 skipped（0.79s），跳过项均未配置真实服务 opt-in。Ruff 与 diff 检查通过。证据保留 .workbuddy/test-runs/coding-worker-temporal-1，服务端测试历史按 namespace 的 1 天保留策略保留，没有清理服务或业务数据。
- 仍未实现完整可信编码图的 Runtime/Temporal 成功执行和审批等待联测；没有运行生产模型、安装生产隔离会话、执行 Git 应用或成长发布。后续要明确组件装配与恢复 dispatch key 的握手，不能以临时空回调绕过准入。

## Temporal worker 的隔离实验准入（2026-10-14）

- worker 直接调用 Runtime.execute_node，不经过提交端检查。对 code 模式且存在 replay_plan 或 replay_source_task_id 标记的实验任务，新增 worker 本地隔离模式、callable dispatcher 与 callable session factory 检查；缺失时返回结构化资源等待，不占用节点、不调用普通执行器、不产生未知副作用记录。即使标记内容为空或畸形，也不会因此降级为普通任务。
- 已完成/跳过节点保留原有缓存返回；普通 read_only 链路不要求隔离编码组件。组件存在仅是必要条件，不代表镜像可信、计划有效、写入授权或生产就绪，后续会话绑定及节点证据校验仍必须执行。
- 隔离入口、任务模式、节点占用和会话工厂回归 78 passed（8.31s），静态检查及 diff 检查通过。另在 17233 专用服务真实运行 replay 与 restart-terminal 两场景，2 passed（1.37s），证明普通只读调度/重启恢复未被此次检查阻断。
- 新增 worker 拒绝测试仍是 Runtime 入口的定向单元测试，不是完整 Docker 编码任务的 Temporal E2E，也未证明缺组件等待后的重新调度恢复。没有启用生产装配、Git 应用或成长发布。

## 真实 Temporal：Runtime 重启后旧快照恢复（2026-10-14）

- 只读 inspect 确认专用 sprout-temporal-validation-temporal-1 在运行且 Compose 归属正确；连接限定 127.0.0.1:17233，使用独立 namespace/queue、新建临时项目和 echo 模型，不访问生产模型、历史业务任务或应用默认 7233 服务。
- 在现有真实服务测试增加 restart-terminal 场景：真实 Runtime 提交只读图，真实 Worker/activity 完成；关闭并重新创建 Runtime、重开存储，传入错误工作区与旧指令/等待状态的 Task 快照。恢复返回 completed，持久化节点记录和提交记录与重启前完全相同，原项目文件未变；实际服务历史回放成功，变更历史的反例仍报不确定性错误。
- 首轮专项 1 passed / 1.26s；第二轮同文件已有 replay、worker-kill、ack-lost-open/closed 与新增 restart-terminal 共 5 passed、2 skipped / 19.27s。两个服务停机故障场景因未开启显式 Docker fault opt-in 跳过，没有停启验证容器，也没有修改应用服务。Ruff 与 git diff --check 通过。
- 证据保留在 .workbuddy/test-runs/temporal-restart-terminal-1 与 temporal-restart-terminal-2，包含隔离 SQLite、trajectory 与 history.json；专用验证 namespace 按 1 天历史保留策略创建，不宣称已清除服务端历史。
- 这是实际 Runtime/Temporal 的只读调度、终态恢复和持久化去重证据，不是隔离编码 Docker 图的生产调度证明，也不是等待审批后继续 APPLY 的验收。生产隔离编码装配、命令工具接口、Git 协调器及成长验证交接仍待完成。

## Runtime 审批恢复的权威任务绑定（2026-10-14）

- 接入检查发现 resume_task 虽读取最新记录，却仍使用调用方旧 Task 编译图。现改为使用持久化权威任务的工作区、模式、指令、身份、权限范围及验收配置；记录缺失直接阻止恢复。
- 完成、取消、回滚任务在任何隔离调度器配置下均直接返回，不查工作区、不重新编译或提交；image_preflight 专用恢复入口限制仍保留。此检查不是并发状态变化的完整事务保证，后续节点占用及持久化调度检查仍必需。
- 新增旧快照/终态、权威工作区与任务绑定、记录缺失回归。隔离入口、replay resume、Temporal 提交、显式任务类型及任务端到端专项共 87 passed（50.84s），Ruff 与 git diff --check 通过。
- 本轮仅修正 Runtime 接入前置问题，没有运行真实 Temporal/Docker 联测，没有安装生产隔离调度器，也没有启用 Git 应用或成长发布。下一步仍需专用验证服务上的固定任务调度及恢复联测，不能以 mock/probe 测试代替该证据。

## 编译任务图与真实 Docker 节点联测（2026-10-14）

- 新增显式 opt-in test_coding_graph_docker_integration.py，使用真实 ExecutionGraphBuilder、NodeExecutionGuard、生产会话工厂、NodeExecutor 的 READ/SANDBOX/TEST_DESIGN/EVALUATION/APPROVAL 处理、SQLite 与实际 Docker/文件代理。PLAN/AGENT 为固定测试回调，不调用生产模型；测试驱动按编译图顺序检查依赖，不替代或修改生产 Runtime/Temporal 调度器，也不让普通任务回退本地调度。
- 真实链路为 READ → SANDBOX → PLAN → TEST_DESIGN（旧代码基线失败）→ AGENT（容器修改/受控导入）→ EVALUATION（验收/回归/demo通过）→ APPROVAL（pending/waiting）。测试将任务记录为 waiting_approval，不自动审批或 completed；提案只含 app.py，实验主仓库和原仓库代码/HEAD 未应用变更。
- 综合证据检查确认当前 worktree 与冻结源输入、JSON 原始执行及空辅助计划一致，但 write_authorized=False。重复进入同一图时 completed/等待节点返回缓存，处理器调用数增加 0，coding 执行记录不变、提案仍只有一个；直接诊断 APPLY 仍返回 isolated_apply_adapter_required，不宣称执行了该未满足审批依赖的节点。
- 两轮真实专项分别 **1 passed / 8.17s**、**1 passed / 8.02s**，每轮 5 个容器（基线1、编码1、验收/回归/PoC3）。仅清理本次持久记录已 completed 且停止、正确 role 的容器；未知结果保留。证据保存在 .workbuddy/test-runs/coding-graph-real-1 / coding-graph-real-2 的独立实验目录，原服务与测试镜像未删除。
- 默认离线回归 **49 passed, 2 skipped**，Ruff/diff 通过。尚未验证生产 Runtime.execute/Temporal 实际调度、真实模型工具循环/候选对照、完整恢复和提交/回滚；这只是编译图与节点处理器/占用的真实容器联测，不是完整自成长端到端成功。生产入口、Git 写入和成长发布继续阻断。

## 首次真实 Docker 编码最小闭环与耗时样本（2026-10-14）

- 按已确认方案继续保留普通任务的原执行方式，只为显式自成长实验推进 Docker 后端；不增加第二套隔离后端，不启用容器池/复用/批处理。Docker Linux 引擎最初未运行，经用户启动后可用，服务版本 29.8.0；原应用服务未操作。
- 新增显式 opt-in test_coding_docker_integration.py：通过实际 ProcessBroker、DockerCodingBackend、SQLite 占用、CodingNodeSession/FileBroker 运行固定测试代码，完成独立副本修改 → 受控导入 → 新节点 JSON 验收/回归/PoC。每轮 4 个独立离线非 root 容器，实际 inspect 核验只读根文件系统、网络 none、1 CPU/512 MiB 上限与 user 65532；原目录与冻结测试文件保持不变。
- 经单独批准，使用已存在的固定本地验收镜像派生独立 explicit-smoke 编码测试镜像，仅设置角色/schema 标签，不下载或安装依赖。新固定 ID 为 sha256:11880faa03b5dda1f3f54a915ad1b0e3d2a97664799d7a9bfcd3790a9dc7a31d，tag sprout-coding:smoke-v1；与原镜像全部 RootFS layer ID 相同，镜像 Size 约 195.3 MB 不代表额外复制一套文件系统。此次使用本地旧构建器且收到弃用警告；不能据此宣称正式镜像构建流程已标准化。
- 真实最小测试先后 **1 passed / 4.68s**、**1 passed / 4.74s**，使用不同证据目录，不覆盖首轮。第一轮编码+导入 747.6 ms，三项验证 2533.5 ms；4 条实际容器命令整体 559.2/677.5/660.0/690.2 ms。测试总耗时还包含初始化/持久化/检查清理，单条计时并非纯容器启动延迟；没有 Docker Desktop 冷启动、常驻 VM 内存或容器实际内存峰值测量。
- 每轮仅按持久记录检查本次创建、正确 role、completed 且已停止的容器并删除，未知/运行中容器保留，不 force，不枚举删除其他服务。两轮共 8 个测试容器已清理，metadata.db、原始进程结果、inspect JSON 与 coding-smoke-metrics.json 保留在 .workbuddy/test-runs/coding-smoke-real-1 / coding-smoke-real-2。测试镜像保留供后续显式验证。
- 默认离线专项 **31 passed, 1 skipped**，真实 Docker 测试缺显式环境变量时跳过；Ruff/diff 通过。本轮首次推进真实编码/导入/IO 验收组件闭环，但测试策略显式授权固定 fixture，未调用生产模型、未跑完整 Runtime/Temporal 图或真实候选对照，未实现真实 Git 提交/回滚，也未授予成长验证/发布完成状态。生产入口与 commissioning 就绪仍不自动开放。

## 自成长实验的模型工具冻结边界（2026-10-14）

- 成长/演化即经验与技能层面的 Agent 自成长：任务经验形成候选、显式隔离验证、审批后发布；不涉及训练模型权重，也不是自动无审批重写 Agent 核心。当前验证观察不自动传入成长可信验证/发布权威。
- 新增 execution/isolated_model_tools.py，隔离 AGENT/PLAN/SUBTASK 的 read/write/edit/patch 工具限制显式冻结 oracle、源/实验生成验收文件、Git/runtime/private 路径；路径大小写、Windows 分隔符、父目录/绝对路径/ADS、含糊尾点/空格与链接保守拒绝，并核对解析后的路径。完整多文件 patch/rename 目标在第一次委托 I/O 或删除审批前校验，拒绝时不先修改其他文件。
- 隔离 list/search 采用受保护遍历器，进入目录和读取文件之前过滤，而非读取后仅隐藏结果；普通工具 path_guard 默认为 None，保留原行为。节点已有 READ 资源在进入模型消息/上下文前过滤；冻结验收提示只展示用例输入/目标，不展示 expected_output。Git/未知模型工具不宿主回退。
- 生产联查发现编排节点 ID 使用 task-id:evaluation 等冒号格式，原会话目录规则无法支持。新增共享 coding_node_directory_name，对受限编排 ID 用完整 SHA256 生成目录名；原 UUID/简单 ID 路径不变，持久节点 ID/owner/slot 不改。会话与证据检查共用映射，工厂测试改用真实编排 ID 形状。
- 专项 **100 passed**，覆盖真实节点消息/上下文不暴露冻结答案、模型路径与多文件 patch 拒绝、list/search 过滤、普通工具/执行门禁回归、编排 ID 会话装配和证据核验。模型/容器 runner 为测试桩；没有全量或真实 Docker端到端验证。Ruff/diff 通过。
- 这不是 OS 级防护或任意副本检测，也不删改用户任务指令中主动提供的预期值；无法宣称识别所有变形/复制后的答案。生产入口尚未安装会话工厂与完整命令分派，编码镜像 commissioning、实际提交协调仍未完成；不因本轮工具保护而启用运行或发布。

## 计划绑定生产会话工厂（2026-10-14）

- 新增 evolution/coding_assembly.py，构造可供 Runtime 安装的生产 CodingNodeSession 工厂，绑定 v5 计划、当前 OS CLI 原创建者、真实 runtime 配置/实现快照、固定编码 image、冻结 regression、任务输入/预算/授权范围与受控 FileBroker；不从模型参数选择镜像、目录或 oracle。
- 每次 prepare 重读持久任务/工作区与实时节点 active owner，核对输入摘要及 running 状态，再重验当前身份/配置/快照、冻结回归和托管 worktree 指针。按既定独立 coding-evidence 布局创建导出，复用原节点会话、占用与导入实现，不建立另一套执行权威。
- 该构造器不启动 Runtime、不安装分派器、不启用实验入口，也不访问 Docker。模型编辑工具冻结文件保护、镜像 commissioning 与实际提交协调仍未完成；只有工厂可构造不等于生产链路已运行。
- 首次补跑中断测试暴露 DelegationScope 的 frozenset 无法直接 JSON 摘要；将集合规范化为排序列表，不用字符串化掩盖权限差异。测试使用非空权限集合，验证持久 roundtrip 稳定及权限变化阻断。专项 **28 passed**，Ruff/diff 通过；容器 runner 和 Runtime 接口为测试桩，没有全量或真实 Docker验证。

该链路在项目中的位置：CLI evolution/replay-compare → evolution 候选实验管理 → Runtime/编排节点 → execution 容器会话 → 验收与持久证据 → changes/Git 审批应用 → 成长验证/发布门禁。当前未将观察报告自动传入 candidate_manager.record_validation，也未开放 approved/published。

## 当前阶段与实际进度（2026-10-13）

近期集中处理的是成长候选的显式隔离编码实验：在独立 revision 上执行编码、IO 验收/回归/PoC、辅助检查，核对证据后仅向实验克隆仓库提交。不是将普通任务全面容器化，也不是自动开放成长发布；本段不是全项目重新审计结果。

| 链路环节 | 当前状态 |
| --- | --- |
| 固定计划/创建者/配置绑定 | 已实现持久契约与保守恢复检查；历史缺证明不升级 |
| 容器命令、节点占用与产物导入 | 组件已实现，部分集成依赖测试桩；未完成生产装配 |
| IO 验收、独立回归与最小 demo | 节点会话接线及持久原始输出核验已实现 |
| 测试/构建辅助检查 | 独立可写副本、清单冻结与记录交接已实现 |
| 实验 Git 提交/回滚 | 只读预检、占用存储、精确审批核验已实现；实际写入未开放 |
| 生产会话与固定编码镜像 | 分派器/会话工厂/镜像绑定未实际装配；编码镜像未 commissioning |
| 完整运行与成长发布 | 新隔离编码链路未完成真实端到端验证；发布继续阻断 |

下一阶段应优先收敛为生产装配与真实容器验证，而不是继续增加独立检查器；随后接审批消耗/事务占用/commit-revert/提案状态协调，做全量及完整实验回归。不能按组件测试数量估算项目完成百分比。

## 编码运行时只读就绪诊断（2026-10-13）

- 新增 evolution/coding_readiness.py，复用 v5 计划、创建者、配置和实现/工具/技能快照检查；同时检查 isolated 模式、实际 runtime 分派器/会话工厂与固定编码 image 绑定。只读，不启动 runtime、不访问 Docker、不下载镜像、不申请/消耗审批，不自动安装回调。
- 诊断区分 configuration_and_assembly_checks_passed 与 ready_to_execute；测试桩 callable 和匹配镜像字符串不能作为 commissioning 证明。当前 ready_to_execute/write_authorized/validation_authorized 始终 False，保留 commissioning 与 Git 写入协调缺口；该诊断目前尚未接生产 CLI/执行入口，不改变既有阻断行为。
- 专项 **45 passed**，覆盖装配缺口、测试桩不能证明就绪、身份/配置/image/模式变化、损坏计划不升级及原配置/快照/profile/审批门禁；Ruff/diff 通过，未做全量或真实 Docker编码验证。

## 隔离辅助检查原始记录交接（2026-10-13）

- 新增 isolated_auxiliary_evidence.py，对 EVALUATION 冻结的 test/build/verification 清单逐项核验 auxiliary-plan 的 owner、镜像、输入 manifest 与摘要；独立副本 baseline seal 与原始输入一致，每个稳定 slot 必须有匹配的 coding intent、completed 执行和可信启动标记。不以汇总报告的 passed/verified 标记替代原始记录。
- 抽取 isolated_process_evidence.py，JSON 验收和辅助检查共用请求/输入摘要、完整 Docker launch 重建及 process.command/启动/输出边界核验，避免维护两套规则。辅助副本允许执行后保留安全构建产物，不要求可写副本仍等于执行前输入，不导入这些产物；不安全副本资源仍会由既有容器计划检查保守拒绝。
- 辅助结果按容器内实际 argv 分类，而不是 Docker 外层 argv，复用既有 classify_verification_command；pytest exit 5/跳过项、执行错误或输出脱敏不能算完整通过。原运行端同步修正，避免新报告继续误判。
- 隔离节点对已装配会话即使没有辅助命令也调用 verify_auxiliary，冻结明确的空清单；检查器要求持久空计划，不把缺计划解释为“无检查”。畸形空声明也严格拒绝，普通任务不变。综合源代码证据检查包含辅助记录摘要，仅移除已解决的辅助交接 blocker，保留 runtime commissioning/write coordinator 阻断。
- 专项 **60 passed**，含真实临时 Git/SQLite 与真实 DockerCodingBackend 占用逻辑（容器 runner 为测试桩）、独立构建副本修改后交接、实际失败/pytest 跳过/空测试拒绝、缺/未知/旧启动记录拒绝、空计划缺失和清单/seal 漂移，以及既有节点/验收/事务门禁回归；Ruff/diff 通过，无全量或真实 Docker验证。
- 当前仅建立观察一致性，不宣称能防止候选程序伪造自身测试日志，也不把辅助命令退出码当作独立 IO 验收。生产会话/镜像 commissioning 与配置兼容核验、审批消耗/事务协调、实际 commit/revert/提案更新仍未实现；write_authorized/validation_authorized 保持 False，成长发布未开放。

## 隔离验收原始输出与待提交源码绑定（2026-10-13）

- 新增 execution/isolated_git_evidence.py，只读读取 completed/guarded EVALUATION、导出 intent/seal、冻结 case plan 与每个 acceptance/regression/PoC 的 coding intent/execution；要求 v5 固定编码镜像、task/workspace/repo/source 绑定和相同 owner，不从报告 passed/verified 或用户 JSON 推断通过。
- 重算当前 Git 只读预检、源码受保护导出 manifest 和持久副本 manifest；冻结回归文件按任务摘要重新读取，验收 spec 与 case plan 精确匹配。重建每条输出型命令和完整 Docker launch 安全参数，仅容器随机名称允许由原记录固定；核验请求/输入摘要、启动证明、已完成状态和原始 process.command，再由宿主 JSON oracle 重判实际 stdout。检查后再次重验源码和输入，输出一致性摘要但不授予写入/成长验证权限。
- 新增 isolated_case_contract.py，共享输出型命令与 public demo 生成，避免运行和核验实现漂移；验收 case contract 新增 image 绑定。旧记录缺字段不补造。MetadataStore 增加只读 get_coding_intent，不改变实时占用/执行权限。
- 联测发现实验根目录内包含代码 worktree，不能直接作为 CodingNodeSession evidence。保留既有“不重叠”约束，检查器固定使用独立 experiment/coding-evidence/coding-node-<id> 布局；测试会话同步采用该布局，尚未将生产入口装配成可运行。
- 专项 **35 passed**，涵盖真实临时 Git/SQLite 及真实 DockerCodingBackend 占用逻辑下的源码绑定、源码/副本/oracle 漂移、伪造通过报告、缺/未知执行与旧启动证据拒绝、原节点验收/会话/审批回归。容器 runner 为测试桩，未运行真实编码 Docker或全量；Ruff/diff 通过。首次联测暴露布局重叠和测试试图覆盖已占用节点被原 guard 拒绝，均未放松门禁：改独立目录，并在受控测试回调结算前提供假报告验证原始输出重判。
- 该模块目前只证明观察记录与源码的一致性，仍缺辅助检查交接、镜像 commissioning/配置兼容核验、审批消耗与写入协调，以及实际 commit/revert/提案状态更新。即使 evidence_binding_passed=True，write_authorized/validation_authorized 仍 False；实验应用与成长发布继续阻断。

## 实验 Git 精确审批绑定核验（2026-10-13）

- 新增 execution/isolated_git_authorization.py，审批参数绑定实验边界、task/proposal、commit/rollback、目标路径、replay 摘要、只读 Git 计划摘要、提案快照摘要和验收证据摘要；不使用普通 proposal_id/risk 审批或“类似操作”授权替代精确绑定。
- 从 OperationalStore 读取审批，要求已批准、未使用、single_use、有未过期时限、精确 action hash/参数 hash/task/resource scope，且请求者/决定者与原 CLI 创建者一致；当前 Principal 必须是可信 CLI 原创建者。畸形参数、额外 approved 标记、旧审批、跨身份/操作/证据变化均阻断。这里比较的是持久审批记录与受信任入口 Principal，不从用户 JSON 建立身份。
- 检查只读，不请求/批准/消耗授权，不取得 Git 事务占用或执行 Git。即使 approval_binding_passed 为 True，write_authorized 恒为 False，仍返回 measurement_handoff_not_established 与 write_coordinator_not_assembled；审批绑定通过不是可信测量或写入证明，运行入口未接线。
- 共享摘要函数移到 execution/git_transaction_contract.py，存储和执行控制依赖同一个纯契约；审批模块不依赖具体 SQLite 提供器，保持存储分层。
- 专项 **46 passed**，包含真实 ApprovalManager + MemoryOperationalStore 生成/决定记录后只读核验、审批漂移/到期/已用/身份阻断，以及 Git 占用、只读计划和编码占用回归。首次测试对更严格的畸形参数阻断原因断言不符，修正为验证有效操作漂移和独立缺记录场景后通过；Ruff/diff 通过，无全量或真实编码 Docker验证。
- 当前仍缺源代码绑定的验收证据交接、写入前审批消耗与事务占用协调、实际 commit/revert 和提案状态更新；不能把本轮检查器表述为提交适配器已可写。实验 apply/rollback 与成长发布继续阻断。

## 实验 Git 事务持久占用与快照绑定（2026-10-13）

- 新增独立 sqlite/git_transaction.py，通过 MetadataStore 契约提供提案快照、事务读取、claim 与 owner-CAS settlement；专用表不复用容器命令或 completed-only 测量占用。事务保存完整原始提案快照、只读计划及各自摘要、replay 摘要和审批引用，避免后续状态改变抹去原始证据。
- 原子 INSERT 要求持久 running/code/v5 任务与实时 running/APPLY 节点 active owner，workspace/root、replay fingerprint 与提案所有列精确匹配；提交仅 approved、撤销仅 applied。同一提案/操作只占用一次，workspace 与目标路径的 active/uncertain 唯一占用阻止其他提案绕过；无过期释放、强制重跑或 not_started 重置入口。
- 只有原 owner 可以将 active 结算为 completed/uncertain；completed 必须提供完整基线和新提交身份并匹配冻结计划。uncertain/完成记录不能再次结算或重领。存储完成不自动把提案标记 applied/rolled_back，也不授予验证完成状态。
- 专项 **27 passed**，覆盖并发单一占用、关闭重开保留未知结果/完整快照、提案/owner/root/replay/任务漂移拒绝、跨提案阻断、撤销状态与结果绑定，以及只读 Git 预检和编码占用回归；Ruff/diff 通过。并发测试为同进程两个 store，不宣称已经验证进程崩溃后的真实 Git 副作用。
- 审批 ID 在存储层只是审计引用，不是授权证明；真实审批、源码/验收 provenance、持久计划完整校验仍需提交适配器负责。实际 commit/revert 与提案原子状态更新未接入，实验应用入口保持阻断，没有对当前项目执行 Git 写操作或开放成长发布。

## 实验提交/回滚只读预检（2026-10-13）

- 新增 execution/isolated_git_plan.py，复用隔离 Git 配置、hooks/filter 防护与 worktree 指针检查，仅允许 status/diff/rev-parse/rev-list/ls-files；拒绝 commit/revert/reset 等写操作。必须是与原项目不重叠的 evidence/repo，路径条件只是宿主装配约束，不是审批或可信计划证明。
- 提交预检要求目标干净且无进行中 merge/cherry-pick/revert，托管沙箱 HEAD 与目标一致；未明确进入差异的新文件阻断。输出基线提交、差异摘要、受保护导出 manifest，write_authorized 恒为 False，不修改 index、文件或 HEAD。
- 回滚预检要求目标 HEAD 精确等于已记录 applied_commit，且该提交只有一个父提交、精确等于 base_commit；输出 new_revert_commit 策略及当前验证失效标记。不撤销提交、不重置仓库，不将旧验证报告改写成新状态。
- 专项 **17 passed**，包括真实临时 Git 仓库上的预检不写入、变化摘要、脏目标/新文件阻断、精确回滚绑定、原项目目标拒绝，以及隔离 Git 管理和节点验收回归。首次运行一个测试夹具漏传 Workspace.id，修正后全部通过；Ruff/diff 通过，无全量或 Docker验证。
- 尚未接入持久事务占用、审批/验收摘要绑定、实际提交/撤销或提案状态更新；ChangeProposalService 与节点 APPLY 的实验入口仍阻断。本轮只读计划不得被表述为提交/回滚已经可用，成长发布仍关闭。

## 隔离辅助检查的独立可写副本（2026-10-13）

- 新增 execution/isolated_auxiliary.py，严格解析 test/build/verification 命令与引号参数，畸形必需项不静默跳过。宿主冻结完整命令清单、输入 manifest 与固定镜像，逐条创建独立 coding-input 和 baseline seal；不覆盖修补部分导出或变化副本。
- 每条命令通过原 DockerCodingBackend 执行，复用实时节点 owner、审批、持久占用与未知结果不重跑规则。仅该命令副本可写，没有产物 import；共享验收输入执行后重验，构建产物不会传给下一条命令或验收。依赖前序产物的命令应显式组合为一条受审批命令，而不能依赖隐式共享。
- CodingNodeSession.verify_auxiliary 与隔离 EVALUATION 接线，缺适配器继续等待；缺冻结回归配置先等待、不启动辅助检查。辅助结果与 JSON 验收结果汇入现有 VerificationReport，失败不能获得完整通过；不替代输入输出验收、回归或 PoC，普通任务不变。
- 专项 **49 passed**，涵盖独立副本、已知失败、审批等待、未知结果停止、严格命令解析及节点报告失败门禁；Docker 后端在副本测试中为测试桩，相关真实占用逻辑另由原后端专项覆盖。Ruff/diff 通过，没有全量或真实编码 Docker验证。
- 不自动联网安装依赖，不靠 stderr 猜测依赖缺失；已执行的缺依赖命令保留失败证据并阻断完成，而不是伪造“未执行”。生产会话装配、编码镜像验收、实验提交/回滚仍未完成，成长发布门禁保持关闭。

## 隔离验收复用冻结设计且不污染仓库（2026-10-13）

- 隔离 TEST_DESIGN 在模型规划和写文件之前进入独立分支，直接归一化源任务冻结 acceptance_spec，并记录摘要；缺设计时等待配置，不重新生成测试或带预期值 demo。基线测量交给节点会话，证据留在独立目录，不向待测仓库写验收文件。
- 隔离 EVALUATION 要求 isolated_design 协议及冻结设计/摘要一致；旧格式设计不静默升级，漂移设计不继续执行。此处取代此前隔离分支对新生成仓库验收文件的 hash 校验，普通任务仍保留原流程。
- 编码导出额外排除 replay_source_task_id 对应的宿主生成测试/demo 路径，避免源任务遗留文件混入待测输入；不宣称能够识别所有变形后的 oracle 副本。
- 专项 **47 passed**，覆盖冻结设计复用、缺设计等待、不调用模型规划或仓库写入、节点验收及普通完成保护。Ruff/diff 通过；未运行全量或真实编码 Docker实验。
- 生产会话装配、辅助检查适配器、实验提交/回滚和编码镜像验收仍未完成；实际实验保持等待，成长验证与发布门禁未放开。

## 实验应用/回滚防回退与清理证据保留（2026-10-13）

- ChangeProposalService.apply/rollback 从持久 Task 读取保留的 replay 标记，对隔离实验返回未应用、适配器未就绪；包含 --force 的调用也不进入普通 ApplyBroker，不写 override 或提案成功状态。实验标记仅用于收紧边界，畸形标记同样保守阻断，不授予任何权限；普通提案不变。
- 隔离节点 APPLY 明确配置等待，不将通用 Git 提交/回滚当作已适配。当前没有实现实验专用提交/回滚，也没有执行 reset/revert 或改变真实仓库。
- SandboxLifecycleService 的终态 finalize、提案 cleanup 和启动 reconcile 识别持久实验任务并保留证据。终止且没有提案的实验 SANDBOX 节点仍计入 referenced roots，避免其他清理扫描误删其 worktree；不依赖内存标记在重启后维持保护。
- 专项 **36 passed**，覆盖实验强制应用/回滚不调用宿主 broker、不改提案，三条清理路线及无提案终态节点引用保留，以及普通提交/清理/执行门禁。Ruff/diff 通过，没有全量或编码 Docker验证。
- 剩余：实验专用提交/回滚与辅助检查适配器、生产会话装配和镜像验收。当前这些操作明确阻断，不能将本轮“封住入口”表述为“已实现提交回滚功能”；成长发布门禁未放开。

## 隔离 Git 管理适配（2026-10-13）

- 新增 sandbox/isolated_git_worktree.py，隔离节点创建 worktree 与审批阶段差异收集改用此提供器。只支持固定 Git 管理操作；禁用 hooks、全局/系统配置、系统 attributes、递归 submodule 与 textconv，并保留既有 pager/fsmonitor/外部 diff 防护。Git filter 配置不静默禁用，而是拒绝并要求显式适配；普通 GitWorktreeSandbox 不变。
- 每次命令核验主仓库 .git、worktree gitdir 与反向 gitdir 文件绑定；显式传 --git-dir/--work-tree，避免核验后再次依赖可能变化的 .git 指针。run_code 明确拒绝，必须使用节点容器后端。命令有时限，配置/命令失败不输出潜在私密原始 stderr。
- 专项 **38 passed**，包括真实临时 Git 仓库的 worktree 创建、checkout hook 禁用与文件差异、配置 filter 在创建前拒绝，以及普通沙箱/执行门禁回归；指针重定向通过模拟 read_text 验证，未强行修改 Windows 受保护 .git 文件。Ruff/diff 通过，没有全量或编码 Docker测试。
- 该适配器是宿主受信任 Git 管理，不是通用 Git 命令容器化；ApplyBroker 提交/回滚、生命周期清理与其他 Git 调用仍需审计。辅助检查适配器与编码镜像/生产会话装配尚未就绪，实验不能据此解除整体等待或发布阻断。

## 编码镜像与隔离协议 v5 计划绑定（2026-10-13）

- CLI replay-compare 增加 --coding-image，显式 code 实验可生成 v5 持久计划，绑定固定编码 image、node-json-v1 和 explicit_adapter_required 辅助检查规则。配置在 Git 克隆前校验，read_only 不接受编码镜像；没有自动拉取/构建或授权。
- replay_plan 规范化验证 v5 字段；不支持的协议、跳过辅助检查、布尔冒充 schema 版本或 mutable image tag 均拒绝，即使重算摘要也不允许。配置复制避免保留可变输入别名。成对条件增加 coding_profile 一致性检查，恢复 SQL 接受结构有效的 v5，但仍要求原有 creator/snapshot/owner 前置条件。
- 专项 **30 passed**，覆盖 v5 roundtrip、改镜像摘要失效、重算摘要后非法配置仍拒绝、旧计划不补造以及原计划/成对条件/恢复 claim/replay 回归；Ruff/diff 通过。没有全量或真实编码 Docker验证。
- 该 CLI 参数当前只记录编码边界配置，不代表生产 session_factory/命令分派已经装配。旧计划可只读审查，但不能被当作具备新隔离边界的证明。Git 管理/辅助检查和固定镜像验收仍未完成，实际实验继续等待，成长发布未放开。

## 隔离节点会话验收接线（2026-10-13）

- Runtime._executor 转发宿主安装的 coding_session_factory；隔离 TEST_DESIGN baseline 和 EVALUATION 分支调用会话.verify，而非在编码容器运行含预期值 pytest/demo。普通任务仍走原代码。验收设计文件/demo 的 host hash 继续重验，节点输出复用原 TestResult 与 VerificationReport，不新建另一套任务完成权威。
- CodingNodeSession 接收并归一化宿主显式回归 spec；缺回归的等待仍由节点验收适配器负责。原节点存在 test/build/verification 命令时，若没有额外检查适配器则明确等待，不能切换模式后静默遗漏既有强制检查，也不执行宿主 Git 静态发现。
- 专项 **47 passed**，含缺会话不回退、三类强制命令不遗漏、模拟会话结果复用既有完成报告及普通执行门禁/会话/完成保护；Ruff/diff 通过。测试装配的会话/容器部分为测试桩，没有全量或真实编码 Docker实验。
- 尚缺实验入口按持久计划创建生产 session_factory、固定编码镜像绑定/审批与辅助检查适配器。节点侧已接线并不代表实验入口已就绪，当前实际隔离实验仍保持未装配等待，成长验证/发布门禁未放开。

## 受控编码节点会话串接（2026-10-13）

- 新增 execution/coding_session.py，将专用导出、稳定节点分派、文件代理导入与宿主节点验收组合为一个宿主装配对象。每节点使用独立 coding-node-<id>/coding-input，先冻结源码 baseline/target/image/oracle 契约，再导出与独占 seal；不允许用覆盖修补部分导出或漂移输入。
- 排除显式冻结路径以及由宿主按 task_id 生成的验收测试/demo 路径。证据目录不得与代码目标/原项目重叠，node 目录名受限；成功命令才进入既有受保护 import，失败/未批准命令不写回。verify 单独调用节点 JSON 验收，保留普通完成状态和成长验证状态的区别。
- 专项 **20 passed**，包含真实 SQLite/文件代理串接（容器后端模拟）、冻结文件不导出、不完整 seal 不重建、源码变化不能替换 baseline、失败命令不导入及验收节点/镜像绑定。Ruff/diff 通过，本轮没有全量或真实 Docker编码测试。
- 该对象尚未生产装配到 Runtime；当前基线契约按节点冻结，不支持同一节点导入后静默更新 baseline 再运行新的修改命令。节点验收多用例走只读输入；多阶段可写命令必须由宿主显式拆分为新节点/契约，而不是覆盖旧基线。sandbox/Git 管理路径与编码镜像验收仍未完成，发布门禁关闭。

## 具体节点 Docker 分派器与 Git 防回退（2026-10-13）

- 新增 execution/coding_dispatch.py，将 NodeExecutor 回调绑定到 DockerCodingBackend，要求实时 task/node/active owner 与宿主生成 baseline-N/evaluation-N 编号。受信任 request_factory 不能改 task_id/command，不能由模型选择镜像、导出目录或编号；持久核验仍在后端执行，不以回调参数替代数据库权威。
- 隔离模式的编码/修复/子任务节点 SandboxGitTool 显式禁止宿主 Git；专用 Git 检查器尚未就绪时返回 no host fallback，不保留偷偷执行的兼容分支。普通模式默认行为不变。此处仅封住 agent 工具路径，不等于 GitWorktreeSandbox 的所有管理/差异操作已接管。
- 分派/后端/存储联合专项 **32 passed**；分派/Git工具/普通沙箱工具/执行门禁联合专项 **38 passed**。Ruff/diff 通过；本轮没有全量或真实编码 Docker测试。
- Runtime 尚未装配生产 request_factory：每步专用导出、冻结路径映射、节点 JSON 验收回调与产物同步仍需串接。当前隔离实验仍会因后端未装配等待，不宣称完整可运行，也不放开验证/发布。

## 可信未启动证据与受控重新授权（2026-10-13）

- ProcessResult 新增 started: bool|None。进程代理的策略拒绝/待审批及只预检明确 False，正常进程结果 True；旧记录或无法确认的异常仍 None，不按未启动处理。
- 编码后端在实际 run 阶段被拒绝且代理明确 started=False 时，持久 settle not_started；没有可信字段则 uncertain。not_started 重试必须请求/输入 signature 相同、节点/任务/owner 当前有效，并重新经过镜像与授权检查；不提供未知副作用重跑。
- coding_executions owner-CAS 支持 not_started，但仅接收 allowed=False、started=False、exit_code=None 的证据。重新 claim 时原拒绝结果与 owner 追加 no_launch_history，旧证据不丢弃；active/uncertain/completed 不因此放开，矛盾或缺字段证据不能置为未启动。
- 初始联合专项 **49 passed**；追加重新授权用例后的编码后端专项 **10 passed**。全量回归 **1655 passed, 23 skipped, 2 dependency warnings**（164.46 秒），仍排除两个原有缺示例模块的 test_bubble_sort.py/test_hello_world.py；全量开始采集后新增的重试授权用例由补充专项覆盖。Ruff/diff 通过，本轮未启用真实 Docker测试。
- 普通任务默认行为保留。完整 Runtime 分派、sandbox/Git 全路径接管及正式编码镜像仍未验收，不能据此标记成长验证完成或发布。

## 编码命令审批前置与已知未执行 owner 交接（2026-10-13）

- ProcessBroker.run 增加内部 preflight_only，完全复用策略/现有 task-scoped 审批流程，但获准后不创建进程。Runtime.authorize_task_process 转发持久任务 actor/source/scope；响应不是授权令牌，实际 run 仍重新检查策略。
- DockerCodingBackend 在 claim 编码命令之前执行授权预检，待审批/拒绝直接返回，不写执行占用、不启动容器；镜像检查待审批同样返回。普通调用默认行为不变。
- coding_intents 在读取冻结记录时再次核验当前 active 节点 owner。已知未执行的审批等待允许合法的新 owner 读取相同输入，但初始 owner 审计字段不改写，任何其他输入变化仍拒绝；unknown coding_executions 仍阻断更换 owner/slot 后执行。
- 专项 **27 passed**，包含真实 ApprovalManager 预检不 spawn、获准仍不 spawn、策略拒绝不被已有 grant 绕过，命令待审批不 claim，以及真实 SQLite 审批等待后 owner 交接/旧 owner 拒绝/原记录不改写。Ruff/diff 通过，没有全量或真实编码 Docker验证。
- 若策略在预检与实际 run 之间改变而拒绝启动，现有后端仍保守保留 settled 尝试，不自动重跑；须后续区分可信“未启动”证据与未知启动结果，不能直接释放占用。完整 Runtime 装配和编码镜像验证尚未完成，发布仍阻断。

## 隔离节点 JSON 验收与无预期值 demo（2026-10-13）

- 按用户确认的兼容方案新增 execution/isolated_acceptance.py：正在运行节点使用 coding_executions 逐命令占用执行功能/独立回归/PoC，预期值与用例契约仅冻结在宿主 coding_intents；不放宽 verification_executions 的 completed-only 条件，不改变普通 pytest/demo。
- 编码请求增加 read_only_input。节点验收使用只读代码挂载，每个用例独立容器命令；宿主 judge_actual 判定真实 JSON，而非候选自报通过字段。demo 由可信模板生成，只携带目标/输入，实际执行生成脚本源码，预期断言放在宿主；脚本独占保存并校验不被替换。
- 输出保持已有 acceptance:<id>: / poc: 测试名称和 JSON-safe TestResult 投影，但本适配器尚未直接装配 NodeExecutor，不能宣称完整任务已经可运行。缺独立回归配置等待，输入发生变化时阻断。
- 专项 **38 passed**，Ruff/diff 通过；覆盖预期值不进命令/demo、只读请求、真实 demo 源码执行命令、缺回归不执行、自报 passed 无效，以及既有容器边界/后端/完成门禁。测试模拟编码后端，未运行真实编码 Docker或全量。
- 待处理：逐命令审批应在副作用占用前完成。当前底层编码后端对尚未获准的启动也会记录 settled 尝试，不能靠自动换编号继续；须补明确的授权预检与已知未执行恢复协议，再完成 Runtime 装配。发布门禁仍关闭。

## 编码输入与导入基线不可替换绑定（2026-10-13）

- 同一 MetadataStore 增加 coding_intents，只保存不可替换的宿主输入意图，与 coding_executions 副作用状态分工，不创建另一套生命周期权威。冻结编码 request/input manifest/slot 和导入 baseline/plan/target；冻结必须由 running/code 任务的 active 节点 owner 发起。
- 编码后端与产物导入在副作用 claim 前冻结意图。claim 原子要求同 id/node/fingerprint 的冻结意图存在，并拒绝同节点已有 active/uncertain 命令；即使其他编号事先已冻结，也不能在未知副作用后启动。未知结果之后不能再新增编号意图。
- 关闭重开数据库后原意图仍可核对，但 baseline/命令参数变化不能覆盖原记录。仍不自动复用可写输入发生变化的已完成命令结果，不释放节点或命令占用。
- 专项 **39 passed**，包括真实 SQLite 关闭重开、冻结内容不能替换、未冻结/摘要错误 claim 拒绝、事先冻结与新编号均不能绕过 uncertain，以及后端/导入/节点守卫回归；Ruff/diff 通过。未运行全量或真实编码 Docker。
- 尚未完成 Runtime 的具体 Docker 分派装配、sandbox/Git 接管和真实镜像批准；普通任务与成长发布门禁未变。

## 文件代理产物导入适配器（2026-10-13）

- 新增 execution/coding_import.py，依据只读差异计划逐文件写回显式 sandbox，禁止导出/目标重叠，重验无链接目标和 baseline hash；所有删除先经过 authorize_delete，未批准时不写文件也不 claim。文本按 UTF-8 处理，二进制或源 hash 不符在 claim 前拒绝。
- 复用 coding_executions 的 artifact_import phase，通过任务/节点/owner CAS 在首次文件副作用前占用。每步与结算前重新核对源和目标，部分失败/取消或结算不明时保留 uncertain/active 与已确认变更路径，不自动回滚或重试；该计划不是多文件原子事务，也不是文件系统竞态的 OS 级防御。
- FileBroker.write_text 新增可选 newline；默认行为不变，导入显式使用空 newline 保留 UTF-8 文本字节换行，避免 Windows 自动转换导致 hash 漂移。删除仍走 FileBroker.delete 的原有审批复验，绝不整目录覆盖。
- 专项 **18 passed**，含真实 FileBroker 字节保留、删除等待前不写入、部分写入后的 uncertain/不重试、目标漂移、结算失败与二进制拒绝，以及既有删除审批/编码占用。Ruff/diff 通过，没有全量或真实编码 Docker验证。
- 适配器尚未接入完整 Runtime 编码实验，源导出 baseline 与 command_slot 仍需宿主调度器持久绑定；全部 sandbox/Git 执行路径和独立编码镜像也未完成。普通任务和发布门禁不变。

## 恢复占用前装配预检与只读产物计划（2026-10-13）

- replay_resume 在审批、Docker 预检与 claim_replay_resume 前确认 Runtime 已装配可调用的编码分派器；镜像预检后再确认一次。缺后端不审批、不启动代码、不写恢复占用，不再等到节点已占用才报缺失。可调用检查仅是装配前置条件，不证明镜像或完整隔离就绪。
- 恢复单元测试显式模拟编码分派器（原有测试已模拟编码图）；新增真实 SQLite 用例证明缺分派器时恢复表和节点表均无占用。测试桩不代表生产已装配。
- 新增 execution/coding_artifacts.py，只读生成新增/修改/删除差异。拒绝链接、私有/运行时资源、冻结路径、模糊/越界路径与非法 baseline，限制 32 MiB/10000 文件；删除只标记需要审批，不授予权限。尚未调用 FileBroker 写回，不做整目录覆盖。
- 专项 **25 passed**，Ruff/diff 通过；没有全量或真实编码 Docker验证。下一步补按文件代理执行差异与写入前重新核对，仍需完整 sandbox/Git 接管与编码镜像审批，发布门禁未变。

## 编码 cwd 映射与任务入口预检（2026-10-13）

- CodingCommand 增加 relative_cwd，宿主限定为 coding-input 内既存无链接目录，映射到 /workspace 子路径；拒绝绝对路径、父目录、Windows 盘符/反斜杠与不存在目录。编码 worker 使用 Docker 固定 cwd，不再强制覆盖为根目录；没有扩大挂载范围。
- Runtime._execute_task 在扫描/建图/节点执行前检测实例级 required 后端是否已装配。缺分派器将任务保持 WAITING_RESOURCE，终态任务不重开；此检查仅证明装配存在，镜像角色和占用仍由编码后端重验，不能作为完整就绪证明。
- 专项 **34 passed**，包括目录映射、越界拒绝、缺后端在工作区读取前等待、终态保护与原恢复回归；Ruff/diff 通过，没有全量或真实编码 Docker验证。
- 恢复入口仍需在 claim_replay_resume 前做完整后端预检；本轮任务入口预检不代替该恢复占用检查。编码导出/产物同步和 sandbox/Git 全路径接管也未完成，当前不能完整执行隔离编码实验。

## 隔离实验节点命令路由接线（2026-10-13）

- NodeExecutor 的验收 baseline 与 evaluation 命令由宿主按顺序生成 baseline-N/evaluation-N 编号，通过独立 coding_dispatch 分派，隔离模式不再调用普通 ProcessBroker 的候选命令路径，也不沿用 transient retry。编号并不是模型参数。
- Runtime 装配 NodeExecutor 时传入实例级的隔离开关/分派器，新建编码实验与恢复显式开启；普通任务默认关闭。开关不依赖候选/task metadata 自报。未装配分派器时抛出明确 no host fallback 阻断，由节点守卫保守处理，不能伪造成功。
- 本轮只接通 dispatch 接口，尚未完成从 sandbox 导出到 DockerCodingBackend 的完整装配，也未接管 run_code/Git 等全部路径。真实隔离编码实验目前不能完整运行；已执行到节点的未就绪异常可能保守留作不确定等待，不应自动释放占用。
- 专项 **35 passed**（节点路由、恢复、独立 replay 与既有执行门禁），Ruff/diff 通过，没有全量或真实编码 Docker验证。下一步完成编码导出/命令 cwd 映射和宿主授权适配器，并在创建节点前进行完整编码后端预检，避免已知未就绪进入节点占用。

## 逐编码命令持久占用（2026-10-13）

- SQLite MetadataStore 新增 coding_executions，与节点占用分工：节点守卫管理节点生命周期，命令表管理稳定 node/slot 的命令副作用。claim 原子要求所属任务 running/code、节点 running/active 且 owner 一致；不放宽 completed-only 测量 claim，也不允许通用节点保存覆盖命令占用。
- Docker 编码后端在启动前写命令占用，绑定任务/节点/owner/slot/请求与输入摘要。已有任何命令记录均阻断再次启动；可写输入已变化时不擅自复用完成结果。命令结果不明或取消时尽力 owner-CAS settle uncertain；失败则 active 保留，落库不明不返回成功。
- 专项 **34 passed**，包含真实 SQLite 双连接单一 claim、关闭重开后 uncertain 不重试、错误 owner/task/mode/取消任务拒绝、已有记录在进程 I/O 前阻断、claim 失败不启动代码、settlement 失败不成功返回。Ruff/diff 通过，未进行全量或真实编码 Docker验证。
- Runtime/nodes/sandbox 尚未接线。稳定 command_slot 必须由宿主调度器产生，不能让模型更换 slot 绕过占用；当前默认 0 仅适用于单命令后端实例。普通任务与成长发布门禁不变。

## 节点占用核验的编码后端（2026-10-13）

- 新增 execution/coding_backend.py，要求在实时 NodeExecutionGuard 回调中传入 owner，不允许通过数据库反查 owner 后重新装配。执行前及镜像检查后核对持久 node/task/RUNNING/active/owner；编码镜像角色不正确或占用不符不启动代码。
- 通过现有授权进程入口运行固定容器命令，60 秒超时、64 KiB 捕获预算，没有宿主回退。单后端实例单次尝试；启动前设置本地锁闩，未知结果/异常交给上层持久节点守卫，不自动重试。本地锁闩不是跨进程命令日志。
- 专项 **26 passed**，覆盖实时占用重验、错误 owner 在镜像 I/O 前阻断、未知结果消耗尝试及既有节点守卫。Ruff/diff 通过，测试使用模拟授权进程；未启动真实编码容器或接入 Runtime。
- 尚需 Runtime/nodes/sandbox 接线、逐命令持久身份设计及独立编码镜像制作审批。当前后端不代表已完成全链路隔离，成长验证/发布仍阻断。

## 隔离编码统一路由契约（2026-10-13）

- 新增 execution/coding_router.py：后端只能由 Runtime 装配，不从候选/task metadata 自报选择；后端缺失返回明确不可执行结果，没有宿主进程回退。要求调用方携带所属节点标识；标识本身不是授权，持久节点占用仍必须由 NodeExecutionGuard 管理。
- 路由不重试、不吞并异常。未知后端结果/取消由所属节点守卫处理，不能伪造成确定失败退出码再执行。普通 ProcessBroker 未修改，尚未装配 Docker 编码后端或接入 Runtime/nodes/sandbox 的真实调用。
- 专项 **30 passed**，覆盖缺后端绝不启动宿主、精确单次分派、未知异常不重试、缺节点阻断及既有节点执行占用；Ruff/diff 通过，没有全量或真实编码 Docker测试。
- 下一步：实现读取并核验持久占用的 Docker 编码后端，接线节点评估与沙箱解释器；完整接线之前仍不能宣称编码已隔离或解除成长发布阻断。

## 隔离编码执行路径审计（2026-10-13）

- 当前工作明确属于 evolution 成长候选验证链路。审计发现 Runtime.run_task_process 并非唯一执行入口：节点评估直接调用 ProcessBroker，GitWorktreeSandbox.run_code 直接启动解释器，SandboxGitTool 也独立调用 Git；只改一个入口无法建立完整隔离。
- verification_executions 的 claim SQL 要求任务 completed，不能直接复用为编码中任务占用。后续必须结合既有节点占用权威设计，不放宽测量 claim 前置条件。
- 新增 Isolated-Coding-Boundary-Plan.md，记录模块职责、具体接管点、实施顺序与真实边界验收条件。本轮为只读代码审计和文档更新，没有修改运行行为、镜像或任务状态，也没有运行新测试。

## 编码容器边界第一阶段（2026-10-13）

- 按用户确认范围新增 execution/coding_container.py 与 coding_worker.py。宿主保留模型、审批、持久状态和预期输出；不可信编码命令以结构化 argv 在断网容器执行，只规划可写的 evidence/coding-input 子目录与只读固定 worker，拒绝父目录/原仓库/其他工作区/冻结路径及私有资源。
- 复用测量容器已有安全参数校验，不新增另一套宽松 Docker 启动器；编码角色 isolated-coding/schema1 与 independent-verification 分开检查，已有测量镜像不能直接充当正式编码镜像。镜像必须固定 ID/digest，规划器不会 pull、build 或批准。
- 本轮仅完成规划器和容器内命令入口，**尚未接入 Runtime/审批/执行占用**，不能据此宣称编码阶段已隔离。普通任务流程、实验执行逻辑和发布门禁未改变。
- 专项 **29 passed**，Ruff/diff 通过，没有运行真实编码容器、下载依赖或制作镜像。下一步需审计并接管隔离实验的 sandbox/tool/verification/Git 宿主执行路径，建立不可回退的调度及持久占用，再显式制作/批准独立编码镜像和进行实际边界测试。只有完整接线通过才考虑可信验证交接。

## 正式交接前统一观察门禁（2026-10-13）

- 新增内部只读 validation_readiness 投影，统一纳入成对绑定、实验条件、执行来源、pytest 观察、双侧功能验收及双侧独立回归。缺结果、单侧结果、False 或数值冒充布尔通过均明确阻断；观察通过不等于允许提交验证。
- comparison-report 输出 validation_readiness；validation_blockers 现在明确包含已知用例失败，不再只依赖总体 verdict。所有观察齐全通过仍保留 trusted_measurement_handoff_not_established，validation_authorized/committed 为 false，未改变 CandidateManager.record_validation/approve 或候选状态。
- 专项 **45 passed**，包括门禁矩阵、成对报告、冻结回归与既有成长验证/发布占用保护；Ruff/diff 通过。没有全量、Docker或生产模型复测。
- 实质剩余边界：测量容器隔离已建立，但编码实验本身仍可执行宿主进程，数据库和冻结 oracle 位于同一 OS 账户可写位置。只读重算与 hash 不能证明这些记录未被同账户代码同时改写。建立正式可信交接需要进一步隔离编码执行与宿主证据/预期输出；这超出已批准的“仅独立测量容器化”范围，实施前应征求用户确认，不能自行删掉信任阻断。

## 显式冻结独立回归 JSON 验收（2026-10-13）

- replay-compare 增加 --regression-spec，首次读取操作员明确指定的本地 JSON（64 KiB 上限），归一化后分别在两侧隔离目录独占冻结，编码前持久化 regression_fingerprint。两侧任务输入摘要、条件报告与成对回归核对均绑定此摘要；不从候选或功能验收自动生成回归配置。
- 复用独立函数执行适配器，purpose=regression 使用不同稳定 ID 与 regression-input 导出；结果单独保存在 regression_records。缺 spec 保持测量等待，pytest regression_commands 仍仅辅助观察。冻结文件变化/缺失在恢复编码前阻断，未知执行不重跑、不替换冻结文件。
- 比较报告增加 regression_execution_provenance，从持久原始实际输出重新判定，功能记录不能替代回归记录，双方冻结摘要不一致阻断。记录一致性不是操作系统/数据库防篡改证明，尚未连接 record_validation 或批准/发布。
- 联合专项 **51 passed, 1 skipped**；固定本地镜像的真实 Docker/导出专项 **9 passed**（其中 1 项实际启动功能、回归两个独立容器），同名用例 ID 不同且各自缓存不重跑；测试专用已停止容器已精确清理，证据保留。Ruff/diff 通过，没有全量或生产模型复测。

## 函数缓存与当前实现绑定（2026-10-13）

- 专用导出复用前，按相同排除与资源分类规则只读重算当前仓库清单，必须与现有导出完全一致。实现修改、新增或删除文件都阻断旧测量复用，不覆盖旧导出、不释放执行占用，也不自动重跑。
- verification_export.export_input 增加 manifest_only 模式，复用既有链接/私有资源/体积检查而不创建或写入目标目录，避免增加第二套导出规则。
- 专项 **37 passed, 1 skipped**；Ruff/diff 通过。包括新增/删除/修改待测实现均在执行前阻断，旧导出字节保持不变。本轮未进行全量或真实 Docker复测。
- 正式验证尚需明确可信回归用例来源。现有 regression_commands 仍是同进程 pytest 观察，不能据此调用 trusted record_validation；新功能验收也不能冒充回归覆盖。建议采用独立、显式指定且实验前冻结的回归 JSON 输入输出 spec，缺配置保持等待，不由候选生成或静默沿用验收用例。

## 函数执行只读核对与流式输出预算（2026-10-13）

- comparison-report 新增 function_execution_provenance：只读查询原隔离数据库，绑定逐用例稳定 ID、schema3 协议、completed 状态、contract 摘要/用例/镜像、实际命令、容器和输入未变；用保存的原始 stdout 重新执行宿主 JSON 比较。导出与持久摘要同时伪造仍不能替代原始实际值。缺函数记录或字段时阻断，不补造历史。
- Runtime.run_task_process/ProcessBroker 增加可选 max_output_bytes，仅函数验收主动要求 stdout/stderr 合计 64 KiB，既有调用默认行为不变。proc.capture_bounded 分块读取并共用字节预算，超限不返回截断的成功结果。参数非法在进程创建前拒绝。
- 超限/超时终止并回收本地进程；修正 Windows 大输出时取消读取后管道仍阻塞的回收问题，终止后分块丢弃剩余管道内容。Docker 客户端终止不等于 daemon 容器已停止，函数占用仍 uncertain，无自动重跑。解析限额和流式预算均存在，但 asyncio 管道自身缓冲及读取块还会带来有限额外内存。
- 专项 **36 passed, 1 skipped**（真实 Docker 显式启用测试未运行），包括真实本地子进程输出溢出；既有 proc/沙箱生命周期回归 **7 passed**。Ruff/diff 通过；本轮没有全量或真实 Docker复测。可信回归和正式验证交接尚未完成，trusted_execution/validation_committed 仍 false，不批准或发布。

## 专用函数导出与隔离实验接线（2026-10-13）

- 新增 evolution/replay_functions.py：验收 spec 必须与冻结测试文件的字节 hash 对应；在独立 function-input 导出中剔除声明的 oracle 路径，以及与生成验收测试/最小 demo 内容一致的文件。原仓库不改写，已有导出必须有完整清单且内容未变；部分/篡改导出阻断，不覆盖修补。
- 新建实验与同任务恢复均在编码完成、oracle 完整且 pytest 未等待后调用函数测量；记录到独立 function_records，保留既有 records 语义。没有 spec 不虚构验收；函数等待会反映到 measurement_status。仍通过授权进程入口，不代用户批准 Docker 命令。
- 联合定向 **47 passed**；固定本地镜像的真实 Docker 及相关导出专项 **6 passed**（其中 1 项实际运行容器）。实际验证非 root、无 Docker socket、冻结测试/demo 不挂载、宿主比较及持久缓存不重复启动。测试专用已停止容器经精确名称与 role 标签核对后删除，证据保留；未下载镜像、操作已有服务或运行生产模型。
- Ruff/diff 通过；未重跑全量。尚缺函数记录的只读执行来源投影、进程流式输出限额与可信回归验收，不调用成长批准/发布。已知 oracle 文件剔除不是任意变形副本/代码内嵌预期值检测，也不是通用秘密扫描。

## 容器外函数输入输出判定适配器（2026-10-13）

- 新增 execution/function_verification.py 与独立 function_worker.py。每用例生成独立 Docker 命令，复用已有固定镜像/断网/非 root/只读挂载/资源约束及授权进程入口；容器只收到 target/args/kwargs，不收到 expected_output。必须显式声明冻结 oracle_paths，拒绝输入中仍包含这些路径。
- 宿主侧严格解析单个 JSON 实际值并与预期比较，不接受候选自报 passed；拒绝非有限数、额外日志、多 JSON、非零退出、输出被脱敏及超过 64 KiB 的结果。64 KiB 是解析接收限额，尚不是底层进程流式捕获内存上限。JSON 数字按规范化表示精确比较（1 与 1.0 不等）。
- 复用 verification_executions 持久占用，协议 function_json_v1/schema3；请求绑定用例、输入清单与 worker hash。完整返回可缓存，未知结果占用保留且不自动重跑；新请求与原用例不一致阻断。
- 专项 **32 passed**，Ruff/diff 通过。测试使用模拟授权进程与真实 SQLite，没有进行真实 Docker或全量复测。适配器尚未接入隔离实验/CLI，没有修改现有 pytest 流程或成长状态。
- 下一步：专用剔除 oracle 的导出与隔离入口接线、真实容器验收、执行证据投影及输出流式限制；候选代码仍可伪造它自己的实际返回值，但不能访问宿主侧判定逻辑。完整链路与可信回归尚缺，trusted_execution/validation_committed 保持 false，不批准或发布。

## 独立测量执行来源核对（2026-10-13）

- 新执行占用记录保存 schema_version=2、slot、nonce 和 observer_hash。comparison-report 经存储层只读查询原隔离数据库，将每条观察绑定到稳定 execution_id、completed 执行、原始进程输出、实际命令、镜像、容器及输入未变记录；根据原始 stdout 重新计算 pytest 摘要，不仅比较报告中的通过布尔值。
- 新增 execution_provenance_verified/blockers，独立于观察结果与实验条件。缺记录、active/uncertain、旧字段不齐及导出报告篡改继续阻断验证，不迁移或补造历史证据，也不重跑执行。
- 专项 **43 passed**；Ruff 与 diff 检查通过。本轮未进行全量、真实 Docker或生产模型复测。
- 来源一致性不等于恶意代码防伪：候选测试与观察器仍同进程，本地数据库也不是密码学信任根。trusted_execution/validation_committed 仍 false，未调用成长验证、批准或发布入口。后续需解决独立可信观察边界，不能直接将本检查转换成发布许可。

## 成对实验条件持久绑定（2026-10-13）

- comparison-report 只读查询两侧隔离 metadata.db，重验 v4 计划、completed/code、工作区路径、持久 actor/source 与 creator、source/revision、测试/回归命令、冻结 oracle、验收配置和实际记录镜像。再比较配置/原创建者/原项目/oracle_paths、实现与工具摘要；指令、预算、委派范围及关键任务输入仅以摘要参与比较，不输出原文。
- Runtime snapshot 升级 v2，保存逐技能 fingerprint/version/instructions_hash（无指令原文）；只允许恰好一个技能增加或替换，且内容 hash/version 对应指定候选。额外技能变化、模型/策略变化、缺计划/快照或外部实现不明阻止实验条件确认。旧快照不自动补造，恢复 claim 也要求 v2。
- report 的 experiment_conditions_verified 仅表示**记录的条件和绑定检查通过**，且要求测量摘要/覆盖门禁也通过；不是当前 OS 身份授权、供应链证明或恶意代码防伪证明。没有修改候选状态，trusted_execution/validation_committed 仍 false；可信测量交接仍是唯一必须解决的后续门禁。
- 联合专项 **59 passed**，Ruff/diff 通过，覆盖相同条件、仅候选差异、重算计划摘要后模型/实现/额外技能变化仍拒绝、任务输入不同、缺计划不补造以及真实 SQLite 前后字节不变。没有全量/真实 Docker/生产模型复测。

## 成对观察只读报告（2026-10-13）

- 新增 `sprout evolution comparison-report <growth-comparison-*.json>`，不构造 Runtime、不执行模型/进程、不改候选状态。重读原 measurement_path（限定 evidence_dir/independent-measurement.json，拒绝直接链接、非 JSON 对象及超过 8 MiB 文件），重算摘要、任务/source/revision/candidate 绑定、完整命令覆盖、runner/测试集合/冻结验收覆盖；不信任清单里的 comparable/validation 标记。
- 输出 incomparable / observed_all_passed / observed_failures，分别展示基线和候选的观察通过情况。完成任务证据缺失、摘要变化及覆盖不完整不能报告可比较。旧清单缺测量仍等待，不自动刷新或替换原证据。
- 即使 observed_all_passed，trusted_execution=false / validation_committed=false；experiment_conditions_verified=false。模型/配置/registry 的实验绑定和可信测量交接尚未接此报告，不证明性能改善或允许发布；不调用 CandidateManager.record_validation。当前 pytest observer 与测试同进程的可信边界仍未解决。
- 复用 replay_pair 的绑定谓词以避免两套判断漂移；联合专项 **44 passed**，Ruff/diff 通过，覆盖摘要变化/伪造成功标记拒绝、失败不算成功、路径绑定、只读不修改证据及 CLI 报告。未运行全量/真实 Docker/生产模型。

## 真实 Docker 恢复与未知结果验收（2026-10-12）

- 确认现有 Linux Docker 29.8.0 及已建固定镜像 `sha256:0bf005068f7c22adbb6f4dcc436eef9b89d8708a5cfd993fa2fbac7480df9152` 的专用标签；没有下载/构建镜像，没有修改或停止现有服务。
- 新增显式 opt-in 恢复集成测试：真实隔离 Runtime/SQLite/ApprovalManager/ProcessBroker + 固定编码完成 fixture + 真实 Docker。先请求审批，无 grant 不执行；精确审批后运行两个独立 pytest 容器，持久化 observed 报告，重复恢复不增加进程/编码执行。
- 客户端 1 秒超时用例实际确认容器仍在运行；验收执行记录保留 uncertain，测量 waiting_resource，第二次恢复不执行任何新进程。等待本次样例容器自然完成后核验标签/停止状态并删除，仅测试清理，不为生产增加自动停止或核销逻辑。
- 以本机 `tcp://127.0.0.1:1` 拒绝端点模拟 Docker 连接不可用，授权后的真实 Docker CLI 失败保持预检等待且没有恢复 owner/模型执行；还原 fixture 连接后同一任务恢复成功。**没有停止/重启 Docker daemon，不证明真实 daemon 故障重启恢复。**
- 联合真实 Docker **5 passed**：新增 3 个恢复场景及既有 2 个隔离边界/缓存场景；普通专项 **38 passed、3 skipped**（新 Docker 场景默认 opt-in 跳过），Ruff/diff 通过。本轮未重跑全量。证据保留 `.workbuddy/test-runs/replay-resume-real-docker-final`，仅本轮明确拥有且已自然停止的测试容器被删除，镜像保留。
- 生产代码未改（本轮仅集成测试、fixture 参数和文档）；编码执行是受控替身，审批策略是测试专用窄策略，独立观察仍 trusted=false/validation_committed=false。不代表生产模型任务已端到端验收，亦不自动更新成对清单或发布成长候选。

## 本机 CLI 恢复执行闭环（2026-10-12）

- 新增 `replay-resume <隔离任务ID> --database <evidence_dir>/metadata.db --config <原配置>`，串起静态身份/计划/配置/仓库门禁、同一隔离 Runtime 组装、候选原权威版本/hash/静态门禁、工具/技能快照、当前镜像策略/审批、占用前后证据复核、原子 owner、原任务 execute、Docker 独立测量、报告与 owner 结算。只恢复 image_preflight waiting_resource；任何已有节点/验收/恢复占用都阻止从头恢复。
- 镜像审批未满足时返回 approval_id，不占用、不执行模型；显式 `--approve <精确ID>` 仅决定同一隔离任务/原创建者/CLI 的 pending process_run 请求，通过现有 ApprovalManager，避免通用自动 resume。DENY 仍优先，不新增 Docker 自动许可/下载构建/force。模型执行仍沿用既有审批机制，可能再次等待。
- Runtime.execute/resume_task 增加专用等待门禁，即使传 stale CREATED 对象也查持久状态；合法 execute 必须匹配 active owner/原请求人/计划指纹。节点执行规则未放宽。取消、异常、证据变化或结算不明保留 active/uncertain，不自动释放/重跑。
- 修复 SQLite Task.actor 落库遗漏 authenticated/source；新记录完整恢复，旧记录默认未认证，不自动升级。隔离组装收口 context/usage DB、memory.home，并断开共享 Redis/Milvus/Neo4j；所有本地持久 lane 在实验目录，knowledge/cache/vectors 为内存、graph 禁用。
- 测试使用真实隔离 SQLite/Runtime/ApprovalManager/ProcessBroker 和受控模型/进程替身：初始等待无 owner、精确审批后执行、同一任务一次执行、并发单赢家、取消/异常/结算失败阻止重试、普通入口防绕过、CLI 调用及真实 Runtime owner 门禁。**不证明生产模型/真实 Docker 故障恢复或外部插件兼容**，不自动修改成对清单、不 validated/published。
- 最终全量 **1526 passed、19 skipped、2 条依赖弃用警告**（继续排除两个原有缺失示例模块测试）；Ruff、diff 检查及实际 CLI --help 通过。此前联合恢复/安全专项 **52 passed**，最终全量包含后补存储隔离测试。实际用法见 Replay-Recovery-Guide.md。

## 本机 CLI 创建者身份门禁（2026-10-12）

- 按已确认范围，新实验由 CLI 入口取得当前操作系统身份；Windows 使用 OS API 确定系统目录，固定 whoami.exe /user 查询 SID，不使用 PATH/USERNAME/USER/LOGNAME 或 --user 自报身份；POSIX 使用本机有效 UID 与主机标识。查询失败保守拒绝。
- ReplayRunner 要求入口显式传可信 CLI Principal，并与当前 OS 账户复核；不把库的任意调用自动升级为本机 CLI。全新实验 task.actor 和 v4 计划 creator 绑定该身份；Web/MCP 没有新增恢复入口。
- inspect_recovery 校验可信创建者存在、当前身份 authenticated/source=cli 且同一 user_id；原子 claim 再比较 requested_by 与计划创建者，旧计划及不同用户不能申请。身份匹配不等于审批授权，仍须当前策略和审批核验；存储方法本身不是身份认证入口。
- 旧实验缺可信创建者不能直接恢复，不能补造旧身份。当前仍无恢复执行命令，只读 CLI 缺当前运行时快照仍保持阻断；本轮实现的是创建/核验/占用的身份链路。
- 专项覆盖环境用户名无法覆盖 OS 身份、非 CLI/未认证/不同用户拒绝、原创建者占用比较及真实隔离任务落库。Windows SID 路径已在当前 Windows 测试运行；POSIX 分支未在本机验证。未全量/真实 Docker 复测。

## 任务进程授权上下文修复（2026-10-12）

- 复查恢复执行链路发现 run_task_process 未传持久任务 source/requested_by，ProcessBroker 默认 interactive/system；cron/automation 因此可能走交互审批而非无人值守拒绝。现改为传 task.source、task.actor.user_id 和 task.actor；PolicyEngine 的 ActionRequest 也使用该 Principal，不再丢失任务身份。
- 未增加自动授权、全局授权或调用方自声明权限；进程仍经过原 PolicyEngine/ApprovalManager，未知结果/独立验收占用规则不变。此修复影响普通任务进程与 Docker 检查入口，不只成长实验。
- 身份来自持久任务上下文，不等于恢复时当前操作者身份认证；实际恢复入口仍未开放，不能用历史 Principal 或占用成功替代当前授权。
- 联合回归 **66 passed**，Ruff/diff 通过：来源与身份传递、缺任务不执行、真实 ProcessBroker 下 cron/automation 默认无人值守拒绝（不建 pending、不启动子进程）、审批范围及安全边界。未全量/真实 Docker 复测。

## 工具/技能快照与实现变化门禁（2026-10-12）

- 新实验计划升级 v3，包含 runtime_snapshot：已加载工具名称/类身份/完整 ToolSpec、技能完整字段（含指令/信任/启用/依赖）的摘要，以及本地 Sprout 非 tests Python 源码摘要。不持久化工具规格或技能指令原文。候选已注册到隔离 registry 后才取快照。
- 快照核对接入 inspect_recovery：缺快照、当前运行时未提供、技能/工具/本地源码变化和外部工具实现未证明均阻止恢复；原子 claim 仅接受 v3 且两个契约版本存在。旧 v1/v2 保留可读，不自动补快照。
- 只读 CLI 不构造 Runtime，不加载/启动外部服务；因此即使文件和配置检查通过，也会返回 current_runtime_snapshot_required，不能据此执行。实际恢复入口仍待接线，并需隔离组装、当前身份授权、占用前后复核和异常结算。
- 该快照不是完整供应链证明：本地源码摘要不证明已加载字节码/动态 monkeypatch、第三方依赖、外部 CLI/MCP 服务或闭包状态一致；后续执行入口必须继续保守约束。源码升级即阻止同一实验恢复，改版本应显式新建实验，不能绕过未知副作用占用。
- 专项覆盖稳定快照、不泄露指令、技能内容/信任变化、工具规格风险变化、缺快照及真实隔离计划持久化。本轮未全量/真实 Docker 复测。

## 配置冻结与旧实验保守门禁（2026-10-12）

- 按已确认规则，新实验 replay_plan 升级 v2，加入 configuration 契约：模型/provider/回退/温度、运行与上下文参数、安全策略、MCP/技能配置分别取摘要。持久化仅摘要，不保存配置原文、模型 URL、凭据或授权；API key/凭据变量标识不参与摘要，允许正常轮换。隔离审计路径及 memory.home 不作为配置条件。
- replay-recovery-check 增加 --config 并加载当前设置进行比较；无当前配置、旧计划缺契约、摘要不一致均阻止恢复。原子恢复 claim 要求 v2/configuration v1，不能先对旧计划补造摘要再恢复。
- MCP env 的任意值可能同时是凭据和行为参数，未提供分类适配前配置该 env 的实验阻止恢复，不把忽略值错误解释成安全一致。配置摘要不是运行时工具实现/技能内容/环境/身份的完整快照；这些仍需实际恢复入口补齐核验。
- 配置门禁不授予权限，当前身份/审批重新核验尚未接执行入口。旧证据可读，但旧实验不能直接恢复；新建实验也不得绕过未知副作用占用。实际恢复命令本轮仍未开放。
- 专项覆盖密钥轮换、模型/温度/权限/执行参数/技能变化、缺契约、MCP env 保守阻断、旧计划拒绝占用及真实隔离计划落库。未全量/真实 Docker 复测。

## 原子恢复占用基础（2026-10-12）

- MetadataStore 新增 get/claim/finish_replay_resume 契约；现有 metadata.db 增加 replay_resumes，以隔离任务 ID 为唯一键。单条 INSERT 条件检查 waiting_resource、显式 code、计划版本/指纹一致、无节点及无验收占用；仅一个请求成功。
- owner CAS 只允许 active 结算为 completed/uncertain；未知结果、已完成结果均不删除、不超时释放、不重新申请。通用 save_task 无法覆盖独立占用记录。此处 completed 是恢复操作结算，不代表任务完成或验证通过。
- 只读恢复检查新增 existing_resume_ownership 阻断；缺少新表的旧数据库保守报告证据不可用，不由检查命令迁移。
- 当前仅存储基础和检查接线，尚无实际恢复执行入口；不绕过现有审批。执行入口还需当前身份/策略、候选快照注册、镜像授权，以及占用前后证据复核和异常结算。单条占用不能单独解决外部文件 TOCTOU，也不约束尚未接入此门禁的其他执行入口。
- 并发双连接、重启保留、错 owner、未知结果阻止重试、类型/状态/摘要变化、已有节点、通用任务更新不释放占用均有专项覆盖。本轮未全量/真实 Docker 复测。

## 恢复前只读门禁（2026-10-12）

- 新增 `sprout evolution replay-recovery-check <隔离任务ID> --database <隔离目录>/metadata.db`。storage 层使用只读 SQLite 事务取得任务/工作区/节点数/验收占用数一致快照，evolution 层检查计划、模式/源任务绑定、目录位置、Git HEAD/未提交变化、冻结 oracle。
- 只支持 waiting_resource 且没有任何节点/验收占用的 code 预检范围；completed、既有节点和任何验收占用（包括 completed）都阻止从头恢复。旧计划、旧表结构、缺证据和 Git 查询失败保守拒绝。
- 不构造 Runtime、不写数据库、不检查/启动 Docker、不决策审批；输出 preflight_checks_passed 仅表示本次静态检查通过，resume_authorized 永远 false。TOCTOU、当前身份/策略、镜像审批、候选注册、原子恢复占用尚需执行入口处理。
- 已覆盖真实预检等待任务的只读检查、改代码后拒绝及已有节点/占用/完成任务拒绝。未知副作用仍不释放、不重跑；本轮没有全量/真实 Docker 复测。

## 独立实验持久运行计划（2026-10-12）

- ReplayRunner 将 schema_version=1 的 replay_plan 随全新任务写入 metadata.db，发生在镜像预检/模型执行之前；预检等待后仍可查到原计划。绑定 source_task_id、固定 Git commit、显式模式、原始/隔离路径、测试/回归命令、冻结 oracle、固定镜像及候选快照。
- 不序列化模型配置、凭据、任务指令或审批授权。fingerprint 用于完整性核对，不是签名、授权或可信验证证明；旧任务缺计划不能自动补造后恢复。
- verify_plan 拒绝摘要变化、不支持版本、缺失/额外字段及非规范计划。当前尚未新增执行恢复命令：仍需当前策略与身份校验、工作区/冻结测试核对、节点/副作用占用门禁及恢复并发控制，不能单凭摘要恢复任务。
- 专项测试覆盖计划往返、版本/命令/镜像/版本号变化、缺字段及预检等待后计划实际落库；未重跑全量或真实 Docker。

## 独立验收只读核对入口（2026-10-12）

- 新增 `sprout evolution verification-report --database <隔离运行目录>/metadata.db`；可用 `--execution-id` 精确筛选。数据库路径就是 ReplayResult 的 evidence_dir 下 metadata.db，不使用原项目数据库替代。
- storage/local/sqlite 层以 SQLite mode=ro/query_only 打开现有数据库，不创建目录/数据库、不迁移、不构造 Runtime、不查询 Docker。CLI 只展示状态、任务 ID、请求指纹、输入文件数、容器名、等待原因和输入一致性；不输出 owner、原始命令、文件名及进程日志。
- active/uncertain 仅提示人工核对，不释放占用、不允许重试；completed 只代表已收到观察，不证明测试通过或可信验证。空列表仅表示没有验收占用，不能推断任务可安全恢复；镜像预检等待发生在占用前，不在本查询覆盖范围。
- 只读查询及持久占用联合回归 **16 passed**，Ruff/diff 通过；缺失路径不创建、旧数据库不迁移、损坏 JSON 拒绝展示，CLI 无 Runtime 依赖。本轮未重跑全量/真实 Docker 测试。
- 审批恢复暂未接线：现有 resume 是会话恢复，独立实验尚缺可校验的持久运行计划；不能用新建实验或重跑原任务伪装恢复。未知副作用依旧没有强制重跑/自动核销入口。

## Docker 导出、持久占用与报告接线（2026-10-12）

- ReplayRunner 编码入口验证固定镜像并限制 pytest 命令；创建新任务后通过原授权 Broker 检查 Linux/专用镜像标签，未就绪则持久化新任务等待，不执行模型、不回退宿主独立复测。原任务和审批记录不复用。普通 Agent 执行/评估仍沿用现有架构，Docker 仅用于完成后的独立测量。
- 新增 verification_export：导出原仓库外的独立副本，排除 Git、运行时、缓存目录，拒绝链接及默认/自定义 SECRET/SENSITIVE/DATA/EXTERNAL 分类资源；限 32 MiB/10000 文件。导出摘要封存后才可使用，部分导出或变化不能复用；冻结 oracle 在导出中必须一致。这不是源码内嵌秘密检测，也暂不支持业务数据/数据库适配。
- MetadataStore 增加独立验收契约，在同一个 metadata.db 的 verification_executions 表保存 owner、active/completed/uncertain、请求指纹、输入摘要与启动命令/容器名。占用仅允许已 completed 且明确 code 的任务，不放宽原节点终态门禁。单条 INSERT/owner CAS 结算，无过期、抢占或强制重跑。
- DockerVerificationRun 启动前占用，nonce/观察脚本摘要/明确退出码匹配才交接结构化观察；已确认完成返回缓存。超时、取消、缺报告、身份不匹配或落库失败保留 active/uncertain，停止后续测量，不因等待而重新启动。输入变化/不同镜像阻止完整成对比较。execution 层通过注入解析器交接，不依赖 evolution 实现。
- coding task completed 与成长测量分离：ReplayResult/证据新增 measurement_status，独立测量等待不伪造任务完成或修改原已完成任务。仍 trusted_execution=false / validation_committed=false，不调用 record_validation，不自动发布。
- 受控真实 Docker 测试 **2 passed**：边界探测及导出→占用→容器→报告→缓存服务链路（重复测量不启动新容器）；测试容器确认标签和停止后删除，日志/记录保留于 .workbuddy/test-runs/verification-execution-real-final，现有服务不修改。此测试使用显式授权的 fixture 进程入口，不证明用户真实任务/CLI 审批恢复已完成。
- 全量回归 **1487 passed、19 skipped、2 条依赖弃用警告**，继续排除两个原有缺失示例模块测试；随后完成类型/镜像/分类补强并以联合专项 **90 passed** 覆盖，Ruff/diff 通过。
- **待办与风险**：生产验收容器暂时保留，不自动停止/删除；Docker 客户端超时后容器可能仍运行，需按已存容器名人工核对。尚无 CLI 审批恢复/未知结果证据核销入口；占用幂等范围为同一隔离任务+命令槽，不是跨不同新实验的全局 exactly-once。报告与测试同进程，可信报告交接、自动晋级及生产故障专项仍未完成。

## Docker 专用镜像与真实边界探测（2026-10-12）

- 用户批准下载/构建专用镜像。拉取 Python 3.13 slim 后锁定基础摘要 python@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f；verification/Dockerfile 与 requirements.txt 只包含 uv.lock 固定版本/哈希的 pytest 9.1.1、iniconfig、packaging、pluggy、pygments。pip 使用 require-hashes / only-binary，pip check 成功。不包含 Sprout 或业务依赖，不安装异步插件。
- 本机镜像标签 sprout-verification:pytest-9.1.1，实际不可变 ID sha256:0bf005068f7c22adbb6f4dcc436eef9b89d8708a5cfd993fa2fbac7480df9152；调用需显式传本机 inspect 获得的 ID，不以标签为验证身份。
- pytest 观察脚本增加 stdout 随机标识报告传输模式，不挂可写宿主证据目录。仍标记 trusted_execution=false；不会因镜像成功构建而提交成长验证。
- 真实 Docker 受控测试 **1 passed**，使用后端生成参数与生成的小样例验证非 root、输入/观察脚本不可写、无外网连接、可写 /tmp、原样例不变和实际结构化用例收集。测试容器核对标签且确认停止后删除，日志/inspect 保留在 .workbuddy/test-runs/verification-image-real-1；不删除未知/运行容器。没有改动现有数据库或 Temporal 容器，专用镜像保留。
- 操作说明位于 src/Sprout/config/docker_templates/verification/README.md。生产重放仍等待：脱敏导出、持久执行占用/未知结果恢复、容器生命周期及报告交接尚未实现；普通 Agent 执行不迁移，禁宿主回退规则保持。
- 最终联合专项 **59 passed、1 skipped**（容器测试默认不指定镜像时跳过，已单独显式运行并通过）；新增依赖锁定检查确保 requirements 与 uv.lock wheel 哈希一致。Ruff/diff 检查通过，本轮未重跑全量。

## Docker 独立验收第一阶段：契约与禁回退（2026-10-12）

- 用户确认仅独立验收引入 Docker，不迁移普通 Agent 执行链路。增加 execution.VerificationRequest / VerificationBackend / AuthorizedProcessRunner 契约与 DockerVerificationBackend，镜像查询经过原 Runtime.run_task_process 授权与审计，不新增默认自动放行。
- 仅接受固定本地 sha256 镜像 ID 或 repository digest；启动计划包含 pull=never、network=none、只读根/输入/观察脚本、非 root、cap-drop、no-new-privileges、CPU/内存/PID/日志限制及 tmpfs。禁用镜像 healthcheck，覆盖 entrypoint，不传宿主密钥环境，不挂 Docker socket、原仓库或可写宿主目录。参数依据 https://docs.docker.com/reference/cli/docker/container/run/ 核对。
- 输入必须是原仓库外的独立导出目录；拒绝链接、越界、设备/网络路径、挂载参数分隔符、Git/运行时目录及默认分类器识别的 SECRET/SENSITIVE/DATA 文件。当前是路径/分类门禁，不能检测任意源码内嵌密钥；尚未完成导出生产者和定制分类策略接线，不能称数据脱敏已全面完成。
- replay-compare 增加 --verification-image。编码重放缺镜像或后端尚未完成委任时返回等待，在克隆/模型执行前停止，绝不调用宿主测量作为回退。当前有镜像也保持等待；这是后端分阶段施工门禁，旧宿主观察助手仅保留诊断/测试用途，不作为独立编码验收回退。
- 只读检查本机已有镜像，没有专用 Python/pytest 验收镜像；未拉取、构建、启动或删除容器，也未修改数据库/Temporal 服务。真实 Docker 验收与故障测试、生命周期占用、导出生产者和容器报告交接尚未实现，可信验证报告仍不提交。
- 本轮联合专项 **58 passed**，Ruff、CLI 帮助和 diff 检查通过；未重跑全量。下一步专用镜像下载/构建需要用户确认，不使用数据库/Temporal 镜像临时替代。

## 成对用例、运行器配置与验收覆盖（2026-10-12）

- 比较清单增加 structured_comparison，逐命令核对实际收集用例集合、Python/pytest/观察脚本身份、相对配置文件摘要和插件名单；目录位置归一化，pytest 的随机数字插件注册名改用插件类型标识，不忽略实际配置差异。未冻结配置、减少用例、缺结构化记录或身份变化均记录阻塞原因。
- 原 source task acceptance_spec 写入独立证据，不由候选重新声明覆盖。复用 AcceptanceSpec 和 render_acceptance_tests，以完整渲染文件摘要绑定冻结 oracle；只有该文件中对应 case 的 pytest call 阶段实际执行过才计入覆盖，同名其他文件、只收集未执行和跳过都不能替代。
- 仅覆盖现有 Python 输入/输出验收适配器；缺原 acceptance_spec、固定 revision 不含匹配验收文件、非 pytest 验收或未冻结配置仍阻止形成完整结构化比较。不以用例数量相同替代用例 ID 和原断言绑定。
- 专项 **42 passed**，含两份独立目录的真实 pytest 对照；Ruff 通过。真实测试确认父目录配置发现与随机插件注册名会导致差异，固定配置并稳定标识后对照通过。
- 本轮全量回归 **1461 passed、17 skipped、2 条依赖弃用警告**，继续显式排除 test_bubble_sort.py / test_hello_world.py 两个缺少示例模块的既有测试；diff 检查通过。
- 此处“可比较”不是成功/改善评分，基线失败仍可观察。配置和报告在同进程可被恶意测试影响，trusted_execution=false，validation_committed=false；尚未接入 record_validation，候选不会自动晋级或发布。完整可信运行环境与生产需求覆盖仍待补齐。

## pytest 结构化观察（2026-10-12）

- 独立测量识别 pytest / python -m pytest，规范化为宿主 Python -I 加候选副本外的独立观察脚本，通过原 Runtime.run_task_process 授权执行。宿主解释器可能不同于命令中原选解释器；记录 requested command 与 executed_command，CLI 帮助说明这一范围。其他命令保留普通进程观察，不冒充 pytest 用例测量。
- 观察 pytest 实际 collection 与 setup/call/teardown，记录用例 ID、阶段结果/耗时、收集错误、xfail/跳过；增加 Python/pytest 版本、解释器路径、观察脚本 SHA256、配置文件路径/摘要与插件名单。每次报告具有独立随机标识与独占文件，缺报告、标识/脚本摘要不匹配、无测试、重复收集、跳过、失败、阶段不完整均不标记 observed_all_passed。
- 退出码 0 但缺结构化报告的观察降为 incomplete_pytest_observation，不再保留普通 passed。报告仍明确 trusted_execution=false，validation_committed=false，未连接 record_validation。
- 隔离重放、命令测量、oracle 与 pytest 联合专项 **31 passed**，其中包含受控真实 pytest 子进程收集/执行测试；Ruff 和 diff 检查通过。本轮未重跑全量，也未对用户实际任务消耗模型额度或运行真实业务测试。
- **边界与下一步**：插件/测试和观察器处于同一进程，记录不是抗恶意伪造证明；Python -I 不提供 OS 沙箱，也不能阻止测试产生外部副作用。需继续比较成对用例集合、运行器配置和需求覆盖；可信执行边界仍未建立，暂不称“可信成长验证闭环完成”。

## 成对观察完整性补强（2026-10-12）

- 发现原比较清单只要求有进程记录，未核对是否覆盖全部配置命令。本轮增加按顺序逐条核对测试/回归命令 argv、分组、授权状态、明确退出码，缺失、替换命令、待审批和未知结果不能标记 comparable_observations。
- 比较清单同时核对记录与 ReplayResult 的新任务 ID、源任务 ID、提交，以及候选快照 ID/内容哈希；无候选快照不生成可比较标志。“可比较”仍仅指完整观察，失败结果可比较但不代表通过。
- 隔离重放、测量及 oracle 专项 **20 passed**，静态检查通过，全量未重跑。测试使用受控 fixture，不证明真实业务任务已完成可信测量。
- 核对 intents.py：基准 7b377ac 已位于 src/Sprout/intents.py，当前 blob 与基准相同（49e2ff1cddd6e0ca2ad8597b6ec1ee1c1e0e45b1），未移动或改动其内容；test_intents.py 的现有差异为增加 .git 测试目录 fixture。
- 后续仍需可信测试收集与运行器身份、验收覆盖绑定。任意命令 exit 0、可写本地 JSON、仓库内测试文件哈希，都不足以独自证明可信成长验证；暂不开放自动晋级。

## 冻结测试路径与成对证据（2026-10-12）

- replay-compare 增加可重复 `--oracle <仓库内相对测试文件/配置文件/目录>`。编码源任务缺少 oracle_paths 保持 waiting_resource，不启动模型。路径必须存在且有文件，拒绝绝对路径、越界与符号链接；输出目录重叠检查提前到 CLI，等待结果也不在源仓库写比较清单。
- 固定提交副本在任务执行前生成 SHA256 文件清单；独立复测前、后比较清单。选定文件修改、删除、目录内新增文件等均使 oracle_unchanged=false，复测前已发生变化则不执行复测。命令本身的测试/回归配置也保留在证据中。
- 新增 growth-comparison-<id>.json，保存两个 ReplayResult、两个测量记录摘要以及 comparable_observations。只有新任务不同、源任务/提交/命令组/冻结文件清单一致、完整性检查通过且都有进程记录时才标为可比较；“可比较”不代表测试成功或候选改善。
- 本轮隔离测量与 oracle 联合专项 **16 passed**；Ruff、CLI 帮助和 diff 检查通过（全量未重跑）。
- **剩余边界**：用户须覆盖测试及相关收集配置，未指定的运行器/依赖/插件仍可能影响结果；目录内测试生成缓存也会被保守视为变化。文件哈希不能证明运行器可信或覆盖足够，没有外部防篡改存储与 OS 沙箱。可信测试收集/运行器身份、完整验收映射、成对结果到 record_validation 的交接仍未完成，validation_committed 持续为 false，候选不自动晋级/发布。

## 独立复测观察记录（2026-10-12）

- ReplayRunner 对已 completed 的编码重放增加独立复测，通过 Runtime.run_task_process / ProcessBroker 执行用户显式测试与回归命令，保留原 PolicyEngine 授权规则；不绕过审批，不使用 shell 展开，不自动重试。只读任务和未完成任务不执行复测。
- 每个隔离目录独占创建 independent-measurement.json，绑定新任务、源任务、固定提交、候选内容哈希、命令组和实际进程结果。记录退出码、耗时、脱敏输出与结果分类；待审批、拒绝或未知结果停止后续命令。pytest 无测试、跳过、超时不能视为通过。
- 命令在模型运行前预检查，保留 Windows 路径反斜杠和带空格参数；CLI 在基线运行前检查候选静态门禁，避免已知不合格候选引发无用模型调用。
- 本轮专项 **10 passed**，Ruff 与 diff 检查通过；本轮未重跑全量，上一轮全量为 1422 passed / 17 skipped，排除两个缺失示例模块测试。
- **验收边界**：复测进程退出成功只是观察记录，尚无冻结测试/配置完整性证明、可信测试数量证明及成对比较清单，因此仍保持 validation_committed=false，不调用 record_validation，不自动晋级/发布。文件隔离仍不等于 OS 沙箱；此次测试使用受控进程结果 fixture，未对用户真实任务执行模型和测试命令。

## 显式隔离成长重放入口（2026-10-12）

- 按用户确认范围新增 `sprout evolution replay-compare <artifact-id> <source-task-id> --revision <commit> --output-dir <源仓库外目录> --check <测试命令> --regression <回归命令>`，后续补强后编码任务还必须提供可重复的 `--oracle <测试/配置相对路径>`。自动成长仍仅收集候选，不自动运行历史任务。
- 比较前将 revision 固定为同一提交；基线与候选分别使用独立 Git 副本、全新 Runtime、数据库和任务 ID，不复用原完成节点，不复制审批记录。拒绝输出目录与源工作区相互包含。隔离证据目录保留供核对。
- 候选版本与内容 SHA256 必须匹配；仅支持 SKILL/WORKFLOW，在隔离注册表中注入可信临时技能，不发布或修改源候选。缺配置、未知历史任务类型、共享 Temporal 环境等情况保持等待，不回退共享执行。
- 入口可能消耗配置模型的额度；这是文件/存储/运行时隔离，不是操作系统沙箱。原有审批规则继续生效，不自动批准或强制应用。
- 新增 3 个隔离测试覆盖缺配置、已完成源任务生成新任务、固定提交、候选哈希与状态不变、路径重叠阻止。全量回归 **1422 passed、17 skipped、2 条依赖弃用警告**；显式排除两个原有缺失示例模块测试。
- **仍待接线**：独立且冻结的测试/回归测量、基线与候选成对证据清单、测量结果到可信验证报告的交接。当前输出 `validation_committed: false`，任务 completed 不等于测试通过，不自动 validated/approved/published。人工未知发布核销仍未实现。

## 成长独立测量前置检查（2026-10-12）

- ReplayEvaluator.checks 不再因 test.started/test.passed/test.skipped/test.completed 等轨迹事件名称推断测试成功；轨迹只可提供已知失败线索，不能证明与候选绑定的独立测试或回归已通过。
- 6 个新信号门禁测试通过；成长联合回归 **69 passed**；Ruff 通过。本次未重跑全量。
- 现有 ReplayRunner 仍复用原任务执行，不能作为独立测量来源；尚未接入验证报告交接。已请求用户确认显式隔离验证范围，在确认前不新增自动运行历史任务行为，也不改动人工发布占用核销规则。

## 成长链路第二阶段：验证绑定与发布占用（2026-10-12）

- CandidateManager 增加内部可信测量交接 record_validation：测试、回归、安全和效用门禁均通过，且有测量 run 引用，才按候选内容/类型/名称/版本/作用域/证据/业务元数据摘要写入绑定报告。此接口不作为 CLI 成功标志，也不能接收模型自报的成功值；调用方负责独立测量与来源真实性。
- 普通 artifact 保存不能伪造或替换 growth_validation。历史 validated/pending_approval 缺少报告、内容变化导致摘要失效时，approve 均被阻止。独立测量生产者的自动接线尚未实现，因此自动成长仍只收集待验证候选，不宣称自动验证闭环已完成。
- MetadataStore/SQLite 在原 artifacts.metadata_json 中保存非过期 publication_guard，不新增数据库或旁路发布流程。发布前单条条件 UPDATE 占用精确快照；同名技能（大小写归一）跨候选 ID 的 active/uncertain 占用也会阻止再次发布。
- 文件、注册表或知识写入成功后才由原 owner CAS 提交 published/completed。异常、取消或完成提交失败保持原状态与 uncertain/active，占用不自动释放；普通保存、重验证、拒绝、轨迹重收集不能覆盖占用。完成后的监测/稳定/弃用生命周期仍可正常推进。
- 缺消费者、缺持久技能仓库、未支持的 artifact 类型在产生副作用前拒绝。成功事件在 published 落库后才发送；通知失败日志单独报告，不重新执行发布（尚无可靠事件 outbox）。
- 只读查询：`uv run sprout evolution candidate-report <artifact-id>`。输出验证记录、指纹是否匹配、发布占用、错误和是否需人工核对；不释放占用、不触发重试。ID 前缀有歧义时拒绝操作。
- 专项回归 **63 passed**；全量回归 **1413 passed、17 skipped、2 条依赖弃用警告**，继续排除两个原有缺失示例模块测试。新增 11 个测试覆盖历史门禁、伪造元数据、内容变化、并发连接、同名目标、部分失败后重启、完成提交失败、取消和生命周期；CLI 帮助、Ruff 与 diff 检查通过。
- 边界：没有自动撤销已发生的副作用，未知发布期间技能文件或内存注册可能已部分生效，需核对真实仓库/注册表/知识记录；这不是跨文件/SQLite/知识库的原子事务，也未实现人工证据核销 API 或发布进程强杀专项。不要手改数据库清除占用。

## 成长链路第一阶段：缺少测量时保留候选（2026-10-12）

- 用户确认：缺少验证保持候选；历史无可信报告的 validated 同样应禁止发布；发布未知结果保持占用、不自动重试。以下仅是第一阶段，不代表三条全部落地。
- HardGate 要求测试和回归测量显式成功；None 不再满足验证门禁。CandidateEvaluator 区分 passed、pending、拒绝：缺少测量且无已知失败保持 pending，已知失败或安全违规仍拒绝。
- CandidateManager 保存 growth_evaluation 状态、缺失原因和效用分数；候选重启后仍为 candidate/waiting_validation，不进入 validated 或直接批准。效用分数不能替代测试证据。
- TrajectoryGrowthService 保留等待验证的提案，不因缺测量直接丢弃；人工仍可明确拒绝 candidate。自动收集没有可信测量输入时只产生候选。
- 专项回归 **52 passed**（包含 9 个新测试）；Ruff 通过。原发布单元测试改为显式 prevalidated fixture，仅用于隔离旧发布逻辑测试，绝不是新增的可信验证来源。
- 本阶段全量回归：**1402 passed、17 skipped、2 条依赖弃用警告**；继续显式排除两个原有缺失示例模块测试。
- **尚未实现**：可信验证报告写入/内容绑定、历史 validated 发布门禁、发布 CAS 占用与完成提交、部分成功/未知结果的人工核对。当前旧 approve 发布顺序仍有风险，不能宣称已修复；这几项为下一阶段，沿用用户已确认规则，无需重新选择策略。

## 本轮实现

- 安全边界：删除操作仅允许 ALLOW；工具入口接入统一 PolicyEngine，命令硬底线不能被人工默认放行策略覆盖。
- 完成标准：审批只批准变更；未执行、部分执行、失败的验证均阻止普通应用。
- 独立强制应用：`sprout project apply <proposal-id> --force --override-reason "原因"`。必须先批准，豁免记录先于应用写入；强制应用不代表验证完成。
- 需求用例：计划生成具体输入与期望输出，在实现前冻结测试，记录基线结果，实现后逐用例执行。测试被修改或移除会拒绝验证。
- 最小 Demo：从正常输入用例生成 `examples/sprout_demo_<task-hash>.py`，独立运行公开函数、断言输出、打印结果，与测试共同纳入完成检查。
- Projects：新增 ProjectAnalysisStore 契约，通过 StorageBundle 装配五个 project_* SQLite 权威库。发布当前快照指针前先完成分区写入；缓存失败不阻止权威写入。项目事实不再由运行时分析服务写入 sprout_knowledge。
- 增量分析：指纹覆盖全部扫描资源，不再只覆盖任务读取清单；当前结构图重建，删除文件只留在历史版本中。

## 模块边界

Strategy 定义验收数据；Execution 生成可执行测试和 Demo；NodeExecutor 协调 Broker 执行并记录证据；ChangeProposalService 统一应用门禁；WorkspaceService 协调分析与权威写入；Storage 持有持久化实现。CLI、Gateway 和工具入口委托服务。

## 尚未完成的范围

- 目前输入输出测试与 Demo 适配器仅支持 Python 公开函数（同步或异步），不能替代 CLI、HTTP、数据库或 UI 的真实集成验收。
- TEST_DESIGN 目前由计划任务的 require_acceptance_tests 标记开启；所有旧任务与直接创建任务的迁移覆盖尚需检查。
- 五库当前完整保留现有分析器产物、证据与置信度；并不意味着所有语言、ORM、接口和数据库结构都已被精准解析。
- 跨进程快照发布并发、故障注入、外部派生图同步与历史清理还需专项验证。
- 最终全量回归与文档同步未收尾，不应将本文件视为全部修复完成声明。

## 已验证

- 安全与完成门禁相关首轮测试：82 项通过。
- Projects 重启读取、重复写入、删除文件与现有分析回归：16 项通过。
- 加入最小 Demo 后的端到端、安全与项目持久化联合测试：22 项通过。
- 另一次联合回归：43 项通过（在加入 Demo 前执行）。这些数量有重叠，不累加。

全量收集存在两个原有示例测试缺失被测模块：test_bubble_sort.py、test_hello_world.py。本轮未创建占位实现，也未删除这些测试。源码静态检查排除原有 approval.py 与 test_approval_visibility.py 问题后通过；完整 Ruff 还会扫描本轮测试临时目录，需要清理或显式排除后再核验。

扩大回归结果：排除上述两个文件后，1217 项通过、7 项跳过、11 项失败。已修复其中缺失 evaluation 的空值错误，更新了删除安全预期及两层拓扑预期；相关复测 49 项通过。尚存 Gateway 在非 Git 工作区被 TEST_DESIGN 拒绝（2 项），以及 AgentLoop 未传递 workspace_request 到 Runtime 的同意链路问题（4 项，现有代码路径中缺失）。因此全量验收仍未通过。

## 后续修复与最新验收（2026-10-10）

上述结果为历史记录；最新结果如下：

- AgentLoop 的普通、恢复和流式工具调用均传递工作区请求，并停止继续模型调用，由 Runtime 统一征求同意。
- 非 Git 项目使用独立只读执行图，不提供写入工具；明确编码请求要求用户自行初始化 Git，Sprout 不自动初始化。聊天入口返回明确阻断提示。
- 已识别的直接编码请求强制开启需求用例设计，不能通过调用方关闭该标记；未知意图与历史任务的全面迁移仍待完善。
- FileBroker 对删除增加按路径、任务、沙箱绑定的审批；拒绝、无人值守及其他任务的授权不能放行。Patch 预检删除和重命名，在获批前不产生文件变更。
- 编码节点保存 pending_tool_calls 和审批标识，进入等待状态；恢复后执行原调用，而不是假装已经完成。
- 专项联合测试 62 项通过；后续入口、取消、同意及端到端联合测试 52 项通过，计数有重叠。
- 最新全量回归：1230 项通过、7 项跳过、2 条依赖弃用警告。仍显式排除 test_bubble_sort.py 和 test_hello_world.py，因原有被测模块缺失；不是无条件全量通过。
- 本轮修改静态检查与 git diff --check 通过，CLI、project apply 与 MCP 帮助入口通过。完整静态检查中的原有问题、MCP inspect 和其他部署级验收尚未全部收尾。
- 本轮临时测试输出集中于 .workbuddy/test-runs，验收后清理。

Python 用例与 Demo 适配器、多语言/ORM 精准解析、跨进程项目快照并发、历史清理，以及 Planner/Router/Temporal/Replay 等其他审查项仍未完成。

## P0 收紧与 Projects 故障验证

- 普通应用除通用验证状态外，还逐项检查冻结验收用例和最小 Demo 的执行通过记录。新生成的所有有变更提案都要求验收证据，即使直接任务没有设置设计标记。计划失败的回退任务也保留验收要求。
- 带验收要求的编码任务未产生提案时，不再以 no_change_proposal 冒充成功。历史提案未携带新元数据的兼容路径仍待迁移；尚不能宣称全部历史入口完成统一。
- 新增真实 Git 端到端矩阵：删除/重命名 × 获批重启/拒绝/取消。已核验真实仓库文件和提交，不只检查状态。
- 修复两个真实链路缺陷：静态检查不再读取已删除文件；沙箱差异改为相对 HEAD，避免 git add -N 暂存删除后将其漏出提案。
- Git 状态使用 NUL 分隔解析，重命名同时保留原路径与新路径，避免应用提交漏掉原路径。
- 用户确认审批拒绝后保留等待，由用户决定重试或取消，不自动取消。
- Projects 分区故障注入证明：失败不会发布新指针，旧快照重启后仍可读取，重试可恢复。快照记录预期事实 ID，缺失事实会明确报错；身份包含知识产物，避免同资源指纹下不同知识被误判为同快照。分析版本升为 3，旧版本通过重新分析更新。
- 完成门禁和审批矩阵联合测试 28 项通过，Projects 故障/重启测试 2 项通过。
- 本轮扩大回归：1238 通过、7 跳过，仍排除两个原有缺少模块的示例测试；本轮静态检查通过。随后增加“所有新变更提案均要求验收”的收紧，需下一轮补充入口专项测试。

后续顺序：历史提案/任务门禁迁移与审批过期等边界补测 → Projects 跨进程并发与派生索引一致性 → 语义解析精准度 → 多类型验收适配器 → Planner/Router/Temporal/Replay → 完整工程化收尾。

## 显式任务类型与历史保护

- 用户确认采用 code / read_only 显式类型，编码入口默认 code，不再靠需求关键词决定写入权限。类型保存在既有任务元数据中，避免新增第二份状态权威。
- 编码任务强制验收标记且要求用户自行初始化 Git；只读类型走独立只读图。创建入口覆盖 CLI、Gateway、Daemon、RPC 和项目 HTTP。
- 缺失或非法类型的历史任务在执行和恢复前阻断。仅未执行且无节点/提案的历史任务可用 task-confirm-mode 确认；已经执行过的任务需重新创建。
- 新增类型专项测试，并更新真实只读 Daemon/RPC 场景；入口联合回归 37 项通过。本轮修改静态检查通过。
- 类型修改前扩大回归为 1241 通过、7 跳过；类型修改后扩大回归仍在核验，不能沿用此前数字宣称通过。
- 历史提案仅通用绿色测试不足以普通应用；仍保留带豁免原因的强制应用。Projects 跨进程测试验证两个写者不会混合分区，但尚未实现防旧版本覆盖新版本的冲突控制。
- SDK/委托入口补齐显式类型传递。扩大回归出现多个失败并长时间无输出，已停止；遇错即停定位到委托只读场景缺少类型，已修复。扩展联合复测同样长时间无输出后停止，未获得通过结果，后续必须定位剩余失败及停滞原因，不得视为验收完成。

## 入口回归收敛与快照发布冲突控制

- 带诊断回归正常结束，未复现死锁；剩余 6 个失败均为只读入口/测试遗漏显式类型。补齐 transport/manager 参数，飞书与微信原有 file.read 入口固定走 read_only；入口联合测试 59 项通过。
- 执行、恢复和外部调度单节点复用同一类型校验；只读调度拒绝写入节点。历史类型确认注册 task.mode_confirmed 审计事件。CLI/remote/MCP 支持传递类型，MCP 创建统一走 Gateway，并纠正原来被记作 CLI 的来源。
- Projects save_analysis 必须携带分析前读取的 expected_snapshot_id；SQLite 最后发布指针使用条件写入，冲突明确失败，内存后端遵守同一契约。发生冲突前不会更新派生缓存。旧分析不能覆盖已发布的新分析；分区故障仍保留旧指针。
- 跨进程测试将两个写者同步到分区写完、发布前，验证最终条件写入允许一方成功、一方报冲突；另测 SQLite/内存旧基线拒绝。Projects/工作区/SQLite 联合测试 30 项通过，发布时点并发与 Web 联合测试 35 项通过。
- 本轮扩大回归 1247 通过、7 跳过，仍排除两个原有缺少模块的示例测试；其后 MCP 与审批展示专项 31 项通过。全项目 Ruff、git diff --check、CLI 帮助与 MCP inspect 通过（修复了 4 处原有纯格式问题）。
- 最后权限核验发现：模型可手写未展示的工具名，空节点工具集会回退到全局注册表。新增对抗性测试先复现失败，再将 Agent 调用改为严格按上下文工具集执行；空集合也拒绝全局回退。联合回归 53 项通过，最终扩大回归待下方结果。
- 严格范围的越界调用返回正常 Unknown tool 拒绝结果，不触发异常；对抗测试同时检查分析完成且工具未执行，避免因流程崩溃而误判安全。两个预算测试显式声明其需要的 Echo 工具；相关联合测试 26 项通过。
- 最终带逐项输出和 20 秒调用栈诊断的扩大回归：1249 通过、7 跳过、2 条依赖弃用警告，99.63 秒完成；仍明确排除原有缺少模块的 test_bubble_sort.py / test_hello_world.py。全项目静态检查和补丁格式检查通过。一次静默输出整套运行发生尾段长时间无输出而中止，尾段独立测试 45 项通过，最后整套复验未复现；间歇停滞的根因尚未证实，保留回归稳定性风险，不把中止运行算作通过。

后续仍需完成派生索引版本一致性、项目语义解析精度、多语言及异常输出验收适配器、Planner/Router/Temporal/Replay 与部署级验收。本次未删除或改名历史 .workbuddy 文档引用；它是已有本地暂存约定，不是产品运行依赖。

## Projects 版本缓存一致性

- 原缓存用一个可变“最新分析”键，旧分析较晚镜像会覆盖新结果。改为 project:{id}:analysis:{snapshot_id}，查询仅使用权威层选定的版本；不维护派生层最新指针。
- 缓存命中检查项目 ID、快照版本和内容身份。快照身份增加完整代码图及 manifest 有效字段，排除扫描时间以保持稳定；分析版本升为 4，旧版本通过重新分析升级。
- 热缓存异常、非法/旧格式和内容不匹配均按未命中处理，回退项目权威层；镜像故障仍不影响已成功发布的权威数据。镜像设置 3600 秒 TTL，失效清理已知版本，其他进程未知版本由 TTL 回收。
- 更新存储拓扑中的项目 Redis 派生注册。未把项目事实写回 sprout_knowledge；Milvus/Neo4j 项目专属索引发布链路仍未实现，不能宣称全部派生索引已完成。
- 版本缓存、故障回退、内容损坏和快照联合测试 24 项通过；全项目 Ruff 通过。扩大回归结果待补充。
- 最终扩大回归 1253 项通过、7 项跳过、2 条依赖弃用警告，101.79 秒完成；仍排除两个原有缺少被测模块的示例测试。拓扑追加专项 20 项通过，静态检查及 git diff --check 通过。验收后清理本轮测试临时目录。

## 项目事实精度首轮修复

- Python、Node、Go 和 Rust manifest 事实只引用实际来源文件，不再把此前读取的其他 manifest 累积为证据。
- 接口资产按项目相对目录识别，父目录命名 api 不再使全部文件被误标。资产清单移除静默的 20/30 文件截断，保持语义库存完整。
- 扫描及事实提取排除 .workbuddy 等本地临时目录，排除规则不区分大小写；资源遍历拒绝解析后越出项目根目录的路径。缓存中测试夹具不再虚构项目语言、protobuf 或测试约定。
- pytest 风格推断引用实际存在的嵌套测试文件，不再引用可能不存在的根 tests 目录；仅有空 tests 目录不构成测试证据。按语言生成的候选测试命令明确归类为推断，不作为已配置、已执行的事实。
- manifest 的非法 UTF-8、非对象 JSON 和环境文件非法编码不会中止整项目分析。分析版本升为 5，旧落库分析通过重新分析更新。
- 新增 6 项精度回归，联同项目权威存储、缓存和工作区测试 30 项通过；全项目 Ruff 和 git diff --check 通过。扩大回归结果待补充。

本轮没有引入新的存储权威或入口解析逻辑。尚未实现 ORM/SQL 结构语义、多语言接口语义；manifest 汇总事实的细粒度来源、外部技能目录变化失效与同名技能唯一身份仍需继续核验。扫描路径约束不是完整的并发文件替换安全保证。

最终扩大回归：1259 项通过、7 项跳过、2 条依赖弃用警告，108.41 秒完成；仍排除原有缺少模块的 test_bubble_sort.py 与 test_hello_world.py。本轮临时测试目录验收后清理。

## 技能身份与外部输入失效

- 新增回归先复现：同名技能 ID 重复导致 SQLite 发布后缺失事实；Workspace 自建扫描漏掉 TOML 技能并纳入隔离区；外部技能变化不影响旧分析复用判断。
- Workspace 改为复用 skills.loader.iter_skill_artifacts 和既有完整产物 digest，删除重复 frontmatter 解析。统一覆盖 TOML、SKILL.md、已提升的 fetched/evolved 目录，隔离区不纳入描述。没有修改技能信任审批或执行权限。
- 技能节点及事实使用产物位置生成稳定 ID，同名安装分开保存；工具节点去重，requires 边引用声明文件。内容变化保留实体 ID，通过快照版本区分历史。
- 外部技能完整 digest 纳入分析指纹，在缓存复用判断前加载；支持说明文件及辅助资产修改、新增、删除和取消技能目录配置。分析版本升为 6，既有落库版本需重新分析。
- 加载器采用函数内延迟导入，避免 skills → security → workspace 包初始化循环；没有把领域逻辑移入 Runtime 或入口。
- 文件 SQLite 重启、同名实体持久化、资产变更和删技能，以及内存/热缓存命中后的重新分析联合回归 42 项通过，包含原有技能信任和 Runtime 装配测试。全项目 Ruff 与 git diff --check 通过；扩大回归结果待补充。

仍待完成：ORM/SQL 和接口语义解析、manifest 汇总来源精细化、项目专属 Milvus/Neo4j 索引、多类型验收与其他初审事项。统一加载器的完整 digest 当前会读取技能全部资产，超大技能目录的成本尚未优化；本轮未实现并发文件替换的原子扫描。

最终扩大回归：1263 项通过、7 项跳过、2 条依赖弃用警告，113.87 秒完成；仍排除原有缺少被测模块的两个示例测试。本轮测试临时目录验收后清理。

## Python 数据库与接口静态声明首轮

- 新增 workspace/python_contracts.py，用 AST 解析明确导入的 SQLAlchemy Core Table/Column 及 FastAPI/FastAPI Router 装饰器；不导入或执行项目代码，不新增依赖。
- database_table 保留表名/schema、列清单、类型表达式、主键、唯一/索引声明、外键目标、默认值表达式及源码位置。未知布尔值保留 null；未解析的表约束记录 unresolved_elements，不假装完整。
- interface_endpoint 保留 HTTP 方法、静态路由及 router 本地前缀、处理函数、参数/返回注解、响应模型表达式、状态码及源码位置。明确 mount_path_resolved=false，不能将局部声明冒充最终部署 URL。
- 动态表名/路径或无法解析的源文件产生分析警告；明确的重新绑定、导入遮蔽和条件写入使后续接收者识别失效，避免仅凭 get/Table 等名称误报。
- ProjectKnowledgeItem 增加 details，沿既有项目事实契约和快照写入 database/architecture 两个权威分区，不新增存储权威。修复通用 codec 将 list 转成字符串的问题；文件 SQLite 重启证明列与参数清单无损读回，源码删除后当前事实消失、旧事实标为过期。
- ProjectAnalyzeTool 的独立回退也统一委托 WorkspaceIntelligence，移除第二套部分分析流水线；工具和 project knowledge --json 输出完整 details。分析版本升为 7。
- 专项联合回归 36 项通过；扩大回归、最终静态检查结果待补充。

范围边界：本轮未实现 SQL DDL、SQLAlchemy declarative/mapped_column、其他 ORM、多语言框架、完整请求响应 schema、调用方或跨文件 router 挂载。源声明不代表实际运行过的路由/数据库。完整领域表级查询契约仍未实现，当前结构化声明沿既有版本化事实记录保存；没有宣称总体语义解析全部完成。

最终扩大回归：1271 项通过、7 项跳过、2 条依赖弃用警告，103.88 秒完成；仍排除原有缺少被测模块的两个示例测试。全项目 Ruff、git diff --check、project knowledge CLI 帮助检查通过。本轮临时测试目录验收后清理。

## 声明式 ORM 本地静态解析

- 新增测试先复现：8 项场景中 7 项失败，原分析未识别声明式表；非 SQLAlchemy/已重新绑定基类的拒绝行为保留。
- 新增 python_database.py 承担 SQLAlchemy Core/ORM 共享列解析与本地声明式模型解析；python_ast.py 统一 AST 符号、表达式和绑定失效辅助逻辑。没有在 Runtime、工具或 CLI 内新增 ORM 判断分支。
- 支持明确导入的 DeclarativeBase/DeclarativeBaseNoMeta、无定制参数的 declarative_base()、本文件抽象基类、Column、mapped_column 及 Mapped 注解列。保留模型名、表/schema、列属性与数据库列名、类型和注解表达式、外键目标、默认表达式、源码行号及原始声明表达式。
- relationship 保留声明目标、back_populates、secondary/uselist 表达式和源码证据，但 target_resolved=false，不冒充已找到跨文件目标或真实数据库外键。
- 基类重新绑定、条件导入遮蔽、字段重写、类内 Column 遮蔽和纯注解不覆盖旧 Column 均有回归。动态元数据、条件类体、定制基类和无法确认的基类产生警告，不靠名字猜测。
- 对照 [SQLAlchemy 官方声明式表文档](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html) 核对注解配置与显式参数边界。未解析 Annotated/类型别名内配置时，primary_key/unique/index 缺省保留 null，而不是虚构 false；注解类型映射和 nullable 推断尚未实现。
- ORM 记录完整结构始终 complete=false、inherited_elements_resolved=false；本轮不声称解析继承来的字段或实时数据库。分析版本升为 8，既有版本需重新分析。
- 项目权威库重启、暖缓存后添加列、模型删除，以及 Core/FastAPI/技能缓存联合回归 50 项通过。全项目 Ruff 通过；最终扩大回归待补充。

后续：SQL DDL 静态解析、跨文件路由挂载；跨文件 ORM 基类、完整继承、类型别名/Annotated 配置、其他 ORM 和表级领域查询契约仍需单独完善。当前不新增依赖，也不执行用户模型代码。

最终扩大回归：1285 项通过、7 项跳过、2 条依赖弃用警告，105.40 秒完成；仍显式排除原有缺少模块的 test_bubble_sort.py 与 test_hello_world.py。全项目 Ruff 和 git diff --check 通过；本轮临时测试目录验收后清理。
## SQL DDL 源码声明清单

- 新增 Workspace 层 SqlContractAnalyzer，不执行 SQL、不新增依赖；仅分析工作区内被分类为 SOURCE 的 .sql 文件，复用已有扫描、指纹和五分区权威落库链路。
- 支持 CREATE TABLE 的显式名称、限定名、列声明原文、表约束原文与源码行号；保留嵌套表达式和引号中的逗号/分号，不将复合主键误推算为每列唯一。
- 方言未确定，complete=false、migration_state_resolved=false。当前未将类型、默认值、主外键等原文归一化为完整语义；ALTER/DROP/CREATE AS 等不支持声明给出警告，不计算迁移后数据库结构。
- 字符串、注释及 dollar-quoted 函数体内的 CREATE TABLE 不作为表声明；畸形引号、括号、嵌套注释和方言相关反斜杠转义保守报警。源文件/令牌数量有上限。
- 提取 source_contracts.py 统一 Python/SQL 证据身份；分析版本升为 9。SQLite 文件库重启、源码修改和删除失效回归通过，联合 Python Core/ORM/FastAPI 专项共 34 项通过。
- 扩大回归：核心 1271 项、Web 26 项通过，合计 1297 项通过、7 项跳过、2 条依赖弃用警告；仍排除原有缺少被测模块的 test_bubble_sort.py 和 test_hello_world.py。全项目 Ruff 与 git diff --check 通过。本轮独立测试目录验收后清理。
- 后续仍需 SQL 方言语义、迁移状态、跨文件路由挂载等，不宣称数据库语义解析全部完成。

## 跨文件 FastAPI 路由包含与源码路径组合

- 新增 python_routes.py，在 Workspace 层对同一轮已解析 AST 做本地模块、相对/绝对导入、别名、重导出和 src 布局解析；不重复读取源码，不执行导入/函数，不新增依赖或存储权威。
- 保留原始 interface_endpoint；新增 interface_mounted_endpoint，记录应用对象、组合路径、原始声明 id、各级 include_router 前缀及源码证据链。多个前缀生成独立记录，部署状态明确 false，完整运行语义仍 complete=false。
- 动态前缀/目标、循环与歧义导入、越界相对导入、遮蔽/重新绑定、条件修改、自包含、未知函数调用及非标准构造器保守报警，不推算已解析路径。
- 初始四个挂载测试全部复现缺失。核对 [FastAPI 当前官方多文件应用文档](https://fastapi.tiangolo.com/tutorial/bigger-applications/) 后发现新版包含已采用活引用，不能将旧版复制规则固定当作通用语义；挂载后子 router 新增声明/嵌套包含会使祖先应用的组合结果失效并提示版本语义未解析。仅输出不受该差异影响的静态声明子集。
- 文件 SQLite 重启证明结构化挂载链进入既有 architecture 权威分区且无损读回；修改前缀、更改/删除路由文件会更新当前快照并标记旧事实过期。分析版本升为 10。
- 范围边界：尚未支持应用工厂、函数内注册、动态模块路径、跨组件自定义导入根、Starlette mount 子应用、代理 root_path、完整请求响应 schema 和调用方。源码路径不代表实际服务已运行。
- 最终接口/ORM/SQL、项目快照与缓存专项联合回归 63 项通过；Ruff 与 git diff --check 通过。中间版本扩大回归 1312 项通过、7 项跳过，但不能将其计作最后版本的完整验收。
- 最后版本完整回归收集 1324 项，停在 test_workspace_consent.py::test_a_status_question_is_not_run_as_a_task 的异步收尾阶段；单独复测该用例 0.13 秒通过，带 faulthandler_timeout=60 的整套复跑再次收尾停滞。已终止本轮已验证身份的两个停滞测试执行，没有跳过该用例或把未完成回归记为通过。
- 只读检查发现 Runtime.stop() 未调用已有的 stop_worker()，属于可能相关的后台 worker 生命周期缺口；这尚不是停滞根因的完整证明，需要先补关闭任务泄漏的确定性复现、再修关闭链路。本轮未修改 Runtime 关闭逻辑，也未将 task 的取消/重启策略擅自改变。
- 本轮测试临时目录结束后清理；下一优先级先收敛 Runtime/异步 worker 关闭问题，再做接口请求/响应 schema 静态解析。

## Runtime 与本地任务 worker 关闭链路

- 三个确定性测试先复现全部失败：Runtime.stop() 未等待空闲 worker；未启动 Runtime 的提前返回遗漏手动启动 worker；执行适配器消费取消后，旧 while True 继续取下一项并卡住关闭。
- Runtime.stop() 统一停止并等待已有 worker，包含手动启动场景。worker 使用显式停止标记，即使执行层消费 CancelledError，也不再取下一项。并发 stop_worker 不重复取消同一消费器，后续显式 start_worker 可重新启动。
- 关闭顺序统一为 worker 退出、后台写入完成、outbox 最终刷新、事件和存储关闭。失败 worker 的异常保留给调用方，但 finally 仍完成必要存储关闭，不把关闭失败藏成成功。
- 文件 SQLite 证明执行退出阶段仍能写入，关闭后重新读取的任务状态保持 RUNNING；本轮不调用用户 task-cancel，不取消 Temporal 服务上的持久化工作流，不改变任务重试/恢复策略。
- 关闭、队列、原有完整同意流程与 outbox 最终专项联合回归 45 项通过；Ruff 与 git diff --check 通过。完整核心和 Web 回归正常结束：1324 项通过、7 项跳过、2 条依赖弃用警告，123.60 秒；仍显式排除原有缺少被测模块的 test_bubble_sort.py 与 test_hello_world.py。此结果同时覆盖上一轮未完成的最终路由解析整套验收。
- 仍不承诺能够中止永久拒绝取消且永不返回的任意外部适配器；本轮修复本地 worker 生命周期泄漏和消费取消后再取任务的确定性问题。
- 原有完整工作区同意流程额外连续复测三轮，每轮 29 项通过，均未出现收尾停滞。本轮实际生成的 7 个独立测试临时目录已清理；修复前的纯内存复现没有生成临时目录。下一步可继续接口请求/响应 schema 的源码解析。

## T01：统一验收报告与失败分类（2026-10-12）

- Execution 定义版本化报告，由既有 TestResult、EVALUATION 和变更提案证据派生；没有数据库迁移或第二个存储权威。应用/完成门禁共用同一报告计算，不能以旧的绿色标记覆盖新增失败证据。
- 区分测试失败、审批等待、策略拒绝、runner 错误、超时、跳过、缺失结果和显式无效基线；保留原有验收规范与最小 demo 门禁。强制应用保留豁免记录，但报告不算验证完成。
- Runtime / ProjectGateway 提供只读查询，CLI 增加 task-verification --json，Web 增加 GET /api/project/tasks/{task_id}/verification。查询不重新执行代码，不信任缓存报告，不写任务状态。
- 新增专项覆盖分类、旧记录兼容、详细失败覆盖旧绿色标记、豁免、SQLite 关闭重开与 CLI JSON 输出。初次完整回归仅新增测试误用同步 close 接口失败；修正后专项 23 项通过。最终完整结果见下。
- 边界：pytest 文本摘要仅作为保守阻断，不是可信 runner 的防篡改证明；混合通过/跳过也阻断。固定 10 个内部评测任务、Python 全链路 demo 和 Temporal 提交恢复（T02/T03）尚未实现，本轮没有导入外部框架或扩大执行权限。
- 最终核心与 Web 回归：1347 项通过、7 项跳过、2 条既有依赖弃用警告，158.57 秒；仍明确排除缺少原有示例模块的 test_bubble_sort.py 与 test_hello_world.py。Ruff 与 git diff --check 通过。测试目录统一位于忽略的 .workbuddy/test-runs 下，没有新增根目录缓存。

## T02：固定任务评测与 Python 闭环 Demo（2026-10-12）

- 新增 evaluation 夹具、脚本模型与独立 runner，通过 Runtime Factory 和公开业务 API 驱动现有链路，不导入 tests、不增加第二条编码业务流或数据库权威。
- CLI 增加 project eval、--case、--output、--json；参数/环境检查先于任务写入。拒绝配置中的 Temporal opt-in、不调用真实账号或付费模型、不自动安装依赖。仅新建专用 Git/SQLite/项目事实目录，模拟审批只绑定夹具任务；fixture Git 禁用本机全局 hooks/signing 影响，不改变真实仓库设置。
- 十个场景覆盖分析事实与重启读回、边界修复、JSON 正常/边界/非法输入、双模块变更、错误实现阻断、强制应用豁免、本地审批恢复/重复恢复、只读工具注入与删除审批拒绝后保持等待。原回归与补丁范围独立检查；冻结测试、demo、审批、补丁、轨迹和应用 revision 留证。
- 工作树之外的固定输入输出检查器用 -I，在应用前确认待修复基线、应用后复核所有案例。脚本模型输出不作为解题质量证据，也不将故意失败场景的检查通过计作编码完成。
- 首轮夹具失败已据实保留：缺 src 布局、显示名称/状态比较错误、动态挂载影响同一应用、未固定 lint 配置、示例导入格式。没有放宽业务门禁来使夹具通过。另加已通过的基线无效注入和 CLI 非绿色退出码测试。
- 正式 CLI 基线 10/10 场景检查通过，四个编码任务验证完成，约 33.23 秒；预期失败代码 check_failed=2、verification_overridden=1。证据保留在 .workbuddy/evaluation-evidence/sprout-eval-82inmaut；使用说明与 Python demo 入口见 Offline-Evaluation-Guide.md。
- 最终核心与 Web 回归 1353 项通过、7 项跳过、2 条既有依赖警告，136.08 秒；仍排除原有两个缺示例模块的测试。Ruff、git diff --check 与 CLI help 通过。
- 未完成：真实模型能力评测、统一子进程 Python 环境绑定、OS 隔离、真实 Temporal 联调；T03 提交失败窗口与幂等恢复尚未开始。本轮没有创建根目录测试缓存，评测及测试证据集中在忽略目录下。

## T03：Temporal 客户端提交与恢复（2026-10-12）

- 用户确认：不可用或提交超时时持久化等待状态、允许重试、不回退本地。先复现六项失败，再修复；没有连接真实 Temporal 服务或启动容器。
- 移除 Temporal 队列的本地 FIFO 双投递和占位 SproutTaskWorkflow 提交，统一由 Runtime 编译执行图后进入 Temporal 后端。队列要求持久化 MetadataStore 和匹配的 Temporal 后端。
- sprout_core.db 新增 task_submissions，注册 MetadataStore 合约和存储拓扑，网络前冻结执行图、工作流 ID、服务地址及队列。重试优先恢复未确认记录，不因配置变化重新路由。REJECT_DUPLICATE 配合 AlreadyStarted 接回；已确认启动只接回句柄，确认结果防止晚到超时覆盖。
- 网络失败、启动/结果超时和非法响应不是任务执行失败；返回 waiting_resource。明确的工作流失败仍返回 failed。连接/提交等待各限制 10 秒，单次结果等待 30 秒；停止等待不取消远端执行。
- 保留关闭工作流后的审批续跑设计，以持久化决策键区分续跑轮次；重复决策不重复启动。已完成/跳过节点不重放，已通过验收后的编辑/测试轮次继续跳过。完成已落库但活动确认丢失时返回原输出。worker 连接失败也会关闭 Runtime，清空活动引用。
- Runtime / ProjectGateway 增加只读提交查询，CLI task-submissions 输出 JSON；既有 task-run 重试原任务。补充 Temporal-Submission-Recovery-Guide.md，修正中文 README 将 Temporal 误写为强制依赖的说明。
- 专项 17 项通过，Ruff 通过；10 项离线评测再次全部通过，证据位于 .workbuddy/evaluation-evidence/sprout-eval-e1tu9s74。完整回归结果见后续最终验收记录。
- 尚未完成：真实服务与 worker 重启联调、跨版本工作流回放、活动副作用的事务/租约保护、自动重试守护进程。启动确认丢失超过 Temporal retention 的历史不能仅凭 REJECT_DUPLICATE 证明恰好一次；文档明确保留人工核查边界。本轮是 T03 客户端部分，不宣称完成部署级恢复验收。
- 最终核心与 Web 回归：1370 项通过、7 项跳过、2 条既有依赖弃用警告，139.35 秒；仍明确排除原有缺少示例模块的 test_bubble_sort.py 与 test_hello_world.py。专项 17 项、Ruff 和 git diff --check 均通过。未创建根目录缓存，测试与离线评测证据继续集中在忽略目录下。

## T03：活动节点持久化占用与未知副作用保护（2026-10-12）

- 用户明确确认：活动结果未知时保持待核对，不因超时自动重跑，不提供独立强制重跑入口；有可信完成记录则返回原结果。
- 新增 orchestration/execution_guard.py 承载节点执行保护；Runtime 继续负责权限/任务类型和工作区准备，SQLite MetadataStore 提供 claim_execution_node / finish_execution_node 原子操作，workflow 仅负责调度与等待分类。
- execution_guard 嵌入既有 execution_nodes.metadata_json，仍由 sprout_core.db 和原有 execution_node 拓扑登记持有，不增加数据库或第二任务权威。SQLite 条件写入确保一个节点只有一个执行所有者，owner 不因超时失效；最终状态与输出在同一写入中提交。只有原持有人可以提交；普通节点保存不能删除占用。
- 异常、执行取消、进程退出后遗留 active、输出落库失败和历史未确认 running/failed 节点都阻止活动重放。正常审批等待需要新的持久化决策才续跑；新增审批不能释放 uncertain 占用。取消/回滚任务不启动新活动。
- 资源等待与审批等待分开，未知节点阻断后续 APPLY，不标记验证完成。重复活动先返回等待、原执行者后补齐可信完成记录时，可基于该证据恢复新轮次，已完成节点不重新执行。切换本地调度器或从图中遗漏受保护节点不能绕过占用。
- Runtime / ProjectGateway 提供只读执行状态报告；CLI 新增 task-executions，显示 occupied_or_unconfirmed / uncertain / legacy_unconfirmed 等状态，不访问 Temporal、不清理占用。新增 Node-Execution-Recovery-Guide.md，并更新提交恢复说明和 README。
- 初次三个存储用例据实失败（缺失占用接口）；修复后测试覆盖同进程并发、真实独立双进程 SQLite 争抢、关闭重开、所有者校验、工具返回后提交失败、取消、未知结果不因审批重放、正常审批重复决策、Runtime 只读权限、等待续跑和 CLI。新增 19 项全部通过，与原提交专项共 36 项通过。
- 最终核心与 Web 回归：1389 项通过、7 项跳过、2 条既有依赖弃用警告，140.79 秒；仍排除原有缺少示例模块的 test_bubble_sort.py 与 test_hello_world.py。Ruff 与 git diff --check 通过。10 项离线评测再次通过，证据位于 .workbuddy/evaluation-evidence/sprout-eval-nlccsfn0；没有新建根目录缓存。
- 未完成：真实 Temporal 网络/worker 强杀/跨版本回放验收、工具调用粒度的对账与幂等协议、人工可信证据补录/核销入口。纯本地未经过该活动入口的原重试逻辑没有迁移。本轮不承诺全系统恰好一次；当前未知结果只能等待核对或由用户明确取消，不能通过数据库编辑/强制审批绕过保护。

# Real Temporal validation — 2026-10-12

## Lost acknowledgement, active outage and baseline replay follow-up

- Real start request succeeds, then a test wrapper discards the acknowledgement.
  Covered both still-pending and already-completed remote workflows. The client
  records uncertain/start, closes Runtime, reopens the same isolated SQLite stores,
  retries the frozen identity and attaches the original server Run ID. One record
  and one attempt per node remain. This is boundary fault injection after real
  acceptance, not a physical packet-loss experiment.
- Stopped only the isolated Temporal container while a READ activity was paused
  after real execution. Released the activity while the server was unavailable;
  its completed guard/output persisted locally. Restarting the server resumes
  acknowledgement and later nodes without duplicate execution or submission.
- User selected repository commit `7b377ac` as the old-version baseline. A frozen
  test-only source snapshot runs against the real test server with fixed activity
  outputs. Four scenarios cover normal execution, completed passed evaluation,
  skipped node and cancelled activity result. Both baseline and current server
  histories are saved; current code replays both and uses new scheduling rules.
- This exposed three scheduling incompatibilities. Added stable SDK patch markers
  `sprout-resume-skips-v1` and `sprout-cancel-stop-v1` to preserve legacy replay
  command order. Runtime cancellation/permission/ownership safety gates remain
  intact. Markers and legacy branches must not be casually removed.
- Real integration plus baseline suite: **10 passed**. Latest broad regression:
  **1393 passed, 17 skipped, 2 dependency warnings**, explicitly excluding the two
  original missing-example-module tests. Ruff and diff whitespace checks passed.
  The first broad run exposed a unit test missing the SDK patch context; only its
  test mock was corrected before the successful full rerun.
- Production histories, running-workflow rolling upgrades and every intermediate
  unmarked development version remain unverified. Baseline activity fixtures test
  workflow command compatibility, not old-version business/tool behavior. Manual
  evidence reconciliation remains unimplemented; unknown side effects stay blocked.
- Existing project containers are untouched; the isolated server is restored and
  its dedicated evidence database volume remains available.

## Worker process termination follow-up

- Added a test-only worker subprocess using the production Runtime factory and
  node activity entry point. The real executor completes its READ work, appends
  a test-owned effect marker, and pauses before guard completion. The parent
  forcibly terminates only that child process and starts a replacement SDK Worker.
- The original active claim survives in SQLite. Actual Temporal activity retry
  (history attempt >= 2) returns resource waiting without executing the node again.
  Total node attempts and effect marker count remain one; no proposal or source
  mutation occurs. Repeating public execution remains blocked. The resulting
  actual history also replays successfully.
- Test-only serialized activity timeouts are 5 seconds; production budgets are
  unchanged. This is node-level interruption protection, not per-tool exactly-once
  delivery or production operating-system isolation.
- Real isolated suite: **3 passed**. Latest broad regression: **1393 passed,
  10 skipped, 2 dependency deprecation warnings**; still explicitly excludes the
  two pre-existing tests with missing example modules. Ruff and diff whitespace
  checks passed. Real tests skip by default.
- Remaining: lost submission acknowledgement, server interruption during active
  work, historical deployed-version replay, and evidence-based manual result
  reconciliation. Earlier "not verified" notes for the specific scenarios above
  are superseded by this section, not by a general production-readiness claim.

## Isolated server outage follow-up

- Real Docker fault injection passed: verified the exact container's Compose
  ownership before stopping only `sprout-temporal-validation-temporal-1`.
  A `finally` block restarts it and waits for SDK connectivity.
- With the server stopped, public Runtime execution returns `waiting_resource`,
  persists one pending submission, and all node attempt counts remain zero
  (no local fallback). After recovery, the same submission identity completes;
  each node runs once, and repeated execution does not add another submission.
- Real-server suite: **2 passed** including actual history replay and negative
  nondeterminism detection. Related offline suite: **40 passed, 2 skipped**.
  Ruff passed before the final assertion-only additions.
- This tests outage **before submission acceptance**, not lost acknowledgements
  or server interruption during an activity. Worker process termination and
  historical-version replay remain pending. No full-suite rerun this round.
- Both the isolated server and the original application server remain running;
  the isolated database volume is retained for further fault tests.

- Added an isolated Compose project `sprout-temporal-validation`, binding only
  `127.0.0.1:17233`. The existing application Temporal containers are untouched.
- Added opt-in integration coverage using the production Runtime factory, isolated
  SQLite stores, Echo model, real SDK worker and server. Read-only execution and
  repeated public execution produce one durable submission and leave source unchanged.
- Fetched and saved actual server history, replayed it successfully, and verified
  that a copied history with an extra activity is rejected as nondeterministic.
- Validation: real integration test **1 passed**; related offline regression suite
  **40 passed, 1 skipped**; Ruff passed. Integration tests skip by default and reject
  endpoints other than the isolated validation port.
- Still unverified: server outage recovery, worker process termination during an
  activity, and replay of histories from an older deployed version. Current-version
  replay is not proof of cross-version compatibility. No full-suite rerun this round.
- The isolated containers and their dedicated database volume remain available
  for the next fault-injection round; no existing containers or volumes were removed.

# Runtime 隔离执行与审批恢复入口一致性（2026-10-14）

- 统一首次执行与 `resume_task` 的隔离调度器准入检查；缺失或非 callable 时持久化等待资源，不进入工作区扫描、图编译或本地执行。完成、取消、回滚状态保持不变。
- 原有 image_preflight 专用恢复入口限制保留；callable 只是必要接入条件，不代表生产就绪、写入授权或成长验证通过。
- 定向验证：隔离入口、replay resume、Temporal 提交测试共 60 passed（10.79s），Ruff 与 git diff --check 通过。本轮没有运行真实 Temporal/Docker 联测，也没有启用生产编码调度器。
- 后续仍需真实 Runtime/Temporal 固定任务联测及恢复验证；生产会话装配、模型可见命令接口、独立 Git 写入协调器和成长验证交接未完成，不能将先前图驱动夹具测试视为完整生产闭环。

# DeepSeek 首次真实编码试验（2026-10-15）

## 统一只读编码任务报告（2026-10-16）

- 新增正式CLI coding-report，统一展示持久化任务阶段、停止/阻塞原因、请求预算、最近已有独立验证摘要、节点占用、历史workflow结果与独立commit状态。复用commit_status_report做creator及Git证据解释，不创建Runtime、不连模型、不启动Worker、不同步状态。
- 报告只读metadata及operational，明确标注不是跨库全局原子快照；不把历史waiting_approval覆盖当前completed，不把预算耗尽自动转成可重跑或加额度。摘要不导出原始模型内容/完整错误/输出日志，名称与原因有界脱敏。
- 旧数据库没有预算台账时显示legacy_unavailable和null消耗/余额，不伪造0；新台账显示responded与unknown_or_failed、可核对的响应tokens，不当作精确账单或金额硬封顶。
- 本地报告/预算/同步/提交回归28 passed in1.56s，Ruff通过；正式独立进程+真实Docker/Temporal脚本模型集成1 passed in20.17s，报告确认code_review、verified、consumed3/remaining0、无commit请求，查询不更改节点或预算。
- 实际只读CLI查询历史购物车确认completed、独立验证23项、commit applied、原workflow waiting_approval；历史预算不可用的区别正确显示。本轮模型API0次、无Git提交。
- 下一阶段：普通生产权限配置下的正式入口验收（不再使用fixture授权替身），随后有针对性补流式/节点外预算等已知边界；不增加新平行执行体系，不宣称已完成真实模型普通权限验收。

## 任务级持久化请求预算第一阶段（2026-10-16）

- metadata.db新增task_model_requests；调用前单条SQLite条件INSERT原子占用，检查持久化任务max_model_calls与原限制一致、总占用未达上限，跨进程不超限。失败/未知/占用后中断均消耗额度，不退款或自动重置，暂不提供追加额度入口。
- llm/task_budget以ContextVar绑定节点所属任务，不改全局可变预算；NodeExecutor公共入口绑定PLAN/SUBTASK/AGENT等所有节点；统一invoke_model在适配器前预占、响应后结算，预算存储不可用阻止发送。usage统计仍独立尽力写入。
- ModelOrchestrator标记为分发层，统一facade只统计底层尝试，修正原调度层+provider重复usage；重试/备用分别占额度，耗尽异常不重试/降级摘要。不会外推第三方包内部不可见的HTTP重试。
- 持久预算/调度/agent回归31 passed in0.94s，包含不同节点、重开SQLite、并发、独立两个子进程共同消耗4次额度、失败占用及包装层不双算；兼容任务回归44 passed in54.83s。
- 正式CLI分进程+真实Docker/Temporal脚本模型任务，TaskBudget=3，PLAN1+AGENT2总计3次responded，验证/观察/同步仍通过，1 passed in18.78s。证据detached-persistent-budget-1下detached-acceptance.json。
- 真实试验入口显式授权次数现在同时写入TaskBudget（未重新运行付费试验）；本轮真实模型API0次、无Git应用/成长发布。
- 未完成：max_model_calls=0保持无显式上限；流式/节点外调用未统一、token只是响应统计无预留、无金额硬封顶、无加额度交互。下一步统一只读预算/状态报告及真实权限入口验收，不宣称全部预算产品化完成。

## 正式分进程接线验收与执行反馈（2026-10-16）

- 新增正式CLI链路集成：正常coding-submit进程先退出（没有Worker）；正常coding-status进程确认running；独立coding-worker进程通过显式测试adapter使用脚本模型/测试策略；真实Runtime/AgentLoop/Temporal/Docker生成代码并独立验收，通过普通coding-status读取结果、coding-sync原子落metadata waiting_approval。
- 正常coding-submit重复调用返回原记录、不再执行，持久化节点逐项不变、提交记录仍1条；查询和同步进程退出后Worker仍活着，随后通过实际Python SIGINT路径正常停止、exit0。原始与目标基线Git未提交，未发布。
- 两次真实集成分别1 passed in18.71s及反馈改动后1 passed in18.97s；证据formal-detached-feedback-1下detached-acceptance.json，记录不同子进程PID。只有模型/授权为测试adapter，不宣称普通生产权限策略或真实模型分进程已验收。
- execution/coding_feedback专职生成有界诊断，coding_command_tool继续负责调用/审批/结果判定；模型可见退出码、耗时、stdout/stderr，错误不再全部丢失。脱敏后最多8192字符，截断保留头尾；反馈不授予验证通过、不增加执行槽、不重跑未知结果。
- 反馈/工具命令/同步/旧offline harness本地27 passed in0.69s，Ruff通过。原fixture默认仍要求工具审批，仅新测试显式allow_coding_command参数放行，正式策略未放宽。
- 本轮模型API请求0次，无当前项目提交或实验Git应用。生命周期正式接线已有无付费证据；仍待生产权限真实入口验收以及任务级持久化预算、统一用户状态报告，不以fixture通过外推全覆盖。

## 显式结果同步落库与分库职责（2026-10-16）

- 新增coding-sync，复用creator-bound accepted记录选择与观察服务；coding-status仍只读。仅可信结构化workflow结果进入storage/local/sqlite/submission_sync，BEGIN IMMEDIATE内同步metadata.db的task_submissions与tasks，不跨库写入。
- 幂等、原workflow/run/route/payload绑定、已确认结果不可替换、历史记录不投影覆盖更新提交、终态不降级、completed图证据检查及失败回滚均有本地回归。未知节点占用不清除，不重提任务、不执行Git、不发布。
- 同步/观察/提交/Temporal提交回归45 passed in1.09s，Ruff通过；正式CLI帮助可见。已在已完成购物车上实际验证幂等：task_status=completed、历史result_status=waiting_approval、task_status_updated=false、nodes_modified=false。
- 本轮模型请求0次，无Git执行；尚未把整个正式三进程编码流程端到端再验收，不提前宣布生命周期迁移完成。
- 分库说明Storage-Ownership-and-Result-Sync.md：metadata执行权威、operational审批/提交决策、session会话、context上下文、usage用量、projects五分析分区，以及文件/Git证据；说明同名tasks表不等于同一权威，knowledge/cache/vectors等实验内存配置不宣称持久化。

## 生命周期分离第三步：只提交、不等待（2026-10-16）

- TemporalOrchestratorBackend增加wait_for_result布尔模式，默认True完全保留原行为；False在持久化accepted/run_id后返回RUNNING及提交标识，不调用handle.result，不启动Worker。连接/提交未知状态的原持久化规则不变。
- 正式CLI coding-submit复用experiment_runtime及assemble_coding_worker做装配校验，只对已有新鲜任务编译/提交，不调用worker.run、Runtime.start、resume或审批。已有任何提交记录仅返回记录（不重提），已有节点但无提交记录也拒绝作为新任务提交。原host/namespace不可变更。
- 初始流程可先coding-submit接受入队，再由独立coding-worker服务该原队列，另用coding-status只读观察。仍未提供结果显式落库同步入口：观察结果不会自动把数据库任务从running改为waiting_approval，因此尚不能称生命周期端到端全部完成。
- 本地提交/未知记录/观察/Worker/结果等待回归48 passed in0.92s；真实Temporal跨进程测试改用正式submit-only后端入队，无模型activity，查询退出Worker继续，工作流只执行一次；该集成及提交测试8 passed in2.10s，Ruff通过，正式CLI帮助可见。
- 证据detached-submit-process-1；此测试仍不是完整生产coding-worker模型执行链路。本轮真实模型请求0次、无Git提交。下一步显式同步可信工作流结果，再做正式三进程接线验收，随后执行反馈优化。

## 生命周期分离第二步：只读结果观察（2026-10-16）

- orchestration/submission_observation只观察持久化accepted记录绑定的workflow/run；不调用start/cancel、不持有Worker、不创建Runtime、不改数据库。支持0..60秒有界等待，超时只返回wait_expired，不判定执行失败或触发恢复。
- cli/coding_observation负责原实验creator/route检查、只读SQLite选择记录；新增evolution coding-status，历史多记录需明确submission-id。分别展示persisted_task_status与原工作流result，避免把后来提交完成状态与历史waiting_approval混淆。
- 无模型真实Temporal跨进程验收：独立Worker进程跑本地fixture activity，观察进程1读running后退出，Worker仍活着；释放fixture后原工作流完成，观察进程2读completed；历史只调度1个activity，Worker明确停止并以0退出。证据worker-observer-process-2下observation-evidence.json。
- 跨进程/观察/Worker/持久化提交回归34 passed in2.62s，Ruff通过。本轮真实模型请求0次、无Git提交。
- 新coding-status已在单独CLI进程只读查询既有购物车：持久化任务completed、原coding workflow仍是已保存的waiting_approval，这是不同阶段的事实，不回写历史。
- 范围限制：跨进程测试不是完整生产coding-worker执行模型任务；embedded入口仍未全部迁移。下一步应补生产编码Worker的独立提交/观察实际接线，再做执行反馈优化；不宣称P0全完成。

## 优化顺序启动：购物车提交完成与独立编码 Worker（2026-10-16）

- 用户授权按既定优先级优化。先用统一入口显式批准已审查购物车提案，经专用 Temporal commit Worker 应用；请求 e69844f1-05c6-5402-aeb0-01cfabeccf2e approved/accepted/applied，持久化 task_completed=true。
- 提交64db1f64498757952cf7c45bfe7296a46a5a3a5b，仅在隔离实验目标仓库；目标干净，原始仓库仍edc345d1b30cf59fe110b7b8be7c6bec4a2ff4f2且干净。没有当前项目提交/推送、没有成长发布、没有模型请求。commit-closure-report.json与原编码报告分开保留。
- 生命周期优化第一步：从 coding_execution 提取 assemble_coding_worker，共享现有镜像/计划/配置/队列绑定，不复制装配规则；新增正式CLI evolution coding-worker，使用原已接受路由，独立前台服务直至操作者停止。
- cli/coding_worker只编排：existing experiment_runtime 管存储装配，coding_run管绑定装配，isolated_worker_lifecycle管Worker与存储所有权。不调用execute/resume/approve，不改变持久化工作流或未知副作用，不启动通用Runtime。拒绝已完成任务启动Worker。
- 原embedded coding_execution仍保留并拥有其Worker；新增入口是可分离能力，不等于已迁移所有调用点。尚需独立结果观察接口、跨进程客户端结束/Worker继续执行的实测、正式入口配置与预算接线。
- Worker/共享装配/lifecycle/commit入口回归26 passed in0.56s，Ruff通过，实际新进程--help可见。当前未启动真实coding-worker以免触发未经单独授权的模型调用。
- 后续顺序保持：生命周期完整分离 → 有界执行反馈 → 正式入口验收 → 持久化预算与统一状态。未完成项不提前标记完成。

## 购物车真实多文件试验通过（2026-10-16）

- 新授权购物车最多20次请求、单次8192输出tokens；首次新实验8次请求后在32.43秒停止。持久化提交 accepted/uncertain/result-phase TimeoutError，AGENT 被 Worker 关闭取消；不是模型被独立验收判错，不重跑该未知节点。
- 根因是 Temporal 客户端固定等待结果30秒，试验把返回的 waiting_resource 当作结束而释放仍执行的 Worker。增加独立 result_wait_seconds 构造参数，普通默认30秒不变，验收入口显式240秒、外层270秒；不改变工作流命令序列/活动超时，不重提或本地回退。相关本地24 passed，另提交/codec/wait回归33 passed。
- 用剩余额度12在全新实验复测，实际8次、57.53秒通过；输入36448/输出4518 tokens。本轮两次累计16/20次，合计输入63666/输出7886 tokens；没有自动使用剩余4次。
- 真实修改 cart.py、discounts.py、checkout.py，新增 tests/test_generated.py；38项pytest通过（36生成测试实例+2原smoke）、20个独立JSON用例、PoC、重复调用/输入不变/模块组合检查全部通过，verified=true。
- 只读审查未见测试删除或硬编码验收样例，原smoke未改；有一项生成测试函数名声称below-threshold但实际测12000以上门槛，属命名/覆盖表达瑕疵；宿主独立9999边界用例已通过，不据此否定功能验收。
- 提案 ee9bf6b4-7f4a-40a8-b80d-d671a417d173 pending，任务3e0261d2-1b67-4257-86c0-61ccff37d076 waiting_approval。未Git应用、未completed、未发布成长候选；当前项目业务代码不含购物车功能。
- 证据 `.workbuddy/test-runs/deepseek-cart-live-2/test_real_deepseek_minimal_cod0/live-coding-report.json`；第一次失败证据独立保留于 deepseek-cart-live-1。底层策略仍为显式试验策略，不能外推生产权限全覆盖或复杂项目成功率。

## 购物车双样例接线完成（2026-10-16）

- 新增 acceptance_samples 固定开发考题注册；coding_acceptance run 支持 --sample addition/cart，显式转交样例、配置和请求额度，缺省仍为 addition。既有真实编码测试复用同一 Runtime/Temporal/Docker 链路，按样例生成独立输入和冻结 JSON 验收。
- 购物车重复调用、输入不变、非法输入 ValueError 和跨模块组合检查，以宿主定义的 Python 额外命令走既有 independent auxiliary export；不写入模型源码，不由模型自报通过。完整命令经执行图编译和标准解析保持一致，仅安装到 EVALUATION，不安装到 PLAN/AGENT 节点。
- 提交脚本按报告定位样例，但必须与持久化 instruction/required_paths/verification_commands 一致，缺性质检查即拒绝；加法历史报告保留默认兼容。购物车提交消息和四个预期文件独立于加法，不改正式业务策略。
- 真实 Docker 四场景 4 passed in 60.46s：缺陷版拒绝、参考版通过全部20个JSON用例及PoC、修改输入反例拒绝、内联绕过组合反例拒绝。另补 stateful 场景 1 passed in 1.39s，检出重复调用漂移。均无模型调用，只清理确认完成的自有停止容器。
- 双样例/考题/入口/隔离命令/提交 Worker 本地最终回归 44 passed in 0.60s，Ruff 通过，run --help 实际显示样例选项。
- 尚未执行购物车真实模型或 Git 提交闭环，不把开发集成通过当成真实多文件任务成功。下一步需明确购物车付费试验授权范围；本轮新增模型请求 0 次。

## 独立多文件购物车考题准备

- 新增 tests/cart_acceptance_fixture.py 保存三模块缺陷输入、固定需求及宿主侧 JSON 用例；购物车不进入 Sprout 业务层，参考解仅位于考题测试中，不交给待测模型。
- 金额整数分，满 10000 减 1500 一次；非法类型（含 bool/float）、非正数量、负单价、缺字段等有明确规则。3 个主验收 + 17 个回归用例符合现有 AcceptanceSpec。
- 本地检查缺陷版本三模块问题均可检出；参考解通过 20 个输入输出用例，并验证重复调用/输入不变/组合调用；6 passed in 0.09s，Ruff 通过。本轮付费请求 0 次。
- 注意参考性质检查不是对未来候选的检查；现有 coding_acceptance run/commit 仍固定加法样例，购物车尚未接线，更未真实运行。下一步需显式样例选择、对应文件审查及候选的性质检查，不默认扩大付费额度。
- 任务说明 Cart-Acceptance-Task.md；未创建购物车业务提交或发布成长候选。

## 可重复使用的开发验收入口

- 增加 `python -m Sprout.tests.coding_acceptance`：check（本地布尔配置检查）、run（显式剩余请求预算和全新证据目录，停在审查）、status（读数据库当前状态）、commit（必须 --approve-reviewed，复用既有显式试验提交服务）。
- 明确为源码/dev 依赖下的固定最小样例验收工具，不提升为生产 API，不把测试授权策略混入正式 Runtime，不自动从验证进入提交。
- run 支持显式配置文件转交，拒绝已有证据目录，子进程传递指定请求额度；不自动计算跨运行余额或追加额度，指南明确必须扣除历史消耗。
- 新入口/预算/提交 Worker 本地回归 25 passed；Ruff 通过。实际新进程 status 已读出原任务 completed/applied，命令帮助正常。本轮没有真实模型请求、没有再次 Git 提交。
- 尚未用新 wrapper 重新运行付费 run/commit 两阶段，不能据单元转发测试称该 wrapper 全链路已真实复验；底层既有真实编码和提交证据保留。
- 使用指南 `Coding-Acceptance-Guide.md`，包含逐阶段命令、预算/审批/状态证据区别及固定样例适用范围。

## 真实编码任务的审批与隔离 Git 提交闭环

- 用户明确继续推进已审查提案；沿用既有 Git 身份，没有写入 Git 配置。新增显式试验脚本 live_coding_commit，通过现有服务审查、普通代码审批、独立提交请求/决策、专用 Temporal 提交队列和 IsolatedCommitWorker 完成提交，无直接 Git/数据库状态绕过。
- 初次入口缺少提交角色预检，在审批/提交前被拒绝；补齐与 commit-request CLI 一致的 IsolatedCommitWorker 构造检查后继续，未重试任何未知 Git 副作用。
- 请求 9a8ee631-fe02-5c1c-991e-eb709f46670d approved/accepted/applied；真实结果 task_completed=true，持久化 commit-status 确认任务 completed。
- 结果提交 d58063dd382362ef2a93d6d517c0b5c43320f4c8，两个文件：app.py、test_generated.py；隔离目标仓库干净。原始实验仓库仍为 346c62e33132e8f1292b21127bf8011087c28443 且干净；当前项目仓库没有提交或推送。
- 本轮模型请求 0 次，未发布成长候选，validation_committed=false 是成长独立验证未提交，不否定已有编码验收及任务完成。
- 独立报告 `.workbuddy/test-runs/deepseek-live-coding-4/test_real_deepseek_minimal_cod0/commit-closure-report.json`；原编码报告保留其当时 waiting_approval 快照，不回写历史证据。
- 提交 Worker/提交 relay/决策 CLI 本地回归 19 passed in 5.63s，新增脚本 Ruff 通过。
- 证明范围：简单真实模型编码任务已完成“改代码→生成测试→独立验证→审查审批→隔离 Git 提交→任务完成”；没有证明复杂任务成功率、全部生产权限策略或真实成长收益。

## 优化后真实模型验收通过（20 次新授权）

- 经用户明确确认最多 20 次实际请求、单次最多 8192 输出 tokens，试验计数器支持显式 1..20；本地上限精确计数及提示回归 9 passed。
- 新隔离任务实际 7 次请求，输入 14520 / 输出 1198 tokens，测试 1 passed in 24.20s。无自动追加、未使用剩余额度继续运行其他任务，没有金额硬封顶承诺。
- 真实 DeepSeek → AgentLoop → Temporal 专用队列 → Docker 命令及导入 → 独立 acceptance / regression / PoC / pytest 全部通过；pytest 4 passed，生成正数/负数/零值三个测试函数，加上原 smoke 共四项。
- 代码审查：app.py 仅将 return 0 改为 return a+b；新增 test_generated.py，包含七个断言；原 test_smoke.py 未修改，原始实验仓库保持不变。变更提案 f3931f49-41a3-496f-b79e-f3964b08e2ff pending，任务 waiting_approval。
- 证据：`.workbuddy/test-runs/deepseek-live-coding-4/test_real_deepseek_minimal_cod0/live-coding-report.json`。Git 未应用、任务未 completed、未发布成长候选。试验使用明确的测试策略，不能外推为全部生产权限路径验收。
- 此结果证明当前简单真实编码链路到审查边界可用，但无相同预算和任务条件的优化前后 A/B，不能据 7 次请求声称某个固定节省比例或复杂项目成功率。

## 预算停止的 Temporal/SQLite 集成验证与提示精简

- 新增无付费模型的真实 Temporal 17233 集成测试：实际 AgentLoop 执行 echo 工具后抛 ModelBudgetExhausted，NodeExecutionGuard 持久化 WAITING/uncertain，真实 SproutExecutionWorkflow 返回 waiting_resource，历史中只调度一个 activity，后续 EVALUATION/APPLY 未调度。
- 实际历史保存并成功 Replayer 回放；重开 SQLite 后即使提供新 resume_key 也不重复运行节点，attempts=1、没有变更提案。此测试验证 workflow/agent/guard 边界，不宣称完整生产 Runtime 或真实模型任务验收。
- READ 资源内容本已传入 PLAN 和 AGENT。调整这两个阶段提示：优先用已提供的完整内容；缺失、截断或需确认新鲜度时才再次读取；计划保持精简，执行节点不再次询问是否开始。保留全部既有工具与审批，不删除规划节点，不改变历史回放调度序列。
- 真实 Temporal 集成及本地 agent/guard/prompt 回归合计 38 passed in 2.23s，新增文件及 nodes.py Ruff 通过。本轮真实模型请求 0 次；请求节省程度尚待以后另行授权的真实测量。

## 剩余额度复测与预算停止语义

- 新付费入口现在必须显式传入 SPROUT_LIVE_REQUEST_LIMIT，不再隐式获得 10 次额度。本次指定 4，实际请求 4，输入 6145 / 输出 1103 tokens；第二轮授权累计 6+4=10 已用尽。
- 新实验 `.workbuddy/test-runs/deepseek-live-coding-3/test_real_deepseek_minimal_cod0/live-coding-report.json`：模型仍在文件读取阶段时额度耗尽，未完成修改/验证、无变更提案、Git 应用或发布。因此不将此结果归因为缓存修复失败，也不声称真实链路验收通过。
- 定位到 AgentLoop 对工具结果后的普通模型异常生成降级摘要，会将明确的预算停止吞成节点输出；新增供应商无关 ModelBudgetExhausted，AgentLoop.chat 调用边界直接抛出，ModelOrchestrator.chat 不重试/不备用切换，试验 LiveRequestLimit 继承该类型。
- 本地预算/调度/AgentLoop 回归 31 passed，Ruff 通过；另有文件策略/缓存/预算回归 25 passed。本轮本地修复后无新增真实请求。通用模型故障的既有降级摘要行为保留。
- 尚未补验修复后预算耗尽的完整 Temporal 状态链路；已有历史报告不回写。后续真实试验须另行取得额度授权，不自动重复付费运行。

## 生成缓存修复的真实 Docker 集成验收

- 扩展已有 test_coding_docker_integration 为三个场景：容器直接修改；容器修改并执行 pytest；先由 SandboxWriteTool/FileBroker 写函数和测试再由容器执行 pytest。
- 使用已有固定镜像、真实 SQLite 节点占用、CodingNodeSession、DockerCodingBackend 和 import_artifacts；无模型替身伪造进程成功结果，无真实 API 请求。
- pytest 默认生成 .pytest_cache，保留原导出缓存作为证据但不导入目标；验证新增测试和代码正确落地，导入占用 completed，模型文件工具先写场景的容器变更列表为空也正确完成。
- 三个场景均继续执行真实隔离 acceptance / regression / PoC，verified=true，但 validation_committed=false；冻结 oracle 和原始目录保持不变。
- 实际结果：3 passed in 17.00s，Ruff 通过；本地 artifacts/import/session 22 passed。只清理每项测试记录中确认 completed 且停止的自有容器，保留证据文件。
- 尚未进行缓存修复后的真实模型/Temporal 全链路复测，未恢复此前 execution_result_unconfirmed 任务，未进行 Git 应用或成长发布；第二轮剩余 4 次模型请求额度不变。

## 第二轮真实编码与生成缓存修复

- 新授权第二轮最多 10 次请求，实际 6 次，输入 13276 / 输出 1919 tokens；没有追加付费调用。第一轮消耗不重置。
- 模型成功将 add 修为 a+b，创建 test_generated.py，包含正数/负数/零值三个测试函数，现有 smoke 共四项测试。
- 原任务 waiting_resource，原因 execution_result_unconfirmed，无提案、Git 应用或成长发布。对原导出证据只读重建 artifact_plan，准确复现 Runtime/private artifact directory forbidden；pytest 生成的 .pytest_cache 导致导入规划失败。
- coding_artifacts 现在仅排除新生成的 .pytest_cache/.ruff_cache/__pycache__，不删除证据、不导入缓存；仍扫描链接及体积、拒绝私有根/冻结路径、拒绝缓存出现在可信基线。不放宽 .git/.env 规则。
- 本地相关回归 36 passed，Ruff 通过；对原模型生成文件以只读挂载、断网 Docker、独立 /tmp 内存盘执行 pytest，4 passed in 0.03s。该复核不是原任务验收，不覆盖其未知状态、不恢复或重跑原任务。
- 报告：`.workbuddy/test-runs/deepseek-live-coding-2/test_real_deepseek_minimal_cod0/live-coding-report.json`。尚未证明修复后的完整新任务通过，需要先补无付费集成验收；第二轮剩余 4 次请求，不以新入口重置额度。

## 真实模型工具授权缺口定位及本地修复

- 已通过实际 ToolExecutor 复现：旧 CodingFixturePolicy 对 sandbox 文件工具的 TOOL_INVOKE 返回 SANDBOX_ONLY，而调度层仅接受 ALLOW/REQUIRE_APPROVAL，因此文件工具未到达路径保护或 FileBroker 就被拒绝。此前脚本模型只调用隔离命令，没有覆盖该路径。
- 增加仅用于显式试验的 LiveCodingPolicy，对列举的六个模型文件工具允许调度；仍保留节点 scoped_only、IsolatedModelPathGuard 和 FileBroker 边界。不修改生产策略、不允许任意 shell、不改变冻结验收规则。
- 保留现有提示契约：通过 sandbox 工具改代码，通过 isolated_coding_command 运行测试；本次未发现需要改为命令写文件的证据。
- 47 passed / 1 skipped（付费入口默认跳过）；包含旧策略失败复现、实际执行器读写及新建测试、冻结/私有/越界路径拒绝、未知工具拒绝、请求预算与隔离命令回归。本轮新增真实请求 0 次。
- 此修复尚未真实模型复测，也不代表完整生产策略验收。此前 9 次请求仍计入原 10 次授权，不能启动新一轮 10 次调用来重置额度。

- 连通探针通过：2 次请求，输入 313 / 输出 53 tokens。
- 新增显式启用的真实编码试验入口及独立请求预算包装器；最多 10 次请求，失败同样计数，并发不能突破上限；默认不运行付费测试。
- 本次真实编码试验实际 9 次请求，成功响应统计输入 18179 / 输出 1845 tokens；单次输出上限 8192，未自动追加试验。没有金额硬封顶。
- Docker、固定编码镜像和专用 Temporal 17233 可用；真实任务到达独立验证，但验证失败，最终 waiting_resource，无变更提案、Git 应用或成长发布。
- 模型报告 sandbox 读写工具被拒绝，现有脚本模型只调用隔离命令，未暴露此路径。已确认试验执行器只显式允许 isolated_coding_command/skill_view，不能据此次失败断言 DeepSeek 不具备编码能力；后续须核对工具授权与隔离编码提示契约。
- 本地模拟回归 23 passed；新增入口默认检查 7 passed / 1 skipped；新增文件 Ruff 通过。
- 证据：`.workbuddy/test-runs/deepseek-live-coding-1/test_real_deepseek_minimal_cod0/live-coding-report.json`。保留失败证据，不用新实验重置已消耗授权。

