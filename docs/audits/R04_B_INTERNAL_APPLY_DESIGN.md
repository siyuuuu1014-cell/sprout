# R04-B-internal-Apply — 只读方案预检

日期：2026-10-21。分支 `codex/r04-broker-zero-bypass`。
HEAD `33a7b06946a0b086a9078bb4893e4fd39f1262ed`。
状态：**HOLD；本文不是实施授权，也不是内部 Apply 安全验收。**

## 1. 基线、范围与证据口径

开始核验：分支/HEAD 符合预期，暂存区为空，没有已跟踪修改；九份原调查材料及 B-Final 报告均在，SHA-256 与前轮记录一致。已有未跟踪材料全部保留。本轮仅新增本文；没有源码、测试、策略、共享执行组件、审批或数据库变更。

证据为当前源码、现有测试内容、上一轮已存在的日志；没有运行 pytest、真实 Git 子命令、Hooks、Qoder、网络、原生集成或数据库。源码事实与待验证假设在下文分开。仓库外部插件/调用者无法静态穷尽，不能宣称远程未认证利用已经成立。

阅读合同：AGENTS.md；`PR_IMPLEMENTATION_ORDER.md:70–79`（零绕行/效果定义）、R05（OS 隔离）、R06/R06b（Action/Invocation 关联）；v1 产品方向与 v2.1 的 F03/F04 修订。不能因“内部”或“已有审批”忽略真实 I/O，不能把 UNKNOWN 当失败可重试。

## 2. 实际调用图与验证来源

```text
CLI project apply/rollback / remote RPC / Web ProjectGateway / MCP proposal_apply
  → Runtime.apply_change_proposal / rollback_change_proposal
  → Runtime._changes → ChangeProposalService.apply / rollback
  → ApplyBroker.apply / rollback → evaluate → _git / run_capture → host git

Task APPLY → NodeExecutor._apply
  → provenance + verification + approved checks
  → ChangeProposalService.apply (正常装配)
    或 ApplyBroker.apply (兼容直接分支)

Python embedding: exported ApplyBroker(policy, approvals=...) .apply/.rollback
  → evaluate (Policy/approval)
  → host git；不自动具备前面 service 的 verification gate
```

| 入口 | 当前实际证据 | 验证/身份边界 |
|---|---|---|
| CLI | `cli/commands/project.py:635` apply，`:726` rollback；force 参数必须有 reason；remote.py proposal API 走 RPC | 正常命令走 gateway/service；不是通用 GitWriteTool |
| Web | `web/webapi/routes/project.py:214` → gateway.apply_proposal；`gateway/project_gateway.py:205–228` → Runtime | 当前适用 Web 单操作者认证是接入边界；此预检不宣称该 API 为多租户隔离或重新审计整个 WebAuth |
| MCP/RPC | `mcp/server/tools.py:414–418`；`gateway/rpc_gateway.py:134–148` | 先找持久 proposal，再 Runtime；普通 apply 不自动 force。MCP 远端工具风险问题不是此内部 Apply 的修复范围 |
| Runtime | `runtime/runtime.py:385` 装配；`:2572–2608` 调 service、成功后 sandbox cleanup；`_changes:565` 附近装配 local applicability | 不直接调用退役 GitBroker；cleanup 失败也必须与“提交已发生”区分，不能把 API 异常当未执行 |
| Service ordinary apply | `runtime/changes.py:136–209`：task 存在、isolated replay 拒绝、APPROVED、proposal_verification_blocker、verification_evidence_applicability=local_match、取 task workspace/scope | 有 R01/R02 继承的来源和当前执行环境门禁；不是完整 Git HEAD/patch/commit 绑定证明 |
| Service force | `changes.py:159–194`：非空 reason，先保存 override 与事件，verified=False；仍要已批准且 Broker evaluate | 只覆盖验证 blocker；不能覆盖 Policy DENY 或缺安全执行器/不确定副作用。不能沿 force 路径恢复宿主 fallback |
| Service rollback | `changes.py:213–235`：task 存在、isolated 拒绝、APPLIED、workspace/scope，再 Broker | 复用原批准；不重跑当前 acceptance。回滚不应声称新状态验证通过，需绑定 recorded applied/base commit |
| Node Apply | `runtime/nodes.py:1153–1191`：isolated coding waiting；来源 applicability、APPROVED、blocker；已有 force-applied 不能算完成；service 或兼容 Broker 分支 | 两条节点路径都有当前已有门禁；但兼容分支不负责 service 的状态持久化，原 ApplyBroker 自身不重做验证 |
| 直接嵌入 | `execution/apply.py:39–86,158–166`；`execution/__init__.py` 公共导出 | evaluate 只 Policy + `{proposal_id,risk}` 及 task 审批；不检查 proposal.status/verification provenance。不接受“所有实际入口都有可信验证来源”的结论；也不把任意 Python 能构造对象等同外部攻击者可直接利用 |

