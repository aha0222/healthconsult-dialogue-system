# 质量闭环 · 工具与口径

**产出人**：成员 B  
**对应任务**：《小暖项目分工》成员 B 第 2、3、5、6 项

> 这几项是「自我改进闭环」的基础设施：**先能量准（⑥）、再能找出问题（③）、有评测集（②）、最后能一条命令验证没改坏（⑤）**。

---

## 一句话总览

| 任务 | 产物 | 一句话 |
|---|---|---|
| **⑥ SCENE 评测口径** | `docs/scene_eval_spec.md` + `tools/eval_scene_markers.py` | 把"场景标得准不准"变成可比较的数 |
| **③ 挖失败集** | `tools/mine_failure_cases.py` | 从审计日志里把可疑对话筛出来 |
| **② 说话人评测集** | `backend/tests/eval/speaker_cases.jsonl` + `tools/speaker_eval_probe.py` | 219 例角色 + 18 例安全不变式 |
| **④ 三路吸收** | `tools/adopt_failure_cases.py` + `review/failure_absorption/` | 失败集 → 扩词 / 改规则 / 加负例 |
| **⑤ 回归门禁** | `tools/run_all_gates.py` | 一条命令跑完该跑的所有检查 |

---

## ⑥ SCENE 评测口径

**为什么**：「场景标记准确率 74%」不说清怎么算就没意义——场景是多标签的
（1816 条里 847 条标 1 个、969 条标 2 个），四种算法能差十几个点。

**主指标**：**任一命中 any**（预测与标注有交集）  
**安全底线**：**逐场景召回**，尤其 E1 / M2 / N1 / N2 / N3 / N4 / M3

```powershell
python tools\eval_scene_markers.py --keyword          # 关键词兜底基线（秒出）
python tools\eval_scene_markers.py --pred preds.jsonl # 评测已有预测
python tools\eval_scene_markers.py --llm --limit 50   # 本地模型现跑
```

**已测基线（关键词兜底，非模型）**：

| 指标 | 值 |
|---|--:|
| 严格命中 exact | 24.6% |
| **任一命中 any** | **59.4%** |
| 主场景命中 first | 45.3% |
| 微平均 F1 | 45.5% |

安全场景召回：**M2 100%**、**N4 9%**、M3 41%、**N1 100%**、**N3 100%**、**E1 100%**、N2 100%。

> 这是 2026-10-18 关键词扩容后的复测值（扩容前：23.2% / 56.2% / 42.2% / 43.7%；
> M2 27%、N1 56%、N3 59%、E1 60%）。

> ⚠️ 这是**兜底路径**，不是模型路径，**不要和组长的 74% 直接比**。
> 详见 `docs/scene_eval_spec.md` 第六节。

---

## ③ 挖失败集

**只做工具，不做修改**（修是第 4 项的事）。

从 `backend/data/sessions.db` 的 `audit_log` 里按规则筛出可疑记录：

| 标记 | 严重度 | 含义 |
|---|---|---|
| `VIOLATION` | fatal | `violations` 非空 |
| `RISK_UNDERESTIMATE` | fatal | 关键词推断等级更高（**可能**漏判） |
| `FALLBACK` | warn | 回复被兜底话术替换 |
| `EMPTY_REPLY` | warn | 回复为空或 <10 字 |
| `NO_SCENES` | warn | 场景为空 |
| `LATENCY_OUTLIER` | warn | 延迟 > 中位数 3 倍 |
| `RISK_OVERESTIMATE` | info | 记录等级高于关键词推断（**可能**误报） |

```powershell
python tools\mine_failure_cases.py
python tools\mine_failure_cases.py --out failures.jsonl --min-severity warn
```

> **样本量**：B 交付时审计库只有 4 条；2026-10-18 复测已积累到 188 条，
> 工具可正常挖出失败样本（低于 20 条时仍会提示"样本量不足"）。

> ⚠️ `RISK_UNDERESTIMATE` / `OVERESTIMATE` 只是**线索**，不是定论——
> 关键词兜底与模型的判级都可能错，每条都要人工过。

---

## ② 说话人评测集

按 `docs/speaker_eval_design.md` 第 2 节的目标条数建成：

| 类别 | 条数 | 当前准确率 |
|---|--:|--:|
| 0 显式信号 | 67 | 100% |
| 1 引用转述 | 35 | 100% |
| 2a 无称呼可判 | 18 | 100% |
| 2b 无称呼且模糊 | 19 | 100% |
| 3 双人对话 | 28 | 100% |
| 4 方言口语 | 32 | 100% |
| 5 历史延续 | 20 | 100% |
| **角色类小计** | **219** | **100%** |
| 6 安全不变式 | 18 | 另计（接口层） |

**方向性主指标：误切 family 0 例，漏判 family 0 例。**

> B 交付时的基线是 89.6%（误切 6 / 漏判 11）；随后仓库里的 D1/D2/D3
> 三处修复（`a8533db`）把当时的 163 例全部判对。合并 B 的独立样例后新增
> 56 条角色样例，又暴露 6 个错判（4 误切 / 2 漏判），已补称谓与第一人称
> 状态句修复；当前 219 例全部判对。B 的原始基线文档未原样并入（数字已过时），
> 更新后的版本见 `docs/speaker_eval_baseline.md`。
>
> 注意：18 例安全不变式仍需 `/api/chat` 接口层验证，函数层测不到。

