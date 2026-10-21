"""风险等级 → 播报语气（语速 / 音高 / 语气指令）。

背景：TTS 模型本身不会"听出"这是急事，语气只能由我们指定。

- **CosyVoice2** 支持自然语言指令（`inference_instruct2`），可以直接要求
  "用沉重、关切的语气说"，这是最贴切的做法，优先使用。
- **Kokoro / Piper** 没有任何语气维度：一个 `.pt` 就是一个说话人，
  "笑着说话"是音色自带的，模型里没有可选的情绪开关。只能退而求其次：
  放慢语速 + 用 `apply_pitch` 把整体音高压低，听感上从"明快"变成"低沉"。
- **云端 CosyVoice v3** 是自回归大模型，语气跟着文本内容走，实测已经正常，
  因此不做任何改动。
"""

from __future__ import annotations

CRISIS_RISKS = frozenset({"R3"})

# 急症场景：稍微慢一点；语速只往下调，调高会让引擎的时长模型失配、念得发飘。
CRISIS_SPEED = 0.82
# 仅给无指令能力的引擎（Kokoro/Piper）用的音高代理。
CRISIS_PITCH = 0.94
# CosyVoice2 的语气指令（写在指令位，不进正文）
CRISIS_INSTRUCT = "请用沉重、低落、关切的语气说这句话，语速放慢，不要带笑意。"


def tts_params(speed: float, risk: str | None = None) -> tuple[float, float]:
    """基线语速 + 风险等级 -> (语速, 音高)。非急症场景原样返回。"""
    base = max(0.5, min(2.0, float(speed or 1.0)))
    if risk in CRISIS_RISKS:
        return max(0.5, min(1.0, base * CRISIS_SPEED)), CRISIS_PITCH
    return base, 1.0


def instruct_for(risk: str | None = None) -> str | None:
    """风险等级 -> CosyVoice2 语气指令；普通场景不传（保持参考音色）。"""
    return CRISIS_INSTRUCT if risk in CRISIS_RISKS else None
