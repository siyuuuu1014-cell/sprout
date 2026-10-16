# Sprout → Codex 交接包

此包是给已有 Sprout Git 仓库补充上下文，不是完整 Sprout 源码。原件历史基线：`dev@762eaf3c62476d2377302329151a759c3d297baa`；若当前 SHA 不同必须重新核验。

## 使用方法

1. 备份并检查仓库现有工作树，勿覆盖自己的 AGENTS.md、docs 等文件。
2. 将压缩包内容合并到 Sprout 仓库根目录；如果已有 AGENTS.md，应人工合并规则。
3. 在 Codex 本地工作区或已连接的 GitHub 项目打开 Sprout，确认 Codex 能读取 `AGENTS.md`、`docs/architecture/sprout-v1.md`、`docs/architecture/sprout-review-v2.1.md`。
4. 新会话复制 `CODEX_START_PROMPT.md`，先做仅审计的 Phase 0，核实 F01-F23 与 PR 依赖。
5. 你审查 Phase 0 的结果后，再复制 `CODEX_FIRST_FIX_PROMPT.md`，让 Codex 仅实施第一批安全修复 R00。
6. 后续每个 R 包建立独立分支/PR，每项通过相应回归门禁后才推进下一包；禁止一个 PR 混合所有架构重构。

## 材料优先级

当代仓库实际源码及可复现测试 > v2.1 修订实施合同 > v1 总体产品架构方向。不能把静态代码阅读或现有测试文件存在当成动态验收通过。

## 文件说明

- `AGENTS.md`: 持久项目约束（Codex 自动识别的文件格式）。
- `docs/architecture/sprout-v1.md`: 从原始 v1 DOCX 转换的可检索正文和表格。
- `docs/architecture/sprout-review-v2.1.md`: 复审报告的 Markdown 版本，含 F01–F23、R 包和测试矩阵。
- `docs/architecture/media`: 从 v1 提取的架构图，与 Markdown 相对路径引用。
- 两份 `*-original.docx`: 带原排版与图形的审查文档副本。
- `CODEX_START_PROMPT.md`: 第一次只读核验任务。
- `CODEX_FIRST_FIX_PROMPT.md`: 通过核验后启动 R00 的第一项代码修复。

**注意**：如果准备将文档公开提交到 GitHub，先检查其中是否存在你不希望公开的信息。此压缩包中的审计文档与原件属于设计/调查证据，而非生产验证结果。