结论：**正常 service/node 路径有来源门禁；公开独立 Broker 入口并非自证已验证。** 后续统一安全入口不能只加一个 caller-supplied `validated=True`。真实服务端入口必须绑定已持久化 proposal/task/workspace 与当前内容；独立实例缺该上下文应拒绝普通 apply，而不是默认为可信。如何保留显式 force 的可信上下文需要单独审查，不改写历史记录。

## 3. 当前执行序列与风险证据矩阵

`apply.py:73–156`：evaluate → 拼接 diffs → 空 patch 直接 applied=True → 写临时 patch → git apply --check → rev-parse HEAD → git apply → git add → git commit → rev-parse HEAD → 返回 → finally 删除 patch。

| 风险 | 源码事实 | 影响/证据不足 |
|---|---|---|
| 广泛暂存 | `apply.py:115–121`：files_changed 非空时 add -- files；**空元组时 add -A** | 会覆盖整个 index 范围的变更集合，而非只按 patch 范围；可能纳入任务外 tracked/untracked/deletion。gitignore 可能排除部分 untracked，但不能作为安全边界 |
| 既有 Index 污染 | `apply.py:137` 普通 commit -m，无私有 index/提交树限制 | 即使只 add 指定文件，其他预先 staged 文件仍留在 Index 并可能提交。命中文件上已有用户未提交内容也可能被 add 一起收录。不是“改成精确 add 就解决” |
| 文件列表与 Patch 可分离 | ChangeProposal `models.py:117–130` files_changed/diffs 独立字段；Broker 不证明二者相等 | files_changed 可漏报/多报；rename/delete/mode/symlink/gitlink/二进制/异常路径都需验证，不靠只按文本 diff header 猜路径 |
| Approval 绑定粒度 | `changes.py:106–115` 与 `apply.py:50–66` 只 `{proposal_id,risk}`, task；single_use=False | fingerprint 不是 patch/files/HEAD/target/index/executor 指纹。相同 id/risk 不自证内容未变；复用用于 apply/rollback 的现有产品合同需保留但限制到同一冻结内容 |
| Identity/source | evaluate ActionRequest 没有显式 actor/source；scope 是 workspace root 级 resource；ApprovalManager lookup 只给 task | 外层 operator 信息不等于已经贯通每个 Broker 执行。approval.py:248、278、355–447 有指纹与条件匹配；此调用没有传 session/source/requester。未绑定 session 的旧记录不因此获得更强身份保证 |
| Verification 对象 | `verification.py:166` 取 report.blocker；`acceptance.py:132–155` 只接受已知 Local binding 当前匹配 | 保留这些必要门禁；此处没有把目标主仓库当前 HEAD/index/patch tree 与已运行的 sandbox 证据做原子比较。Local launcher 文档明确非恶意代码沙盒 |
| 时间窗口 | apply --check、HEAD 读取、apply、add、commit 分开 await | concurrent 用户/Git/候选进程可在之间改变配置/index/HEAD/目标文件。前置扫描+后置 hash 只能检测部分变化，不能取消已发生 helper/文件副作用 |
| 临时 patch | `_write_patch:244` NamedTemporaryFile，随后按路径提供 Git，finally unlink | 文件创建具有限制并不等于整个执行期间不可替换；需拥有稳定句柄/权限/受控目录。异常时删除 patch 可能减少恢复证据，应保留受控 digest/诊断而非泄露全文 |
| 回滚 | `apply.py:170–185` revert --no-edit metadata.applied_commit；旧记录走 apply -R:187–215 | 无 full OID/parent/current HEAD 一致性检查，旧参数可能缺失/歧义；service 限制 APPLIED，但不证明当前仓库仍是该结果。原 no-hard-reset 行为必须保持 |
| 失败补偿 | `_undo:234–241` restore --staged 指定 paths，然后 apply -R；调用者不检查补偿返回码 | 可能清除用户原 staging，反向 patch 也可能失败；非零 commit/Hook 失败不自证未产生提交。不得据此报告“工作区已恢复”或无条件重试 |
| 空 patch 与观察失败 | 空 patch applied=True；`_head_commit:227` 非零返回空串 | 空 diff 不表示待提交的 index 安全；成功但记录 commit id 失败不能当作完整交付证明。上层只在 result.applied 后记录状态，崩溃可能留已提交/PENDING 或 APPROVED |

