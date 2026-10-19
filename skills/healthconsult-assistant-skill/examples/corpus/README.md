# 语料库（数据卡片）

> 本目录是小暖的对话语料。字段、值域、生成方式、校验与查看命令都在这份卡片里。
>
> **一句话结论**：对话用 OpenAI / HuggingFace 通行的 `messages` 三角色格式；
> 标注（风险等级、场景）放在**行级、与 `messages` 平级**——这是对话数据集的社区主流做法，
> 结构不需要改。缺的一直是这份说明。

## 1. 文件清单

| 文件 | 条数 | 谁在用 | 说明 |
|------|-----:|--------|------|
| `v0.4.0_corpus_expanded.jsonl` | 1725 | **运行时检索**（`EXEMPLAR_CORPUS_PATH` 的默认值）、多个工具 | 主训练语料，24 个场景 / 全部风险档 |
| `v0.3.0_corpus500.jsonl` | 498 | 语料回归测试、快照刷新脚本 | 上一版语料，发布物之一 |
| `v0.4.0_seed_anchors.jsonl` | 16 | `finalize_corpus_v2.py` 的输入 | 手工锚点种子 |
| `pilot_seeds.jsonl` | 41 | 生成脚本 | 早期种子 |
| `negatives_all_cells.jsonl` / `negatives_from_redline.jsonl` | 47 / 26 | 语料工具 | 按格子/按红线挖的负例 |
| `corpus_targets.json` | — | `finalize_corpus_v2.py`、`build_cell_negatives.py` | 目标分布矩阵（场景×风险格子），不是记录 schema |
| `*_bad_cases.csv`（3 个） | — | — | `tools/validate_outputs.py` **生成**的质检报告，按 `.gitignore` 不入库 |
| `pilot_reviewed/corpus_approved.jsonl` | — | 审核产物 | 人工审核通过的子集 |
| `example_sample.json` | 3 | **人看** | 本卡片配套的缩进美化样例，从真实行导出 |

## 2. 字段

两个主语料文件（`v0.3.0` / `v0.4.0`）的键集完全一致，恒 6 个必填 + 2 个稀疏字段：

| 字段 | 类型 | 值与来源 | 谁在读 |
|------|------|---------|--------|
| `sample_id` | str | 生成脚本写入，全局唯一 | 检索去重/缓存键、多个工具 |
| `category` | str | 中文场景名（`v0.4.0` 有 24 种） | **仅生成阶段**读；运行时无人读，但别删（生成脚本依赖） |
| `speaker_type` | str | `elder_self`（老人本人）/ `family_caregiver`（家属） | **仅生成阶段**读，用于提示口吻 |
| `risk_level` | str | 值域 = `taxonomy.RISK_LEVELS`：`R3` / `R2b` / `R2a` / `R1` / `R0` | 检索重排加分（`reranker._label_boost`） |
| `scenes` | list[str] | 值域 = `taxonomy.SCENES`（24 个，如 `E1` 急症、`L1` 生活照护、`M2` 心理） | 同上 |
| `messages` | list | **恒 3 条**，角色依次 `system` → `user` → `assistant` | `user`/`assistant` 被检索与注入消费；**`system` 运行时不读**（见 §5） |
| `human_edited` | bool | **稀疏**：`v0.3.0` 8 条 / `v0.4.0` 6 条 | 无读取方，人工改稿的痕迹 |
| `renamed_from` | str | **稀疏**：各 8 条 | 无读取方，去重改 id 的溯源 |

**标注是行级的**：`risk_level` / `scenes` 与 `messages` 平级，不塞进消息里——因为一条对话只有一个风险结论。
（若将来要挂多标注者或置信度，按惯例加 `annotations: {...}` 子对象，而不是继续往顶层堆散键。）

**正文尾部的 `[RISK:x]` / `[SCENE:x]` 是渲染产物**：结构化字段是唯一事实源，标记由
SKILL.md 约定让模型输出、后端解析。一致性由 `safety_checker` 的校验函数负责。

### 实测分布

| | `v0.3.0_corpus500`（498） | `v0.4.0_corpus_expanded`（1725） |
|---|---|---|
| R0 / R1 | 164 / 178 | 534 / 692 |
| R2a / R2b / R3 | 80 / 37 / 39 | 292 / 121 / 86 |
| `elder_self` / `family_caregiver` | — | 1347 / 378 |

## 3. 一条样本

完整可读样例见同目录 [`example_sample.json`](example_sample.json)（3 条，含一条急症、一条家属口吻）。
结构就是：

```json
{
  "sample_id": "exp_E1_R3_b0_01",
  "category": "急症识别",
  "speaker_type": "elder_self",
  "risk_level": "R3",
  "scenes": ["E1"],
  "messages": [
    { "role": "system",    "content": "<SKILL.md 快照原文，11,353 字，此处省略>" },
    { "role": "user",      "content": "我这会儿左边胳膊突然抬不起来了，说话也有点大舌头，是不是压着神经了，揉揉能好不" },
    { "role": "assistant", "content": "您听我说，先别揉，也别等。……[RISK:R3]\n[SCENE:E1]" }
  ]
}
```

