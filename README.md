# 小暖对话系统（healthconsult-dialogue-system）

面向老年健康陪护的完整对话系统。系统由三部分组成：**前端**（用户界面）、**后端**（对话编排与安全兜底）、**skill**（可插拔的回复规范包）。

一句话：**前端负责呈现，后端负责对话，skill 负责"怎么答才安全"。**

---

## 系统架构

```
┌───────────┐   HTTP    ┌──────────────────────┐   system prompt   ┌──────────────────────────────┐
│  前端      │ ────────▶ │  后端 backend/        │ ───────────────▶  │  skill                        │
│ frontend/ │ ◀──────── │  对话编排 + 安全检查   │ ◀───────────────  │  skills/healthconsult-...     │
└───────────┘  回复+等级 └──────────────────────┘   LLM 回复+标记    │  SKILL.md / rules / examples  │
                                                                    └──────────────────────────────┘
```

一次对话的数据流：

1. 前端把老人说的话发给后端。
2. 后端把 skill 的 `SKILL.md` 作为 system prompt，调用 LLM。
3. LLM 生成回复，并在末尾附带场景标记（如 `[RISK:R1]`）。
4. 后端剥离标记，并用 `safety_checker` 做一次兜底快检。
5. 后端把「回复正文 + 风险等级」返回前端展示。

更完整的说明见 `docs/architecture.md`。

---

## 目录结构

| 路径 | 职责 | 状态 |
|------|------|------|
| `frontend/` | 网页界面（人格展示 + 在线对话 + TTS） | 可用 |
| `backend/` | 对话系统后端：编排、安全检查、API | 骨架 |
| `skills/healthconsult-assistant-skill/` | 小暖回复规范包（纯规范，零 Python） | 可用 |
| `tools/` | 离线数据工具：生成、清洗、质检、人格对比 | 可用 |
| `tests/results/` | 历史测试结果 | 可用 |
| `docs/` | 系统架构与设计文档 | 可用 |
| `.github/workflows/ci.yml` | 自动测试配置 | 可用 |

**skill 与系统相互独立**：`skills/` 里只有规范、规则、示例数据，不含任何 Python 代码；前端、后端、工具都可以替换，skill 本身保持可复用。

---

## 环境要求

- **Python 3.10+**（推荐 3.12）：后端与 tools 需要
- **现代浏览器**（Chrome / Edge）：前端需要
- 想真调用大模型，需要一个兼容 OpenAI 接口的 API Key（如 DeepSeek）

---

## 快速开始

### 只想看界面（零安装）

用浏览器直接打开 `frontend/index.html`（人格对比）或 `frontend/chat.html`（在线对话）。
`chat.html` 填的 API Key 只保存在本机浏览器，不会上传。

### 跑 skill 工具与测试

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tools/requirements.txt
.\.venv\Scripts\python.exe -m pytest backend/tests/test_safety_checker.py -v
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r tools/requirements.txt
python -m pytest backend/tests/test_safety_checker.py -v
```

### 质检 skill 数据

```powershell
.\.venv\Scripts\python.exe tools/validate_outputs.py --input skills/healthconsult-assistant-skill/examples/v0.2.3_health_safety_repair.jsonl --mode generated_sft
```

### 后端（骨架）

后端目前只有占位实现，尚未提供可运行的 API。目录与职责见 `backend/README.md`。

---

## 常用命令速查

| 我想…… | 命令 |
|--------|------|
| 跑后端单测 + 红线用例 | `python -m pytest backend/tests/test_safety_checker.py -v` |
| 严格质检训练候选集 | `python tools/validate_outputs.py --input skills/healthconsult-assistant-skill/examples/v0.2.3_health_safety_repair.jsonl --mode generated_sft` |
| 开发用命令行对话 | `python tools/chat.py --mode local`（或 `--mode api --api-key sk-xxx`） |
| 清洗候选数据 | `python tools/clean_candidates.py --input <in.jsonl> --output <out.jsonl>` |
| 生成候选数据 | `python tools/generate_candidates.py`（需要 API Key） |
| 四版人格批量对比 | `python tools/batch_test_personalities.py`（需要 API Key） |
| 生成人工抽查清单 | `python tools/generate_manual_review_list.py` |

> Windows 下把 `python` 换成 `.\.venv\Scripts\python.exe`。

---

## 如何新增或替换 skill

1. 在 `skills/` 下新建一个目录，放入该 skill 的 `SKILL.md` 与规则、示例。
2. 在 `backend/` 的编排逻辑里指定要加载的 skill 目录（见 `backend/app/paths.py`）。
3. 用 `tools/validate_outputs.py` 质检该 skill 的示例数据。

系统与 skill 解耦，替换 skill 不需要改动前端。

---

## 相关仓库

- 旧版 skill 独立仓库（已归档，不再维护）：https://github.com/aha0222/healthconsult-assistant-skill

---

## 参考

- Nuwa Skill 官方仓库：https://github.com/alchaincyf/nuwa-skill
- Agent Skills：https://agentskills.io/
