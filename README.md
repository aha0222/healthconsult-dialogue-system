# 小暖对话系统（healthconsult-dialogue-system）

面向老年健康陪护的完整对话系统。系统由五部分组成：**前端**（用户界面）、**后端**（对话编排与安全兜底）、**skill**（可插拔的回复规范包）、**语音服务**（本地离线 ASR/TTS）、**离线运行时**（本地大模型与一键部署包）。

一句话：**前端负责呈现，后端负责对话，skill 负责"怎么答才安全"，语音与离线让系统离开云端也能用。**

---

## 系统架构

```
┌───────────┐   HTTP/SSE ┌──────────────────────┐   system prompt   ┌──────────────────────────────┐
│  前端      │ ──────────▶ │  后端 backend/        │ ───────────────▶  │  skill                        │
│ frontend/ │ ◀────────── │  对话编排 + 三层安全    │ ◀───────────────  │  SKILL.md / rules / 语料 1725 │
└─────┬─────┘  回复+等级   └──────────┬───────────┘   LLM 回复+标记    └──────────────────────────────┘
      │ HTTP 直连 8100                │ get_llm()
      ▼                               ▼
┌───────────────┐            ┌────────────────────────────┐
│ 语音服务       │            │ LLM：云端 DeepSeek          │
│ voice_service/ │            │   或 本地离线（OFFLINE_MODE）│
│ ASR/TTS/流式   │            │   llama.cpp / 任意私有端点  │
└───────────────┘            └────────────────────────────┘
```

一次对话的数据流：

1. 前端把老人说的话发给后端（说话人识别自动区分「老人本人 / 家属」，仅调整称呼与侧重）。
2. 后端先用关键词快路径预判风险/场景（0 token），非高风险时检索相似语料注入 prompt。
3. 后端把 skill 的 `SKILL.md` 作为 system prompt，调用 LLM（云端或本地）。
4. LLM 生成回复，并在末尾附带双维度标签（如 `[RISK:R1]` + `[SCENE:S3]`）；小模型漏标时由本地关键词兜底。
5. 后端剥离标记，`safety_checker` 硬红线快检，高风险再做 LLM 语义复核。
6. 后端把「回复正文 + 风险等级 + 场景类别」返回前端展示；语音开启时前端把回复交给 voice_service 播报。

更完整的说明见 `docs/architecture.md`；离线交付细节见 `docs/offline_delivery_report.md`。

---

## 目录结构

| 路径 | 职责 | 状态 |
|------|------|------|
| `frontend/` | 网页界面（人格展示 + 在线对话 + 语音接线 `scripts/voice.js`） | 可用 |
| `backend/` | 对话系统后端：编排、三层安全、HTTP API、SQLite 持久化 | 可用 |
| `skills/healthconsult-assistant-skill/` | 小暖回复规范包（纯规范，零 Python；语料 v0.4.0 共 **1725 条 / 24 场景**） | 可用 |
| `voice_service/` | 独立语音服务：本地 ASR（Paraformer）+ TTS（Piper）+ 流式/打断，默认 8100 端口 | 可用 |
| `backend/app/providers/` + `scripts/offline/` | 离线运行时：本地 LLM（llama.cpp 端点优先）+ 一键冷启动/打包/拔网线自检 | 可用 |
| `tools/` | 离线数据工具：语料生成、清洗、质检、分布验收、人格评测、离线自检 | 可用 |
| `backend/tests/` | 后端测试 + **54 例红队安全回归集**（CI 常驻） | 可用 |
| `docs/` | 系统架构、语料建设报告、离线交付报告、人格评测报告 | 可用 |
| `scripts/` | 一键运行脚本（`run.sh` / `run.ps1`）+ `offline/`（离线包） | 可用 |

**skill 与系统相互独立**：`skills/` 里只有规范、规则、示例数据，不含任何可执行代码；前端、后端、工具都可以替换，skill 本身保持可复用。

**能力开关（默认全关 = 与上一阶段行为完全一致）**：

