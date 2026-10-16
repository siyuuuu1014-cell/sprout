<div align="center">
  <a href="README.md">English</a> |
  <a href="README_CN.md">简体中文</a> |
  <a href="README_ZH-HANT.md">繁體中文</a> |
  <a href="README_JA.md">日本語</a> |
  <a href="README_KO.md">한국어</a> |
  <a href="README_RU.md">Русский</a> |
  <a href="README_ES.md">Español</a> |
  <a href="README_PT.md">Português</a>
</div>

<br />

### 项目任务的显式类型

`sprout project task-create <workspace_id> "<需求>" --mode code` 默认创建编码任务。
编码要求已有 Git 仓库，并在普通应用和验证完成前通过冻结的输入/输出验收用例及可执行最小 Demo。
Sprout 不会自动初始化 Git。只读分析使用 `--mode read_only`，不提供写入工具，也允许非 Git 项目。
HTTP/RPC/SDK 创建任务使用相同的 `execution_mode` 字段。

缺少类型的历史任务会在执行和恢复时被阻止。未执行且无提案的任务可通过
`sprout project task-confirm-mode <task_id> --mode code|read_only` 确认；已有执行记录或提案的任务须重新创建。
人工强制应用仍是独立入口，必须记录豁免原因，不代表验证完成。

<div align="center">
  <img src="assets/seam_sprout.svg" alt="SEAM Sprout logo" width="200" />
</div>

<h1 align="center">SEAM Sprout</h1>

SEAM Sprout 是每一个计算机项目的一段“基因”。它可以接入代码库，理解项目结构和上下文，然后完成代码生成、隔离环境运行、代码校验、代码集成，并推动项目自动成长。

它的目标不是只聊天，而是真正参与项目：完善功能、修复问题、验证代码、集成变更，并让项目在受控状态下持续进化。

<div align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12%2B-8b5cf6" /></a>
  <a href="https://nodejs.org/"><img src="https://img.shields.io/badge/node.js-20%2B-339933" /></a>
  <a href="https://pypi.org/project/aiyallm/"><img src="https://img.shields.io/pypi/v/aiyallm?color=8b5cf6" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-10b981" /></a>
</div>

## 产品亮点

- **精准项目分析**：行动前先理解仓库结构、代码上下文、会话、记忆和知识。
  Python 分析支持 SQLAlchemy Core 表/列、明确的本文件声明式 ORM 模型及 FastAPI 路由声明，保留结构化源码证据。
  明确的跨文件 FastAPI router 包含关系会生成独立的源码组合路径及前缀证据链，SQL CREATE TABLE 保留列和约束原文。
  结果不代表实时数据库或部署 URL；跨文件 ORM 继承、动态工厂、注解类型映射、SQL 方言语义及版本相关的挂载后修改仍未解析。
- **代码生成**：把意图变成具体代码修改，而不是只给文字建议。
- **隔离环境运行**：在 git worktree 沙箱中运行生成代码，通过受控 broker 应用变更。
- **代码校验**：对每次修改进行测试和检查，避免直接写入错误代码。
- **代码集成**：把验证通过的变更集成回项目，并保留完整过程记录。
- **有序关闭**：先停止并等待本地任务消费器退出，再完成后台写入、outbox 刷新和数据库关闭；服务退出不等于取消业务任务。
- **自动成长**：从已完成的工作中沉淀经验，形成可审核的后续改进提案。

## 商业场景

- 希望项目内置一个 AI 工程师，而不是额外打开一个聊天工具。
- 需要持续修复、重构、补全功能的代码库。
- 希望生成代码后能安全运行、测试并集成的团队。
- 需要项目级记忆、进化和审计能力的企业或组织。
- **如实的隔离边界**：每个高危动作都要先经过 authorization 层（hard floor → policy → approval），变更落在 git worktree sandbox 里。该 sandbox 隔离的是*变更可见性*，不是*权限*——agent 进程与宿主共享 filesystem、OS user、network 和 kernel，没有 container 或 OS 级后台。七个 execution broker 里，当前装配进 runtime 的是 file、process、apply 三个；network、database、git 三个 broker 已实现但尚未接进 agent 路径。

