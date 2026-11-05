# R04-B — GitBroker 逐操作安全设计审查

日期：2026-10-21。状态：**仅设计与 Mock 复现；生产修复未授权、未实施。**

分支 `codex/r04-broker-zero-bypass`；HEAD
`9d2f9a21561258242d84585bc6a284bb29013a9e`，父提交
`0d53f7dd26fbd49b37b02db7905ec33c9ad3ab6a`。保留 A/A2 两次提交及全部调查材料。
本轮只新增本文和 `src/Sprout/tests/test_r04_b_git_design.py`，未暂存、提交、推送。

## 1. 证据口径与现有调用链

历史 v1/v2.1、F03/F04 及路线图 R04 仅作定位；下列结论来自当前源码。
没有运行测试性的 Git init/config/add/commit/reset/checkout/push/pull/merge，
没有运行 Hook、外部 helper、网络或真实用户数据库。Mock 到达执行点不等于真实攻击成功。

### 1.1 已证实的调用关系

- `tools/system_tools.py:625-767`：GitInspectTool 有 10 项操作；`:702-715`
  仅 status/log/branch/diff 委派 Broker，剩余 6 项走 `_run_git`。
- `tools/system_tools.py:770-1004`：GitWriteTool 有 21 项操作；`:865-883`
  仅 add/commit/pull 委派 Broker，剩余 18 项走 `_run_git`。**push 虽已有 Broker 方法，工具仍未接入。**
- Broker 缺失时，全部 31 项工具路径均可进入 `_run_git`；本轮逐项 Mock 证实。
  已注入但不支持的操作也没有 fail-closed；原调查 31 项注入 DENY 对照有 24 项失败。
- `execution/git_broker.py:42-162`：只有 status/diff/log/branch/add/commit/push/pull 8 个公开方法。
  `_run` (`:190-198`) -> `git_args/git_env` -> `run_capture` -> 最终子进程。
  `_run_git` (`system_tools.py:1007-1020`) 是另一条直接 `_run_command` 路径，**不使用 GitBroker 的 git_args/git_env profile**。
- Runtime 已注入 GitBroker (`runtime/runtime.py:396-399,421-422`)；不能继续声称正常 Runtime 没有注入。
  独立 `create_tool_registry` 的 Git 工具仍可无 Broker (`tools/registry.py:200-201`)。
- 默认模型：AgentLoop -> ActionExecutor -> ToolExecutor -> 工具；外层风险/审批并非不存在。
  `git_inspect` 标为 low、`git_write` 为 high。外层只准入 tool.invoke，不能替代具体 Git 动作检查。
- `tools/executor.py:148-188` 在 REQUIRE_APPROVAL 时拒绝 cron/automation。
  但 Git 工具不在 `CONTEXT_TOOLS` 中，没有沿用 CLI 的服务端上下文及 A2 交接。
  `git_broker.py:165-187` 默认 actor=system，只把 workspace.root 作为资源；工具没有传 task、scope、session 或来源。
  log/branch/add/pull 甚至没有 scope 参数；status/diff/commit/push 有 scope 但工具没有传入。
- `GitBroker.commit/push/pull` 查询真实 ApprovalManager，不接受普通 bool 授权。
  **本轮没有复现 Git 的 CLI bool 漏洞**：伪造 `_sprout_context.approval_granted=True` 的 commit 对照拒绝。
  实际问题是授权参数/身份脱节及旧无绑定记录：外层完整参数与内层 message/files 等指纹不同；
  外层已消费的一次性审批不会自动成为内层可信证据；正常提交出现双层不兼容。
- `GitBroker._decide` 调用 PolicyEngine，并在提供 EventBus 时发布 policy.decided；
  没有完整 Git 动作开始/结果/部分成功/remote 效果凭证。工具回退只有可能存在的外层工具事件，没有内层 Git 决策。
- 当前 restricted 节点使用 strict 工具集合，不包含全局 git_write/git_inspect；
  `runtime/nodes.py:350,474,653` 也把 isolated 的 SandboxGitTool host 能力关闭。
  `execution/sandbox_tool.py:679-682` 返回 no host fallback。不能把全局工具缺陷当作已证明的 restricted 节点逃逸。
  非隔离 SandboxGitTool 是另外三个直接 Git 视图，需纳入后续共享 profile 审查，不在本轮修改。

### 1.2 当前策略不是“Git 全放行”

`security/access.py:27-34` 仅有 8 个 Git ActionType。
`security/engine.py:118-136` 的默认矩阵为：

| ActionType | 当前默认矩阵（正常 Scope） |
| --- | --- |
| git.status / git.diff | ALLOW |
| git.commit / git.push | REQUIRE_APPROVAL |
| git.log / git.branch / git.add / git.pull | **DENY：落入未映射默认拒绝** |

组织/委派层只能继续收窄。后四项是功能不可用，不是权限绕过；8 项矩阵已用真实 PolicyEngine 核验。
默认组织对 `process.run` 的 git force 命令 glob 也不等于 Git Broker 的逐字段规则：
GitBroker 发出的是 git.push 等动作和 remote/branch 参数，并非一个 process.run 命令请求。

## 2. 所有 31 项操作的受控合同

### 2.1 下表共同生效的模式、审批、审计与效果规则

