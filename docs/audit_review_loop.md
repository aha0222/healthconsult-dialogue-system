# 审计对话复盘闭环

**工具**：`tools/audit_review.py`
**数据源**：`backend/data/sessions.db` → `audit_log`
**产出目录**：`review/audit_review/<时间>/`（已加入 .gitignore，含真实对话，不要提交）

---

## 一、它解决什么问题

平时的测试/试用对话已经落进 `audit_log`，但如果没有复盘机制，这些数据只是日志。
这个工具把「点踩、重复追问、打断、违规、兜底、风险低估」变成**有优先级的待审队列**，
再由人决定每条对话的去向：

| 去向 | 含义 | 产物 |
|---|---|---|
| `skill` | 回复方式/规范问题 | `skill_suggestions.md`（只给建议，不改 SKILL.md） |
| `negative` | 安全/定级失败样本 | `negative_cases.jsonl`（待审负例） |
| `corpus` | 可复用的问答对 | `corpus_candidates.jsonl`（候选，不直接进正式语料） |

**工具不自动改 SKILL.md、安全规则、正式语料库或模型权重。** 所有吸收都要人工确认并过回归门禁。

---

## 二、日常用法

### 1. 生成复盘队列

```powershell
python tools\audit_review.py collect
```

默认输出到 `review/audit_review/<时间>/`：

- `queue.jsonl`：待审记录（含信号、优先级、建议去向）
- `report.md`：人读版
- `summary.json`：机器可读统计

可选：

```powershell
python tools\audit_review.py collect --limit 50 --min-priority medium
python tools\audit_review.py collect --no-positive   # 不把点赞记录放进候选
```

### 2. 人工审核

编辑 `queue.jsonl` 里的每条记录：

```json
{
  "decision": "accept",
  "destination": "corpus,negative",
  "reviewer": "组长",
  "notes": "睡眠建议太笼统，补成可执行步骤",
  "expected_risk": "R1",
  "expected_scenes": ["L3"],
  "corrected_reply": "先固定起床时间，睡前少喝浓茶……"
}
```

- `decision`：`accept` / `edit` / `reject` / `pending`
- `destination`：`skill` / `negative` / `corpus`，可逗号组合
- `expected_risk` / `expected_scenes`：审核后的正确标签
- `corrected_reply`：修正后的回复；不填则用原回复

### 3. 吸收已审核条目

```powershell
python tools\audit_review.py apply --queue review\audit_review\<时间>\queue.jsonl
```

默认写到同一目录：

- `corpus_candidates.jsonl`：可直接被语料工具读取的候选格式
- `negative_cases.jsonl`：待并入回归集的负例
- `skill_suggestions.md`：给 SKILL.md 的修改建议

先看效果可以加 `--dry-run`。

---

## 三、信号与优先级

| 信号 | 权重 | 含义 |
|---|--:|---|
| 用户点踩 | 5 | 唯一由人给出的质量信号 |
| 安全层报违规 | 5 | `violations` 非空 |
| 风险低估 | 4 | 模型定级低于关键词兜底 |
| 重复追问 | 3 | 老人没听懂或用不上，又问一遍 |
| 回复被兜底 | 3 | `fallback_used=1` |
| 回复为空/过短 | 3 | 用户收到空气泡 |
| 播报被打断 | 2 | 没听完就插话 |
| 无场景标签 | 2 | 场景识别缺失 |
| 延迟异常 | 1 | > 3× 中位延迟 |
| 用户点赞 | 1 | 可优先作为语料候选 |

优先级：总分 ≥7 high，4–6 medium，其余 low。

---

## 四、为什么不能自动“学会”

赞/踩只是线索，不是正确答案。自动把线索写进红线或语料，会把标注错误固化成规则。
因此流程是：

```text
测试对话 → 复盘队列 → 人工判断 → 候选文件 → 人工合并 → 回归门禁
```

回归门禁至少跑：

```powershell
python tools\run_all_gates.py --quick
python -m pytest backend/tests -q
```

通过后，才把 `corpus_candidates.jsonl` 合并进正式语料，或把 `negative_cases.jsonl`
并入回归集，或按 `skill_suggestions.md` 修改 SKILL.md。
