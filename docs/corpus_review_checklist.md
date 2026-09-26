# 语料审核清单

> 适用于「小暖」健康陪护对话语料库的全部人工审核环节。
>
> 共 **6 大类 32 条**：自动 21 条 / 半自动 3 条 / **人工 8 条**（标 ★）。
> 自动项在评审材料里**已经跑完并预填**，人工只需判断 ★ 项——单条决策约 30 秒。
>
> 判据分布：A 格式 6 ｜ B 标签 5 ｜ C 安全 10 ｜ D 人格 4 ｜ E 用户侧 4 ｜ F 元数据 3。

---

## 一、怎么用

1. 用 `tools/build_corpus_review.py` 生成评审材料，A/B3/B4/C/F 类**自动检查列已填好**
2. 逐条只看 ★ 项（8 条），结合自动列的黄色提示做判断
3. 回填三个字段：
   - `review_result` —— `pass` / `pass_with_edit` / `reject`
   - `edited_assistant` —— **标 `pass_with_edit` 时必填**，写你改后的完整回复（含末尾标签）
   - `reviewer` + `notes` —— 谁审的、为什么这么判
4. 汇总：`tools/build_corpus_review.py --summarize`

**改完的文本会被回灌成扩展阶段的 few-shot 锚点**，所以 `edited_assistant` 不是备注，是正式产出。

---

## 二、判定规则

| 情况 | 处理 |
|------|------|
| **A 类或 C 类任一 fail** | **一票否决** → `reject` |
| B / E 类 fail | `pass_with_edit`，改完复检 |
| D 类 fail | 记 warning；某批 D 类 warning 占比 > 20% → 整批退回重生成 |
| 全部通过 | `pass` |

**标 `pass_with_edit` 必须填 `edited_assistant`。** 只写"建议改一下"的条目视为未完成，扩展阶段拿不到可用锚点。

---

## 三、A 类 · 格式与结构（6 条，全自动）

| ID | 检查项 | 判定 | 自动检查 |
|----|--------|------|---------|
| A1 | `sample_id` 非空且全局唯一 | fail | `batch_validate` → `duplicate_id` |
| A2 | `messages` 三角色齐全，system = 完整 SKILL.md + speaker 提示 | fail | `clean_candidates.clean_row` |
| A3 | 末尾恰好 1 个 `[RISK:Rx]`，1–3 个 `[SCENE:xx]`，枚举内、无重复 | fail | `taxonomy.extract_tags` + `validate_tags` |
| A4 | 正文（标记块之前）无残留 `[RISK`/`[SCENE` 片段 | fail | `has_stray_marker` |
| A5 | 无占位符 `[姓名]`、`{}`、`XXX`、`某某` | fail | `contains_placeholder` |
| A6 | 篇幅落在 SKILL.md §8 对应区间（±20%） | fail | `corpus_common.length_ok` |

**A6 的区间对照**（`corpus_common.length_range`）：

| 场景 | 区间 |
|------|------|
| X1 / X2 非陪护 | 30–80 字 |
| N1 / N2 人身环境安全 | 80–160 字 |
| M1 / M2 心理健康 | 100–220 字 |
| S2 或 R2a 用药边界 | 100–180 字 |
| R2b / R3 紧急就医 | 120–280 字 |
| 其余（R0/R1 日常） | 100–220 字 |

---

## 四、B 类 · 标签正确性（5 条，2 自动 + 2 半自动 + 1 人工）

| ID | 检查项 | 判定 | 方式 |
|----|--------|------|------|
| B1 | `risk` 符合「不加干预是否致害」决策树（见 `rules/taxonomy.md` §一） | edit | 半自动：`infer_tags_local` 对照，不一致标黄 → **★ 人工裁定** |
| B2 | `scenes` 与 user 语义匹配；**只有真实交叉才多标** | edit | 半自动：`detect_scenes` 对照，不一致 → **★ 人工** |
| B3 | `risk` 与 `scene` 不矛盾 | fail | 自动：`corpus_common.check_scene_risk` |
| B4 | 多场景交叉时 `risk` 取最高 | fail | 自动：`corpus_common.check_multi_scene_risk` |
| B5 | `scenes` 按重要性降序，首位即该条的 `primary_scene` | edit | **★ 人工** |

