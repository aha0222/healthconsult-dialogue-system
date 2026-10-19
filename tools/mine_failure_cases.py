"""从审计日志里挖「失败集」——自我改进闭环的第一步。

对应任务：《小暖项目分工》成员 B 第 3 项 ——「从审计日志里挖失败集（**只做工具**）」。

这一项**只负责找出来，不负责修**。修是第 4 项（失败集 → 三路吸收）。
分开的理由：先看清"坏在哪、坏多少"，再谈改什么，否则改起来没有靶子。

审计库在哪
----------
    backend/data/sessions.db  →  audit_log 表

    字段：id, session_id, personality, user_message, reply, risk,
          violations, fallback_used, model, latency_ms, created_at, scenes

判定规则
--------
每条记录会被打上若干标记（一个条可以同时有多个），带严重度：

    fatal  VIOLATION           violations 非空（安全层报过错）
    fatal  RISK_UNDERESTIMATE  关键词推断的等级**高于**记录的等级
                               → 可能是漏判（记录偏低），也可能关键词过保守，需人工看
    warn   FALLBACK            fallback_used=1（回复被兜底话术替换过）
    warn   EMPTY_REPLY         回复为空或过短（< 10 字）
    warn   NO_SCENES           scenes 为空（场景没判出来）
    warn   LATENCY_OUTLIER     延迟 > 中位数 3 倍（可能是冷启动，也可能是卡住）
    info   RISK_OVERESTIMATE   记录的等级高于关键词推断（可能误报，不一定是错）

**重要**：RISK_UNDERESTIMATE / OVERESTIMATE 都只是**线索**，不是定论。
关键词兜底本身也会误判（177 条高风险基准的漏检已清零，但低风险池仍有误抬），
它说"应该更高"不代表真的错；反之亦然。每一条都需要人工过。

用法
----
    python tools\\mine_failure_cases.py
    python tools\\mine_failure_cases.py --out failures.jsonl
    python tools\\mine_failure_cases.py --min-severity warn     # 只看 warn 及以上
    python tools\\mine_failure_cases.py --db <其他 sessions.db>

注意
----
    **样本量可能很小。** 审计库只有真实跑过的对话，本机当前只有 4 条。
    工具会如实报告样本量；数据不够时，结论应当写"样本不足"，不要硬下判断。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEFAULT_DB = REPO / "backend/data/sessions.db"
SEVERITY_RANK = {"info": 0, "warn": 1, "fatal": 2}


def load_rows(db: Path):
    if not db.is_file():
        raise SystemExit(f"审计库不存在：{db}\n先跑一次应用，产生对话记录后再来。")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM audit_log ORDER BY id")]
    con.close()
    return rows


def classify(rows):
    from backend.app.dialogue.markers import infer_tags_local
    from backend.app.dialogue.taxonomy import RISK_ORDER

    def rank(x):
        return RISK_ORDER.get(str(x), -1)

    lat = [r["latency_ms"] for r in rows if r.get("latency_ms")]
    med = statistics.median(lat) if lat else 0

    out = []
    for r in rows:
        flags = []
        violations = r.get("violations") or ""
        reply = (r.get("reply") or "").strip()
        scenes = r.get("scenes") or ""
        risk = r.get("risk")
        text = r.get("user_message") or ""

        if str(violations).strip() not in ("", "[]", "null"):
            flags.append(("fatal", "VIOLATION", str(violations)[:120]))

        if text:
            kw_risk = infer_tags_local(text)[0]
            if rank(kw_risk) > rank(risk):
                flags.append(("fatal", "RISK_UNDERESTIMATE",
                              f"记录={risk} 关键词推断={kw_risk}"))
            elif rank(risk) > rank(kw_risk):
                flags.append(("info", "RISK_OVERESTIMATE",
                              f"记录={risk} 关键词推断={kw_risk}"))

        if r.get("fallback_used"):
            flags.append(("warn", "FALLBACK", "回复被兜底话术替换"))

        if len(reply) < 10:
            flags.append(("warn", "EMPTY_REPLY", f"回复仅 {len(reply)} 字"))

        if str(scenes).strip() in ("", "[]", "null"):
            flags.append(("warn", "NO_SCENES", "场景为空"))

        if med and r.get("latency_ms") and r["latency_ms"] > med * 3:
            flags.append(("warn", "LATENCY_OUTLIER",
                          f"{r['latency_ms']}ms > 3×中位({med:.0f}ms)"))

        if flags:
            out.append(dict(
                id=r["id"], user=text, reply=reply, risk=risk, scenes=scenes,
                latency_ms=r.get("latency_ms"), model=r.get("model"),
                flags=[dict(severity=s, code=c, detail=d) for s, c, d in flags],
                max_severity=max((s for s, _, _ in flags), key=lambda x: SEVERITY_RANK[x]),
            ))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="从审计日志挖失败集")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default="")
    ap.add_argument("--min-severity", choices=["info", "warn", "fatal"], default="info")
    ap.add_argument("--json-summary", default="")
    args = ap.parse_args()

    db = Path(args.db)
    rows = load_rows(db)
    cases = classify(rows)
    cases = [c for c in cases
             if SEVERITY_RANK[c["max_severity"]] >= SEVERITY_RANK[args.min_severity]]

    print("=" * 80)
    print("  失败集挖掘")
    print("=" * 80)
    print(f"  审计库   : {db}")
    print(f"  总记录   : {len(rows)} 条")
    print(f"  命中标记 : {len(cases)} 条（min-severity={args.min_severity}）")

    if len(rows) < 20:
        print()
        print(f"  ⚠️ **样本量不足**：只有 {len(rows)} 条。")
        print("     工具能跑，但下面任何比例都不可靠。")
        print("     建议先积累真实对话记录（演示/试用跑一阵），再来挖。")
    print()

    if not cases:
        print("  没有命中任何标记 ✓")
        return 0

    # 按 code 汇总
    by_code = Counter()
    by_sev = Counter()
    for c in cases:
        for f in c["flags"]:
            by_code[f["code"]] += 1
        by_sev[c["max_severity"]] += 1

    print("-" * 80)
    print("  按标记汇总")
    print("-" * 80)
    desc = {
        "VIOLATION": "安全层报错（violations 非空）",
        "RISK_UNDERESTIMATE": "可能漏判（关键词推断更高）",
        "FALLBACK": "回复被兜底替换",
        "EMPTY_REPLY": "回复为空或过短",
        "NO_SCENES": "场景为空",
        "LATENCY_OUTLIER": "延迟异常",
        "RISK_OVERESTIMATE": "可能误报（记录等级更高）",
    }
    for code, n in by_code.most_common():
        print(f"  {code:<22}{n:>4} 条   {desc.get(code, '')}")
    print()
    print(f"  严重度分布: {dict(by_sev)}")
    print()

    print("-" * 80)
    print("  逐条明细")
    print("-" * 80)
    for c in cases:
        print(f"\n  #{c['id']}  [{c['max_severity']}]  risk={c['risk']}  "
              f"latency={c['latency_ms']}ms")
        print(f"    user : {c['user'][:64]}")
        print(f"    reply: {c['reply'][:64]}")
        for f in c["flags"]:
            print(f"      - [{f['severity']}] {f['code']}: {f['detail']}")

    if args.out:
        Path(args.out).write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
            encoding="utf-8")
        print(f"\n  明细已写 {args.out}")

    if args.json_summary:
        Path(args.json_summary).write_text(json.dumps(dict(
            total=len(rows), hit=len(cases), by_code=dict(by_code),
            by_severity=dict(by_sev),
            sample_sufficient=len(rows) >= 20,
        ), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  汇总已写 {args.json_summary}")

    print()
    print("  提醒：RISK_UNDERESTIMATE / OVERESTIMATE 只是**线索**，不是定论——")
    print("        关键词兜底自身也会误判，每条都需人工过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