以上为静态可达结构，未运行污染提交或真实竞态；需要下文 Mock RED 与后续原生证据。不能凭这些事实宣称已经复现真实用户仓库破坏。

## 4. 精确暂存 / Index 威胁模型与建议约束

### 威胁对象

1. 用户在任务外文件预先 staging，或本次 task 文件中混入用户工作区编辑。
2. proposal files_changed 为空、多报、漏报或与 Patch 不符；已有 Index 包含冲突/unmerged/sparse/submodule/特殊 entry。
3. 主 HEAD、refs、index、工作区、attributes/config 在 preflight 后变化；同一 proposal 并发 Apply。
4. Git helper 依据 attributes/config 转换 patch/暂存内容，或 hook 在 commit 前后改写 index/refs；审批只看过原 diff。

### 初期保守合同（候选，不是已实施）

- 非空 patch 无精确可验证文件集 → **拒绝**，移除 add -A 兜底；空 patch 只可返回明确 no-op，不自动提交 Index。
- 不采用 commit pathspec、commit --only 或先保存/清空用户 Index 作为临时修补：这些有不同 Git 语义，不能证明提交等于获批 patch，也可能改动用户 staging。
- 首个可用性收窄方案可要求目标 HEAD 固定、用户 Index 清洁、目标工作区无无关改动；不自动清理/重置。**这是准入条件，不是并发安全证明。** 重叠用户变更应拒绝而非猜测合并。
- 后续由可信执行器在**稳定受控视图**构造独立 index（基于已授权 parent tree、应用准确 patch），计算预期 tree 并核对路径/OID/mode；最终 commit 应只有该 parent/expected tree、批准身份/消息。该步骤须排除 filters/helper、保留 rename/delete/文件模式语义。
- 私有 GIT_INDEX_FILE 只隔离一个 Index，不能隔离共享 config/object DB/refs/worktree/attributes，也不锁住用户主目录；绝不称为完整修复。
- 在受控实验副本上验证成功，不自动允许更新主仓库。主仓库发布必须有可信 exclusive ownership/OS 受控目录或等价可证明保护，以及 HEAD 的原子条件更新和结果观察；普通 Git 锁文件不能排除任意同用户文件写入者。
- 不实现“第二套提交协调器”。若现有事务组件能满足 Local Apply 的语义，应复用；否则停止申请现有接口扩展，而不是另建不兼容 ledger。

## 5. Git 安装、配置与 Hook/helper 安全边界

当前 `_git:218` 和 legacy rollback:195 都启动裸 `git` + 共享 git_args/git_env。`git_env.py:19–23` 明确保留 Hooks，`:47–52` 只固定 fsmonitor=false/pager=cat。SecretBroker whitelist 保留 PATH/HOME/USERPROFILE/XDG 等，未固定 system/global config；用户家目录中凭证/config 仍可能被 Git 或其 helper 读取。

实际可能性（静态来源，不等于每次子命令必触发）：

- commit/revert 受 local/global/system config、include/includeIf、core.hooksPath、签名程序、commit 模板/editor、自动 maintenance/gc 等影响；非交互环境并不能禁止所有程序。
- add/checkout 类处理可能用 clean/smudge/process filters、working-tree-encoding、attributes；提交实际 tree 可不同于原 patch。fsmonitor 的一个 pin 不屏蔽这些来源。
- attributes/config/对象库可指定外部路径、alternates、promisor/partial clone 或子模块上下文；读取缺失对象可能触发隐式取对象。Apply 不显式 push/pull，不等于零网络。
- pager=cat/editor=true 仍是执行程序名称，安装依赖与 PATH 也需证明可信。禁止按首次发现 Git 的 hash 或“workspace.kind=git”签发身份。

