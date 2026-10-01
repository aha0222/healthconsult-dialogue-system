"""刷新语料内嵌的 SKILL.md 快照。

背景：训练语料（v0.3.0_corpus500.jsonl 等）的 system 消息是生成时刻的
SKILL.md 快照。SKILL.md 一旦修改，test_corpus.py 的
`test_embedded_system_matches_current_skill` 会失败，提醒快照过期。

用法：
    python tools/refresh_skill_snapshot.py            # 处理缺省文件（corpus500）
    python tools/refresh_skill_snapshot.py A.jsonl …  # 显式指定文件

只替换 system 中的 SKILL.md 部分（以 SKILL.md 开头标题识别），
保留其后附加的说话人提示；user/assistant 与标签一律不动。
"""

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from corpus_common import SKILL_MD, write_jsonl, load_jsonl  # noqa: E402
from migrate_tags_v2 import SKILL_HEADING, migrate_system  # noqa: E402

DEFAULT_FILES = [
    REPO_ROOT / "skills" / "healthconsult-assistant-skill"
    / "examples" / "corpus" / "v0.3.0_corpus500.jsonl",
]


def refresh_file(path: Path) -> tuple:
    rows = load_jsonl(path)
    changed = 0
    for row in rows:
        for message in row.get("messages") or []:
            if message.get("role") != "system":
                continue
            content = message.get("content") or ""
            if SKILL_HEADING not in content:
                continue
            new_system, updated = migrate_system(content)
            if updated:
                message["content"] = new_system
                changed += 1
    if changed:
        write_jsonl(path, rows)
    return changed, len(rows)


def main() -> None:
    paths = [Path(arg).resolve() for arg in sys.argv[1:]] or DEFAULT_FILES
    print(f"SKILL.md 源：{SKILL_MD}")
    for path in paths:
        if not path.is_file():
            print(f"[跳过] 文件不存在：{path}")
            continue
        changed, total = refresh_file(path)
        print(f"  {path.relative_to(REPO_ROOT)}: {changed}/{total} 行刷新")


if __name__ == "__main__":
    main()
