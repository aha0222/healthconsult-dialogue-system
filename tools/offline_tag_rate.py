# -*- coding: utf-8 -*-
"""测量模型输出 [RISK] / [SCENE] 标记的比例与正确率（标签输出率）。

为什么单独量这个：本地小模型**不保证**吐标签，标记缺失时会退回关键词推断
（`backend/app/dialogue/markers.py`）。`tools/offline_check.py` 量的是那个兜底
的漏检率，本脚本量的是更上游的一环——模型自己有没有吐、吐得对不对。

用法（本地档先跑 scripts/offline/start.ps1 把端点起起来；在线档加 --online）：
    .venv/Scripts/python.exe -X utf8 tools/offline_tag_rate.py [样例数] [--label 档位名] [--endpoint URL]
    .venv/Scripts/python.exe -X utf8 tools/offline_tag_rate.py 177 --out scripts/offline/tag_rate.jsonl
    .venv/Scripts/python.exe -X utf8 tools/offline_tag_rate.py --cases eval177.jsonl --online

`--cases` 可换一份自带 expected_risk / expected_scenes 的 JSONL（默认 risk_cases.jsonl，39 条）。

`--out` 写逐条结果（id/user/want/model_risk/has_raw_tag/want_ok/dir），
`tools/run_all_gates.py` 的标签率门禁与 `tools/adopt_failure_cases.py` 都读这份文件。

样例取自 backend/tests/eval/risk_cases.jsonl（自带 expected_risk / expected_scenes）。
不注入检索样例——本脚本测的是模型的标记能力，不是检索效果。

**prompt 形态默认镜像线上**（2026-10-16 改）：线上对高风险用完整 SKILL.md、
对典型低风险(R0/R1)用精简 prompt。早期版本对本脚本所有样例一律套精简 prompt，
于是高风险（R2b → E1）在精简下更容易被标成 S1——**低估了最该守住的那一档**。
`--plain-prompt` 保留旧口径（一律按 prompt_compact 配置），用于与历史数据对齐。

**数据必须标注档位**：3B/CPU 与 7B/GPU 是两个答案，不得混用。
"""

import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backend.app.config import Settings  # noqa: E402
from backend.app.dialogue.classifier import SceneRiskClassifier  # noqa: E402
from backend.app.dialogue.markers import parse_marker  # noqa: E402
from backend.app.dialogue.prompt import build_system_prompt  # noqa: E402
from backend.app.dialogue.taxonomy import (  # noqa: E402
    HIGH_RISKS,
    RISK_ORDER,
    canonical_risk,
)
from backend.app.providers import get_llm  # noqa: E402

CASES = REPO / "backend" / "tests" / "eval" / "risk_cases.jsonl"


def _arg(flag: str, default: str) -> str:
    if flag in sys.argv:
        return sys.argv[sys.argv.index(flag) + 1]
    return default


def _rank(value) -> int:
    return RISK_ORDER.get(str(value), -1)


def direction(model_risk, want) -> str:
    """模型等级相对标注的方向，供失败集分流（偏低优先修）。"""
    if _rank(model_risk) < _rank(want):
        return "偏低"
    if _rank(model_risk) > _rank(want):
        return "偏高"
    return "一致"


def _compact_for(classifier, user: str, settings) -> bool:
    """复刻 orchestrator._compose_system_prompt 的 prompt 形态选择。

    classifier 为 None（--plain-prompt）时退回旧口径：一律按 PROMPT_COMPACT 配置。
    """
    if classifier is None:
        return bool(getattr(settings, "prompt_compact", True))
    if not getattr(settings, "prompt_compact", True):
        return False
    try:
        # 线上实时链路同样不额外调 LLM：只用关键词快路径预判
        classification = classifier.classify(user, allow_llm=False)
    except Exception:  # 预判失败不阻断测量，退回完整 prompt（安全优先）
        return False
    risk = classification.risk
    is_high = bool(getattr(classification, "ambiguous", False)) or bool(
        risk and risk in HIGH_RISKS
    )
    return not is_high and risk in ("R0", "R1")


