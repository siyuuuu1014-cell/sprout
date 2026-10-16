# 真实模型独立进程编码验收（2026-10-16）

## 结论

最新状态：用户随后明确继续，已通过现有审批/提交服务完成隔离 Git 应用，
当前 task=completed、commit=applied。以下 waiting_approval 为编码结束时的历史
快照，不回写原 live-coding-report.json。提交收尾见文末。

本轮达到已验证的人工代码审查边界，任务 waiting_approval，非 completed。
没有 Git 应用、没有成长发布。原独立样例仓库未修改，当前项目业务代码未改。

授权最多 10 次 DeepSeek 请求，每次最多输出 8192 tokens；实际持久化计数 5 次，
成功响应 5 次、未知/失败 0 次，输入 9725 / 输出 1123 tokens。
剩余 5 次未使用，不自动发起下一轮。请求次数上限不是金额硬封顶，未查询账单。
真实专项 1 passed（24.71s），独立 Worker 正常退出码 0。

## 实际链路

新独立样例与五类 SQLite → 正式 coding-submit 子进程 → Temporal →
正式 coding-worker 子进程 → 真实 DeepSeek/AgentLoop → Docker 编码 →
独立验收 → coding-status 子进程 → coding-sync 子进程 → waiting_approval。

命令由显式测试适配器启动，加载真实配置并在内存限定输出 8192 tokens，
不改用户 sprout.toml/.env；代理模型使用直接 provider，无编排器自动重试。
任务持久化请求账本与进程内额度共同限制请求；观察/同步不调用模型。
Worker 保持独立运行，观察结束后由测试 SIGINT 正常停止，不取消 workflow。

沿用既有 LiveCodingPolicy 实验授权：允许隔离工具，不代表普通生产权限策略
已经验收；路径/文件 broker、镜像绑定和隔离执行仍由真实实现执行。
使用 evolution 专用入口，不声称普通 project task-worker 已支持隔离成长实验。

## 模型产物及审查

- app.py：add(a,b) 改为返回 a+b。
- test_generated.py：新增正数、负数/混合符号、零三组测试，包含七个断言。
- 当前只读 coding-report：verification=passed，reported_verified=true，
  五项检查，失败列表为空；read/sandbox/plan/test_design/agent/evaluation 均
  completed，approval 为 waiting，commit_requests 为空。
- 人工静态检查未发现样例硬编码返回值、删测试或绕过验收；本轮只代表这个
  最小整数任务，不能推出复杂多文件任务成功率或生产模型质量。

## 证据

报告：.workbuddy/test-runs/deepseek-detached-live-1/test_real_deepseek_minimal_cod0/live-coding-report.json

实验：同目录 experiments/growth-replay-_go1cdkr。

任务：6c6394b1-17f7-4f04-8f14-95a431807ae8。
待审提案：99fef2f2-d105-4a06-a9c5-08fc4660a0d7。
未应用文件位于 .sprout-sprout-sandbox-16531c07，独立 Git 基线和数据库保留。

新增 live_coding_cli_fixture 仅为显式付费测试安装；普通导入不会修改工厂。
未授权时真实测试跳过，离线预算/提交回归 15 passed、1 skipped，静态检查通过。

## 后续

如需完成 Git 闭环，先确认审查上述两文件，再明确授权隔离提交；本轮不自动批准。
生产工具审批体验、普通 Gateway 编码链路和更复杂任务另行验收，避免将实验授权
与生产功能完成混为一谈。

## 审查批准后的 Git 收尾

用户授权后再次检查 app.py 差异、新增 test_generated.py 和既有验证证据，
沿用已配置 Git 身份，不修改全局/项目身份配置。调用 coding_acceptance commit
→ ExperimentCommitService 审查/决定 → submit_cli_commit → 独立提交 Worker，
没有直接执行 git commit 或修改数据库状态绕过服务。

- 请求：171017ea-e583-529a-9c5e-643d00cf92cd。
- 基线：94480eb6595b38814971a745a95bcba09c9368ca。
- 结果提交：3fe2e3f1285b49130fabcbae401d99840c0ffe87。
- 当前数据库状态：task completed、request applied、decision approved。
- 目标实验 repo 干净；original 仍在基线且无修改；当前项目没有 Git 提交。
- 本步骤模型请求 0，整轮仍为 5/10 次；published=false。
- validation_committed=false 是原提交结果字段，不能把 Git 应用当作新增验证，
  验证依据仍为此前真实 evaluation 及已有提案证据。

新报告：同实验顶层 commit-closure-report.json。原编码报告保留历史 waiting_approval，
通过 status 从数据库读取当前 completed，不覆盖历史以伪造单一时刻的状态。

这证明最小样例在实验授权下的模型编码→测试/独立验证→人工审查→隔离提交闭环，
不代表生产权限策略、任意复杂任务或自动成长收益已经完成验收。
