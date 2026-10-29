# R04-B 范围冻结与风险交接

日期：2026-10-21；分支 `codex/r04-broker-zero-bypass`。
HEAD：`56c22ad84c7e4e43dcd0408bb945d8168949b29c`。

**当前状态：通用 Git 与 ApplyBroker 封闭子范围 PASS；R04-B 整体 HOLD。**

本文只整理既有审查、Mock 与本地提交证据；不新增源码审计、实施方案或测试。PASS 指源码与已执行 Mock 层的拒绝合同，不是原生执行能力、全工作流或整体 R04 验收。

## 已提交的六项修复

| 修复 | 本地 Commit SHA | 已冻结合同 |
|---|---|---|
| B1 | `5de0547f1be185755d56f6ae16ca50ba4b8200cf` | 缺 GitBroker/未支持操作不回退宿主 Git |
| B2 | `8750ba55224222c2fb26799941905ba79e62c0f5` | status/diff S1 默认拒绝框架；无生产安装/视图签发器 |
| B3-min | `33a7b06946a0b086a9078bb4893e4fd39f1262ed` | 内置 GitWriteTool 全21项退役；Broker六项非S1 API及裸执行叶子封闭；确切内置工具在审批前拒绝 |
| Apply A1 | `e8d0e88163189cae2d20afa18c7d0dbe24bd5dd0` | 非空Patch/空文件集拒绝；移除 add-A 兜底 |
| Apply A2 | `0987b02c9f909205e7287c6e4f3511519292b5de` | 既有staging及Index检查失败/未知提前拒绝；仅时点检查，不保证无并发污染 |
| Apply FC | `56c22ad84c7e4e43dcd0408bb945d8168949b29c` | 暂停公开apply/rollback及私有Git/补偿叶子，在审批消费、Patch和Git前拒绝；保留A1/A2源码防护 |

## 封闭范围与仍可执行路径

- 内置公开31项中：Write21项全部拒绝；Inspect6项未支持拒绝，log/branch由退役Broker拒绝，status/diff因S1缺可信签发器拒绝。既有批准、自定义Policy ALLOW、普通metadata不能恢复这些内置能力。
- ApplyBroker.apply/rollback均返回能力不可用；空Patch也不算执行完成；`_git`、`_undo`及旧模块runner为拒绝叶子。正常Apply、force、rollback当前均不可执行，不能因已有验证或批准而放行。历史状态不被改写，force原理由/审计与verified=False保留。
- **未封闭且不能继承上述保证：** GitWorktreeSandbox、Local SandboxGitTool、IsolatedGitWorktreeSandbox的宿主管理命令、isolated_git_coordinator/writer、成长实验本地clone/checkout、Skill Git clone与脚本、经过ProcessBroker/CliRunTool的合法命令，以及CliDetect/Grep等独立工具。它们仍可能运行宿主程序；同名自定义Python插件不属于确切内置类封闭保证。此列表来自B-Final，不是本轮重新验证的源码结论。

## 残余风险主责任（单一主归属，依赖不重复建系统）

以下为既有风险的交接标签，不代表启动或完成相应工作包：

| 风险域 | 唯一主责任工作包 | 关联依赖/约束 |
|---|---|---|
| Apply/rollback功能恢复、源/补丁/文件/Index/HEAD/目标/执行器一致性及效果 | R04-B-internal-Apply | R05提供可信执行环境/OS保护；R06/R06b补跨入口关联及持久恢复。复用现有审批与事务，不建第二套协调器 |
| Sandbox读取、add-N、worktree/branch生命周期及宿主helper/config | R04-B-internal-Sandbox | 依赖R05隔离准入、R06关联；不自动套用B2 S1 |
| 已有isolated提交与成长replay的Git执行、安装/视图和UNKNOWN证据 | R04-B内部隔离Git/Replay执行边界 | 依赖R05 OS保证、R06持久事务绑定；保留已有isolated coordinator/writer，不另建ledger |
| Skill脚本/获取、通用CLI及辅助进程的宿主执行与重试 | R04进程/Skill执行边界（F03） | R05受限执行；R06/R06b身份/效果；R17只接后续供应链增强，不替代当前拒绝责任 |
| 未分级MCP调用及远端超时效果 | R04-C/MCP（F08） | R06上下文/效果；后续R17增强，不把风险延后即视为通过 |
| DB_READ可派发状态变更SQL | R04数据库动作边界 | R06关联；不归为Git已修复 |
| 下载缺NetworkBroker的直接网络/文件路径 | R04网络/下载边界（F03） | R05隔离、R06关联；不归为Git已修复 |
| 公共P1读取过滤参数、真实S1恢复 | R04-B读取能力恢复（延后） | 有独立需求与安装/稳定视图证据后另批；无公开P0需求，不扩张B4–B7 |

