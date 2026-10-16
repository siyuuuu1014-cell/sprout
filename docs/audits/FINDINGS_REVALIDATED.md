# F01–F23 当前源码复核

日期：2026-10-16；基线：`dev@762eaf3c62476d2377302329151a759c3d297baa`。
路径以仓库根目录为起点；行号指当前基线。`confirmed` 表示代码行为或缺口成立，**不自动表示公开入口已被成功利用**；`partially_confirmed` 表示已有部分保护或历史表述过宽。动态未执行部分逐项标明。

汇总：**17 confirmed，6 partially_confirmed；0 项可整体认定 already_fixed**。不能因为 SHA 相同就跳过重审，也不能因为没有完整动态环境就否认静态可见分支。F06/F07/F10/F13/F15/F23 中已有实现或历史结论的偏差必须保留/纠正。

## 逐项结论

| ID / 判定 | 真实源码证据与调用链 | 本轮证据 / 后续最小验收 |
|---|---|---|
| F01 P0 confirmed | `src/Sprout/runtime/runtime.py:1316` 优先读取 metadata.intent/intent_name，1324 标为 metadata override；`_recognize_intent:1257` 再进入路由。`gateway/runtime_gateway.py:70` 把 Message 交 Runtime.handle；与 `execute_message:48` 丢弃伪造角色的保护不是同一合同。 | 静态确认；未做公开入口攻击。补 Web/MCP/SDK 伪造 intent、trigger_event、roles 的负例；TrustedIntent 必须为服务端产物，而非模型/用户字段。 |
| F02 P0 confirmed | `src/Sprout/intents.py:86` detect_task_hint，末尾关键词子串匹配；只处理有限的能力问句，没有否定/引用识别。 | 纯函数实测“不要修复登录问题，只分析原因”返回 True。现有 intents 测试入选 88 项（排除 async 提交例）；补否定、引用、历史状态、歧义不创建写任务的断言。 |
| F03 P0 confirmed | `src/Sprout/tools/registry.py:192` 注册下载、195 注册 CliRun、200/201 注册 Git 工具，未注入相应 Broker；`system_tools.py:443` 有 Broker 才转交，否则 448 直接执行。 | 静态确认；**外层 ToolExecutor 策略/审批仍存在**，不是“所有动作无需审批”。补所有直接进程/网络/文件/Skill/MCP I/O 清单和缺 Broker 拒绝测试，区分 trusted_local 与 restricted。 |
| F04 P0 confirmed | `src/Sprout/tools/system_tools.py:854` 只为 add/commit/pull 使用 GitBroker；889 fetch、899 push、914 checkout 等直接 `_run_git`，996 封装宿主命令。 | 未运行 Git 写动作。补各 operation 的 Broker 委派、remote/ref/Hook/凭证约束测试；不能只补 add/commit。 |
| F05 P0 confirmed | `src/Sprout/task/models.py:114` permits 的 121/123 使用 startswith；187/190 的 scope 相交也用字符串前缀。 | 纯函数实测 allowed_paths=('/repo',) 允许 '/repo-backup/a'。不能据此断言所有 FileBroker 都可逃逸：底层另有 path guard。补目录边界、Windows 盘符/大小写、symlink/junction、交集为空和资源打开时复验。 |
| F06 P1 partially_confirmed | `src/Sprout/execution/apply.py:73` apply → 95 git apply --check → 105 读取当时 HEAD → 应用/暂存/提交；没有与验收时预期 HEAD/patch 指纹做统一 CAS。**已有** `runtime/changes.py:149` verification blocker、批准及强制原因约束；`execution/isolated_git_evidence.py:41` 和 coordinator:51 有隔离专用证据核对。 | `test_isolated_apply_boundary.py`、verification_report 入选通过。普通 Apply 的 HEAD/patch/spec 变更拒绝与并发 CAS 未动态验证；统一合同，复用隔离证据，不新建第二条 Apply。 |
| F07 P1 partially_confirmed | `src/Sprout/orchestration/execution_guard.py:39` 已有持久占用和可信完成缓存，75 异常进入 uncertain，95 `_cached` 拒绝未知重复执行；`execution/isolated_git_coordinator.py:118` 同样持久化 uncertain。 | node_execution_guard、temporal_submission 入选通过。不是“没有恢复机制”；缺口是逐效果可核对凭证和受控人工 resolve 的完整覆盖。真实 Worker 崩溃/效果丢响应未测，未知不可超时自动重跑。 |
| F08 P1 confirmed | `src/Sprout/mcp/adapter/tool.py:36` adapter 默认 medium；41 构造 spec，68 invoke 转远程 call_tool；`mcp/client/connection.py` 负责 stdio/网络连接，不能提供远端副作用证明。 | 静态确认，MCP 未运行。补 server/operation 风险上界、可信配置、启动身份、超时 UNKNOWN；现有外层 ToolExecutor 保留。高危拒绝归入 R04，不能等 P3。 |
| F09 P1 confirmed | `web/webapi/app.py:102` lifespan；initialize/startup/start 在 try/yield 之前；110 stop、112 on_shutdown、113 close 顺序关闭，前项抛错跳过后项。 | 静态确认，未做故障注入。新增 start/stop/callback 各阶段抛错、资源必关闭及错误可观测测试。 |
| F10 P1 partially_confirmed | `src/Sprout/sandbox/git_worktree.py:157` run_code 在 176 调 run_capture，cwd 为工作树；环境白名单/超时不等于 OS 隔离。现有 restricted Docker 编码链路不能与该宿主路径混为一谈。 | 未执行目标代码或容器。保留明确授权的 trusted_local；逐入口验证 restricted 缺镜像/启动失败不走 host。不是重写已有 DockerCodingBackend。 |
| F11 P1 confirmed | 当前 Git 树无 `.github/workflows`；`pyproject.toml:48` 有 pytest/ruff 开发依赖，uv.lock 已存在。`tests/test_bubble_sort.py:21`、`test_hello_world.py:20` 引入缺失示例模块。 | lock、lint 通过；全量 collect exit1，2280 collected/2 errors；88 项通过。未 fresh sync/build；无远端 CI 结果。修 fixture/测试归属而非静默 ignore。 |
| F12 P2 confirmed | `src/Sprout/llm/registry.py:12` 仅 provider 注册/默认选择，没有明确 ModelCapabilities 契约。已有模型配置和 `llm/task_budget.py`、client 的持久预算，不是从零缺少预算。 | agent_budget 入选通过；真实 Provider tool/stream/thinking 特性未测。对现有注册接口加兼容能力声明和 fail-fast，不复制模型调度层。 |
| F13 P2 partially_confirmed | `src/Sprout/context/retrieval.py:14` KnowledgeRetriever 走关键词知识查询；`workspace/graph.py:49` 已有源码/前端关系构建；已有 workspace 上下文和项目存储。未见完整统一 CodeHit/精确符号+FTS+RRF 合同。 | 未跑检索 benchmark。历史“无混合合同”成立，不应扩写成“无图/无索引”。先验证现有快照/图/知识能力，再补统一召回和离线基准。 |
| F14 P2 confirmed | `src/Sprout/task/models.py:19` TaskStatus，21 DISCOVERING、23 READY；v1 第12章状态文字没有完整列出。 | 文档偏差，不修改/重命名既有状态值。R01 补完整状态投影矩阵，Local/Temporal/前端序列化另测。 |
| F15 P2 partially_confirmed | `tests/test_coding_graph_docker_integration.py:47` 显式 image gate；但还有 `test_gateway_process_integration.py:26`、`test_isolated_coding_temporal_integration.py:106`、`test_detached_coding_cli_integration.py` 等更广集成。 | “所有集成都只是后端 fixture”不成立；“真实闭环未证实”仍成立。**本轮全未运行**。标明 scripted/echo/live、Local/Temporal、正常策略/降级策略分别覆盖什么，不因测试文件存在宣称通过。 |
| F16 P0 confirmed | `src/Sprout/context/context.py:36` `_skill_visible` 查 trust/activation；70 visible_skills 使用它；83 skill_instructions 只查 enabled；`agent/loop.py:27` eager 把正文作为 system。 | G01：untrusted，visible_count=0，却 eager_injected=True；G02：trusted 但缺 required toolset，同结果。两项内存探针已复现；现有 skills_disclosure 测试虽通过仍缺负例。首个 R00 修复。 |
| F17 P1 confirmed | `src/Sprout/agent/loop.py:121`、337 写 self._last_plan；166/197/235/267/294、stream 392/434 读取；`runtime/factory.py:143` 注册共享 AgentLoop。异步挂起期间实例态可被覆盖。 | 静态竞态确认；未执行交错并发探针，不声称已观察串线。用屏障控制 A/B planner/model，断言 run/run、run/stream、stream/stream 的计划和账本隔离。 |
| F18 P1 confirmed | `src/Sprout/agent/executor.py:79` detailed 只执行前8；loop:481 调 detailed，486 从 outcomes 构造消息，494 对全部 allowed_calls 计数。451 附近 executor=None 也只给首个 ID 返回。 | 静态确认。预算3路径已有 skipped 消息，不可笼统说预算裁剪全丢；缺陷是 per-step cap 与禁用工具路径。新增9 ID/8执行/9结果、0执行/9拒绝、预算3/9结果测试。 |
| F19 P1 confirmed | `src/Sprout/agent/loop.py:291` 截断标记；`runtime/nodes.py:438` 返回字典只保留 content/node/resources/approval/pending_calls，没有 truncated/usage/stop reason。 | agent_budget 测试通过不证明 Node 投影正确。补预算耗尽→Node→任务/提案的测试，禁止自动解释为编码完成；兼容旧 AgentResult。 |
| F20 P1 confirmed | `src/Sprout/agent/loop.py:328` 后为单独 stream 路径；与 run 的 usage/StepStore/invoke_model 和结构化终态不对称。 | 静态确认；未运行真实流式模型。统一内部 step 状态机，保持现有流消费者兼容；scripted run/stream 最终预算/审批/步骤/停止原因对照。 |
| F21 P1 confirmed | `src/Sprout/gateway/runtime_gateway.py:70` handle_message 将 session_id 放 task_id（73），固定 completed（74）。**execute:38** 正确投影 TaskResult；`gateway/task_queries.py:19` 已有归属检查和 report/observe。 | 未跑该入口动态探针；静态确定映射错误。修 ACK 路径而非重做 TaskQueries/Actions；加排队/等待/无Task/真实完成及旧客户端兼容测试。 |
| F22 P1 confirmed | `src/Sprout/agent/executor.py:71` 只传 requested_by/session_id；`tools/executor.py:78/80` 默认 task_id空/source interactive，121 构造 ActionRequest；`security/access.py:52` 已有权威请求类型。 | **局部已实现**：`tools/context_keys.py:39` NodeToolInvocationContext，executor:91 绑定节点上下文恢复部分链路。通用路径仍丢来源/身份；未做所有入口动态追踪。复用既有 context，不让模型 JSON 补身份。 |
| F23 P1 partially_confirmed | `src/Sprout/runtime/runtime.py:1417` 的提示集1449包括 strategy；`intents.py:23` 七类没有 strategy。但是 **runtime.py:117 的本地 _INTENT_EVENTS 在125明确包括 strategy**，1260检查的是这个本地表，不是七类枚举。 | normalize_intent('strategy') 的确实测为 conversation，但**不能据此说 Runtime._recognize_intent 会丢弃 strategy**；该历史因果结论未复现，静态源码反证。剩余问题是重复权威表/不同调用者语义漂移。完整模型和事件链未测；R02统一兼容合同而不是重复“修复不存在的运行时回退”。 |

