"""Retriever 语料回归：非空、id 唯一、与训练语料标签逐条一致。

对应语料库建设交付（v0.3.0）的建议：给 scene_risk_corpus.jsonl 加回归保护，
防止重跑构建脚本时静默丢失或错标。
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.prompt import load_skill_prompt
from backend.app.dialogue.taxonomy import (
    RISK_LEVELS,
    SCENES,
    canonical_risk,
    normalize_scenes,
)

CORPUS_PATH = REPO_ROOT / "backend" / "app" / "dialogue" / "corpus" / "scene_risk_corpus.jsonl"
TRAIN_PATH = (
    REPO_ROOT / "skills" / "healthconsult-assistant-skill"
    / "examples" / "corpus" / "v0.3.0_corpus500.jsonl"
)
ID_RE = re.compile(r"^C\d{3}$")


def _load(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _training_labels():
    labels = {}
    for row in _load(TRAIN_PATH):
        user = ""
        for message in row.get("messages", []):
            if message.get("role") == "user":
                user = (message.get("content") or "").strip()
                break
        if not user:
            continue
        labels[user] = (
            canonical_risk(row.get("risk_level")),
            tuple(normalize_scenes(row.get("scenes"))),
        )
    return labels


def test_corpus_non_empty_and_sized():
    entries = _load(CORPUS_PATH)
    assert len(entries) >= 500, f"Retriever 语料应约 500 条，实际 {len(entries)}"


def test_corpus_ids_unique_and_wellformed():
    entries = _load(CORPUS_PATH)
    ids = [entry.get("id", "") for entry in entries]
    assert len(set(ids)) == len(ids), "语料 id 存在重复"
    assert all(ID_RE.match(i) for i in ids), "语料 id 应为 C001… 格式"


def test_corpus_labels_valid():
    for entry in _load(CORPUS_PATH):
        assert canonical_risk(entry.get("risk")) in RISK_LEVELS, entry
        scenes = normalize_scenes(entry.get("scenes"))
        assert scenes and all(s in SCENES for s in scenes), entry


def test_corpus_consistent_with_training_labels():
    training = _training_labels()
    entries = _load(CORPUS_PATH)
    mismatches = []
    matched = 0
    for entry in entries:
        key = (entry.get("user") or "").strip()
        if key not in training:
            continue
        matched += 1
        got = (canonical_risk(entry.get("risk")), tuple(normalize_scenes(entry.get("scenes"))))
        if got != training[key]:
            mismatches.append({"user": key, "expected": training[key], "got": got})
    assert matched >= 400, f"与训练语料匹配的条目过少：{matched}"
    assert not mismatches, f"标签与训练语料不一致：{mismatches[:3]}"


def test_embedded_system_matches_current_skill():
    """语料内嵌 system 必须是当前 SKILL.md 的快照（防止 SKILL.md 改动后训练数据静默过期）。"""
    skill = load_skill_prompt().strip()
    stale = []
    for row in _load(TRAIN_PATH):
        system = next(
            (m.get("content", "") for m in row.get("messages", []) if m.get("role") == "system"),
            "",
        )
        if not system.strip().startswith(skill):
            stale.append(row.get("sample_id"))
    assert not stale, (
        f"语料内嵌 system 与当前 SKILL.md 不一致，需刷新（tools/migrate_tags_v2.py --apply）：{stale[:5]}"
    )


def test_corpus_registers_training_source():
    entries = _load(CORPUS_PATH)
    by_source = {}
    for entry in entries:
        by_source[entry.get("source")] = by_source.get(entry.get("source"), 0) + 1
    assert by_source.get("corpus500", 0) >= 400, by_source
