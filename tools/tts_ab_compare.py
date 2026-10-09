# -*- coding: utf-8 -*-
"""Kokoro 音色 A/B 试听：同一批语料逐句合成，输出 wav + 一个可直接点的对比 HTML。

默认对比三条链路：
  A = v1.0  hexgrad/Kokoro-82M        zf_xiaoxiao   （当前线上配置）
  B = v1.1  hexgrad/Kokoro-82M-v1.1-zh zf_001
  C = v1.1  hexgrad/Kokoro-82M-v1.1-zh zf_002
  D = v1.1  hexgrad/Kokoro-82M-v1.1-zh zf_003
    E = v1.1  hexgrad/Kokoro-82M-v1.1-zh zf_001   关自写数字改写（留给 cn2an 读）
    F = v1.1  hexgrad/Kokoro-82M-v1.1-zh zf_001   外加持长句减速

线上默认 = B 的音色（zf_001）+ F 的长句减速（2026-10-10 拍板）；本工具用于复核该结论。

跑法（GPU 机）：
  .venv/Scripts/python.exe -X utf8 tools/tts_ab_compare.py
产物在 out 目录：A_*.wav … E_*.wav + index.html（浏览器打开逐句试听）。

首次会经 HF_ENDPOINT（默认 hf-mirror.com）拉取 v1.1 模型与音色，约 320MB。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from voice_service import pronunciation  # noqa: E402
from voice_service.tts import (  # noqa: E402
    SENTENCE_PAUSE_SEC,
    _trim_silence,
    split_sentences,
)

# 语料：覆盖真实回复里的问候、多音字、阿拉伯数字、小数、方剂名、长句、急症安全话术
CORPUS = [
    ("01_问候", "爷爷您好，我是小暖。今天感觉怎么样？"),
    ("02_多音字", "您昨晚睡得怎么样？要是睡个好觉不容易，白天就少睡点，长时间躺着也不舒服。"),
    ("03_口干", "嘴里发干的话，多喝点温水，菜里少放点盐。"),
    ("04_睡不着", "睡不着别着急，我陪您说说话，慢慢就困了。"),
    ("05_教做操", "我教您做一套简单的操，坐着就能做。"),
    ("06_血压数字", "刚量的血压是 150/95，比平时高了，您先歇十分钟再量一次。"),
    ("07_剂量小数", "地高辛每天吃一次，每次 0.125 毫克，别自己加量。"),
    ("08_计数", "早晚各量一次血压，连续记 7 天，下次带给我看看。"),
    ("09_长句", "您这个年纪，血压高一点也常见，别太紧张。平时吃得淡一点，觉睡够，慢慢走一走，都会帮着稳下来的。"),
    ("10_急症", "您要是胸口闷、疼得厉害，或者出冷汗，马上打 120，别等着。"),
    ("11_英文缩写", "这药跟维生素D一起吃没事，回头再做个CT看看骨头。"),
    # 44 字、带逗号但不超过切句阈值 → 整句一次合成（音素数 >83，会触发"赶"）
    ("12_长单句", "然后咱们就报警，打 120 让警察同志来处理，这种事今天就得办，拖不得。"),
]

VARIANTS = [
    # (标签, repo, 音色, 自写数字规则, 长句减速)
    ("A", "hexgrad/Kokoro-82M", "zf_xiaoxiao", True, False),
    ("B", "hexgrad/Kokoro-82M-v1.1-zh", "zf_001", True, False),
    ("C", "hexgrad/Kokoro-82M-v1.1-zh", "zf_002", True, False),
    ("D", "hexgrad/Kokoro-82M-v1.1-zh", "zf_003", True, False),
    ("E", "hexgrad/Kokoro-82M-v1.1-zh", "zf_001", False, False),
    ("F", "hexgrad/Kokoro-82M-v1.1-zh", "zf_001", True, True),
]


def make_speed_callable(base: float):
    """官方 make_zh.py 的减速曲线：音素数越多放得越慢，治长句末尾"赶"。

    v1.1-zh 训练数据基本在 100 token 以内，长输入会赶；按音素数递减语速。
    """

    def fn(len_ps: int) -> float:
        factor = 1.0 if len_ps <= 83 else max(0.6, 1 - (len_ps - 83) / 500)
        return base * factor

    return fn


def build_pipe(repo_id: str, device: str):
    from kokoro import KPipeline

    from voice_service import tts as vtts

    # 与线上一致：v1.1 走官方 en_callable，英文缩写不当未知音素
    en_callable = vtts._kokoro_en_callable() if repo_id.endswith("v1.1-zh") else None
    return KPipeline(lang_code="z", repo_id=repo_id, device=device, en_callable=en_callable)


def synth_sentence(pipe, text: str, voice: str, speed):
    """单句 → float32 ndarray（裁掉首尾静音）。speed 可为浮点或 len_ps 回调。"""
    import numpy as np
    import torch

    with torch.inference_mode():
        chunks = list(pipe(text, voice=voice, speed=speed))
    audios = [_trim_silence(c.audio.numpy(), 24000) for c in chunks if c.audio is not None]
    audios = [a for a in audios if len(a)]
    if not audios:
        return np.zeros(1, dtype="float32")
    pause = np.zeros(int(24000 * SENTENCE_PAUSE_SEC), dtype=audios[0].dtype)
    merged = []
    for i, a in enumerate(audios):
        merged.append(a)
        if i < len(audios) - 1:
            merged.append(pause)
    return np.concatenate(merged)


def synth_line(pipe, text: str, voice: str, speed, convert_numbers: bool):
    import numpy as np

    pieces = split_sentences(text, convert_numbers)
    segs = [synth_sentence(pipe, s, voice, speed) for s, _ in pieces]
    if not segs:
        return np.zeros(1, dtype="float32")
    gap = np.zeros(int(24000 * SENTENCE_PAUSE_SEC), dtype="float32")
    out = []
    for i, s in enumerate(segs):
        out.append(s)
        if i < len(segs) - 1:
            out.append(gap)
    return np.concatenate(out)


def write_wav(path: Path, samples) -> None:
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setframerate(24000)
        w.setsampwidth(2)
        w.setnchannels(1)
        w.writeframes(pcm)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "scratch" / "kokoro_ab"))
    ap.add_argument("--speed", type=float, default=0.9)
    ap.add_argument("--device", default="")
    args = ap.parse_args()

    import torch

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    pronunciation.apply()

    pipes = {}
    files = {}  # (label, case_id) -> filename
    for label, repo, voice, conv, slow in VARIANTS:
        key = (repo, device)
        if key not in pipes:
            t = time.time()
            pipes[key] = build_pipe(repo, device)
            print(f"[pipe] {repo} on {device} ({time.time() - t:.0f}s)", flush=True)
        pipe = pipes[key]
        speed = make_speed_callable(args.speed) if slow else args.speed
        for case_id, text in CORPUS:
            t = time.time()
            samples = synth_line(pipe, text, voice, speed, conv)
            name = f"{label}_{case_id}.wav"
            write_wav(out_dir / name, samples)
            dur = len(samples) / 24000
            print(f"  {name}  {dur:.1f}s audio / {time.time() - t:.1f}s wall", flush=True)
            files[(label, case_id)] = name

    # ── 对比页 ────────────────────────────────────────────────────────
    head = "".join(
        f"<th>{label}<br><small>{voice}{'' if conv else ' · 不转数字'}{' · 长句减速' if slow else ''}</small></th>"
        for label, _, voice, conv, slow in VARIANTS
    )
    rows = []
    for case_id, text in CORPUS:
        cells = "".join(
            f'<td><audio controls preload="none" src="{files[(label, case_id)]}"></audio></td>'
            for label, _, _, _, _ in VARIANTS
        )
        rows.append(f"<tr><td class=case><b>{case_id}</b><br>{text}</td>{cells}</tr>")
    html = f"""<!doctype html><meta charset=utf-8>
<title>Kokoro A/B 试听</title>
<style>
 body{{font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:24px}}
 table{{border-collapse:collapse}} th,td{{border:1px solid #ddd;padding:8px;vertical-align:top}}
 th{{background:#f5f5f5}} .case{{max-width:320px}}
 audio{{width:210px}}
</style>
<h1>Kokoro 音色 A/B 试听</h1>
<p>逐行同一句话对比。A=当前线上配置；B/C/D=v1.1 三个女声；E=v1.1 zf_001 但不做数字改写（交给 G2P 的 cn2an）；F=v1.1 zf_001 + 长句减速（官方 make_zh.py 曲线）。</p>
<table><tr><th>语料</th>{head}</tr>
{chr(10).join(rows)}
</table>
"""
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"\nOK -> {(out_dir / 'index.html').as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
