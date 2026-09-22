"""构建 Retriever 用的标注语料（种子版）。

从现有已标注数据汇总，去重后按稳定顺序编号 C001、C002……输出 JSONL。
正式语料库搭建后，可直接把新语料并入同一目录，本脚本负责生成种子与校验格式。

用法：
    python tools/build_scene_risk_corpus.py
    python tools/build_scene_risk_corpus.py --output backend/app/dialogue/corpus/scene_risk_corpus.jsonl
    python tools/build_scene_risk_corpus.py --stdout
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

from backend.app.config import DEFAULT_CORPUS_PATH
from backend.app.dialogue.taxonomy import canonical_risk, normalize_scenes
from backend.app.safety.safety_checker import (
    SCENE_KEYWORDS,
    _S1_KEYWORDS,
    _extract_fields,
)

# 来源文件（按固定顺序，保证编号稳定）
SOURCES = [
    ("risk_cases", REPO_ROOT / "backend" / "tests" / "eval" / "risk_cases.jsonl"),
    ("redline_cases", REPO_ROOT / "backend" / "tests" / "redline_cases.jsonl"),
    (
        "good_examples",
        REPO_ROOT / "skills" / "healthconsult-assistant-skill"
        / "examples" / "good_health_assistant_examples.jsonl",
    ),
    (
        "paired_messages",
        REPO_ROOT / "skills" / "healthconsult-assistant-skill"
        / "examples" / "paired_health_messages.jsonl",
    ),
]

_KEYWORDS_BY_SCENE = {**SCENE_KEYWORDS, "S1": _S1_KEYWORDS}


def _keywords_for(text, scenes) -> list:
    found = []
    for scene in scenes:
        for kw in _KEYWORDS_BY_SCENE.get(scene, []):
            if kw in text and kw not in found:
                found.append(kw)
    return found


def _load_rows(path):
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def build_entries(sources=None):
    """返回去重后的语料条目列表（不含 id）。"""
    sources = sources if sources is not None else SOURCES
    entries = []
    seen = set()
    for source_name, path in sources:
        for row in _load_rows(path):
            _sid, user, _assistant, _scene, risk, scenes = _extract_fields(row)
            if not canonical_risk(risk):
                risk = row.get("expected_risk") or risk
            if not normalize_scenes(scenes):
                scenes = row.get("expected_scenes") or scenes
            user = (user or "").strip()
            canonical = canonical_risk(risk)
            scene_list = normalize_scenes(scenes)
            if not user or not canonical or not scene_list:
                continue
            if user in seen:
                continue
            seen.add(user)
            entries.append(
                {
                    "user": user,
                    "risk": canonical,
                    "scenes": scene_list,
                    "keywords": _keywords_for(user, scene_list),
                    "source": source_name,
                }
            )
    for index, entry in enumerate(entries, 1):
        entry_with_id = {"id": f"C{index:03d}"}
        entry_with_id.update(entry)
        entries[index - 1] = entry_with_id
    return entries


def write_corpus(path, entries):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="构建场景/风险标注语料（种子）")
    parser.add_argument("--output", default=str(DEFAULT_CORPUS_PATH), help="输出 JSONL 路径")
    parser.add_argument("--stdout", action="store_true", help="打印到标准输出，不写文件")
    args = parser.parse_args()

    entries = build_entries()
    if args.stdout:
        for entry in entries:
            print(json.dumps(entry, ensure_ascii=False))
    else:
        write_corpus(args.output, entries)
        print(f"[OK] 写入 {len(entries)} 条语料 → {args.output}")


if __name__ == "__main__":
    main()
