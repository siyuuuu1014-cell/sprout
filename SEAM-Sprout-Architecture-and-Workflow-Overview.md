# SEAM Sprout 架构与流程梳理

> 基于对全部源码（约 46k 行生产代码 + 24k 行测试，30 个子系统）的逐模块审查整理。
> 整理日期：2026-09-29

---

## 一、项目定位

SEAM Sprout 自我定位为"每个计算机项目的基因"——一个**嵌入式、可自我成长的 Agent 框架**：

接入代码库 → 理解上下文 → 生成代码 → git worktree 沙箱运行 → 校验 → 提案审批 → 集成回主仓 → 从执行轨迹沉淀可审核的改进候选。

技术栈：Python 3.12+ / Typer（CLI）/ Starlette + React + Vite（Web）/ SQLite + JSONL + blob（持久化）/ Temporal（编排）/ MCP（协议接入）。默认完全离线可跑（echo 模型 + 本地 SQLite）。

---

## 二、总体分层架构

```text
                    ┌───────────────────────────────────────────────┐
 入口面(薄)          │  CLI(Typer)   Web控制台(React+Starlette)       │
                    │  MCP服务端(stdio)   RPC/远程   IM通道(飞书/微信) │
                    └────────────────────┬──────────────────────────┘
                                         │ 全部汇聚到唯一组装点
                                         ▼
                    ┌───────────────────────────────────────────────┐
                    │    runtime/factory.py :: create_runtime()     │
                    │    （构造器注入全部协作者，单点装配）             │
                    └────────────────────┬──────────────────────────┘
       ┌────────────────┬────────────────┼──────────────┬─────────────────┐
       ▼                ▼                ▼              ▼                 ▼
 ┌───────────┐   ┌──────────────┐  ┌───────────┐  ┌──────────────┐  ┌──────────┐
 │ 在线回合   │   │  任务编排     │  │  成长平面  │  │  安全平面     │  │ 持久化    │
 │ agent/    │◄─►│ orchestration│  │ evolution/│◄►│ security/    │  │ storage/ │
 │ session/  │   │ (Temporal/图) │  │ 轨迹→候选  │  │ floor→policy │  │ rootstock│
 │ context/  │   │ task/ sched/ │  │ →审批→发布 │  │ →approval    │  │ 六库扇出  │
 │ memory/   │   └──────┬───────┘  └───────────┘  └──────┬───────┘  └──────────┘
 │ llm/      │          │                                │
 └───────────┘          ▼                                ▼
                    ┌────────────────────────────────────────────────┐
                    │  执行平面：7 broker + git worktree sandbox       │
                    │  execution/  sandbox/  workspace/  tools/ skills│
                    └────────────────────────────────────────────────┘
```

三条核心架构原则（guidance.md 明文规定，代码高度遵守）：

1. **单一装配点**：所有入口都走 `create_runtime()`；入口层保持薄，不直接打开数据库。
2. **单一权威存储**：每个实体恰有一个权威库（5 个 SQLite + JSONL + blob），Redis/Milvus/Neo4j 只是可重建的派生镜像。
3. **三层授权 + 可见性隔离**：HardFloor → PolicyEngine → Approval；代码变更落在 worktree 沙箱，主分支不可见，人工批准后才应用回来。

---

## 三、在线回合主流程（一条消息的旅程）

| 步骤 | 动作 | 关键代码 |
| --- | --- | --- |
| 1 | 消息进入，发 MESSAGE_RECEIVED 事件，洋葱中间件 before | runtime/runtime.py:710 |
| 2 | 会话解析（Session 可变 + Turn 冻结，seq 为唯一可信序） | session/manager.py:26 |
| 3 | 组装上下文：记忆快照 + 知识检索 + 近期历史 + 工作区图，按 token 预算裁剪 | runtime.py:724，memory/composer.py:45 |
| 4 | 意图识别双闸：关键词快路 →（不确定时）向量相似 + LLM JSON 分类 | runtime.py:1256 |
| 5a | task 意图 → 入队后台执行，立即返回 ack | runtime.py:963 |
| 5b | conversation → AgentLoop 反应式循环 | agent/loop.py:84 |
| 6 | 唯一写点落库：user+assistant 双 Turn；>8KB 正文进 blobstore；同事务写 outbox | runtime.py:1753 |
| 7 | 返回 OutboundMessage，中间件 after | runtime.py:794 |

