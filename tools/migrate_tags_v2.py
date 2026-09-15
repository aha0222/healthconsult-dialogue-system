"""一次性全量迁移：旧单维度标记 → 双维度标签（风险等级 + 场景类别）。

迁移内容：
  1. 回复正文里的旧标记 [SITUATION:S0] / [MENTAL:M0] / [OTHER:X] / [RISK:R2]
     统一改写为 [RISK:Rx] + [SCENE:xx]（末尾，RISK 唯一、SCENE 可多个）。
  2. 样本字段：scene/sub_scene/category → scenes 列表；risk_level/llm_risk → 规范风险等级。
  3. 评测集 expected_risk 规范化，并补 expected_scenes。
  4. messages 里的 system 若内嵌旧 SKILL.md，则替换为新 SKILL.md。
  5. SQLite 历史数据：messages/audit_log 的 scenes 列按旧 risk 回填。

用法：
    python tools/migrate_tags_v2.py --dry-run
    python tools/migrate_tags_v2.py --apply
    python tools/migrate_tags_v2.py --apply --db backend/data/sessions.db
"""

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT, SKILL_MD, EXAMPLES_DIR

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.taxonomy import (
    LEGACY_RISK_MAP,
    MAX_SCENES,
    canonical_risk,
    canonical_scene,
    format_tags,
    max_risk,
    scene_from_alias,
)
from backend.app.safety.safety_checker import detect_scenes
from backend.app.dialogue.markers import infer_tags_local

ANY_TAG_RE = re.compile(r"\[(RISK|SCENE|SITUATION|MENTAL|OTHER):([^\]]+)\]", re.IGNORECASE)
SKILL_HEADING = "# 小暖健康陪护 Skill"
SPEAKER_HINT_RE = re.compile(r"\n?\[注意：当前说话者[^\]]*\]")

# 旧原因标识 → 新原因标识（红线用例 expected_reasons）
REASON_ALIASES = {
    "emergency_missing_escalation": "emergency_scene_missing_escalation",
    "mental_health_scene_missing_professional_guidance":
        "mental_crisis_scene_missing_professional_guidance",
}
REASON_SUBSTRINGS = [
    ("S0级别", "R3级别"),
    ("M0级别", "R3级别"),
    ("S1级别", "R3级别"),
]


def convert_text_tags(text):
    """把一段文本里的新旧标记统一为 (正文, 风险, 场景列表)。"""
    risk = ""
    scenes = []
    for match in ANY_TAG_RE.finditer(text or ""):
        kind = match.group(1).upper()
        code = match.group(2).strip()
        if kind == "SCENE":
            for token in re.split(r"[,\s，]+", code):
                scene = canonical_scene(token)
                if scene and scene not in scenes:
                    scenes.append(scene)
            continue

        new_risk = canonical_risk(code) if kind == "RISK" else ""
        if new_risk:
            risk = max_risk(risk, new_risk)
            continue
        legacy = LEGACY_RISK_MAP.get(code.upper())
        if legacy:
            legacy_risk, legacy_scenes = legacy
            risk = max_risk(risk, legacy_risk)
            for scene in legacy_scenes:
                if scene not in scenes:
                    scenes.append(scene)

    clean = ANY_TAG_RE.sub("", text or "").strip()
    return clean, risk, scenes[:MAX_SCENES]


def migrate_assistant(assistant_text, fallback_risk="", fallback_scenes=None, ensure_tags=True):
    clean, risk, scenes = convert_text_tags(assistant_text)
    if not risk:
        risk = canonical_risk(fallback_risk) or ""
    if not scenes:
        scenes = [s for s in (fallback_scenes or []) if canonical_scene(s)]
    if not risk:
        risk = "R0"
    if not scenes:
        scenes = ["X1"]
    if not ensure_tags:
        # 保留"故意缺标记"的负例，仅剥离旧标记
        return clean, risk, scenes[:MAX_SCENES]
    return clean + format_tags(risk, scenes), risk, scenes[:MAX_SCENES]


def row_fallback(row):
    """从行内字段/用户输入推断 fallback 风险与场景。"""
    risk = (
        row.get("risk_level")
        or row.get("llm_risk")
        or row.get("expected_risk")
        or ""
    )
    canonical = canonical_risk(risk)
    if not canonical and str(risk).upper() in LEGACY_RISK_MAP:
        canonical = LEGACY_RISK_MAP[str(risk).upper()][0]

    user = row.get("user") or row.get("user_input") or ""
    if not user:
        for message in row.get("messages", []) or []:
            if message.get("role") == "user":
                user = message.get("content", "")
                break

    scenes = list(row.get("scenes") or [])
    if not scenes:
        alias = (
            row.get("scene")
            or row.get("sub_scene")
            or row.get("category")
            or ""
        )
        scene = scene_from_alias(alias)
        if scene:
            scenes = [scene]
        else:
            scenes = detect_scenes(user)

    if not canonical:
        canonical, inferred_scenes = infer_tags_local(user)
        if not scenes:
            scenes = inferred_scenes
    return canonical, scenes


def normalize_expected_reasons(reasons):
    out = []
    for reason in reasons or []:
        new_reason = REASON_ALIASES.get(reason, reason)
        for old, new in REASON_SUBSTRINGS:
            new_reason = new_reason.replace(old, new)
        out.append(new_reason)
    return out


def migrate_system(system_text):
    """若 system 内嵌旧 SKILL.md，则替换为新 SKILL.md，保留说话者提示。"""
    if not system_text or SKILL_HEADING not in system_text:
        return system_text, False
    hint_match = SPEAKER_HINT_RE.search(system_text)
    hint = hint_match.group(0) if hint_match else ""
    new_system = SKILL_MD.read_text(encoding="utf-8") + hint
    return new_system, True


