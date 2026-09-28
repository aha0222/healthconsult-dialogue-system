"""speaker.py 契约测试（Day0 空壳）。

固定"默认行为＝现状"这一不变量：未交付前恒按老人本人处理，零回归。
"""

from backend.app.dialogue import speaker


def test_default_role_is_elder():
    """未实现前，任何输入都必须判为老人本人（＝维持现状）。"""
    assert speaker.detect_speaker_role("我这两天血压有点高") == speaker.SPEAKER_ELDER
    assert speaker.detect_speaker_role("我儿子血压高，该怎么办") == speaker.SPEAKER_ELDER
    assert speaker.detect_speaker_role("") == speaker.SPEAKER_ELDER


def test_history_argument_is_accepted():
    """签名含可选 history，必须可接受而不报错。"""
    assert (
        speaker.detect_speaker_role("我该注意什么", history=["我母亲吃药了"])
        == speaker.SPEAKER_ELDER
    )


def test_role_hint_empty_for_elder_and_unknown():
    assert speaker.role_hint(speaker.SPEAKER_ELDER) == ""
    assert speaker.role_hint(speaker.SPEAKER_UNKNOWN) == ""
    assert speaker.role_hint("不存在的角色") == ""


def test_family_hint_keeps_safety_boundary():
    """家属性提示只调称呼与侧重，必须显式声明安全边界不放松。"""
    hint = speaker.role_hint(speaker.SPEAKER_FAMILY)
    assert "家属" in hint
    assert "安全" in hint


def test_valid_roles_enum():
    assert set(speaker.VALID_SPEAKER_ROLES) == {"elder", "family", "unknown"}