| 开关 | 默认 | 作用 |
|------|------|------|
| `VOICE_ENABLED` | 0 | 启用语音服务（前端探活 voice_service，不可用自动回退打字版） |
| `OFFLINE_MODE` | 0 | LLM 换成本地/私有端点（`get_llm()` 自动切换，绝不回退云端） |
| `SPEAKER_DETECT_ENABLED` | 0 | 说话人识别（区分老人/家属，低置信一律按老人处理） |

---

## 环境要求

- **Python 3.10+**（推荐 3.12）：后端与 tools 需要
- **现代浏览器**（Chrome / Edge）：前端需要
- 云端模式需要一个兼容 OpenAI 接口的 API Key（如 DeepSeek）
- 语音服务：约 300MB 本地模型（脚本自动下载）；**离线 LLM**：约 2GB（CPU 慢，见下文 GPU 建议）
- 可选 GPU 语音合成（Kokoro）：另需 2.6GB 的 **CUDA 版** PyTorch——它的下载源和其它依赖不一样，
  走错源要下十几个小时，见下文「GPU 加速语音合成（可选）」

---

## 快速开始

### 获取代码

```bash
git clone https://github.com/aha0222/healthconsult-dialogue-system.git
cd healthconsult-dialogue-system
```

### 一键运行后端（云端模式，推荐）

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
- 改端口 / 监听地址：`--port 8080`、`--host 0.0.0.0`。

> 别人运行本项目，必须自备一个 `DEEPSEEK_API_KEY`（仓库不包含任何密钥）。

### 启用语音（本地离线，可选）

```powershell
python -m pip install -r voice_service\requirements.txt   # sherpa-onnx / piper-tts 等
python voice_service\download_models.py                   # 下载约 300MB 本地模型（国内源）
python -m voice_service                                    # 默认 http://127.0.0.1:8100
```

浏览器打开 `voice_service/demo.html` 自测录音识别与合成；在 `.env` 设 `VOICE_ENABLED=1` 后，前端对话页的麦克风/播报即走本地语音服务（服务未启动时自动回退浏览器语音或打字）。

上面装的是 CPU 版语音（sherpa-onnx / piper）。Kokoro（音色更自然，`tts.py` 里的首选后端）需要额外装
`kokoro` / `misaki[zh]` 和 **CUDA 版 PyTorch**，装法如下——**它的下载源是个独立的坑**。

#### GPU 加速语音合成（可选）

> ⚠️ **Windows 上的 CUDA 版 PyTorch 只发布在 `download.pytorch.org`。**
> PyPI 上那份 `torch-*-cp3xx-cp3xx-win_amd64.whl`（清华、阿里等 **PyPI 镜像里也是同一份**，约 109MB）
> 是 **CPU-only 构建**。装错之后不报错，只是 `torch.cuda.is_available()` 返回 `False`、GPU 完全用不上。
> 所以**装 torch 本身不要用 `-i https://pypi.tuna.tsinghua.edu.cn/simple`**，只有小依赖（sympy / filelock 等）
> 才走镜像；也**不要把 torch 写进 `voice_service/requirements.txt`**，那会让 `-r` + 镜像装上 CPU 版。

同一个 `torch-2.11.0+cu128-cp312-cp312-win_amd64.whl`（2.62 GiB）各来源实测（2026-10-06，电信宽带）：

| 来源 | 实测速度 | 预计耗时 |
|------|----------|----------|
| `download.pytorch.org/whl/cu128`（官方） | 18.3 MB/s | **约 2.5 分钟** |
| `mirror.sjtu.edu.cn/pytorch-wheels/cu128`（上海交大） | 1.3 MB/s | 约 33 分钟 |
| `mirrors.aliyun.com/pytorch-wheels/cu128`（阿里云） | 84 KB/s | **9～11 小时** |
| 清华 / 南大 PyTorch 镜像 | 没有这个镜像（404） | — |

