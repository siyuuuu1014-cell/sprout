# R04-B：最小 Git 能力需求收敛审查

审查日期：2026-10-21。源码基线：`8750ba55224222c2fb26799941905ba79e62c0f5`，分支 `codex/r04-broker-zero-bypass`。
本轮只读源码/测试/文档，唯一新增本文件；没有执行 Git 工作流、pytest、模型、数据库、网络或外部 helper。
缓存 `origin/dev=9258289a3bb3488bd19f2491b765369f0c407090`，**未联网核实远端，也未 fetch/pull**。
本文件是建议，不是 B3–B7 实施授权；不更改 Schema、默认策略、审批或历史红色测试。

## 1. 结论与分类口径

**不需要为了完成 Sprout 核心编码闭环而恢复 31 项通用模型 Git 操作。**

- 下表对“通过 `git_inspect` / `git_write` 开放的公开 operation”分类，不对 Git 子命令的所有内部用途一刀切。
- P0 当前必需：找得到核心工作流必须依赖该公开入口的调用链。**本次没有找到这样的操作：0 项**。
- P1 有用但可延后：7 项只读公开能力，可提供诊断/导航，但没有发现它们是 Task/Proposal/Apply 的硬依赖。
- P2 保持拒绝：24 项公开能力（全部 21 项通用写操作及 3 项额外检查操作），近期没有足够收益支持新增执行/授权面。
- 第 4 节另列**内部 P0**：状态/补丁收集、精确文件暂存、提交、HEAD 身份、生命周期和回滚。不能因其公开版本为 P1/P2 就删除内部实现。
- “未发现固定生产消费者”仅指本轮静态检索范围内无必经调用，不表示注册后模型不能请求，也不证明所有外部嵌入程序从未使用该 API。
- P2 是**建议的最小合同**，不表示这些入口在当前 HEAD 已全部 DENY。当前 `GitBroker.commit/push` 及自定义 Policy 下的其他执行路径仍需单独处理。

