"""小暖四版人格 · 回复生成器（公平对比版）

为 43 个测试场景 × 4 版人格 × N 次重复生成回复，供后续盲评 / 配对比较使用。

── 为什么要剥离基础人格 ──────────────────────────────────────────
SKILL.md 第 1 节把「温婉邻居型」写死为默认人格核心。如果直接拿
SKILL.md + 其他三版的语气修饰去生成，另外三版实际上是在「被要求推翻
系统已经声明的基础设定」，存在系统性劣势——这会让人格比较失真。

本脚本先从 SKILL.md 中剥离人格相关小节（人格核心 / 称呼习惯 / 语气参数），
得到中立的「安全骨架」，再为四版人格各自附加等价的完整人格定义
（取自 skills/.../docs/personalities.md）。

用 --keep-base-persona 可切回旧口径（SKILL.md 原样 + 语气修饰），
作为对照组复现初版报告的方法。

── 为什么不注入风险等级 ──────────────────────────────────────────
初版脚本对 R3 场景额外注入「必须建议120」的提示。这会人为抹平各人格在
风险识别上的差异，而风险识别恰恰是要测的维度之一。故本脚本不注入任何
风险提示，SKILL.md 本身已要求模型自行分类。

── 关于可复现性 ─────────────────────────────────────────────────
LLM 采样不保证逐位可复现（DeepSeek 不承诺 seed 语义）。本脚本把每次调用的
原始回复、模型名、温度、时间戳全部落盘，复现性由「原始数据 + 脚本 + 参数」
共同保证，而非依赖种子。

用法：
    python tools/generate_personality_responses.py --dry-run        # 只看提示词构造
    python tools/generate_personality_responses.py                  # 正式生成
    python tools/generate_personality_responses.py --runs 2 --workers 4
    python tools/generate_personality_responses.py --keep-base-persona
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT, SKILL_MD, SKILL_DIR, EXAMPLES_DIR, RESULTS_DIR  # noqa: E402

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# .env 必须在读取环境变量之前加载
try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from backend.app.dialogue.markers import parse_marker  # noqa: E402

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

TEMPERATURE = 0.7
MAX_TOKENS = 800

# DeepSeek 默认开启思考模式：慢、忽略 temperature，而且推理 token 会吃掉
# max_tokens，导致正文（content）为空。必须显式关闭——与
# backend/app/dialogue/llm_client.py 的 _extra_body() 保持一致。
THINKING_ENABLED = os.environ.get("THINKING_ENABLED", "0").lower() in ("1", "true", "yes")


def _extra_body() -> dict:
    return {"thinking": {"type": "enabled" if THINKING_ENABLED else "disabled"}}

PERSONALITY_ORDER = ["温婉邻居型", "贴心闺女型", "素朴家常型", "从容守护型"]

SCENARIOS_PATH = EXAMPLES_DIR / "personality_test_scenarios.jsonl"
PERSONALITIES_DOC = SKILL_DIR / "docs" / "personalities.md"

# ── 旧口径对照组的语气修饰（原样复制自 batch_test_personalities.py）────
LEGACY_MODIFIERS = {
    "温婉邻居型": "",
    "贴心闺女型": """

【本轮对话的语气风格】
请以"贴心闺女型"风格回复——像自家贴心小闺女，说话亲切软糯直白，撒娇式关心。常用词：咱、您呀、可不能、乖啊、听话、您答应我哈、您说是不是嘛。句式短、口语化，偶尔用反问句。"先安抚→再解释→再建议→再兜底"的格式可以更自在灵活。偶尔用"您老人家"带有宠溺意味。""",
    "素朴家常型": """

【本轮对话的语气风格】
请以"素朴家常型"风格回复——像年轻时在厂里干过的退休大姐，朴素实在接地气，用生活类比解释医学概念。常用词：我跟您说啊、这有啥、甭担心、咱老百姓、实在不行、踏实。称呼可用老哥、老姐。句式短、口语化，偶尔带北方话味道。不用叠词，不撒娇。""",
    "从容守护型": """