建议新增 **Apply 专用执行适配**，不改共享 git_env/proc，不借用 B2 未签发 S1：

1. 由服务端安装权威明确选择绝对 Git 安装及 loader/helper 依赖身份，核验版本、保护目录；无证据拒绝，不 PATH fallback。
2. 由可信受控仓库生命周期证明执行期间的配置、attributes、对象库、refs、index/工作区稳定及 Scope 可见范围。不得只扫描 config 后在可变原仓库执行。
3. 独立白名单环境，固定无凭据 HOME/TEMP、隔离 system/global config；固定 hooks 空目录、禁用签名/editor/pager/fsmonitor/auto-maintenance、协议默认拒绝/no-lazy-fetch；对 local includes、filter、attributes、alternates/promisor 等没有完整排除证明则拒绝。命令 pins 和 env 只是纵深防御。
4. 只允许 Apply/rollback 所需固定 argv 模板及明确 operand 边界；不暴露任意 `git` runner；reject 非完整/不匹配 OID、歧义路径/特殊对象。
5. 在没有受控视图与 OS 保护的当前环境，可以交付 default-deny executor framework，但必须明确真实 Apply 将 HOLD。不能以可用性为由恢复旧 runner。

**重要可用性裁决：** 单改 apply.py 增加若干 -c、timeout 或私有 index，无法同时承诺完整 helper 排除、原主仓库无竞态、功能完全不变。执行能力恢复必须另批可信签发与环境建立，不能把本设计当作已有能力。

## 6. 执行效果、UNKNOWN 与恢复协议设计

### 当前事实

`proc.run_capture:52–85` timeout 默认 None；Apply 所有调用未传 timeout；无界 communicate。异常会 terminate；Windows taskkill /T 是 best-effort，POSIX 仅 direct child kill，没有完整进程组保护。terminate 的 drain/wait 没有统一上限。spawn await 在 try 之前；启动阶段取消/异常不能由当前 Apply 自证未启动。返回 `process.returncode or 0` 不是 unknown 的独立类型。

ApplyResult (`models.py:133–139`) 只有 applied bool/reason/base/applied commit，没有 effect/cleanup/attempt identity。非零 git/异常可能发生于部分 apply/add/commit/Hook 之后；service 未记录 APPLIED 不能证明没有提交。Node `_apply:1192` 对未落地抛异常，注释指向 FAILED→RETRYING；没有 Apply durable claim 时，重试不得以“FAILED”作为安全依据。

### 候选阶段与语义（不是新增平行状态机）

| 阶段 | 必要证据 | 失败时语义 |
|---|---|---|
| NOT_STARTED | 所有身份/验证/Scope/批准/执行器条件失败且可信层尚未尝试启动或写文件 | 可明确拒绝；对未改变绑定的重新请求需重新检查，不自动审批 |
| CLAIMED / PREPARED | 现有权威事务成功领取，冻结 proposal+task+target+base+patch+files+verification+executor 绑定 | 崩溃保留 claim，不能超时自动回收再执行；不能说 Task 已成功 |
| WRITE_STARTED | 持久 one-shot write marker 在首个资源副作用前成功 | timeout/cancel/nonzero/观察失败均保守 UNCONFIRMED，除非有独立可验证终态 |
| OBSERVED / SETTLED | 当前仓库 observed OID/parent/tree 与冻结结果匹配，状态持久成功 | 才能报告 applied；报告或 cleanup 失败与 Git 结果分别保留 |
| UNCONFIRMED | 启动状态、效果、清理或持久化任一不能证明 | 禁止自动重试、自动补偿、接管 claim；人工核对或显式重新发起新计划，不吞异常伪装 ordinary failure |

- 有限 spawn/command/cleanup deadlines，stdout/stderr 合计限额，取消保持传播并携带不确定性；kill/reap 成功不等于无副作用。
- 不自动 `_undo` 当作安全回滚。变更补偿本身必须是绑定和授权的独立恢复决定；无法证明完整逆变换则保留现场，不 reset --hard。
- 现有 `MetadataStore` 已有 get_git_proposal_snapshot、claim_git_transaction、begin_git_commit_write、settle_git_commit_proposal (`storage/contracts/metadata.py:78–91`)；目前对应 isolated coordinator 的精确合同，**不能未经核验直接给普通 Apply 使用**。优先评估语义、后端支持和 CAS 绑定；若缺字段/迁移需单独授权。其他后端不支持时 fail-closed，不回退内存锁。
- 仅增加 ToolExecutionUnconfirmed 异常可以阻止伪成功，但**不能独自阻止重启后调用者重试**。安全上线需要现有 durable 状态、公开结果和节点恢复都理解该语义。
- ApplyResult 如需兼容加法字段，必须版本化含义/旧构造器默认 unknown、对公开 API 的读取/序列化兼容测试；本轮不批准修改该模型或 Schema。