**AgentLoop 内部**（每轮）：

```text
prompt 组装顺序：
  身份系统提示 → 技能 L0 索引 → Plan → 记忆块(summary/facts/recalled) → 历史 → 用户消息
      │
      ▼
invoke_model ──无 tool_calls──► 返回
      │
   有 tool_calls
      ▼
ToolExecutor 安全门控执行 ──► 结果回填 ──► 下一轮
      │
  遇 REQUIRE_APPROVAL
      ▼
挂起：存 pending_tool_calls 落库，等人工审批后 resume_pending 重入循环
```

LLM 层三个 provider（echo / OpenAI-compatible / aiyallm）统一在 `ModelProvider` 协议下；重试与降级链由 `ModelOrchestrator` 实现（它本身也实现该协议，组合而非继承）。

**记忆预算机制**：最近 N 轮对话为锚点永不裁剪；summary/facts 钉在 system prompt 不占预算；召回与知识竞争剩余额度，放不下即丢弃。记忆快照经 sha256 冻结，以保 LLM 前缀缓存命中。

---

## 四、任务编码闭环（项目核心卖点链路）

由编排图编译执行（orchestration/compiler.py），节点序列：

```text
READ → SANDBOX → PLAN → (AGENT → EVALUATION)×n → APPROVAL → APPLY
```

| 阶段 | 做什么 | 关键实现 |
| --- | --- | --- |
| SANDBOX | `git worktree add -b sprout-sandbox-<hex8>` 在主仓旁建隔离目录 | sandbox/git_worktree.py:54 |
| AGENT | 在 worktree 内写代码；FileBroker 只接受 SANDBOX_ONLY 决策落盘，越界路径直接拒绝 | execution/file_broker.py:76,177 |
| EVALUATION | 在沙箱 cwd 跑 manifest 测试/静态检查；未批准命令挂起而非虚报失败 | runtime/nodes.py:500 |
| 提取 diff | `git add -N` + `diff --no-ext-diff`（排除派生物），构建 ChangeProposal 入库，图挂起等人类 | nodes.py:714 |
| APPROVAL | 人工经 CLI `approvals approve --resume` 或 Web 面板批准 | security/approval.py |
| APPLY | 拼 patch → `git apply --check` 预检 → apply → commit "SEMA: …"；失败自动 `_undo`，支持 revert / `apply -R` 回滚 | execution/apply.py:73 |
| 清理 | `worktree remove --force` + 删分支；孤儿目录 glob 回收 | sandbox/lifecycle.py:58 |

**隔离边界的诚实说明**（README 自己承认，代码注释确认）：沙箱隔离的是*变更可见性*，不是*权限*——agent 进程与宿主共享文件系统、OS 用户、网络和内核。权限防线靠策略引擎 + 审批 + git 子进程白名单环境变量补偿（process_broker.py:226 明言 "logical boundary rather than an OS one"）。

---

## 五、安全平面：三层授权裁决

```text
动作请求 ActionRequest
   │
   ├─ ① HardFloor：13 条正则底线（rm -rf、危险命令、SQL 破坏等）
   │      命中即 DENY，任何审批都无法越过
   │
   ├─ ② PolicyEngine：风险矩阵
   │      风险分级 low/medium/high/critical
   │      CRITICAL→DENY，HIGH→REQUIRE_APPROVAL
   │      LayeredPolicyEngine 分层(org/workspace/delegation)只收紧不放宽，
   │      DENY 永不被下层加宽
   │
   ├─ ③ Approval：审批记录持久化在 audit 库
   │      参数 sha256 指纹匹配、single_use 消费即失效、超时 sweep 过期
   │
   └─ 全部通过 → tool.invoke / broker 执行
```

