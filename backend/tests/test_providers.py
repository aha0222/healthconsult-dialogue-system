"""providers.py 契约测试。

固定四条不变量：
  1) 默认（OFFLINE_MODE 关闭）必须返回**现有云 LLM**，零回归；
  2) offline 开启但缺本地实现时，必须抛清晰错误，**不得静默降级**；
  3) offline 开启且实现可用时，返回的是 LocalLLM（构造必须惰性，否则主链路会反复加载模型）；
  4) 所有本地后端都不可用时，必须抛**点名每一条原因**的清晰错误，
     且**绝不允许**退回云端 LLMClient。

测试 2 的写法说明（成员 B 交付时改动）：
    原空壳假设「local_llm.py 不存在」，直接调用即抛 ImportError。
    B 交付该文件后这个前提不再成立，测试会失败。这里改为**人为制造**
    「实现缺失」这一前提（sys.modules 置 None 会让 import 抛 ImportError），
    断言与它保护的不变量原封不动。
"""

import sys

import pytest

from backend.app import config as config_mod
from backend.app import providers
from backend.app.dialogue.llm_client import LLMClient, LLMError


def test_get_llm_default_returns_cloud_client(monkeypatch):
    monkeypatch.setattr(config_mod, "get_settings", lambda: config_mod.Settings())
    llm = providers.get_llm()
    assert isinstance(llm, LLMClient)


def test_get_llm_offline_flag_without_local_impl_raises(monkeypatch):
    settings = config_mod.Settings()
    settings.offline_mode = True
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    # 模拟 B 未交付 / 导入失败：sys.modules 置 None 会让 import 抛 ImportError
    monkeypatch.setitem(sys.modules, "backend.app.providers.local_llm", None)

    with pytest.raises(RuntimeError) as exc:
        providers.get_llm()
    assert "local_llm" in str(exc.value)


def test_offline_mode_defaults_to_false():
    """开关必须默认关闭，否则会改变现有在线行为。"""
    assert config_mod.Settings().offline_mode is False


# ── 以下为成员 B 新增 ────────────────────────────────────────────────


def _offline_settings(monkeypatch):
    settings = config_mod.Settings()
    settings.offline_mode = True
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    return settings


def test_get_llm_offline_returns_local_impl(monkeypatch):
    """offline 开启且实现存在时返回 LocalLLM。"""
    from backend.app.providers.local_llm import LocalLLM

    _offline_settings(monkeypatch)
    llm = providers.get_llm()
    assert isinstance(llm, LocalLLM)
    assert not isinstance(llm, LLMClient)


def test_local_llm_construction_is_lazy(monkeypatch):
    """构造必须惰性：不连网、不加载模型。

    主链路里 `main.py` 一次启动就构造两个 LLM 实例，`MemoryManager` 更是每次请求
    都构造一次。若构造函数就去连端点或加载 GGUF，离线模式会每请求重载 2GB 模型。
    """
    from backend.app.providers.local_llm import LocalLLM

    # 指向一个必然拒绝连接的端口；构造仍须成功
    monkeypatch.setenv("OFFLINE_LLM_BACKEND", "endpoint")
    monkeypatch.setenv("OFFLINE_LLM_ENDPOINT", "http://127.0.0.1:1")
    llm = LocalLLM(_offline_settings(monkeypatch))
    assert llm is not None


def test_offline_backend_all_unavailable_raises_clear_error(monkeypatch):
    """所有本地后端都不可用 → 抛点名每条原因的错误，且不回退云端。

    场景：auto 模式下端点连不上（127.0.0.1:1 必然 ECONNREFUSED），
    且未安装 llama-cpp-python / 未配置模型路径。
    """
    from backend.app.providers.local_llm import LocalLLM

    monkeypatch.setenv("OFFLINE_LLM_BACKEND", "auto")
    monkeypatch.setenv("OFFLINE_LLM_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OFFLINE_LLM_MODEL_PATH", "")
    monkeypatch.delenv("OFFLINE_LLM_STRICT", raising=False)

    llm = LocalLLM(_offline_settings(monkeypatch))
    assert isinstance(llm, LocalLLM)

    with pytest.raises(LLMError) as exc:
        llm.chat([{"role": "user", "content": "你好"}])

    msg = str(exc.value)
    # 两条原因都要点名，便于定位
    assert "endpoint" in msg, f"错误未点名端点原因：{msg}"
    assert "llama_cpp" in msg, f"错误未点名进程内后端原因：{msg}"
    # 且绝不能悄悄回退到云端
    assert not isinstance(getattr(exc.value, "llm", None), LLMClient)


def test_local_llm_strict_mode_does_not_fall_back(monkeypatch):
    """OFFLINE_LLM_STRICT=1 时锁定 endpoint 后端，失败不尝试进程内。

    断言的是「失败原因列表里没有 llama_cpp 这一条」，而不是消息里没出现
    llama_cpp 这个词——消息末尾会给出「改用 llama_cpp 后端」的建议，那是
    正常提示，不代表发生了回退。失败原因的格式是 `后端名（异常类型: 详情）`。
    """
    from backend.app.providers.local_llm import LocalLLM

    monkeypatch.setenv("OFFLINE_LLM_BACKEND", "endpoint")
    monkeypatch.setenv("OFFLINE_LLM_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OFFLINE_LLM_STRICT", "1")

    llm = LocalLLM(_offline_settings(monkeypatch))
    with pytest.raises(LLMError) as exc:
        llm.chat([{"role": "user", "content": "你好"}])

    msg = str(exc.value)
    reasons = msg.split("失败原因 ——", 1)[-1].split("。", 1)[0]
    assert "endpoint（" in reasons, f"未记录端点失败原因：{reasons}"
    assert "llama_cpp（" not in reasons, f"strict 模式不应尝试进程内回退：{reasons}"


def test_local_llm_endpoint_base_url_has_v1(monkeypatch):
    """OpenAI SDK 不会自动补 /v1，缺失会拼出 /chat/completions 而 404。"""
    from backend.app.providers.local_llm import _with_v1

    assert _with_v1("http://127.0.0.1:8080") == "http://127.0.0.1:8080/v1"
    assert _with_v1("http://127.0.0.1:8080/") == "http://127.0.0.1:8080/v1"
    assert _with_v1("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080/v1"
    assert _with_v1("http://127.0.0.1:8080/v1/") == "http://127.0.0.1:8080/v1"
