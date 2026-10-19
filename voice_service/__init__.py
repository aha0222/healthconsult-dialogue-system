"""小暖语音服务（独立进程 / 独立端口）。

本包是**独立于主后端**的语音能力服务，前端通过 HTTP / WebSocket 直连。

契约版本：v1（Day0 冻结，签名改动需 A 与组长双方确认）
职责边界：只做「音频 <-> 文本」，不做对话编排、不碰 safety、不碰大模型。
"""

import os
from pathlib import Path

# 读取项目根目录 .env，让语音服务与主后端共用同一份密钥配置（可选依赖）。
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # noqa: BLE001 - dotenv 缺失或 .env 不存在时不阻断启动
    pass

# numba 默认缓存目录在部分 Windows 机器上会因锁/慢盘导致 CosyVoice2 首次加载
# 长达 5-10 分钟。这里把缓存固定到 voice_service 目录下，加载可降到 10 秒左右。
_NUMBA_CACHE_DIR = os.environ.get(
    "NUMBA_CACHE_DIR",
    str(Path(__file__).resolve().parent / ".numba_cache"),
)
os.environ["NUMBA_CACHE_DIR"] = _NUMBA_CACHE_DIR

# numba 的 ensure_cache_path 会在缓存目录里创建/删除一个临时文件来探测可写性；
# 在部分 Windows 机器上（Defender 实时扫描）这个动作会卡 5-10 分钟，拖慢 CosyVoice2
# 首次加载。这里直接替换为“只建目录”，跳过临时文件探测，加载可从数分钟降到 10 秒级。
try:
    import numba.core.caching as _numba_caching

    def _ensure_cache_path_fast(self):
        os.makedirs(self.get_cache_path(), exist_ok=True)

    _numba_caching._CacheLocator.ensure_cache_path = _ensure_cache_path_fast
except Exception:  # noqa: BLE001 - numba 缺失或版本差异时不影响启动
    pass

__version__ = "0.1.0"
CONTRACT_VERSION = "v1"
SERVICE_NAME = "voice_service"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100
