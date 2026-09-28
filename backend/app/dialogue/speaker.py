"""说话人角色识别（Day0 契约，签名冻结）。

┌─ 解决什么问题 ───────────────────────────────────────────────────────┐
│ 现状：系统默认「说话人一定是老人」，遇到家属提问（"我该怎么照顾老人"）│
│       会输出很不合理的话（把家属当老人安抚、称呼也不对）。             │
│ 目标：对输入做一次**轻量**判断，区分「老人本人 / 家属 / 未知」，        │
│       据此调整称呼与内容侧重。                                        │
└──────────────────────────────────────────────────────────────────────┘

现状（stub）：恒定返回 SPEAKER_ELDER ＝**维持系统现有行为**，零回归。
组长交付标准：
  1) 实现启发式判定（第一/三人称、称谓词、指代对象等），可选轻量 LLM 兜底；
  2) **低置信度必须回退 SPEAKER_ELDER**（宁可不切，不可误切）；
  3) 家属性输出只调整「称呼 + 内容侧重」，**安全边界一律不放松**。

安全不变式（任何角色下都成立）：
  用药 / 急症 / 心理危机 / 人身安全红线**不因说话人是谁而改变**。
  家属问"能不能给老人加药"与老人自己问，红线判定必须一致。
"""

from __future__ import annotations

from typing import Iterable, Optional

# 角色枚举（对外契约，写入 /api/chat 的 speaker_role 字段）
SPEAKER_ELDER = "elder"  # 老人本人
SPEAKER_FAMILY = "family"  # 家属 / 照护者
SPEAKER_UNKNOWN = "unknown"  # 无法判断

VALID_SPEAKER_ROLES = (SPEAKER_ELDER, SPEAKER_FAMILY, SPEAKER_UNKNOWN)

# 各角色的"内容侧重"提示（供 prompt.py 注入；组长维护）
ROLE_HINTS = {
    SPEAKER_ELDER: "",
    SPEAKER_FAMILY: (
        "【当前说话人：家属/照护者】对方是在替家中老人咨询。称呼用“您”，"
        "侧重“您可以怎么照顾/观察/陪伴老人”，不要对家属本人做身体不适的安抚；"
        "涉及用药、急症、心理危机的安全红线与对老人本人一致，不得放松。"
    ),
    SPEAKER_UNKNOWN: "",
}


def detect_speaker_role(text: str, history: Optional[Iterable[str]] = None) -> str:
    """判断说话人角色。

    参数：
        text    —— 当前用户输入（必填）
        history —— 可选的历史用户消息，供实现参考（当前 stub 忽略）

    返回：SPEAKER_ELDER / SPEAKER_FAMILY / SPEAKER_UNKNOWN

    默认实现返回 SPEAKER_ELDER（＝维持现状）。
    """
    return SPEAKER_ELDER


def role_hint(role: str) -> str:
    """角色 -> 注入 system prompt 的提示片段（未知/老人返回空串）。"""
    return ROLE_HINTS.get(role, "")


__all__ = [
    "SPEAKER_ELDER",
    "SPEAKER_FAMILY",
    "SPEAKER_UNKNOWN",
    "VALID_SPEAKER_ROLES",
    "ROLE_HINTS",
    "detect_speaker_role",
    "role_hint",
]
