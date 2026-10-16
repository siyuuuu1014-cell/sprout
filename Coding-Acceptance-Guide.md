# Coding Acceptance Guide

最新进展：2026-10-16 已完成真实 DeepSeek 的独立 CLI 提交/Worker/观察/同步
验收，5 次请求，停在已验证 waiting_approval；未应用 Git。仍是实验授权策略，
不是生产权限策略验收。详见 Detached-Real-Coding-Acceptance.md；以下早期阶段
记录中的“仍待接线/待实测”应按对应历史阶段理解，不覆盖最新证据。

## 独立 Worker 能力（阶段性接线）

正式CLI新增 `sprout evolution coding-worker TASK --database EXP/metadata.db --config ORIGINAL`。
它从既有已接受提交读取原host/namespace/queue，复用实验绑定检查，只负责前台Worker，
不提交任务、不审批、不自动恢复未知结果。Ctrl+C停止此Worker，不是取消任务的命令。
配置必须匹配原实验实际配置；不能为了让历史实验启动而静默调整快照。

现有开发 `run` 仍是embedded试验，尚未迁移成独立Worker/提交/观察三个进程；
因此新增命令只是生命周期分离第一步，不表示旧入口的等待超时问题已全面解决。
下一步需结果观察接口和跨进程验收，暂不向用户推荐借此强制恢复旧未知任务。

现已增加只读结果观察：

```powershell
uv run sprout evolution coding-status TASK --database EXP/metadata.db --wait-seconds 0
```

`--wait-seconds`为0..60，0只检查当前状态；多个候选提交记录时用`--submission-id`明确选择。
它不提交、不审批、不启动Worker、不改数据库，查询超时不会取消工作流。
`persisted_task_status`是当前任务状态，`result`是原工作流结果；例如独立提交后任务已completed，
原编码工作流仍为waiting_approval，二者同时成立，不能通过改写历史让字段看起来一致。

无模型真实Temporal跨进程测试已验证观察进程退出后独立Worker仍能完成任务；
完整正式编码Worker/任务提交分进程的模型执行验收仍待补齐。

对已准备好、尚无节点和提交记录的新实验，正式入口还可只提交、不等待：

```powershell
uv run sprout evolution coding-submit TASK --database EXP/metadata.db `
  --config ORIGINAL --temporal-host HOST --temporal-namespace NAMESPACE
```

服务端接受后立即返回，不启动Worker；再由另一进程运行coding-worker，第三个进程可用
coding-status观察。已有提交再次调用不会作为恢复或重跑入口，只返回原记录。
当前仅提供提交和只读观察，结果显式同步落库仍待接线；工作流完成不代表观察命令已修改
数据库任务状态。不要把pending/uncertain状态改成“可重跑”，也不要改路由绕过历史记录。

现已提供单独的显式结果同步命令：

```powershell
uv run sprout evolution coding-sync TASK --database EXP/metadata.db
```

仅把可信结果同步至metadata.db的提交记录与任务状态，同库事务，不改节点占用或其他库。
运行中/查询失败不写；原结果waiting_approval不会把后续Git提交完成的任务降级。
这不改变coding-status的只读语义，也不构成未知副作用的恢复授权。

2026-10-16已补正式CLI分进程接线验收：提交/查询/同步均为普通CLI子进程，编码Worker也是
独立CLI子进程，但测试adapter替换模型及授权策略。真实Temporal和Docker完成代码修改、
独立验收、结果落库；Worker在查询进程退出后存活，最终正常停止。该证据证明接线，
不是普通生产权限策略或真实模型分进程任务的再次验收。

隔离命令工具现向模型返回最多8192字符的脱敏执行输出，含退出码、耗时、stdout/stderr；
过长保留头尾。执行反馈不是独立验收授权，不改变审批、单节点执行槽或未知结果规则。

## 统一任务摘要（只读）

```powershell
uv run sprout evolution coding-report TASK --database EXP/metadata.db
```

无需模型配置，不创建Runtime或Worker；只读汇总当前任务阶段、预算、验证、节点及Git提交。
其中historical_result_status与当前task_status属于不同阶段，不能互相覆盖。
旧实验无持久化预算表时消耗/余额显示null，不能据此推断免费或0次请求。
token统计只覆盖持久台账里的成功响应，不代表精确账单；查询不会花费请求额度。
这是分库只读观察，不是跨库原子快照或审批/执行授权。

## 用途与边界

当前支持最小整数加法和三模块购物车两种固定样例：真实模型修改函数、生成测试、通过
Temporal/Docker 执行及独立验收，然后人工审查，另行批准隔离 Git 提交。

这是开发验收入口，依赖源码目录、pytest 和 uv；不包含在发布 wheel 中，
也不是正式的任意任务生产 API。试验使用显式测试授权策略，不能代替生产权限验收。
当前不支持任意任务描述或自定义验收集，也不发布成长候选。
加法样例已有真实模型和提交闭环证据；购物车接线后的真实模型流程尚未实测。

从项目根目录执行下列命令。密钥只保存在 `.env` 的 `DEEPSEEK_API_KEY` 中，
`sprout.toml` 的 `api_key_env` 填变量名，不填密钥。配置必须显式指定。

## 1. 检查配置（不收费）

```powershell
uv run python -m Sprout.tests.coding_acceptance check --config .\sprout.toml
```

只输出配置是否匹配及密钥是否存在，不输出密钥、不检查余额、不连接 Docker/Temporal。
基础设施另需确保 Docker 已启动、编码镜像已在本地、专用 Temporal 位于 127.0.0.1:17233。
入口不拉取或构建镜像，不回退本地执行。

## 2. 显式发起真实编码试验（会收费）

先确认本轮剩余请求额度和费用范围，再填写 `--authorize-requests`（1..20）。
这个参数是实际请求次数上限，不是金额硬封顶；失败请求也占额度，单次输出上限 8192 tokens。
它不会自动追踪多个独立运行间的授权余额，操作者必须扣除已消耗额度，不能重置预算重跑。

```powershell
uv run python -m Sprout.tests.coding_acceptance run `
  --config .\sprout.toml `
  --sample addition `
  --output-dir .\.workbuddy\test-runs\my-fresh-trial `
  --authorize-requests 10 `
  --coding-image sha256:11880faa03b5dda1f3f54a915ad1b0e3d2a97664799d7a9bfcd3790a9dc7a31d
```