当前没有一个可由工具参数声明的统一 trusted_local/restricted 开关。下列 **T/R 是拟议执行合同，非现有 bool/metadata**：

- **T**：服务端已验证的本地操作者、已授权的仓库/Scope，以及受控 Git 执行 profile。
  local 只是执行位置，不代表自动批准 Hook、配置或任意程序。
- **R**：服务端冻结的隔离任务身份、受管理 sandbox 和具备所需能力的受限执行器。
  未提供隔离 Git 执行器的操作拒绝；不能调用宿主 GitBroker 或回退 `_run_git`。
- **I（inspect）**：T 仅在 S1 profile、所需资源 Scope 和 Policy 均满足时 ALLOW；
  R 仅在隔离执行器支持相同检查时 ALLOW，否则 DENY。无人值守也只能做此类已准入的受控读取。
- **H（local mutation）**：T/R 都要具体动作策略 + 原子消费的真实审批 + 冻结效果范围；
  cron/automation 默认 DENY，不因旧审批或来源缺失变成 interactive。
- **N（network）**：T/R 默认 DENY，直到 transport、endpoint、凭证和网络执行约束全部可证明；
  启用后仍需 H 的显式审批，无人值守仍默认 DENY。不用一般 git_write 同意代替网络授权。
- **D（destructive）**：T/R 默认 DENY；未来若要支持，需单独授权的服务端策略与专用精确审批。
  R04-B 不自动启用 hard reset、强制推送、删除 refs、丢弃 stash 等能力。
- S1/S2/S3/SN profile 见第 5 节。**无法证明任一前提时保持拒绝，而非边执行边猜测。**
- 每一行必须记录同一 action_id、可信身份、operation、仓库身份、审批/计划指纹、策略、开始与结果。
  对通过列表形式返回的路径和 remote 信息也做 Scope/敏感信息过滤。不能只记录一个 tool.ok。
- 所有行都适用第 6 节的中断规则：开始状态不明不可当作无效果；副作用操作无条件自动重试禁止。
  下表“现有内层动作=无”表示工具回退没有该 GitBroker 决策，不表示没有外层 ToolExecutor。

### 2.2 GitInspectTool：10 项

`B`=已委派 Broker；`F`=工具直接回退；`—`=无现有方法/枚举。
拟议新增枚举仅为设计，尚未添加或改变任何策略。

| # | operation | 当前路径 / 内层动作 / 默认策略 | 拟议动作 | 最小合同（T/R 依 2.1） | 资源、副作用、helper 风险及兼容点 |
| --- | --- | --- | --- | --- | --- |
| I01 | status | B.status / git.status / ALLOW | 保留 git.status | I + S1；仓库元数据及可见工作树 Scope | 状态读取也可能刷新 index、调用 fsmonitor；抑制可选写锁、禁用 monitor，固定 porcelain 输出；现有裸工具含 branch，而 Broker 输出不同 |
| I02 | log | B.log / git.log / DENY | 保留 git.log，单独补矩阵 | I + S1；有界 limit、冻结 ref/OID | 当前委派丢弃 ref；固定 format，禁用签名验证程序/外部 diff/textconv/子模块委派；日志可能含敏感提交文本 |
| I03 | branch | B.branch / git.branch / DENY | 保留 git.branch（只列表） | I + S1；可见 heads/remotes | 不得复用该动作授权创建/删除；固定 --list/format，不跟踪用户 alias，输出隐藏 refs 需 Scope |
| I04 | diff | B.diff / git.diff / ALLOW | 保留 git.diff | I + S1；固定 base/ref OID、paths/name_only | 当前委派丢 base/ref/files/name_only，可能扩大输出；现有 --no-ext-diff **没有禁用 textconv**；不能执行 repo 指定 driver |
| I05 | show | F / 无 | git.show（新增） | I + S1；一个冻结对象及允许的视图 | 现有 --stat --oneline；不是任意 Git argv；控制 diff/textconv、签名及输出大小，不把 ref:path 当任意路径读取 |
| I06 | remote | F / 无 | git.remote.list（新增） | I + S1；只列已授权配置，不联网 | 只做 remote -v/规范列表；不升级为 remote show；URL/userinfo/凭证不得原样返回或上传 |
| I07 | tag | F / 无 | git.tag.list（新增） | I + S1；只列表、固定 format | 与写 tag_create/delete 分离；控制 pager，禁止隐式签名验证 helper |
| I08 | stash_list | F / 无 | git.stash.list（新增） | I + S1；有界 reflog 列表 | 不调用 apply/pop；消息可含私密路径，返回稳定 stash OID/序号，不能把可变 stash@{0} 当审批身份 |
| I09 | rev_parse | F / 无 | git.rev_parse（新增） | I + S1；受限 revision -> 一个 OID | 当前仅 --verify；禁止 option-like/ref 注入和任意 --git-path 查询；非法输入不得静默变 HEAD |
| I10 | ls_files | F / 无 | git.ls_files（新增） | I + S1；显式 tracked/untracked、路径范围 | untracked 也会暴露文件名；禁用 magic pathspec、子模块递归，过滤禁读资源；部分 Scope 无法安全满足时拒绝 |

### 2.3 GitWriteTool：21 项

