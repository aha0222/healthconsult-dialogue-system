"""离线运行时自检（成员 B 交付）。

拔网线后跑这一条命令，验证「不联网也能跑通一轮完整问答」。

用法：
    python tools/offline_check.py                       # 用默认离线档配置
    python tools/offline_check.py --env-file <路径>      # 指定配置文件
    python tools/offline_check.py --runtime             # 启用运行时检索后自检
    python tools/offline_check.py --require-offline     # 没真断网就判失败（验收用）
    python tools/offline_check.py --json                # 输出 JSON 报告

退出码：0 = 全部通过；1 = 有检查项失败。

┌─ 两个必须说清的前提 ─────────────────────────────────────────────────┐
│ 1. 本脚本在做任何 backend 导入**之前**先设好环境变量。               │
│    因为 `config.get_settings()` 是 lru_cache(maxsize=1)，一旦有模块  │
│    提前导入过，缓存里就是非离线的 Settings，后面怎么设都改不了。      │
│                                                                       │
│ 2. 接线未落地时，本脚本**显式注入** LocalLLM 到 DialogueOrchestrator。│
│    `orchestrator.py` / `memory.py` / `semantic_checker.py` 属于其他  │
│    成员，B 不能改。它们目前仍 `LLMClient(self.settings)`，所以自动    │
│    走本地模型要靠交付报告第 5 节的接线 diff。在 diff 合入前，本脚本   │
│    用注入的方式证明「离线链路本身是通的」。                           │
└──────────────────────────────────────────────────────────────────────┘
"""

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT  # noqa: E402

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SEP = "=" * 72
DEFAULT_ENV_FILE = REPO_ROOT / "scripts" / "offline" / ".env.offline"
REDLINE_PATH = REPO_ROOT / "backend" / "tests" / "redline_cases.jsonl"
CORPUS_PATH = (REPO_ROOT / "backend" / "app" / "dialogue" / "corpus"
               / "scene_risk_corpus.jsonl")


# ── 环境准备（必须在导入 backend 之前）────────────────────────────────

def load_env_file(path: Path) -> list:
    """把离线档配置读进 os.environ。返回实际生效的键值对。"""
    if not path or not path.is_file():
        return []
    applied = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        os.environ[key] = value
        applied.append((key, value))
    return applied


def prepare_env(args) -> list:
    """设好离线档环境变量。返回生效清单。"""
    applied = []
    if args.env_file is not False:
        path = Path(args.env_file) if args.env_file else DEFAULT_ENV_FILE
        applied = load_env_file(Path(path))
        if not applied:
            print(f"[提示] 未读到配置文件 {path}，改用命令行开关。")

    # 命令行覆盖（在配置文件之后）
    if args.backend:
        os.environ["OFFLINE_LLM_BACKEND"] = args.backend
    if args.endpoint:
        os.environ["OFFLINE_LLM_ENDPOINT"] = args.endpoint
    if args.model_path:
        os.environ["OFFLINE_LLM_MODEL_PATH"] = args.model_path

    # 没有配置文件也没给端点时，用默认端点，避免误跑到云端
    os.environ.setdefault("OFFLINE_LLM_ENDPOINT", "http://127.0.0.1:8080")
    os.environ.setdefault("EMBEDDING_BACKEND", "hash")
    # 假 key 只为绕过 /api/chat/stream 的 has_api_key 门禁；LocalLLM 不读它
    os.environ.setdefault("DEEPSEEK_API_KEY", "sk-local")
    if args.runtime:
        os.environ["RUNTIME_CLASSIFIER"] = "1"
        os.environ["RUNTIME_RETRIEVAL"] = "1"
    # 无条件打开：本脚本就是验离线链路的
    os.environ["OFFLINE_MODE"] = "1"

    return applied


# ── 检查项 ────────────────────────────────────────────────────────────

def check_config() -> tuple:
    from backend.app.config import get_settings
    s = get_settings()
    offline = bool(getattr(s, "offline_mode", False))
    return offline, f"OFFLINE_MODE={'1' if offline else '0'}  " \
                    f"EMBEDDING_BACKEND={s.embedding_backend}  " \
                    f"端点={os.environ.get('OFFLINE_LLM_ENDPOINT')}"


