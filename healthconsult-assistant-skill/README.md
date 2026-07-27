# healthconsult-assistant-skill

小暖老年健康陪护对话回复规范 Skill。面向养老康养场景，以**温婉邻居型**人格为核心，提供安全的健康咨询回复生成规范。

本 Skill 只规范 assistant 回复行为，不包含 user 数据，不替代模型训练，不包含 API Key 或模型权重。

## 目录结构

```
healthconsult-assistant-skill/
├── SKILL.md                          # 核心行为规范
├── README.md
│
├── rules/                            # 安全规则
│   ├── medical_safety_boundaries.md  # 医疗安全红线
│   ├── emergency_escalation_rules.md # 急症升级规则
│   ├── medication_boundary_rules.md  # 用药边界
│   ├── forbidden_medical_outputs.md  # 禁止话术清单
│   ├── response_patterns.md          # 六大回复模式
│   ├── personal_safety_rules.md      # 人身/环境/诈骗安全
│   ├── mental_health_rules.md        # 心理危机与情绪困扰
│   ├── family_caregiver_guidance.md  # 家属/照护者代问
│   └── r2a_r2b_distinction.md        # R2a/R2b 就医紧急度区分
│
├── examples/                         # 示例数据
│   ├── good_health_assistant_examples.jsonl
│   ├── bad_health_assistant_examples.md
│   ├── paired_health_messages.jsonl
│   ├── paired_health_messages_bad_cases.csv
│   ├── generated_candidates_20260719_174148.jsonl
│   ├── generated_candidates_passed_20260719_174728.jsonl
│   ├── v0.2.3_health_safety_repair.jsonl  # 清洗后训练候选集
│   └── manual_review_list.csv        # 人工抽查清单
│
├── tests/                            # 测试
│   ├── validate_health_assistant_outputs.py  # 自动质检
│   ├── batch_test_personalities.py           # 四版人格 API 对比
│   ├── generate_candidates.py                # 候选数据生成
│   ├── test_safety_checker.py                # pytest 单元测试
│   ├── redline_cases.jsonl                   # 20+ 条红线测试集
│   ├── bad_sample_test_llm_bad_cases.csv
│   └── results/                              # 历史测试结果
│       ├── batch_report.md
│       └── batch_results.json
│
├── docs/                             # 文档
│   ├── personalities.md              # 四版人格蒸馏方案
│   └── personality_test_report.md    # 8场景实测报告
│
├── demo/                             # 网页 Demo
│   ├── index.html                    # 人格对比展示（大创汇报）
│   └── chat.html                     # 在线对话界面（支持四版人格 + TTS）
│
├── scripts/                          # 脚本
│   ├── chat.py                       # 命令行对话
│   ├── clean_candidates.py           # 候选数据清洗
│   ├── generate_manual_review_list.py # 人工抽查清单生成
│   └── safety_checker.py             # 统一安全检查模块
│
├── requirements.txt                  # Python 依赖
└── .gitignore
```

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 命令行对话（本地测试，无需 API）
.\.venv\Scripts\python.exe scripts/chat.py --mode local

# 3. 命令行对话（API 模式）
.\.venv\Scripts\python.exe scripts/chat.py --mode api --api-key sk-xxx --model deepseek-chat --base-url https://api.deepseek.com/v1

# 4. 网页对话（支持四版人格切换 + 浏览器 TTS 语音播报）
# 浏览器打开 demo/chat.html

# 5. 人格展示页
# 浏览器打开 demo/index.html

# 6. 四版人格批量对比测试
.\.venv\Scripts\python.exe tests/batch_test_personalities.py

# 7. 单元测试
.\.venv\Scripts\python.exe -m pytest tests/test_safety_checker.py -v
```

```bash
# 审核已有数据中提取的示例（宽松模式）
.\.venv\Scripts\python.exe tests/validate_health_assistant_outputs.py \
  --input examples/paired_health_messages.jsonl \
  --mode source_sample

# 审核新生成的候选数据（严格模式）
.\.venv\Scripts\python.exe tests/validate_health_assistant_outputs.py \
  --input <your_candidates.jsonl> \
  --mode generated_sft

# 将生成器产出的 candidates 清洗为最终训练候选集
.\.venv\Scripts\python.exe scripts/clean_candidates.py \
  --input examples/generated_candidates_20260719_174148.jsonl \
  --output examples/v0.2.3_health_safety_repair.jsonl

# 生成人工抽查清单
.\.venv\Scripts\python.exe scripts/generate_manual_review_list.py
```

source_sample 模式下急症/用药的缺失为 warning，generated_sft 模式下为 fatal。任何 fatal error 的记录不得进入训练数据。

## 参考

- Nuwa Skill 官方仓库：https://github.com/alchaincyf/nuwa-skill
- Agent Skills：https://agentskills.io/