## 采用技术

| 层级 | 技术 |
|---|---|
| 语言 | Python 3.12+、Node.js 20+ |
| CLI | Typer |
| Web | Starlette、Uvicorn、React、Vite |
| LLM | echo、OpenAI-compatible、aiyallm |
| MCP | MCP Python SDK |
| 持久化 | SQLite、JSONL、文件系统 blob、内存存储（预留 Milvus、Neo4j、Redis） |
| 配置 | TOML 与类型化 dataclass 设置 |
| 工程工具 | uv、pytest、pytest-asyncio、ruff |

## 项目结构

```text
src/Sprout/
├── runtime/       # Runtime、中间件、生命周期、工作区、锁、队列与组装
├── agent/         # Agent 协议、AgentLoop、路由、规划、执行
├── session/       # 会话与轮次模型
├── memory/        # 记忆组合、预算、快照与持久化
├── context/       # AgentContext 与上下文构建
├── message/       # 统一消息、附件与转换
├── llm/           # 模型提供方：echo、OpenAI-compatible、aiyallm
├── tools/         # ToolSpec、安全门控执行器、注册表、系统工具
├── skills/        # 版本化技能与技能仓储
├── security/      # 风险分级、策略、审批
├── events/        # 进程内事件总线
├── registry/      # 通用注册表
├── storage/       # 存储契约与本地实现
├── evolution/     # 成长平面、回放、维护与轨迹成长
├── artifacts/     # 产物模型与元数据
├── capability/    # 能力模型
├── execution/     # 变更应用与 Broker 适配器
├── gateway/       # Runtime、RPC、Task、Daemon 与传输网关
├── orchestration/ # 图编译器、Temporal terminal、worker 与 workflows
├── rootstock/     # 会话/根持久化后台
├── sandbox/       # Git worktree 沙箱
├── task/          # 任务模型与生命周期
├── trajectory/    # 轨迹持久化
├── workspace/     # 工作区辅助
├── config/        # 类型化设置与 TOML 加载
├── scheduler/     # 定时任务支持
├── cli/           # sprout 命令行
└── mcp/           # MCP 服务端、客户端与适配器

web/
├── frontend/      # Vite + React Web 控制台
│   ├── src/components/
│   ├── src/views/
│   └── src/
└── webapi/        # Starlette HTTP/WebSocket 应用、路由与数据库

assets/            # 共享 Logo 与静态资源
~/.sprout/docker/  # 六库栈：compose、运行器镜像、一键启动脚本
```

## 安装

```bash
git clone <repository-url>
cd SEAM_Sprout
uv sync --dev
```

默认配置完全离线：使用 `echo` 模型提供方、本地 SQLite 数据库，不连接外部 MCP 客户端。

使用 `aiyallm` 模型提供方时，先安装对应发行版：

```bash
pip install aiyallm
```

### 六个数据库，两条命令

运行时会在六个数据库间扇出。其中三个是普通文件——SQLite、JSONL 证据日志、Blob 对象库——**不需要任何安装**，首次写入时自动创建；另外三个（Redis、Milvus、Neo4j）以容器形式运行。两条命令覆盖整个生命周期：

```powershell
sprout db init                    # 初始化所有已配置 lane
sprout db status                  # 逐个报告 healthy/failed
sprout db backup <dir>            # 备份 SQLite 数据库
sprout db restore <dir>           # 恢复 SQLite 数据库
```
```bash
sprout db init
sprout db status
```

`sprout db init` 会初始化所有已配置 lane；`sprout db status` 会逐个检查并显示 healthy/failed。

端口、配置、无 Docker 的降级用法与排障见 `~/.sprout/docker/README.md`。

## 使用方式

### Agent

Python API 是最核心的使用方式。构建运行时并发送一条 `Message`：

```python
import asyncio
from Sprout.config.defaults import default_settings
from Sprout.message.models import Message
from Sprout.runtime.factory import create_runtime

async def main() -> None:
    runtime = create_runtime(default_settings())
    async with runtime:
        reply = await runtime.handle(Message("hello", channel="python"))
        print(reply.content)

asyncio.run(main())
```