【本轮对话的语气风格】
请以"从容守护型"风格回复——像经验丰富的老护士长，淡定从容，话不多但每句都有用。常用词：您放心、我帮您梳理一下、一步一步来、第一第二第三。不主动加称呼前缀，不用叠词不用语气词。回复结构化，语速偏慢。""",
}

PERSONA_HEADING_RE = re.compile(r"^## 版本[一二三四]：([^（(]+)", re.M)

_write_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════
# 提示词构造
# ══════════════════════════════════════════════════════════════


def build_safety_skeleton(skill_text: str, strip_examples: bool = False) -> str:
    """从 SKILL.md 剥离人格相关小节，得到中立的安全骨架。"""
    # 删除「### 人格核心」到「### 身份守护」之间的全部内容
    skeleton = re.sub(r"### 人格核心.*?(?=### 身份守护)", "", skill_text, flags=re.S)
    # 标题里残留的默认人格名也去掉，避免任何暗示
    skeleton = skeleton.replace("人格核心：温婉邻居型", "人格核心")
    # 正文剥离干净后，还要处理 YAML frontmatter 的 description——
    # 那里同样写着「以温婉邻居型人格为核心」
    skeleton = re.sub(
        r"(?m)^description:.*$",
        "description: 面向养老陪护场景的健康咨询助手回复规范，严格守护医疗安全边界。",
        skeleton,
    )

    if strip_examples:
        skeleton = re.sub(r"## 10\. 正面示例.*?(?=## 11\.)", "", skeleton, flags=re.S)

    # 守门：骨架里绝不允许残留任何人格名，否则实验作废
    leaked = [p for p in PERSONALITY_ORDER if p in skeleton]
    if leaked:
        raise RuntimeError(f"安全骨架中仍残留人格名，会污染对照实验：{leaked}")

    return skeleton.strip()


def load_personalities(doc_path: Path) -> dict:
    """从 personalities.md 解析四版人格的完整定义。

    注意：小节必须在「下一个二级标题」处结束，而不是「下一个人格标题」处。
    否则最后一版会把其后的「四版人格对比总览」「推荐策略」一并吞掉——
    而「推荐策略」里写着"首选：温婉邻居型"，会让该版人格在提示词里读到
    其他版本更受偏好的暗示，直接污染实验。
    """
    lines = doc_path.read_text(encoding="utf-8").splitlines(keepends=True)
    heads = [
        (i, m.group(1).strip())
        for i, line in enumerate(lines)
        if (m := PERSONA_HEADING_RE.match(line))
    ]
    if not heads:
        raise RuntimeError(f"未能在 {doc_path} 中解析到人格小节")

    h2_lines = [i for i, line in enumerate(lines) if line.startswith("## ")]

    result = {}
    for start, name in heads:
        end = next((j for j in h2_lines if j > start), len(lines))
        body = "".join(lines[start + 1:end])  # 跳过标题行本身
        body = re.sub(r"\n?-{3,}\s*$", "", body).strip()  # 去掉尾部分隔线
        result[name] = body
    return result


def build_system_prompt(
    skill_text: str,
    skeleton: str,
    persona_name: str,
    persona_defs: dict,
    keep_base_persona: bool = False,
    strip_examples: bool = False,
) -> str:
    if keep_base_persona:
        # 旧口径：SKILL.md 原样 + 简短语气修饰
        return skill_text + LEGACY_MODIFIERS.get(persona_name, "")

    persona_def = persona_defs.get(persona_name)
    if not persona_def:
        raise RuntimeError(f"personalities.md 中找不到人格：{persona_name}")
    return (
        skeleton
        + "\n\n---\n\n# 你的人格设定\n\n以下是你说话的方式，安全规则不受人格影响，永远优先。\n\n"
        + persona_def
    )


# ══════════════════════════════════════════════════════════════
# 数据读写
# ══════════════════════════════════════════════════════════════


def load_scenarios(path: Path) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_done_keys(out_path: Path) -> set:
    """支持断点续跑：已完成的 (场景, 人格, 第几次) 组合跳过。"""
    done = set()
    if not out_path.exists():
        return done
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                done.add((r["scenario_id"], r["personality"], r["run"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def append_record(out_path: Path, record: dict) -> None:
    with _write_lock:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════════════
# LLM 调用
# ══════════════════════════════════════════════════════════════


def call_llm(client, system_content: str, history: list, user_text: str, retries: int = 3):
    messages = list(history or []) + [{"role": "user", "content": user_text}]
    last_err = None
    for attempt in range(retries):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system_content}] + messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                extra_body=_extra_body(),
            )
            latency_ms = int((time.time() - t0) * 1000)
            raw = (resp.choices[0].message.content or "").strip()
            if not raw:
                # 空回复不是"成功"，是必须重试的失败——
                # 早期版本把 76 条空回复当成成功入库，直接污染了整批数据。
                last_err = "模型返回空回复（检查 thinking 是否已关闭）"
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                continue
            usage = getattr(resp, "usage", None)
            return {
                "raw_reply": raw,
                "latency_ms": latency_ms,
                "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
                "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            }, None
        except Exception as exc:  # noqa: BLE001 - 需要兜住所有网络/SDK 异常
            last_err = str(exc)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None, last_err


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="四版人格回复生成器")
    parser.add_argument("--scenarios", default=str(SCENARIOS_PATH), help="场景 JSONL 路径")
    parser.add_argument("--runs", type=int, default=2, help="每格重复生成次数")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    parser.add_argument("--output", default="", help="输出路径（默认自动生成带时间戳）")
    parser.add_argument("--keep-base-persona", action="store_true",
                        help="旧口径对照：不去人格化，复现初版报告方法")
    parser.add_argument("--strip-examples", action="store_true",
                        help="同时剥离 SKILL.md 第 10 节正面示例（温婉语气示范）")
    parser.add_argument("--dry-run", action="store_true", help="只打印提示词构造，不调 API")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个场景（调试用）")
    parser.add_argument("--ids", default="", help="只跑指定场景 id，逗号分隔（如 s11,s24,s43）")
    args = parser.parse_args()

    skill_text = SKILL_MD.read_text(encoding="utf-8")
    skeleton = build_safety_skeleton(skill_text, strip_examples=args.strip_examples)
    persona_defs = load_personalities(PERSONALITIES_DOC)
    scenarios = load_scenarios(Path(args.scenarios))
    if args.ids:
        wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
        scenarios = [s for s in scenarios if s["id"] in wanted]
    if args.limit:
        scenarios = scenarios[: args.limit]

    missing = [p for p in PERSONALITY_ORDER if p not in persona_defs]
    if missing:
        print(f"[错误] personalities.md 缺少人格定义：{missing}")
        sys.exit(1)

    variant = "legacy" if args.keep_base_persona else "fair"
    total = len(scenarios) * len(PERSONALITY_ORDER) * args.runs

    print("=" * 68)
    print(f"  小暖四版人格 · 回复生成（{variant} 口径）")
    print(f"  场景 {len(scenarios)} × 人格 {len(PERSONALITY_ORDER)} × 重复 {args.runs} = {total} 条")
    print(f"  模型 {MODEL} | 温度 {TEMPERATURE} | 并发 {args.workers}")
    print("=" * 68)

    if args.dry_run:
        sys_content = build_system_prompt(
            skill_text, skeleton, PERSONALITY_ORDER[1], persona_defs,
            args.keep_base_persona, args.strip_examples,
        )
        print(f"\n安全骨架长度: {len(build_safety_skeleton(skill_text))} 字")
        print(f"骨架是否仍含「温婉邻居型」: {'是（有问题）' if '温婉邻居型' in skeleton else '否（已剥离）'}")
        print(f"\n--- 人格定义解析结果 ---")
        for name in PERSONALITY_ORDER:
            print(f"  {name}: {len(persona_defs[name])} 字")
        print(f"\n--- 示例系统提示词（{PERSONALITY_ORDER[1]}）前 400 字 ---")
        print(sys_content[:400])
        print(f"\n--- 结尾 300 字 ---")
        print(sys_content[-300:])
        return

    if not API_KEY:
        print("\n[错误] 未找到 DEEPSEEK_API_KEY。请在仓库根目录的 .env 中配置，或设置环境变量。")
        sys.exit(1)

    from openai import OpenAI

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        out_path = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = RESULTS_DIR / f"personality_gen_{variant}_{ts}.jsonl"

    done = load_done_keys(out_path)
    if done:
        print(f"\n检测到已有结果，跳过 {len(done)} 条已完成组合（断点续跑）")

    # 为每个人格预构建系统提示词（同名人格内容一致，避免重复拼接）
    sys_prompts = {
        name: build_system_prompt(
            skill_text, skeleton, name, persona_defs,
            args.keep_base_persona, args.strip_examples,
        )
        for name in PERSONALITY_ORDER
    }

    jobs = []
    for sc in scenarios:
        for pname in PERSONALITY_ORDER:
            for run in range(1, args.runs + 1):
                if (sc["id"], pname, run) in done:
                    continue
                jobs.append((sc, pname, run))

    print(f"待生成: {len(jobs)} 条 → {out_path}\n")

    ok = fail = 0
    failures = []

    def work(job):
        sc, pname, run = job
        result, err = call_llm(
            client, sys_prompts[pname], sc.get("history"), sc["user"]
        )
        return sc, pname, run, result, err

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(work, j) for j in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            sc, pname, run, result, err = fut.result()
            if err:
                fail += 1
                failures.append({"scenario_id": sc["id"], "personality": pname, "run": run, "error": err})
                print(f"  [{i}/{len(jobs)}] {sc['id']} {pname} #{run} 失败: {err[:60]}")
                continue

            reply, marker = parse_marker(result["raw_reply"])
            record = {
                "scenario_id": sc["id"],
                "risk": sc.get("risk"),
                "category": sc.get("category"),
                "legacy_anchor": sc.get("legacy", False),
                "personality": pname,
                "run": run,
                "user": sc["user"],
                "has_history": bool(sc.get("history")),
                "raw_reply": result["raw_reply"],
                "reply": reply,
                "marker": marker,
                "marker_missing": marker is None,
                "model": MODEL,
                "temperature": TEMPERATURE,
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
                "latency_ms": result["latency_ms"],
                "variant": variant,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            append_record(out_path, record)
            ok += 1
            flag = "" if marker else "  [未标记]"
            print(f"  [{i}/{len(jobs)}] {sc['id']} {pname} #{run} → {marker or '—'}{flag}")

    print("\n" + "=" * 68)
    print(f"  生成完成：成功 {ok} | 失败 {fail}")
    print(f"  输出：{out_path}")
    print("=" * 68)

    if failures:
        fail_path = out_path.with_suffix(".failures.json")
        fail_path.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  失败明细：{fail_path}")
        print("  重新运行同一命令即可续跑（已完成的会自动跳过）")


if __name__ == "__main__":
    main()
