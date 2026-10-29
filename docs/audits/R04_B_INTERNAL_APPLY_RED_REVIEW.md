# R04-B-internal-Apply-A — Mock RED 与专用适配接口审查

日期：2026-10-21。分支 `codex/r04-broker-zero-bypass`；HEAD `33a7b06946a0b086a9078bb4893e4fd39f1262ed`。
结论：**HOLD。已完成独立 RED 与接口设计，未实施或接入生产适配器。**

## 1. 变更和安全边界

本轮仅新增两份获批文件：

- `src/Sprout/tests/test_r04_b_internal_apply_red.py`：自包含 Mock，全文即该文件；无历史未跟踪测试 fixture 导入。
- 本报告。生产、既有测试、Schema、Policy、ApprovalManager、B2 issuer、共享 proc/git_env 未修改；未 Stage/Commit/Push。

开始时 HEAD/分支符合预期，暂存区为空，无 tracked diff。九份原调查材料、B-Final 及内部 Apply 预检文档存在且保持前轮哈希。
测试文件最终 SHA-256：`E0B7BDC842AFE8FE4465C180F1EC7905EED389365218C667961A81B1E000BB76`。

真正被测试的层：真实 ApplyBroker.apply/evaluate/rollback、PolicyEngine、MemoryOperationalStore + ApprovalManager、ChangeProposalService，以及 NodeExecutor 的未知来源拒绝。最终 `_git` 是内存 Git 语义模型；模块 run_capture、proc.run_capture、asyncio exec/shell、subprocess run/Popen、patch 创建/unlink、_undo 均 Mock。没有 Git 安装探测或网络调用；既有隔离 runner 另行阻断网络/越界写入，不把 Python guard 说成 OS 沙盒。

## 2. 执行命令和证据

环境：现有 `.venv` Python 3.13.9 / pytest 9.1.1 / Ruff 0.16.8，无依赖安装。使用既有离线 runner，显式校验 Sprout 从本 worktree 导入；隔离 HOME、pytest basetemp，禁 cache/pyc，日志只在仓库外。

命令形式（以下 root 展开为绝对路径）：

```text
PY = D:/dev/projects/sprout/.venv/Scripts/python.exe
RUN = C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r03_worktree_checks.py
WT = C:/Users/WingYouther/.codex/worktrees/r04-broker-zero-bypass/sprout
PY -I -S -B RUN WT r04-internal-apply-red-final src/Sprout/tests/test_r04_b_internal_apply_red.py
```

控制组同一 runner，label=`r04-internal-apply-controls`，明确选取：

```text
src/Sprout/tests/test_r04_cli_broker.py
src/Sprout/tests/test_r04_a2_handoff.py
src/Sprout/tests/test_r04_b1_git_no_fallback.py
src/Sprout/tests/test_r04_b2_git_s1.py
src/Sprout/tests/test_r04_b3_min_closure.py
src/Sprout/tests/test_git_broker_approval.py
src/Sprout/tests/test_local_acceptance_apply.py
src/Sprout/tests/test_legacy_acceptance_backend_boundary.py
src/Sprout/tests/test_apply_commit.py::test_failed_revert_never_attempts_reset_or_reports_success
```

完整实际子进程 argv、stdout/stderr、JUnit、退出码、测试前后文件 manifests 位于以下独立目录，各有 command.json、pytest.log、junit.xml、summary.json、source-before/after.json：

`C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/`

| 目录 | Passed | Failed | Errors | Skipped | 原始 Python 退出码 | 耗时 |
|---|---:|---:|---:|---:|---:|---:|
| r04-internal-apply-red（首版42项） | 7 | 35 | 0 | 0 | 1 | 2.50s |
| r04-internal-apply-red-final（最终43项） | 8 | 35 | 0 | 0 | 1 | 2.40s |
| r04-internal-apply-controls | 452 | 0 | 0 | 0 | 0 | 13.60s |

首版日志未覆盖。之后只修正新文件5处 E501、为复杂 patch 参数提供固定 ids，并新增真实 PolicyEngine action-scope DENY 正向对照；没有修改生产或降低安全断言。最终两组 source-before/after 检查均 true。各组不可累计；RED 不等于修复或业务验收通过。

`ruff check . --no-cache`：All checks passed，0；`git diff --check`：0。后者不覆盖 untracked 文件内容；新增文件另由 Ruff 及人工 EOF 检查审查，未通过 Stage 来验证。完整 L1、原生 Git、真实 Hook/helper、网络、真实用户仓库/DB 均 not_run；真实 Git fixture 没有运行。

## 3. 最终43项逐类结果和含义