```powershell
python tools\speaker_eval_probe.py backend\tests\eval\speaker_cases.jsonl
python tools\speaker_eval_probe.py backend\tests\eval\speaker_cases.jsonl --json out.json
```

---

## ④ 失败集 → 三路吸收

**只出待审清单，不自动改安全配置。**

```powershell
python tools\adopt_failure_cases.py                                  # 从标签率实测结果
python tools\adopt_failure_cases.py --from-mine failures.jsonl       # 从审计日志失败集
```

| 轮次 | 数据源 | ① 扩关键词 | ② 改规则 | ③ 加负例 |
|---|---|--:|--:|--:|
| 第一轮（B 交付） | 离线 3B/CPU 档 177 条 | 0 | 19 | 19 |
| **第二轮（2026-10-19）** | **在线档 177 条** | **0** | **12** | **12** |

第二轮产出在 `review/failure_absorption_online/`：12 条全是**模型给了标签但等级偏低**，
关键词兜底 12/12 判对；另有 8 条模型完全没出标签，生产链路已由关键词兜底接住。
建议在 `orchestrator.py:378` 的 `max_risk` 里加入 `infer_risk_local(message)`，
在线档 177 条上可把漏判 **12 → 0**，代价是误抬 3 → 11。
**但这组数字只覆盖 177 条高风险基准**：低风险 R0/R1 池里关键词本身已误抬 237/1263（18.8%），
作为全局下限会把在线档原本判对的这些样本一起抬上去。启用前应先在全量输入上复测。

---

## ⑤ 回归门禁

```powershell
python tools\run_all_gates.py                   # 全部
python tools\run_all_gates.py --quick           # 跳过 pytest（秒级）
python tools\run_all_gates.py --strict-safety   # 关键词漏检超阈值即失败
python tools\run_all_gates.py --strict-tags     # 标签输出率低于阈值即失败
python tools\run_all_gates.py --strict-speaker  # 说话人验收线不过即失败
python tools\run_all_gates.py --with-scene preds.jsonl
```

**接入状态（5 项）：**

| 门禁 | 状态 | 结果 |
|---|---|---|
| 后端回归 | ✅ | B 交付时 348 passed, 2 skipped；合并后 398 passed |
| 红队用例 | ✅ | 56 条全部符合预期 |
| 关键词兜底漏检 | ✅ | **0/177 = 0%**（默认仅报告） |
| **标签输出率** | ✅ | 在线档 177 条安全基准：输出 **169/177 = 95%**，定级正确 **155/177 = 88%** |
| **说话人评测** | ✅ | 219 例 **100%**，误切 family 0 例 |
| 场景评测 | ⏸ | 可选（需预测文件）；关键词基线 any 59.4% |

> **标签率 ≠ 兜底漏检率**。前者问"模型输没输出 `[RISK:][SCENE:]`"（在线档 177 条为 95%，
> 离线 3B/CPU 档仅 36%），后者问"关键词兜底判低了没有"（扩容后 0%）。两个指标必须分开看。
> 数据档位以 `tag_rate.jsonl` 每条记录的 `tier` 字段为准。

> 合并时顺手修了一处既有测试不一致：`test_retention_default_is_forever` 原来拿
> 应用实例断言默认留存为 0，而 `.env` 显式配置了 `AUDIT_RETENTION_DAYS=180`。
> 现在拆成"未配置时默认 0"与"配置后按配置生效"两条测试，语义更准确。

**退出码**：0 = 硬门槛全过；1 = 有硬门槛未过。可直接接 CI。

---

## 这三项怎么串成一个闭环

```
        ┌──────────────────────────────┐
        │  ⑥ 量准（场景/风险的尺子）    │
        └──────────────┬───────────────┘
                       ↓
        ┌──────────────────────────────┐
        │  ③ 找出坏例子（审计日志）      │
        └──────────────┬───────────────┘
                       ↓
        ┌──────────────────────────────┐
        │  ④ 三路吸收（扩词/改提示/负例）│  ← 第一轮已出清单
        └──────────────┬───────────────┘
                       ↓
        ┌──────────────────────────────┐
        │  ⑤ 一条命令验证没改坏          │
        └──────────────┬───────────────┘
                       └──── 回到 ③ ────┘
```

④ 的**工具与第一轮清单已完成**（`docs/failure_absorption_round1.md`）：
用离线实测失败集跑通了三路分流（0 / 19 / 19）。
但"真正吸收"需要人审：关键词要审、SKILL.md 要组长改、负例要确认标注后再并入。
审计库已有 188 条记录，可以用 `--from-mine` 把真实用户失败接进同一套分流。

---

## 还没接上的

| 缺什么 | 谁 | 影响 |
|---|---|---|
| 类 6 的接口层验证 | 需 `/api/chat` 驱动 | 12 例安全不变式目前只列出，未实跑 |
| 标签率实测数据未入库 | 换机器/清日志就没了 | 门禁只能提示，不能判定标签率 |
| SKILL.md 的修改权 | 组长 | ④ 的第二路只能出建议，不能落盘 |
| 在线档的场景预测导出 | 组长 | 「74%」的口径仍未对齐 |

---

## 建议的下一步

1. **先对齐 74% 的口径**（`scene_eval_spec.md` 第六节）——不先对齐，后面所有"提升"都是空的
2. 把 ⑤ 接进日常提交流程（`--quick` 只要几秒）
3. 用现有 188 条审计日志跑 `mine_failure_cases.py --out failures.jsonl`，
   再跑 `adopt_failure_cases.py --from-mine failures.jsonl`，开始收真实用户的失败
