# -*- coding: utf-8 -*-
"""清理过期审计记录（按 AUDIT_RETENTION_DAYS / --days）。

为什么单独一个脚本：后端启动时已经会清一次，但后端可能连续开机好几周，
"定期"这件事交给计划任务更靠谱，也让删除动作始终是显式的、可审计的。

    .venv/Scripts/python.exe -X utf8 tools/prune_audit.py --days 180
    .venv/Scripts/python.exe -X utf8 tools/prune_audit.py --dry-run

只删 audit_log：sessions / messages（老人在界面上能翻到的对话历史）不动。
`--days 0`（或留空且环境变量为 0）＝不做任何事，与默认行为一致。
"""

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backend.app.config import Settings  # noqa: E402
from backend.app.storage import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="清理过期审计记录")
    parser.add_argument(
        "--days", type=int, default=None, help="保留天数；0=不清理。缺省读 AUDIT_RETENTION_DAYS"
    )
    parser.add_argument("--db", default=None, help="数据库路径，缺省读 DB_PATH")
    parser.add_argument("--dry-run", action="store_true", help="只报数，不删除")
    args = parser.parse_args()

    settings = Settings.from_env()
    days = settings.audit_retention_days if args.days is None else args.days
    db = Database(args.db or settings.db_path)

    total = len(db.list_audit(limit=10**9))
    if days <= 0:
        print(f"保留天数 {days}（0=永久）→ 不清理。当前审计记录 {total} 条。")
        return

    if args.dry_run:
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        expired = sum(1 for row in db.list_audit(limit=10**9) if row["created_at"] < cutoff)
        print(f"库：{db.path}\n保留 {days} 天 → 将删除 {expired} 条（共 {total} 条）。dry-run，未删除。")
        return

    removed = db.prune_audit(days)
    print(f"库：{db.path}\n保留 {days} 天 → 已删除 {removed} 条（原 {total} 条）。")


if __name__ == "__main__":
    main()
