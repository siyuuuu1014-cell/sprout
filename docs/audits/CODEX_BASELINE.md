# Sprout Phase 0 开发基线

审计日期：2026-10-16。范围：交接资料安装、当前源码复核、非破坏性基线检查和实施计划。**未修改业务源码，未开始修复。**

## 1. 仓库与资料

- 工作区：`D:\dev\projects\sprout`。
- 分支：`dev`；HEAD：`762eaf3c62476d2377302329151a759c3d297baa`，与历史审计基线完全相同。
- origin：`https://github.com/siyuuuu1014-cell/sprout.git`。本轮没有 fetch/push/merge/commit。
- Git 树有 844 个 tracked blob；文件树检查、关键调用链审阅不等于全仓逐行审计。
- 接手时已有 8 个 tracked Markdown 删除，原样保留：`Node-Execution-Recovery-Guide.md`、`Normal-Policy-Usability-Check.md`、`Offline-Evaluation-Guide.md`、`Open-Source-Agent-Reference-and-Roadmap.md`、`Repair-Progress.md`、`Replay-Recovery-Guide.md`、`SEAM-Sprout-Architecture-and-Workflow-Overview.md`、`Temporal-Submission-Recovery-Guide.md`。不得归因于本轮，不自动恢复。

交接源是已解压目录 `D:\Download\Sprout_Codex`，没有必要再解压邻近 ZIP。安装了 26 个文件：根目录 `AGENTS.md`、`CODEX_START_PROMPT.md`、`CODEX_FIRST_FIX_PROMPT.md`、`README-交接说明.md`；`docs/architecture/` 下 2 个 Markdown、2 个原始 DOCX、`media/` 下 18 个 PNG。目标没有同名冲突，未覆盖原文件、未运行包内脚本。

安装检查包括：路径规范化和目标目录约束、拒绝 reparse point/越界路径、扩展名白名单、复制前后逐文件 SHA256 相等。18 个 Markdown 图片引用均指向项目内现存文件；18 张 PNG 通过 Pillow 完整性检查；2 个 DOCX 通过 ZIP CRC、XML/RELS 解析及必需部件检查，无 VBA 宏。原始 DOCX 未编辑，**未做分页渲染或视觉排版认证**；外部超链接未联网验证。使用 documents 技能指导 DOCX 结构检查。

## 2. 当前架构与权威边界

保持 Python 模块化单体，不增加第二套 Agent 框架。以下是调用链索引，不表示每个入口拥有完全相同的状态语义：

| 层 | 当前实现及连接 | 需要保留的合同 |
|---|---|---|
| 入口 | `pyproject.toml:35` → `Sprout.cli.app:main`；`web/webapi/app.py:83` 创建 Web 应用；`src/Sprout/gateway/runtime_gateway.py:22` 提供 facade；MCP server 位于 `src/Sprout/mcp/server/server.py` | CLI/Web/MCP/SDK 共用现有 Runtime，身份由服务端确认 |
| 意图与任务 | `runtime/runtime.py:750` → `_recognize_intent`（1257）→ classifier（1315）；`gateway/task_adapter.py:47` 转换 Task；`task/models.py:19` 为权威状态枚举 | 会话消息与显式任务入口分开；metadata 不得赋权；旧任务类型保守处理 |
| 编排 | `orchestration/service.py`、`temporal_workflows.py`、`terminal/local.py`、`terminal/temporal.py`；`execution_guard.py:39` 在节点执行外层持久占用 | Local/Temporal 是两条实际执行路径；提交不明不回退 Local；未知效果不自动重跑 |
| Agent | `runtime/factory.py:143` 注册 AgentLoop；`runtime/nodes.py:435` 路由并执行 agent；`agent/loop.py:112` 构建上下文、规划和模型/工具循环 | 保留 router、planner、AgentResult；每次运行的状态必须独立 |
| 动作 | `agent/executor.py:71` → `tools/executor.py:70` 附近 → `security/access.py:52` 的既有 ActionRequest → 策略/审批/Broker | 不创建同名第二套 ActionRequest；不能把外层策略存在误写为底层 Broker 已全覆盖 |
| 隔离和交付 | `sandbox/git_worktree.py:157` 为宿主执行；Docker 编码后端在 `execution/`；`runtime/changes.py:133` → verification blocker（149）→ ApplyBroker（`execution/apply.py:73`）；隔离 Git 有独立 evidence/coordinator | 工作树不是 OS 沙箱；普通批准不豁免验证；强制应用保留原因但不代表验证完成 |
| 数据与成长 | `storage/bootstrap.py`、`storage/topology.py`、`storage/local/` 与项目分区实现；`workspace/graph.py:49` 已有图构建；`evolution/isolated_replay.py`、`candidate_manager.py` 等已有验证/发布流程 | 不重复设计存储或成长系统；现有候选、证据、发布占用与等待状态必须兼容 |

模块耦合的主要风险不是“模块不存在”，而是跨边界投影丢失：入口身份 → ToolExecutor；AgentResult → NodeResult；会话 ACK → Task 状态；验收/批准 → 真实 Apply。具体源码证据见 [F01–F23 复核](FINDINGS_REVALIDATED.md)。

