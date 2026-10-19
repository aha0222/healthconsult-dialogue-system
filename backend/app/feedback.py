"""人工反馈与交互信号：把"老人满不满意"变成审计表里可查的字段。

为什么单独一个模块：`audit` 表本来只有客观信号（violations / fallback_used /
风险等级），没有"好坏信号"。而自我改进真正的瓶颈就是好坏信号缺失——
赞/踩是唯一由人给出的质量标注，重复追问是它的客观代理：老人没听懂或用不上，
就会把同一件事再问一遍。这里只做判定，不碰任何安全逻辑。

三个信号：
    feedback          前端赞/踩，由 `POST /api/audit/{id}/feedback` 写入
    interrupted       前端播报被打断，由 `POST /api/audit/{id}/interrupt` 写入
    repeated_question 本模块在请求进入时判定，随审计落库
"""

import difflib
import re

# 赞 / 踩（0＝撤销或未评）。与 audit_log.feedback 的取值域一致。
FEEDBACK_UP = 1
FEEDBACK_DOWN = -1
FEEDBACK_NONE = 0
VALID_FEEDBACK = (FEEDBACK_DOWN, FEEDBACK_NONE, FEEDBACK_UP)

# 相似度阈值：太松会把"还是咳嗽"和"有点头晕"这种同长度短句误判成重复；
# 0.8 是在"同一件事换个说法"（睡不着 / 还是睡不着，≈0.86）上仍能命中的经验值。
REPEAT_SIMILARITY_THRESHOLD = 0.8

# 太短的句子（"嗯""好""疼"）相似度不可靠，一律不算重复追问
MIN_REPEAT_CHARS = 4

_NON_CONTENT_RE = re.compile(r"[\s，。、！？；：,.!?;:~…—\-（）()\[\]“”\"']+")


def normalize_question(text: str) -> str:
    """去掉标点与空白，只留实义字符，避免"睡不着。" 与"睡不着" 被判为不同。"""
    return _NON_CONTENT_RE.sub("", text or "")


def is_repeated_question(previous: str | None, current: str | None) -> bool:
    """连续两次用户消息是否在问同一件事。

    用标准库 difflib 做字符级相似度，不引入依赖、不调模型——这个信号要能
    在每次请求的同步路径上廉价算完。判不出来时一律返回 False（宁缺勿滥）。
    """
    prev = normalize_question(previous)
    cur = normalize_question(current)
    if len(prev) < MIN_REPEAT_CHARS or len(cur) < MIN_REPEAT_CHARS:
        return False
    if prev == cur:
        return True
    return difflib.SequenceMatcher(None, prev, cur).ratio() >= REPEAT_SIMILARITY_THRESHOLD