| # | operation | 当前路径 / 内层动作 / 默认策略 | 拟议动作 | 最小合同与安全默认值 | 文件、工作树、refs、remote 及 helper 效果 |
| --- | --- | --- | --- | --- | --- | --- |
| W01 | add | B.add / git.add / DENY | 保留 git.add，单独补 H 矩阵 | H + S2；显式逐路径 Scope，拒绝隐式全仓 | 更新 index/objects；clean/process filter 可执行；当前直接拼 files，空集变 -A，未放 --，根 Scope 不代表所有目标获准 |
| W02 | commit | B.commit / git.commit / REQUIRE_APPROVAL | 保留 git.commit | H + S2/S3；冻结 parent、index/文件、message、作者及目标 ref | 当前先 add(files 或 -A) 再 commit，可能带入用户原暂存项；Hook/签名/维护程序风险；部分成功需凭证，正常单次审批不能变双提示 |
| W03 | pull | B.pull / git.pull / DENY | 保留 git.pull，N 默认拒绝 | N + SN + S3；仅已授权 endpoint/ref，ff-only | 当前工具丢 remote/upstream；Broker 默认 origin；对象/refs/FETCH_HEAD/工作树可变，失败可能已 fetch；filter/Hook/认证 helper |
| W04 | fetch | F / 无 | git.fetch（新增） | N + SN；不得隐式 --all | 修改 objects、remote refs、FETCH_HEAD；不改工作树不等于无副作用；当前非法/空 remote 可能变 --all |
| W05 | push | F；B.push 已有但未接入 / 工具无内层动作 | 保留 git.push | N + SN；精确 src OID -> dst ref；禁止 force/delete/mirror/wildcard | 远端 refs 与本地 tracking/upstream 可能变化；pre-push、remote/credential/ssh helper；当前 branch 可装入 +src:dst 或 :dst |
| W06 | checkout | F / 无 | git.checkout（新增） | H + S3；明确 branch/OID 模式，无 force | 写 HEAD/index/工作树；post-checkout/filter；不能让 branch/path 模式歧义扩大范围 |
| W07 | switch | F / 无 | git.switch（新增） | H + S3；已存在分支及 expected OID | 工作树/HEAD/index，可能触发 filter/Hook；禁止隐式 -C/-c、discard changes、猜测 tracking |
| W08 | branch_create | F / 无 | git.branch.create（新增） | H + S2；新 ref 不存在、固定起点 OID | 创建 refs/reflog，可有 reference-transaction Hook；不能覆盖同名分支或靠当前 HEAD 猜起点 |
| W09 | branch_delete | F / 无 | git.branch.delete（新增） | D 默认 DENY；将来仅精确专用授权 | 删除 refs/reflog；保留 -d 合并检查，不提供 -D 绕过；当前分支及受保护 dev/main 等拒绝 |
| W10 | merge | F / 无 | git.merge（新增） | H + S3；冻结双方 OID、预检冲突范围 | refs/index/工作树/冲突状态；merge driver、Hook、签名；禁止配置驱动隐式策略或丢弃用户修改 |
| W11 | rebase | F / 无 | git.rebase（新增） | H + S3；明确本地可改 refs/起点/范围 | 重写历史、冲突与 sequencer；pre-rebase/post-rewrite、editor/exec 配置；禁止隐式 autostash/交互 exec |
| W12 | reset | F / 无 | git.reset（新增） | soft/mixed 为 H + S3；hard 为 D 默认拒绝 | soft 改 ref，mixed 另改 index，hard 另丢工作树内容；不能仅按工具同一风险等级处理 |
| W13 | restore | F / 无 | git.restore（新增） | H + S3，丢弃内容须明确确认；禁止默认 . | 写所选工作树/可能 index；smudge/process filter；固定 source OID、逐文件 Scope，不自动恢复整个仓库 |
| W14 | revert | F / 无 | git.revert（新增） | H + S3；指定 commit 与父选择，拒绝歧义 merge commit | 新提交/冲突/index/工作树；commit helper/Hook/签名；不能隐式选 mainline 或自动提交未审内容 |
| W15 | stash_push | F / 无 | git.stash.push（新增） | H + S3；明确 index/worktree/路径及不包含秘密 | 修改 objects、refs/stash、index/工作树；不是备份无害操作，不能未经同意暂存/隐藏用户修改 |
| W16 | stash_apply | F / 无 | git.stash.apply（新增） | H + S3；冻结 stash OID 与目标状态 | 修改 index/工作树，可能冲突及 filter；不用会移动的 stash 序号替代固定对象 |
| W17 | stash_pop | F / 无 | git.stash.pop（新增） | H + S3，含删除部分的精确授权；首批禁用 | apply + 删除 stash 复合操作；部分成功不得再 pop；未完成证据合同前保持 DENY |
| W18 | stash_drop | F / 无 | git.stash.drop（新增） | D 默认 DENY | 删除恢复入口/ref/reflog；必须固定 OID/expected stash 链，不能默认 stash@{0} 后盲删 |
| W19 | tag_create | F / 无 | git.tag.create（新增） | H + S2；固定对象、tag 不存在；轻量/注解显式 | refs/objects，签名程序及 reference Hook；禁止覆盖已有 tag、自动签名或借 message 改模式 |
| W20 | tag_delete | F / 无 | git.tag.delete（新增） | D 默认 DENY | 删除 tag refs；固定 expected OID，不能带通配符或扩大为远端删除 |
| W21 | clone | F / 无 | git.clone（新增） | N + SN；另需目标父目录/新目录写授权 | 当前忽略 repo，以进程 cwd/任意 directory 落盘；网络、template/config/filter/checkout/submodule 风险；目标已存在/越界拒绝，首批不 checkout、不递归模块 |

