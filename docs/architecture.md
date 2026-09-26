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
  -> 调用 LLM 生成回复，要求末尾带双维度标签
  -> 解析并剥离标记，得到 (回复正文, 风险等级, 场景列表)
  -> safety_checker.check_reply 兜底快检
  -> 命中硬红线时，替换为按主场景/风险等级预置的安全兜底话术
  -> 返回前端展示
```

前端通过 `POST /api/chat` 调用后端（`backend/app/main.py`），请求携带 `message`、`personality` 与 `history`/`session_id`，
响应返回 `reply`、`risk`、`risk_label`、`violations`、`fallback_used`、`session_id`。
另有 `POST /api/chat/stream` 提供 SSE 流式输出：先逐字推送 `delta`，最后推送 `done`（命中红线时 `done.reply` 为安全兜底话术，客户端覆盖显示）。
会话与消息用 SQLite 持久化（`backend/app/storage.py`），带 `session_id` 时自动续接上下文。
长会话由 `dialogue/memory.py` 维护滚动摘要与长期画像（schema v3），续接时注入 system prompt。
续接时还会用 `markers.append_marker` 把历史助手回复的双维度标签还原，避免模型模仿"无标记"格式而漏标。
LLM 漏标时，后端用 `dialogue/markers.py` 的本地关键词分类器兜底分级。

## 用户级档案与权威记忆

欢迎流程采集的 10 项资料不再只停留在浏览器 `localStorage`，而是通过 `POST /api/profile`
写入后端 `users` 表（schema v5/v6/v7），成为跨会话复用的「用户级权威档案」：

- `collected`：采集的 10 项资料（敏感字段已脱敏，紧急联系电话只保留首尾位、中间遮蔽）。
- `profile`：由采集字段映射出的「自述画像」（`conditions / medications / notes`，不含对话提炼）。
- `conversation_profile`：由对话提炼出的「对话画像」（`conditions / medications / family / preferences / notes`）。
- `summary`：由采集字段生成的确定性摘要（采集摘要）。
- `conversation_summary`：对话提炼的滚动摘要（对话摘要），与采集摘要分离、互不覆盖。

`dialogue/profile.py` 是唯一事实源：负责「采集字段 → 画像」映射、敏感字段脱敏、
用户自述画像与对话提炼画像的合并（去重，冲突以用户自述为准），以及组装唯一注入
system prompt 的「【已知信息】」块。

`dialogue/memory.py` 在会话绑定用户（`sessions.user_id`）后，把对话提炼画像持久化到
`users.conversation_profile`、对话摘要持久化到 `users.conversation_summary`，读取时用
`merge_profiles(profile, conversation_profile)`（用户自述优先、去重）合成权威画像，使同一用户
新开会话也能读到此前对话里新学到的家属、偏好、新基础病等。注入 system prompt 时只使用一份合并后的
「【已知信息】」，不再同时注入 `user_profile` 与 `memory_block` 两份重复、冲突的信息。所有已知信息
统一标注「未经医疗核实」，不得据此诊断、开药、调药或判断是否就医。

## 实现状态

| 组件 | 状态 |
|------|------|
| `backend/app/dialogue/orchestrator.py` | 已实现（编排 + 兜底 + 流式） |
| `backend/app/main.py`（HTTP API） | 已实现（chat / stream / sessions / users / profile / audit / health / personalities） |
| `backend/app/storage.py` | 已实现（SQLite 会话/消息/审计/记忆/用户档案 + schema 迁移） |
| `backend/app/dialogue/memory.py` | 已实现（长会话滚动摘要 + 长期画像 + 用户级合并） |
| `backend/app/profile.py` | 已实现（采集字段映射、脱敏、画像合并、已知信息组装） |
| `backend/app/sampling.py` | 已实现（线上样本脱敏导出） |
| `backend/app/evaluation.py` | 已实现（风险分级评测） |
| `backend/app/dialogue/routing.py` | 已实现（按风险选择模型） |
| `backend/app/dialogue/classifier.py` | 已实现（关键词快路径 + 歧义 LLM 兜底） |
| `backend/app/dialogue/retriever.py` | 已实现（local/api/hash 三嵌入后端 + 向量缓存） |
| `backend/app/dialogue/reranker.py` | 已实现（向量 + n-gram + 关键词混合精排） |
| `backend/app/cache.py` | 已实现（低风险首轮问答缓存，默认关闭） |
| `backend/app/security.py` | 已实现（API Key 鉴权 + 按 IP 限流） |
| `backend/app/safety/semantic_checker.py` | 已实现（高风险 LLM 语义复核，默认对 R3/R2b 开启） |
| `backend/app/alerts.py` | 已实现（高危告警：日志 + 可选 webhook） |
| `backend/app/logging_config.py` | 已实现（plain / json 结构化日志） |
| `frontend/chat.html` | 已改为调用后端流式 API，浏览器不再接触大模型密钥 |

> 鉴权：配置 `BACKEND_API_KEY` 后，除 `/api/health` 外接口需带 `X-API-Key`。审计日志记录每条回复的 `risk`/`violations`/`fallback_used`/`latency_ms`，便于高危场景复盘。

## 三层安全

| 层级 | 方式 | 成本 | 作用 |
|------|------|------|------|
| 第一层 | `SKILL.md` 作为系统指令 | 0 | LLM 自主定风险等级 + 打场景类别并约束行为，绝大多数安全问题在此解决 |
| 第二层 | 本地关键词快检（`safety_checker`） | <1ms | 覆盖开药、调药、怂恿开门、轻视心理危机等硬红线，命中即替换为安全话术 |
| 第三层 | LLM 语义复核（`safety/semantic_checker.py`，默认开启） | 一次 LLM 调用 | 对高风险等级（默认 R3/R2b）复核，抓关键词漏掉的换说法越界 |

离线侧另用 `tools/validate_outputs.py --mode llm` 做入库前的精确审查，不走实时链路。

**高危告警与红队**：`alerts.py` 在命中兜底或高风险等级时记录/推送告警；`tests/redline_cases.jsonl` 作为红队回归集，CI 自动执行，新增安全规则必须补用例。

## skill 输出契约

每条回复末尾必须携带**双维度标签**：恰好一个风险等级 + 一个或多个场景类别。

- 风险等级（唯一）：`[RISK:R3]` / `[RISK:R2b]` / `[RISK:R2a]` / `[RISK:R1]` / `[RISK:R0]`
- 场景类别（可交叉，最多 3 个）：`[SCENE:S1-S4]` / `[SCENE:M1-M2]` / `[SCENE:L1-L4]` / `[SCENE:E1]` / `[SCENE:N1-N3]` / `[SCENE:X1-X2]`

完整定义与旧码映射见 `skills/healthconsult-assistant-skill/rules/taxonomy.md`；
实现层单一事实源为 `backend/app/dialogue/taxonomy.py`。

质检时，`generated_sft` 模式缺失标记为 fatal，`source_sample` 模式为 warning；
同时校验风险唯一、场景 1~3 个、枚举合法且无重复。

## 数据闭环

线上真实对话通过审计表回流到离线质检，形成持续改进闭环：

```
线上对话 → audit_log → tools/export_online_samples.py（脱敏）
        → JSONL → tools/validate_outputs.py --mode generated_sft
        → 发现问题 → 补规则/补红线用例 → 回归
