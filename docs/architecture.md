# 系统架构

## 组件

| 组件 | 目录 | 职责 | 依赖 |
|------|------|------|------|
| 前端 | `frontend/` | 呈现对话、切换人格、语音播报 | 浏览器 |
| 后端 | `backend/` | 会话编排、调用 LLM、安全检查、API | Python、openai |
| skill | `skills/healthconsult-assistant-skill/` | 回复规范、规则、示例数据 | 无（纯规范） |
| 工具 | `tools/` | 数据生成、清洗、质检、人格对比 | Python、openai |

skill 是声明式的：它只规定「怎么答」，不包含任何可执行代码。后端在运行时把 skill 的 `SKILL.md` 作为 system prompt 注入。

## 一次对话的数据流

```
用户输入
  -> 后端载入 skill（SKILL.md 作为 system prompt）
  -> 调用 LLM 生成回复，要求末尾带场景标记
  -> 解析并剥离标记，得到 (回复正文, 风险等级)
  -> safety_checker.check_reply 兜底快检
  -> 返回前端展示
```

## 三层安全

| 层级 | 方式 | 成本 | 作用 |
|------|------|------|------|
| 第一层 | `SKILL.md` 作为系统指令 | 0 | LLM 自主分类到 S/M/R/X 并约束行为，绝大多数安全问题在此解决 |
| 第二层 | 本地关键词快检（`safety_checker`） | <1ms | 覆盖开药、调药、怂恿开门、轻视心理危机等硬红线 |
| 第三层 | LLM 语义质检（`tools/validate_outputs.py --mode llm`） | 离线 | 新数据入库前的精确审查，不走实时链路 |

## skill 输出契约

每条回复末尾必须携带合法的场景标记：

- S 类：`[SITUATION:S0]` / `[SITUATION:S1]` / `[SITUATION:S2]`
- M 类：`[MENTAL:M0]` / `[MENTAL:M1]`
- R 类：`[RISK:R3]` / `[RISK:R2b]` / `[RISK:R2a]` / `[RISK:R1]` / `[RISK:R0]`
- X 类：`[OTHER:X]`

质检时，`generated_sft` 模式缺失标记为 fatal，`source_sample` 模式为 warning。

## 扩展点

- **新增 skill**：在 `skills/` 下加目录，后端指定加载路径即可。
- **新增安全规则**：改 `skills/.../rules/` 与 `safety_checker.py`，并补 `redline_cases.jsonl` 用例。
- **替换前端/后端**：skill 不受影响。
