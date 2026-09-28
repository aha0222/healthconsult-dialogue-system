"""`python -m voice_service` 启动入口（独立进程）。

环境变量：
  VOICE_HOST  监听地址，默认 127.0.0.1
  VOICE_PORT  监听端口，默认 8100
"""

from __future__ import annotations

import os

import uvicorn

from . import DEFAULT_HOST, DEFAULT_PORT


def main() -> None:
    host = os.environ.get("VOICE_HOST", DEFAULT_HOST)
    port = int(os.environ.get("VOICE_PORT", str(DEFAULT_PORT)))
    print(f"[voice_service] 启动：http://{host}:{port}  （探活 /health）")
    uvicorn.run("voice_service.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
