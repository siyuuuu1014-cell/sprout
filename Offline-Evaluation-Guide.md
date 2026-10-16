# 本地固定任务评测与 Python 闭环 Demo

## 目的与边界

这套工具用于回答“现有工程链路是否按预期工作”，不是通用 Agent 排行榜。它使用仓库内版本化夹具和脚本模型，调用现有 Runtime Factory 与公开 Runtime API，不另建编码执行引擎，不读取真实账号，不调用付费模型，不自动安装依赖。

每次新建独立的 Git 项目、SQLite 数据库、项目事实库和证据目录。模拟审批仅绑定本次夹具任务；真实项目的审批与权限规则不变。评测中 Git 初始化是创建测试夹具，不意味着业务入口开始自动为用户初始化 Git。

Git worktree 和外部输入输出检查器不是 OS 安全隔离。这里只运行第一方固定代码，不接收任意项目路径或模型生成的外部代码。真实模型能力、容器隔离和真实 Temporal 服务联调仍未测量。

## 运行

当前开发环境需有 Python、Git、pytest、Ruff；缺少工具时明确报错，不自动下载。设置了 `TEMPORAL_HOST` 时拒绝运行，避免向真实服务提交评测工作流。

```powershell
# 全部十个固定场景；默认证据放系统临时目录
uv run sprout project eval

# 保留在已有忽略目录下，每次仍创建新的子目录
uv run sprout project eval --output .workbuddy/evaluation-evidence --json

# 单个 Python 输入输出闭环样例
uv run sprout project eval --case C02 --output .workbuddy/evaluation-evidence

# 选择多个固定场景
uv run sprout project eval --case C04 --case C05 --json
```

任何场景检查失败，命令退出码为 1；参数或环境不满足时不创建任务。目录不会覆盖已有报告，也不会自动删除证据。长时间运行会累计证据，清理应针对明确的旧运行子目录，不删除用户项目或整个工作目录。

## 十个场景

| ID | 验证内容 | 成功含义 |
|---|---|---|
| A01 | Python 函数、导入边与来源；文件 SQLite 重启读回 | 符号及带来源的边存在，项目图与事实保持一致 |
| A02 | router 字面量前缀和独立的动态挂载 | 精确组合静态路径，动态项保留警告，不宣称已部署 |
| A03 | 本地声明式 ORM 和 SQL CREATE TABLE | 表声明及来源正确，不宣称完整或 live schema |
| C01 | 修复负数边界 bug | 修改前验收失败；修改后原正数回归、验收、PoC 与独立检查通过 |
| C02 | JSON 输入输出函数 | 正常、空列表、非法输入有固定期望；生成最小 demo 并实际执行 |
| C03 | 两个模块协作的小改动 | 验收通过；补丁只含规定实现文件及冻结测试/demo |
| C04 | 故意错误的实现 | 任务失败且不应用；不能把场景检查通过计作任务完成 |
| C05 | 故意错误实现的强制应用 | 可应用并保存原因，但 verified=false 且任务不是 completed |
| R01 | 等待审批时关闭重开、多次恢复 | 原任务正常完成；随后恢复拒绝或无操作，Git 不重复应用 |
| R02 | 只读任务伪造写工具；删除审批拒绝 | 零写入/提案；删除拒绝保持等待，最后由评测显式取消 |

无结果、跳过、runner 错误和超时分类继续由 T01 专项测试覆盖；这些并不全部是本套十场景中的独立进程故障注入。R01 是本地 Runtime + 文件 SQLite 恢复，不代表 Temporal 网络故障窗口已经修复。

## Python C02 的实际链路

固定需求 → Runtime 创建显式编码任务 → 扫描/规划 → Git worktree → 冻结输入输出验收与 demo → 脚本模型通过正常 sandbox 工具写代码 → 原有 EVALUATION → 任务范围内模拟审批 → ApplyBroker 应用并提交 → 独立输入输出复核。

`src.app:summarize` 的期望：

- `{"values": [2, 3]}` → `{"sum": 5, "count": 2}`。
- `{"values": []}` → `{"sum": 0, "count": 0}`。
- `null` → `{"error": "invalid"}`。

验收前先检查旧代码确实未满足这些要求，避免“任务本来就通过”被误计为修复。工作树之外的独立检查器使用固定期望、当前 Python 和 `-I`，不加载项目 pytest/conftest；它在实际应用后再次检查所有输入输出。检查器、夹具和源码均有摘要可核对。PoC 本身由既有 Runtime 渲染/冻结/校验流程产生，不再造一套 demo 完成标准。

## 证据与口径

每个场景目录保留：

- `fixture.json`：固定需求、初始源码、脚本改动和期望输出。
- `repo/`：实际独立 Git 项目与成功应用后的代码、测试、demo。
- `baseline.json`、`independent_result.json`、`independent_oracle.py`：应用前后检查及 Git revision。
- `task.json`、`nodes.json`：任务、计划、冻结验收摘要、逐阶段状态与输出。
- `proposals.json`、`approvals.json`、`verification.json`：补丁、审批、豁免和共享验收报告。
- `trajectory/`、`audit.jsonl`、SQLite/项目事实库：原有运行证据；分析场景另有 `analysis.json`。
- `result.json`：单场景结果。顶层 `partial-results.json` 和 `report.json` 给出进度与最终摘要。

`scenario_passed` 是场景契约通过数量，不是编码任务完成数量。十场景全部通过时，应有四个验证完成的编码任务；错误实现和强制应用场景也必须检查通过，但其编码任务不能完成。报告另给失败阶段、验收失败代码分布、无效基线、源码/夹具摘要、环境路径、耗时与零付费模型成本。

实际环境可能让 `python`、`pytest` 和独立检查器使用不同 Python 安装；报告记录这些路径，不把不同安装伪装成统一环境。后续 runner 环境绑定与真实模型对照应沿用这组夹具，而不是放宽验收。

## 本轮基线（2026-10-12）

实际 `sprout project eval` 命令：10/10 场景检查通过，四个编码任务验证完成，基线无效数为零，耗时约 33.23 秒。故意失败场景记录两次 `check_failed`，强制应用记录一次 `verification_overridden`，这些是预期的保护行为，不是被掩盖的成功。

本轮证据位于 `.workbuddy/evaluation-evidence/sprout-eval-82inmaut/report.json`。
Python demo 位于该运行的 `C02/repo/examples/sprout_demo_fd8a60a2e20d8772.py`；
具体正常/边界/非法输入输出见 `C02/independent_result.json`。

最终核心与 Web 回归：1353 项通过、7 项跳过、2 条既有依赖弃用警告，136.08 秒。仍明确排除缺少原有示例模块的 `test_bubble_sort.py` 与 `test_hello_world.py`，不将跳过或排除项算作通过。Ruff、diff 空白检查与 eval CLI help 检查通过。

## 模块归属与后续

`evaluation/fixtures.py` 拥有固定场景，`model.py` 拥有脚本响应，`runner.py` 只组装环境、调用业务 API、核对独立期望并写证据。CLI 是薄入口。评测产物是可重新生成的证据，不是第二个 Task/Project 数据权威；任务运行仍使用已有事件和存储。未通过 MCP 暴露，因为运行会创建项目、执行代码并模拟审批。

下一步 T03：先注入 Temporal connect/start 超时和重复调用，证明持久提交与执行权威问题，再修幂等 dispatch 和恢复。真实服务或付费模型实验需要单独明确启动，不能由评测默认触发。
