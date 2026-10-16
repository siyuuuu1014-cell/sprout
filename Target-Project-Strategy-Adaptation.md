# 目标项目策略适配修复（2026-10-16）

## 修复原因

正常策略的真实模型试用发现：独立 app.py 项目仍被指定向 src/Sprout/tests
写测试并执行 ruff check src。模型因此计划新增无关 Sprout 包和重复测试。
根因在策略规则，不应通过扩大模型预算掩盖。

## 模块分工

project_profile 只读识别目标项目约定，复用 WorkspaceScanner 的语言识别，
读取有限大小的配置；不执行命令或安装依赖。
StrategyPipeline 统一组装 profile、影响报告与规划结果；RequirementPlanner
使用 profile 生成测试路径，VerificationPlanner 使用 profile 生成验证建议。
ExecutionPlanner 仍负责将计划桥接到 TaskSpec，Gateway、Worker、审批及执行器
没有各自新增一套规则。数据库专用规划保持不扫文件系统，缺少约定时不猜命令。

## 当前规则

- Python：优先沿用已有 Python 测试目录，其次采用 pyproject 中已存在的 pytest
  testpaths 目录，最后建议 tests/。新增文件名仅用 ASCII 字母、数字及下划线。
  pytest -q 为 Python 项目的约定建议，不代表依赖已安装；执行仍经过原授权流程。
- Ruff：只有 pyproject 的 tool.ruff 或 Ruff 配置文件存在时才建议 ruff check .，
  不假设 src 存在。未覆盖仅通过依赖声明启用 Ruff 的全部配置形式。
- Node/TypeScript：仅在 package.json 声明对应脚本时建议 npm test/npm run lint，
  新增测试采用 .test.js/.test.ts；尚未识别所有包管理器和 monorepo 配置。
- Go/Rust：依据 go.mod/Cargo.toml 建议 go test ./... / cargo test。
- 未知项目或无测试脚本：不填入虚构命令，风险中明确提示确认验收配置。
- 已匹配的目标项目测试保持优先；移除源文件必须位于 src/web/app/lib/tests 的
  限制，修复平铺源文件漏识别。补充自定义目录 test_*.py 和 *.test.* 识别。
- .workbuddy 排除仅针对目标根内的相对路径，避免目标本身位于实验目录时全被排除。

API/数据库影响不再自动构造未经证实的 pytest -k api/database/migration/approval
选择器；采用项目测试套件，并明确需求专属案例仍需补齐。完整套件通过不等于
已经证明 API 合约或数据库迁移正确，不绕过后续独立验收与完成门槛。

## 验证与边界

83 项离线回归通过（5.27s）：平铺 Python、包内/自定义测试、pytest testpaths、
Ruff、Node 脚本有无、Go/Rust、未知项目、最终 TaskSpec、数据库只读规划和既有
策略/CLI/任务模式/工作区/验证报告。静态检查通过。未执行建议的目标项目命令，
未发起付费模型请求，未恢复旧等待任务，未提交当前项目。

这是单一主要语言项目的基础规则；多语言 monorepo、配置继承和更多测试框架
尚未覆盖。下一步处理简单规划的重复探索和请求挤占，不默认追加真实模型预算。