**阿里云的 pytorch-wheels 镜像极慢，绕开它**——网上「换个国内镜像就秒下」的经验对它不成立，
十几小时的下载基本都是这里来的。推荐两种做法：

```powershell
# 做法 1（最快）：在一台网速好的机器上下好 wheel，再把文件拷给别人（U 盘 / 局域网）
python -m pip download torch==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128 --no-deps -d E:\wheels
# 拿到文件的一方：
python -m pip install E:\wheels\torch-2.11.0+cu128-cp312-cp312-win_amd64.whl -i https://pypi.tuna.tsinghua.edu.cn/simple

# 做法 2：直接走上海交大镜像（已实测 pip 可解析）
python -m pip install torch==2.11.0+cu128 `
  --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/ `
  --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 再装 Kokoro 本身（这两个在 PyPI 上有，走镜像没问题）
python -m pip install kokoro misaki[zh] -i https://pypi.tuna.tsinghua.edu.cn/simple
# v1.1 中文模型要把正文里偶尔出现的「维生素D」「做个CT」念成英文，需要这个 spaCy 模型。
# 不装也能跑（只是那几个英文片段没声音），但离线机器上一定要预装——
# misaki 缺它时会当场 pip 下载，离线环境下就是一次必然失败的网络调用。
python -m spacy download en_core_web_sm
```

装完自查，应输出 `2.11.0+cu128 True`：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

`+cu128` 对 RTX 5060（Blackwell / sm_120）是支持的，不用降级。中文合成用的是
`hexgrad/Kokoro-82M-v1.1-zh`（专训普通话，默认音色 `zf_001`；旧版 v1.0 的 `zf_xiaoxiao`
不在新仓库里）。权重首次合成时从 HuggingFace 拉取（约 320MB），`tts.py` 已默认把
`HF_ENDPOINT` 指向 `hf-mirror.com`，无需翻墙。换音色改 `voice_service/tts.py` 的
`KOKORO_TORCH_VOICE`：`zf_001`～`zf_099` 女声、`zm_xxx` 男声。
注意 pip **不支持单文件断点续传**，换源后从头下比在慢源上续剩下的快得多。

### 离线模式（可选：不要云端 Key 也能跑）

离线档细项见 `scripts/offline/.env.offline.example` 与 `scripts/offline/收件人操作单.md`：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\offline\fetch_model.ps1    # 下载推理引擎 + Qwen2.5-3B（约 2GB）
powershell -ExecutionPolicy Bypass -File scripts\offline\start.ps1          # 一条命令冷启动（llama-server + 后端）
python tools\offline_check.py                                              # 拔网线自检（11 项）
```

**GPU 提速实测（RTX 5060 8GB / Qwen2.5-7B Q4）**：一轮完整问答（含 5775 token 预填充与全等级语义复核）从 CPU 的 414 秒降到 **7.9 秒**，标签输出率 0/4 → 4/4。CUDA 版引擎需补 `python scripts/offline/download_cudart.py`（缺失时 CUDA 后端会**静默回退 CPU**，显存占用极低是判断特征）。机器人落地建议 8GB 级以上 GPU；更大模型走"机器人瘦终端 + 局域网 LLM 服务器"（`OFFLINE_LLM_ENDPOINT` 指过去，代码零改动）。

### 只想预览界面（零安装）

用浏览器直接打开 `frontend/index.html`（人格对比）或 `frontend/chat.html`（在线对话）。
`chat.html` 里填的是**后端访问密钥**（`BACKEND_API_KEY`，由部署方提供），只保存在本机浏览器；
**大模型 API Key（`DEEPSEEK_API_KEY`）始终保存在服务端环境变量中，浏览器不接触**。

### 跑测试与工具

> 后端测试会 `import backend.app...`，必须安装 `backend/requirements.txt`；
> `tools/requirements.txt` 只覆盖离线脚本，单独安装后跑后端测试会 ImportError。

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip install -r tools/requirements.txt
.\.venv\Scripts\python.exe -m pytest -q          # 后端 + 语音契约测试（共 363 项）
```

