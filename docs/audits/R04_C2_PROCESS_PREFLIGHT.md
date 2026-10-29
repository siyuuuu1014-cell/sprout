# R04-C2 — 本机进程边界预检与 Mock RED

## 基线与结论

- 分支：`codex/r04-broker-zero-bypass`。
- HEAD：`d63edb8dfd23fb9641ce643e313f1dbc7ed1e724`。
- 开始时暂存区为空、已跟踪工作区干净；13份既有调查材料的 SHA-256 与上一轮记录逐项一致；原 C1 RED 文件也未变。
- 本轮仅新增本报告和 `src/Sprout/tests/test_r04_c2_process_boundary.py`，无生产修改、暂存、提交或外部执行。
- **C2 HOLD**：三个工具均没有 ProcessBroker 注入接口，当前仍可直接到达宿主进程叶子。外层策略仍有效，不等于所有外部调用都无审批。
- 文档架构基线（sprout-v1、sprout-review-v2.1 的 F03/R04）要求复用双层策略、无静默宿主回退；实际源码仍存在以下绕行，不以历史文档宣称已实现。

## 实际调用图及可达性

源码行号以本轮 HEAD 为准，未修改生产文件。

```text
runtime/factory.py:92 create_tool_registry(...skills_dir...)
  -> tools/registry.py:188 GrepCheckTool()
  -> tools/registry.py:190 CliDetectTool()
  -> tools/registry.py:203 SkillScriptTool(skills_dir) [有配置才注册]
factory.py:131 ToolExecutor -> AgentLoop / ActionExecutor
agent/executor.py:80,96 -> ToolExecutor.execute
  -> tools/executor.py:113 risk policy
  -> :132 ActionRequest(TOOL_INVOKE) / layered boundary
  -> :180 final deny / :191 approval / :256 invoke
  -> system_tools.py 三个 invoke -> :48 _run_command
  -> :64 asyncio.create_subprocess_exec -> :73 communicate

直接嵌入 Python: 三个工具.invoke -> 同一 _run_command，无独立 broker 判断
Skill 在 invoke 开始还会 -> ensure_temporal_async -> probe / 可选服务和 Worker 启动
```

Runtime 在 `runtime.py:399–424` 重新注册 CliRun/Download/Git 等带 Broker 工具，但没有给本轮三种工具注入 ProcessBroker。
`agent/loop.py:478` 的节点严格 overlay 和 `tools/executor.py:332–352` 的 strict 解析仍限制工具暴露。上述调查**不是**已证明 restricted 节点可逃离 overlay；不能把直接 Python 接口存在当成外部攻击者可达证明。
普通 Agent 注册链可使用 Detect/Grep；Skill 是否可用取决于 skills_dir。SkillScriptTool 只按目录名寻找脚本，不查询 SkillIndex 的批准摘要；不能把 factory.py:101–105 的 SkillRegistry 信任管理自动套用到此独立工具。

## 三工具边界矩阵

| 工具 | 当前执行对象及输入 | 已有正确防护 | 缺口与兼容影响 |
|---|---|---|---|
| SkillScriptTool (`system_tools.py:529–622`) | skill/script 经 resolve 与 relative_to 校验；优先 which(script)，否则 .py 用 sys.executable，或可执行文件直接执行；args 字符串列表、cwd=skill_root、模型 timeout 最低0.1无上限 | 路径越界/缺失拒绝；HIGH 经标准 Executor 要审批，cron 拒绝 | 无 ProcessBroker、无冻结脚本/解释器/Scope/操作者身份绑定；resolve后可变目标及检查到使用竞态；独立invoke忽略上游授权；先探测Temporal再校验输入；脚本可间接任意I/O |
| CliDetectTool (`:194–262`) | 用户command经 PATH which，可含路径；默认 --version，允许 version/help 等参数 | 非版本/帮助参数拒绝；找不到程序是无启动的正常结果 | 参数白名单不是可信可执行文件白名单；恶意二进制的 --version/--help 仍可写入、联网；LOW无独立进程授权；参数空列表也通过；timeout/OSError被包装为成功且version=unknown |
| GrepCheckTool (`:108–191`) | PATH优先grep，找不到才rg；pattern/path/include/context/max_results，cwd=当前进程工作目录 | 空pattern、工具缺失拒绝；returncode=1正常无匹配；无shell | 未验证安装身份；无Process/FileBroker及具体file.read Scope；pattern/path前缺少 --；可搜索任意传入目录；递归/符号链接及rg配置行为未受稳定视图约束；match数是每文件限制不是总输出限制 |