所有入口都通过 `create_runtime()` 完成唯一组装。

### Web

Web 控制台是基于 Vite 的 React 组件化应用。

先构建前端：

```bash
cd web/frontend
npm install
npm run build
cd ../..
```

然后启动 Web 控制台：

```bash
uv run sprout serve
```

打开 `http://127.0.0.1:8000`。Web 应用提供聊天、任务看板、任务管理和配置设置。

前端热更新开发模式：

```bash
cd web/frontend
npm run dev
```

可用 HTTP 接口包括：

- `POST /api/chat`
- `POST /api/chat/stream`
- `GET /api/sessions/{session_id}/history`
- `GET /api/tasks`、`POST /api/tasks`
- `PATCH /api/tasks/{task_id}`、`DELETE /api/tasks/{task_id}`
- `GET /api/settings`、`PUT /api/preferences`
- `GET /api/tokens`
- `GET /api/logs`
- `GET /api/storage/status`、`GET /api/storage/plan`
- `GET /api/storage/graph/status`、`GET /api/storage/vectors/count`
- `GET /api/health`

WebSocket 地址为 `ws://127.0.0.1:8000/ws/chat`。

### CLI

```bash
uv run sprout --help
uv run sprout                     # 交互式聊天
uv run sprout chat "hello"        # 单发消息
uv run sprout info                # 查看运行时与配置快照
uv run sprout project workspace <path>   # 本地打开工作区
uv run sprout remote workspace-list      # 操作远程 SEMA 服务
uv run sprout db init             # 初始化所有已配置 lane
uv run sprout db status           # 报告 healthy/failed
uv run sprout db backup ./backup  # 备份本地数据库
uv run sprout mcp inspect         # 查看 MCP 定义
uv run sprout evolution candidates  # 查看成长平面已产出的候选
uv run sprout skills import ~/code/my-skills   # 从本机目录导入技能
uv run sprout skills list         # 查看已装技能
uv run sprout skills index --rebuild   # 同步快照，报告注册表与磁盘的分歧
uv run sprout skills forget <name>     # 删除注册表记录，保留文件
uv run sprout serve               # 启动 Web 控制台与 API
uv run sprout stop serve          # 停止 Web 控制台
```

`skills import` 会递归扫描目录：含 `SKILL.md` 的文件夹算作一个技能，独立的
`*.toml` 单文件也算一条。嵌套布局（`skills/writing/docs/SKILL.md`）同样能找到，
依赖目录会被跳过；每个技能都走子系统统一的 broker —— 先做安全扫描，再由策略
引擎裁决。本机路径属于首方来源，因此无需审批；扫描器的致命项底线依旧生效，
且无法被审批越过。

### CLI 语言

交互式 CLI 可以在不重启的情况下切换菜单语言：

```text
/language
/language ja
/language zh-Hant
```

支持的语言见
[`src/Sprout/cli/i18n_languages.md`](src/Sprout/cli/i18n_languages.md)。选择
结果保存在 `~/.sprout/settings.json`，下一次启动会继续使用。

### Temporal

任务编排、队列、定时任务、成长自动化、Web 任务和 Gateway 工作都可以运行在 Temporal 上。

通过 TEMPORAL_HOST 显式启用 Temporal；未启用时使用离线本地编排。启用后提交失败不能回退本地，任务保留等待调度/结果待确认状态，可恢复服务后重试。使用仓库内的
Compose 启动本地 Temporal 及其专用 Postgres：

```bash
docker compose -f ~/.sprout/docker/temporal/docker-compose.yml up -d
```

无需安装 Temporal CLI，直接检查连通性：

```bash
export TEMPORAL_HOST=127.0.0.1:7233
sprout orchestrator doctor
sprout orchestrator worker
```

`doctor` 会报告服务端可达性、服务端版本、命名空间是否存在，以及配置的
任务队列上正在轮询的 worker 数量。

`sprout project task-submissions <task_id>` 查询持久化提交记录，`sprout project task-run <task_id>` 重试原任务。链路、审批续跑和幂等边界见 [Temporal-Submission-Recovery-Guide.md](Temporal-Submission-Recovery-Guide.md)。