| 测试函数（参数化数） | P/F/E/S | 断言与实际观察 |
|---|---|---|
| test_file_index_boundary_must_refuse_before_unreviewed_commit (4) | 0/4/0/0 | empty_files 模型观察 add-A，staged_other 提交集合含 operator.txt，target_extra 模型标记污染，mismatch 提交了列表而非 patch 对象；均到达 Mock commit |
| test_empty_patch_never_commits_existing_index (1) | 1/0/0/0 | 空 patch 不调用 Git/写 patch、不动已有内存 Index；不据此证明 applied=True 是正确交付语义 |
| test_unproven_patch_shapes_have_no_execution_contract (7) | 0/7/0/0 | rename/delete/mode/symlink/binary/escape/pathspec 在无可靠验证合同时仍进入 Mock _git；目标合同要求入口拒绝 |
| test_direct_broker_cannot_treat_coarse_approval_as_verification (1) | 0/1/0/0 | 真实粗粒度批准使 PENDING、无 verification Proposal 到达 Mock Git；Broker 不承担 Service 门禁 |
| test_old_approval_does_not_bind_a_changed_execution_plan (5) | 0/5/0/0 | 同 id/risk/task 的 patch、files、head、workspace、verification 变化仍提交；现审批未绑定这些维度 |
| test_real_scope_remains_authority (3) | 2/1/0/0 | expired/root_deny 已正确零 I/O；仅 task.txt 被 deny 时资源仅检查 root，仍执行 |
| test_real_policy_action_deny_precedes_existing_approval (1) | 1/0/0/0 | 真实 file.read-only scope → PolicyEngine DENY，旧批准不能覆盖 |
| test_post_effect_interruption_requires_uncertain_evidence_without_undo (12) | 0/12/0/0 | apply/add/commit/observe × timeout/cancel/nonzero；2项触发 _undo，9项无明确 uncertainty，1项观察失败仍 applied=True |
| test_rollback_identity_requires_trusted_current_binding (2) | 0/2/0/0 | 短 OID/当前HEAD不等于 recorded commit 仍进 Mock revert |
| test_conflicting_rollback_preserves_no_reset_contract (1) | 1/0/0/0 | 非零 revert 明确 manual reconciliation，只有一次 revert，不 reset/undo |
| test_repeated_apply_requires_durable_one_shot_claim (2) | 0/2/0/0 | 两个 Broker 对象并发/顺次重建都产生两次模型 commit；无 durable claim 合同 |
| test_missing_approval_refuses_before_any_patch_io (1) | 1/0/0/0 | 无审批真实 REQUIRE_APPROVAL 拒绝，零 patch/Git |
| test_existing_unverified_service_node_gate_is_preserved (2) | 2/0/0/0 | 普通 service 和 Node 拒绝 unknown 原来源，零执行 |
| test_force_audits_exception_but_is_not_execution_environment_authority (1) | 0/1/0/0 | 原 reason、blocker、verified=False 保存正确；但 force 后仍无可信执行适配就进入 Mock Git |
| 合计 | **8/35/0/0** | 35失败均为安全合同 AssertionError，无导入/fixture错误；不是本轮修复目标已完成 |

### 证据局限，不混淆 RED 与原生漏洞

- 内存 runner 明确建模 add-A 暂存所有、精确 add 不清除已有 Index、普通 commit 提交整个 Index。没有运行真实 Git 来证明版本/平台行为。
- 特殊 patch 用于证明 Broker 无提前合同校验，不是保证所给简化 patch 被原生 git apply 接受；Fake preflight 成功是隔离下层 native 检查的受控假设。路径/二进制等真实可利用性仍需独立原生验证。
- head/workspace/verification 变化测试揭示当前 API 根本没有冻结执行授权维度；没有伪称创建了项目里不存在的完整旧执行凭据。
- 并发为同进程两 Broker + 内存批准，restart 为新 Broker 对象复用同内存记录。证明缺少 one-shot guard，不是两个 SQLite 连接或真实进程重启验证；当前 Apply 接口无 durable claim 可测试，跨连接/崩溃恢复列为架构阻塞。
- 未在此测试捕获新的 OS/network/helper 缺陷。无 monkeypatch 真实门禁变 ALLOW；最终 Git Fake 故意成功，以观察调用和不确定性。取消被测试辅助函数捕获用于断言，生产取消传播未被修改，也未以普通取消替代 UNKNOWN。
- 452控制组包括现有 match/unknown/mismatch 的 Service/Node 正负对照，B3 独立内部门禁/路由和 A/A2/B1/B2。未把这组称为原生 Apply 工作流通过；原始49审计失败记录不变，也未重跑/隐藏。

## 4. 未来 execution/apply_git.py 最小接口（设计而非实现）

### 4.1 身份与 authority：数据形状不等于授权

候选类型在执行模块内部，均不可由工具 JSON 反序列化后直接视为可信：