`_run_command` 使用 SecretBroker 环境白名单而非完整父环境，这是已有防护；不等于操作系统隔离或可信程序身份证明。PATH、HOME及所启动程序解释的其他配置仍需独立验证。
`_clip` 在完整 communicate 已缓存后才截断输出，40,000字节是展示上限，不是采集内存上限。helper超时会kill单个进程并wait，但没有进程树保证；取消没有finally清理。清理失败、部分效果及结果未知没有持久状态。工具本身没有自动重试；普通failure乃至Detect的unknown成功仍会丢失效果信息，不能据此安全重试。

## Mock RED 设计与安全性

新增自包含测试，不导入历史审计夹具。只在pytest临时目录创建惯性脚本文本，从不执行。
三个工具测试替换 `_run_command`、PATH查询、Temporal检查；helper生命周期测试只使用内存Fake进程。同步Popen/run及异步exec/shell全部阻断或替换；现有离线runner再限制网络和写入路径。没有启动Runtime、真实进程、Temporal、MCP或数据库。
真实SecurityPolicy、PolicyEngine、ApprovalManager及内存审批存储保留。Scope对照明确由服务端测试适配层将真实DelegationScope交给PolicyEngine，证明已有上层Scope DENY不会被risk低标签放宽，**不证明生产工具已绑定文件读取Scope**。
原审批测试先走真实request/decide再调用，展示外层批准不能代替尚不存在的底层Broker合同；不是声称ApprovalManager自身接受伪造批准。

### 精确结果（不累计重叠组）

环境：现有Python 3.13.9 / pytest 9.1.1 / Ruff 0.16.8，未安装更新依赖。

```text
D:/dev/projects/sprout/.venv/Scripts/python.exe -I -S -B
 C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r03_worktree_checks.py
 <本worktree> r04-c2-red-final src/Sprout/tests/test_r04_c2_process_boundary.py
```

- 最终38项：**19 passed /19 failed /0 errors /0 skipped，Python原始退出码1**，runner 4.82秒，pytest 2.68秒。
- 测试期间源码/任务文件哈希不变。准确命令、JUnit、输出、前后哈希保存在仓库外 `sprout-r02-local-1c0815af/r04-c2-red-final/` 的 command.json、junit.xml、pytest.log、source-before.json、source-after.json。
- 首轮 `r04-c2-red/` 为18 passed /20 failed /0 errors /0 skipped，退出1。其中一项是Popen Mock不可下标导致MCP依赖类型注解导入失败，**不计为真实RED**。仅修正新增测试的Popen替身以支持Popen[bytes]类型表达、仍禁止构造，保留首轮日志后重跑。最终19个失败均为安全合同断言，无fixture/导入失败。

最终19项失败分组（完整node ID见JUnit）：

| 用例族 | 数量 | 实际证据 |
|---|---:|---|
| no_broker_means_zero_host_execution | 3 | 三工具均await宿主spy一次 |
| forged_context_cannot_mint_process_authority | 3 | 附带trusted/approval/source仍await一次；对照无字段也一样，不把这些标签错误描述为原有签发器 |
| low_label_or_outer_allow_is_not_lower_process_authority | 3 | 外层ALLOW后没有底层Broker，启动spy一次 |
| old_tool_approval_is_not_a_broker_execution_grant | 1 | 正规外层批准后仍直接启动，无底层执行计划授权 |
| attempted_effect_loss_is_not_retryable_failure | 6 | 三工具TimeoutError/ConnectionResetError未传播ToolExecutionUnconfirmed |
| grep_pattern_cannot_be_parsed_as_an_option | 1 | pattern前无 --；仅证明argv问题，未运行原生grep或宣称特定flag利用成功 |
| skill_validation_precedes_temporal_side_effect | 1 | 无效输入也先调用Temporal就绪spy |
| command_cancel_reaps_started_process | 1 | CancelledError传播但kill/wait次数0 |

