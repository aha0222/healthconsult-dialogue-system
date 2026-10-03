"""下载本地 ASR / TTS 模型到 voice_service/models/（国内可用源，免翻墙）。

用法（在项目根目录）：
    python voice_service/download_models.py

来源：
  ASR 模型  GitHub（经代理）: 120660021/sherpa-asr-models release 的 model.int8.onnx
  ASR 词表  ModelScope: damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch 的 tokens.json
  TTS 音色  ModelScope: Trelis/piper-zh-cn-huayan-medium 的 model.onnx + model.onnx.json
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.request
from pathlib import Path

# 给所有网络请求设默认超时，避免某个代理半路卡死导致下载无限挂起
socket.setdefaulttimeout(60)

VOICE_DIR = Path(__file__).resolve().parent
MODELS_DIR = VOICE_DIR / "models"

GITHUB_PROXIES = [
    "https://gh-proxy.com/",
    "https://ghfast.top/",
]

ASR_MODEL_TAIL = (
    "https://github.com/120660021/sherpa-asr-models/"
    "releases/download/paraformer-zh-v1/model.int8.onnx"
)
ASR_TOKENS_MODEL = (
    "damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
)
TTS_MODEL = "Trelis/piper-zh-cn-huayan-medium"

ASR_MODEL_SIZE = 243371218
TTS_MODEL_SIZE = 63201294


def _modelscope_url(model: str, path: str) -> str:
    return f"https://modelscope.cn/api/v1/models/{model}/repo?Revision=master&FilePath={path}"


def _download(url: str, dest: Path, expected_size: int | None = None) -> None:
    if dest.exists() and (
        expected_size is None or dest.stat().st_size == expected_size
    ):
        print(f"  已存在，跳过：{dest.name}")
        return
    if dest.exists():
        print(
            f"  大小不符（现有 {dest.stat().st_size} 字节，预期 {expected_size}），"
            f"重新下载：{dest.name}"
        )
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  下载 {dest.name}")
    part = dest.with_name(dest.name + ".part")
    if part.exists():
        part.unlink()

    last = {"pct": -1.0}

    def reporthook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size <= 0:
            return
        done = block_num * block_size
        pct = min(100.0, done * 100.0 / total_size)
        if pct - last["pct"] < 1.0 and pct < 100.0:
            return
        last["pct"] = pct
        sys.stdout.write(
            f"\r    {pct:5.1f}%  ({done // (1024 * 1024)}/{total_size // (1024 * 1024)} MB)"
        )
        sys.stdout.flush()

    try:
        urllib.request.urlretrieve(url, part, reporthook=reporthook)
        if expected_size is not None and part.stat().st_size != expected_size:
            raise RuntimeError(
                f"下载不完整：{dest.name} 得到 {part.stat().st_size} 字节，"
                f"预期 {expected_size}"
            )
        part.replace(dest)
    except Exception:
        if part.exists():
            part.unlink()
        raise
    print("\n  完成")


def download_asr_model() -> None:
    dest = MODELS_DIR / "asr" / "model.int8.onnx"
    last_error = None
    for proxy in GITHUB_PROXIES:
        try:
            _download(proxy + ASR_MODEL_TAIL, dest, expected_size=ASR_MODEL_SIZE)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"    代理 {proxy} 失败：{exc}")
            if dest.exists():
                dest.unlink()
    raise last_error


def download_asr_tokens() -> None:
    dest = MODELS_DIR / "asr" / "tokens.txt"
    if dest.exists():
        print("ASR 词表已存在，跳过")
        return
    url = _modelscope_url(ASR_TOKENS_MODEL, "tokens.json")
    print("  下载并转换 ASR 词表 tokens.json -> tokens.txt")
    raw = urllib.request.urlopen(url).read()
    tokens = json.loads(raw.decode("utf-8"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    # sherpa-onnx 的词表格式是「symbol id」两列，每行一个 token。
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        for i, tok in enumerate(tokens):
            f.write(f"{tok} {i}\n")
    print(f"    完成，共 {len(tokens)} 个 token")


def download_tts() -> None:
    for src, name, expected in [
        ("model.onnx", "zh_CN-huayan-medium.onnx", TTS_MODEL_SIZE),
        ("model.onnx.json", "zh_CN-huayan-medium.onnx.json", None),
    ]:
        _download(
            _modelscope_url(TTS_MODEL, src),
            MODELS_DIR / "tts" / name,
            expected_size=expected,
        )


def main() -> None:
    print("== 1/3 ASR 模型 ==")
    download_asr_model()
    print("== 2/3 ASR 词表 ==")
    download_asr_tokens()
    print("== 3/3 TTS 音色 ==")
    download_tts()
    print(f"\n全部完成。模型目录：{MODELS_DIR}")


if __name__ == "__main__":
    main()