## 7. 最小生产补丁候选与批准顺序

以下是待单独审批的范围，不是本轮改动，也不能一次性宣称全修复：

| 批次 | 候选文件 | 最小目标 / 阻塞 |
|---|---|---|
| A / 先 Mock RED + bounded scope | `execution/apply.py` + 专项测试 | 证明 add-A、既有 Index、空 patch、目录级 Scope、直接未验证调用和 mutation timeout 的当前边界。先写不执行 Git 的回归，再选拒绝条件；不修改共享组件 |
| B / Apply 执行适配 default-deny | `execution/apply.py`、新增专用 `execution/apply_git.py`（名称待批）+ 测试 | 双入口 apply/rollback 无安全安装/视图时统一拒绝；固定操作计划、有界生命周期、无备用宿主；先交付框架时要承认真实功能 HOLD |
| C / 可信冻结准入与恢复 | `runtime/changes.py`、`runtime/nodes.py`；必要时 `execution/models.py`，现有 metadata 事务合同经评估后另批 | service/兼容 node/独立 Broker 一致验证来源与当前执行计划；通过服务端 capability/持久 snapshot 而非普通 metadata 授权。保留 force audit verified=False；持久 uncertain 阻断重试。若 storage 修改必要，先停报申请 |
| D / 实际可用性恢复 | 明确受控工作区/OS 执行器提供者，另行范围审批 | 安装/环境/稳定视图与发布条件证明；复用现有 Apply 和 isolated 事务思路，不恢复通用 Git，不启用 B2 两个 resolver |

不能承诺只改 apply.py 就覆盖跨 Runtime、重启、一致审批和 OS 攻击者。全局 GIT_COMMIT、ApprovalManager 通用规则、共享 git_env/proc、内部 Sandbox 不在建议首批修改中。

## 8. 需要先编写的 Mock RED（本轮未新增或执行）

所有最终 Git、文件副作用、helper、安装探测和网络 Mock，存储使用内存或临时测试 SQLite。既有断言不得弱化。

1. 非空 diff + files_changed=()：拒绝且绝不 add -A/commit；空 diff 不提交预 staged 内容。
2. 精确 files_changed 但 index 含 unrelated staged 修改/新增/删除：不得提交污染；目标文件已有非任务修改也拒绝。
3. patch/files 不一致、rename 两端、delete、mode、symlink/gitlink/binary/path traversal/option-like operand；无可信解析/支持能力则零启动。
4. Policy DENY / Scope denied file / Scope 过期（首授权后亦变化）均在对应副作用前拒绝；不能只检查根目录后放行任意 path。
5. 同 id/risk 改 patch、task/workspace/HEAD/index/verification/executor 变化，旧批准不能适用；正常同一冻结计划的 apply→rollback 复用保留；历史记录不补写新身份。
6. 独立 Broker 缺可信服务端验证来源拒绝；service/node 两分支一致；force 有 reason/audit 仍 unverified，不绕过执行环境或 Policy。
7. 未授权安装、可变配置视图、hooks/filters/include/global/system/attributes/signing/helpers/promisor 对象任一不明均零启动；不能借单次扫描通过。
8. spawn timeout/异常/取消、输出超限、执行超时、cleanup 失败 → 正确区分可证未启动与 UNCONFIRMED；无 runner retry、无 `_undo`/reset fallback。
9. apply 已写/commit 已产生但非零、HEAD 观察失败、APPLIED 持久化失败、Runtime cleanup 异常 → 不伪报未执行或重新提交。
10. 两 Runtime 并发/重启复用同 proposal 只允许一次 write claim；领取成功后崩溃保守拒绝，不能拿 asyncio.Lock 当跨进程保证。
11. rollback 缺 full OID、parent/base/current HEAD 变化、冲突、hook 后结果不明 → 保留现场、零 hard reset；旧 patch rollback 缺可靠边界时明确拒绝/人工处理。
12. 保留 R01 Local provenance/unknown/mismatch、force/Task 非完成、R02/R03 与 A/A2/B1/B2/B3 退役回归；不要用修改 Policy 或删旧断言制造绿色。

