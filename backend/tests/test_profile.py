"""profile.py 单元测试：采集字段映射、脱敏、合并与已知信息组装。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.profile import (
    build_collected_summary,
    build_known_info,
    build_known_info_from_text,
    map_collected_to_profile,
    mask_phone,
    merge_profiles,
    redact_collected,
    split_list,
)


def test_split_list():
    assert split_list("高血压、糖尿病；冠心病") == ["高血压", "糖尿病", "冠心病"]
    assert split_list("氨氯地平,二甲双胍\n阿司匹林") == ["氨氯地平", "二甲双胍", "阿司匹林"]
    assert split_list("") == []


def test_mask_phone():
    assert mask_phone("13812345678") == "138****5678"
    assert mask_phone("138 1234 5678") == "138****5678"
    assert mask_phone("12345") == "1****5"
    assert mask_phone("") == ""


def test_redact_collected_masks_phone_and_pii():
    cleaned = redact_collected(
        {
            "name": "张阿姨",
            "emergencyPhone": "13812345678",
            "conditions": "高血压，电话13812345678",
        }
    )
    assert cleaned["emergencyPhone"] == "138****5678"
    assert "[手机号]" in cleaned["conditions"]
    assert "13812345678" not in cleaned["conditions"]


def test_map_collected_to_profile():
    profile = map_collected_to_profile(
        {
            "conditions": "高血压、糖尿病",
            "medications": "氨氯地平",
            "allergies": "青霉素过敏",
            "healthConcerns": "膝盖疼",
            "mobility": "能自理",
        }
    )
    assert profile["conditions"] == ["高血压", "糖尿病"]
    assert profile["medications"] == ["氨氯地平"]
    assert any("过敏史：青霉素过敏" in n for n in profile["notes"])
    assert any("健康困扰：膝盖疼" in n for n in profile["notes"])
    assert any("行动/自理：能自理" in n for n in profile["notes"])


def test_merge_profiles_user_wins_and_dedupes():
    user = {"conditions": ["高血压"], "medications": ["氨氯地平"], "notes": ["过敏史：青霉素"]}
    memory = {
        "conditions": ["高血压", "冠心病"],
        "medications": ["氨氯地平", "阿司匹林"],
        "family": ["儿子"],
        "preferences": ["爱喝茶"],
        "notes": ["过敏史：青霉素", "最近睡不好"],
    }
    merged = merge_profiles(user, memory)
    assert merged["conditions"] == ["高血压", "冠心病"]
    assert merged["medications"] == ["氨氯地平", "阿司匹林"]
    assert merged["family"] == ["儿子"]
    assert merged["preferences"] == ["爱喝茶"]
    assert merged["notes"] == ["过敏史：青霉素", "最近睡不好"]


def test_build_known_info_single_block():
    collected = {
        "name": "张阿姨",
        "age": "72",
        "living": "独居",
        "emergencyPhone": "138****5678",
    }
    merged = {"conditions": ["高血压"], "medications": []}
    block = build_known_info(merged, collected, conversation_summary="血压偏高")
    assert block.startswith("【已知信息】")
    assert "称呼：张阿姨" in block
    assert "慢病/健康状况：高血压" in block
    assert "近期摘要：血压偏高" in block
    assert "未经医疗核实" in block
    # 只注入一份已知信息，不再出现「用户自述信息」的双路径标题
    assert "用户自述信息" not in block


def test_build_known_info_empty():
    assert build_known_info({}, {}, None) == ""


def test_build_collected_summary():
    summary = build_collected_summary(
        {"name": "张阿姨", "age": "72", "living": "独居", "conditions": "高血压"}
    )
    assert "张阿姨" in summary
    assert "72 岁" in summary
    assert "基础病：高血压" in summary


def test_build_known_info_from_text():
    block = build_known_info_from_text("高血压、青霉素过敏")
    assert block.startswith("【已知信息】")
    assert "高血压" in block
    assert "未经医疗核实" in block
