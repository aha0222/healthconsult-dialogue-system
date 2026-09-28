"""小暖语音服务（独立进程 / 独立端口）。

本包是**独立于主后端**的语音能力服务，前端通过 HTTP / WebSocket 直连。

契约版本：v1（Day0 冻结，签名改动需 A 与组长双方确认）
职责边界：只做「音频 <-> 文本」，不做对话编排、不碰 safety、不碰大模型。
"""

__version__ = "0.1.0"
CONTRACT_VERSION = "v1"
SERVICE_NAME = "voice_service"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100
