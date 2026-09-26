"""模型路由：按输入的高风险特征选择便宜或更强的模型。

路由只看输入（在调用 LLM 之前），用本地关键词分级器做廉价预判：
高风险（急症/心理/人身安全）走强模型，其余走便宜模型。
默认关闭（ROUTING_ENABLED=0），关闭时统一用 DEEPSEEK_MODEL。
"""

from ..config import Settings
from .markers import infer_risk_local

# 需要更强模型的高风险等级
STRONG_RISKS = {"R3", "R2b"}


def select_model(message: str, settings: Settings, risk: str | None = None) -> str:
    """返回本次调用应使用的模型名；risk 可由分类器预判传入，缺省再本地兜底。"""
    if not settings.routing_enabled:
        return settings.model
    risk = risk or infer_risk_local(message)
    return settings.strong_model if risk in STRONG_RISKS else settings.fast_model
