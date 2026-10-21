# -*- coding: utf-8 -*-
"""文本清洗（引擎无关）：只去掉模型不该念出来的东西，不动数字与正常标点。"""

from voice_service.app import clean_for_tts


def test_strips_bold_and_italic():
    assert clean_for_tts("这几种症状凑在一起，**马上打120**，一分钟都别耽搁。") == (
        "这几种症状凑在一起，马上打120，一分钟都别耽搁。"
    )
    assert clean_for_tts("这是*很重要的*事情") == "这是很重要的事情"


def test_strips_markers_and_code():
    assert clean_for_tts("您先别慌。[RISK:R3]\n[SCENE:E1]") == "您先别慌。"
    # 整块代码围栏直接丢掉（念 print(1) 对老人没有意义）
    assert clean_for_tts("```python\nprint(1)\n```") == ""
    assert clean_for_tts("用 `血压计` 量一下") == "用 血压计 量一下"


def test_strips_heading_list_and_link():
    assert clean_for_tts("## 建议\n- 少放盐\n1. 多走动") == "建议，少放盐，多走动"
    assert clean_for_tts("详见[就医指南](https://example.com)") == "详见就医指南"


def test_newlines_become_pauses():
    assert clean_for_tts("第一句\n\n第二句") == "第一句。第二句"
    assert clean_for_tts("前半句\n后半句") == "前半句，后半句"


def test_keeps_numbers_and_slash_untouched():
    # 数字本身不改，交给引擎自己的文本规范化
    assert clean_for_tts("血压 150/95，每天 0.125 毫克") == "血压 150比95，每天 0.125 毫克"


def test_digit_slash_becomes_bi():
    """血压写法 150/95 必须读成"一百五十比九十五"。

    实测：云端会读成"除以"、本地 CosyVoice2 会读成"九十五分之一百五十"（方向反了）。
    """
    assert clean_for_tts("血压 150/95") == "血压 150比95"
    assert clean_for_tts("90/60 偏低") == "90比60 偏低"
    assert clean_for_tts("150 / 95") == "150比95"


def test_date_slash_is_left_alone():
    # 日期不能变成"2024比10比09"
    assert clean_for_tts("2024/10/09 复诊") == "2024/10/09 复诊"


def test_simple_name_untouched():
    assert clean_for_tts("朱阿姨，您说的是血压吧？") == "朱阿姨，您说的是血压吧？"


def test_empty_after_cleaning():
    assert clean_for_tts("**  **") == ""
    assert clean_for_tts("") == ""
