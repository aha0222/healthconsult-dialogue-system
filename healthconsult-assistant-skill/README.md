# 小暖健康陪护 Skill

给"会聊天的老年健康助手"定的一套**安全回复规范**，外加配套的生成、清洗、质检工具。

一句话：**让 AI 陪老人聊天时，既温暖，又不乱来。**

---

## 这是什么？用大白话讲

想象一个放在养老机器人或手机 App 里的 AI 助手，老人对它说：

> "我血压这两天有点高，是不是该加药？"

这句回答背后有个大坑：**AI 不能替医生做决定**。它要是回一句"加半片吧"，可能出人命。

这个项目做的事，就是给这个 AI 立规矩：

1. **先判断这句话有多危险**（普通咨询？还是要马上打 120？）
2. **按危险程度给出安全回复**（该劝就医就劝就医，该打 120 就打 120）
3. **每条回复末尾打一个"场景标签"**，方便程序检查它有没有跑偏
4. **提供一整套工具**，自动检查回复合不合格

> 它**不是**模型、不是 App、也不是训练数据本身。它是一份"行为规范 + 质检工具包"。
> 里面**不含**任何 API Key、模型权重或用户隐私数据。

---

## 30 秒看懂它怎么工作

```
老人说一句话
     │
     ▼
先分类：S（人身安全）/ M（心理）/ R（身体健康）/ X（闲聊）
     │
     ▼
再定级：比如 R0 日常 → R1 波动 → R2 要就医 → R3 急症
     │
     ▼
按等级给出安全回复，末尾带上标签，例如：[RISK:R1]
```

四种场景的简单理解：

| 类别 | 管什么 | 例子 |
|------|--------|------|
| **S** | 人身 / 环境 / 诈骗安全 | "有人敲门""煤气味""中奖要转账" |
| **M** | 心理状态 | "不想活了""天天一个人没人说话" |
| **R** | 身体健康 | 血压、血糖、用药、胸痛、失眠 |
| **X** | 和健康无关的闲聊 | 手机怎么用、今天吃什么 |

紧急程度（R 类最常用）：`R0` 日常 → `R1` 指标波动 → `R2a` 尽快就医 → `R2b` 今天必须去 → `R3` 立即打 120。

---

## 先看效果（不用装任何东西）

只想看看它长啥样，直接用浏览器打开这两个文件即可，**零安装**：

| 文件 | 作用 |
|------|------|
| `demo/index.html` | 四版人格对比展示页（适合做汇报） |
| `demo/chat.html` | 在线对话界面，可切换四版人格、浏览器语音播报 |

`chat.html` 想真聊天需要填一个 API Key（页面里填）。**这个 Key 只保存在你自己的浏览器里**，不会上传到任何服务器。不填 Key 也能先看界面和演示数据。

---

## 快速开始（约 5 分钟）

### 前置条件

- 电脑上装了 **Python 3.10 或更高版本**（推荐 3.12）
- 会复制粘贴命令就行

检查是否已装 Python：

```powershell
python --version
```

### 第 1 步：装好运行环境

**Windows（PowerShell）：**

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS / Linux：**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> 如果 `python` 找不到，Windows 可以试 `py`；macOS/Linux 试 `python3`。

### 第 2 步：本地试跑（不需要 API Key）

```powershell
.\.venv\Scripts\python.exe scripts/chat.py --mode local
```

macOS / Linux 用 `python scripts/chat.py --mode local`。

它会用几个预设场景走一遍流程，让你确认环境没问题。

