# 语料库交付 · 交接说明

> 这份是给**团队其他成员**看的。技术细节和完整数据见 [`corpus_build_report.md`](corpus_build_report.md)。
>
> 交付内容：498 条训练语料 + 569 条 Retriever 标注语料 ｜ 交付日期：2026-09-26

---

## 0. 发在群里的那段

> 语料部分我做完了，498 条训练语料 + 569 条 Retriever 语料，在 `语料库建设.zip` 里。
> 按 `_MANIFEST.md` 的「仓库路径」列归位。有几处需要相关同学确认的，我下面分人写清楚了。
> 先看 `README.md` 和这份交接说明，再看 `corpus_build_report.md`。

包内动到了 5 个原本属于其他模块的文件，见第 6 节。

---

## 1. → 负责分级 / 标签的同学

### 1.1 我改了 5 处既有样本的错误场景标签

`backend/tests/redline_cases.jsonl`：

| 样本 | 内容 | 原标签 | 改为 |
|---|---|---|---|
| `good_family_stroke` | 卒中 | `X1` 闲聊 | `E1` 急症识别 |
| `good_r2b_blackstool` | 黑便 | `X1` 闲聊 | `E1` 急症识别 |
| `good_r2b_fever` | 发热 | `X1` 闲聊 | `E1` 急症识别 |
| `good_r2_perception` | 看见人影 | `S2` 用药管理 | `S1` 症状咨询 |
| `bad_diag_aspirin` | 诊断开药 | `X1` 闲聊 | `S1` 症状咨询 |

**为什么要改**：这些错标会被 `build_scene_risk_corpus.py` 采信、静默进入 Retriever 语料。
而 `validate_sample` 用「声明场景 ∪ 检测场景」判定，**不会报错**，所以一直没被发现。

**需要你确认**：这跟你正在修订的新分级冲不冲突？冲突以你的版本为准，我这份单独提交。
若你改的是同一个文件，合并时注意这 5 条。

### 1.2 有 4 条「老人近期跌倒」的样本定了 R0，建议复核

| 样本 | 老人说 |
|---|---|
| `exp_L1_R0_b0_02` | 「我妈今年78，前阵子摔了一跤，腿脚不太利索了，我想问问平时给她做饭怎么搭配」 |
| `exp_L1_R0_b2_03` | 「我一个人住，前阵子摔了一跤，现在腿脚不太利索，下楼买菜费劲」 |
| `exp_L1_R0_b4_02` | 「我妈八十二了，牙口不好，我想问问每天吃多少肉合适，她前阵子还摔了一跤」 |
| `exp_L4_R0_b2_01` | 「前阵子把腿摔了一下，现在走路得拄拐，天天在家对着电视」 |

E1 的判定确实是关键词误报（这些不是急症）。但**定级本身值得复核**：独居、近期跌倒、遗留行动不便，
按多数老年医学跌倒风险指南应至少是 R1（提示风险、建议评估），而不是 R0（无需特殊处置）。
回复里也只提了「别站着炒菜」这类预防，没建议做跌倒评估或查骨密度。

**这是分级问题，不是语料生成问题，所以我没动这些标签。** 若你的新分级把「近期跌倒」纳入 R1 或更高，
这 4 条需要跟着调整。

另有一条 `exp_L2_R0_b3_01`：「我腿脚还成，就是走一会儿就喘」——**没跌倒**，但七十岁老人活动后气促
是心脏问题的常见信号，也定了 R0，同样建议复核。

### 1.3 扩展阶段的反例只覆盖 9 个场景

原计划用「pilot 驳回的样本」作扩展阶段的反例。但 pilot 轮 **41 条零驳回**，
`corpus_rejected.jsonl` 是空文件，反例机制没有输入。

临时改用 `negatives_from_redline.jsonl`（26 条，从既有 `redline_cases.jsonl` 抽取，覆盖 9 个场景，
每条附驳回原因如「诊断、开药」）。33 个格子里只有 9 个场景有对应反例，**其余格子没有负样本约束**。

---

## 2. → 负责 `safety_checker` / 质检的同学

### 2.1 `validate_outputs` 会报 10 条 fatal，全部已核实为误报

