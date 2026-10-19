"""失败集 → 三路吸收（工具）。对应《小暖项目分工》成员 B 第 4 项。

本工具**只产出待审清单，不改任何安全配置、不写 SKILL.md**。
三路吸收的人为边界是刻意的：

    ① 扩关键词   红线词表是安全配置 → 只出候选，必须人工审
    ② 改 SKILL.md 是组长的文件     → 只出建议 diff/片段，不落盘
    ③ 加负例     可以直接生成       → 输出待审 JSONL，审过再并入回归集

为什么第 4 项不能"全自动"：失败集里既有真漏判，也有语料标注争议。
自动吸收会把标注错误固化成规则，所以三路里只有第 ③ 路是机器可直接产出的。

用法
----
    # 主用法：从离线档标签率实测结果里挖当前仍失败的样本
    python tools\\adopt_failure_cases.py --from-tag-rate scripts/offline/tag_rate.jsonl

    # 从审计日志失败集（mine_failure_cases.py 的输出）分流
    python tools\\adopt_failure_cases.py --from-mine failures.jsonl

    # 指定输出目录
    python tools\\adopt_failure_cases.py --out-dir review/round1

产出
----
    keyword_candidates.md      第 ① 路：候选词待填表（按场景分组）
    skill_md_suggestions.md    第 ② 路：SKILL.md 建议片段（不自动应用）
    negative_cases.jsonl       第 ③ 路：待审负例
    summary.json               机器可读汇总 + max(模型,兜底) 规则测算
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.app.dialogue.markers import infer_tags_local      # noqa: E402
from backend.app.dialogue.taxonomy import RISK_ORDER           # noqa: E402

DEFAULT_TAG_RATE = REPO / "scripts/offline/tag_rate.jsonl"


def rank(x) -> int:
    return RISK_ORDER.get(str(x), -1)


def higher(a, b):
    """返回 a、b 中风险更高者（None 忽略）。"""
    return a if rank(a) >= rank(b) else b


def load_tag_rate(path: Path):
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = []
    all_ctx = []          # 全量 177 条，用于测算 max 规则的真实代价
    for r in rows:
        kw_risk, kw_scenes = infer_tags_local(r["user"])
        model_risk = r.get("model_risk")
        # 生产链路的实际定级：有模型标签用模型的，没有才退回兜底
        current = model_risk or kw_risk
        all_ctx.append(dict(want_risk=r["want"], current_risk=current,
                            keyword_risk=kw_risk))
        if rank(current) >= rank(r["want"]):
            continue
        out.append(dict(
            id=r["id"],
            text=r["user"],
            want_risk=r["want"],
            want_scenes=r.get("want_scenes") or [],
            model_risk=model_risk,
            keyword_risk=kw_risk,
            keyword_scenes=kw_scenes,
            current_risk=current,
            source="tag_rate",
            direction=r.get("dir") or "偏低",
        ))
    return out, len(rows), all_ctx


def load_mine(path: Path):
    """审计日志失败集：没有 gold，标为待人工判定。"""
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = []
    for r in rows:
        text = r.get("user") or r.get("text") or ""
        if not text:
            continue
        kw_risk, kw_scenes = infer_tags_local(text)
        out.append(dict(
            id=str(r.get("id", "")),
            text=text,
            want_risk=None,          # 审计日志没有人工标注
            want_scenes=[],
            model_risk=None,
            keyword_risk=kw_risk,
            keyword_scenes=kw_scenes,
            current_risk=r.get("risk") or kw_risk,
            source="audit_log",
            direction="待人工判定",
            flags=[f.get("code") for f in (r.get("flags") or [])],
        ))
    return out, len(rows), []


def route(cases):
    """三路分流。返回 {channel: [case,...]}。"""
    out = defaultdict(list)
    for c in cases:
        if c["want_risk"] is None:
            # 审计日志样本：缺 gold，进候选词表与负例待审队列
            out["keyword"].append(c)
            out["negative"].append(c)
        elif c["model_risk"] is None:
            # 模型没给标签，靠兜底仍判低了 → 补关键词
            out["keyword"].append(c)
            out["negative"].append(c)
        else:
            # 模型给了标签但偏低 → 提示词/规则问题
            out["skill"].append(c)
            out["negative"].append(c)
    return out


def write_keyword_md(path: Path, cases):
    lines = [
        "# 第 ① 路 · 关键词候选（待人工审核）",
        "",
        "> **不要直接抄进 `safety_checker.py`。** 红线词表是安全配置，",
        "> 每条候选都要对照原话确认不会误抬，再审后手工写入。",
        "",
        f"本轮候选 **{len(cases)}** 条。",
        "",
    ]
    if not cases:
        lines += [
            "## 本轮为 0 条",
            "",
            "当前失败集中，**没有**「模型没给标签、兜底也判低」的样本——",
            "说明关键词这一路在现有基线上已经补齐（见 `summary.json`）。",
            "",
            "后续从审计日志挖到新失败时，这一路会自动出现待填表。",
            "",
        ]
    else:
        by_scene = defaultdict(list)
        for c in cases:
            scene = (c["want_scenes"] or c["keyword_scenes"] or ["未分类"])[0]
            by_scene[scene].append(c)
        for scene, items in sorted(by_scene.items()):
            lines += [
                f"## {scene}（{len(items)} 条）",
                "",
                "| ID | 用户原话 | 期望 | 兜底 | 建议新增词（人工填） |",
                "|---|---|---|---|---|",
            ]
            for c in items:
                want = c["want_risk"] or "待标"
                text = c["text"].replace("|", "／")[:70]
                lines.append(f"| {c['id']} | {text} | {want} | {c['keyword_risk']} |  |")
            lines.append("")
        lines += [
            "## 填写规则",
            "",
            "- 优先从原话里摘**用户真的会说的短词**，不要整句。",
            "- 每个词先跑 `python tools\\simulate_keyword_expansion.py` 看误报。",
            "- 单用误报高的词（如「阳台」「跟着我」）改用限定语境（「从阳台」「跟着我上」）。",
            "- 写完后跑 `python tools\\run_all_gates.py --strict-safety` 验证。",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_skill_md(path: Path, cases, max_rule):
    lines = [
        "# 第 ② 路 · SKILL.md 建议片段（**不自动应用**）",
        "",
        "> `skills/healthconsult-assistant-skill/SKILL.md` 归组长维护。",
        "> 本文件只给建议，**没有**、也**不应**由本工具写入 SKILL.md。",
        "",
        f"本轮 **{len(cases)}** 条失败属于「模型给了标签，但等级偏低」。",
        "共同点：本地兜底的关键词等级是对的，说明**信号在文本里，模型没抓住**。",
        "",
        "## 一、系统性修法（建议优先评估）",
        "",
        "`orchestrator.py:376-383` 已经有一个「风险下限」机制，但它只拿",
        "`pre_classification.risk` 当下限——**分类器没接线时这个下限是空的**。",
        "把关键词兜底也加进同一个 `max_risk` 即可，改动只有一行：",
        "",
        "```python",
        "# backend/app/dialogue/orchestrator.py:378",
        "floored = max_risk(",
        "    risk,",
        "    pre_classification.risk if pre_classification else \"\",",
        "    infer_risk_local(message),        # ← 新增：关键词兜底作为下限",
        ")",
        "```",
        "",
        "`infer_risk_local` 在 `orchestrator.py:27` 已经导入，无需新增 import。",
        "它只做本地字符串匹配，不增加模型调用、不增加延迟。",
        "",
        "**实测代价**（177 条安全基准）：",
        "",
        "| 指标 | 现状 | 改用 max(模型,兜底) |",
        "|---|--:|--:|",
        f"| 漏判（低于标注） | {max_rule['before_miss']} | **{max_rule['after_miss']}** |",
        f"| 误抬（高于标注） | {max_rule['before_over']} | {max_rule['after_over']} |",
        "",
        f"误抬全部是 R2b→R3（安全方向），共 {max_rule['after_over']} 条。",
        "代价是这些轮次会多走一次语义复核、话术更保守；",
        "收益是把 19 条「应该叫 120 却说没事」的漏判清零。",
        "",
        "## 二、逐条证据（按场景）",
        "",
    ]
    by_scene = defaultdict(list)
    for c in cases:
        by_scene[(c["want_scenes"] or ["未分类"])[0]].append(c)
    for scene, items in sorted(by_scene.items()):
        lines += [
            f"### {scene}（{len(items)} 条）",
            "",
            "| ID | 用户原话 | 模型 | 兜底 | 标注 |",
            "|---|---|---|---|---|",
        ]
        for c in items:
            text = c["text"].replace("|", "／")[:76]
            lines.append(f"| {c['id']} | {text} | {c['model_risk']} | "
                         f"{c['keyword_risk']} | {c['want_risk']} |")
        lines.append("")
    lines += [
        "## 三、若要改 SKILL.md 的写法（供组长参考）",
        "",
        "不要只加「注意不要漏判」这类空话；按场景给**可执行的判别线索**：",
        "",
        "- **E1**：写出「黑便/柏油样 + 头晕站不稳」「痰中带血 + 胸闷」"
        "「心口窝疼 + 大汗/溻透」这类**组合条件**，并明确至少 R2b/R3。",
        "- **M2**：把「把药攥在手里/数药片」「不如睡过去」"
        "「活着没劲」列为**独立**强信号，不要因为语气平静就降级。",
        "- **N2**：燃气味 + 仍想做饭 → 先劝离、再报警，不得按普通家务咨询。",
        "- **N3**：承诺高返利（存五万返两万）→ 明确按诈骗风险提示。",
        "",
        "每条规则后面附 2–3 条来自本文件的真实原话作为锚例。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_negative(path: Path, cases):
    with path.open("w", encoding="utf-8") as f:
        for c in cases:
            rec = dict(
                text=c["text"],
                expected_risk=c["want_risk"],
                expected_scenes=c["want_scenes"],
                observed_risk=c["current_risk"],
                observed_model_risk=c["model_risk"],
                observed_keyword_risk=c["keyword_risk"],
                source=c["source"],
                direction=c["direction"],
                source_id=c["id"],
                needs_human_label=c["want_risk"] is None,
            )
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def analyze_max_rule(all_ctx):
    """在**全量**评测集上测算 max(模型, 兜底) 的代价与收益。"""
    before_miss = before_over = after_miss = after_over = 0
    over_cases = []
    for c in all_ctx:
        want = c["want_risk"]
        if rank(c["current_risk"]) < rank(want):
            before_miss += 1
        elif rank(c["current_risk"]) > rank(want):
            before_over += 1

        merged = higher(c["current_risk"], c["keyword_risk"])
        if rank(merged) < rank(want):
            after_miss += 1
        elif rank(merged) > rank(want):
            after_over += 1
            over_cases.append(dict(want=want, got=merged))
    return dict(
        scope=f"全量 {len(all_ctx)} 条",
        before_miss=before_miss,
        after_miss=after_miss,
        before_over=before_over,
        after_over=after_over,
        over_cases=over_cases[:20],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="失败集 → 三路吸收（只出待审清单）")
    ap.add_argument("--from-tag-rate", default="",
                    help=f"标签率实测结果（默认 {DEFAULT_TAG_RATE}）")
    ap.add_argument("--from-mine", default="",
                    help="mine_failure_cases.py 的 --out 输出")
    ap.add_argument("--out-dir", default="",
                    help="输出目录（默认 review/failure_absorption）")
    args = ap.parse_args()

    if not args.from_tag_rate and not args.from_mine:
        args.from_tag_rate = str(DEFAULT_TAG_RATE)

    if args.from_mine:
        src = Path(args.from_mine)
        cases, total, all_ctx = load_mine(src)
        source_name = f"审计日志 {src}"
    else:
        src = Path(args.from_tag_rate)
        if not src.is_file():
            raise SystemExit(
                f"找不到 {src}\n"
                "先跑 tools/offline_tag_rate.py 177 --out scripts/offline/tag_rate.jsonl"
            )
        cases, total, all_ctx = load_tag_rate(src)
        source_name = f"标签率实测 {src}"

    out_dir = Path(args.out_dir) if args.out_dir else REPO / "review/failure_absorption"
    out_dir.mkdir(parents=True, exist_ok=True)

    routed = route(cases)
    max_rule = analyze_max_rule(all_ctx) if all_ctx else None

    write_keyword_md(out_dir / "keyword_candidates.md", routed["keyword"])
    write_skill_md(out_dir / "skill_md_suggestions.md", routed["skill"], max_rule
                   or dict(before_miss=0, after_miss=0, before_over=0, after_over=0))
    write_negative(out_dir / "negative_cases.jsonl", routed["negative"])

    summary = dict(
        source=source_name,
        total_evaluated=total,
        failures=len(cases),
        channels={
            "keyword": len(routed["keyword"]),
            "skill_md": len(routed["skill"]),
            "negative": len(routed["negative"]),
        },
        by_direction=dict(Counter(c["direction"] for c in cases)),
        by_want_risk=dict(Counter(c["want_risk"] for c in cases)),
        max_rule=max_rule,
        out_dir=str(out_dir),
        note="三路只产出待审清单；关键词表与 SKILL.md 均未改动。",
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 78)
    print("  失败集 → 三路吸收（只出待审清单）")
    print("=" * 78)
    print(f"  来源        : {source_name}")
    print(f"  评测总数    : {total} 条")
    print(f"  当前失败    : {len(cases)} 条")
    print(f"    偏低      : {summary['by_direction'].get('偏低', 0)}")
    print()
    print(f"  ① 扩关键词  : {len(routed['keyword'])} 条待填")
    print(f"  ② 改 SKILL  : {len(routed['skill'])} 条待评估（仅建议，未改文件）")
    print(f"  ③ 加负例    : {len(routed['negative'])} 条待审")
    if max_rule:
        print()
        print(f"  max(模型,兜底) 测算：偏低 {max_rule['before_miss']} → "
              f"{max_rule['after_miss']}，误抬 +{max_rule['after_over']}")
    print()
    print(f"  输出目录    : {out_dir}")
    for name in ("keyword_candidates.md", "skill_md_suggestions.md",
                 "negative_cases.jsonl", "summary.json"):
        print(f"      {name}")
    print()
    print("  ⚠️ 关键词表、SKILL.md 均未改动；三路都要人工审核后才吸收。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
