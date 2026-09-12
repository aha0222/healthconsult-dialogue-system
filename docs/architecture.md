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
  -> 后端载入 skill（SKILL.md 作为 system prompt，叠加人格覆盖）
  -> 调用 LLM 生成回复，要求末尾带场景标记
  -> 解析并剥离标记，得到 (回复正文, 风险等级)
  -> safety_checker.check_reply 兜底快检
  -> 命中硬红线时，替换为按风险等级预置的安全兜底话术
  -> 返回前端展示
```

前端通过 `POST /api/chat` 调用后端（`backend/app/main.py`），请求携带 `message`、`personality` 与 `history`/`session_id`，
响应返回 `reply`、`risk`、`risk_label`、`violations`、`fallback_used`、`session_id`。
另有 `POST /api/chat/stream` 提供 SSE 流式输出：先逐字推送 `delta`，最后推送 `done`（命中红线时 `done.reply` 为安全兜底话术，客户端覆盖显示）。
会话与消息用 SQLite 持久化（`backend/app/storage.py`），带 `session_id` 时自动续接上下文。
长会话由 `dialogue/memory.py` 维护滚动摘要与长期画像（schema v3），续接时注入 system prompt。
续接时还会用 `markers.append_marker` 把历史助手回复的场景标记还原，避免模型模仿"无标记"格式而漏标。
LLM 漏标时，后端用 `dialogue/markers.py` 的本地关键词分类器兜底分级。

## 实现状态

| 组件 | 状态 |
|------|------|
| `backend/app/dialogue/orchestrator.py` | 已实现（编排 + 兜底 + 流式） |
| `backend/app/main.py`（HTTP API） | 已实现（chat / stream / sessions / audit / health / personalities） |
| `backend/app/storage.py` | 已实现（SQLite 会话/消息/审计/记忆 + schema 迁移） |
| `backend/app/dialogue/memory.py` | 已实现（长会话滚动摘要 + 长期画像） |
| `backend/app/sampling.py` | 已实现（线上样本脱敏导出） |
| `backend/app/evaluation.py` | 已实现（风险分级评测） |
| `backend/app/dialogue/routing.py` | 已实现（按风险选择模型） |
| `backend/app/cache.py` | 已实现（低风险首轮问答缓存，默认关闭） |
| `backend/app/security.py` | 已实现（API Key 鉴权 + 按 IP 限流） |
| `backend/app/safety/semantic_checker.py` | 已实现（高风险 LLM 语义复核，默认对 R3/M0/S0 开启） |
| `backend/app/alerts.py` | 已实现（高危告警：日志 + 可选 webhook） |
| `backend/app/logging_config.py` | 已实现（plain / json 结构化日志） |
| `frontend/chat.html` | 已改为调用后端流式 API，浏览器不再接触大模型密钥 |

> 鉴权：配置 `BACKEND_API_KEY` 后，除 `/api/health` 外接口需带 `X-API-Key`。审计日志记录每条回复的 `risk`/`violations`/`fallback_used`/`latency_ms`，便于高危场景复盘。

## 三层安全

| 层级 | 方式 | 成本 | 作用 |
|------|------|------|------|
| 第一层 | `SKILL.md` 作为系统指令 | 0 | LLM 自主分类到 S/M/R/X 并约束行为，绝大多数安全问题在此解决 |
| 第二层 | 本地关键词快检（`safety_checker`） | <1ms | 覆盖开药、调药、怂恿开门、轻视心理危机等硬红线，命中即替换为安全话术 |
| 第三层 | LLM 语义复核（`safety/semantic_checker.py`，默认开启） | 一次 LLM 调用 | 对高风险等级（默认 R3/M0/S0）复核，抓关键词漏掉的换说法越界 |

离线侧另用 `tools/validate_outputs.py --mode llm` 做入库前的精确审查，不走实时链路。

**高危告警与红队**：`alerts.py` 在命中兜底或高风险等级时记录/推送告警；`tests/redline_cases.jsonl` 作为红队回归集，CI 自动执行，新增安全规则必须补用例。

## skill 输出契约

每条回复末尾必须携带合法的场景标记：

- S 类：`[SITUATION:S0]` / `[SITUATION:S1]` / `[SITUATION:S2]`
- M 类：`[MENTAL:M0]` / `[MENTAL:M1]`
- R 类：`[RISK:R3]` / `[RISK:R2b]` / `[RISK:R2a]` / `[RISK:R1]` / `[RISK:R0]`
- X 类：`[OTHER:X]`

质检时，`generated_sft` 模式缺失标记为 fatal，`source_sample` 模式为 warning。

## 数据闭环

线上真实对话通过审计表回流到离线质检，形成持续改进闭环：

```
线上对话 → audit_log → tools/export_online_samples.py（脱敏）
        → JSONL → tools/validate_outputs.py --mode generated_sft
        → 发现问题 → 补规则/补红线用例 → 回归
```

- `backend/app/sampling.py`：`redact`（手机号/身份证/银行卡脱敏）、`audit_to_sample`（按 risk 还原场景标记，复用现有质检契约）、`export_samples`（支持 `--since`、`--only-flagged`）。
- `backend/app/evaluation.py` + `tools/eval_risk.py`：固定评测集 `backend/tests/eval/risk_cases.jsonl` 上给风险分级器打分；`--mode local` 用本地兜底分级器（确定性，CI 可跑），`--mode llm` 走真实编排链路。
- SFT 迭代：`generate_candidates` → `clean_candidates` → `validate_outputs` → `generate_manual_review_list` → 版本化入库（现为 `v0.2.3`）。

## 性能与成本

- **预算**：`MAX_MESSAGE_CHARS` / `MAX_HISTORY_ITEMS` 在请求层校验；`MAX_TOKENS` / `TEMPERATURE` 控制生成成本；每次调用记录 `llm_usage`。
- **模型路由**：开启后按输入风险走 `MODEL_FAST` / `MODEL_STRONG`。
- **缓存**：TTL + LRU，仅低风险、无上下文的首轮问答，默认关闭。
- **并发**：同步实现，FastAPI 用线程池承载同步端点；后续可切 `AsyncOpenAI` + 异步端点。

## 扩展点

- **新增 skill**：在 `skills/` 下加目录，后端指定加载路径即可。
- **新增安全规则**：改 `skills/.../rules/` 与 `safety_checker.py`，并补 `redline_cases.jsonl` 用例。
- **替换前端/后端**：skill 不受影响。