示例的 10 是操作示例，不构成付费授权。输出目录必须不存在且父目录已存在，
不可复用历史证据目录。用 `--sample cart` 切换购物车；缺省仍为 addition。
只运行所选固定试验，不触碰当前项目的业务文件或 Git 历史。

报告位于输出目录下的 `test_real_deepseek_minimal_cod0/live-coding-report.json`。
成功表示达到已验证的代码审查边界，任务通常为 waiting_approval，不代表已提交或 completed。
失败或预算耗尽不自动追加请求，未知执行结果不自动恢复或重跑。

## 3. 读取当前状态（不收费）

```powershell
uv run python -m Sprout.tests.coding_acceptance status --report <live-coding-report.json>
```

报告仅用于定位实验数据库，当前状态来自 SQLite，不把历史报告的 passed 当作授权。
因此即使原报告仍写 waiting_approval，完成提交后此命令应显示 completed/applied。

## 4. 审查后明确批准提交（不调用模型，但会修改隔离 Git）

先审查真实差异、验收结果和作者身份。仅在明确批准对应样例文件后运行：

```powershell
uv run python -m Sprout.tests.coding_acceptance commit `
  --report <live-coding-report.json> `
  --config .\sprout.toml `
  --output <fresh-commit-report.json> `
  --approve-reviewed
```

提交会核对报告样例与持久化任务的要求、文件清单和附加检查一致；旧加法报告不含 sample
字段仍按 addition 核对，不能仅改报告的样例名来批准另一任务。
该命令调用既有审查/审批/提交请求/决策服务及独立 Temporal 提交 Worker；
不直接运行 Git 绕过验收，不自动发布成长候选。沿用已有 Git 身份，不修改 Git 配置。
缺身份、配置不匹配或证据不一致时停止；输出文件必须不存在。
这是单次推进入口，不是失败/未知结果的自动恢复器，不要重复执行来绕过等待状态。
未知状态先用 status 和现有 evolution commit-status 检查，必要时人工核对。

## 已有证据

已通过的一次真实任务：7 次请求，输入 14520 / 输出 1198 tokens；pytest 4 项通过，
独立验收、回归与 PoC 均通过，随后在隔离仓库提交 d58063d 并完成任务。
编码报告和提交报告是各自时刻的快照，不回写历史报告。

这是简单任务样本，不代表复杂任务成功率或固定的调用节省比例。下一步应先验证
同入口的独立进程重复使用，再明确多文件样例的验收范围，不继续扩展成长基础设施。
