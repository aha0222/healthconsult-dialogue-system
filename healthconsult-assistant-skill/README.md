# healthconsult-assistant-skill

小暖老年健康陪护对话回复规范 Skill。面向养老康养场景，以**温婉邻居型**人格为核心，提供安全的健康咨询回复生成规范。

本 Skill 只规范 assistant 回复行为，不包含 user 数据，不替代模型训练，不包含 API Key 或模型权重。

## 文件说明

| 文件 | 用途 |
|------|------|
| `SKILL.md` | 核心行为规范：角色定位、安全规则、回复模板、正反面示例 |
| `rules/medical_safety_boundaries.md` | 医疗安全红线 |
| `rules/emergency_escalation_rules.md` | 急症升级规则，R2-R3 场景响应 |
| `rules/medication_boundary_rules.md` | 用药边界，禁止引导自行调药 |
| `rules/response_patterns.md` | 五大回复模式（日常/用药/急症/慢病/情感） |
| `rules/forbidden_medical_outputs.md` | 禁止话术完整清单 |
| `examples/good_health_assistant_examples.jsonl` | 正面回复示例 |
| `examples/bad_health_assistant_examples.md` | 错误回复对照 |
| `examples/paired_health_messages.jsonl` | system-user-assistant 三元组示例 |
| `tests/validate_health_assistant_outputs.py` | 回复自动质检脚本 |
| `demo.html` | 人格测试对比展示 Demo（大创汇报用） |
| `personalities.md` | 四版人格蒸馏方案 |
| `personality_test_report.md` | 8 场景实测报告 |

## 核心安全边界

小暖绝不：诊断、开药、调药、替代医生判断。

## 校验

```bash
# 审核已有数据中提取的示例（宽松模式）
python tests/validate_health_assistant_outputs.py \
  --input examples/paired_health_messages.jsonl \
  --mode source_sample

# 审核新生成的候选数据（严格模式）
python tests/validate_health_assistant_outputs.py \
  --input <your_candidates.jsonl> \
  --mode generated_sft
```

source_sample 模式下急症/用药的缺失为 warning，generated_sft 模式下为 fatal。任何 fatal error 的记录不得进入训练数据。

## 参考

- Nuwa Skill 官方仓库：https://github.com/alchaincyf/nuwa-skill
- Agent Skills：https://agentskills.io/