```

- `backend/app/sampling.py`：`redact`（手机号/身份证/银行卡脱敏）、`audit_to_sample`（按 risk + scenes 还原双维度标签，复用现有质检契约）、`export_samples`（支持 `--since`、`--only-flagged`）。
- `backend/app/evaluation.py` + `tools/eval_risk.py`：固定评测集 `backend/tests/eval/risk_cases.jsonl` 上给风险分级器打分；`--mode local` 用本地兜底分级器（确定性，CI 可跑），`--mode llm` 走真实编排链路。
- SFT 迭代：`generate_candidates` → `clean_candidates` → `validate_outputs` → `generate_manual_review_list` → 版本化入库（现为 `v0.2.3`）。

## 场景/风险分级与语料检索（一期：离线工具）

两步对话的第一步是**先定风险等级与场景类别**，用于后续按标签检索语料、提升回复质量。

```
用户输入
  -> classifier 关键词/规则快路径（<1ms，0 token）
       命中且无歧义 -> 直接得 (risk, scenes)
       未命中/歧义/冲突 -> LLM 兜底分类（Retriever 召回 + Reranker 精排的 Top-N 相似语料作少样本）
  -> 第二步：按 (risk, scenes) 检索语料，注入回复生成 prompt（语料库已建：569 条）
