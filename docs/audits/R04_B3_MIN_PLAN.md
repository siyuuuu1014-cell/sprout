# R04-B3-min：最小执行入口封闭方案（仅审查，未实施）

日期：2026-10-21。分支 `codex/r04-broker-zero-bypass`；HEAD `8750ba55224222c2fb26799941905ba79e62c0f5`。
前置决策：[最小能力分类](R04_B_MINIMUM_CAPABILITIES.md)已接受：公开 P0=0 /P1=7 /P2=24；内部必要 Git 保留。
本轮只新增本文和独立红色回归 `src/Sprout/tests/test_r04_b3_min_closure.py`，没有修改生产、原测试、策略或审批。
本方案需要单独批准，**不是 B3-min 生产补丁，也不是通用 Git 已完成封闭的声明**。

## 1. 当前调用图与可信边界

```text
普通模型工具调用 / ToolExecutor.execute
  → 服务端 registry / node overlay 解析工具对象
  → SecurityPolicy(HIGH) + LayeredPolicyEngine → audit
  → ApprovalManager 请求/消费 git_write 的审批
  → GitWriteTool.invoke
      add/commit/pull → 注入的 GitBroker → PolicyEngine/可选旧审批
                    → GitBroker._run → run_capture("git", ...) → 子进程边界
      其余18项 → B1 拒绝（但外层审批已经发生）

直接 Python / 可信插件调用 GitWriteTool.invoke
  → 绕过 ToolExecutor 的外层提示，但仍有上述工具/Broker 判断

直接 Python 嵌入 GitBroker.log/branch/add/commit/push/pull
  → 不需要经过 GitWriteTool/ToolExecutor
  → 自定义 Policy ALLOW，或适用的 REQUIRE_APPROVAL + 已批准记录
  → 原 _run → run_capture("git", ...)

GitInspectTool.status/diff → GitBroker Policy → B2 S1.run_read
  → 无生产安装/视图签发器 → 拒绝，零启动
GitInspectTool.log/branch → GitBroker 原方法（默认 DENY，自定义 ALLOW 可达）
其余6项 Inspect → B1 拒绝
```

这里的“可达”有静态调用及 Mock 最终 I/O 证据。没有证明远端未认证用户可配置 Policy、直接执行任意 Python 或任意批准历史记录。
直接 Python/插件处于嵌入宿主信任边界；公开 Broker 仍不能把 ALLOW 或旧审批误当成当前缺失的安全执行合同。
未对不可见的外部插件/SDK 客户端作使用量或攻击面穷尽证明。

### 精确源码依据