## 证据纠偏与待动态确认项

1. F22 虽有局部修复，通用路径的原问题仍可定位，所以记 confirmed；F06/F07/F10/F13/F15 的历史整体描述须收窄，记 partially_confirmed。F23 的 Runtime 回退推论与当前本地表不符，只保留枚举/映射不一致这一部分。
2. 当前没有可整体记 already_fixed 的编号，但 **verification blocker、隔离证据绑定、非过期执行占用、只读执行类型、服务端角色丢弃、持久预算、任务查询/动作 facade、真实集成测试** 均为已有资产，后续不得重复实现。
3. F01 的公开可达性/实际权限提升，F03/F04 的逐动作策略效果，F06 并发 Apply，F07 故障恢复，F09 关闭异常，F10 容器边界，F17 并发，F20 stream、F21 全入口兼容均需动态测试。结论“代码缺口成立”与“完整攻击/故障轨迹已验证”分开。
4. 探针只有固定字符串和内存对象：G01/G02 直接构造 AgentContext/Skill 并调用 `_skill_message`；F02 调 detect_task_hint；F05 调 DelegationScope.permits；F23 调 normalize_intent。没有将第三方 Skill 作为本轮指令执行。
5. 不根据原对话中历史通过数字、已删除报告、模型自述或 skipped 测试推定当前验收通过。准确命令和覆盖边界见 [基线](CODEX_BASELINE.md)。

## 第一项修复提案（尚未实施）

R00：先在 `src/Sprout/tests/test_skills_disclosure.py` 添加 G01/G02 失败用例和 eager/progressive 同资格参数化测试，再让 `context/context.py:83` 共用 visible_skills。审查 `agent/loop.py:27` 的两个调用路径，不改变公开接口、Skill schema、批准/信任存储；仅必要时修改 loop。覆盖 untrusted/quarantined/rejected/disabled、requires 不满足、fallback 命中、trusted 正常显示；同时检查模型 system messages。运行相关 skills 与 context 回归，报告实际数字后停下。没有数据库迁移，单 PR 可逆；回滚时禁用 eager，而不是恢复不可信注入作为运行方案。