```

- `classifier.py`：快路径复用 `safety_checker.detect_scenes` 与 `markers.infer_tags_local`；
  歧义判定含"无场景命中 / 风险与场景矛盾 / 仅泛化词命中 / 多高风险场景并存"。
- `retriever.py`：嵌入后端可插拔，保证可移植——`local`（sentence-transformers，权重在用户缓存目录）、
  `api`（OpenAI 兼容 `/embeddings`，零下载）、`hash`（纯 Python，零依赖零下载，测试/离线兜底）；
  local/api 不可用时自动回退 hash。语料为空时返回空列表，分类退回快路径 + LLM 兜底，功能不受影响。
- `reranker.py`：向量相似度 + 字符 n-gram + 关键词/标签命中的混合打分，只保留 Top-N(2~5) 交给 LLM。
- 语料：`backend/app/dialogue/corpus/scene_risk_corpus.jsonl`（**569 条**，稳定编号 C001…），
  由 `tools/build_scene_risk_corpus.py` 从训练语料 `v0.3.0_corpus500.jsonl`（498 条）投影派生，
  并复用既有标注样本；不带参数即可重建。语料建设流程与审核见 `docs/corpus_build_report.md`。
- 工具：`tools/classify_scene_risk.py`（CLI，`--mode auto|keyword|llm`、`--setup` 预热）、
  `tools/eval_classifier.py`（快路径覆盖率 / 兜底率 / 准确率 / token 估算）。
- 依赖：`tools/requirements-classifier.txt`（仅 `local` 后端需要），向量缓存写入 `.cache/`（已 gitignore）。

评测参考（`risk_cases.jsonl` 21 条，hash 后端）：快路径覆盖约 81%、兜底率约 19%，
风险准确率与场景 F1 均为 100%；兜底单次 prompt 约 350 token，对照完整 `SKILL.md` 约 7000 token。

## 人格选型评测

默认人格（温婉邻居型）由离线评测确定，完整报告见 `docs/personality_evaluation_report.md`。
评测不进入运行时链路，但为 `dialogue/prompt.py` 的 `DEFAULT_PERSONALITY` 提供依据。

- **生成**：`tools/generate_personality_responses.py` 从 `SKILL.md` 剥离人格小节得到中立安全骨架，
  再为四版各自附加等价人格定义，消除「基础人格写死」导致的对照不公平。
- **评价**：`tools/score_replies.py`（五维量表盲评）、`tools/build_manual_review.py`（人工同量表抽检）、
  `tools/rank_personas.py`（场景内强制排序）。量表唯一定义在 `tools/_rubric.py`，人工评审页与大模型评委共用，
  避免两套标准漂移。
- **统计**：`tools/analyze_scores.py`（bootstrap 置信区间 + 区组置换检验 + 量表效度检查）、
  `tools/analyze_agreement.py`（人工 vs 大模型偏差与相关）。
- **回归**：`backend/tests/test_persona_evaluation.py` 对已入库的原始数据断言跨方法不变量，
  防止后续改人格或改 SKILL.md 时结论被静默推翻。
- **展示**：`tools/export_persona_report_data.py` 把聚合结果导出为
  `frontend/data/personality_evaluation.js`（挂到 `window` 的 JS 对象，规避 file:// 下
  `fetch` 本地 JSON 的 CORS 限制），`frontend/index.html` 只做渲染，不含硬编码评分。

原始数据（344 条回复、344 条评分、86 轮排序、87 条人工评分）在 `tests/results/`，
人工评审材料在 `skills/healthconsult-assistant-skill/examples/manual_review/`。

## 性能与成本

- **预算**：`MAX_MESSAGE_CHARS` / `MAX_HISTORY_ITEMS` 在请求层校验；`MAX_TOKENS` / `TEMPERATURE` 控制生成成本；每次调用记录 `llm_usage`。
- **模型路由**：开启后按输入风险走 `MODEL_FAST` / `MODEL_STRONG`。
- **缓存**：TTL + LRU，仅低风险、无上下文的首轮问答，默认关闭。
- **并发**：同步实现，FastAPI 用线程池承载同步端点；后续可切 `AsyncOpenAI` + 异步端点。

## 扩展点

- **新增 skill**：在 `skills/` 下加目录，后端指定加载路径即可。
- **新增安全规则**：改 `skills/.../rules/` 与 `safety_checker.py`，并补 `redline_cases.jsonl` 用例。
- **替换前端/后端**：skill 不受影响。