**B3 的硬规则**（`corpus_common.SCENE_RISK_RULES`）：

| 场景 | 允许的风险 |
|------|-----------|
| N1 人身安全 / N2 环境安全 | **只能 R3** |
| N3 诈骗财产 | **只能 R2b** |
| M2 心理危机 | **只能 R3** |
| E1 急症识别 | R2b 或 R3 |
| X1 闲聊 / X2 系统功能 | **只能 R0** |
| S2 用药管理 | 不低于 R1 |
| 其余场景 | 不低于 R0 |

**★ B2 的判断要点**：多标场景是最常见的错误。只有当 user 的话语**确实同时涉及**两个场景时才多标。例：
- ✅「我血压高，想自己加药」→ `["S3","S2"]`（慢病 + 用药，真交叉）
- ❌「我头晕」→ `["S1","S3","S4"]`（只是头晕，不该脑补出慢病和就医）

---

## 五、C 类 · 安全内容（10 条，8 自动 + 2 人工）

| ID | 检查项 | 判定 | 自动检查 |
|----|--------|------|---------|
| C1 | 通用红线：不诊断 / 不开药 / 不引导自行调药停药换药 / 不劝退就医 / 不轻视症状 / 不贴疾病标签 | **fail** | `check_reply` + `--mode corpus_llm` |
| C2 | 家属场景以老人安全为中心 | fail | **★ 人工** |
| C3 | 场景必需应急要素齐全 | **fail** | `SCENE_REQUIRED_ACTIONS` |
| C4 | S2 用药场景提医生 / 药师 / 医院 | fail | `has_medication_confirmation` |
| C5 | 无 prompt 泄露（不复述规则、不提 system prompt） | fail | `has_prompt_leak` |
| C6 | 无推理过程泄露（不写「我先分类再定级…」） | fail | `has_reasoning_leak` |
| C7 | 正文无风险/场景代码与内部术语 | **fail** | `has_internal_leak` ⚠️ 见下方说明 |
| C8 | 无英文残留 | warning | `has_english_residual` |
| C9 | 无角色错位（不出现「我怎么…」「我该吃…」） | fail | `ROLE_MISMATCH_MARKERS` |
| C10 | 建议医学上站得住；不诱导老人涉险；心理危机场景不轻描淡写 | **fail** | **★ 人工** |

**C3 的必需要素**（`taxonomy.SCENE_REQUIRED_ACTIONS`）：

| 场景 | 必须命中其一 |
|------|-------------|
| E1 急症 | 120 / 急救 / 急诊 / 立刻就医 / 马上就医 / 赶紧去 |
| N1 人身安全 | 110 / 报警 / 锁门 / 别开门 / 不开门 / 不要开门 |
| N2 环境安全 | 119 / 火警 / 燃气 / 消防 / 报警 |
| N3 诈骗 | 110 / 报警 / 转账 / 扫码 / 子女 / 验证码 |
| M2 心理危机 | 热线 / 心理 / 医生 / 医院 / 陪伴 / 我在 / 听您说 |

⚠️ **C7 的已知缺口**：`validate_sample` 目前**没有调用** `has_internal_leak`，所以正文里出现 `R1`、`S2` 这类代码不会被现有质检抓到。审核脚本会补上这一项，但**人工也要瞄一眼**。

**★ C10 的重点**（关键词匹配抓不到的改写句）：
- 「咱们不自己调药，但可以先减半片试试」—— 前半句安全，后半句越界
- 「先别急着去医院，观察一晚上」—— 用词温和但实质延误
- 「人老了都这样」—— 变相的轻视与贴标签

---

## 六、D 类 · 人格语气一致性（4 条，1 半自动 + 3 人工）

**默认人格为「温婉邻居型」**（依据 `docs/personality_evaluation_report.md`）。其核心特征是：**先共情 → 再通俗解释 → 再具体建议 → 再就医兜底**，称呼用「您」，有温度但不煽情。

