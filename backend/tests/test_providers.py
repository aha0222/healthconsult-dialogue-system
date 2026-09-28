"""providers.py 契约测试（Day0 空壳）。

固定两条不变量：
  1) 默认（OFFLINE_MODE 关闭）必须返回**现有云 LLM**，零回归；
  2) OFFLINE_MODE 开启但缺本地实现时，必须抛清晰错误，**不得静默降级**。
"""

import pytest

from backend.app import config as config_mod
from backend.app import providers
from backend.app.dialogue.llm_client import LLMClient


def test_get_llm_default_returns_cloud_client(monkeypatch):
    monkeypatch.setattr(config_mod, "get_settings", lambda: config_mod.Settings())
    llm = providers.get_llm()
    assert isinstance(llm, LLMClient)


def test_get_llm_offline_flag_without_local_impl_raises(monkeypatch):
    settings = config_mod.Settings()
    settings.offline_mode = True
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError) as exc:
        providers.get_llm()
    assert "local_llm" in str(exc.value)


def test_offline_mode_defaults_to_false():
    """开关必须默认关闭，否则会改变现有在线行为。"""
    assert config_mod.Settings().offline_mode is False
