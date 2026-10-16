# 正常策略使用链路初检（2026-10-16）

## 本轮范围

普通 project task create/submit/observe/sync/report 与正式 task-worker，
真实专用 Temporal/独立 SQLite/独立 Git 样例，使用原有权限策略，不安装
LiveCodingPolicy 或离线编码授权适配器。只有 Worker 退出信号由测试 harness
投递。模型为标准离线 echo，不是能完成编码或结构化生成的模型。

任务：增加 increment(value)=value+1，并测试正数、负数、零。
独立样例基线提交沿用已有 Git 身份，没有修改身份配置或当前项目 Git。

## 真实观察

正式 CLI 创建、提交正常，Worker 完成 read/sandbox/plan；测试设计节点要求
结构化 cases 数组，而 echo 返回回显文本，解析失败：
`ValueError: Acceptance design must contain a cases array`。

该节点保留 uncertain 占用，workflow 返回 waiting_resource，显式 sync 与报告
一致，未伪造 completed。approvals list 返回空，源 app.py 不变，Worker 正常退出。
因此本轮没有到达模型编辑、命令审批或代码审查，不能声称生产授权体验已验收，
也不能把这个错误归因于权限拒绝或真实 DeepSeek 行为。

## 待改进点

1. UI/报告应区分模型输出格式错误与未知外部副作用；本轮不直接放开旧占用重跑。
2. 现有 CLI create 没有显式传入结构化验收案例的选项，普通任务主要依赖测试设计
   模型产出；应考虑补充可选验收输入，但不能静默塞入实验固定答案。
3. 下一步使用正常策略和真实模型的新任务继续检查审批链路，需明确允许使用
   付费额度。旧任务保留证据，不复用或强制恢复。

真实诊断专项 1 passed（6.80s），表示成功复现并记录等待，不是编码任务成功。
证据 .workbuddy/test-runs/normal-policy-cli-final-1 下 production-policy-evidence.json。
付费请求 0，无变更批准/应用/成长发布。首次预检曾因测试 Git helper 隔离环境
无法读取已有身份而停止，随后基线准备改用正常 Git 配置，没有虚构身份。

## 真实 DeepSeek 续验

用户明确授权使用上一轮剩余最多五次请求，每次最多输出 8192 tokens。
本轮使用正常 Runtime 工厂和 ModelOrchestrator、正常策略与正式 CLI/task-worker，
不注入模型或工具授权适配器。仅为新独立任务在执行前设置 TaskBudget.max_model_calls=5，
临时配置使用原 DeepSeek 路由/密钥变量名并限制输出；没有复制密钥值或修改用户配置。
创建使用默认本地策略规划，没有模型规划请求；所有本轮请求均由节点持久化账本计数。

真实诊断专项 1 passed（23.40s），表示正确记录等待而不是任务编码成功。
任务 6199221a-a613-4dcc-a0d3-3ce44814067e，当前 waiting_resource/stage=budget_stopped。
五次请求全部收到响应，输入 7030/输出 2104 tokens：PLAN 四次、TEST_DESIGN 一次。
第六次请求在本地预算预留处拒绝，未发送。审批列表为空，未执行编辑节点、未批准
或提交，源 app.py 不变。原实验额度现已全部用完，不自动追加或恢复旧节点。

证据：.workbuddy/test-runs/normal-policy-deepseek-1/test_gateway_submit_worker_obs0/production-policy-evidence.json。

### 暴露的问题

1. 规划节点占了四次调用，完成较长方案后仅剩一次测试设计调用，测试设计尚未返回
   最终验收结果就触发下一次调用的预算阻断。请求分布可确认，但不能据此说测试设计
   已成功，也不能把预算不足归因于提供商失败（五次均 responded）。
2. 策略层 RequirementPlanner._default_test_path 硬编码 src/Sprout/tests，并仅用空格
   拼接 slug；对独立 app.py 样例仍生成带括号/分号等字符的 Sprout 测试路径。
   模型方案因此打算在目标项目创建 Sprout 包结构和重复测试。这是策略规划耦合
   当前仓库结构的真实缺陷，不是用户目标项目要求。模型关于 pytest 收集的解释
   未独立验证，不作为事实采信。
3. VerificationPlanner 默认硬编码 ruff check src，而样例没有 src，语言/工具也未
   依据目标项目证据选择。尚未执行到验证节点，但该命令已经进入持久化任务图。
4. 预算已正确止损，但没有节点阶段额度分配；报告未知结果提示仍过于笼统。

### 建议下一步

先修复目标项目路径/验证命令推导，再限制简单任务规划的重复探索及阶段预算挤占；
用离线项目矩阵回归验证，不继续为同样的结构性错误消耗真实模型额度。
没有真实工具审批证据，正常审批体验仍待新任务补验；不释放旧占用强制重跑。
