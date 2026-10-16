# R01 — 可复现测试基线与 CI

日期：2026-10-16。开发分支 `codex/r01-baseline-ci`，起点
`dev@32e5f734fc556f5ccdce152b8ec949b75021fbd6`。R00 已经用户审查完成。
本文件是 R01 的当前增量记录，不改写 Phase 0 的历史检查结果。

## 范围与约束

修改测试 fixture、测试替身/子进程环境、测试运行脚本和 CI；首轮审查修正另获授权，
仅在 evaluation 生成的 conftest 导入块后增加空行。没有修改 Runtime、Broker、审批、
存储 schema、任务执行、评测目标/Oracle/门禁，也没有恢复八份历史文档。
文档恢复另开分支/PR，按用户决定分别归档至 docs/runbooks 和 docs/archive。
当前已有 [Draft PR #2](https://github.com/siyuuuu1014-cell/sprout/pull/2)。本轮审查修正
仅允许本地修改/提交，不再 push，不 merge、不转 Ready、不部署。

## 示例测试：修复真实收集错误

原始两个测试直接 import 根目录 bubble_sort/hello_world，但模块不存在；本地可达
Git 历史未找到原始实现。新建 `src/Sprout/tests/fixtures/` 下的两个第一方参考实现，
明确标记为依据已有行为测试新编写，不声称恢复原件；保留全部 18 个原测试及断言。
移除 sys.path 注入，显式导入测试 fixture。没有 ignore、skip、importorskip 或占位实现。
fixture 自检通过不表示 Sprout Agent 的代码生成能力已通过独立验收。

## 可重复运行

先使用已审核的 Python 与 uv。CI 固定 uv 0.11.25，矩阵为 Ubuntu/Python 3.12 和
Windows/Python 3.13。本地实际使用 Windows、Python 3.13.9。示例：

```powershell
python scripts/ci_baseline.py --output .workbuddy/test-runs/r01-new-run --python <python-executable>
```

output 必须不存在；脚本不会删除或复用旧证据，也不清理用户 `.venv`。它执行：

1. 仅以 `git ls-files -z` 清单复制 Git 跟踪文件的当前工作树内容，保存逐文件 SHA256
   清单；不再递归扫描补充文件。新文件须显式纳入版本控制后才进入快照。不会复制任意
   未跟踪文件、忽略的 .env、用户数据库或既有虚拟环境；跟踪文件仍须提交前审查。
2. 在该新目录中运行 Python/uv 版本、`uv lock --check --python ...`、
   `uv sync --locked --group dev --python ...`。独立 venv/cache，不更新源仓库 uv.lock。
3. 导入 Sprout、CLI、Web、pytest、ruff；运行 `ruff check . --no-cache`。
4. 完整收集 `src/Sprout/tests` 和 `web/tests`，保留完整日志、JUnit 和逐项门禁清单。
5. 分别执行两个示例测试、R00 disclosure 回归，再执行全部离线 L1。

每一步完整 stdout/stderr 写入 `<step>.log`，命令、退出码和耗时写入 summary.json。
测试输出还有 examples.xml、r00.xml、l1.xml、collect.xml 和 gates JSON。
命令退出非零即记录失败；没有 continue-on-error 让流水线伪绿。

测试进程不继承 provider key、服务地址、SPROUT_CONFIG 或激活标记；以测试专用
sitecustomize 在导入 Sprout 前将 Path.home 定向到临时 home，避免默认路径触及用户库。
不修改 HOME、用户 Git 配置或真实模型配置。测试 Git 使用独立的临时配置和明确的
`Sprout CI Fixture <fixture@example.invalid>` 身份，绝非用户/Agent 的提交身份。
L1 的 Python socket 限制为 loopback；这是测试卫生措施，**不是 OS 沙箱**，也不能
作为对任意子进程完全断网的证明。源码快照不包含用户 .env 或 Git 元数据。

## L0/L1 与外部集成分类

`scripts/ci_support/pytest_gates.py` 只在显式加载时生效，不改变生产功能或裸 pytest 的
既有行为。默认 CI 先完整收集，再使用 `--baseline-l1` 将已核查的真实服务测试逐项记为
not_run；不是忽略收集错误或排除失败单元测试。混合文件按函数/参数区分，真实服务和
离线替身合同不混淆（如 live_model_budget 的离线预算测试仍属 L1）。

| 门禁 | 默认状态 | 显式启用时的约束 |
|---|---|---|
| Docker | not_run | 本地固定测试镜像；不自动拉取/构建；缺镜像阻断，跳过也不能记集成成功 |
| Temporal | not_run | 专用 127.0.0.1:17233，独立 namespace/queue，禁止生产地址 |
| Docker 故障注入 | not_run | 除 Temporal 门禁外，再显式允许故障标记；不能由一般 Temporal 验收推定 |
| 真实付费模型 | not_run | 单独人工授权、明确剩余请求预算/配置及全部组合基础设施；不沿用历史额度 |
| Redis/Milvus/Neo4j | not_run | 专用服务地址；不使用用户现有数据库 |

集成操作不由 baseline.py 启动。后续经人工批准、准备专用环境后，可显式加载同一插件：

```text
python -m pytest -p scripts.ci_support.pytest_gates --integration-gates=docker ...
python -m pytest -p scripts.ci_support.pytest_gates --integration-gates=temporal ...
```

组合用例要求其全部 gate 都被启用；model、databases、docker_faults 同理。缺少显式
前提时插件返回 UsageError；显式集成出现 skipped 则最终非零，不能当 passed。
插件不负责部署基础设施、发放预算、证明镜像安全或恢复未知副作用。真实模型门禁
没有默认 Actions job，也没有传入生产秘密；本轮不执行任何真实集成。

新增测试验证分类、缺前提阻断、L1 不隐藏离线失败以及显式集成跳过不能伪绿。

## GitHub Actions

`.github/workflows/baseline.yml` 在 PR、dev push 和手动触发时运行 L0/L1；
最小 contents:read，checkout 不保留凭证，不注入 secrets，失败仍上传日志/JUnit/JSON。
官方 Action 发布标签与 SHA 已于本轮用 git ls-remote 核实：

- checkout v4.2.2 → 11bd71901bbe5b1630ceea73d27597364c9af683
- setup-python v5.6.0 → a26af69be951a213d495a4c3e4e4022e16d87065
- setup-uv v6.1.0 → f0ec1fc3b38f5e7cd731bb6ce540c5af426746bb
- upload-artifact v4.6.2 → ea165f8d65b6e75b540449e92b4886f43607fa02

远端首轮真实结果见末尾“PR #2 首轮审查修正”。两平台均已 completed/failure，
不是 not_run 或 in_progress；本轮未推送修正，因此远端尚未验证修正后的代码。

## 首轮真实检查与根因归类

- 修改前两个示例测试：2 个 collection errors，未执行测试。
- 修复后当前环境示例：18 passed。
- 首次沙箱内 clean install：网络权限失败；日志显示固定 certifi 下载被阻断；保留证据。
- 经授权在沙箱外独立环境安装：锁文件 124 packages，安装 68 packages；imports/ruff 成功。
- 首轮完整收集：2383 项，0 errors，1 条 Starlette/AnyIO 弃用警告。
- 首轮 clean examples：18 passed；R00：67 passed。
- 首轮完整 L1：2318 passed、6 failed、1 skipped、58 deselected/not_run、2 warnings，251.25s。
- 证据目录：`.workbuddy/test-runs/r01-clean-windows-2/`，不提交临时数据库/证据原始目录。

| 根因 | 证据与最小处理 | 本轮决定 |
|---|---|---|
| 4 个 preflight 测试替身过期 | `test_coding_preflight.py` 的 SimpleNamespace 缺少 Runtime._isolated_execution_wait；真实调用在 runtime.py:2589，实现在2556 | 测试替身绑定真实 helper，保留所有原断言，不 mock 返回值、不改 Runtime |
| CLI 子进程失去测试 home bootstrap | `test_lanediag.py:263` 原来覆盖整个 PYTHONPATH，导致隔离环境不能解析 home | 只在测试层保留已有 PYTHONPATH 并附加源码路径，不读取真实 home |
| 固定评测生成的 conftest 不满足 ruff | `evaluation/runner.py:87` 生成 imports 后缺空行，I001；C01/C02/C03/R01 验证失败，scenario_passed=6，不是10 | 首轮保留失败；PR #2 审查后用户已批准最小格式修复，见末节 |

最后一项的 pytest 和输入输出可通过，但 ruff 失败使 verified=false，这是正确的
fail-closed 结果；不能降低门禁或把预期10改成6。查看首轮 C01/verification.json 与
l1.log 可复核。该段记录首次发现时的证据，修正后结论见末节。

## TaskStatus 文档纠偏（F14，仅列当前源码事实）

`src/Sprout/task/models.py:19` 当前定义的完整值为：created、discovering、planning、
ready、running、waiting_approval、waiting_resource、retrying、paused、evaluating、
ready_to_apply、applying、completed、failed、cancelled、rolled_back。
v1 的文字清单漏列 discovering/ready；本轮不新增、重命名或迁移任何枚举，也不以
此清单证明所有 Local/Temporal/UI 状态转换已经动态验收。

## 兼容、回滚与停止条件

公开 API、持久化 schema、审批指纹、R00 实现与用户数据不变。测试示例显式导入是
测试内部变更；新 CI 脚本是独立入口。没有新增依赖或更新 uv.lock。
回滚按本分支的独立补丁撤回测试/fixture/scripts/workflow，不回滚 dev 的 R00，不操作
用户数据库或恢复历史删除。证据目录先保留；不使用 reset --hard 或宽范围删除。

最终复验结果在下节填写；只要有失败/未运行的目标平台，就不能宣称完整验收完成。
本轮到 R01 停止，不进入 R02。

## 首次交付复验（历史结果，修正前）

最终证据：`.workbuddy/test-runs/r01-clean-windows-reviewed/`。

| 检查 | 真实结果 |
|---|---|
| Python / uv | Windows Python 3.13.9 / uv 0.11.25 |
| lock / sync | exit 0 / exit 0；锁解析124包，独立新环境安装68包；uv.lock未改 |
| 包导入 / ruff | 均exit 0；当前工作树再次 ruff check 与 git diff --check 通过 |
| 完整收集 | **2396 tests collected，0 errors，1 warning**，4.23s |
| 两个示例 | **18 passed，0 failed，0 skipped**，0.13s |
| R00 disclosure | **67 passed，0 failed，0 skipped**，0.22s；R00两个文件blob与基线相同 |
| 完整离线L1 | **2336 passed，1 failed，1 skipped，58 deselected/not_run，2 warnings**，261.98s；exit 1 |
| Ubuntu/Python3.12 Actions | 此次本地执行未验证；后续真实 Actions 见末节 |
| Windows/Python3.13 Actions | 此次为本地执行；后续真实 Actions 见末节 |

18项示例、67项R00和新增13项CI合同均包含在完整L1中，不重复累加。
跳过1项为 `test_authz.py::test_read_broker_rejects_symlink_escape`：当前Windows环境
不能创建symlink；没有将其算通过。两条警告来自Starlette/AnyIO和lark protobuf的弃用API。

58项not_run按互斥组合分类：Docker17、Temporal21、Docker+Temporal10、
Docker故障+Temporal2、Docker+Temporal+model1、Temporal+model1、外部数据库6。
既没有启动这些基础设施，也没有付费调用；门禁名称不代表已经取得集成证明。

中间复验 `.workbuddy/test-runs/r01-clean-windows-final/` 曾得到2333 passed、4 failed、
1 skipped、58 not_run、5 warnings：3个新CI合同测试与完整收集过的同名模块发生导入冲突，
另1个仍为固定评测失败。已将pytester模拟用例放到独立解释器和本地pytest配置中，
关闭其缓存，避免读外层配置/模块缓存；保留这些失败日志。联合收集真实Docker测试的
专项验证为13 passed、3 skipped（真实Docker未启用）；最终完整L1中13项均通过。
早期沙箱内专项曾因临时目录权限出现3个setup errors，经授权在独立目录复测通过，
不将权限失败或未启用的Docker计作成功。

最终命令：

```powershell
.\.venv\Scripts\python.exe -B scripts/ci_baseline.py --output .workbuddy/test-runs/r01-clean-windows-reviewed --python D:/dev/runtimes/conda/python.exe
```

全部展开的uv/pytest命令和退出码在summary.json；完整输出在各步log；JUnit在各步xml；
逐项not_run清单在l1-gates.json，源码身份在source-manifest.json。首次失败、复验与最终
证据分目录保留，不覆盖历史报告。四个修改的原有测试文件经AST对比，原有assert表达式未变。

### 修改文件清单

- `.github/workflows/baseline.yml`：双平台L0/L1与失败时上传证据。
- `scripts/ci_baseline.py`：独立安装/源码快照/测试/报告；保留源文件执行权限。
- `scripts/ci_support/__init__.py`、`sitecustomize.py`、`pytest_gates.py`：测试专用隔离与门禁。
- `src/Sprout/tests/fixtures/__init__.py`、`bubble_sort.py`、`hello_world.py`、`README.md`：新参考样例与来源说明。
- `src/Sprout/tests/test_bubble_sort.py`、`test_hello_world.py`：修复导入，18项行为测试不变。
- `src/Sprout/tests/test_coding_preflight.py`：测试替身绑定真实准入方法。
- `src/Sprout/tests/test_lanediag.py`：子进程保留测试隔离路径。
- `src/Sprout/tests/test_ci_baseline.py`：13项测试基础设施合同。
- `docs/audits/R01_TEST_BASELINE.md`：本记录。

### DoD与阻塞

首次交付因固定评测失败未满足 DoD；该结论保留为历史记录。当前审查修正状态、
真实远端结果及剩余阻塞见末节。R02不启动。

## 独立分支流程复核（2026-10-16）

用户再次确认每个Rxx独立开发分支、独立PR、审核后合并。本轮开始时已在
codex/r01-baseline-ci，HEAD及远端dev均为32e5f734，远端尚无R01分支；此前15个
R01文件已经暂存，没有额外未暂存修改。本轮复用该分支，不重建或覆盖它。
将长期流程写入AGENTS.md，并准备R01_PR_DESCRIPTION.md待审说明。
随后经用户明确授权，提交 d08de1133b709a0dc5fd1e643efd91bc9192853a 已推送并创建 Draft PR #2。
完整L1数字仍来自上节明确记录的执行，本轮流程记录不冒充一次新的全量测试。
首次交付未改 eval 模板；首轮审查已批准本节之后的最小修正。历史文档恢复仍不在范围内。

## PR #2 首轮审查修正（2026-10-16）

开始时分支 `codex/r01-baseline-ci`、HEAD `d08de1133b709a0dc5fd1e643efd91bc9192853a`，
工作树/暂存区干净；`git ls-remote` 确认远端任务分支同 SHA，远端 dev 仍为起点 32e5f734。
PR #2 保持 Draft。本轮不推送、不合并、不新建 PR、不修改 dev。

### 修改与红灯证据

- `evaluation/runner.py::_repo()` 唯一生产差异是在生成 conftest 的 import 块后增加 `\n`。
  新增 `test_evaluation_runner.py::test_generated_fixture_conftest_passes_ruff`；未改变现有
  10 场景、断言、通过门禁、Oracle、执行命令或预期。
- `scripts/ci_baseline.py::snapshot()` 删除目录 rglob 补充逻辑，仅用 Git 跟踪清单。
  `test_ci_baseline.py::test_snapshot_excludes_untracked_sensitive_files_without_deleting_them`
  在临时 Git 仓库的 scripts、fixtures、workflows 分别放置未跟踪的模拟敏感文件，
  断言不复制、不进入 manifest、原文件字节保留。没有删除用户文件。
- 修复前实际执行：模板回归 **1 failed**（Ruff I001）；快照回归 **3 failed、13 deselected**
  （三个模拟文件均被复制）。修复后联合固定评测、模板、CI 合同 **18 passed、0 failed、
  0 skipped**，37.22s。固定评测仍要求 10/10，不是放宽成 6/10。

失败回归精确命令（使用授权的本地解释器，关闭缓存）：

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q src/Sprout/tests/test_ci_baseline.py -k snapshot -p no:cacheprovider --basetemp=.workbuddy/test-runs/r01-review-snapshot-red --tb=short
.\.venv\Scripts\python.exe -B -m pytest -q src/Sprout/tests/test_evaluation_runner.py::test_generated_fixture_conftest_passes_ruff -p no:cacheprovider --basetemp=.workbuddy/test-runs/r01-review-template-red --tb=short
```

绿色定向回归使用前次隔离 venv，在测试专用 home、禁外网 bootstrap、临时 Git 配置下，
以当前仓库 src 和 scripts 为 PYTHONPATH；实际 pytest 参数：

```text
-B -m pytest -q src/Sprout/tests/test_evaluation_runner.py::test_generated_fixture_conftest_passes_ruff src/Sprout/tests/test_evaluation_runner.py::test_fixed_suite_and_persisted_report src/Sprout/tests/test_ci_baseline.py -p no:cacheprovider --basetemp=.workbuddy/test-runs/r01-review-targeted-green --junitxml=.workbuddy/test-runs/r01-review-targeted-green.xml --tb=short -o faulthandler_timeout=60
```

### GitHub Actions 实际结果（修正前提交）

[run 38883570451](https://github.com/siyuuuu1014-cell/sprout/actions/runs/38883570451)
为 PR head d08de113 的合并检查，checkout SHA 是 `e6745159f98b9bb4f01360d0b967fbded23a7d56`。
初查 Windows 为 in_progress，复查以下两个 job 均已 completed/failure；日志和报告上传成功。
本轮修正未推送，其远端检查是 **not_run**，不能将旧 CI 红灯或本地绿灯算作新 CI 验收。

| Job | 安装/收集/子集 | 完整离线 L1 |
|---|---|---|
| Ubuntu 24.04 / Python 3.12.15；116915807177 | lock124、install66；imports/ruff passed；2396 collected、0 errors、1 warning；示例18、R00 67 passed | 2336 passed、1 failed、1 skipped、58 not_run、2 warnings；164.85s |
| Windows / Python 3.13.16；116915807399 | lock124、install68；imports/ruff passed；2396 collected、0 errors、1 warning；示例18、R00 67 passed | 2333 passed、5 failed、0 skipped、58 not_run、2 warnings；254.14s |

已证实两平台共同失败为 `test_evaluation_runner.py::test_fixed_suite_and_persisted_report`，
C01/C02/C03/R01 未完成验证、结果6/10。I001 在本地生成文件中已直接复现；远端摘要
未上传其内部 verification.json，因此将共同失败完全归因于 I001 仍是推断，而非远端内部证据。

Windows **另有四项真实失败**，不能与 Ubuntu 混同，也不能归因于原本地唯一失败：

- `test_task_e2e.py:205`：`test_delete_approval_survives_restart_and_controls_commit[delete-approve]`
  和 `[rename-approve]`，final.status 为 failed 而非 completed。
- `test_task_e2e.py:318`：`test_requirement_output_controls_whether_the_change_lands[True]`。
- `test_task_e2e.py:424`：`test_a_proposal_offers_only_the_agents_edits`；后二者同样最终状态失败。

根因定位目前止于真实任务链路完成断言：`test_task_e2e.py::_repo/_runtime/_drive` 经
Runtime 执行/审批/验证/应用后返回 failed。现有日志为 short traceback，artifact 配置只上传
顶层 log/xml/json，不包含 pytest-temp 下任务内部失败证据。尚不能区分验证失败、Git 应用
失败或环境差异，**不声称已定位底层根因或已修复**；未修改 Runtime、原断言或排除这些用例。
需经批准补充这四个用例失败时的诊断证据并在 Windows runner 复验；这是剩余 CI 阻塞。
本轮不擅自推送/重跑远端，不把 Python 小版本或平台差异直接当根因。

Actions 还有 Node20 被平台强制改用 Node24 的警告，但 setup/checkout/upload 均成功，
没有证据证明它造成测试失败；本轮不顺带升级 Action。

### 修正后干净环境完整复验

```powershell
.\.venv\Scripts\python.exe -B scripts/ci_baseline.py --output .workbuddy/test-runs/r01-review-clean-windows-1 --python D:/dev/runtimes/conda/python.exe
```

完整日志、命令、退出码、SHA256 manifest 和 JUnit 留在该独立证据目录；当前源码修改
通过工作树跟踪文件进入快照，summary 的 source_head 仍是修正前 HEAD，manifest 才是精确内容身份。
实际结果：Windows Python3.13.9 / uv0.11.25；lock 124包、sync 新装68包，均 exit0；
imports/ruff exit0；完整收集 **2400项、0 errors、1 warning**，4.25s；示例 **18 passed、
0 failed、0 skipped**，0.13s；R00 **67 passed、0 failed、0 skipped**，0.20s。
完整离线 L1 **2341 passed、0 failed、1 skipped、58 deselected/not_run、2 warnings**，
257.20s，exit0。一个 skip 仍是本地 Windows 无法创建 symlink 的 authz 测试；未算通过。
两条 warning 仍为 Starlette/AnyIO 和 lark protobuf 弃用 API。示例/R00/新增回归已包含
在 L1 中，不重复累加；Windows CI 额外失败的四个用例本地通过，不能据此证明远端修复。
Docker、Temporal、真实模型、外部数据库均 not_run，不继承历史额度。
修正后工作树 `ruff check . --no-cache` 和 `git diff --check` 也已通过。

公开 API/持久化数据/审批/R00 实现不变，uv.lock 不变。回滚只撤回本轮六文件补丁或本地
修正提交，不撤回 R00、原 R01 交付或历史资料。PR 必须维持 Draft，修正后双平台远端复验
及 Windows 四项失败根因仍未解决前，**R01 完整 DoD 未满足，不合并、不进入 R02**。
