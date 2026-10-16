# 首次交给 Codex 的任务（直接复制以下内容作为新会话指令）

你现在负责维护 `siyuuuu1014-cell/sprout`，默认工作分支 `dev`。这是一个已有大量实际实现的 AI 软件工程 Runtime，不是待重建的新项目。

**本轮仅做 Phase 0：读取与核对、建立实施基线；不要开始大规模改代码。**

1. 先阅读仓库根目录 `AGENTS.md`，再阅读 `docs/architecture/sprout-v1.md` 与 `docs/architecture/sprout-review-v2.1.md`。v1 是方向，v2.1 是实施修订；两者的源码事实都必须以当前仓库实测为准。
2. 核对 Git 状态、当前分支和 SHA，特别对照历史审计基线 `dev@762eaf3c62476d2377302329151a759c3d297baa`。如果当前代码有更新，先识别审计结论是否已过期，不要把已修复问题当成待修复问题。
3. 建立核心调用链索引：Gateway/Runtime/Intent → Task/Orchestration/AgentLoop → ToolExecutor/Security/Brokers → Docker/Verification/Approval/Apply → Storage/Events/Evolution；分别核查 CLI、Web、MCP 和 SDK 的入口差异。
4. 按 v2.1 的 F01–F23 逐项校验：标记 `confirmed / partially_confirmed / already_fixed / needs_runtime_test / not_reproduced`，给出具体文件和行号、调用链证据、当前已有测试及需要新增的测试；不要只复述报告。
5. 审查 R00、R01–R18、R02b、R06b、R10b 的依赖，调整真实优先级。必须先处理 P0 的安全边界，再处理模型状态/交付闭环，后做 RepoMap、多语言、Evolution 和多 Agent。
6. 若本地已具备依赖且无需违反安全规则，可以运行当前基线的静态检查与安全的单元测试；请准确报告已运行、失败、跳过、因缺依赖未运行的项目，不能声称 Docker/Temporal 已通过。
7. 输出 `docs/audits/CODEX_BASELINE.md`、`docs/audits/FINDINGS_REVALIDATED.md`、`docs/audits/PR_IMPLEMENTATION_ORDER.md` 三份审计文件，或在当前会话输出同等内容。PR 计划每项要写明目标、现状、文件范围、依赖、测试、风险和完成定义。
8. **Phase 0 到此停止**：在没有给出审查结果前，不要修改运行时源代码、不做 Git push、不要合并、不要绕过测试或审批。最后提出第一个最小修复 PR（通常是 R00：Skill eager 信任筛选）的准确变更和测试计划，供我检查。

每一条审查判断必须区分：源代码确认、测试存在但未运行、动态验证已通过、架构建议。发现文档自身的问题也要如实纠正。