```
python tools/validate_outputs.py --input <语料> --mode generated_sft
→ {"total": 498, "fatal_count": 10, "pass": false}   退出码 1
```

9 条样本、10 个原因（`exp_L1_R0_b4_02` 同时命中两类）：

| 原因 | 次数 |
|---|--:|
| `emergency_scene_missing_escalation` | 6 |
| `medication_missing_doctor_or_pharmacist_confirmation` | 4 |

**成因**：`validate_sample` 用「声明场景 ∪ **检测**场景」判定必需动作，而 `detect_scenes` 是纯关键词匹配。

| 样本 | 触发词 | 被判为 | 实际情况 |
|---|---|---|---|
| `exp_L1_R0_b0_02` | 摔 | E1 急症 | 「前阵子摔了一跤」后问饮食搭配 |
| `exp_L1_R0_b0_06` | 药 | S2 用药 | 「除了吃药，吃的上面能帮我调调不」——明确问饮食 |
| `exp_L1_R0_b2_03` | 摔 | E1 急症 | 摔后腿脚不便，问省事吃法 |
| `exp_L1_R0_b4_02` | 摔 / 药 | E1 + S2 | 牙口不好，问每天吃多少肉 |
| `exp_L2_R0_b3_01` | 摔 | E1 急症 | **没摔**，是「怕摔了没人扶」的顾虑 |
| `exp_L4_R0_b2_01` | 摔 | E1 急症 | 摔后拄拐，问社区活动 |
| `exp_M2_R3_b0_01` | 药 | S2 用药 | **自杀意念**，回复给危机热线 + 120 |
| `exp_N3_R2b_b0_02` | 药 | S2 用药 | **冒充执法诈骗**，回复让打 110 |
| `exp_S2_R1_b2_04` | 中风 | E1 急症 | 中风后偷减药，回复让找开药医生 |

后两条最能说明问题：对**自杀危机**的回复要求「请与医生或药师确认用药」，对**诈骗案**要求同样的事，
都是把回复改坏的方向。要求最后一条打 120 也属过度升级。

### 2.2 我没改 `safety_checker.py`

属你的模块，我不越界改。**建议的修法**：`detect_scenes` 命中但**未在 `scenes` 里声明**时降级为 warning，
而不是要求该场景的必需动作；或直接改为「仅按声明场景」判定必需动作。

改动很小（`validate_sample` 第 417–431 行，约 4 行），我已验证影响面：

- `SCENE_REQUIRED_ACTIONS` 只覆盖 E1/M2/N1/N2/N3 + 单独的 S2
- `all_scenes` 在 431 行之后没有被使用
- **redline_cases 47 条里有 3 条受影响**（`bad_no_hospital`、`bad_open_door`、`bad_no_hospital2`），
  但它们各自还有别的真实 fatal（劝退就医话术、110/119 缺失），**不会因此翻转成 pass**
- 改完 `fatal_count` 从 10 降到 0，`pass: true`

### 2.3 顺带一处 docstring 与实现不符

`validate_sample` 的 docstring 写 `"generated_sft" — warning 视为 fatal`，
但 `validate_outputs.py:202` 实际是 `"pass": len(fatal) == 0`——**warning 并不会被提升为 fatal**。
docstring 过时了。

---

## 3. → 负责 Retriever / 检索的同学

### 3.1 `scene_risk_corpus.jsonl` 从空占位填成了 569 条

`backend/app/dialogue/corpus/scene_risk_corpus.jsonl` 原为 0 行占位，
分类器只能退回「关键词 + LLM 兜底」。现在 569 条：

```
corpus500×496  good_examples×6  paired_messages×1  redline_cases×45  risk_cases×21
```

496 条从训练语料**投影派生**，不重新打标——逐条核对与训练语料 **498/498 标签完全一致**
（498 条训练语料里有 2 条与既有语料的 user 文本重复，在 Retriever 侧被去重合并）。

### 3.2 我改了一处会静默毁数据的坑，请留意

这个文件是**生成产物**。原先 `build_scene_risk_corpus.py` 的 `SOURCES` 里**没有**训练语料，
只能靠 `--include` 手动传入——**任何人只要不带参数重跑一次，那 496 条会被静默冲掉且不报错**，
语料看着还在，检索质量却退回关键词兜底。