def main() -> None:
    n = int(_arg("--n", next((a for a in sys.argv[1:] if a.isdigit()), "20")))
    out_path = _arg("--out", "")
    online = "--online" in sys.argv
    label = _arg("--label", "在线档（云端）" if online else "未标注档位")
    endpoint = _arg("--endpoint", "http://127.0.0.1:8090")

    if online:
        # 在线档：用 .env 里的真实 key（config 导入时已 load_dotenv），走云端模型。
        # 同一个指标换个档位量一次——它自己的纪律就是"数据必须标注档位"。
        os.environ.update({"OFFLINE_MODE": "0"})
    else:
        os.environ.update({
            "OFFLINE_MODE": "1",
            "OFFLINE_LLM_BACKEND": "endpoint",
            "OFFLINE_LLM_ENDPOINT": endpoint,
            "DEEPSEEK_API_KEY": "sk-local",
            "EMBEDDING_BACKEND": "hash",
        })
    settings = Settings.from_env()   # 必须用 from_env：Settings() 只给默认值（offline_mode=False）
    llm = get_llm(settings)

    plain = "--plain-prompt" in sys.argv
    classifier = None if plain else SceneRiskClassifier(settings=settings)

    cases_path = Path(_arg("--cases", str(CASES)))
    cases = [json.loads(line) for line in
             cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = cases[:n]

    print(f"档位：{label}    端点：{endpoint}    样例：{len(cases)} 条")
    print("system prompt："
          + ("旧口径，一律按 PROMPT_COMPACT（--plain-prompt）" if plain
             else "镜像线上（高风险=完整 / 典型低风险 R0-R1=精简）")
          + "，不注入检索样例")
    print("=" * 74)

    risk_emitted = scene_emitted = 0
    risk_correct = scene_correct = 0
    errors = []
    latencies = []
    details = []

    for i, case in enumerate(cases, 1):
        user = case.get("user") or case.get("message") or ""
        exp_risk = case.get("expected_risk")
        exp_scenes = case.get("expected_scenes") or []
        system = build_system_prompt(compact=_compact_for(classifier, user, settings))
        t0 = time.time()
        try:
            raw = llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=getattr(settings, "max_tokens", 400),
            )
        except Exception as exc:  # 调用失败也要计数，不能悄悄跳过
            errors.append(f"{case.get('id')}: {type(exc).__name__}: {exc}")
            print(f"  {i:3d} ✗ 调用失败 {case.get('id')}")
            continue
        dt = time.time() - t0
        latencies.append(dt)

        _body, risk, scenes = parse_marker(raw)
        has_risk = risk is not None
        has_scene = bool(scenes)
        risk_emitted += has_risk
        scene_emitted += has_scene
        ok_risk = has_risk and canonical_risk(risk) == canonical_risk(exp_risk)
        ok_scene = has_scene and set(exp_scenes) & set(scenes)
        risk_correct += bool(ok_risk)
        scene_correct += bool(ok_scene)
        details.append((
            case.get("id"), exp_risk, risk, exp_scenes, scenes, dt,
            {
                "id": case.get("id"),
                "user": user,
                "want": exp_risk,
                "want_scenes": exp_scenes,
                "model_risk": risk,
                "model_scenes": scenes,
                "has_raw_tag": has_risk,
                "want_ok": bool(ok_risk),
                "dir": direction(risk, exp_risk),
                "tier": label,
            },
        ))
        print(f"  {i:3d} {'OK ' if ok_risk else 'MISS'} risk={risk or '无'} "
              f"(期望 {exp_risk})  场景={scenes or '无'}  {dt:.1f}s")

    done = len(latencies)
    total = len(cases)
    print("=" * 74)
    if not done:
        print("没有任何一条成功调用，无法给出标签输出率。")
        for e in errors[:5]:
            print("  ", e)
        raise SystemExit(1)

    print(f"标签输出率（成功 {done}/{total} 条）")
    print(f"  RISK  标记：{risk_emitted}/{done} = {risk_emitted / done:.0%}")
    print(f"  SCENE 标记：{scene_emitted}/{done} = {scene_emitted / done:.0%}")
    print(f"  两者都有  ：{sum(1 for d in details if d[2] and d[4])}/{done} = "
          f"{sum(1 for d in details if d[2] and d[4]) / done:.0%}")
    print()
    print("标记正确率（在**已输出标记**的样本内）")
    if risk_emitted:
        print(f"  RISK  correct：{risk_correct}/{done} = {risk_correct / done:.0%}"
              f"（占成功样本）")
    if scene_emitted:
        print(f"  SCENE overlap：{scene_correct}/{done} = {scene_correct / done:.0%}"
              f"（占成功样本，与期望场景有交集即算对）")
    print()
    print(f"单条调用耗时：均值 {statistics.mean(latencies):.1f}s  "
          f"中位 {statistics.median(latencies):.1f}s  "
          f"范围 {min(latencies):.1f}–{max(latencies):.1f}s")
    if errors:
        print(f"\n调用失败 {len(errors)} 条：")
        for e in errors[:5]:
            print("  ", e)
    print()
    print("注：RISK 正确率按 canonical_risk 比较（R2b/R2a 保留小写，不能大写化）；"
          "场景按交集计，因为一条样例可命中多个场景。")

    if out_path:
        # 逐条落盘：回归门禁与三路吸收都读这份文件，字段名不要随手改。
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n".join(json.dumps(d[6], ensure_ascii=False) for d in details) + "\n",
            encoding="utf-8",
        )
        print(f"逐条结果已写 {target}")


if __name__ == "__main__":
    main()
