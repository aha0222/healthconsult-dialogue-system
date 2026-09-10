"""把线上审计记录导出为可离线质检的 JSONL 样本。

用法：
    # 导出全部（默认脱敏）
    python tools/export_online_samples.py --output online.jsonl

    # 只导出命中兜底/高危的记录，便于重点复盘
    python tools/export_online_samples.py --output flagged.jsonl --only-flagged

    # 指定时间之后、关闭脱敏
    python tools/export_online_samples.py --output out.jsonl --since 2026-01-01T00:00:00 --no-redact

导出后接质检（闭环）：
    python tools/validate_outputs.py --input online.jsonl --mode generated_sft
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

from backend.app.sampling import export_samples
from backend.app.storage import Database


def main():
    parser = argparse.ArgumentParser(description="导出线上审计样本（数据闭环）")
    parser.add_argument(
        "--db",
        default=str(REPO_ROOT / "backend" / "data" / "sessions.db"),
        help="SQLite 数据库路径",
    )
    parser.add_argument("--output", required=True, help="输出 JSONL 路径")
    parser.add_argument("--since", default=None, help="ISO 时间，只导出该时间之后")
    parser.add_argument(
        "--only-flagged",
        action="store_true",
        help="只导出命中兜底或高危风险等级的记录",
    )
    parser.add_argument("--limit", type=int, default=1000, help="最多导出条数")
    parser.add_argument(
        "--no-redact", action="store_true", help="关闭脱敏（默认脱敏手机号/身份证/银行卡）"
    )
    args = parser.parse_args()

    db = Database(args.db)
    samples = export_samples(
        db,
        since=args.since,
        only_flagged=args.only_flagged,
        limit=args.limit,
        redact_text=not args.no_redact,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")

    print(f"[完成] 导出 {len(samples)} 条 -> {output}")


if __name__ == "__main__":
    main()
