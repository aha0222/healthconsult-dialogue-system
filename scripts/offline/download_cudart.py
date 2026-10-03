"""下载 llama.cpp 的 CUDA 运行时（cudart/cublas DLL）。

b6475 的 win-cuda 主包不含 cudart64/cublas64 DLL，缺它们时 llama.cpp 的
CUDA backend 加载失败、静默回退 CPU 推理（表现为显存占用极低、速度与 CPU 相当）。
本脚本带断点续传与重试循环，走 gh-proxy 镜像，下载后解压到 bin-cuda/。

用法：python scripts/offline/download_cudart.py [--check]
"""

import os
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
URL = ("https://gh-proxy.com/https://github.com/ggml-org/llama.cpp/"
       "releases/download/b6475/cudart-llama-bin-win-cuda-12.4-x64.zip")
ZIP_PATH = SCRIPT_DIR / "bin-cuda" / "cudart.zip"


def main() -> None:
    check_only = "--check" in sys.argv
    have = ZIP_PATH.stat().st_size if ZIP_PATH.exists() else 0
    print(f"当前已下载 {have/1e6:.0f}MB")
    if check_only:
        return

    done = False
    for attempt in range(30):
        have = ZIP_PATH.stat().st_size if ZIP_PATH.exists() else 0
        try:
            req = urllib.request.Request(
                URL, headers={"Range": f"bytes={have}-"} if have else {})
            with urllib.request.urlopen(req, timeout=30) as r, open(ZIP_PATH, "ab") as f:
                total = int(r.headers.get("Content-Length", 0)) + have
                while True:
                    chunk = r.read(1 << 19)
                    if not chunk:
                        break
                    f.write(chunk)
                    have += len(chunk)
            if total and have >= total:
                done = True
                print(f"下载完成 {have/1e6:.0f}MB", flush=True)
                break
        except Exception as exc:  # noqa: BLE001
            print(f"  第{attempt + 1}次中断于 {have/1e6:.0f}MB（{type(exc).__name__}），3秒续传…",
                  flush=True)
            time.sleep(3)
    if not done:
        raise SystemExit("多次重试后仍失败，可稍后重跑本脚本续传")

    with zipfile.ZipFile(ZIP_PATH) as z:
        z.extractall(SCRIPT_DIR / "bin-cuda")
        dlls = [n for n in z.namelist()
                if "cublas" in n.lower() or "cudart" in n.lower()]
    print("补齐 DLL:", dlls[:6], flush=True)
    ZIP_PATH.unlink()
    print("OK：重新启动 llama-server 后应看到 loaded CUDA backend / CUDA0 buffer")


if __name__ == "__main__":
    main()
