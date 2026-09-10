# backend · 对话系统后端

负责把「老人说的话」变成「安全、温暖的回复」：载入 skill 作为 system prompt，调用 LLM，解析回复末尾的场景标记，并用安全检查做兜底。

## 状态

**可用**：对话编排、HTTP API、SSE 流式输出、SQLite 会话/审计持久化、长会话滚动摘要与长期画像、兜底快检、高风险语义复核、高危告警、API Key 鉴权、按 IP 限流、结构化日志、token 预算、模型路由、回复缓存均已实现。

## 目录

```
backend/
├── requirements.txt          # openai, fastapi, uvicorn, httpx, python-dotenv, pytest
├── app/
│   ├── main.py               # FastAPI 入口 + 路由 + 访问日志中间件
│   ├── config.py             # 环境变量 / .env 配置
│   ├── security.py           # API Key 鉴权 + 内存限流
│   ├── logging_config.py     # plain / json 结构化日志
│   ├── alerts.py             # 高危告警（日志 + 可选 webhook）
│   ├── sampling.py           # 线上样本脱敏导出（数据闭环）
│   ├── evaluation.py         # 风险分级评测
│   ├── cache.py              # 回复缓存（TTL + LRU）
│   ├── schemas.py            # 请求/响应模型
│   ├── storage.py            # SQLite 会话/消息/审计 + schema 迁移
│   ├── paths.py              # 仓库根 / skill 目录定位
│   ├── dialogue/
│   │   ├── orchestrator.py   # 对话编排：组装 prompt -> LLM -> 解析 -> 兜底
│   │   ├── memory.py         # 长会话滚动摘要 + 长期画像
│   │   ├── routing.py        # 按风险选择模型
│   │   ├── prompt.py         # SKILL.md 载入 + 人格覆盖 + 风险标签
│   │   ├── markers.py        # 场景标记解析 + 本地兜底分级
│   │   └── llm_client.py     # OpenAI 兼容客户端封装
│   └── safety/
│       ├── safety_checker.py    # 运行时关键词快检 + 数据质检核心
│       └── semantic_checker.py  # 高风险 LLM 语义复核
└── tests/
    ├── test_safety_checker.py
    ├── test_markers.py
    ├── test_orchestrator.py
    ├── test_api.py
    ├── test_storage.py
    ├── test_security.py
    ├── test_semantic_checker.py
    ├── test_alerts.py
    ├── test_memory.py
    ├── test_sampling.py
    ├── test_evaluation.py
    ├── test_routing.py
    ├── test_cache.py
    ├── eval/risk_cases.jsonl   # 风险分级评测集
    ├── redline_cases.jsonl   # 红线测试集
    └── bad_sample_test_llm_bad_cases.csv
```

## 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

## 配置

支持 `.env`（复制仓库根目录的 `.env.example` 为 `.env`）或直接设环境变量。

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `DEEPSEEK_API_KEY` | 大模型 API Key（**必填**，仅在服务端保存） | 空 |
| `DEEPSEEK_BASE_URL` | 兼容 OpenAI 的接口地址 | `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | 模型名 | `deepseek-chat` |
| `CORS_ORIGINS` | 允许的前端来源，逗号分隔 | `*` |
| `MAX_HISTORY` | 携带的历史消息条数上限 | `10` |
| `DB_PATH` | SQLite 数据库文件路径 | `backend/data/sessions.db` |
| `BACKEND_API_KEY` | 后端访问密钥；**留空则关闭鉴权**（仅建议本地） | 空 |
| `RATE_LIMIT_PER_MINUTE` | 每 IP 每分钟请求上限；`0` 关闭 | `60` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_FORMAT` | `json` 或 `plain` | `plain` |
| `SEMANTIC_CHECK` | 是否开启高风险语义复核（`1`/`true`） | `0` |
| `SEMANTIC_CHECK_RISKS` | 需要语义复核的风险等级，逗号分隔 | `R3,M0,S0,S1,S2` |
| `SEMANTIC_CHECK_FALLBACK` | 语义复核判定不安全时是否替换为安全话术 | `1` |
| `ALERT_RISKS` | 触发告警的风险等级，逗号分隔 | `R3,M0,S0` |
| `ALERT_WEBHOOK_URL` | 告警 webhook 地址（为空仅记日志） | 空 |
| `SUMMARY_ENABLED` | 是否开启长会话滚动摘要与画像 | `1` |
| `SUMMARY_THRESHOLD` | 消息数超过该值才触发摘要 | `20` |
| `SUMMARY_KEEP_RECENT` | 摘要时保留最近多少条原文 | `10` |
| `MAX_MESSAGE_CHARS` | 单条用户消息最大字符数 | `2000` |
| `MAX_HISTORY_ITEMS` | 单次请求历史消息条数上限 | `20` |
| `MAX_TOKENS` | 单次回复最大 token | `600` |
| `TEMPERATURE` | 采样温度 | `0.7` |
| `ROUTING_ENABLED` | 是否按风险路由模型 | `0` |
| `MODEL_FAST` / `MODEL_STRONG` | 低/高风险模型（空则用 `DEEPSEEK_MODEL`） | 空 |
| `CACHE_ENABLED` | 是否开启回复缓存 | `0` |
| `CACHE_TTL_SECONDS` | 缓存有效期秒数 | `300` |
| `CACHE_MAX_SIZE` | 缓存最大条目数 | `256` |

## 性能与成本