```bash
# macOS / Linux（在仓库根目录执行）
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r backend/requirements.txt -r tools/requirements.txt
python -m pytest -q
```

CI（`.github/workflows/ci.yml`）每次推送自动执行：ruff → 后端全量测试（覆盖率 ≥75%）→ 语音契约测试 → 风险分级基线 → 语料 v0.4.0 质检。

### 质检 / 验收语料

```powershell
# 质检主语料（1725 条，应无 fatal）
.\.venv\Scripts\python.exe tools\validate_outputs.py --input skills\healthconsult-assistant-skill\examples\corpus\v0.4.0_corpus_expanded.jsonl --mode generated_sft
# 分布验收（61 个场景×风险格子、重复率、篇幅、安全）
.\.venv\Scripts\python.exe tools\check_distribution.py --input skills\healthconsult-assistant-skill\examples\corpus\v0.4.0_corpus_expanded.jsonl
```

### 复现人格选型评测

四版人格的默认人格选型见 `docs/personality_evaluation_report.md`：用「大模型绝对打分 + 人工抽检 + 场景内强制排序」三种独立方法交叉验证，结论只取跨方法一致的部分。

```powershell
.\.venv\Scripts\python.exe tools\generate_personality_responses.py --runs 2 --workers 5   # 生成 344 条
.\.venv\Scripts\python.exe tools\score_replies.py --workers 5                             # 大模型盲评
.\.venv\Scripts\python.exe tools\analyze_scores.py                                        # 统计检验
.\.venv\Scripts\python.exe tools\rank_personas.py --workers 5                             # 场景内强制排序
.\.venv\Scripts\python.exe tools\export_persona_report_data.py                            # 导出前端报告数据
```

第 1/2/4 步会真实调用大模型，需要 `DEEPSEEK_API_KEY`。原始数据在 `tests/results/`，
人工评审材料在 `skills/healthconsult-assistant-skill/examples/manual_review/`。

---

## 三层安全（任何模型、任何开关状态下都不放松）

| 层级 | 方式 | 成本 | 作用 |
|------|------|------|------|
| 第一层 | `SKILL.md` 作为系统指令 | 0 | LLM 自主定风险等级 + 打场景标签，绝大多数安全问题在此解决 |
| 第二层 | 本地关键词红线快检（`safety_checker`） | <1ms | 开药调药、怂恿开门、轻视心理危机等硬红线，命中即替换安全话术；小模型漏标时的定级兜底 |
| 第三层 | LLM 语义复核（默认 R3/R2b，离线档全等级） | 一次 LLM 调用 | 抓关键词漏掉的换说法越界 |

- 红队回归集 `backend/tests/redline_cases.jsonl`（**54 例**）随 CI 执行；**新增安全规则必须补用例**。
- 高危命中写审计与告警（可配 webhook）；说话人角色**不改变任何风险判定**——家属问"能不能加药"与老人自问，红线一致。
- 离线档实测闭环：本地模型漏标 → 关键词兜底判 R2b/E1 → 语义复核触发 → 替换预置安全话术。

---

## 如何新增或替换 skill

1. 在 `skills/` 下新建一个目录，放入该 skill 的 `SKILL.md` 与规则、示例。
2. 在 `backend/` 的编排逻辑里指定要加载的 skill 目录（见 `backend/app/paths.py`）。
3. 用 `tools/validate_outputs.py` 质检该 skill 的示例数据。

系统与 skill 解耦，替换 skill 不需要改动前端。

---

## 相关仓库

- 旧版 skill 独立仓库（已归档，不再维护）：https://github.com/aha0222/healthconsult-assistant-skill

## 参考

- Nuwa Skill 官方仓库：https://github.com/alchaincyf/nuwa-skill
- Agent Skills：https://agentskills.io/

---

## License

本项目基于 [MIT License](LICENSE) 开源，Copyright (c) 2026 aha0222。
