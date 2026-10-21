"""风险等级 → 播报语气：纯函数，不需要加载模型。"""

from voice_service.tone import CRISIS_INSTRUCT, CRISIS_RISKS, instruct_for, tts_params


def test_normal_risk_keeps_baseline():
    assert tts_params(0.9) == (0.9, 1.0)
    for risk in ("R1", "R2a", "R2b"):
        assert tts_params(0.9, risk) == (0.9, 1.0)
        assert instruct_for(risk) is None


def test_crisis_risks_slower_and_lower():
    for risk in CRISIS_RISKS:
        speed, pitch = tts_params(0.9, risk)
        assert speed < 0.9
        assert pitch < 1.0


def test_crisis_gets_natural_language_instruction():
    # CosyVoice2 的正解是指令位；正文一个字都不该改。
    for risk in CRISIS_RISKS:
        assert instruct_for(risk) == CRISIS_INSTRUCT
    assert "沉重" in CRISIS_INSTRUCT


def test_speed_stays_inside_engine_range():
    # 速度只往下调：调高会让引擎的时长模型失配、念得发飘。
    for base in (0.5, 0.9, 2.0):
        for risk in CRISIS_RISKS:
            speed, _ = tts_params(base, risk)
            assert 0.5 <= speed <= 1.0


def test_unknown_risk_is_normal():
    assert tts_params(0.9, "R9") == (0.9, 1.0)
    assert tts_params(0.9, None) == (0.9, 1.0)
    assert instruct_for("R9") is None
    assert instruct_for(None) is None