def migrate_row(row):
    fallback_risk, fallback_scenes = row_fallback(row)
    ensure_tags = "missing_scene_marker" not in (row.get("expected_reasons") or [])
    new_row = dict(row)

    # 顶层 assistant 字段格式
    if isinstance(new_row.get("assistant"), str):
        migrated, risk, scenes = migrate_assistant(
            new_row["assistant"], fallback_risk, fallback_scenes, ensure_tags
        )
        new_row["assistant"] = migrated
        new_row["risk_level"] = risk
        new_row["scenes"] = scenes
    elif isinstance(new_row.get("reply"), str):
        migrated, risk, scenes = migrate_assistant(
            new_row["reply"], fallback_risk, fallback_scenes, ensure_tags
        )
        new_row["reply"] = migrated
        new_row["risk_level"] = risk
        new_row["scenes"] = scenes

    # messages 格式
    messages = new_row.get("messages")
    if isinstance(messages, list):
        risk = ""
        scenes = []
        for message in messages:
            role = message.get("role")
            content = message.get("content", "")
            if role == "assistant":
                migrated, risk, scenes = migrate_assistant(
                    content, fallback_risk, fallback_scenes, ensure_tags
                )
                message["content"] = migrated
            elif role == "system":
                message["content"], _ = migrate_system(content)
        if risk:
            new_row["risk_level"] = risk
            new_row["scenes"] = scenes

    # 字段规范化
    if "scene" in new_row:
        new_row.pop("scene", None)
    if "sub_scene" in new_row:
        new_row.pop("sub_scene", None)
    if "llm_risk" in new_row:
        new_row.pop("llm_risk", None)

    # 评测集：expected_risk 规范化 + expected_scenes
    if "expected_risk" in new_row:
        expected = canonical_risk(new_row["expected_risk"])
        if not expected and str(new_row["expected_risk"]).upper() in LEGACY_RISK_MAP:
            expected = LEGACY_RISK_MAP[str(new_row["expected_risk"]).upper()][0]
        new_row["expected_risk"] = expected or new_row["expected_risk"]
        if not new_row.get("expected_scenes"):
            alias_scene = scene_from_alias(
                new_row.get("scene") or new_row.get("category") or ""
            )
            if not alias_scene:
                detected = detect_scenes(new_row.get("user", ""))
                new_row["expected_scenes"] = detected
            else:
                new_row["expected_scenes"] = [alias_scene]

    # 红线用例：原因标识随标签体系更新
    if "expected_reasons" in new_row:
        new_row["expected_reasons"] = normalize_expected_reasons(
            new_row["expected_reasons"]
        )

    return new_row


TARGET_GLOBS = [
    EXAMPLES_DIR / "*.jsonl",
    REPO_ROOT / "backend" / "tests" / "*.jsonl",
    REPO_ROOT / "backend" / "tests" / "eval" / "*.jsonl",
]


def iter_target_files():
    seen = set()
    for pattern in TARGET_GLOBS:
        parent = pattern.parent
        if not parent.is_dir():
            continue
        for path in sorted(parent.glob(pattern.name)):
            if path in seen:
                continue
            seen.add(path)
            yield path


def migrate_file(path, apply=False):
    lines = path.read_text(encoding="utf-8").splitlines()
    out_lines = []
    changed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out_lines.append(line)
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            out_lines.append(line)
            continue
        new_row = migrate_row(row)
        new_line = json.dumps(new_row, ensure_ascii=False)
        if new_line != stripped:
            changed += 1
        out_lines.append(new_line)

    if apply and changed:
        path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return changed, len(lines)


def migrate_db(db_path, apply=False):
    import sqlite3

    if not Path(db_path).is_file():
        return 0
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    migrated = 0
    try:
        for table in ("messages", "audit_log"):
            try:
                rows = conn.execute(
                    f"SELECT id, risk, scenes FROM {table}"
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                risk = canonical_risk(row["risk"])
                scenes = []
                if not risk:
                    legacy = LEGACY_RISK_MAP.get(str(row["risk"] or "").upper())
                    if legacy:
                        risk, scenes = legacy[0], list(legacy[1])
                if not risk:
                    continue
                try:
                    existing = json.loads(row["scenes"] or "[]")
                except (TypeError, ValueError):
                    existing = []
                if existing:
                    continue
                migrated += 1
                if apply:
                    conn.execute(
                        f"UPDATE {table} SET risk = ?, scenes = ? WHERE id = ?",
                        (risk, json.dumps(scenes, ensure_ascii=False), row["id"]),
                    )
        if apply:
            conn.commit()
    finally:
        conn.close()
    return migrated


def main():
    parser = argparse.ArgumentParser(description="旧标记 → 双维度标签 全量迁移")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认只预演）")
    parser.add_argument("--dry-run", action="store_true", help="只预演，不写入")
    parser.add_argument("--db", default=str(REPO_ROOT / "backend" / "data" / "sessions.db"),
                        help="SQLite 数据库路径（可选）")
    parser.add_argument("--skip-db", action="store_true", help="跳过数据库迁移")
    args = parser.parse_args()

    apply = args.apply and not args.dry_run
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] 迁移开始")

    total_changed = 0
    for path in iter_target_files():
        changed, total = migrate_file(path, apply=apply)
        if changed:
            rel = path.relative_to(REPO_ROOT)
            print(f"  {rel}: {changed}/{total} 行改写")
        total_changed += changed

    print(f"[{mode}] 文件共改写 {total_changed} 行")

    if not args.skip_db:
        db_changed = migrate_db(args.db, apply=apply)
        print(f"[{mode}] 数据库回填 scenes：{db_changed} 行（{args.db}）")

    if not apply:
        print("提示：加 --apply 才会真正写入。")


if __name__ == "__main__":
    main()
