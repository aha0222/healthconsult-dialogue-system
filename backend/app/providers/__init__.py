"""LLM provider 抽象（Day0 契约，签名冻结）。

┌─ 为什么要有这一层 ────────────────────────────────────────────────────┐
│ 现状：主后端直接 new 出 dialogue.llm_client.LLMClient（云 LLM）。      │
│ 目标：离线运行时换成本地 LLM，但**不改变任何调用点**。                 │
│ 做法：所有调用点改成 get_llm()，由本模块决定返回谁。                   │
└──────────────────────────────────────────────────────────────────────┘

契约：get_llm() 返回的对象必须实现两条方法（与现 LLMClient 完全一致）：
    chat(messages, temperature=None, max_tokens=None, model=None) -> str
    chat_stream(messages, temperature=None, max_tokens=None, model=None) -> Iterator[str]

默认行为 = **现有云 LLM**，保证零回归。
B 的交付标准：
  1) 新增 providers/local_llm.py，实现 LocalLLM（同签名）；
  2) 由 config.Settings 新增的 OFFLINE_MODE 决定返回云还是本地；
  3) 在线路径与默认行为**不允许改变**，缺依赖时必须抛出清晰错误，不得静默降级。
"""

from __future__ import annotations


def get_llm(settings=None):
    """返回当前生效的 LLM 客户端。

    默认（OFFLINE_MODE 未开启 / 未配置）＝ 现有云 LLM（现状）。
    OFFLINE_MODE 开启时＝本地 LLM（B 新增实现）。

    参数 settings（成员 B 新增，**向后兼容**）：
        不传时读 `get_settings()` 单例，与原契约完全一致；stub 的两条契约
        测试都是无参调用，仍然通过。
        传时用调用方自己的 Settings。

        为什么需要它：四个调用点各自持有 `self.settings`，而
        `tools/eval_risk.py:51` 是用 CLI 的 --api-key/--base-url/--model
        构造 `Settings(**overrides)` 再传进 orchestrator 的。若强制无参，
        这些 CLI 覆盖会**静默失效**（改用全局 .env），而单元测试覆盖不到
        ——测试都直接注入了 fake llm。
    """
    from ..config import get_settings
    from ..dialogue.llm_client import LLMClient

    settings = settings or get_settings()

    # getattr 兜底：组长尚未在 Settings 加 offline_mode 时，安全回退到在线行为。
    if getattr(settings, "offline_mode", False):
        try:
            from .local_llm import LocalLLM  # noqa: PLC0415 —— B 交付的新文件
        except ImportError as exc:  # 缺实现时给出可诊断的报错，不静默降级
            raise RuntimeError(
                "OFFLINE_MODE 已开启，但缺少本地 LLM 实现 "
                "backend/app/providers/local_llm.py（应由 B 交付）。"
            ) from exc
        return LocalLLM(settings)

    return LLMClient(settings)


__all__ = ["get_llm"]