31 行全部以“缺 Broker 或当前执行器不支持 -> 明确拒绝”为共同底线。
不能用 `getattr` 缺方法后 `_run_git`、改调用 ProcessBroker、或更宽的宿主 shell 兜底。

## 3. Broker 最小扩展与权限消费

### 3.1 扩展既有 GitBroker，不建立 GitActionBroker 第二框架

建议在同一 GitBroker 中增加固定 operation -> parser/ActionType/plan/runner 表，
提供一个受控 dispatch；保留既有 8 个公开方法作为兼容 wrapper。
不允许模型传 argv、命令名、profile、trusted 标志或可执行文件路径。
表外操作、缺字段、错误类型、超长/控制字符输入拒绝，而非字符串拼接。

为尚未表示的 6 项读取、17 项写入设计 **23 个加法式 ActionType**（名称见两表）。
现有 8 个值不改义，尤其 git.branch 仍是列表，不能复用为分支删除；
不能把所有新写操作伪装成 git.commit 或 process.run 来绕开动作收窄。
新增默认矩阵必须逐项审核；非托管/未证明 profile 下仍拒绝，不全局按 git.* 放行。
现有 log/branch/add/pull 的 DENY 不可在接线时顺手改成 ALLOW。

ActionRequest 继续复用，不重定义；身份来自服务器持有的 Principal、原任务 source/Scope，
而不是 repo 参数、Message.metadata 或当前前台入口反推。
现有老方法可增加关键字上下文，保留位置参数读取兼容；无可信身份的历史写调用不能静默获得新权限。
对只有部分路径权限的 Scope，必须核验仓库管理区与每个实际目标；无法把 Git 隐含资源读取限在 Scope 内就拒绝。

### 3.2 精确审批与 A2 的关系

计划至少冻结：版本、operation/action、真实仓库根/common-dir/worktree 身份、
当前 parent/ref/index、源/目标 OID、规范文件集、remote 的有效 endpoint/refspec、
作者/message/模式、可信 task/session/source/requester、资源边界、执行器/profile 与到期时间。
敏感 URL/凭证不得放入公开摘要；只记录受控 ID/指纹。

外层 ToolExecutor 仍做 tool policy/人审。内层每个 Git 步骤仍做 ActionRequest/Scope/策略，DENY 先于任何交接。
审批要匹配同一规范计划，而非当前外层完整参数和内层子集各算一遍。
复用现有 ApprovalManager 的原子一次性消费，随后交接调用内不可由参数伪造的凭据；
可借鉴已实现 A2 的 broker/manager/task 身份绑定、短有效期、finally 失效，
**不能直接把 CLI bool 或任意 approval_id/token 当 Git 权限**。

Git commit 等是多步骤操作：一次批准的是整个限定计划，不是无限命令；
每步绑定其 phase/argv/前置状态，只有该受控 runner 可领取相应阶段；同阶段不可重复执行。
旧一次性 grant 不可因“需要补提示”恢复为 APPROVED，异常后不重放。
Git 上下文不得复用 CLI 或 Local Acceptance 的域；普通直接 Python API 仍需其自身可信来源/真实审批。

历史参数缺少版本/operation/仓库/身份时保持旧记录原样，新合同视为不适用，要求重新审批；
不能把旧 {message,files}、session 缺失记录或任意调用者提供的 plan_fingerprint 自动升级。
这是拟议兼容性变化，实施前应批准相应测试调整；本轮没有修改 ApprovalManager 或数据库。

### 3.3 结果与审计

当前 GitResult (`execution/models.py:173-177`) 仅 action/output/allowed/reason，
没有 approval_id、started、exit_code 或效果状态。当前 `_git_result_to_tool_result` 也不能表达 parked/UNKNOWN。
建议小范围加法式结果版本，保留四旧字段及默认值，补 approval_id、started、effect_state、operation_id，
并映射既有 ToolResult.needs_approval / ToolExecutionUnconfirmed，不创立第二种通用任务状态机。
是否需要 execution/models.py、公开序列化和事件合同变化须另行批准，不能现在隐式扩大生产范围。
旧结果没有 started/effect 证据时为 UNKNOWN，不做成功或可重试的回填。若需要持久化效果 claim，另报 Schema/接口范围。

## 4. 操作数、仓库与版本边界

1. **不能用 `_safe_git_ref` 兼做所有参数校验**：它只 strip/拒绝开头 - 和 NUL，返回空串；
   当前 callers 又把非法值替换成 HEAD/origin/--all。必须区分省略值与显式非法值；后者明确拒绝。
2. 按操作定义不同的类型：受限 revision、分支全名、tag、stash OID、remote 配置名、URL、文件路径。
   拒绝 ref 中选项/控制字符/超长文本；分支/tag 走相应合法性规则，revision 不能无限接受 Git 表达式。
   需要范围时分别解析两个端点并冻结 OID，不将自由文本拼成 base..ref。