我把训练语料登记进了 `SOURCES` 末位，现在不带参数即可重建出同样的 569 条：

```powershell
python tools/build_scene_risk_corpus.py     # → 569 条
```

（新增来源一律**追加到末尾**，否则既有条目的 id 会整体错位。）

### 3.3 无测试覆盖这个文件

`backend/tests/` 下没有测试引用 `scene_risk_corpus`。若要做回归保护，
建议加一条断言「文件非空且标签与训练语料一致」。

---

## 4. → 负责 SKILL.md 的同学

语料每条 `messages[0]` 内嵌了 SKILL.md 全文（约 10.5k 字符）——这是**仓库既有约定**，
`clean_candidates.py` 和审核清单 A2 都这么规定。

**它是生成时刻的快照，不是运行时引用。** 运行时的 system prompt 由 `prompt.py` 每次实时读取当前 SKILL.md，
与语料内嵌副本相互独立。

**风险**：改了 SKILL.md 之后，语料里的旧副本**不会被任何机制发现**——CI 和测试都不比对二者，
训练数据会静默沿用旧规范。

**刷新方法**：`python tools/migrate_tags_v2.py --apply`（手动执行，无自动触发）。

当前状态：语料内嵌副本与 SKILL.md **逐字节相同，未过期**。

---

## 5. 需要所有人都知道的两点

### 5.1 初始语料只用了 Kimi，没用到 GPT

任务书写的是「利用 kimi、gpt 等模型生成高质量的初始语料」。实际 41 条初始语料
**全部由 `kimi-k3` 单模型生成**。扩展阶段用的是 `deepseek-chat`（与初始语料模型不同）。

若要补多模型交叉，换个 `GEN_*` 环境变量重跑 pilot 再合并去重即可：
`GEN_BASE_URL=https://api.moonshot.cn/v1`、`GEN_MODEL=kimi-k3`。

### 5.2 人工审核情况

「通过」的含义是**该条全部判据达标**。逐项判定完成度：

| 轮次 | 条数 | ★ 项逐项判定 | 仅记录结论 |
|---|--:|--:|--:|
| Phase 2 初始语料全审 | 41 | **38（93%）** | 0 |
| Phase 4 抽检 | 176 | 84（48%） | 91（52%） |

Phase 4 有 91 条只记录了结论「通过」未逐项展开，结论同样是全部判据达标。

两轮各有一处 ★ 项被判「有问题」（同为 `pilot_p_m2_r3_01_v2` 的 D3）。
按清单规则 **D 类 fail 记 warning 不否决**（只有 A/C 类一票否决），占比 2.4% 远低于 20% 的整批退回线，
且该条已人工改写定稿（222 字）并回灌进语料。

---

## 6. 提交：本包动到了 5 个属于其他模块的文件

按「每人只提交自己负责的部分」的约定，提交前请先确认这几个文件的归属：

| 文件 | 改动 | 必要性 |
|---|---|---|
| `tools/build_scene_risk_corpus.py` | 新增 `--include`/`--source-label`；把训练语料登记进 `SOURCES` | **必须** |
| `backend/app/dialogue/corpus/scene_risk_corpus.jsonl` | 空占位 → 569 条（生成产物） | **必须** |
| `backend/tests/redline_cases.jsonl` | 修正 5 处错误场景标签 | **建议单独提交** |
| `tools/validate_outputs.py` | 新增 `--mode corpus_llm`（交叉模型审核） | 可选 |
| `tools/clean_candidates.py` | 复用其定型逻辑，未改行为 | 可选 |

`redline_cases.jsonl` 与语料生成无关，修的是**既有数据的错误标签**，建议独立成一个提交。

---

## 7. 一句话索引

| 你想找 | 去哪看 |
|---|---|
| 全部结论、质检数据、已知问题逐条核实 | `文档/corpus_build_report.md` |
| 30 秒看懂交了什么、要你确认什么 | `README.md` |
| 每个文件回哪个仓库路径 | `_MANIFEST.md` |
| 审核判据（32 条） | `要求8-审核清单/corpus_review_checklist.md` |
| 逐条对照表（10 条 fatal、抽检分层） | `文档/corpus_build_report.md` 第 5、6.1 节 |