### 第 3 步：跑一遍自动测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_safety_checker.py -v
```

看到 `37 passed` 就说明一切正常。

### 第 4 步（可选）：接上 API 真聊天

需要一个 DeepSeek（或兼容 OpenAI 接口的）API Key：

```powershell
.\.venv\Scripts\python.exe scripts/chat.py --mode api --api-key sk-你的key --model deepseek-chat --base-url https://api.deepseek.com/v1
```

### 第 5 步（可选）：四版人格批量对比

```powershell
.\.venv\Scripts\python.exe tests/batch_test_personalities.py
```

> 这个需要 API Key（放到环境变量 `DEEPSEEK_API_KEY`），结果会写进 `tests/results/`。

---

## 目录里都是什么

| 路径 | 作用 | 想改什么来这里 |
|------|------|----------------|
| `SKILL.md` | **核心行为规范**，AI 的人格和安全准则 | 改人格、话术、安全边界 |
| `rules/` | 各类安全规则细则（医疗、急症、用药、心理、人身安全…） | 改某条具体规则 |
| `examples/` | 正例、反例、候选数据、清洗后的训练集 | 看/换示例数据 |
| `tests/` | 自动质检脚本、红线测试集、单元测试 | 加测试用例 |
| `scripts/` | 对话、数据清洗、人工抽查清单、安全检查模块 | 改工具逻辑 |
| `docs/` | 四版人格方案、实测报告 | 看设计思路 |
| `demo/` | 网页演示 | 改展示效果 |
| `requirements.txt` | Python 依赖清单 | 加依赖 |
| `.github/workflows/ci.yml` | 自动测试配置 | 一般不用动 |

**文档从哪看起？**
- 想懂设计 → `SKILL.md`（最重要）
- 想看对比数据 → `docs/personality_test_report.md`
- 想看反面教材 → `examples/bad_health_assistant_examples.md`

---

## 常用命令速查

| 我想…… | 命令 |
|--------|------|
| 本地试跑（不花钱） | `python scripts/chat.py --mode local` |
| 真聊天 | `python scripts/chat.py --mode api --api-key sk-xxx` |
| 跑单元测试 | `python -m pytest tests/test_safety_checker.py -v` |
| 质检已有示例（宽松） | `python tests/validate_health_assistant_outputs.py --input examples/paired_health_messages.jsonl --mode source_sample` |
| 质检新生成数据（严格） | `python tests/validate_health_assistant_outputs.py --input 你的文件.jsonl --mode generated_sft` |
| 清洗候选数据 | `python scripts/clean_candidates.py --input examples/generated_candidates_20260719_174148.jsonl --output examples/v0.2.3_health_safety_repair.jsonl` |
| 生成人工抽查清单 | `python scripts/generate_manual_review_list.py` |

> Windows 下把 `python` 换成 `.\.venv\Scripts\python.exe`。

---

## 数据质检是怎么判的？

质检脚本有三种模式，差别在于**严格程度**：

| 模式 | 用途 | 缺安全提醒时 |
|------|------|--------------|
| `source_sample` | 审核从已有数据里挑出来的示例 | 只提醒（warning），放行 |
| `generated_sft` | 审核新生成、准备进训练集的数据 | 直接判失败（fatal），拦下 |
| `llm` | 用大模型做语义级审查（需要 API Key） | 更准，但慢、要花钱 |

规则很简单：**只要有 1 条 `fatal`，这份数据就不合格，不能拿去训练。**

另外，每条回复**末尾必须带合法的场景标签**，例如：

```
您这几天固定早晚各量一次血压，把数值记下来带给医生看。[RISK:R1]
```

`generated_sft` 模式下缺标签 = fatal，`source_sample` 模式下 = warning。

每次 push / 提交 PR，GitHub 上的 CI 会自动帮你跑一遍单元测试和严格质检。

---

## 遇到问题怎么办

**`python` 不是内部或外部命令**
没装 Python，或没加进 PATH。去 python.org 下载安装，安装时勾选 "Add Python to PATH"。

**`No module named pytest` / 其他模块缺失**
依赖没装。回到第 1 步重跑 `pip install -r requirements.txt`。

**命令里的路径报错**
确认你现在就在项目根目录（也就是能看到 `SKILL.md` 的目录）下执行命令。

**网页 Demo 打不开或没声音**
直接用浏览器打开 `demo/chat.html` 即可；语音播报依赖浏览器的语音合成功能，Chrome / Edge 支持较好。

**API 报错 / 401 / 超时**
检查 Key 是否正确、账户是否有额度、`--base-url` 是否填对（DeepSeek 是 `https://api.deepseek.com/v1`）。

**控制台中文乱码**
Windows 可先执行 `chcp 65001` 切到 UTF-8；文件本身都是 UTF-8 编码，不影响使用。

---

## 想改内容，从哪下手？

| 你的目标 | 改哪里 |
|----------|--------|
| 调整 AI 的语气、称呼、人格 | `SKILL.md` 第 1 节 |
| 增加/修改安全红线 | `SKILL.md` 第 6 节 + `rules/` 对应文件 |
| 新增一条红线测试 | `tests/redline_cases.jsonl` |
| 调整质检的关键词规则 | `scripts/safety_checker.py` |
| 换一批训练候选数据 | `tests/generate_candidates.py` 里的种子列表 |

改完记得跑一遍 `python -m pytest tests/test_safety_checker.py -v`，确保没把安全校验改坏。

---

## 参考

- Nuwa Skill 官方仓库：https://github.com/alchaincyf/nuwa-skill
- Agent Skills：https://agentskills.io/
