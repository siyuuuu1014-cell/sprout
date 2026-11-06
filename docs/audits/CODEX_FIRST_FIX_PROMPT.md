# Phase 1：首次真正修改代码（在 Phase 0 报告核对之后使用）

根据已经复核通过的 Phase 0 审计，仅实施 `R00 / F16`：修复 `skills.disclosure=eager` 模式下的 Skill 可信过滤不一致。不要扩展到其他 P0/P1 问题。

先确认现有 `AgentContext.visible_skills()`、`skill_instructions()`、`AgentLoop._skill_message()` 的当前实现仍与审计一致。保留 `progressive` 和 `eager` 两种显示模式。优先先写失败测试：
- G-01: enabled=True, trust=untrusted, disclosure=eager 时，不得把 Skill 正文或描述注入到任何模型系统消息。
- G-02: Skill 的 requires_toolsets 不满足时，不得在 eager 模式注入；与 progressive 的资格筛选保持一致。
- 已可信、启用且符合 requires_toolsets 的 Skill 在两种模式下仍正常可用。

然后在最小代码范围内复用统一可见性谓词，避免新增平行安全状态；必须测试与以前版本兼容。运行相关 pytest 和 ruff，记录真实执行结果。检查有无旧版可选模式或调用者需要兼容处理。

交付：变更文件列表、逐项测试结果、执行前后行为、风险、回滚办法、关联 F16/G-01/G-02。除非我明确批准，否则本轮不开始 R01/R02，不推送或合并。