3. push branch 实际是 Git refspec：`+src:dst`、`:dst`、通配符、mirror/all 与强制/删除效果必须拒绝。
   显式冻结一个 src OID 和一个 dst ref。不能只挡 --force 字符串而让 + 前缀进入。
4. remote 名称只能引用受信、已审的配置项；它不是任意 URL 或 ext::/自定义 helper 输入。
   clone 的 URL 走独立解析/transport 合同。拒绝内嵌 userinfo/密码，不从输出记录凭证。
5. 路径先经 R03 风格规范化、目录/文件授权及工作树身份验证；reject 越界、设备 namespace、
   非法 drive-relative、路径穿越和 magic pathspec。适用子命令使用 -- 分隔并采用字面 pathspec。
   `--` 不是万能修复：它既不关闭 pathspec magic，也不能盲目放在每个子命令的相同位置。
6. 验证真正 Git root/common-dir/git-dir，不以传入 Workspace.kind 或 -C 子目录证明仓库身份。
   `add -A` 可能作用于整个仓库，不限于 cwd；clone 当前更不使用 repo 参数。
   新目标目录不得覆盖既有文件；所有中间目录、.git 指针、alternates 和 worktree 关联都要处于准入边界。
7. commit 前确认预期 parent、index 与授权文件集合；不能把用户既有 staged 文件一起提交。
   不在失败时 reset/clean 用户工作树。复用现有 isolated_git_plan/evidence/authorization/coordinator 与
   git_transaction_contract 的可用机制，不复制独立 Apply 通道，也不把其来源证明从 metadata 伪造出来。
   普通 Git 工具与隔离实验合同当前并未连接，不能把后者测试通过当作前者保证。
8. **单次 Path.resolve/config 扫描不消除 TOCTOU**。原生 Git 会重新打开路径、配置和驱动。
   不可信且可并发替换的仓库，需受管理固定快照/执行器文件系统约束；无法证明时拒绝。
   本设计不宣称仅凭 Git 参数 pin 就可在 Windows/POSIX 消除链接竞态，也不提前实施 R05。

## 5. Git 执行 profile：Helper/Hook/环境/配置

### 5.1 当前已做与未做

`execution/git_env.py` 使用 SecretBroker 环境白名单，固定 noninteractive 变量，
`git_args` 固定 core.fsmonitor=false 与 core.pager=cat；GitBroker.diff 有 --no-ext-diff。
这些机制应保留，但不是“仓库程序不会执行”的完整证明：

- 同文件文档明确 **刻意保留 Hooks**；本轮原红色 Hook 探针仍失败。
- --no-ext-diff 与 --no-textconv 不同。后者当前缺失。
- HOME/USERPROFILE/XDG 等仍在 whitelist；未显式禁止 system/global config，
  repo config/attributes/include/helper/签名/子模块/partial clone 也不因此消失。
- Broker 执行 `git` 来自 PATH；固定 pager=cat、editor=true 也不是可信可执行文件身份绑定。
- `_run_git` 回退没有使用上述 Git pins；普通环境白名单不等于 Git 配置隔离。
- git_env/git_args 同时被 ApplyBroker、GitWorktreeSandbox 和 SandboxGitTool 使用；
  **不能为修本工具就全局修改 Hook 行为**。先在 GitBroker 的专用 profile 中收紧，其他使用方独立评估。

### 5.2 拟议 profiles（未实现）

- **S1 / bounded inspect**：服务端选择的可信 Git 完整路径与版本；结构化 argv；
  不启动 pager/editor；禁 fsmonitor/Hook、外部 diff、textconv、签名验证及隐式子模块/网络。
  设置读取所需的可选锁抑制，固定输出格式与大小。partial/promisor 仓库可能 lazy fetch，
  未证明断网且对象就绪时拒绝相应读取，不能因 inspect 标签放行网络。
- **S2 / local single mutation**：在 S1 的配置、可执行程序和身份约束上，
  允许批准计划内的 index/objects/ref 修改，逐目标 Scope；禁默认签名、外部 filter/merge driver 和维护程序。
  任何工作区能选择的外部程序如无法安全排除，就保持该操作禁用。
- **S3 / local multi-effect**：进一步冻结工作树/index/refs 与阶段计划，
  管理冲突、清理与不确定结果；只有具备可验证隔离/快照约束时才执行不可信代码可改的配置。
  不调用任意 editor/rebase exec，不自动丢弃用户状态。没有这些前提就返回 blocked。
- **SN / approved transport**：另行证明最终 endpoint、协议、refs、凭证与连接约束。
  禁 ext/file/本地路径/自定义协议、未经批准的重定向、proxy/ssh/upload-pack/receive-pack/credential helper。
  在安全执行器中使用受信 transport，非交互认证；没有受控实现就 DENY，不回退系统 credential manager。

共同配置方案：为子进程单独构造环境，不改用户全局 Git；
禁止继承 GIT_CONFIG_* 注入、GIT_DIR/WORK_TREE/INDEX_FILE/OBJECT_DIRECTORY/SSH_COMMAND/EXEC_PATH 等调用者覆写。
显式阻断 system/global config 搜索（受控空配置文件与对应环境变量），隔离 HOME/XDG。
Hook 路径固定到服务端控制、不可由工作区写入的空目录，跨平台验证；不是让用户传 --no-verify。
对本地 config.worktree/includeIf、attributes、filter.*、merge.*、签名配置及 remote URL rewrite 做严格审查/约束。
不能声称 global/system 关闭就不读 local config，也不能以“一次扫描没发现”证明后续不被替换。