| 入口 | 当前源码 | 已观察行为 |
|---|---|---|
| Runtime 装配 | [runtime/runtime.py:396](../../src/Sprout/runtime/runtime.py#L396)、[421](../../src/Sprout/runtime/runtime.py#L421) | 创建 GitBroker，注入两个通用工具；不是缺注入问题 |
| 通用 Write | [tools/system_tools.py:807](../../src/Sprout/tools/system_tools.py#L807)、[813](../../src/Sprout/tools/system_tools.py#L813) | risk=high；保留21项 Schema；缺 Broker 拒绝，add/commit/pull 委派，其余拒绝 |
| 外层审批顺序 | [tools/executor.py:108](../../src/Sprout/tools/executor.py#L108)、[145](../../src/Sprout/tools/executor.py#L145)、[152](../../src/Sprout/tools/executor.py#L152)、[179](../../src/Sprout/tools/executor.py#L179)、[215](../../src/Sprout/tools/executor.py#L215) | 策略及 audit 后先取审批，再 invoke；工具自身的不可用拒绝太晚 |
| 风险合同 | [security/policy.py:57](../../src/Sprout/security/policy.py#L57)、[70](../../src/Sprout/security/policy.py#L70) | HIGH 需审批；always_allow 可覆盖风险层。仅调整 risk 不构成不可恢复的能力封闭 |
| Broker 普通读写 | [execution/git_broker.py:59](../../src/Sprout/execution/git_broker.py#L59)、[68](../../src/Sprout/execution/git_broker.py#L68)、[77](../../src/Sprout/execution/git_broker.py#L77)、[85](../../src/Sprout/execution/git_broker.py#L85)、[113](../../src/Sprout/execution/git_broker.py#L113)、[138](../../src/Sprout/execution/git_broker.py#L138) | 六项非 S1 公开方法仍可执行 |
| 实际执行叶子 | [execution/git_broker.py:187](../../src/Sprout/execution/git_broker.py#L187) | 裸 git + 共享 git_args/git_env → run_capture；没有 S1 安装/视图证明 |
| 默认矩阵 | [security/engine.py:118](../../src/Sprout/security/engine.py#L118)、[126](../../src/Sprout/security/engine.py#L126) | status/diff 策略 ALLOW 不等于 S1 可用；commit/push REQUIRE_APPROVAL；其他 Git 动作默认 DENY |
| 导出 API | [execution/__init__.py:7](../../src/Sprout/execution/__init__.py#L7)、[35](../../src/Sprout/execution/__init__.py#L35) | GitBroker 可由外部 Python 导入；只封工具不足以封直接 API |
| S1 默认拒绝 | [execution/git_s1.py:56](../../src/Sprout/execution/git_s1.py#L56)、[62](../../src/Sprout/execution/git_s1.py#L62) | 两个 resolver 仍返回 None，本轮不启用 |

## 2. 当前到达最终 Git runner 的条件

以下均是**当前**行为，不是安全认可；本轮实际进程全部 Mock。

| 操作/入口 | 默认策略 | 可以到达原 runner 的条件 | 模拟的最终调用数/形态 |
|---|---|---|---|
| GitWriteTool.add | DENY | 注入 Broker 的自定义 Policy=ALLOW | 1 次 add |
| GitWriteTool.commit | REQUIRE_APPROVAL | 自定义 ALLOW；或可匹配该 Broker 参数/任务的记录 | 2 次 add + commit；工具未完整传递身份等旧问题仍存在 |
| GitWriteTool.pull | DENY | 自定义 ALLOW，或自定义 REQUIRE_APPROVAL 并有适用记录 | 1 次 pull |
| GitWriteTool 其他18项 | 工具自身拒绝 | 无 B1 宿主回退 | 0；外层仍可能先请求/消费审批 |
| GitBroker.log | DENY | 直接 API + 自定义 ALLOW | 1 次 log |
| GitBroker.branch | DENY | 直接 API + 自定义 ALLOW | 1 次 branch --all |
| GitBroker.add | DENY | 直接 API + 自定义 ALLOW | 1 次 add |
| GitBroker.commit | REQUIRE_APPROVAL | 自定义 ALLOW；或默认矩阵 + 真实已批准旧 git_write 记录 | 2 次 add + commit |
| GitBroker.push | REQUIRE_APPROVAL | 自定义 ALLOW；或默认矩阵 + 真实已批准旧 git_write 记录 | 1 次 push；不因工具 push 被拒绝而消失 |
| GitBroker.pull | DENY | 自定义 ALLOW；或自定义 REQUIRE_APPROVAL + 旧记录 | 1 次 pull；本轮没有声称默认矩阵放行 |
| GitBroker.status/diff | Policy ALLOW | 当前无生产 S1 签发器 | 0，明确拒绝 |

旧记录复现使用真实 ApprovalManager + MemoryOperationalStore，以对应参数和 task_id 创建/批准记录；覆盖 single_use=True/False。
这证明“现有审批可以越过**拟议的能力退役边界**”，不是伪造审批，也不把测试创建的记录称为实际用户历史数据。
普通 ToolExecutor 的批准会消费完整工具参数记录；它与内层 Broker 的参数子集/缺失身份并不一致。
因此默认 Broker 下“批准后仍拒绝”与直接 Broker 使用匹配记录“可以执行”可以同时成立，不能混为一个结果。

## 3. 审批体验问题的复现

`GitWriteTool.spec.risk_level="high"`，外层在知道工具不可执行之前就请求审批。
新增测试逐一覆盖21项，先真实创建审批记录，再在内存中模拟人工批准并续调。

- 21 项都出现外层 approval_id，违反“退役能力直接拒绝、不要求无意义批准”的拟议合同。
- 18 项工具不支持操作在批准后拒绝且零 Git runner 调用。
- add/commit/pull 在用于分离外层体验的自定义 ALLOW Broker 下，批准后到达 Mock Git runner。
- 这不是说默认安全策略对所有三项都会执行；默认 add/pull DENY 和 commit 审批错配仍然存在。
- 不应通过把 risk 从 high 降为 low、强行 always_allow、跳过 Policy 或单纯隐藏提示解决。

## 4. 内部消费者：必须保留的独立链

```text
Task → NodeExecutor._sandbox / _approval
  → GitWorktreeSandbox.create / diff / changed_files
  → ChangeProposalService（原审批、验证来源与适用性）
  → ApplyBroker.apply / rollback → ApplyBroker._git

隔离实验 → 已有 plan / evidence / exact approval / durable claim
  → isolated_git_coordinator → isolated_git_writer
```

| 消费者 | 源码证据 | 对通用 GitBroker 六项的必要依赖 |
|---|---|---|
| Node/编码图 | [runtime/nodes.py:714](../../src/Sprout/runtime/nodes.py#L714)、[1029](../../src/Sprout/runtime/nodes.py#L1029)、[1153](../../src/Sprout/runtime/nodes.py#L1153) | 无固定依赖；使用独立 sandbox 和 changes/apply |
| Apply/Proposal | [runtime/changes.py:136](../../src/Sprout/runtime/changes.py#L136)、[213](../../src/Sprout/runtime/changes.py#L213)；[execution/apply.py:73](../../src/Sprout/execution/apply.py#L73)、[158](../../src/Sprout/execution/apply.py#L158) | 无；审批主体为 git.commit + proposal_id/risk，不是通用 git_write 参数 |
| Sandbox/GitWorktree | [sandbox/git_worktree.py:54](../../src/Sprout/sandbox/git_worktree.py#L54)、[76](../../src/Sprout/sandbox/git_worktree.py#L76)、[113](../../src/Sprout/sandbox/git_worktree.py#L113) | 无；自身 _run/run_capture 路径 |
| SandboxGitTool | [execution/sandbox_tool.py:647](../../src/Sprout/execution/sandbox_tool.py#L647) | 无；另一工具名和独立执行路径，isolated 模式有宿主拒绝 |
| 精确实验提交 | [execution/isolated_git_coordinator.py:75](../../src/Sprout/execution/isolated_git_coordinator.py#L75)、[111](../../src/Sprout/execution/isolated_git_coordinator.py#L111)；[isolated_git_writer.py:16](../../src/Sprout/execution/isolated_git_writer.py#L16) | 无；不能删掉其持久化 claim/精确审批，也不能用通用 Broker 替换它 |
| CLI/Web/MCP/RPC/主 SDK | [最小能力审查第5节](R04_B_MINIMUM_CAPABILITIES.md#5-cli--web--mcp--sdk-的入口结论) 的具体源码 | 项目任务、Proposal 展示/Apply 不是通用 git_write 的硬依赖；外部嵌入者可能直接使用 GitBroker，需要兼容告知 |
| Runtime/插件 | Runtime 只装配通用工具；源码中未发现不可替代的内部业务直调；GitBroker 的公共导出仍存在 | 不能排除外部插件依赖；这属于能力退役的显式兼容性改变，不是继续暴露不安全 runner 的理由 |

新的三个内部 Mock 对照分别验证：

1. ApplyBroker 无批准被拒绝；真实内存 git.commit 批准后，Apply→精确路径 add→commit；rollback 只针对记录 commit 执行 revert。
2. ChangeProposalService 的人工批准仍能成立，但缺少可信验收证据时不能普通 Apply，验证门禁未被测试绕过。
3. GitWorktreeSandbox.diff / changed_files 走自己的 add -N / diff / status，不调用通用 GitBroker。

上述只是路由/门禁 Mock 证据，**不是**完整普通任务、真实提交一致性、OS 隔离或原生 rollback 验收。
Apply/Sandbox 当前仍有共享配置、Hook/filter、并发/UNKNOWN 等独立风险，不能因通用入口关闭而标记解决。

## 5. 推荐的最小生产补丁范围（等待单独授权）

| 文件 | 最小变更建议 | 不允许顺带改变 |
|---|---|---|
| `src/Sprout/tools/system_tools.py` | 仅 GitWriteTool：21项合法 operation 一律明确拒绝，不调用任何注入 Broker；保留名称、Schema、high risk 及无 Broker fail-closed | 不改 GitInspectTool 的 B1/B2 路由；不恢复 fallback；不改 CliRunTool/A2 交接 |
| `src/Sprout/execution/git_broker.py` | log/branch/add/commit/push/pull 保留兼容签名，但在任何 ALLOW/旧审批情况下拒绝；不查询或消费旧批准以恢复执行；保留 DENY 优先与既有审计合同。停用原 `_run` 宿主执行叶子，避免后续误复用 | status/diff 继续 S1；不改 PolicyEngine、ApprovalManager、共享 git_env/proc；不切断其他类的独立 Git |
| `src/Sprout/tools/executor.py` | **为消除无意义审批而必需的第三处**：服务端解析出实际退役的 GitWriteTool 后，在请求/消费审批和 invoke 之前返回受控拒绝；维持策略 DENY 及拒绝审计 | 不添加由 arguments/metadata 指定的 disabled/trusted 布尔合同；不降低风险、不做通用审批框架、不影响其他工具 |

建议外层使用可信工具实现类型识别（例如针对服务端注册的确切 GitWriteTool），而不是让模型提供 unavailable 标志。
须在实现审查中明确子类/自定义插件行为：至少不能因此恢复工具自身和 Broker 的执行；不能假定所有同名外部插件都是该内置类。
判定位置应保留原 Policy 层 DENY 和 audit/TOOL_DENIED，不把一次 Policy ALLOW 日志误报成最终执行允许。
B3-min 关闭之后，即使外层配置 always_allow、调用者伪造 context，内层工具/Broker 仍拒绝。
接口字段、scope 参数和兼容常量可保留，不代表功能启用；停用 `_run` 时需审查旧 Mock spy 接口，不能靠删除观察点掩盖执行。

**为什么不是只改两个文件：** 工具内部拒绝发生在 ToolExecutor 已经申请/消费审批之后。只关工具/Broker 可以阻止执行，但不能满足本轮明确提出的审批体验目标。
**为什么不改全局 GIT_COMMIT 策略：** ApplyBroker 也使用它；全局 DENY 会误伤原 Proposal Apply。应关闭通用能力，而非关闭同名内部动作。

## 6. 现有测试的逐项处理建议（现在未修改）

| 文件/用例 | 后续获批后的建议 | 必须保留的安全断言 |
|---|---|---|
| 新 `test_r04_b3_min_closure.py` 全部68项 | 作为生产修复目标，39红转绿；三个内部对照不得变红 | 零通用 Git/宿主调用、无无意义审批、旧记录不消费、内部门禁与路由保留 |
| `test_r04_b1_git_no_fallback.py::test_supported_delegates_once_without_retry` | Inspect 四项继续委派；Write 三项改为能力退役拒绝，不再期待调用 Broker/抛下层中断 | Write 零调用更强；不能删掉 Inspect 的拒绝/异常/取消不回退 |
| 同文件 `test_missing_or_noncallable_capability_refuses` | Write 的具体理由改成退役，而不是缺方法；Inspect 保留能力检查 | 无 Broker、无方法均零宿主；不通过删操作枚举减少覆盖 |
| 同文件无 Broker31项、未支持24项、Schema/静态零回退检查 | 保留 | operation 名称、31项、零 fallback 仍有效 |
| `test_r04_broker_audit.py::test_every_git_write_operation_obeys_injected_broker` | 原21项期待 broker.calls==1 改按全部拒绝/零委派新合同单独审查；原始版本保留为历史证据 | not result.ok 与零 host 不能削弱；Inspect 仍按 B1 的支持/未支持区别 |
| `test_r04_b_git_design.py::test_genuine_outer_commit_approval_has_one_prompt_and_bound_identity` | 通用 commit 退役后应测不请求/不消费审批；不能拿它要求复活 runner | Proposal/隔离事务的合法批准体验由独立测试保留，不改其身份/单次消费 |
| 同文件 filter/pull 参数丢失、scope、unattended、operand、remote/ref 等探针 | 没有执行能力的通用入口改按零执行拒绝归类；原失败记录保留；若任何路径仍保留执行则原安全约束仍须真正实现 | 不能用“P2”标签替代实际拒绝，也不能以不再执行为由删除内部 Scope 测试 |
| 同文件 mutating timeout UNKNOWN | 关闭通用入口后不应再达到 timeout；现有生产可达性和历史证据不改写 | 内部 Apply/事务的未知结果风险仍需其自己的故障注入，不改成安全失败 |
| 旧 status/diff timeout/textconv 探针；B2 专项测试 | S1 仍未签发；B2 的默认拒绝和私有 runner Mock 不变 | 不启用 resolver 为旧探针制造调用，不修改共享 git_env 的合同 |
| `test_git_broker_approval.py::test_broker_commit_accepts_an_approval_granted_for_the_tool` | 旧“批准后到达真实 Git”正向预期需要改成批准也不能恢复退役能力；用 Mock 替代其真实 repo fixture，需单独测试修改授权 | 不能执行真实 Git 来验证封闭，也不能改 ApprovalManager 通用语义 |
| 同文件 `test_broker_refuses_a_commit_with_no_grant` | 保留拒绝/零效果，新理由可能是能力关闭而非缺审批；去掉真实 Git fixture 的改动需批准 | 拒绝不能以创建/提交临时真实仓库来证明 |
| 同文件 manager 可见性及命名常量测试 | 仍可保留接口/ApprovalManager 单元语义 | 不必为移除执行能力而删除通用审批测试 |
| `test_security_posture.py` scope 签名检查 | 保留 scope/方法签名及审查目标；预计无需改 posture 生产代码 | 不缩短检查清单制造绿色 |
| A/A2、R02/R03、Apply/Sandbox/精确事务、MCP/Skill/DB 红色审计 | 原样保留，生产修复时运行可安全执行的关联回归 | 不批量改 fixture、skip/xfail 或弱化断言 |

预计需要的旧测试调整不等于本轮已授权；尤其原始未跟踪审计必须先保存历史版本，再独立审批其新合同期望。

## 7. 新红色回归与结果

新增文件：[test_r04_b3_min_closure.py](../../src/Sprout/tests/test_r04_b3_min_closure.py)。自包含 fixture，不依赖原未跟踪调查测试。
最终测试文件 SHA-256：`AA55C9D33323D28DE6AD265C4F3CA79B16077DDD54EBBB7B5E5EBBEC6FA43C55`。

| 分组 | 实际结果 | 解释 |
|---|---|---|
| 全21项 Write + 实际 Broker/custom ALLOW | 18 passed /3 failed | add/commit/pull 仍进入通用 runner；其他18项已拒绝 |
| 六项直接 Broker API + custom ALLOW | 0 passed /6 failed | 六项均执行至 Mock runner |
| 真实旧批准（commit/push/pull × single_use两种） | 0 passed /6 failed | 前两项默认矩阵、pull 自定义 REQUIRE_APPROVAL；可恢复旧执行 |
| 全21项外层不应先请求审批 | 0 passed /21 failed | 均产生 approval_id；测试批准并续调，记录结果，不隐藏多余提示 |
| 六项直接 API + DENY | 6 passed /0 failed | DENY 保持零执行 |
| 闭合入口不应到达异常/超时/取消 runner | 0 passed /3 failed | 通用 add 仍到达1次 Mock runner；未发生 _run_git/_run_command 宿主重试 |
| status/diff 无 S1 签发 | 2 passed /0 failed | B2 默认拒绝保留 |
| 内部 Apply/Proposal/Sandbox | 3 passed /0 failed | 路由/审批/验收拒绝对照通过，非真实 Git 验收 |
| **总计** | **29 passed /39 failed /0 errors /0 skipped** | **Python原始退出码1**；失败是拟议安全合同的真实红色基线 |

第一次新增回归为65项：29 passed /36 failed，Python exit=1；随后仅扩展单次旧批准覆盖和修正新增测试的三处 E501，最终68项结果如上。
不把外层 PowerShell 最后运行静态检查的0退出码当作 pytest 通过。
两个版本的日志均保留；没有用 skip/xfail、修改旧断言或生产补丁消除失败。

精确命令、JUnit 和测试前后源码哈希：

```text
C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r04b3-min-red/
C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r04b3-min-final-red/
  command.json, pytest.log, junit.xml, summary.json, source-before.json, source-after.json
```

启动命令形态（准确子进程 argv 以 command.json 为准）：

```powershell
& D:/dev/projects/sprout/.venv/Scripts/python.exe -I -S -B `
  C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r03_worktree_checks.py `
  C:/Users/WingYouther/.codex/worktrees/r04-broker-zero-bypass/sprout `
  r04b3-min-final-red src/Sprout/tests/test_r04_b3_min_closure.py
```

既有离线守卫、无缓存/字节码、临时 HOME/日志/pytest 目录，使用 MemoryOperationalStore/Mock metadata；不创建 Runtime 或真实数据库。
所有最终 run_capture 与 OS spawn 均 Mock；测试未运行真实 Git、Hook、网络或 helper。
真实 PolicyEngine/ApprovalManager 的特定判断与内存消费参与测试，不 Mock 掉这些待核查门禁。
最终 Ruff 0.16.8 全仓检查 exit=0，git diff --check exit=0；本轮没有完整 collection/L1 或原生验证。

## 8. 是否取消通用 B4–B7 扩张、剩余风险与回滚

**建议正式取消“恢复全部31项通用 Git 执行能力”的 B4–B7 功能扩张。**
保留本次最小拒绝、已有内部 P0 路径，以及将来有明确需求才单项审批的 P1 读取。
这项范围决策还需用户确认；不能把 B4–B7 的所有安全任务标为已完成或全部不需要。

必须继续跟踪：

- B2 没有可信安装/受控视图签发器，真实 S1 继续 HOLD。
- ApplyBroker、Local/isolated sandbox、SandboxGitTool、实验准备、Skill 下载仍有独立 Git 执行及配置/helper/网络风险；本补丁不触碰它们。
- 通用 ProcessBroker/CliRunTool、自定义插件可能另行调用 Git；本方案不是 OS 层“任何 Git 都禁用”的保证，也未证明远端可利用性。
- 内部提交的验证/审批/patch/HEAD 绑定、并发/崩溃/UNKNOWN 不能因通用 API 退役而免验收。
- API 仍存在但返回拒绝是明确兼容性改变；直接依赖 Broker 的外部嵌入者需要说明，不静默改为调用内部 Apply。
- 保留方法/Schema/参数、原身份及授权边界；旧批准不迁移、不重新解释、不用来启用新能力。

本轮静态检查与 Mock 并不证明生产修复后完整回归通过；生产补丁仍未实施。
完整 L1、原生 Windows/POSIX Git、真实 Hook/helper/网络、外部数据库、Docker/Temporal、真实模型：**not_run**。
历史原生崩溃/长路径/symlink/外部集成限制仍保留；七份原调查材料及已接受的最小能力文档均不改写。
安全回滚应保留 A/A2/B1 的零回退、B2 默认拒绝和退役边界；不能恢复 `_run` 执行或降低风险级别救可用性。

**审查建议：批准上述三份生产文件的独立 B3-min 最小补丁及明确列出的必要测试调整；本轮到此停止，尚未实施、暂存或提交。**