## 4. 内嵌 SKILL.md 快照（重要，别当冗余删掉）

`messages[0]` 的 system 是**生成时刻的 SKILL.md 全文**（11,353 字），每条都带一份。

- **为什么**：防"规范改了、训练数据静默过期"——这批语料是在哪个版本的规范下生成的，可查。
- **谁守着**：`backend/tests/test_corpus.py::test_embedded_system_matches_current_skill`
  （断言内嵌 system 以当前 SKILL.md 为前缀；SKILL.md 一改，它就红，提醒你刷新快照）。
- **怎么刷新**：`python tools/refresh_skill_snapshot.py`
- **⚠️ 运行时不会把它发给模型两次**：`retriever.load_corpus` 只取 `user` / `assistant`，
  那条 `system` 在加载时就被丢弃；注入给模型的相似样例块实测 **781 字**（3 条，只有「老人说／小暖答」），
  不含任何 skill 正文。检索索引也只嵌 `user` 字段。
  所以它只付出**文件体积**（约 20MB 重复文本）与一次性解析开销，**每轮对话的 token 成本为 0 额外**。

## 5. 生成与维护脚本

| 脚本 | 作用 | 产出/改动 |
|------|------|----------|
| `tools/generate_corpus.py` | 批量生成候选 | `corpus/{stage}_candidates_*.jsonl` |
| `tools/build_seed_anchors.py` | 造手工锚点 | `v0.4.0_seed_anchors.jsonl` |
| `tools/finalize_corpus_v2.py` | 去重、裁到目标分布 | **`v0.4.0_corpus_expanded.jsonl`**（透传字段，不改结构） |
| `tools/apply_review_edits.py` | 人工改稿落库 | `v0.3.0_corpus500.jsonl`（置 `human_edited`） |
| `tools/restore_false_rejects.py` | 恢复被误剔的条目 | `v0.3.0_corpus500.jsonl` |
| `tools/fix_fall_risk_labels.py` | 修风险标签 | **`v0.4.0_corpus_expanded.jsonl`**（原地） |
| `tools/refresh_skill_snapshot.py` | 刷新内嵌 SKILL.md 快照 | `v0.3.0_corpus500.jsonl`（原地） |
| `tools/migrate_tags_v2.py` | 标签规范化迁移 | `examples/*.jsonl`（**非递归，不含本目录**） |
| `tools/validate_outputs.py` | 批量校验 | `*_bad_cases.csv`（生成物，不入库） |
| `tools/build_scene_risk_corpus.py` | 投影成扁平检索语料 | `backend/app/dialogue/corpus/scene_risk_corpus.jsonl` |

**没有共享的 schema 定义**：各脚本各自拼 dict，字段顺序靠约定而非约束（所有读取方都按名取值，不依赖顺序）。

## 6. 来源与许可

- **自建语料**：模型生成 → 人工全审/抽检 → 扩写定容。方法与判据见
  [`docs/corpus_build_report.md`](../../../../docs/corpus_build_report.md)、
  [`docs/corpus_review_checklist.md`](../../../../docs/corpus_review_checklist.md)。
- **不含任何真实老人数据**，无真实个人健康信息。
- 许可随仓库（MIT）。版本命名 `v<major>.<minor>.<patch>_<用途>.jsonl`；中间产物不带版本前缀。

## 7. 常用查看命令（人读入口）

JSONL 每行紧凑、机器友好但难读。日常看数据用 `jq`（没有 `jq` 时用 `python -m json.tool`）：

```bash
C=skills/healthconsult-assistant-skill/examples/corpus/v0.4.0_corpus_expanded.jsonl

head -1 "$C" | jq .                          # 美化看第一条
jq -c 'select(.risk_level=="R3")' "$C" | head -3          # 只看最高风险
jq -c 'select(.scenes|index("E1"))' "$C" | head -3        # 只看急症场景
jq -c 'select(.speaker_type=="family_caregiver")' "$C" | head -3   # 只看家属提问
jq -c 'select(.messages[1].content|test("大便发黑"))' "$C"         # 按用户原话搜
jq -r '.risk_level' "$C" | sort | uniq -c                 # 风险分布
jq -r '.scenes[]' "$C" | sort | uniq -c                   # 场景分布
jq -r '[.sample_id,.risk_level,(.scenes|join(",")),.messages[1].content]|@tsv' "$C" | head -20  # 一览表
```

要给人看一份缩进样例，直接用 [`example_sample.json`](example_sample.json)（由真实行导出，不会漂移）。

## 8. 已知不一致（待统一）

运行时**检索**语料是另一份文件：`backend/app/dialogue/corpus/scene_risk_corpus.jsonl`，
扁平格式 `id / user / risk / scenes / keywords / source`（无 `messages`）。
它的键名是 **`risk`**，而本目录是 **`risk_level`**——目前靠 `retriever.load_corpus` 的别名容错兜着。
统一命名属后续工作（要同时改数据文件与相关工具）。
