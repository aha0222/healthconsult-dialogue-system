"""speaker.py 测试：契约不变量 + 启发式判定。

低置信度必须回退 elder（宁可不切，不可误切）。
准确率评测不在这里——见文件末尾说明。
"""

from backend.app.dialogue import speaker


# ── 契约不变量（Day0 冻结，长期有效）─────────────────────────────────


def test_valid_roles_enum():
    assert set(speaker.VALID_SPEAKER_ROLES) == {"elder", "family", "unknown"}


def test_signature_accepts_text_and_history():
    """签名含可选 history，必须可接受而不报错。"""
    assert speaker.detect_speaker_role("我该注意什么") == speaker.SPEAKER_ELDER
    assert speaker.detect_speaker_role(
        "我该注意什么", history=["我爸得了脑梗"]
    ) == speaker.SPEAKER_FAMILY


def test_role_hint_empty_for_elder_and_unknown():
    assert speaker.role_hint(speaker.SPEAKER_ELDER) == ""
    assert speaker.role_hint(speaker.SPEAKER_UNKNOWN) == ""
    assert speaker.role_hint("不存在的角色") == ""


def test_family_hint_keeps_safety_boundary():
    """家属性提示只调称呼与侧重，必须显式声明安全边界不放松。"""
    hint = speaker.role_hint(speaker.SPEAKER_FAMILY)
    assert "家属" in hint
    assert "安全" in hint


def test_return_values_within_contract():
    """任何输入（含空、超长、奇怪字符）都只返回契约枚举值，不抛异常。"""
    for text in ("", "   ", "！！！", "我", "a" * 500, "\n\t"):
        assert speaker.detect_speaker_role(text) in speaker.VALID_SPEAKER_ROLES


# ── 判定行为 ──────────────────────────────────────────────────────────


def test_clear_family_inputs_detected():
    assert speaker.detect_speaker_role("我爸血压高，该怎么照顾他") == speaker.SPEAKER_FAMILY
    assert speaker.detect_speaker_role("我家老人摔了一跤，要不要去医院") == speaker.SPEAKER_FAMILY
    assert speaker.detect_speaker_role("怎么护理卧床的老人") == speaker.SPEAKER_FAMILY
    assert speaker.detect_speaker_role("我奶奶最近总忘事") == speaker.SPEAKER_FAMILY


def test_clear_elder_inputs_detected():
    assert speaker.detect_speaker_role("我这两天头晕得厉害") == speaker.SPEAKER_ELDER
    assert speaker.detect_speaker_role("我血压有点高，要不要紧") == speaker.SPEAKER_ELDER
    assert speaker.detect_speaker_role("我晚上老是睡不着") == speaker.SPEAKER_ELDER


def test_downward_kinship_means_elder_speaker():
    """问子女/配偶的事：说话人是老人本人（向下亲属＝老人视角）。"""
    assert (
        speaker.detect_speaker_role("我儿子血压高，该怎么办") == speaker.SPEAKER_ELDER
    )
    assert speaker.detect_speaker_role("我老伴上周住院了") == speaker.SPEAKER_ELDER


def test_kinship_does_not_leak_into_elder_self_score():
    """"我爸不肯吃药"里的"吃药"不得计入老人自述分。"""
    assert speaker.detect_speaker_role("我爸不肯吃药怎么办") == speaker.SPEAKER_FAMILY
    assert speaker.detect_speaker_role("我妈血糖控制不好") == speaker.SPEAKER_FAMILY


def test_low_confidence_falls_back_to_elder():
    """弱信号（第三人称+症状）无历史佐证时不切换，保持 elder。"""
    assert speaker.detect_speaker_role("他打呼噜很响") == speaker.SPEAKER_ELDER
    assert speaker.detect_speaker_role("你好") == speaker.SPEAKER_ELDER
    assert speaker.detect_speaker_role("") == speaker.SPEAKER_ELDER


def test_tie_goes_to_elder():
    """家属分与老人分打平（老人提到母亲病史又说自己不舒服）：判老人。"""
    text = "我妈年轻时也有这毛病，我现在也头晕"
    assert speaker.detect_speaker_role(text) == speaker.SPEAKER_ELDER


def test_history_continuation_and_ignored_items():
    """先说清是家属，后续指代延续判家属；非字符串历史项被忽略。"""
    assert (
        speaker.detect_speaker_role("他最近总忘事", history=["我爸今年80了"])
        == speaker.SPEAKER_FAMILY
    )
    assert (
        speaker.detect_speaker_role("那该怎么办", history=[None, 123, "我妈吃药了"])
        == speaker.SPEAKER_FAMILY
    )
    # 历史是家属，但当前明确是老人自述 -> 老人
    assert (
        speaker.detect_speaker_role("我头晕得厉害", history=["我爸今年80了"])
        == speaker.SPEAKER_ELDER
    )


# ── 准确率评测已迁出 ──────────────────────────────────────────────────
# 原先内嵌的 67 例样例列表与 test_accuracy_meets_acceptance_bar 已迁至
# backend/tests/eval/speaker_cases.jsonl（并新增 96 例对抗样例，共 163 例
# 角色类 + 12 例安全类），由 backend/tests/test_speaker_eval.py 驱动，
# 避免样例与判定散落两处。本文件保留契约与行为单测。
