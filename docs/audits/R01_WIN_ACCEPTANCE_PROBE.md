# R01-WIN-DIAG — Acceptance 启动证据（本地补丁）

日期：2026-10-17。基线：`a2205858bb14e745d5d05fea5b33b4bbfa4b370a`。
分支：`codex/r01-win-acceptance-probe`，独立 managed worktree：
`C:/Users/WingYouther/.codex/worktrees/r01-win-acceptance-probe/sprout`。
原 worktree `D:/dev/projects/sprout` 和分支 `codex/r01-baseline-ci` 保持 b7f759c；
不 reset/amend/rebase，不恢复或再次删除 README，不将该删除纳入本分支。
远端 R01 基线仍为 a220585，dev 为32e5f734。本轮只允许本地诊断提交，不 push 或改 PR。

## 已知事实与待验证假设

此前 run39000738780 的 Windows 五项失败均为 Acceptance exit1/module_missing，
verified=false，Apply verification_blocked，HEAD不变；Ubuntu 同一提交全量L1成功。
现有摘要没有缺失模块类别/原子解释器身份，不能把 module_missing 直接等同于 pytest 缺失。

Python [subprocess 官方说明](https://docs.python.org/3/library/subprocess.html)指出 Windows
shell=False 时，env 不能替代可执行程序搜索所用 PATH。这仅支持裸 python 的解释器选择
假设，不证明这些场景实际选择了哪个解释器。本轮不修改命令或进程启动行为。

## 最小观测位置与不变契约

既有链路：`execution/acceptance.py::acceptance_commands` →
`runtime/nodes.py::_evaluation` → `ProcessBroker.run` → 已持久化的 VerificationCheck.output
→ 原 R01-DIAG 的 project 投影。只在投影验收检查时增加 acceptance_launch 解析。

新增 `scripts/ci_support/acceptance_probe.py` 是被动纯函数：**不启动环境探针或第二个
验收子进程**，不拦截 ProcessBroker，不改 argv/env/cwd/PYTHONPATH/sys.path，不安装包。
`diagnostics.py` 仅对 acceptance 类型添加字段；Runtime、Broker、acceptance.py、审批、
冻结指纹、Oracle、业务断言、验证门禁、workflow矩阵及上传路径均不变。

## 原子进程证据与字段白名单

从原检查已保存的输出中识别 CPython 启动错误格式：绝对路径前缀 + `No module named`。
该前缀作为**子进程自身报告的解释器路径**，只在内存中与预期测试解释器 sys.executable
比较；Windows比较忽略大小写并规范斜杠。不是在外层执行 which 或把外层 sys.executable
冒充子进程值，也不是OS进程镜像/所有环境相同的证明。无法识别绝对前缀时保持 null。

每个验收检查新增 `launch_diagnostic`，仅包含：

| 字段 | 允许值与证据意义 |
|---|---|
| evidence_source | 固定 original_acceptance_check，无辅助环境探针 |
| reported_executable_matches_expected | true/false/null；原错误前缀与预期解释器路径比较，非物理文件身份保证 |
| launcher_imports_pytest | false：原启动错误明确缺 pytest；true：该 python -m pytest 已成功，或已出现 pytest收集/执行失败标志；否则null |
| missing_module_category | pytest / project_module / other_dependency / unknown |
| failure_phase | before_pytest_startup / collection / test_execution / unknown / not_failed |

pytest/src 两类是固定白名单：src仅对应这批第一方验收 fixture 的已知包根，不扩展为通用
项目模块识别器；其他模块只标other_dependency，绝不导出名称。启动前缺 pytest与
测试过程中某导入失败区别对待；后者不推断启动器不能加载 pytest。

成功的 Ubuntu 进程通常没有解释器前缀，此时一致性应为 null，而非“因为通过所以相同”。
报告被截断、缺文本、无法确定阶段或未执行时保留 unknown/null，不补造根因。
沿用既有 observation_only/root_cause=not_established，解析出的签名不是新的验证权威。

不上传原stderr/stdout、完整路径、用户目录、环境变量、导入名、对话/审批参数/补丁或DB。
原 Artifact 文件名不新增：baseline.json及失败时最多八份固定场景JSON；只有验收检查对象
增加上述五个白名单字段，现有public-diagnostics/*.json上传范围不变。

## 回归与验证

新增11项回归：Windows报告路径不一致、大小写/斜杠规范化；pytest/src/其他依赖分类；
pytest收集/执行阶段；未知/未执行不造证据；成功不推断子解释器身份；新字段接入原投影；
私密路径/模拟密钥/导入名不外泄。

一个单元测试执行真实**合成**子进程 `sys.executable -I -S -m pytest`，刻意排除site包，
证明解析器能读取该子进程启动错误。它不等于原验收调用的远端观测，不用于断定Windows根因。
新旧诊断测试初次验证：26 passed、0 failed、0 skipped，1.08s；ruff通过。

独立完整基线命令（新文件逐个显式暂存后供Git跟踪清单快照）：

```powershell
D:/dev/projects/sprout/.venv/Scripts/python.exe -B scripts/ci_baseline.py --output D:/dev/projects/sprout/.workbuddy/test-runs/r01-win-probe-clean-1 --python D:/dev/runtimes/conda/python.exe
```

最终独立环境：Windows/Python3.13.9、uv0.11.25；lock124包、sync新装68包，
imports/ruff/lock/sync全部exit0。完整收集 **2426项、0 errors、1 warning**（6.42s）。
示例 **18 passed、0 failed、0 skipped**（0.18s）；R00 **67 passed、0 failed、0 skipped**
（0.21s）。完整离线L1 **2367 passed、0 failed、1 skipped、58 not_run、2 warnings**，
487.34s，exit0。11项新增回归包含于完整L1，不重复计数。
单项skip为本地symlink不可用；warnings为既有依赖弃用提示，均不算通过证明。
Docker/Temporal/真实模型/外部数据库not_run，未调用付费模型。
完整结果以独立目录summary、log、JUnit、source-manifest为准。
本轮不执行新的远端CI，Ubuntu/Windows对子解释器的新字段比较均为not_run。

提交前只读Git检查在沙箱内曾返回worktree识别错误；在受保护managed目录的沙箱外
重新执行同一检查成功，未调整Git配置/路径/元数据。最终展示四文件staged diff后才
本地提交。原分支继续保持b7f759c和干净状态，README删除不混入本分支。

## 后续修复分界（未实施、必须另行批准）

只有远端原验收输出确认“报告解释器不同且启动前缺pytest”，才进入启动方式修复评审；
若是project_module或其他依赖，应沿具体导入链调查，不能套用解释器替换。
即便路径不同，仍须区分已观察路径差异与导致模块不可用的完整因果关系。

最小生产方案需要分别明确：Local 后端的可信Python由服务端/运行环境确定，不能接受模型
给出的路径；Temporal使用实际执行worker的环境，不使用提交端路径；容器/隔离后端使用
该后端已验证的解释器，不能塞入宿主机sys.executable，更不能回退宿主机。
不能把全局替换裸python视为现成修复。

命令若改变会影响既有冻结命令及授权指纹，应走新设计/重新验证和必要的重新审批，不能
原地篡改历史任务命令或借诊断继承授权；回归应覆盖不同后端、旧任务冻结记录、审批不
复用、原十场景/四项端到端及完整L1。生产安全边界不应扩张，本轮不作任何这些修改。

回滚仅撤回本分支诊断/测试/说明提交，不动原分支、b7f759c、已有R01/R00或用户数据。
**本地诊断补丁通过不等于Windows根因已证实，R01 DoD仍受远端五项失败阻塞。**