```text
_GitInstallationV1:
  absolute executable + approved installation/build identity + supported version
  loader/helper dependency identities + protected-directory proof + authority version

_ManagedApplyViewV1:
  target repository identity (not just path/workspace.kind)
  stable config/attributes/object/ref/index/worktree ownership lease
  source/target identities + scope-visible set + expiry/revocation handle
  sanitized config/home/hooks/temp ownership evidence + complete local objects proof

_FrozenApplyPlanV1:
  domain/version, operation apply|rollback
  task/proposal/snapshot/workspace IDs, server-resolved principal/source/session
  base/result parent OIDs, target identity, patch digest, files+OID+mode set
  expected index/tree digest, verification snapshot + current applicability
  scope digest, approval fingerprint, executor/view identity, transaction owner
```

字段是审计/比对输入，不是调用者可自报的签发证明。签发函数来自服务端受信装配/权威持久 snapshot；执行器验证其对象归属、有效 lease、绑定和过期/撤销。普通 dataclass 构造、trusted=True、任意 token/hash/绝对路径、模型 metadata、B2 _Plan/_Installation 均无权签发。无生产安装/视图 issuer 时 resolver 返回 None，**零 probe/零 spawn/零 patch写入**。

不能以首次 PATH 命中或首次 hash 自动注册安装；需要外部明确授权的安装身份及依赖稳定性。不能 config 扫描一次后在仍可变原仓库执行；view 必须在整个操作期间受保护，不能用临时 lease 名称冒充实际 OS 保证。私有 index 只能隔离 index，不隔离 local config/refs/对象库/用户文件。scope 必须覆盖实际读取和修改资源，不能用根目录 ActionRequest 替代文件集合检查。

### 4.2 入口、固定模板与资源时点

```text
admit_apply(persisted_snapshot, resolved_task, resolved_principal,
            current_evidence, scope, transaction_owner) -> Admission | Refusal
admit_rollback(recorded_result, same_original_authorization,
               resolved_task, resolved_principal, scope, transaction_owner) -> Admission | Refusal
execute(admission) -> ApplyExecutionOutcomeV1
observe(transaction_id, trusted_current_context) -> observation (NO retry)
```

不提供 execute(argv)/run_git 任意入口。admit 不根据 caller validated 字段跳过来源；execute 重新核验租约/绑定。建议模块只负责 Git 计划和效果机制；持久 transaction 由原有权威接口协调，不在 helper 内建另一套数据库或全局 approval registry。

固定 apply 模板：绑定对象/HEAD检查 → 受控 patch preflight → 独立 expected index/tree（精确 files，无 add-A）→ 一次受授权 commit → parent/tree/identity/result OID观察。固定 rollback 模板：full recorded OID/parent/base/current HEAD确认 → 显式新 revert commit → 观察；旧 patch 记录若缺完整身份则拒绝要求人工恢复，不自行 reverse/clean/reset。正常 commit 的具体原生命令选择要先证明 tree等价，不使用 commit --only/pathspec 猜测安全。

无用户 Index 清理或隐式 stash；用户已有 staged/未获批重叠内容先保守拒绝。目标目录、Index、refs/config不能执行期保护时不发布到用户主仓库。apply --check 不是执行期稳定保证，commit成功退出也不是最终绑定证明。

独立 env 白名单：固定隔离 HOME/TEMP，禁继承凭据和调用者 Git/env 配置；屏蔽 system/global配置、Hook/pager/editor/signing/fsmonitor/maintenance，禁止协议/隐式lazy-fetch；local includes/filter/attributes/alternates/promisor/submodule 等未知来源无法排除则拒绝。固定 argv/env pins 只是纵深防御，不是受控视图证明。取消/异常后不得换共享 git_env/proc或宿主 runner重试。

### 4.3 效果模型：投影到既有事务，不另造独立状态机

| 对外效果 | 必需证据 | 可否重试 |
|---|---|---|
| NOT_STARTED | 可信层证明未尝试任何启动/写操作，准入拒绝 | 不自动重试；新请求重验环境/批准 |
| WRITE_STARTED | 既有持久事务 begin-write 成功，准备开始首个资源副作用 | 不是安全失败/不是已执行成功；禁止第二个写领取 |
| UNCONFIRMED | spawn、效果、观察、清理、持久化任一不明 | 不自动补偿/重试/接管；人工核对 |
| SETTLED | observed OID/parent/tree吻合冻结计划且权威持久 settle成功 | 报告已执行；重复调用只能查询同一结果，不创建新提交 |

安装/视图准入后，现有事务 claim 必须先于 patch、workspace/index或进程副作用。有限 spawn/command/cleanup deadline、合计输出预算；启动 await超时也可能已创建子进程，不能硬标 NOT_STARTED。清理成功不等于无效果；POSIX进程组/Windows Job等 OS 证据需另批，不把 taskkill返回当完整证明。