alias 需准确描述：固定 31 个内建子命令不等于能被同名普通 alias 任意覆盖；
Git 手册说明隐藏已有内建命令的 alias 被忽略（废弃命令别名有例外）。
风险重点是动态/未知子命令、! shell alias、remote/helper/filter 等程序入口，
因此不支持工具操作到任意用户 alias 的扩展，不声称本轮已复现 alias 攻击。

**审批 Git commit 不等于批准运行仓库 Hook。** trusted_local 默认也不据此自动运行 Hook。
需要 Hook 的真实开发流程应明确提示此兼容性变化；未来独立 Hook capability/隔离运行另行审批，
不在本轮设计中默默提供“可信本地例外”。作者/邮箱/签名缺失时等待可信配置，不虚构身份。

### 5.3 网络与凭证边界

fetch/pull/push/clone 不能只做一个字符串 URL allowlist 后交给宿主 Git 自由联网。
effective URL 包括 remote.url/pushurl、url.*.insteadOf、代理、redirect、协议 helper 与 DNS 解析结果。
拉取可能写 objects/refs，推送失败可能远端已接受；网络隔离及凭证授权要覆盖真正 transport。
复用现有 NetworkBroker/SecretBroker 可验证部分，不创立第二套网络授权；若 Git transport
无法受其约束，等待单独受控执行器方案/真实 L2 授权。**本轮无任何网络效果保证或网络测试。**

## 6. 取消、超时、非零退出及 UNKNOWN

当前 GitBroker._run 没有 timeout 参数；run_capture 的 timeout 默认 None。
工具回退有 30 秒 timeout，但返回普通 failure，不能据此证明无副作用。
run_capture 会在异常/取消时杀死并回收子进程，这不撤销已写 index/refs、Hook 或远端效果。
GitBroker 自身没有自动重试循环；不能据此推断 Agent/调用方不会再次发起，也不能说已复现自动重试。

建议每行操作共同使用以下效果合同：

| 阶段 | 可信结论 | 处理 |
| --- | --- | --- |
| 验证/授权前拒绝，可信未启动 | NOT_STARTED | 无 Git 文件/refs/网络副作用；需要新证据后再申请，不切宿主路径 |
| 明确执行完毕且收集结果/必要效果凭证 | COMPLETED 或已知失败及效果 | 返回真实结果；非零不自动等于无效果；只报告已观察到的 OID/状态 |
| 已启动或无法证明未启动，超时/取消/丢结果/复合动作部分完成 | UNKNOWN/UNCONFIRMED | 通过现有 ToolExecutionUnconfirmed 阻断自动重复；保留占用/证据交人工核对 |

write 行都要有限 timeout、输出限制、kill/reap 与真实开始证据。取消必须传播且先清理资源，
不能被泛型 catch 转成可自动重试的普通错误；不自动 reset/rollback 清理用户状态。
HEAD 未变、进程已结束、文件时间戳未变均不是“无效果”的充分证据。
RO 只有在 S1 已证明无外部副作用时才能设计有界重试，仍不可无限重试或升级后端。
如需要新的持久化执行 claim/人工 resolve，复用 NodeExecutionGuard/既有隔离 Git 协调器，
先提交新增范围，不能在 R04-B 初始小补丁中自行实现另一个队列或 Schema。

## 7. Mock 复现与兼容测试

### 7.1 已执行

既有隔离环境：Python 3.13.9 / pytest 9.1.1 / Ruff 0.16.8，不安装依赖。
最终 Git runner、_run_git、OS 进程创建均由 Mock/spy 替代；真实审批使用内存存储。
测试临时路径由 pytest 建立；未在任何真实 Git 仓库执行被测写命令。

| 证据目录 label | 选择 | Passed | Failed | Errors | Skipped | 原始 Python 退出码 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| r04b-original-git | 原调查 Git 31 委派 + remote/Hook 两项 | 7 | 26 | 0 | 0 | 1 |
| r04b-design-probes | 新探针初版 53 项 | 10 | 43 | 0 | 0 | 1 |
| r04b-design-final | 新探针含 4 个危险 operand、6 个中断、1 个 bool 对照 | 17 | 47 | 0 | 0 | 1 |
| r04b-compat-mocked | 原工具/scoping、两项无 Git 进程审批单测、A/A2 | 97 | 0 | 0 | 0 | 0 |
| r04b-review-final | 完成换行修正后，新 64 项与原 Git 33 项同次复验 | 24 | 73 | 0 | 0 | 1 |

表格选择存在重叠，不累计成完整 L1。新探针有一处 E501 仅换行修正后复验，断言没有改变。
完整 Ruff 0.16.8、git diff --check 均为退出码 0。最终复验测试前后文件哈希一致；
本段在复验后补写结果，没有改动已测生产源码或测试。
新增测试 SHA-256：`36713610cbb1426e20ef42cf1be8de84d957900dc9890cf62de42d16335b8cd4`。

新增 64 项测试归属：