历史设计中的“所有 Git 操作受控”可以通过明确拒绝满足，不等于每项都要实现执行器。
依据：[R04 原始 DoD](PR_IMPLEMENTATION_ORDER.md#r04--动作清单与-broker-零绕行)；历史 v2.1 的框架建议不能覆盖当前源码与本轮收敛要求。

## 2. 源码证据索引

以下行号针对上述 HEAD；本文件后续维护时应重新核对，而非永久冻结结论。

| 编号 | 当前源码/测试位置 | 观察到的事实 |
|---|---|---|
| G1 | [tools/system_tools.py:625](../../src/Sprout/tools/system_tools.py#L625)、[695](../../src/Sprout/tools/system_tools.py#L695) | Inspect 声明 10 项；仅 status/log/branch/diff 委派，其他直接拒绝；无 Broker 拒绝 |
| G2 | [tools/system_tools.py:725](../../src/Sprout/tools/system_tools.py#L725)、[813](../../src/Sprout/tools/system_tools.py#L813) | Write 声明 21 项；仅 add/commit/pull 委派，其他拒绝（包括 push），并非 21 项都能执行 |
| G3 | [runtime/runtime.py:396](../../src/Sprout/runtime/runtime.py#L396)、[421](../../src/Sprout/runtime/runtime.py#L421)；[tools/registry.py:200](../../src/Sprout/tools/registry.py#L200) | Runtime 注册有 Broker 的通用工具，裸 registry 构造无 Broker；注册/公开不是固定业务调用 |
| G4 | [execution/git_broker.py:42](../../src/Sprout/execution/git_broker.py#L42)、[185](../../src/Sprout/execution/git_broker.py#L185)；[security/engine.py:118](../../src/Sprout/security/engine.py#L118) | status/diff 接 S1；log/branch/add/pull 默认 DENY；commit/push 默认 REQUIRE_APPROVAL；其他方法仍用原 `_run` |
| G5 | [execution/git_s1.py:56](../../src/Sprout/execution/git_s1.py#L56)、[62](../../src/Sprout/execution/git_s1.py#L62) | 安装与视图 resolver 均返回 None；实际 S1 读取能力仍 HOLD |
| C1 | [strategy/execution.py:11](../../src/Sprout/strategy/execution.py#L11)；[runtime/nodes.py:308](../../src/Sprout/runtime/nodes.py#L308)、[423](../../src/Sprout/runtime/nodes.py#L423)、[456](../../src/Sprout/runtime/nodes.py#L456)、[626](../../src/Sprout/runtime/nodes.py#L626)；[context/builder.py:220](../../src/Sprout/context/builder.py#L220) | 核心计划/编码显式传入节点自己的 sandbox 文件工具及 `sandbox_git_inspect`，不是以通用 git_write 作为交付步骤；此处不是所有动态/自定义工具调用不可达的证明 |
| C2 | [runtime/nodes.py:714](../../src/Sprout/runtime/nodes.py#L714)、[990](../../src/Sprout/runtime/nodes.py#L990)、[1029](../../src/Sprout/runtime/nodes.py#L1029)、[1153](../../src/Sprout/runtime/nodes.py#L1153) | 建 sandbox、按变更文件构造检查、生成 Proposal diff/files、通过 Changes/Apply 落地；没有调用通用 GitBroker 来完成这些阶段 |
| C3 | [sandbox/git_worktree.py:54](../../src/Sprout/sandbox/git_worktree.py#L54)、[76](../../src/Sprout/sandbox/git_worktree.py#L76)、[113](../../src/Sprout/sandbox/git_worktree.py#L113)、[189](../../src/Sprout/sandbox/git_worktree.py#L189)、[225](../../src/Sprout/sandbox/git_worktree.py#L225) | 内部 worktree add -b、add -N、diff、status -z、受限根目录清理和专用分支删除 |
| C4 | [execution/sandbox_tool.py:647](../../src/Sprout/execution/sandbox_tool.py#L647)；[tests/test_isolated_git_tool.py:7](../../src/Sprout/tests/test_isolated_git_tool.py#L7) | 独立工具仅 status/diff/log；Local 可直接运行 Git；`allow_host_git=False` 时明确拒绝，不提供宿主回退 |
| A1 | [runtime/changes.py:94](../../src/Sprout/runtime/changes.py#L94)、[136](../../src/Sprout/runtime/changes.py#L136)、[213](../../src/Sprout/runtime/changes.py#L213)；[execution/apply.py:73](../../src/Sprout/execution/apply.py#L73)、[158](../../src/Sprout/execution/apply.py#L158) | 已有审批、验证来源/适用性门禁；ApplyBroker 自己执行 apply/add/commit 和 rollback，不依赖 git_write 工具 |
| A2 | [execution/apply.py:227](../../src/Sprout/execution/apply.py#L227)、[233](../../src/Sprout/execution/apply.py#L233) | HEAD 用固定 rev-parse；失败补偿仅对指定路径 restore --staged 和反向 patch，不用 reset --hard |
| E1 | [execution/isolated_git_plan.py:30](../../src/Sprout/execution/isolated_git_plan.py#L30)、[46](../../src/Sprout/execution/isolated_git_plan.py#L46)、[65](../../src/Sprout/execution/isolated_git_plan.py#L65) | 成长实验内部计划使用固定 rev-parse/status/ls-files/diff/rev-list；rollback_plan 只生成计划 |
| E2 | [execution/isolated_git_coordinator.py:75](../../src/Sprout/execution/isolated_git_coordinator.py#L75)；[execution/isolated_git_writer.py:16](../../src/Sprout/execution/isolated_git_writer.py#L16)、[49](../../src/Sprout/execution/isolated_git_writer.py#L49) | 内部提交协调器已有精确审批、持久化 claim、单次写入及 uncertain 路径；writer 用 apply --index/commit，并用固定 show 校验作者/消息；不是通用写工具 |
| E3 | [evolution/replay_runner.py:136](../../src/Sprout/evolution/replay_runner.py#L136) → [evolution/isolated_replay.py:40](../../src/Sprout/evolution/isolated_replay.py#L40) | 显式实验准备从本地源 clone --no-local --no-checkout，再 checkout --detach 已验证 commit；不调用 GitWriteTool.clone/checkout |
| E4 | [sandbox/isolated_git_worktree.py:11](../../src/Sprout/sandbox/isolated_git_worktree.py#L11) | 现有 isolated 管理器校验根和 worktree backlink、禁 filter 等；不是 B2 要求的不可变仓库视图/可信安装签发器 |
| S1 | [skills/sources/github.py:150](../../src/Sprout/skills/sources/github.py#L150)、[162](../../src/Sprout/skills/sources/github.py#L162)；[skills/broker.py:320](../../src/Sprout/skills/broker.py#L320)、[342](../../src/Sprout/skills/broker.py#L342) | Skill 安装的独立 source.fetch 路径确实执行网络 sparse/partial clone；这是 Skill 供应链消费者，不是 GitWriteTool.clone 的需求或安全背书 |
| T1 | [tests/test_tools.py:168](../../src/Sprout/tests/test_tools.py#L168)、[214](../../src/Sprout/tests/test_tools.py#L214)；[test_r04_b1_git_no_fallback.py](../../src/Sprout/tests/test_r04_b1_git_no_fallback.py) | Schema 枚举/拒绝/Mock 委派测试证明接口和 B1 合同，不证明产品需要 31 项执行能力 |
| T2 | [tests/test_task_e2e.py:137](../../src/Sprout/tests/test_task_e2e.py#L137)、[295](../../src/Sprout/tests/test_task_e2e.py#L295)、[409](../../src/Sprout/tests/test_task_e2e.py#L409) | 现有 Task E2E 验证审批、验收和只提交 Agent 改动，模型通过 sandbox 文件工具修改，不需要通用 git_write；本轮未重跑 |
| T3 | [tests/test_apply_commit.py:55](../../src/Sprout/tests/test_apply_commit.py#L55)、[126](../../src/Sprout/tests/test_apply_commit.py#L126) | 明确覆盖 Apply/rollback（旧 fixture 的 force override 不代表普通验证通过）；失败 revert 不 reset 的测试是安全不变量 |
| T4 | [tests/test_sandbox_lifecycle.py:100](../../src/Sprout/tests/test_sandbox_lifecycle.py#L100)、[180](../../src/Sprout/tests/test_sandbox_lifecycle.py#L180)、[207](../../src/Sprout/tests/test_sandbox_lifecycle.py#L207)、[242](../../src/Sprout/tests/test_sandbox_lifecycle.py#L242) | 清理专用分支、保留待审批 sandbox、过滤验证缓存的内部需求已有测试 |

检索覆盖 `src/Sprout`、`web/webapi` 的 Git 工具名、Broker 引用、固定 Git argv、任务与 Proposal 入口，并只读核查上述相关测试。
“未发现”不覆盖插件、用户自编 SDK 或 LLM 运行时动态选择；普通模型聊天依然可能请求已注册工具。
`fetch/pull` 的字符串也用于数据库/Docker；CLI chat 的 restore 是数据库菜单，不应误记为 Git 消费者。

## 3. 31 项公开 operation 分类

本表的 P0/P1/P2 **仅决定是否需要恢复对应的公开工具执行能力**。内部必要性另见第 4 节。
“现状”指当前默认策略/工具接线，不表示自定义 Policy 或直接嵌入 Broker 都被封闭。

| # | 工具 / operation | 类别 | 当前接线及实际生产证据 | 保留/延后理由 |
|---|---|---|---|---|
| I01 | Inspect / status | P1 | G1→G4→G5：默认 S1 拒绝；C2/C3 内部另有 status | 诊断有用；主图不依赖通用入口，不能拿内部 P0 证明公开 P0 |
| I02 | Inspect / log | P1 | G1→GitBroker.log 默认 DENY；C4 是另一独立 sandbox log | 有已有模型历史查看场景，但无固定必经调用；延后有界只读适配 |
| I03 | Inspect / branch | P1 | G1→GitBroker.branch 默认 DENY；G1 描述/枚举有分支查看 | 可选导航；C3 的专用分支创建/删除不等于必须公开 branch 列表 |
| I04 | Inspect / diff | P1 | G1→G4→G5：默认 S1 拒绝；C2/C3、Proposal 展示另有数据 | 交互诊断有用；不需要恢复通用 diff 才能构造/展示 Proposal |
| I05 | Inspect / show | P1 | G1 仅声明，B1 拒绝；E2 内部固定格式 show | 元数据审阅场景合理且有内部用例，但不可把内部固定 argv 扩成任意公开 show |
| I06 | Inspect / remote | P2 | G1 仅声明，B1 拒绝；E3/S1 的获取仓库不是 remote 列表消费 | 无固定消费者；URL 及凭证信息风险，近期不建设公开 remote 系统 |
| I07 | Inspect / tag | P2 | G1 声明 tag 列表，B1 拒绝；未发现固定产品调用 | 无标签发布/检索工作流证明；保持拒绝 |
| I08 | Inspect / stash_list | P2 | G1 仅声明，B1 拒绝；C3 使用专用 worktree 而非 stash | 不需要读取操作者 stash 才能编码/交付，暂不暴露 |
| I09 | Inspect / rev_parse | P1 | G1 声明但 B1 拒绝；A2/E1 内部固定 HEAD/commit 解析 | 身份诊断可用；公开任意 ref 解析不是内部版本绑定的依赖 |
| I10 | Inspect / ls_files | P1 | G1 声明但 B1 拒绝；E1 固定查询 untracked | 可选文件导航；已有 sandbox 文件工具，实验内部查询不需第二条公开路径 |
| W01 | Write / add | P2 | G2→GitBroker.add 默认 DENY；C3 add -N、A1 精确文件 add | 必要的是受控暂存实现，不是模型自由暂存用户 index |
| W02 | Write / commit | P2 | G2→GitBroker.commit 需审批；A1/E2 已独立提交 | 通用入口和既有 Proposal/事务并列，增加未经相同验证的第二条交付路径；建议禁用而非建全通用审批 |
| W03 | Write / pull | P2 | G2 仍委派，GitBroker.pull 默认 DENY（G4） | 未发现核心固定消费者；网络+合并/工作树副作用，无近期必要性 |
| W04 | Write / fetch | P2 | G2 B1 拒绝，无 Broker.fetch；E3/S1 获取仓库不调用此工具 | 不因成长/Skill 下载就开放通用网络 refs 更新 |
| W05 | Write / push | P2 | G2 B1 拒绝；GitBroker.push 仍独立存在且需审批 | 无核心自动发布消费者；远端效果/凭证/ref 成本高，应继续封闭模型入口 |
| W06 | Write / checkout | P2 | G2 B1 拒绝；E3 仅独立实验 checkout --detach 固定 commit | 不需要对用户当前分支开放任意 checkout；复用已有实验流程 |
| W07 | Write / switch | P2 | G2 仅声明，B1 拒绝；C3 worktree 不切换原分支 | 无固定消费者，保持原工作树稳定更符合产品闭环 |
| W08 | Write / branch_create | P2 | G2 B1 拒绝；C3 worktree add -b 创建专用分支 | 内部生命周期已有唯一位置；不必再允许模型创建任意分支 |
| W09 | Write / branch_delete | P2 | G2 B1 拒绝；C3 remove 仅清理命名/根目录受限的专用分支 | 内部清理必须保留，但不能推广成用户 refs 删除能力 |
| W10 | Write / merge | P2 | G2 仅声明，B1 拒绝；A1 使用 patch apply，不 merge 分支 | 无近期开源合并/冲突交付合同，保留拒绝 |
| W11 | Write / rebase | P2 | G2 仅声明，B1 拒绝；A1/E2 无该依赖 | 历史重写/editor/exec 风险，不需要构建通用 rebase 执行器 |
| W12 | Write / reset | P2 | G2 B1 拒绝；A2/T3 明确不用 hard reset 处理失败 | 所有 reset 模式均暂不开放，尤其不能用 hard reset 做恢复 |
| W13 | Write / restore | P2 | G2 B1 拒绝；A2 内部仅错误补偿 restore --staged 指定 paths | 内部定向补偿不是授权模型覆盖文件；不开放通用 restore |
| W14 | Write / revert | P2 | G2 B1 拒绝；A1 rollback 针对已记录 applied_commit | 保留 Proposal rollback，不新建任意提交 revert 的模型入口 |
| W15 | Write / stash_push | P2 | G2 仅声明，B1 拒绝；C3 专用 worktree | 不应自动搬动操作者工作区以绕过 dirty 状态 |
| W16 | Write / stash_apply | P2 | G2 仅声明，B1 拒绝；未发现业务消费者 | 无已冻结 stash 对象及冲突合同，继续拒绝 |
| W17 | Write / stash_pop | P2 | G2 仅声明，B1 拒绝；未发现业务消费者 | 应用+删除复合效果，无当前必要性 |
| W18 | Write / stash_drop | P2 | G2 仅声明，B1 拒绝；未发现业务消费者 | 操作者恢复数据的破坏性删除，建议长期不开放 |
| W19 | Write / tag_create | P2 | G2 仅声明，B1 拒绝；未发现标签发布流程 | 不为可能的未来 release 提前构建签名/refs 系统 |
| W20 | Write / tag_delete | P2 | G2 仅声明，B1 拒绝；未发现业务消费者 | 删除 refs 非编码交付依赖，长期拒绝 |
| W21 | Write / clone | P2 | G2 B1 拒绝；E3 本地实验 clone、S1 Skill 网络 clone 独立存在 | 有内部需求但不需要通用模型 clone；两条内部获取路径仍须独立供应链/执行审查 |

汇总：P0=0，P1=7，P2=24；Inspect=10，Write=21，共31，名称保持现有公开 Schema。
P1 也不表示立即启用；保持现有拒绝直到另行批准。

## 4. 真正的内部 P0 与独立工作流

### 4.1 主编码闭环

调用链（静态已证实，非本轮动态验收）：

`Runtime/Task → NodeExecutor._sandbox → GitWorktreeSandbox.create`
`→ sandbox 文件工具修改 → EVALUATION → _approval(diff + changed_files)`
`→ ChangeProposalService.approve/apply → ApplyBroker.apply → 生命周期清理`

| 内部能力 | 当前消费者及精确用途 | 必要性与公开能力区别 |
|---|---|---|
| status | C2 `_static_check_commands`、`_approval` → C3 `changed_files`，含 untracked/rename 两端并过滤验证产物 | 核心变更集合 P0；不是 GitBroker.status。禁用它会影响 lint 范围与 Proposal 文件集合 |
| diff | C2 `_approval` → C3 `diff` | 核心补丁证据 P0；包括新文件 intent-to-add。不能只读旧 Proposal 来生成新补丁 |
| add | C3 add -N 用于 diff；A1 暂存待提交路径 | 核心暂存 P0，但参数和归属必须绑定任务/补丁；不等于模型可 add -A |
| commit | A1 Apply 生成一次交付提交，记录 base/applied commit | 当前交付设计 P0；不调用 GitBroker.commit。保留审批/验证/force 审计边界 |
| rev-parse | A2 `_head_commit` | 记录基线/产物身份 P0；固定 HEAD，不是任意用户 ref 查询 |
| worktree add/remove、专用 branch 创建/删除 | C3；[sandbox/lifecycle.py:137](../../src/Sprout/sandbox/lifecycle.py#L137) | 生命周期 P0；worktree 操作甚至不在公开 31 项中，不能用该列表替代内部需求清单 |
| rollback：revert / 定向 restore / apply -R | A1/A2，T3 | 既有恢复功能必须保留；冲突要人工核对，不是任意 reset/checkout 授权 |

内部路径“已有”不等于“已证明满足 S1”。共享 `git_env.py` 明确保留 Hooks；`ApplyBroker` 和 Local sandbox 仍有裸 Git、配置、filter、并发及结果不确定性问题需要各自安全工作包处理。
**不能因停用通用 git_write，就宣称所有内部 Git 风险解决。**

### 4.2 沙箱模型查看与真正的替代读取

- C4 是实际存在的第二条模型查看路径，不是 GitBroker 的另一个别名。Local 运行时可直接执行 status/diff/log；isolated 模式拒绝宿主读取。
- 因此“暂缓 S1 不影响主流程”的准确说法是：**B2 没有改掉已有主图的内部变更收集链**，而不是“已用另一个同等安全的 S1 替代”。本轮没有验证改动后的 E2E。
- 已有 sandbox_read_file/list/search 适合查看源码和目录，不能完整替代 index/untracked/rename/OID 等 Git 语义。
- 已有 Proposal.diff/files、节点输出与 VerificationReport 可展示**已记录的任务证据**，不能冒充当前工作树的实时状态，也不自动继承到新的 HEAD/patch。

### 4.3 成长实验与 Skill 获取：确有需求，但不是公开 GitWriteTool 需求

- E1/E2 的固定读取、事务提交以及 E3 的本地 clone/checkout 是现有可选实验工作流。应复用其持久化 claim/原始审批，不构造第二套通用提交状态机。
- `rollback_plan` 只是计划；普通 isolated Apply/rollback 仍有门禁。不能把 E2 内部 writer 存在说成所有配置均已上线可执行。
- S1（证据编号）的 GitHubSource.fetch 真实存在网络 clone，且可能触发 partial-clone 后续取对象；它有产品消费者，但不是对任意模型开放 clone 的理由。其 runner/供应链/网络风险仍登记在 R04/后续 Skill 范围。
- 成长/Skill 是独立消费者，不能因公开 clone 为 P2 而删除或静默阻断这些既有功能；若要收紧它们，另行设计和授权。

## 5. CLI / Web / MCP / SDK 的入口结论

| 接入面 | 实际链及证据 | 对公开 31 项的依赖 |
|---|---|---|
| CLI | [cli/commands/project.py:612](../../src/Sprout/cli/commands/project.py#L612) changes、[634](../../src/Sprout/cli/commands/project.py#L634) apply、[670](../../src/Sprout/cli/commands/project.py#L670) show、[725](../../src/Sprout/cli/commands/project.py#L725) rollback，经 ProjectGateway/Runtime | 基于 Proposal；show 输出已持久化 diff，不调用 GitInspectTool.show |
| Web | [web/webapi/routes/project.py:188](../../web/webapi/routes/project.py#L188) 展示 Proposal，apply_proposal 调用 gateway；[gateway/project_gateway.py:216](../../src/Sprout/gateway/project_gateway.py#L216) | 不是 GitWriteTool.commit；关闭模型通用写入口不等于移除 Web Apply |
| MCP | [mcp/server/tools.py:367](../../src/Sprout/mcp/server/tools.py#L367) proposal_show、[414](../../src/Sprout/mcp/server/tools.py#L414) proposal_apply；[391](../../src/Sprout/mcp/server/tools.py#L391) 只请求审批 | 没有公开一个“自动批准任意 git commit”的必要性；仍经过原 Runtime/Proposal 门禁 |
| RPC | [gateway/rpc_gateway.py:111](../../src/Sprout/gateway/rpc_gateway.py#L111)、[138](../../src/Sprout/gateway/rpc_gateway.py#L138) | Proposal diff / apply / rollback，不依赖通用 GitWriteTool |
| SDK | [gateway/sdk_gateway.py:47](../../src/Sprout/gateway/sdk_gateway.py#L47) execute → RuntimeGateway；[gateway/client.py:41](../../src/Sprout/gateway/client.py#L41) list_changes | 主 SDK 以 Task/查询为面；但 GitBroker 从 execution.__init__ 导出，不能断言外部嵌入者从不直接调用 commit/push |
| 普通模型聊天 | G3 的注册仍可被 Agent 动态调用 | status/diff 会明确拒绝，不能声称聊天 Git 体验未变化；Schema 保留也不表示全部操作可用 |

R02 的可信意图、会话/来源、Workspace Consent 和 Apply 审批均不能因这份需求建议而绕过。
通用 CliRunTool/ProcessBroker 或自定义插件可能另有 Git 命令渠道；本轮不证明“系统内所有 Git 已禁用”。

## 6. status/diff：最小可用性恢复方案

### 6.1 当前决策

可延后真实 S1：没有找到主编码图必须调用 `GitBroker.status/diff` 的固定依赖。
普通聊天直接 Git 查看能力会缺失，应如实说明拒绝原因；对已产生的 Proposal 优先展示已有证据，不偷偷换用 SandboxGitTool 或 CLI 宿主路径作为回退。
此判断是静态依赖结论，不是 B2 后完整 L1/真实任务执行成功的结论。

### 6.2 有明确需求后，只实现一个受控视图读取适配

1. 优先服务**已管理 sandbox / 已记录 Proposal 的查看**，而非任意用户仓库地址；明确结果属于哪个源 commit、视图和时点。
2. 可信 Git 安装由受信服务端安装配置/组件授权，核验绝对路径、版本/构建身份、安装目录及依赖保护；不能对 PATH 命中的程序做首次 hash 后即信任。
3. 视图必须由已有 sandbox/快照生命周期提供真实来源、Scope 可见集合和稳定性证据。当前 IsolatedGitWorktreeSandbox 只有根/backlink 等检查，**不自动符合这个合同**。
4. 在已授权的最小适配中产生并持有整个执行期稳定的 sanitized view/config/attributes/空 Hooks、对象可用性证据。若只能扫描仍可变的原仓库，则继续拒绝；跨平台不能保证时不启用该平台。
5. 复用 B2 runner 的固定 argv、白名单环境、timeout/输出限额/UNCONFIRMED；另外验证本地配置 include、filter、submodule、alternates、promisor/lazy-fetch、remote/helper 的排除及无隐式写入/网络。
6. `status` 若在副本上运行，只能标注“该视图的状态”；干净快照的 status 不能冒充用户实时工作树 status。不能只为了让 API 返回成功而提供语义错误的空状态。
7. `diff` 优先传递已有固定 Proposal.diff；若需重新生成，必须绑定 source/patch 与用途，不复用旧验证/审批。

以上是后续设计条件，不是本轮已具备的实现；不启用 B2 resolver、不引入第二套 Git 事务/审批框架。

## 7. B3–B7 收敛后的必要工作与下一批建议

| 原批次 | 建议保留 | 可推迟/取消的扩张 |
|---|---|---|
| B3 动作/身份/审批/结果 | 现存可达入口的可信来源、Policy DENY、无人值守、UNKNOWN 和不重试边界；明确非必要能力拒绝合同 | 不先增加 23 个动作、31 项执行计划、通用 Git 审批框架；公开写能力不启用就不需要为它建新的审批交接 |
| B4 读取恢复 | 有真实需求时只做一个固定视图 status/diff 适配，其他 P1 分别审批 | 其余 8 项 Inspect 不要求一次实现；S1 原生执行当前可以继续 HOLD |
| B5 本地写 | 现有 Apply/Sandbox/隔离提交协调器的精确授权、验证绑定及不确定状态问题不能丢弃 | 不开放通用 add/commit/branch_create/checkout 等第二条模型写入路径 |
| B6 transport | 已有 Skill 下载/实验获取路径另行核查授权、配置与效果；记录真实消费者 | 无通用模型 fetch/pull/push/clone 的近期需求；不先建通用传输/凭证/ref 系统 |
| B7 破坏性例外 | 保留拒绝；内部生命周期不能破坏；真实平台门禁另行授权 | reset/rebase/任意 refs 删除/stash 不作为近期功能目标；完整 31 项执行矩阵不是验收目标 |

**推荐下一项最小实施批次（尚未获准）：B3-min，封闭非必要通用 Git 执行入口。**

- 先提一个极小生产范围方案：通用 GitWriteTool 的 add/commit/pull 不再因 Broker 存在而可执行；同时评估 GitBroker 直接嵌入入口（尤其 commit/push）在缺少专用安全执行合同下的拒绝，不能只关模型工具留下自称受控的普通 host runner。
- 对默认 DENY 的 log/branch/add/pull，还要区分自定义 Policy=ALLOW 的现状；**本轮没有改变它们**。是否同时封闭非 S1 执行需单独列出兼容面后申请批准。
- 不修改 ApplyBroker / Sandbox / 持久化提交协调器，不把其原审批改成通用工具审批，不用 PolicyEngine 全局 DENY 误伤原 Proposal Apply。
- 保持 31 个 operation Schema 兼容，明确拒绝信息与状态；对外部直接 Broker 使用方说明可用性变化，而非静默转移到其他执行器。
- 回归以“零子进程、零旁路、旧身份/审批无法恢复执行”为主，不建设新的状态系统。保留主任务/Proposal/rollback 的已有行为；真实内部 Git 验收需单独授权。
- 这是缩小执行面，不是宣称 R04 整体完成。Apply/Sandbox 的 Hooks/filter/环境与 R04-C–E 风险仍需要既有路线图下的独立处理。

若用户另行明确要求“通用模型 git commit”，应先证明其相对 Proposal Apply 的不可替代需求，再仅设计该单项；不能以“Schema 已声明”要求恢复全部写操作。

## 8. 旧红色测试的分类建议（本轮一个断言也未改）

| 证据/测试 | 当前应如何解释 | 后续处理前提 |
|---|---|---|
| 原审计 24 项“不支持也必须调用 Broker 一次” | 与已批准 B1 的零 Broker、零宿主拒绝合同不一致；**不是 24 项待恢复功能** | 独立批准后改为拒绝+零调用，同时保留原始证据，不能直接删掉 |
| 所有 31 项无 Broker 与 B1 24 项未支持拒绝 | 仍是有效安全回归 | 必须继续保留；不能因缩减需求而 skip |
| `test_genuine_outer_commit_approval_has_one_prompt_and_bound_identity` | 记录通用 commit 的审批错配；若公开 commit 退役，正向可执行期望需改分类 | 需求合同正式批准后另行调整；不能因此弱化现有 Proposal/隔离提交的合法审批体验 |
| commit 的 Scope、无人值守、file operand、remote/ref、timeout UNKNOWN | 当前仍有可达 Broker 路径，仍是风险证据，不因 P2 建议就消失 | 后续拒绝实现可要求零执行；若保留任何执行，就必须真正修复原边界 |
| 旧 status/diff textconv/finite-timeout 探针 | 观察的是旧 run_capture，B2 缺权限已提前拒绝，所以旧 call_args=None；不是新 runner 真正执行的证据 | 专用 B2 Mock 验证 runner；生产签发/原生门禁仍 HOLD，旧失败历史不改写 |
| `git_env()` 的 global/system 配置红测试、Hook 红测试 | 共享消费者（Apply/Sandbox 等）仍用它；不能以 B2 不修改共享文件为由宣布风险关闭 | 分配到相应内部执行工作包，不删断言、不静默改共同默认 |
| 所有 MCP/Skill/DB/下载红色测试 | 与最小 Git 能力清单无关，仍有效 | 留在原 R04 包，不通过缩小 Git 需求消除 |

历史测试证据（不是本轮新执行）：B2 定向组 309 passed，exit=0；collection 3936，exit=0；原审计组合 61 passed /53 failed，exit=1（B 设计 48/16，原审计 13/37）。
定向组有重叠，不相加；collection 不等于通过。对应日志位于仓库外 `C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r04b2-*`，原文件保留。

## 9. 安全前提、兼容、回滚与本轮边界

- 本轮没有新的 pytest/L1/平台测试：**not_run**。源码引用和测试存在性只经静态核查，不把测试代码当通过证据。
- 原生 Windows/POSIX Git、真实 Hook/filter/helper、网络/凭证、Docker/Temporal、付费模型、真实 SQLite、并发工作树/配置替换均 **not_run**。
- 历史 Windows 原生崩溃、长路径问题、symlink 平台限制和外部集成未运行记录不因本审查而解除。
- 缩小公开能力需明确兼容说明；保留名字/Schema、输出明确拒绝，不静默成功。不要把源码已有 API 当作无人使用；外部 SDK 需求需要另行征集。
- 仅文档回滚不会更改执行行为。后续安全回滚必须保留 A/A2/B1 的零回退和 B2 默认拒绝，不能重新启用宿主 Git 以恢复体验。
- 七份原调查材料保持原样未提交；本轮不 Stage/Commit/Push/PR/Merge，不使用 Hooks 临时禁用，不推进其他工作包。

**建议决策：接受 0 项公开 P0 /7 项 P1 /24 项 P2 的收敛基线；先审核 B3-min 拒绝方案，而非继续完整通用 Git 框架。内部 P0 保留并复用，真实 S1 能力仍 HOLD。**