Outcome 建议版本化：effect、phase、reason_code、transaction_id、cleanup_confirmed、observed_result_digest；不输出凭据/原始敏感日志。当前 ApplyResult 只有 applied bool/commit字段，不足完整表达。将 UNCONFIRMED 转 applied=False 而上层可自动retry不算完成；必须沿原 Runtime/Node/持久恢复链保留阻断。取消继续传播，并由现有事务持久 uncertain，不能仅凭异常注释防重启。

### 4.4 与既有事务和审批的衔接

`storage/contracts/metadata.py:78–94` 已有 get_git_proposal_snapshot/get_git_transaction/claim_git_transaction/begin_git_commit_write/settle_git_commit_proposal/finish_git_transaction。isolated_git_coordinator 已使用 claim→re-admit→精确批准消费→begin-write→writer→settle，异常保留 uncertain。这是应复用/扩展审查的唯一事务方向，**不能直接假定现有记录结构适合普通 Local Apply/rollback**。

下一轮必须先证明这些 API 对 Local计划、各存储后端、old records与rollback的字段和原子条件足够。若不够，申请合同/Schema范围；本轮不改，也不建第二套ledger。后端不支持则拒绝，不回退 asyncio.Lock。新设计中的效果枚举只是该既有事务的结果投影，不是平行 ownership 系统。

审批仍复用 ApprovalManager fingerprint 和 service审查入口；同一已冻结 proposal 的 apply/rollback复用原产品语义，但参数必须证明内容/目标/执行器一致。旧 `{proposal_id,risk}` 批准不自动认领新计划；可能需要显式重新审阅，不能静默更新旧批准。force依然只豁免验证 blocker，保留 reason/audit/verified=False，不签发安装/view/claim权限。不能改全局 GIT_COMMIT规则。

## 5. default-deny 接入的可用性影响（必须单独批准）

当前无可信安装与稳定视图签发器，若把新适配接入：

| 正常现有行为 | 会发生什么 | 恢复前提 |
|---|---|---|
| 已批准且 Local verification通过的 Proposal Apply | 拒绝或configuration waiting，不再宿主执行 | 可信安装/受控视图、冻结计划、精确批准及持久claim |
| CLI/Web/MCP/Node普通 Apply | 同样阻断，不换通用 Git或CLI路径 | 所有入口一致接线与结果/恢复协议 |
| force + override_reason | 原override审计仍保留，但无法执行；不能说已验证 | 同普通Apply执行前提；验证豁免不解决安装信任 |
| recorded commit rollback | 无安全执行器拒绝；历史OID/HEAD不完整不能默认适用 | 完整历史身份、受控当前目标、授权恢复计划 |
| legacy patch reverse rollback | 默认拒绝，不能猜测恢复 | 可信独立恢复证据/明确人工决策，可能不恢复自动兼容 |
| direct Python ApplyBroker | 缺可信Admission拒绝 | 服务端嵌入合同与持久snapshot，非caller布尔 |
| no-diff proposal | 必须明确 no-op，不动Index；状态语义需兼容评估 | 有无变更的可验证结果，不能借此提交预staged数据 |

可用性收窄是必要的显式变化，不得以“保留API签名”掩盖；恢复需真实环境证明而非Mock假issuer。回滚只能维持拒绝或撤回未启用框架，不恢复裸 `_git`/add-A/旧粗批准/自动undo。旧记录和不确定现场不得删除或自动迁移。

## 6. 最小后续候选范围与未解决前提

建议独立申请 `execution/apply.py` + 新 `execution/apply_git.py` + 专项测试，先评审 default-deny准入和固定runner；不要修改共享git_env/proc、S1 issuers、全局Policy或ApprovalManager。

但**只这两个文件不能完成真实Apply安全恢复**：Service/Node需一致供应可信冻结计划和处理uncertain；ApplyResult/公开报告需兼容表达；现有事务合同是否足够尚未证明。若要接入并恢复功能，预计另批 `runtime/changes.py`、`runtime/nodes.py`、必要的 `execution/models.py` 及经过评估的现有事务接口；没有实施这些文件。

需要先解决：服务端安装及loader身份、执行期稳定的目标/config/attributes/object/ref保护、可信文件/patch/tree/HEAD绑定、跨进程durable one-shot及观测settlement、OS进程树/网络隔离。R05/R06相关责任只作依赖登记，不启动实施，也不据此放行当前未知执行。真实Windows/POSIX Git、Hook/helper、外网/本地服务、完整L1、真实用户Index/DB均not_run。

测试和报告审查后停止；不进入Sandbox、R04-C、R05或R06开发。