责任转移只确定主归属，**不消除失败、不构成验收通过**。共享安装/config/helper风险由各实际执行路径的主责任包落实；共用R05/R06证据与组件，不为每条路径复制审批/事务体系。

## 功能恢复与整体验收阻断

任何恢复Apply/rollback的方案，必须同时具备：服务端授权且可证明的可信执行器/依赖；执行期稳定受控仓库视图；冻结的Proposal、Patch、文件集、HEAD、Index、目标、Scope、身份来源及验证证据；针对同一计划的精确审批；复用既有权威事务的持久UNKNOWN、跨进程单次写入及可信结果settlement。旧批准、force、PATH首次发现、一次config扫描、内存锁或普通trusted标记均不能替代。

当前只是停用，不具备上述恢复前提。完整R04还受未封闭内部Git、Skill/CLI、MCP、DB和网络路径及其效果定义/测试证据阻断；不能因取消通用Git功能扩张或封闭Apply而宣布整体完成。未获新范围授权不得恢复执行或启动其他包。

安全回滚保留拒绝、A1/A2、Policy DENY与A2可信CLI审批交接；不得恢复裸runner、add-A、旧宽批准或自动补偿，不改写历史事务/审批记录。

## 历史验证与证据引用

各组重叠，不累计；均为提交前历史运行，本轮没有重跑：

| FC阶段测试 | Passed | Failed | Errors | Skipped | Python退出码 |
|---|---:|---:|---:|---:|---:|
| 新FC专项43项 | 43 | 0 | 0 | 0 | 0 |
| 原62项（原断言保留） | 30 | 32 | 0 | 0 | 1 |
| 关联452项 | 450 | 2 | 0 | 0 | 1 |

原33项RED中21项仅因零执行转绿，不代表其底层机制实现；12项仍期待执行后UNKNOWN。其余20项旧62测试期待特定拒绝理由、Index探测或成功执行。关联2失败分别期待Mock内部Apply/rollback成功、执行revert后返回reconciliation；均未改写/skip/xfail。更早原审计65 passed/49 failed（35旧合同、14未解决/延后风险）继续作为历史记录，不被FC结果覆盖。

- [B-Final](R04_B_FINAL_REVIEW.md)：31项矩阵、内部消费者、49项历史分类。
- [Internal Apply Design](R04_B_INTERNAL_APPLY_DESIGN.md)与[Mock RED审查](R04_B_INTERNAL_APPLY_RED_REVIEW.md)：风险、接口与架构阻塞，属当时快照。
- FC完整Diff/测试/证据：`C:/Users/WingYouther/AppData/Local/Temp/sprout-r02-local-1c0815af/r04-internal-apply-fc-new-final/REVIEW.md`；同根目录`r04-internal-apply-fc-old62`、`r04-internal-apply-fc-controls`保留command/JUnit/log/summary。
- FC提交后的哈希与提交证据见当前HEAD；提交后未重新运行测试。

**完整L1、真实Windows/POSIX Git、Hook/helper、网络、真实仓库工作流：not_run。** 历史Windows原生崩溃、长路径、symlink限制及58项外部not_run记录不因本次交接解除。

本轮只新增本文。无Stage、Commit、Push、PR、Merge、Hook执行或新阶段实施；等待R04-B收尾范围与功能恢复独立裁决。
