"""下载 Kokoro v1.1 中文模型与音色（走魔搭国内镜像）。

用法（在项目根目录）：
    python voice_service/download_kokoro.py

来源：ModelScope 的 AI-ModelScope/Kokoro-82M-v1.1-zh
输出：voice_service/models/kokoro/，并生成 MANIFEST.txt（大小 + SHA256）
"""

from __future__ import annotations

import hashlib
import json
import socket
import sys
import urllib.request
from pathlib import Path

VOICE_DIR = Path(__file__).resolve().parent
MODELS_DIR = VOICE_DIR / "models" / "kokoro"

MODELSCOPE = "AI-ModelScope/Kokoro-82M-v1.1-zh"
MODEL_SIZE = 327247856


def _url(path: str) -> str:
    return f"https://modelscope.cn/api/v1/models/{MODELSCOPE}/repo?Revision=master&FilePath={path}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _download(path: str, dest: Path, expected: int | None = None, retries: int = 1000) -> None:
    if dest.exists() and (expected is None or dest.stat().st_size == expected):
        print(f"  跳过（已存在）: {path}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last_err = None
    for attempt in range(1, retries + 1):
        existing = part.stat().st_size if part.exists() else 0
        if expected is not None and existing >= expected:
            if existing == expected:
                part.replace(dest)
                print(f"    完成 {dest.stat().st_size} 字节")
                return
            part.unlink()
            existing = 0
        headers = {}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        note = f"第 {attempt}/{retries} 次" + (f"，续传 {existing} 字节" if existing else "")
        print(f"  下载 {path}（{note}）")
        try:
            req = urllib.request.Request(_url(path), headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r, open(
                part, "ab" if existing else "wb"
            ) as f:
                remain = int(r.headers.get("Content-Length") or 0)
                total = existing + remain if remain else (expected or 0)
                done = existing
                while True:
                    block = r.read(1 << 20)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    if total:
                        sys.stdout.write(
                            f"\r    {done * 100 // total}%  ({done // (1 << 20)}/{total // (1 << 20)} MB)"
                        )
                        sys.stdout.flush()
            print()
            size = part.stat().st_size
            if expected is not None and size != expected:
                raise RuntimeError(f"大小不符：{path} 得到 {size}，预期 {expected}")
            part.replace(dest)
            print(f"    完成 {dest.stat().st_size} 字节")
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"    失败（保留续传文件，稍后重试）：{exc}")
    raise last_err


def main() -> None:
    socket.setdefaulttimeout(60)

    print("== 1/2 模型权重与配置 ==")
    _download("kokoro-v1_1-zh.pth", MODELS_DIR / "kokoro-v1_1-zh.pth", MODEL_SIZE)
    _download("config.json", MODELS_DIR / "config.json", 3228)
    _download("configuration.json", MODELS_DIR / "configuration.json", 72)

    print("== 2/2 中文音色（zf/zm 共 100 个）==")
    files_url = (
        f"https://modelscope.cn/api/v1/models/{MODELSCOPE}/repo/files"
        "?Revision=master&Recursive=true"
    )
    data = json.loads(urllib.request.urlopen(files_url).read().decode("utf-8"))
    voices = sorted(
        f["Path"]
        for f in data["Data"]["Files"]
        if f["Path"].startswith("voices/")
        and f["Path"].endswith(".pt")
        and ("zf_" in f["Path"] or "zm_" in f["Path"])
    )
    for p in voices:
        _download(p, MODELS_DIR / "voices" / p.split("/")[-1])

    print("== 记录校验清单 ==")
    items = [
        MODELS_DIR / "kokoro-v1_1-zh.pth",
        MODELS_DIR / "config.json",
        MODELS_DIR / "configuration.json",
    ] + sorted((MODELS_DIR / "voices").glob("*.pt"))
    with open(MODELS_DIR / "MANIFEST.txt", "w", encoding="utf-8") as fh:
        fh.write("# Kokoro v1.1 中文模型 · 文件清单与 SHA256\n")
        for p in items:
            if p.exists():
                rel = p.relative_to(MODELS_DIR).as_posix()
                fh.write(f"{_sha256(p)}  {p.stat().st_size}  {rel}\n")

    print(f"\n完成：模型 + {len(voices)} 个音色已就位，校验清单写入 models/kokoro/MANIFEST.txt")


if __name__ == "__main__":
    main()
