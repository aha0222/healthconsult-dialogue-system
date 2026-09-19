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
3. LLM 生成回复，并在末尾附带双维度标签（如 `[RISK:R1]` + `[SCENE:S3]`）。
4. 后端剥离标记，并用 `safety_checker` 做一次兜底快检。
5. 后端把「回复正文 + 风险等级 + 场景类别」返回前端展示。

更完整的说明见 `docs/architecture.md`。

---

## 目录结构

| 路径 | 职责 | 状态 |
|------|------|------|
| `frontend/` | 网页界面（人格展示 + 在线对话 + TTS） | 可用 |
| `backend/` | 对话系统后端：编排、安全检查、HTTP API | 可用 |
| `skills/healthconsult-assistant-skill/` | 小暖回复规范包（纯规范，零 Python） | 可用 |
| `tools/` | 离线数据工具：生成、清洗、质检、人格对比、线上采样、风险评测 | 可用 |
| `tests/results/` | 历史测试结果 | 可用 |
| `docs/` | 系统架构与设计文档 | 可用 |
| `scripts/` | 一键运行脚本（`run.sh` / `run.ps1`） | 可用 |
| `LICENSE` | MIT 开源许可 | 可用 |
| `.github/workflows/ci.yml` | 自动测试配置 | 可用 |

**skill 与系统相互独立**：`skills/` 里只有规范、规则、示例数据，不含任何 Python 代码；前端、后端、工具都可以替换，skill 本身保持可复用。

---

## 环境要求

- **Python 3.10+**（推荐 3.12）：后端与 tools 需要
- **Ubuntu / Debian**：需先装 venv 支持：`sudo apt update && sudo apt install -y python3-venv`
- **现代浏览器**（Chrome / Edge）：前端需要
- 想真调用大模型，需要一个兼容 OpenAI 接口的 API Key（如 DeepSeek）

> 下面的命令都要在**克隆下来的仓库根目录**执行（即能看到 `backend/`、`tools/`、`frontend/` 的目录），
> 否则会报 `Could not open requirements file`。

---

## 快速开始

### 获取代码

```bash
git clone https://github.com/aha0222/healthconsult-dialogue-system.git
cd healthconsult-dialogue-system
```

也可以直接在 GitHub 页面点 **Code → Download ZIP** 解压。

### 一键运行后端（推荐）

```bash
# macOS / Linux（在仓库根目录执行）
bash scripts/run.sh
```

```powershell
# Windows（PowerShell）
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
```

脚本会自动完成：创建 `.venv` → 安装依赖 → 从 `.env.example` 生成 `.env`。
首次运行后请编辑 `.env` 填入**你自己的** `DEEPSEEK_API_KEY`，再运行一次即可启动后端（默认 `http://127.0.0.1:8000`）。
然后浏览器打开 `frontend/chat.html`，在设置里确认后端地址。

- 只准备环境、不启动：`--setup-only`（PowerShell 用 `-SetupOnly`）。
- 改端口 / 监听地址：`--port 8080`、`--host 0.0.0.0`（PowerShell 用 `-Port`、`-ListenHost`）。

> **别人运行本项目，必须自备一个 `DEEPSEEK_API_KEY`**（仓库不包含任何密钥）。

### 只想预览界面（零安装）

用浏览器直接打开 `frontend/index.html`（人格对比）或 `frontend/chat.html`（在线对话）。
`chat.html` 里填的是**后端访问密钥**（`BACKEND_API_KEY`，由部署方提供），只保存在本机浏览器；
**大模型 API Key（`DEEPSEEK_API_KEY`）始终保存在服务端环境变量中，浏览器不接触**。

### 跑 skill 工具与测试

> 测试会 `import backend.app...`，必须安装 `backend/requirements.txt`（含 fastapi / httpx 等）；
> `tools/requirements.txt` 只覆盖离线脚本，单独安装后跑后端测试会 ImportError。

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip install -r tools/requirements.txt
.\.venv\Scripts\python.exe -m pytest backend/tests -v
```

```bash
# macOS / Linux（在仓库根目录执行）
# Ubuntu / Debian 若报 ensurepip is not available，先装：sudo apt install -y python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m pip install -r tools/requirements.txt
python -m pytest backend/tests -v
```

### 质检 skill 数据

```powershell
.\.venv\Scripts\python.exe tools/validate_outputs.py --input skills/healthconsult-assistant-skill/examples/v0.2.3_health_safety_repair.jsonl --mode generated_sft
```

### 复现人格选型评测

四版人格的默认人格选型见 `docs/personality_evaluation_report.md`：用「大模型绝对打分 + 人工抽检 + 场景内强制排序」三种独立方法交叉验证，结论只取跨方法一致的部分。

```powershell
# 1. 生成回复（43 场景 × 4 人格 × 2 次 = 344 条）
.\.venv\Scripts\python.exe tools\generate_personality_responses.py --runs 2 --workers 5
# 2. 大模型逐条盲评（提示词不含人格名）
.\.venv\Scripts\python.exe tools\score_replies.py --workers 5
# 3. 统计与显著性检验（纯标准库，可离线跑）
.\.venv\Scripts\python.exe tools\analyze_scores.py
# 4. 场景内强制排序（标签随机分配）
.\.venv\Scripts\python.exe tools\rank_personas.py --workers 5
# 5. 导出前端报告数据（纯离线，读 tests/results/）
.\.venv\Scripts\python.exe tools\export_persona_report_data.py
```

第 1/2/4 步会真实调用大模型，需要 `DEEPSEEK_API_KEY`。原始数据在 `tests/results/`，
人工评审材料在 `skills/healthconsult-assistant-skill/examples/manual_review/`。
第 5 步生成的 `frontend/data/personality_evaluation.js` 供 `frontend/index.html` 读取，
页面本身不含硬编码评分。改完人格或重跑评测后，重跑第 5 步即可刷新页面。
旧版 9 场景星级对比（`skills/healthconsult-assistant-skill/docs/personality_test_report.md`）
为初版方法，已被上述评测取代，仅作方法演进对照保留。

### 手动启动后端（不用脚本）

后端把 `SKILL.md` 作为 system prompt 调用 LLM，解析场景标记并做安全兜底，供前端调用。支持 SSE 流式、SQLite 会话/审计持久化、API Key 鉴权、按 IP 限流与结构化日志。
需先按「跑 skill 工具与测试」安装好虚拟环境与依赖。

```powershell
# Windows
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
Copy-Item .env.example .env    # 按需修改
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

```bash
# macOS / Linux
pip install -r backend/requirements.txt
cp .env.example .env           # 按需修改
python -m uvicorn backend.app.main:app --reload --port 8000
```

接口与全部配置见 `backend/README.md`。

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

---

## License

本项目基于 [MIT License](LICENSE) 开源，Copyright (c) 2026 aha0222。