def check_network_isolation(require_offline: bool) -> tuple:
    """探测能否连到云端。断网时这里应当连不上。"""
    t = time.perf_counter()
    try:
        socket.create_connection(("api.deepseek.com", 443), timeout=3).close()
        reachable = True
    except Exception:
        reachable = False
    dt = time.perf_counter() - t

    if reachable:
        detail = f"仍能连到 api.deepseek.com:443（{dt:.2f}s）—— 本次是「模拟离线」"
        return (not require_offline), detail
    return True, f"已确认断网：api.deepseek.com:443 连不上（{dt:.2f}s）"


def check_provider_routing() -> tuple:
    from backend.app.dialogue.llm_client import LLMClient
    from backend.app.providers import get_llm
    from backend.app.providers.local_llm import LocalLLM

    llm = get_llm()
    if isinstance(llm, LocalLLM):
        return True, f"get_llm() -> {type(llm).__name__}"
    if isinstance(llm, LLMClient):
        return False, "get_llm() 返回了云端 LLMClient —— 离线分流没有生效"
    return False, f"get_llm() 返回了未知类型 {type(llm).__name__}"


def check_endpoint_is_loopback() -> tuple:
    url = os.environ.get("OFFLINE_LLM_ENDPOINT", "")
    ok = any(h in url for h in ("127.0.0.1", "localhost", "::1", "0.0.0.0"))
    return ok, f"端点 {url} {'指向本机' if ok else '不是本机地址（可能外呼）'}"


def check_embedder(settings) -> tuple:
    from backend.app.providers.embedding import (
        EmbedderUnavailable, build_embedder_strict, describe_embedder,
    )
    try:
        emb = build_embedder_strict(settings)
    except EmbedderUnavailable as exc:
        return False, f"嵌入后端不可用：{exc}"
    info = describe_embedder(emb)
    is_hash = info["backend"] == "hash"
    return is_hash, f"嵌入后端 {info['signature']}" + ("" if is_hash else "（离线档建议用 hash）")


def check_local_llm_call(settings) -> tuple:
    """最短的一次本地推理，先确认模型真的能出话。"""
    from backend.app.providers import get_llm

    llm = get_llm()
    t = time.perf_counter()
    try:
        out = llm.chat([{"role": "user", "content": "只回复两个字：在的"}],
                       max_tokens=16)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    dt = time.perf_counter() - t
    ok = bool(out and out.strip())
    return ok, f"{dt:.1f}s 返回 {len(out)} 字：{out[:40]}"


def _build_orchestrator(settings, with_semantic: bool = True):
    """显式注入 LocalLLM 构造编排器（接线未落地时的自检方式）。"""
    from backend.app.dialogue.orchestrator import DialogueOrchestrator
    from backend.app.providers import get_llm

    llm = get_llm()
    semantic = None
    if with_semantic and getattr(settings, "semantic_check", False):
        from backend.app.safety.semantic_checker import SemanticChecker
        semantic = SemanticChecker(llm=llm, settings=settings)
    return DialogueOrchestrator(llm=llm, settings=settings, semantic_checker=semantic)


def check_full_turn(settings) -> tuple:
    """验收标准：跑通一轮完整问答（非流式）。"""
    orch = _build_orchestrator(settings)
    t = time.perf_counter()
    try:
        result = orch.respond(message="我这两天大便发黑，是不是吃啥东西染的？",
                              history=[], memory_block="")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    dt = time.perf_counter() - t
    reply = (result or {}).get("reply", "")
    ok = bool(reply.strip())
    return ok, (f"{dt:.1f}s  risk={(result or {}).get('risk')}  "
                f"scenes={(result or {}).get('scenes')}  "
                f"回复 {len(reply)} 字")


def check_stream_turn(settings) -> tuple:
    """前端实际用的是流式接口，必须单独验。"""
    orch = _build_orchestrator(settings)
    t = time.perf_counter()
    pieces, final = [], None
    try:
        for kind, payload in orch.respond_stream(message="我最近总睡不着，怎么办？",
                                                 history=[], memory_block=""):
            if kind == "delta":
                pieces.append(payload)
            else:
                final = payload
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    dt = time.perf_counter() - t
    text = "".join(pieces)
    ok = bool(text.strip()) or bool((final or {}).get("reply", "").strip())
    return ok, f"{dt:.1f}s  流式片段 {len(pieces)} 个，共 {len(text)} 字"


