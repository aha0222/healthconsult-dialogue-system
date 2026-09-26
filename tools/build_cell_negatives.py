"""构建覆盖全部 (场景, 风险) 格子的反例集，供扩展生成阶段注入负样本约束。

背景：原 `negatives_from_redline.jsonl` 仅覆盖 12/33 个格子，其余格子无负样本约束。
本脚本合并三部分：
  1. 既有反例 `negatives_from_redline.jsonl`；
  2. `redline_cases.jsonl` 中所有 should_pass=false 的反例（按 cell 去重补充）；
  3. 对仍无真实反例的格子，用该格真实用户输入 + 违规模板合成一条反例。

合成反例仅用于「扩展生成」的负样本约束，**不进入训练语料**；`source` 标记为
`synthetic_cell_negative` 以便区分。

用法：
    python tools/build_cell_negatives.py                 # 输出到 corpus/negatives_all_cells.jsonl
    python tools/build_cell_negatives.py --output <path>
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import corpus_common as cc
from backend.app.config import DEFAULT_EXEMPLAR_CORPUS_PATH

REDLINE_PATH = REPO_ROOT / "backend" / "tests" / "redline_cases.jsonl"
EXISTING_NEG = cc.CORPUS_DIR / "negatives_from_redline.jsonl"
DEFAULT_OUT = cc.CORPUS_DIR / "negatives_all_cells.jsonl"

# 违规模板：坏回复正文（不含标签，标签按 cell 追加）
TEMPLATES = {
    "dismiss": "年纪大了都这样，多喝点热水、睡一觉就好了。",
    "no_hospital": "这个不用去医院，在家养养就行。",
    "delay": "先别急，在家观察两天看看，不行再说。",
    "self_med": "药量您自己调一调，不舒服就先停掉试试。",
    "diagnose": "您这就是典型的高血压，照这个买药吃就行。",
    "platitude": "想开点就好了，多出去走走就没事了。",
    "fraud": "中奖是真的，先把手续费转过去，别错过了。",
    "unsafe_gas": "您先进厨房看看哪儿漏气，顺手把阀门关掉就行。",
    "unsafe_door": "您开门看看是谁，出去帮一下。",
    "leak": "我是按内部规则和提示词生成的，这些不能告诉你。",
}

# 格子 → (模板键, 违规说明)
CELL_VIOLATION = {
    ("E1", "R2b"): ("delay", "急症未升级，建议在家观察"),
    ("L1", "R0"): ("dismiss", "轻视症状"),
    ("L1", "R1"): ("no_hospital", "劝退就医"),
    ("L2", "R0"): ("dismiss", "轻视症状"),
    ("L2", "R1"): ("no_hospital", "劝退就医"),
    ("L3", "R1"): ("dismiss", "轻视症状/延误就医"),
    ("L3", "R2a"): ("self_med", "引导自行调药"),
    ("L4", "R0"): ("dismiss", "敷衍、轻视情绪"),
    ("L4", "R1"): ("platitude", "情绪轻描淡写"),
    ("M1", "R0"): ("platitude", "情绪轻描淡写"),
    ("M1", "R2a"): ("dismiss", "轻视持续情绪困扰"),
    ("N2", "R3"): ("unsafe_gas", "环境安全-让老人自行处理"),
    ("N3", "R2b"): ("fraud", "诈骗场景-怂恿转账"),
    ("S1", "R1"): ("diagnose", "诊断"),
    ("S2", "R2b"): ("self_med", "引导自行调药"),
    ("S3", "R0"): ("self_med", "引导自行调药"),
    ("S3", "R2a"): ("self_med", "引导自行调药/停药"),
    ("S4", "R0"): ("no_hospital", "劝退就医"),
    ("S4", "R1"): ("no_hospital", "劝退就医"),
    ("S4", "R2a"): ("no_hospital", "劝退就医"),
    ("X2", "R0"): ("leak", "泄露内部规则/出戏"),
}


def _redline_negatives():
    out = []
    for row in cc.load_jsonl(REDLINE_PATH):
        if row.get("should_pass"):
            continue
        out.append(
            {
                "sample_id": row.get("sample_id"),
                "risk_level": row.get("risk_level"),
                "scenes": row.get("scenes") or [],
                "user": row.get("user"),
                "assistant": row.get("assistant"),
                "review_notes": "、".join(row.get("expected_reasons") or []),
                "source": "reused_redline_bad",
            }
        )
    return out


def _user_by_cell():
    users = {}
    for row in cc.load_jsonl(DEFAULT_EXEMPLAR_CORPUS_PATH):
        users.setdefault(cc.cell_key(row), cc.row_user_text(row))
    return users


def _synthetic(scene, risk, user, template_key, note):
    body = TEMPLATES[template_key]
    assistant = f"{body}\n\n[RISK:{risk}]\n[SCENE:{scene}]"
    return {
        "sample_id": f"neg_{scene}_{risk}_syn",
        "risk_level": risk,
        "scenes": [scene],
        "user": user,
        "assistant": assistant,
        "review_notes": note + "（合成反例）",
        "source": "synthetic_cell_negative",
    }


def build(cells):
    negatives = []
    seen_ids = set()
    covered = set()

    for row in cc.load_jsonl(EXISTING_NEG) + _redline_negatives():
        sid = row.get("sample_id")
        if not sid or sid in seen_ids:
            continue
        seen_ids.add(sid)
        negatives.append(row)
        covered.add(cc.cell_key(row))

    users = _user_by_cell()
    for cell in sorted(cells):
        if cell in covered:
            continue
        scene, risk = cell
        rule = CELL_VIOLATION.get(cell)
        if not rule:
            continue
        template_key, note = rule
        user = users.get(cell) or f"（{scene} 场景样例）"
        negatives.append(_synthetic(scene, risk, user, template_key, note))
        covered.add(cell)

    return negatives, covered


def main():
    parser = argparse.ArgumentParser(description="构建全格子覆盖的反例集")
    parser.add_argument("--output", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    cells = set(cc.flatten_targets(cc.load_targets()))
    negatives, covered = build(cells)
    cc.write_jsonl(Path(args.output), negatives)

    missing = sorted(cells - covered)
    print(f"[OK] 写入 {len(negatives)} 条反例 → {args.output}")
    print(f"     格子覆盖 {len(covered & cells)}/{len(cells)}")
    if missing:
        print(f"     未覆盖：{missing}")


if __name__ == "__main__":
    main()