## 3. 实际执行结果

工具：uv 0.11.25、Python 3.13.9、pytest 9.1.1、ruff 0.16.8；`pyproject.toml:5` 要求 Python >=3.12，开发依赖在第 48 行，已有 `uv.lock`。

| 检查 | 实际结果 | 能证明什么 / 不能证明什么 |
|---|---|---|
| `uv lock --check --offline` | 通过，Resolved 124 packages；沙箱缓存权限失败后，经批准在沙箱外重跑成功 | 锁文件检查，不是 fresh install/build |
| `.venv\Scripts\ruff.exe check . --no-cache --statistics` | exit 0，无发现 | 未运行 --fix；不等价于功能正确 |
| `.venv\Scripts\python.exe -m pytest --collect-only -q src/Sprout/tests -p no:cacheprovider` | exit 1：2280 tests collected，2 collection errors | `test_bubble_sort.py:21` 缺 `bubble_sort`，`test_hello_world.py:20` 缺 `hello_world`；没有执行全量测试 |
| 定向安全单元集（命令见下） | **88 passed, 1 deselected in 2.76s** | 仅这些选中用例；Temporal 使用替身，不是真实服务 |
| 首轮较宽定向集 | 产生 14 个进度点后长时间未结束，人工停止，无最终摘要 | 不记 passed/failed；挂起原因未证实；仅终止匹配该测试命令的进程 |
| 内存/纯函数探针 | G01/G02、F02、F05、F23 复现，见复核文档 | 没有调用模型、Git 写操作、网络或运行不可信代码 |

通过的定向命令（PowerShell，换行展示）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  src/Sprout/tests/test_skills_disclosure.py `
  src/Sprout/tests/test_intents.py `
  src/Sprout/tests/test_node_execution_guard.py `
  src/Sprout/tests/test_agent_budget.py `
  src/Sprout/tests/test_task_execution_mode.py `
  src/Sprout/tests/test_verification_report.py `
  src/Sprout/tests/test_isolated_apply_boundary.py `
  src/Sprout/tests/test_temporal_submission.py `
  -k 'not test_task_intent_submits_async' -p no:cacheprovider `
  --basetemp=.workbuddy/test-runs/phase0-safe-units-20261016-1 `
  -o faulthandler_timeout=30
```

该用例被排除是避免重复触发尚未定位的运行等待，不代表它不需要修复。重新运行应使用新的 basetemp，避免 pytest 清除已有诊断目录。首轮命令涉及的其他测试没有完整结果，不能并入 88 项。

## 4. 覆盖边界与发布判断

| 领域 | 已有证据 | 本轮没有证明的内容 |
|---|---|---|
| CI | 本地树未见 `.github/workflows`；锁和 lint 可执行 | 没有远端 CI 运行记录，不证明其他外部 CI 不存在 |
| CLI/SDK/Gateway | 有 `test_runtime_gateway.py`、`test_sdk_gateway.py`、`test_gateway_task_actions.py`、`test_gateway_task_queries.py`、跨进程 fixture | 本轮没有启动真实 CLI 编码闭环；ACK 投影仍有 F21 |
| Web | 有应用、路由、WebSocket、认证及测试代码 | 未启动服务器/浏览器，未做 shutdown 故障注入；F09 为静态确认 |
| MCP | 有 adapter/client/server 和 `test_mcp_adapter.py` | 未启动 stdio MCP 或远程服务器；默认风险合同待补 |
| Docker | 已有 coding、graph、verification、replay、handoff opt-in 集成测试；例如 `test_coding_graph_docker_integration.py:47` 要求显式镜像 | 本轮未调用 Docker，未证明镜像/资源限制/禁宿主回退在真实环境生效 |
| Temporal | `test_temporal_integration.py:85` 要求 `SPROUT_TEMPORAL_TEST_HOST`；真实隔离编码集成在 `test_isolated_coding_temporal_integration.py:106` 同时要求 host/image | 未连接 Server/Worker，未执行跨版本回放、断网或崩溃注入；单元通过不等于真实集成通过 |
| 模型/成长 | 有预算、隔离实验、成对测量和候选验证模块及测试 | 未使用 API key、未付费调用、未发布候选，不能宣称自成长或生产交付已验收 |

当前适合继续小步开发与受控验证；不据本轮结果宣称 Restricted Coding Beta、Durable Execution Beta 或生产上线完成。

## 5. 后续会话使用规则

1. 先重读 AGENTS，核对 HEAD 和 dirty diff；本报告行号固定于上述 SHA。
2. 先读 [逐项复核](FINDINGS_REVALIDATED.md)，再读 [PR 顺序](PR_IMPLEMENTATION_ORDER.md)。已有保护不能重写成“从未实现”。
3. 每个 PR 更新证据：准确命令、成功/失败/跳过/not_run、接口兼容与回滚。源码变化后重新定位行号，不机械复用。
4. 本轮资料和审计文档均未提交；原 8 项删除仍属于用户工作树。需要提交时分开审查，不夹带原删除。
5. Phase 0 到此停止。下一步仅在用户检查后执行 R00。