关键设计：

- **风险等级不可自报**：查服务端注册表，未知/未声明工具直接 DENY（security/layered_policy.py:308）。
- **非交互源 fail-closed**：cron 等来源遇到需审批动作直接拒绝。
- **技能扫描 fatal 项永不进策略引擎**，因此结构上无法被审批越过（skills/broker.py:149）。
- 已发现的裂缝：ToolExecutor 路径只走风险层简化裁决，HardFloor 与分层引擎主要靠 broker 路径兜底。

七个执行 broker（file / process / apply / git / network / database / read）统一模式：构造 ActionRequest → 策略裁决 → 发 policy.decided 事件 → 按决策行动。装配点在 runtime/runtime.py:371-423。

---

## 六、存储拓扑：六库扇出

```text
                  写                        镜像(fail-open，失败只记不抛)
权威层 ─────────────────► 单写点+事务outbox ──┬──► Redis   (缓存，TTL+LTRIM 自修剪)
 · sprout_core.db        工作区/任务/提案      ├──► Milvus  (向量，可离线重嵌重建)
 · sprout_conversation.db 会话/轮次/附件/FTS   └──► Neo4j   (图，MERGE 幂等投影)
 · sprout_knowledge.db   知识
 · sprout_audit.db       审批/观测/技能登记     重建机制：
 · sprout_usage.db       token 用量            ① 事务性 outbox 幂等重放
 · JSONL 轨迹 / blob 内容寻址                  ② 按 owner 全量 resync
                                              ③ sprout storage check 回读验证
```

- 读**永远**走权威层（storage/lanes.py:69），冷 Redis 不打挂在线路径。
- 会话存储独立成 **rootstock（砧木）层**：`SessionStore` 协议 + 7 个后端按 DSN 分发，runtime/context/memory 只依赖协议，不碰具体库。
- 实体归属总表在 storage/topology.py:60，权威/派生 lane 分类机器可读。

---

## 七、成长平面：自动进化闭环

```text
JSONL 执行轨迹（runtime 按任务 append-only 写入）
   │
   ▼
资格过滤（屏蔽取消/泄密事件）──► 信号提取：失败信号 + 稳定路径信号(≥3次重复)
   │
   ▼
GrowthRouter 定候选类型 ──► Replay 校验（当前为规则打分 ≥0.6，非真回放）
   │
   ▼
硬门：空内容 / 证据<3 / TOOL_PROPOSAL / 权限扩张键 / 危险文本 / 测试回归 → 直接挡
   │
   ▼
候选 artifact 入库 ──► 人工 approve ──► 发布
   ├─► 技能：注册进 SkillRegistry，trust==trusted 且 digest 匹配才注入 prompt
   └─► 知识：写入 KnowledgeStore
   
后续治理：
  consolidation — 弃用同名旧版本，保护已决策状态
  maintenance  — MONITORING → STABLE → DEPRECATED 按 1天/7天 推进
```

生命周期事件全程走进程内 EventBus（约 40 个事件，带 category + 权威 lane），ObservationRecorder 订阅 `"*"` 落观测库，fail-open。

---

## 八、技能生命周期（版本化 + 安全安装）

```text
发现(爬虫写 .hub/remote.json，不注入)
  → install：拉 bundle → .quarantine/ 暂存
  → 扫描：纯正则、不执行不调模型（info < warning < fatal，>1MB 文件只计数）
  → 裁决：fatal 直接 DENY（永不进策略引擎，审批救不了）
          非 fatal 走来源策略：local/project/evolved 免审；第三方 REQUIRE_APPROVAL
  → 晋升：.fetched/ 或 .evolved/ + 登记 SQLite 索引 + export_index 派生快照
  → 运行时注入：仅当 trust==trusted 且内容 digest 匹配，内容变更即降级
  → index --rebuild：注册表与磁盘对账，报告 missing/unregistered 分歧，
                    且保留历史审批判决（避免重建撤销已批权限）
  → forget：删注册表记录，磁盘文件保留
```