- `test_every_operation_without_broker_must_refuse`：31 failed，逐项 Mock 显示宿主回退。
- inspect 参数 2、pull 参数 1：3 failed，委派丢失已指定的视图或 remote/upstream。
- 真正外层批准的 commit：1 failed，内层拒绝；不是放松审批，需匹配同一计划并保留真实身份。
- 已批准但文件在 denied_paths：1 failed，根资源判定不能替代文件授权。
- 无人值守来源旧精确记录：2 failed。探针主动构造并人工批准 source=cron/automation、无 session 的记录，
  证明直接 Broker 以默认 interactive 参数消费；**不代表标准无人值守入口会自动产生批准**。
- add option 文件 1、force/delete/wildcard/helper push 4：5 failed，只证明危险操作数到达 Mock，不声称 Git 一定接受。
- textconv 禁用、有限 timeout、UNKNOWN 分类、用户配置隔离：各 1 failed。
- 现有默认矩阵 8、标准 unattended 拒绝 2、委派中断无回退 6、CLI bool 不授权 Git 1：17 passed。

兼容旧测试 `test_git_tools_fall_back_when_broker_lacks_new_operations` 目前仍通过，
并明确期望不安全回退；未来 B1 要经授权改为拒绝合同，不能继续把它作为绿色兼容目标。
另两项裸工具构造 argv 测试也要改为显式受控 Broker/编译器测试，而非保留无 Broker 执行。
`test_git_broker_approval.py` 中会实际 git init/config/操作仓库的两项未运行；只选择其纯内存/静态两项。

### 7.2 原始证据与命令

仓库外证据根：
`C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/`。
每个 label 下有 command.json（完整实际子进程 argv）、pytest.log、junit.xml、summary.json、
source-before.json/source-after.json，测试时文件哈希一致。所有失败保留，不使用 skip/xfail。

启动器形式（从当前 R04 worktree 调用）：

```text
D:/dev/projects/sprout/.venv/Scripts/python.exe -I -S -B C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r03_worktree_checks.py <R04 worktree> <label> <具体测试文件或 node IDs>
```

未运行：真实 Linux/Windows Git helper/Hook 行为、网络/SSH/credential manager、真实仓库变更、
取消后的远端效果核对、Docker/Temporal、真实用户数据库、模型与部署、完整 L1。
Mock 不证明 native Git profile 的跨平台安全。Windows 原生崩溃/长路径历史风险未因此解决。
Python 测试保护层不构成 OS 沙盒。

### 7.3 本机离线 Git 官方资料（不是业务动态验收）

只读 `git --version`：2.54.0.windows.1。查阅本机随 Git 安装的手册，没有外网请求：

- `D:/dev/Git/mingw64/share/doc/git-doc/git-config.html`：alias（约 2008-2120）、
  fsmonitor（3164 起）、ssh/proxy、hooksPath（3687-3710）、credential.helper、protocol.allow（8081 起）。
- 同目录 `diff-options.adoc:822-838`：--no-ext-diff 与 --no-textconv 是不同控制。
- 同目录 `git.adoc:762-780`：system/global config 环境覆盖。
- 同目录 `git-push.adoc:61-105`：refspec 的 +、删除和通配符语义。

这些支持风险/设计依据，不代表已经在 Ubuntu 或真实恶意仓库中验证相应 profile。
实施时按锁定 Git 支持范围逐平台验证参数；不支持安全选项时拒绝，不删除检查继续跑。

### 7.4 操作到回归的归属与尚缺验证

两张操作表的**每一行**都对应以下两个可复核的参数化 node ID：

- 新 `test_r04_b_git_design.py::test_every_operation_without_broker_must_refuse[<inspect|write>-<operation>]`。
- 原 `test_r04_broker_audit.py::test_every_git_<inspect|write>_operation_obeys_injected_broker[<operation>]`。

此外，按实际风险归属（空缺不是通过）：

| 操作 ID | 当前额外证据 / 兼容测试 | 实施时必须补齐的差距 |
| --- | --- | --- |
| I01/I03 | 默认矩阵参数化测试；I01 中断不回退、有限 timeout 探针 | S1 环境/可选 index 写入、输出 Scope、真实平台验证 |
| I02/I04 | delegated_inspect filters；I04 textconv；旧 test_git_inspect_diff_builds_structured_command | ref/文件过滤恢复、签名/driver/no-lazy-network、同视图兼容 |
| I05–I10 | 当前只有通用缺 Broker/委派红色矩阵；旧 fallback 兼容测试覆盖 I09 | 新 Broker 方法、具体 operand/输出 Scope、配置与未知效果逐项测试；不得称为已有原生验证 |
| W01 | file operands 不得为选项、默认矩阵、委派中断不回退 | 显式 paths/index 及过滤器隔离、stage-all 拒绝 |
| W02 | 外层真实审批、denied 文件 Scope、旧来源记录、UNKNOWN；既有 git_broker_approval 的纯内存两项 | exact-plan 交接/父状态/index、原生 Hook/签名、部分成功审计；实际仓库测试本轮未运行 |
| W03 | pull remote/upstream 丢失、默认矩阵 | transport gate + ff-only 实际效果、pull 部分成功不可重试 |
| W04/W05/W21 | 旧 option remote；W05 新 force/delete/wildcard/helper 探针、旧 push argv 兼容 | 所有 remote/ref/目标路径/协议/凭证字段，网络 L2；未执行真实 fetch/push/clone |
| W06–W20（W13 另有 argv 兼容） | 通用 31 项矩阵；旧 push/restore argv 兼容覆盖 W13 | 每项选项/Scope、Hook/filter、预期 OID、破坏性默认拒绝和异常/取消/部分成功；不能用通用 Mock 代替这些细节 |

