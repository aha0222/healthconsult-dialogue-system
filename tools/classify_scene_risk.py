"""场景 + 风险分级 CLI（关键词快路径 + LLM 兜底 + Retriever/Reranker）。

用法：
    # 单条
    python tools/classify_scene_risk.py --text "这两天总是忘记吃药，一天吃两次怎么办？"

    # 批量（JSONL，逐行取 user 字段）
    python tools/classify_scene_risk.py --input cases.jsonl --mode auto

    # 只跑关键词快路径（不调 LLM，可离线/进 CI）
    python tools/classify_scene_risk.py --input cases.jsonl --mode keyword

    # 强制走 LLM 分类
    python tools/classify_scene_risk.py --text "..." --mode llm

    # 预下载/预热嵌入模型与语料向量
    python tools/classify_scene_risk.py --setup
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings
from backend.app.dialogue.classifier import SceneRiskClassifier
from backend.app.safety.safety_checker import _extract_fields


def build_settings(args) -> Settings:
    settings = Settings.from_env()
    if args.corpus:
        settings.corpus_path = args.corpus
    if args.embedding_backend:
        settings.embedding_backend = args.embedding_backend
    if args.embedding_model:
        settings.embedding_model = args.embedding_model
    if args.top_k:
        settings.retriever_top_k = args.top_k
    if args.top_n:
        settings.reranker_top_n = args.top_n
    return settings


def load_inputs(path):
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(f"[警告] 第 {idx} 行解析失败，已跳过", file=sys.stderr)
                continue
            _sid, user, _assistant, _scene, _risk, _scenes = _extract_fields(row)
            user = (user or row.get("text") or "").strip()
            if user:
                items.append((row.get("id") or row.get("sample_id") or str(idx), user))
    return items


def run(args):
    settings = build_settings(args)
    classifier = SceneRiskClassifier(settings=settings)

    if args.setup:
        retriever = classifier.retriever
        retriever.ensure_vectors()
        print(
            json.dumps(
                {
                    "embedder": retriever.embedder.signature,
                    "corpus_size": len(retriever.corpus),
                    "corpus_path": settings.corpus_path,
                    "cache_dir": settings.embedding_cache_dir,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    mode = args.mode
    allow_llm = mode != "keyword" and not args.no_llm
    force_llm = mode == "llm"

    if args.text:
        result = classifier.classify(args.text, allow_llm=allow_llm, force_llm=force_llm)
        print(
            json.dumps(
                {"input": args.text, **result.as_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not args.input:
        print("[错误] 需要 --text 或 --input（或 --setup）", file=sys.stderr)
        raise SystemExit(2)

    results = []
    for item_id, user in load_inputs(args.input):
        result = classifier.classify(user, allow_llm=allow_llm, force_llm=force_llm)
        record = {"id": item_id, "input": user, **result.as_dict()}
        results.append(record)
        print(json.dumps(record, ensure_ascii=False))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            for record in results:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"[OK] 写入 {len(results)} 条 → {args.output}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="场景 + 风险分级（快路径 + LLM 兜底）")
    parser.add_argument("--text", help="单条输入文本")
    parser.add_argument("--input", help="批量输入 JSONL 路径")
    parser.add_argument("--output", help="结果输出 JSONL 路径（可选）")
    parser.add_argument(
        "--mode",
        choices=["auto", "keyword", "llm"],
        default="auto",
        help="auto=快路径+歧义兜底 | keyword=仅快路径 | llm=强制 LLM",
    )
    parser.add_argument("--no-llm", action="store_true", help="禁用 LLM 兜底")
    parser.add_argument("--setup", action="store_true", help="预下载模型并预热语料向量")
    parser.add_argument("--corpus", help="语料 JSONL 路径")
    parser.add_argument("--embedding-backend", choices=["local", "api", "hash"])
    parser.add_argument("--embedding-model", help="本地嵌入模型名或 API 模型名")
    parser.add_argument("--top-k", type=int, help="Retriever 召回数")
    parser.add_argument("--top-n", type=int, help="Reranker 保留数")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
