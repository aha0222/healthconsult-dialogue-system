"""下载 CosyVoice2-0.5B 推理所需文件与 wetext 文本归一化资源。

只下载推理必需文件，跳过训练/导出用的大文件（约 4GB，而不是整仓约 5.6GB）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import requests
from modelscope import snapshot_download

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models" / "cosyvoice2"
WETEXT_DIR = BASE_DIR / "models" / "wetext"
MODEL_BASE = "https://modelscope.cn/models/iic/CosyVoice2-0.5B/resolve/master"

TOPLEVEL_FILES = [
    "cosyvoice2.yaml",
    "llm.pt",
    "flow.pt",
    "hift.pt",
    "campplus.onnx",
    "speech_tokenizer_v2.onnx",
]


def _download_toplevel() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name in TOPLEVEL_FILES:
        out = MODEL_DIR / name
        if out.exists():
            print(f"已存在，跳过：{out}")
            continue
        tmp = out.with_suffix(out.suffix + ".part")
        existing = tmp.stat().st_size if tmp.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        url = f"{MODEL_BASE}/{name}"
        print(f"下载 {name} ...")
        with requests.get(url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            mode = "ab" if r.status_code == 206 else "wb"
            with open(tmp, mode) as fh:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        fh.write(chunk)
        tmp.replace(out)
        print(f"完成：{out}")


def _download_blanken() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print("下载 CosyVoice-BlankEN（中文多音字消歧前端）...")
    snapshot_download(
        "iic/CosyVoice2-0.5B",
        local_dir=str(MODEL_DIR),
        allow_file_pattern=[r"CosyVoice-BlankEN/.*"],
    )


def _download_wetext() -> None:
    if (WETEXT_DIR / "zh" / "tn" / "tagger.fst").exists():
        print(f"已存在，跳过：{WETEXT_DIR}")
        return
    WETEXT_DIR.mkdir(parents=True, exist_ok=True)
    print("下载 wetext 文本归一化资源（git）...")
    subprocess.check_call(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "https://www.modelscope.cn/pengzhendong/wetext.git",
            str(WETEXT_DIR),
        ]
    )


def main() -> int:
    _download_blanken()
    _download_toplevel()
    _download_wetext()
    print("COSYVOICE2_DOWNLOAD_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