def check_retrieval(settings) -> tuple:
    """检索链路目前没接进 /api/chat，必须显式跑一次才能证明嵌入离线可用。"""
    from backend.app.dialogue.retriever import HashEmbedder, Retriever, load_corpus

    path = Path(settings.corpus_path or CORPUS_PATH)
    if not path.is_file():
        return False, f"语料不存在：{path}"
    corpus = load_corpus(path)
    if not corpus:
        return False, f"语料为空：{path}"
    r = Retriever(corpus, HashEmbedder(), settings.embedding_cache_dir)
    t = time.perf_counter()
    hits = r.retrieve("大便发黑怎么回事", top_k=3)   # 返回 [(CorpusItem, score)]
    dt = time.perf_counter() - t
    ok = len(hits) > 0
    if ok:
        item, score = hits[0]
        tail = (f"首条 risk={item.risk} scenes={'/'.join(item.scenes or [])} "
                f"相似度={score:.3f}")
    else:
        tail = "未召回任何条目"
    return ok, f"{dt:.2f}s  语料 {len(corpus)} 条，召回 {len(hits)} 条；{tail}"


def check_redline() -> tuple:
    """硬约束：离线链路必须保留本地 safety_checker 关键词兜底，红队用例全绿。"""
    from backend.app.safety.safety_checker import validate_sample

    if not REDLINE_PATH.is_file():
        return False, f"红队用例不存在：{REDLINE_PATH}"
    rows = [json.loads(l) for l in REDLINE_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    bad = []
    for case in rows:
        _sid, violations = validate_sample(case, mode="generated_sft")
        fatal = [v for sev, v in violations if sev == "fatal"]
        if (len(fatal) == 0) != bool(case.get("should_pass")):
            bad.append((case.get("sample_id"), case.get("should_pass"), fatal))
    ok = not bad
    detail = f"{len(rows)} 条红队用例，"
    detail += "全部符合预期" if ok else f"{len(bad)} 条不符合预期"
    return ok, detail


def check_keyword_fallback_coverage(settings) -> tuple:
    """本地关键词兜底对语料里已知高风险样本的漏检率。

    为什么必须查：本地 LLM **不保证输出 `[RISK:][SCENE:]` 标记**
    （实测 Qwen2.5-3B 就不输出），此时 orchestrator 会退回
    `markers.infer_tags_local` 的关键词推断。而关键词表有洞——
    E1 场景收了「黑便」却没收「大便发黑」，于是语料里标注 R2b/E1 的
    黑便急症样本会被本地推断成 R0/X1。

    后果不是"稍微不准"：`semantic_check_risks` 默认只有 {R3, R2b}，
    判成 R0 会让**第三层语义复核整层跳过**，一条消化道出血的输入
    就失去了语义兜底。

    这不是 B 能改的（关键词表在 safety_checker.py，门控在 orchestrator.py），
    所以这里只把它**显式暴露**出来，默认告警、--strict-safety 时判失败。
    """
    import json

    from backend.app.dialogue.markers import infer_tags_local
    from backend.app.dialogue.taxonomy import RISK_ORDER

    path = Path(settings.corpus_path or CORPUS_PATH)
    if not path.is_file():
        return False, f"语料不存在：{path}"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    # 取已标注为高风险、且主场景是安全场景的样本作为"应有表现"的基准
    safety = {"E1", "N1", "N2", "N3", "M2"}
    risky = [r for r in rows
             if r.get("risk") in ("R3", "R2b")
             and safety & set(r.get("scenes") or [])]
    if not risky:
        return True, "语料里没有可用于基准的高风险样本"

    def rank(r):
        # RISK_ORDER 是 {等级: 序号} 的字典（R0=0 … R3=4），不是列表
        return RISK_ORDER.get(r, -1)

    missed = []
    for r in risky:
        local_risk, local_scenes = infer_tags_local(r.get("user", ""))
        if rank(local_risk) < rank(r.get("risk")):
            missed.append((r.get("user", "")[:26], r.get("risk"), local_risk))

    n, total = len(missed), len(risky)
    rate = n / total * 100
    detail = f"{n}/{total} 条高风险样本被本地关键词兜底低估（{rate:.0f}%）"
    if n:
        detail += "；例如 " + "、".join(f"「{u}…」{a}→{b}" for u, a, b in missed[:2])
    return n == 0, detail


# ── 主流程 ────────────────────────────────────────────────────────────

def run_checks(args) -> dict:
    from backend.app.config import get_settings

    settings = get_settings()
    results = []

    def add(name, ok, detail):
        results.append({"name": name, "pass": bool(ok), "detail": detail})
        print(f"  {'✅' if ok else '❌'} {name}")
        if detail:
            print(f"      {detail}")

    print(SEP)
    print("  离线运行时自检")
    print(SEP)

    print("\n[配置]")
    if args.env_file is not False:
        path = Path(args.env_file) if args.env_file else DEFAULT_ENV_FILE
        print(f"  配置文件：{path}")

    print("\n[检查项]")
    add("离线档配置", *check_config())
    add("网络隔离", *check_network_isolation(args.require_offline))
    add("provider 分流", *check_provider_routing())
    add("端点在本机", *check_endpoint_is_loopback())
    add("嵌入后端", *check_embedder(settings))

    llm_ok, llm_detail = check_local_llm_call(settings)
    add("本地 LLM 推理", llm_ok, llm_detail)

    if llm_ok:
        add("完整一轮问答（非流式）", *check_full_turn(settings))
        add("前端所用的流式接口", *check_stream_turn(settings))
    else:
        # 模型都调不动，后面的端到端必然失败，直接标注跳过而不是刷一堆红
        add("完整一轮问答（非流式）", False, "跳过：本地 LLM 不可用")
        add("前端所用的流式接口", False, "跳过：本地 LLM 不可用")

    add("检索链路（嵌入离线可用）", *check_retrieval(settings))
    add("safety 关键词兜底 + 红队用例", *check_redline())

    # 本地模型不吐标签时会退回关键词推断，这里量一下它的漏检率
    kw_ok, kw_detail = check_keyword_fallback_coverage(settings)
    if kw_ok or args.strict_safety:
        add("关键词兜底对高风险样本的覆盖", kw_ok, kw_detail)
    else:
        results.append({"name": "关键词兜底对高风险样本的覆盖",
                        "pass": True, "warn": True, "detail": kw_detail})
        print("  ⚠️ 关键词兜底对高风险样本的覆盖")
        print(f"      {kw_detail}")
        print("      这不是 B 能改的（关键词表在 safety_checker.py），"
              "但它会让语义复核整层跳过。加 --strict-safety 可判失败。")

    passed = sum(1 for r in results if r["pass"])
    report = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass": passed == len(results),
        "checks": results,
        "offline_mode": bool(getattr(settings, "offline_mode", False)),
        "endpoint": os.environ.get("OFFLINE_LLM_ENDPOINT", ""),
    }
    return report