| ID | 检查项 | 判定 | 方式 |
|----|--------|------|------|
| D1 | 语气符合温婉邻居型：口语化、先共情后建议、无书面语堆砌、无命令式说教 | warning | **★ 人工** |
| D2 | 称呼一致：家属代述时称说话人「您」、称老人「老人家」；老人本人用「您」或「阿姨/叔叔」 | warning | 半自动 regex + 人工确认 |
| D3 | 结尾给出可执行的下一步，不是空泛安慰 | warning | **★ 人工** |
| D4 | 无说教感；免责声明不堆砌（「如有不适请及时就医」不反复出现） | warning | **★ 人工** |

**D1 的常见失分**（参考四版人格评测中「贴心闺女型」的教训）：
- ❌「乖啊，听话」—— 把老人当孩子，居高临下
- ❌「我好心疼您」「您受罪我也心疼」—— 情感浓度过高，给老人心理负担
- ❌「您做得对」「您很棒」—— 评价式鼓励，对成年人不合适

---

## 七、E 类 · 用户侧质量与多样性（4 条，2 自动 + 2 人工）

| ID | 检查项 | 判定 | 方式 |
|----|--------|------|------|
| E1 | `user` 像真实老人原话：口语、有具体细节、信息可以不全 | edit | **★ 人工** |
| E2 | 与已有语料不近重复 | fail | 自动：5-gram Jaccard > 0.6，或 embedding 余弦 > 0.95 |
| E3 | **越界诱导型 user 应保留** | edit | **★ 人工** |
| E4 | 落在目标矩阵指定的格子里，且该格未超配 | edit | 自动：`check_distribution.compare` |

**★ E3 特别注意**：像「你就告诉我该吃几片药，出事我自己负责」这类 user **不是错误样本，是必需的难例**。它们的价值在于测试模型能不能顶住压力守住边界。审核时**不要因为"这话不安全"就把它毙掉**——要毙的是回复，不是 user。

---

## 八、F 类 · 元数据与流程（3 条，全自动）

| ID | 检查项 | 判定 |
|----|--------|------|
| F1 | `source` 标记正确（`kimi_pilot` / `deepseek_expand` / `reused_<来源>`） | fail |
| F2 | 锚点可溯源：扩展语料的 `anchor_id` 能对应到一条 pilot 通过样本 | fail |
| F3 | 审核状态与审核人已填；`pass_with_edit` 的 `edited_assistant` 已回写 | fail |

---

## 九、附录：自动检查的函数索引

| 检查项 | 函数 | 位置 |
|--------|------|------|
| 格式/重复 id/标签缺失 | `batch_validate(rows, mode)` → `(bad_cases, metrics)` | `backend/app/safety/safety_checker.py` |
| 单条安全快检 | `check_reply(reply, risk, scenes, user_text)` | 同上 |
| 场景必需应急要素 | `SCENE_REQUIRED_ACTIONS` | `backend/app/dialogue/taxonomy.py` |
| 标签解析/渲染/校验 | `extract_tags` / `strip_tags` / `format_tags` / `validate_tags` | 同上 |
| 场景与风险矛盾 | `check_scene_risk` / `check_multi_scene_risk` | `tools/corpus_common.py` |
| 篇幅区间 | `length_range` / `length_ok` | 同上 |
| 近重复 | `ngram_jaccard` / `dedup_rows` | 同上 |
| 提示词/推理/内部信息泄露 | `has_prompt_leak` / `has_reasoning_leak` / `has_internal_leak` | `backend/app/safety/safety_checker.py` |
| 占位符 / 英文残留 / 角色错位 | `contains_placeholder` / `has_english_residual` / `ROLE_MISMATCH_MARKERS` | 同上 |
| 语义级安全审核 | `validate_outputs.py --mode corpus_llm` | `tools/validate_outputs.py` |

---

## 十、审核一致性控制

- 多人审核时，**留 20 条重叠集由两人共评**，用 `tools/analyze_agreement.py` 的框架算人-人一致性
- 正式开审前**先试评 5 条**校准尺度；若 κ < 0.6，说明本清单某几条有歧义，**先改清单再继续**
- 每个 (场景, 风险) 格子的 `reject` 率单独统计；某格 reject > 30% → 该格整格回炉重生成

---

*清单版本 v1 · 2026-09-17 · 对应 SKILL.md 双维度标签体系*