- **Token 预算**：`MAX_MESSAGE_CHARS` / `MAX_HISTORY_ITEMS` 在 `schemas.py` 做请求校验，超限返回 `422`；`MAX_TOKENS` / `TEMPERATURE` 控制单次生成成本。每次调用记录 `llm_usage`（prompt/completion/total tokens）便于成本观测。
- **模型路由**（`dialogue/routing.py`）：开启 `ROUTING_ENABLED` 后，用本地关键词对输入做廉价预判，高风险（R3/R2b/M0/M1/S*）走 `MODEL_STRONG`，其余走 `MODEL_FAST`。
- **回复缓存**（`cache.py`）：TTL + LRU。**默认关闭**，且只缓存"低风险 + 无上下文"的首轮问答（无 `session_id`、无 `history`，本地预判与最终风险均为 R0/R1 且未兜底），高风险/带上下文一律不走缓存。
- **并发**：当前为同步实现，FastAPI 会把同步端点放到线程池执行，不会阻塞事件循环；进一步可改用 `AsyncOpenAI` + 异步端点（见计划）。

## 长期记忆

`dialogue/memory.py` 在会话消息数超过 `SUMMARY_THRESHOLD` 时，把"较旧"的对话（保留最近 `SUMMARY_KEEP_RECENT` 条原文）交给 LLM 压缩为：

- **摘要**（summary）：按时间顺序概括重要事实与情绪；
- **画像**（profile）：结构化字段 `conditions / medications / family / preferences / notes`。

二者以 schema v3 存在 `sessions` 表，续接会话时作为「历史摘要」「已知信息」注入 system prompt。摘要更新失败不阻断对话。当前画像按会话存储，跨会话复用需配合用户身份（见计划）。

此外，续接时会用 `markers.append_marker` 为历史助手回复补回场景标记（库里存的是剥离后的正文），保证模型上下文格式一致，避免模型模仿"无标记"而漏标。

## 安全加固

- **第二层（关键词快检）**：`safety/safety_checker.py`，覆盖开药/调药/劝退就医/轻视症状/贴标签等硬红线，命中即替换为 `orchestrator.SAFE_FALLBACKS` 的安全话术。
- **第三层（语义复核）**：`safety/semantic_checker.py`。开启 `SEMANTIC_CHECK` 后，仅对 `SEMANTIC_CHECK_RISKS` 中的高风险等级额外调一次 LLM 审核，抓关键词漏掉的换说法越界；复核失败不阻断主链路，仅记录 `semantic_check_error`。
- **高危告警**：`alerts.py`。命中兜底或风险等级在 `ALERT_RISKS` 内时记录 `high_risk_alert`，配置 `ALERT_WEBHOOK_URL` 则 POST 推送（3s 超时，失败不影响对话）。
- **审计**：每次成功回复落 `audit_log`（含 `risk`/`violations`/`fallback_used`/`latency_ms`），可用 `/api/audit` 复盘。
- **红队回归**：`tests/redline_cases.jsonl` 覆盖正例与反例，CI 自动跑；新增安全规则必须补用例。

## 鉴权与限流

- 配置了 `BACKEND_API_KEY` 后，除 `/api/health` 外的接口都需要请求头 `X-API-Key: <key>`，否则返回 `401`。
- 超过 `RATE_LIMIT_PER_MINUTE` 时返回 `429`，并带 `Retry-After` 头。
- 限流为单进程内存实现；多副本部署请替换为 Redis 等共享存储。

## 运行

```powershell
set DEEPSEEK_API_KEY=sk-xxx
set BACKEND_API_KEY=your-secret
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

交互式文档：<http://127.0.0.1:8000/docs>

## API

### `POST /api/chat`

请求：

```json
{
  "message": "我血压有点高，是不是该加药了？",
  "personality": "温婉邻居型",
  "history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

响应：

```json
{
  "reply": "……",
  "risk": "R1",
  "risk_label": "一般关注",
  "violations": [],
  "fallback_used": false,
  "semantic_checked": false,
  "cached": false,
  "personality": "温婉邻居型",
  "model": "deepseek-chat"
}
```

- 后端会剥离 LLM 回复末尾的场景标记，单独以 `risk` 返回。
- 命中硬红线（禁止话术 / 缺失紧急要素）时，`reply` 会被替换为安全兜底话术，`fallback_used=true`。
- `violations` 为质检提示列表，供前端展示；`semantic_checked` 表示是否走了高风险语义复核；`cached` 表示是否命中缓存。
- 首次请求不传 `session_id` 时会自动新建会话并在响应里返回 `session_id`；后续带上它即可自动续接上下文（此时 `history` 被忽略）。

### `POST /api/chat/stream`

流式输出（SSE），逐字返回。事件类型：

- `event: delta` — `data: {"text": "..."}`，增量文本。
- `event: done` — `data: {reply, risk, risk_label, violations, fallback_used, semantic_checked, personality, model, session_id}`，最终结果。
  客户端应以 `done.reply` 覆盖已显示的正文（命中红线时它是安全兜底话术）。
- `event: error` — `data: {"detail": "..."}`。

### 会话接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/sessions` | 会话列表（含消息数） |
| `GET` | `/api/sessions/{id}` | 会话详情、全部消息，以及 `summary` / `profile` |
| `DELETE` | `/api/sessions/{id}` | 删除会话 |

### 审计接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/audit` | 最近审计记录，支持 `?limit=` 与 `?session_id=` |

每条成功回复都会写入审计：`user_message`、`reply`、`risk`、`violations`、`fallback_used`、`model`、`latency_ms`、`created_at`，便于复盘高危场景。

### `GET /api/cache/stats`

返回缓存开关与命中统计：`{enabled, size, hits, misses, max_size, ttl}`。

### `GET /api/health`

返回 `{ "status": "ok", "model": "...", "has_api_key": true, "auth_enabled": false }`。此接口无需鉴权。

### `GET /api/personalities`

返回四种人格的名称、描述与默认标记。

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -v
```

## 计划

1. 会话标题与用户身份。
2. 前端会话列表 / 历史回看。
3. 端到端集成测试。