现有测试参考（只读存在性，不等于本轮通过）：`test_apply_commit.py` 3 项中两项有真实 init/config/commit fixture，本轮 not_run；其 failed_revert Mock 断言保留；`test_local_acceptance_apply.py`、`test_legacy_acceptance_backend_boundary.py` 直接/节点/后端变化安全门禁；`test_r04_b3_min_closure.py` 内部 Apply/Proposal/Sandbox Mock 对照。上一轮 internal-mock 79 passed/exit0 和关联492 passed/exit0 是历史选择集，不是本轮或完整 Apply 验收。

## 9. 原生 Windows/POSIX 验证计划（另行授权后）

- 两平台分别使用锁定环境、可信安装、全新隔离测试仓库与配置 HOME；不得指向项目/用户仓库，不继承凭据。原生进程运行、测试 hook/helper sentinel 即便无害也需单独许可，本轮禁止。
- 检查文件模式/大小写/Unicode/含空格路径、符号链接/junction、Git-dir/worktree 链接、Index 格式与换行转换。不可运行平台记 not_run；不修改 skip 达成绿色。
- 受控恶意配置/attributes/filter/include/signing/pager/fsmonitor/remote-helper/promisor/submodule/alternates 矩阵；只有在 OS 约束能阻断外联且 helper 测试经批准时执行；否则不通过真实网络探测猜测安全。
- 验证已 staged unrelated 内容、并发 index/refs/config 替换、准确 parent/tree/patch、正常 add/delete/rename/二进制提交和 recorded rollback；保留用户 staging 不被清理。
- timeout/cancel/output/descendant tree/崩溃持久化故障矩阵；检查资源关闭、未知状态阻断重试，而非仅断言收到异常。
- 定向 Mock 与原生证据后才申请完整 L1；既有 Windows 原生崩溃、长路径、symlink skip、外部58 not_run 仍单列，不能用本设计消除历史风险。

## 10. R05/R06 边界、兼容与回滚

**B-internal-Apply**：固定动作计划、Scope 及源/产物比对、执行器准入、无回退、效果分类、沿用现有审批/验证入口。**R05**：进程/文件系统/网络隔离、同用户任意写入者及 loader/helper 保护、可信镜像/服务配置与原生运行证据。**R06/R06b**：跨入口 action/task/session/source/principal/executor/patch 关联和持久重启/并发语义；不能无限延期当前可达副作用的基本 fail-closed。若 B 必须依赖这些能力，保持 HOLD，不先上线声称已安全。

兼容影响须明确：拒绝 dirty/index 污染、无文件集、历史无冻结来源、无法证明安装/视图的操作，可能中断现有直接 Python/force/旧 patch rollback 或要求重新审阅。禁 Hooks/filters/signing 会改变用户仓库预期；不能静默禁用后声称行为完全不变，也不能为兼容再执行未经授权 helper。普通 apply/rollback 的意图和单次人工审查体验保留，但批准必须针对同一冻结内容，不能继续宽松复用到新 patch。API 名称可以兼容，执行可用性需显式等待/拒绝。

force 仅覆盖已批准的验证 blocker，不自动授予 Git 配置/helper、网络或恢复权限；历史验证不改写。rollback 仍为显式已有操作，不用 reset/clean/checkout 覆盖用户文件；模糊状态仅人工 reconciliation。

安全回滚：禁用尚未验证的专用适配，保留所有记录和现场；不重新开放裸 host `_git`、add-A、通用 GitWriteTool/GitBroker、旧宽审批或自动 `_undo`。如部署后有不确定尝试，先按现有权威事务人工核对，不删 claim/迁移数据以“恢复成功”。本轮只有文档，可撤回文档而不改变执行。

**结论：尚无可直接批准的一文件完整修复。建议先审批 A 的 Mock RED 范围和 B 的 default-deny 适配设计；在实际执行身份、受控视图、冻结绑定及持久不确定性接口范围获批前，真实内部 Apply 保持 HOLD。未自动开始任何实施。**
