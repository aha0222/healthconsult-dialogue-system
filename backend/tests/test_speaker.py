"""speaker.py 测试：契约不变量 + 启发式判定 + 准确率评测。

验收标准（分工方案）：识别准确率 85%+；
低置信度必须回退 elder（宁可不切，不可误切）。
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


# ── 准确率评测（验收标准 85%+）───────────────────────────────────────

ELDER_CASES = [
    "我这两天头晕得厉害",
    "我血压有点高，要不要紧",
    "我晚上老是睡不着",
    "我吃了降压药还是头晕",
    "我腿疼得走不了路",
    "我一个人住，心里发慌",
    "我胃口不好，吃不下饭",
    "我最近总忘事，是不是老了",
    "我心慌胸闷",
    "帮我看看这个化验单",
    "我今晚咳嗽得睡不着",
    "我腰疼半个月了",
    "我这血压一直不稳",
    "我老伴上周住院了，我很担心",
    "我儿子血压高，该怎么办",
    "我女儿在外地，我想她了",
    "我吃完饭就犯困",
    "我夜里起夜三四次",
    "我手抖得拿不住筷子",
    "我眼睛干得难受",
    "我走路多了就喘",
    "我想问问我的降压药能不能停",
    "我记性越来越差",
    "我浑身没劲",
    "我最近瘦了好几斤",
    "我吃了安眠药还是睡不好",
    "我这个月量血压都是150多",
    "我有点恶心想吐",
    "我膝盖疼上下楼困难",
    "我心里烦得很，跟你说说话",
    "我拉肚子两天了要不要去医院",
    "我头晕刚才差点摔倒",
    "我想问问怎么量血压",
    "我今天量血糖7点多",
    "我夜里心口疼出了一身汗",
    "你们这都能聊什么",
    "谢谢姑娘，我心里舒坦多了",
]

FAMILY_CASES = [
    "我爸血压高，该怎么照顾他",
    "我妈妈不肯吃药怎么办",
    "我奶奶最近总忘事",
    "我家老人摔了一跤，要不要去医院",
    "我父亲确诊了糖尿病，饮食上注意什么",
    "怎么照顾得了脑梗的老人",
    "我爷爷晚上不睡觉，到处走",
    "我岳母做完手术回家，怎么护理",
    "老爷子不肯戴助听器怎么办",
    "我妈血糖控制不好，该挂什么科",
    "老太太总说有人偷她东西",
    "我婆婆心脏不好，能坐飞机吗",
    "家里老人便秘一周了",
    "我外公记性越来越差，是不是老年痴呆",
    "我母亲说她心里难受，我该注意什么",
    "我爸做完化疗恶心吃不下",
    "我妈一个人住，我不放心",
    "替我爸问一下，他降压药忘了吃",
    "我奶奶86了，最近老说头晕",
    "老人不肯去体检怎么办",
    "我妈腿脚不便，家里该怎么改造",
    "我爷爷吃饭老呛咳",
    "我父亲夜里老是说胡话",
    "怎么护理卧床的老人",
    "我外婆出院后情绪很低落",
    "我妈有高血压，能吃感冒药吗",
    "我婆婆总买保健品，怎么劝",
    "我家老人老说腿疼",
    "我母亲最近瘦了很多",
    "我爸一个人住，我远程怎么关心他",
]


def test_accuracy_meets_acceptance_bar():
    """分工方案验收标准：识别准确率 85% 以上。"""
    cases = [(text, speaker.SPEAKER_ELDER) for text in ELDER_CASES] + [
        (text, speaker.SPEAKER_FAMILY) for text in FAMILY_CASES
    ]
    wrong = [
        (text, expected, speaker.detect_speaker_role(text))
        for text, expected in cases
        if speaker.detect_speaker_role(text) != expected
    ]
    accuracy = 1 - len(wrong) / len(cases)
    assert accuracy >= 0.85, f"准确率 {accuracy:.2%} 低于 85%，错判：{wrong}"