"版本化" = version 字段（默认 0.1.0）+ 内容 digest：同名冲突按 digest 前 8 位后缀并存，`--force` 才替换。

---

## 九、MCP 双向接入

**服务端**（stdio 运行，把 runtime 包成 MCPServer）：

- 只暴露安全能力：发消息、建会话、读历史、列技能、搜知识、workspace/task/sandbox/proposal 共 18 工具 + 5 资源。
- `proposal_request_approval` 只能"提出"请求，批准必须由 MCP 之外的人类完成。
- 高危操作（存储写入、审批、成长发布）不通过 MCP 暴露。

**客户端**：配置外部 MCP server 命令 → 起 stdio 子进程 → list_tools → 适配器加工具名前缀、统一 medium 风险 → 注册进 runtime.tools。

---

## 十、入口面与配置

| 入口 | 说明 |
| --- | --- |
| CLI | 约 20 个命令组；无参进交互聊天；`serve` 前台起 uvicorn，`stop` 用 lsof 找 PID SIGTERM；支持 8 种语言 `/language` 热切换 |
| Web | React 单页 9 视图（Chat/Board/TaskManager/Project/Approvals/Storage/Logs/Settings/Token）；聊天走 SSE 流式；`/ws/chat` 已实现但前端未用 |
| Gateway | 三层 project→daemon→rpc；对外 RPC 为自定义 JSON-RPC over HTTP，Bearer token 鉴权（默认关闭，空 token fail-closed） |
| 任务编排 | Temporal 后端提交 SproutExecutionWorkflow，逐节点调 activity；`orchestrator doctor` 检查可达性/版本/namespace/worker 数 |
| 配置 | `~/.sprout/sprout.toml` → tomllib → 递归映射 dataclass，未知键直接报错；密钥只从环境变量读，不落配置文件 |

---

## 十一、当前真实完成度评估

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 在线回合 / 记忆预算 / 存储扇出 / 沙箱 / 审批恢复 / 技能生命周期 | ✅ 真实闭环 | 核心链路贯通，测试覆盖充分 |
| Planner | ⚠️ 桩 | DirectPlanner 恒返 ("respond",)，每回合注入一条无用 Plan |
| AgentRouter | ⚠️ 未接线 | 恒走默认 agent，意图分类结果不参与路由决策 |
| Temporal 编排 | ⚠️ 半接线 | 默认仍是本地编排（TEMPORAL_HOST 环境变量才启用）；TemporalScheduler 全库无调用者；Task/Gateway 两个 workflow 是回显桩 |
| Replay 校验 | ⚠️ 简化 | 规则打分；真重跑器 ReplayRunner 存在但未接入成长管道 |
| Network/Database/Git broker | ⚠️ 部分 | 已注册进全局工具表但未进沙箱任务管线；DatabaseBroker 写路径形同虚设 |
| README 与代码出入 | 📌 注意 | README 称三 broker "未接入"已过时；配置 `<home>` 占位符无任何解析代码，照抄模板得到字面路径 |
| 已知裂缝 | 📌 注意 | run/stream 两条 prompt 组装约 60 行复制粘贴（stream 漏记 usage/step 遥测）；tasks 表在 core 与 audit 库双份 schema（潜在双权威，违背自家拓扑规则）；FileBroker.delete 仅 REQUIRE_APPROVAL 时删、ALLOW 反而拒，疑似反转；blobstore 无 GC；部分 ack 文案硬编码中文 |

---

## 十二、一句话总括

**这是一套"装配单点、存储单权威、安全分层、变更进沙箱、经验成候选"的骨架清晰的项目——在线消息链路和沙箱编码闭环是真实贯通的，规划、路由、真实回放与 Temporal 定时目前是名实不符的半成品；架构成熟度显著高于功能完成度。**