最终19项通过：Policy DENY3、真实Scope DENY3、Skill无审批拒绝1/正常申请审批不执行1/cron拒绝1、timeout无自动重试3、取消传播无重试3、Detect非法参数拒绝1/缺失程序无执行成功1、Skill路径逃逸拒绝1、helper超时kill/reap1。
没有可信固定二进制的生产探测签发器，因此不伪造“版本标记即可安全执行”的正向证据；现有无启动探测与参数拒绝作为正向兼容对照。

### 原审计复现

同一runner，label=`r04-c2-historical`，显式选择：

- `test_r04_broker_audit.py::test_skill_script_has_no_unbrokered_host_execution`
- `test_r04_broker_audit.py::test_low_risk_label_is_not_process_authority`（2个参数）

结果 **0 passed /3 failed /0 errors /0 skipped，Python原始退出码1**，3.75秒。全部因host_calls非空的原始安全断言失败，未改写历史文件。

Ruff全仓 `ruff check --no-cache .` 退出0；`git diff --check` 退出0。新增未跟踪文件不在git diff之内，Ruff已明确扫描新增测试。本轮不运行旧的真实CLI/脚本执行fixture。

## 建议的最小补丁拆分（仅方案，未实施）

1. **C2-a立即封闭**：候选 `tools/system_tools.py`，限定三个invoke缺少可信执行合同就提前拒绝，尤其在Temporal ensure之前；保留输入Schema、无启动查询兼容；若保留Detect发现路径需明确文件元数据隐私边界。不能凭default LOW或版本参数恢复进程执行。
2. **C2-b有条件恢复，须独立批准范围**：`tools/registry.py`、`runtime/runtime.py` 的服务端ProcessBroker/可信InvocationContext接线，`system_tools.py` 结构化命令与真实Scope绑定；若复用上游批准，需要与命令/文件摘要/解释器/cwd/env/task/session/source单次绑定，不能借CLI专属capability或approval_granted布尔。可能涉及context_keys/executor/process_broker，必须先审查能力接口，不预先批准修改。
3. **按产品目标独立恢复**：Detect优先无启动which查询；可信版本探测需受管理安装与固定argv。Grep可优先已有受控文件检索，不建第二执行框架；原生grep/rg恢复需可信安装、文件Scope、配置/链接边界与资源限制。Skill需批准内容摘要、解释器与稳定视图，不能仅验证路径属于skills目录。

三个工具可以共用一份**小的默认拒绝补丁**，但不宜在同一轮同时恢复全部能力。Skill的任意代码/Temporal前置与Detect固定探测、Grep文件读取的信任证据不同，应分开验收。不能把它们未经接线直接丢给现有ProcessBroker就宣称已满足可信安装、文件Scope或UNKNOWN闭环。

## R05 / R06 与保留风险

- R04主责：无Broker零绕行、真实服务端身份与审批交接、命令对象与文件作用域绑定、即时效果分类；不是仅降低risk或加模型标记。
- R05依赖：可信程序安装、受控不可变脚本/仓库视图、OS限制、受限子进程树与文件/网络隔离。普通PATH、首次哈希、一次resolve、单进程kill不是完整证明。
- R06依赖：全链路InvocationContext及持久执行恢复/UNKNOWN接线；具体持久幂等、效果receipt、跨重启禁止重复执行需与现有事务体系协同。不得另建平行事务/审批系统，也不得将立即捕获异常当成持久闭环。
- 未验证恶意原生程序、实际grep/rg选项语义、Windows/POSIX子进程树、真实Skill、实际Temporal行为。完整L1、真实进程/Git/Hook/helper/网络及系统集成全部 **not_run**。
- 不声称restricted节点已可利用；标准Executor拒绝和严格overlay仍有效。直接invoke与被允许的普通Agent调用存在缺口，风险需按真实暴露面分级。
- 本轮只新增证据，无生产兼容性变化。未来fail-closed会阻断脚本、版本执行及原生grep；恢复必须另获批准，不能回滚到当前无Broker宿主路径以追求可用性。

## 文件与收尾

新增测试 SHA-256：`082E15A85309BB99063F1DC542E03AEF93834DACFC27AFBF581691958F64CF38`。
本报告哈希以交付时外部计算为准，避免自引用。原13份调查材料、原C1 RED完整保留。生产与既有测试未修改；暂存区为空；HEAD不变。**完成预检后停止，等待最小生产补丁独立授权。**
