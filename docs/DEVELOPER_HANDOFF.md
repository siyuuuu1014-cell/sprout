# SEAM Sprout 开发交接

基线为 GitHub `dev` 的 `71308e94d76ae19404ccdad79c1fb0d96f6574d1`，包含 [PR #15](https://github.com/siyuuuu1014-cell/sprout/pull/15)。该 PR 的功能提交为 `6709e5f77ad6e2bfd6be5db4f5f22a07d48019d6`。后续从最新 `dev` 开发。此文描述该基线中已合入的行为。

## 接手后先跑通一次

1. 检出最新 `dev`，安装 Python 3.12+ 和 uv，在 Sprout 仓库运行 `uv sync --locked --group dev`。
2. 为真实 Coding 配置模型。仓库的 [sprout.toml](../sprout.toml) 是 DeepSeek 示例，使用 OpenAI-compatible Provider，密钥由 `DEEPSEEK_API_KEY` 提供。把密钥放在本机环境或未跟踪的 `.env` 中。Echo Provider 可用于离线检查 CLI。
3. 启动 Docker，按 [Coding 镜像说明](../src/Sprout/config/docker_templates/coding/README.md) 准备含 pytest 和目标项目依赖的本地镜像。常规前台 Coding 使用本地 SQLite，无需启动可选数据库容器或 Temporal Worker。
4. 用一次性项目副本试跑。启动后确认当前目录，输入一句明确的代码修改需求。已有项目规格时引用规格，说明需要保留的测试和文件，以及验证通过后是否需要写回。
5. 核对 `[Task] created`、Sandbox、Verification、Review、Apply 和写回后复测。只读测试也会产生 `completed`，因此以 Coding Task ID 和 Apply 事务为准。

Windows PowerShell 示例。仓库路径和目标项目路径按本机修改。历史验收使用过的 `PYTHONPATH`、诊断模式及验收 JSON 环境变量应清除，以便验证默认入口。

```powershell
$sproutRepo = 'D:/dev/projects/sprout'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:SPROUT_DEV_NO_HUMAN_APPROVAL -ErrorAction SilentlyContinue
Remove-Item Env:SPROUT_FILESYSTEM_VERIFICATION_SPEC -ErrorAction SilentlyContinue
Set-Location 'D:/path/to/temporary-python-project'
uv run --no-sync --project $sproutRepo --env-file "$sproutRepo/.env" sprout chat --config "$sproutRepo/sprout.toml"
```

`--config` 优先于 `SPROUT_CONFIG` 和自动发现的配置。默认前台工作流自动处理受支持的任务内审批，日志会记录 `AUTO-APPROVED`。普通对话 Agent 的独立宿主工具调用仍可能需要人工批准。首次确认工作区和任务内审批是两个不同的交互点。

## 模块地图

| 层 | 代码入口与职责 |
| --- | --- |
| 产品入口 | [CLI](../src/Sprout/cli/app.py) 和 [chat](../src/Sprout/cli/commands/chat.py) 处理交互。 [Gateway](../src/Sprout/gateway/) 定义 Principal、请求和传输适配。 [Web](../web/webapi/app.py)、[MCP](../src/Sprout/mcp/) 和 Python SDK/RPC 复用 Runtime 合同。 |
| Runtime 与意图 | [Runtime 工厂](../src/Sprout/runtime/factory.py) 装配模型、存储、工具和编排后端。 [intents.py](../src/Sprout/intents.py) 区分代码修改、分析、只跑测试与条件修复。 [Runtime](../src/Sprout/runtime/runtime.py) 绑定已认证会话和工作区、分发任务并结算。 |
| 项目理解与上下文 | [workspace](../src/Sprout/workspace/) 负责扫描、分类、授权读取和符号关系。 [session](../src/Sprout/session/)、[context](../src/Sprout/context/) 和 [memory](../src/Sprout/memory/) 组织会话与上下文。 |
| 模型和工具 | [llm](../src/Sprout/llm/) 封装 Echo、OpenAI-compatible 与 aiyallm。 [agent](../src/Sprout/agent/) 执行模型与工具循环。 [tools](../src/Sprout/tools/) 和 [skills](../src/Sprout/skills/) 提供工具及版本化技能。 |
| 任务与编排 | [task](../src/Sprout/task/) 定义生命周期。 [orchestration](../src/Sprout/orchestration/) 编译并执行节点、绑定效果证据。 [NodeExecutor](../src/Sprout/runtime/nodes.py) 连接读取、沙箱、模型、独立验证和 Apply。 [Temporal 后端](../src/Sprout/orchestration/terminal/temporal.py) 与本地前台路径分开验收。 |
| 执行与审批 | [security](../src/Sprout/security/) 负责策略和审批。 [execution](../src/Sprout/execution/) 提供 FileBroker、ProcessBroker、Git 合同、Docker 观察与写回事务。 [sandbox](../src/Sprout/sandbox/) 管理 Filesystem Sandbox 和 Git Worktree。 |
| 持久化 | [配置](../src/Sprout/config/settings.py) 定义存储入口。 [SQLite Metadata](../src/Sprout/storage/local/sqlite/metadata.py) 记录 Task、Node、验证和效果台账。 [trajectory](../src/Sprout/trajectory/) 保留执行轨迹。SQLite、JSONL 与文件 Blob 按配置分工，Redis、Milvus、Neo4j 作为派生层。 |
| 扩展与演进 | [evaluation](../src/Sprout/evaluation/)、[evolution](../src/Sprout/evolution/) 和 [scheduler](../src/Sprout/scheduler/) 提供评估、候选提案和计划任务。它们拥有各自的执行合同。 |

### 前台 Coding 调用链

```text
chat → Intent → Runtime._handle_task → code_task.prepare
     → discover_plan → Acceptance Producer → 冻结 Bundle → 持久化 Task
     → FilesystemExperiment 或 ForegroundGitCoding
     → 沙箱编辑 → Docker 项目测试和独立用例 → 必要时自动修复与复验
     → Review → Apply → 从原目录重新读取并隔离复测 → completed
```

- [文件规划](../src/Sprout/cli/filesystem_preparation.py) 消费已授权的项目发现证据，给现有文件和新文件制定读写计划。 [修复目标](../src/Sprout/cli/repair_target.py) 使用真实 pytest 失败及调用关系，或独立规格用例的观测，定位需要改的模块。
- [Acceptance Producer](../src/Sprout/execution/acceptance_producer.py) 从原始需求、规格、已有测试和捕获的函数签名生成验收与回归用例。预期、证据和产物摘要在 Coding 前冻结。 [独立执行器](../src/Sprout/execution/isolated_acceptance.py) 运行候选代码，Host 对比实际结果与冻结预期。 [写回复测](../src/Sprout/execution/post_apply_verification.py) 从实际写回文件再次验证。
- [Filesystem 前台路径](../src/Sprout/runtime/filesystem_experiment.py) 使用受控副本， [Filesystem Apply](../src/Sprout/execution/filesystem_apply.py) 用精确计划和文件版本条件写入。 [Git 前台路径](../src/Sprout/runtime/foreground_git_coding.py) 使用 Worktree， [Git Apply](../src/Sprout/execution/foreground_git_apply.py) 按 Review 证据写回，并保留 Git Index。前台写回完成也不会自动创建 Git Commit。
- 一条条件修复请求可先创建只读 pytest Task。现有 pytest 全绿时，非 Git Python 可以继续用只读规格用例复现缺陷；规格和回归检查都通过时结束。修复或验证失败时不要用模型草稿推断项目是否写回，查看 Task、Node、事务和磁盘。

## 验收证据

| 场景 | 最近一次对应的真实结果 |
| --- | --- |
| 非 Git Python，现有测试全绿，规格存在缺陷 | 12 和 14 源码模块项目均验证过写回 4 个必要文件。另验收了仅需修 1 个文件、需要修 2 个文件及健康项目无写回。 |
| 非 Git Python，现有测试失败 | 两个真实 pytest 失败触发跨模块修复，写回后 4 项 pytest、Acceptance 3/3、Regression 1/1、Demo 1/1。Task `aa3ac46b-9e9a-4cf7-a5d8-d4981d653e39`。 |
| `src/` 布局新增功能 | 新建模块、修改两个调用方、补测试，自动修复后 Apply 完成。写回后 21 项 pytest 通过。Task `add3bab4-cca4-4240-8e63-963d0c0317c9`。 |
| `src/` 布局重构 | 抽取共用模块，保留公开签名和现有测试，写回后 2 项 pytest 通过。Task `cb143faa-b7a8-4a14-b2c8-46528c058037`。 |
| 验证持续失败 | 重试后任务失败，CLI 显示未执行写回，文件哈希未变，Apply 事务为零。 |
| 多文件写回中断 | 首文件真实写入并结算后由测试夹具结束进程。重启两次均无重放，事务 `10e19ae2-2712-54b9-a0d7-0bb92f691502` 保留 `WRITE_STARTED` 和逐文件状态。 |
| Windows 全仓 pytest | 6835 通过、49 失败、179 跳过、0 错误。49 个失败标识与当时最近的同分支基线相同，新增 0。另有 301 项定向测试、22 项 Apply 专项通过。 |

原始 Windows CLI 日志、SQLite 和哈希当时保存在开发工作树的本机测试缓存。接手时可用上述场景和 Task ID 向原开发者索取；这些缓存不会随 Git 克隆分发。托管 CI 独立于本机真实验收，查阅 [PR #15 检查记录](https://github.com/siyuuuu1014-cell/sprout/pull/15/checks) 与 [基线工作流](../.github/workflows/baseline.yml)。

## 排查、回归与下一步

| 现象 | 首先检查 |
| --- | --- |
| 识别到 Coding 意图但没有 Task | 查看绑定工作区、意图判定与 `code_task.prepare` 的首个错误。 `[Task] creating` 仅表示开始准备，`created` 才表示持久化。 |
| Docker 或独立验收不可用 | 核对 Docker Engine、镜像 ID 和标签、镜像依赖、冻结用例及其引用。保留 stdout、stderr 和退出码。 |
| 写回冲突或进程中断 | 用 `sprout chat --session <session_id>` 回到原会话，输入 `/status <task_id>` 查看逐文件状态。核对写入计划、实际文件哈希与事务结果。 `WRITE_STARTED`、`PARTIAL` 或 `UNKNOWN` 需要人工核对。 |
| 任务列表仍显示 running | 已知情况。中断事务详情会提示不可自动重放，列表尚未把该状态直接呈现为「写回中断，待核对」。 |
| TypeScript、Go 或 Git 条件修复受阻 | TypeScript、Go 缺非 Git Coding 镜像及冻结验收、写回复测适配；Git 的绿色 pytest 规格探查路径仍受 `spec_probe_filesystem_workspace_required` 限制。 |

从仓库根目录运行：

```powershell
uv run pytest -q src/Sprout/tests/test_g5_cli_workflow.py src/Sprout/tests/test_acceptance_producer.py src/Sprout/tests/test_filesystem_preparation.py src/Sprout/tests/test_filesystem_apply_ledger.py src/Sprout/tests/test_filesystem_apply_native.py
uv run pytest -q
uv run ruff check .
git diff --check
```

CI 等价的干净检出基线用 `python scripts/ci_baseline.py --output .workbuddy/ci-baseline-local`，输出目录需尚不存在。Docker、真实模型、Temporal 服务和存储集成需要各自证据。比较全量回归时保留失败标识与首条错误，报告跳过与未运行项。

接手的最小下一项是补任务列表的中断态显示，再用同一中断夹具复测。其后可以分别接入 TypeScript、Go，以及 Git 条件规格探查。Windows 的 49 项历史失败另行维护；现有诊断、Git 和 Temporal 的历史记录可从下方文档追溯。发布改动需要回退时，按 PR 或独立功能提交执行 Git revert。数据库中的已写回事务与项目文件应按事务证据单独核对。

- [历史架构方向](architecture/sprout-v1.md) · [历史审查基线](architecture/sprout-review-v2.1.md)
- [本地 CLI 诊断](architecture/local-cli-diagnostics.md) · [任务生命周期](architecture/Task-Lifecycle-CLI-Guide.md)
- [Worker 运维](architecture/Task-Worker-Operations.md) · [Temporal 阶段记录](architecture/temporal-t0-t1.md) · [存储所有权](architecture/Storage-Ownership-and-Result-Sync.md)
