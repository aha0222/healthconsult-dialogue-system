"""sampling.py 单元测试：脱敏、样本转换与导出。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.sampling import (
    audit_to_sample,
    export_samples,
    redact,
)
from backend.app.storage import Database
from backend.app.dialogue.taxonomy import tags_to_markers


def make_db(tmp_path):
    return Database(tmp_path / "s.db")


def test_redact_phone_id_bank():
    assert redact("我电话13812345678") == "我电话[手机号]"
    assert "[身份证]" in redact("身份证号110101199001011234")
    assert "[银行卡]" in redact("卡号6222021234567890")


def test_redact_keeps_normal_numbers():
    assert redact("血压150，血糖7.2") == "血压150，血糖7.2"


def test_tags_to_markers():
    assert tags_to_markers("R1", ["S3"]) == ["[RISK:R1]", "[SCENE:S3]"]
    assert tags_to_markers("R2b", ["E1"]) == ["[RISK:R2b]", "[SCENE:E1]"]
    assert tags_to_markers("R0", ["X1"]) == ["[RISK:R0]", "[SCENE:X1]"]
    assert tags_to_markers("", []) == []


def test_audit_to_sample_shape_and_marker():
    record = {
        "id": 7,
        "session_id": "s1",
        "user_message": "我电话13812345678，血压高",
        "reply": "您记下来带给医生看。",
        "risk": "R1",
        "scenes": ["S3"],
        "violations": ["missing_scene_marker"],
        "fallback_used": False,
        "model": "deepseek-chat",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    sample = audit_to_sample(record)
    assert sample["sample_id"] == "online_7"
    assert "[手机号]" in sample["user"]
    assert sample["assistant"].endswith("[RISK:R1]\n[SCENE:S3]")
    assert sample["risk_level"] == "R1"
    assert sample["scenes"] == ["S3"]
    assert sample["metadata"]["source"] == "online"
    assert sample["metadata"]["fallback_used"] is False


def test_audit_to_sample_without_marker():
    record = {
        "id": 1,
        "user_message": "你好",
        "reply": "您好。",
        "risk": "",
        "scenes": [],
        "violations": [],
        "fallback_used": False,
    }
    sample = audit_to_sample(record)
    assert sample["assistant"] == "您好。"


def test_export_samples_filters(tmp_path):
    db = make_db(tmp_path)
    db.add_audit(None, "温婉邻居型", "u1", "r1", risk="R0", scenes=["X1"], fallback_used=False)
    db.add_audit(None, "温婉邻居型", "u2", "r2", risk="R2a", scenes=["S2"], fallback_used=True)
    db.add_audit(None, "温婉邻居型", "u3", "r3", risk="R3", scenes=["E1"], fallback_used=False)

    assert len(export_samples(db)) == 3
    # 兜底 或 高危（R3）
    assert len(export_samples(db, only_flagged=True)) == 2
    # 仅 R3
    assert len(export_samples(db, risks=["R3"])) == 1
    # 时间过滤
    assert len(export_samples(db, since="2000-01-01T00:00:00+00:00")) == 3
    assert len(export_samples(db, since="2999-01-01T00:00:00+00:00")) == 0
    # limit
    assert len(export_samples(db, limit=1)) == 1