后续每项新 Broker 实现都应沿用“allow、DENY、需审批、缺身份/Scope、unsupported、
超时、异常、取消、执行后结果未知”的公共矩阵，并补该行特有的 operand 与效果断言。
本轮保留的 7 项已有委派对照通过，仅意味着 lower DENY 没有被回退，不意味着对应原生 Git 安全已完成。

## 8. 建议的最小实施批次（全部等待下一次授权）

| 批次 | 最小生产范围候选 | 目标与明确不做 | 验收/依赖 |
| --- | --- | --- | --- |
| B1，建议首先批准 | tools/system_tools.py + 对应工具测试 | 31 项缺 Broker 拒绝；未支持 operation 拒绝；删除工具层 Git 宿主回退。仅使用现有 7 项委派，不顺手补网络/策略放行 | 原 31 委派与新增缺 Broker红转绿；普通 Broker DENY/异常/取消不回退；明确调整三个旧 fallback 构造测试。剩余 Hook/身份风险继续 HOLD |
| B2，执行 profile | execution/git_broker.py + Broker 专用小 helper/测试（如需新增须批准） | 建立 S1 的可信 Git、隔离配置、Hook/helper/有界执行底线；不全局修改 git_env 影响 Apply/Worktree | 完整参数/环境 Mock、真实 POSIX/Windows 安全前提另行 L2；未证明配置隔离的能力不启用 |
| B3，动作/身份/审批/结果合同 | security/access.py、engine.py、GitBroker、ToolExecutor/context、system_tools；models.py 需单独确认 | 23 个加法式动作、逐动作默认策略、可信上下文、同计划单次审批、UNKNOWN 与审计；不改通用 ApprovalManager/Schema，若必须则暂停 | 无伪造 bool/ID、旧记录保守拒绝、跨任务/会话/命令/phase 重放拒绝、unattended 拒绝、单提示成功 |
| B4，逐个开启受控读取 | GitBroker + 两个工具/测试 | 先 status/diff，再 log/branch 与其余 6 项；恢复全部 filters 参数，补只读矩阵，不放出任意 argv | 每个操作 S1+Scope+输出限制+Mock；restricted 无执行器仍拒绝；不是一次全量 ALLOW |
| B5a，本地单效果 | GitBroker + 相应测试 | add、branch_create、tag_create 分别审核；文件集合、对象、ref 预期状态与 filter/Hook 禁止 | B2/B3；每项一个可审查小单元，不默认 stage-all |
| B5b，多效果本地写 | GitBroker 及确有必要的既有协调器适配（另批范围） | commit 单独一批；checkout/switch/restore 等按操作小批；merge/rebase/revert/stash 再分别审核 | B2/B3/B5a；不得携带用户 staged/dirty 修改；部分成功 UNKNOWN 不重试；如果需存储/Schema 必须追加授权 |
| B6，transport | GitBroker + 既有 Network/Secret 受控适配（另批范围） | fetch/pull/push/clone 分项；没有可证明 transport 前保持 N=DENY | 独立 endpoint/凭证/隔离合同、真实 L2 单独批准；不提前实施 R04-C Network 全面重构 |
| B7，破坏性例外 | 无默认启用计划 | hard reset、force/mirror/delete push、branch/tag 删除、stash_drop 等保持 DENY；确有产品需求先做独立决策 | 专用审批/保护 ref/expected OID/恢复证据方案，不因普通 git_write 批准绕过 DENY |

B1 仅是减少绕行面，**不等于 GitBroker 31 项全部安全可用**。B 的完成定义是每项有安全
实现或明确受控拒绝；不是强迫暂不具备执行器证据的网络/危险操作“功能全开”。

## 9. 兼容性、回滚与最终判断

- 明确破坏性兼容变化：裸构造 Git 工具不再执行；旧不完整 Broker 不再兜底；非法输入不再默默
  转 HEAD/origin/--all；Hook/签名/过滤器不再随 repo 配置自动获准；未完整绑定的旧审批不适用于新合同。
- 保留 31 个公开 operation 名称、正常参数意图和 8 个 Broker wrapper。支持能力缺失时返回明确拒绝，
  不把某种操作静默改成别的效果；补充可用性说明与版本化结果，避免 UI 将 blocked 误作 completed。
- 不重写历史审批、Git 数据、作者配置或用户仓库；不迁移真实数据库。
- 安全回滚：禁用不兼容的 Git 能力/保持拒绝，保留审计及未知占用；不能恢复 _run_git fallback、
  关闭 Scope、信任 metadata、启用全局 Hook 或将 UNKNOWN 当成功。用户修改不以 reset/clean 清理。
- 原 A/A2 修复、全部历史红色证据保留。当前生产代码没有改变。

**建议下一步仅审查/批准 B1；本轮设计不构成 B1 或其他生产实施授权。**