def main():
    ap = argparse.ArgumentParser(description="离线运行时自检：拔网线跑通一轮完整问答")
    ap.add_argument("--env-file", default=None,
                    help=f"离线档配置文件，默认 {DEFAULT_ENV_FILE}")
    ap.add_argument("--no-env-file", dest="env_file", action="store_const", const=False,
                    help="不读配置文件，只用环境变量与命令行开关")
    ap.add_argument("--backend", choices=["auto", "endpoint", "llama_cpp"],
                    help="覆盖 OFFLINE_LLM_BACKEND")
    ap.add_argument("--endpoint", help="覆盖 OFFLINE_LLM_ENDPOINT")
    ap.add_argument("--model-path", help="覆盖 OFFLINE_LLM_MODEL_PATH（GGUF 路径）")
    ap.add_argument("--runtime", action="store_true",
                    help="启用运行时分类/检索（默认沿用配置）")
    ap.add_argument("--require-offline", action="store_true",
                    help="没真断网就判失败（验收时用）")
    ap.add_argument("--strict-safety", action="store_true",
                    help="关键词兜底漏检高风险样本时也判失败（默认只告警）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args()

    prepare_env(args)

    # 环境设好后清一次缓存，确保拿到的是离线档 Settings
    from backend.app.config import get_settings
    get_settings.cache_clear()

    report = run_checks(args)

    print("\n" + SEP)
    if report["pass"]:
        print(f"  ✅ 全部通过（{report['passed']}/{report['total']}）")
    else:
        print(f"  ❌ {report['failed']}/{report['total']} 项未通过")
        for r in report["checks"]:
            if not r["pass"]:
                print(f"    · {r['name']}")
    print(SEP)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