活动节点采用持久化占用：未知副作用不自动重跑、不因超时抢占，也不提供强制重跑入口。`sprout project task-executions <task_id>` 仅读取占用与结果状态；有可信完成记录才可安全继续。范围与人工核对边界见 [Node-Execution-Recovery-Guide.md](Node-Execution-Recovery-Guide.md)。

### MCP

通过 stdio 运行 MCP 服务端：

```bash
uv run sprout mcp serve
# 或
sprout-mcp
```

MCP 服务端只暴露安全能力：发送消息、创建会话、读取会话历史、列出技能和搜索知识。存储写入、审批和成长发布等高危操作不会通过 MCP 暴露。

查看对外暴露的工具、资源和提示词：

```bash
uv run sprout mcp inspect
```

## 配置

用户配置位于 `~/.sprout/sprout.toml`。请只保留这一份配置，不要在项目根目录重复添加 `sprout.toml`；仅在必要时用 `SPROUT_CONFIG` 指向其他路径。

Project Runtime MCP 工具包括：

```text
workspace_scan
workspace_query
task_create
task_execute
task_plan
task_changes
sandbox_diff
sandbox_test
trajectory_query
proposal_show
proposal_request_approval
proposal_reject
proposal_apply
```

## 真实模型配置

编辑 `~/.sprout/sprout.toml` 以使用 DeepSeek 或其他提供方：

```toml
[model]
provider = "aiyallm"
model = "your-model"
api_key_env = "YOUR_PROVIDER_API_KEY"
base_url = "https://your-provider.example/v1"

[web]
host = "127.0.0.1"
port = 8000

[storage]
# Sprout 运行时 authority：conversation 支持 sqlite | jsonl | blobstore；
# Milvus/Neo4j/Redis 永远只是可重建的派生层。
core = "sqlite:////<home>/.sprout/data/sprout_core.db"
conversation = "sqlite:////<home>/.sprout/data/sprout_conversation.db"
knowledge = "sqlite:////<home>/.sprout/data/sprout_knowledge.db"
audit = "sqlite:////<home>/.sprout/data/sprout_audit.db"
usage = "sqlite:////<home>/.sprout/data/sprout_usage.db"

[evolution]
approval_required = true
```

密钥只从环境变量读取，不会写入配置文件。

## 开发

```bash
uv run pytest
uv run ruff check .
```

开发流程与 CLI/MCP 暴露规则见 [guidance.md](guidance.md)。

## 贡献者

感谢所有为 SEAM Sprout 做出贡献的人：

| 贡献者 | GitHub |
|---|---|
| JasonXuanxuan | [@JasonXuanxuan](https://github.com/JasonXuanxuan) |
| rest8945 | [@rest8945](https://github.com/rest8945) |
| siyuuuu1014-cell | [@siyuuuu1014-cell](https://github.com/siyuuuu1014-cell) |

## 任务验收报告

固定链路评测：`uv run sprout project eval`；单个 Python 闭环 demo：
`uv run sprout project eval --case C02 --json`。只运行专用夹具与脚本模型，
模拟审批不会作用于真实项目；每次新建证据目录，不自动安装依赖。
用法与范围见 [Offline-Evaluation-Guide.md](Offline-Evaluation-Guide.md)。

使用 `sprout project task-verification <task_id> --json` 读取已有验收证据；Web 提供只读 `GET /api/project/tasks/{task_id}/verification`。查询不会重新运行代码。报告包含版本、是否验证完成、阻断原因、失败分类、命令、工作目录、退出码和输出。报告由原有 EVALUATION / 变更提案证据计算，不增加存储权威。

测试失败、策略拒绝、等待审批、runner 错误、超时、跳过和缺失结果分别记录；强制应用豁免不等于验证完成。pytest 包含跳过用例时保守阻止通过。日志分类不是隔离评测或防恶意代码的安全保证；固定任务评测和提交恢复仍需后续实现。

## License

本项目使用 [MIT License](LICENSE)。
