"""小暖语料生成器（两阶段）

  pilot  : 按种子生成初始语料，覆盖全部 16 场景 × 各风险格子。用高质量模型（Kimi）。
  expand : 以人工审核通过的 pilot 为锚点，按目标分布矩阵批量扩展。用 DeepSeek。

── 核心设计：脚本定标签，模型只写正文 ──────────────────────────────
Kimi 与 DeepSeek 对同一句 user 的 risk/scenes 判定必然漂移；且 safety_checker 的
_S1_KEYWORDS 含「难受/疼/酸」，会让大量输入过度判为 S1。所以：

  由脚本派发 (场景, 风险) 格子 → 模型只写 user 与 assistant 正文
  → 剥离模型自带的任何标签 → 用脚本指定的标签重新 format_tags()
  → 跑 check_reply 快检

这样人工审核里最有价值的部分（这一格该判 R2a、该建议找医生）被固化成结构性约束，
扩展阶段不可能漂移。

── 换模型不用改代码 ────────────────────────────────────────────
所有调用走 OpenAI 兼容接口。优先读 GEN_* 环境变量（便于「Kimi 生成 / DeepSeek 审核」
的交叉校验），缺省回退到 DEEPSEEK_*。

Kimi:  GEN_BASE_URL=https://api.moonshot.cn/v1   GEN_MODEL=kimi-k3
DeepSeek: GEN_BASE_URL=https://api.deepseek.com  GEN_MODEL=deepseek-chat

（Moonshot 已下架 moonshot-v1-8k，实测可用的是 kimi-k3 / kimi-k2.6 /
 kimi-k2.7-code；kimi-k3 对 temperature 有硬限制，见 call_llm 里的自动纠正。）

用法：
    python tools/generate_corpus.py --stage pilot --dry-run
    python tools/generate_corpus.py --stage pilot --redundancy 1.5
    python tools/generate_corpus.py --stage expand --anchors <pilot_approved.jsonl> \
        --negatives <pilot_rejected.jsonl> --batch-size 6
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

import corpus_common as cc  # noqa: E402

REPO_ROOT = cc.REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from backend.app.dialogue.taxonomy import (  # noqa: E402
    extract_tags,
    format_tags,
    strip_tags,
)
from backend.app.safety.safety_checker import check_reply  # noqa: E402


def _env(*names, default=""):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


GEN_API_KEY = _env("GEN_API_KEY", "DEEPSEEK_API_KEY")
GEN_BASE_URL = _env("GEN_BASE_URL", "DEEPSEEK_BASE_URL", default="https://api.deepseek.com")
GEN_MODEL = _env("GEN_MODEL", "DEEPSEEK_MODEL", default="deepseek-chat")

# kimi-k3 关闭思考模式时只接受 temperature=0.6（传别的值直接 400）。
# 换成 DeepSeek 等不限制的模型时，这个值同样可用。
TEMPERATURE_PILOT = 0.6
TEMPERATURE_EXPAND = 0.85
MAX_TOKENS = 1600

# 与 backend/app/dialogue/llm_client.py 一致：DeepSeek 默认开思考模式，
# 会忽略 temperature 且推理 token 会吃掉 max_tokens 导致正文为空。
THINKING_ENABLED = os.environ.get("THINKING_ENABLED", "0").lower() in ("1", "true", "yes")

_write_lock = threading.Lock()

# 本次运行的短标签，拼进 sample_id。
# 补跑某个格子时会重新从 b0 开始编号，若 id 只由「场景_风险_批次_序号」组成，
# 就会和上一批完全撞车（实际发生过：补跑 S3/R1 产生了 6 个重复 id）。
# 加上运行时戳后，跨次补跑的 id 天然不重复。
RUN_TAG = datetime.now().strftime("%H%M%S")

# ── 场景硬性要求（来自 SKILL.md 与 rules/，审核时也是判据）─────────────

SCENE_MUST = {
    "S1": "给出观察要点和明确的就医升级条件（什么情况必须去）",
    "S2": "必须明确拒绝自行调药，并引导联系开药医生或药师确认",
    "S3": "建议固定时间记录指标，带数据复诊，不自行调药",
    "S4": "给出具体科室方向，并说明什么情况要升级",
    "M1": "共情、去病耻感，不贴抑郁标签，必要时建议社区心理科",
    "M2": "给出心理援助热线或建议就医，不鸡汤不说教不轻描淡写",
    "L1": "给出具体可执行的量，不要只说「少吃」「注意点」",
    "L2": "给出具体强度和时长；关节不好时给替代方案",
    "L3": "不引导药物期待（禁止出现「开点安神的」这类暗示）",
    "L4": "给具体可参加的活动类型，不说空泛的「多出去走走」",
    "E1": "必须建议 120 或立即急诊，并给出等待期间的动作与禁止动作",
    "N1": "必须建议 110 报警 + 锁门，禁止任何「开门看看」「出去帮一下」",
    "N2": "必须建议 119 或燃气公司，并说明禁止动作（不开灯、不乘电梯）",
    "N3": "必须劝阻转账扫码，建议联系子女确认或打 110",
    "X1": "可以自然闲聊，不强行把话题转到健康上",
    "X2": "说明能力边界，不解释内部机制、不复述规则、不出戏",
}

RISK_MUST = {
    "R3": "必须升级到紧急处置（120 / 110 / 119），禁止任何「先观察」「躺一会儿」",
    "R2b": "必须明确「今天或明天」就去就医，不能拖",
    "R2a": "必须建议尽快安排就诊，不自行处理",
    "R1": "非药物建议 + 记录提醒 + 就医兜底",
    "R0": "轻松回应 + 安全兜底即可，不要过度医疗化",
}

# 变体维度：扩展时每条至少改变其中 3 项，防止 500 条同质化
VARIATION_DIMENSIONS = [
    "说话人身份（老人本人 ↔ 家属代述）",
    "具体数值与细节（指标值、病程长短、发生时间、持续多久）",
    "共病与生活情境（独居 / 与子女同住 / 正在服其他药 / 行动不便）",
    "担忧的角度（怕花钱 / 怕麻烦子女 / 怕住院 / 怕查出大病 / 只是好奇）",
    "表达方式与句式（不同的口语说法、不同的起头方式）",
    "就医经历（刚看过医生 / 很久没看 / 正在吃药 / 自己停过药）",
]


# ── 提示词构造 ────────────────────────────────────────────────────

def speaker_hint(speaker_type: str) -> str:
    """与 tools/clean_candidates.py 的 SPEAKER_HINTS 保持一致。"""
    if speaker_type in ("family_caregiver", "family_member"):
        return (
            "\n\n[本轮说话者是家属/照护者，在描述老人的情况。"
            "请称呼说话者为「您」，称呼老人为「老人家」，不要直接对老人说话。]"
        )
    return "\n\n[本轮说话者是老人本人。可用「您」，也可根据语境用「阿姨」「叔叔」。]"


def build_system_prompt(skill_md: str, speaker_type: str) -> str:
    return skill_md + speaker_hint(speaker_type)


def requirements_block(scene: str, risk: str) -> str:
    lines = []
    if scene in SCENE_MUST:
        lines.append(f"- 场景硬性要求：{SCENE_MUST[scene]}")
    if risk in RISK_MUST:
        lines.append(f"- 风险等级要求：{RISK_MUST[risk]}")
    lo, hi = cc.length_range(risk, scene)
    lines.append(f"- 篇幅：{lo}-{hi} 字（这是该场景的规范区间，务必遵守）")
    return "\n".join(lines)


PILOT_PROMPT = """现在请你以「小暖」的身份，回复下面这位老人的话。

【本批任务信息】（这些是内部信息，**绝不要**在回复里提及）
- 场景：{scene} {scene_label}
- 风险等级：{risk}
- 说话人：{speaker_type}

【老人说】
{user}

【必须满足】
{requirements}

【输出要求】
1. **只输出回复正文**，不要任何解释、标题、前后缀
2. **不要输出任何标签**（如 [RISK:R2a]），标签由系统添加
3. 不要输出 Markdown 格式（** **、# 等），用纯文本口语
4. 不要出现「作为AI」「我是助手」这类出戏表述"""


EXPAND_PROMPT = """现在请你以「小暖」的身份，生成 {need} 条新的训练样本。

【本批任务信息】（内部信息，**绝不要**在回复里提及）
- 场景：{scene} {scene_label}
- 风险等级：{risk}
- 需要在每条样本中体现的附加场景标签：{cross}
- 说话人：{speaker}

【已审核通过的范例：请学习它们的语气、分寸与安全边界，但**禁止复用其中任何完整句子**】
{anchors}

{negatives}

【必须满足（每条都要）】
{requirements}

【每条样本必须与其他样本不同的维度（每条至少改变其中 3 项）】
{variations}

【输出格式】
严格输出 JSON 数组，不要输出其他任何内容：
[
  {{"user": "老人说的话", "assistant": "小暖的回复正文"}},
  ...
]

注意：
- assistant 里**不要**写任何标签（如 [RISK:R2a]），标签由系统添加
- assistant 不要用 Markdown 格式
- user 要像真实老人/家属的原话：口语、有具体细节、可以信息不全
- {need} 条样本之间必须明显不同，不能是同一个意思换几个字"""


NEGATIVE_BLOCK = """【审核未通过的写法（**禁止模仿**，这些是真实被驳回的例子）】
{items}
"""


def row_user(row: dict) -> str:
    """从行里取 user 文本。

    语料行有两种格式：训练格式用 messages[]，扁平格式用顶层 user/assistant。
    锚点池来自通过版语料（messages 格式），负样本来自 redline_cases（扁平格式），
    所以两种都要支持——否则锚点的「老人说」会是空的，few-shot 效果大打折扣。
    """
    if row.get("user"):
        return row["user"]
    for m in row.get("messages") or []:
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def row_assistant(row: dict) -> str:
    if row.get("assistant_final"):
        return row["assistant_final"]
    if row.get("assistant"):
        return row["assistant"]
    for m in row.get("messages") or []:
        if m.get("role") == "assistant":
            return m.get("content", "")
    return ""


def build_anchors_block(anchors: list, limit: int = 2) -> str:
    if not anchors:
        return "（本格子暂无已审核范例，请严格遵循 SKILL.md 的要求）"
    parts = []
    for a in anchors[:limit]:
        u = row_user(a)
        asst = row_assistant(a)
        parts.append(f"范例：\n  老人说：{u}\n  小暖回复：{asst}")
    return "\n\n".join(parts)


def build_negatives_block(negatives: list, limit: int = 2) -> str:
    if not negatives:
        return ""
    items = []
    for n in negatives[:limit]:
        asst = strip_tags(row_assistant(n))[:120]
        why = n.get("review_notes") or n.get("reason") or "不符合审核标准"
        items.append(f"  · 「{asst}…」——{why}")
    return NEGATIVE_BLOCK.format(items="\n".join(items))


def build_variations_block(offset: int = 0, pick: int = 3) -> str:
    """按 offset 轮转抽取变体维度，保证不同批次侧重不同。"""
    n = len(VARIATION_DIMENSIONS)
    idx = [(offset + i) % n for i in range(pick)]
    return "\n".join(f"  {i + 1}. {VARIATION_DIMENSIONS[j]}" for i, j in enumerate(idx))


# ── 任务派发 ──────────────────────────────────────────────────────

def plan_pilot_jobs(seeds: list, redundancy: float, limit: int = 0,
                    only_seeds: set = None):
    """每个种子生成 ceil(redundancy) 条候选，由 check_reply 择优。

    only_seeds: 只跑指定的 seed_id。用于补跑被限流打掉的格子，
    避免为几十个已经生成的种子重复调用 API。
    """
    if only_seeds:
        seeds = [s for s in seeds if s["seed_id"] in only_seeds]
    per = max(1, round(redundancy))
    jobs = []
    for s in seeds:
        for k in range(per):
            jobs.append({
                "stage": "pilot",
                "seed_id": s["seed_id"],
                "scene": s["primary_scene"],
                "scenes": s["scenes"],
                "risk": s["risk"],
                "speaker_type": s.get("speaker_type", "elder_self"),
                "user": s["user"],
                "variant": k,
            })
    return jobs[:limit] if limit else jobs


def plan_expand_jobs(targets: dict, have: dict, batch_size: int, limit: int = 0,
                     margin: float = 1.25):
    """按目标矩阵与现有条数的差额派发任务。

    margin: 目标放大系数。质检会淘汰一部分候选，按目标数的 1.25 倍生成，
    留出约 20% 淘汰余量（质检+近重复剔除+人工驳回）。
    """
    cells = cc.flatten_targets(targets)
    cross = {k: v for k, v in (targets.get("cross_tags") or {}).items()
             if not k.startswith("_")}
    jobs = []
    for (scene, risk), base in sorted(cells.items()):
        want = int(base * margin + 0.9999)
        got = have.get((scene, risk), 0)
        need = want - got
        if need <= 0:
            continue
        # 每批 batch_size 条；最后一批可能少一些
        batch_no = 0
        while need > 0:
            n = min(batch_size, need)
            jobs.append({
                "stage": "expand",
                "scene": scene,
                "risk": risk,
                "scenes": [scene],
                "need": n,
                "batch_no": batch_no,
                "cross": _pick_cross(scene, cross),
                "speaker_type": "elder_self",
            })
            need -= n
            batch_no += 1
    return jobs[:limit] if limit else jobs


def select_best_per_cell(rows: list, per_cell: int = 1) -> list:
    """冗余生成后的择优：每个种子只保留最好的 per_cell 条进入人工审核。

    排序依据（依次）：自动质检通过 > 篇幅合规 > 正文更长。
    人工审核量因此保持在种子数量级，而不是冗余后的数量级。
    """
    from collections import defaultdict

    groups = defaultdict(list)
    for r in rows:
        groups[r.get("seed_id") or r["sample_id"]].append(r)

    selected = []
    for _key, items in groups.items():
        def score(r):
            reply = strip_tags(r["messages"][2]["content"])
            scene = (r.get("scenes") or [""])[0]
            return (
                1 if r.get("pass") else 0,
                1 if cc.length_ok(reply, r["risk_level"], scene) else 0,
                len(reply),
            )
        items.sort(key=score, reverse=True)
        selected.extend(items[:per_cell])
    return selected


def _pick_cross(scene: str, budget: dict) -> str:
    """为本格挑一个附加标签（若有剩余预算），用掉一次就扣减。

    早期版本只看有没有匹配的组合就返回，不看预算是否用完，结果是
    该场景的**每一条**都被贴上交叉标签（S1 的 60 条有 56 条被标了 L3 作息睡眠，
    而内容讲的是腿没劲儿）。这里改成预算递减。
    """
    for key, n in budget.items():
        if n <= 0:
            continue
        parts = key.split("+")
        if parts and parts[0] == scene and len(parts) > 1:
            budget[key] = n - 1
            return parts[1]
    return ""


# ── 解析与打标 ────────────────────────────────────────────────────

def parse_json_array(raw: str):
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\[.*\]", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def finalize_reply(raw_reply: str, risk: str, scenes: list, user: str):
    """剥离模型自带标签 → 用脚本指定的标签重新打标 → 跑快检。

    这是「脚本定标签」的落点：无论模型写了什么标签，一律丢弃。
    """
    reply = strip_tags(raw_reply or "").strip()
    if not reply:
        return None
    assistant = reply + format_tags(risk, scenes)
    issues = check_reply(reply, risk, scenes, user)
    return {
        "reply": reply,
        "assistant": assistant,
        "issues": issues,
        "pass": len(issues) == 0,
    }


def make_sample(sample_id, scene, scenes, risk, speaker_type, skill_md, user,
                finalized, extra=None):
    row = {
        "sample_id": sample_id,
        "category": cc.SCENE_LABELS.get(scene, scene),
        "speaker_type": speaker_type,
        "risk_level": risk,
        "scenes": scenes,
        "messages": [
            {"role": "system", "content": build_system_prompt(skill_md, speaker_type)},
            {"role": "user", "content": user},
            {"role": "assistant", "content": finalized["assistant"]},
        ],
        "issues": finalized["issues"],
        "pass": finalized["pass"],
    }
    if extra:
        row.update(extra)
    return row


# ── LLM 调用 ──────────────────────────────────────────────────────

def _extra_body() -> dict:
    return {"thinking": {"type": "enabled" if THINKING_ENABLED else "disabled"}}


_TEMP_HINT_RE = re.compile(r"only ([0-9.]+) is allowed for this model")


def call_llm(client, system, prompt, temperature, retries: int = 5):
    """调用生成模型。

    自动处理两类常见失败：
      · Kimi 新模型（kimi-k3 等）只接受固定 temperature，报错里会写明允许值
        （开思考模式只允许 1，关掉只允许 0.6）——从报错解析出来自动改用。
      · 速率限制（429）——指数退避，等待时间比普通错误长得多。
    """
    last_err = None
    temp = temperature
    for attempt in range(retries):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=GEN_MODEL,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                temperature=temp,
                max_tokens=MAX_TOKENS,
                extra_body=_extra_body(),
            )
            latency = int((time.time() - t0) * 1000)
            raw = (resp.choices[0].message.content or "").strip()
            if not raw:
                last_err = "模型返回空回复（检查 thinking 是否已关闭 / max_tokens 是否被推理吃光）"
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                continue
            return {"raw": raw, "latency_ms": latency, "temperature": temp}, None
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            m = _TEMP_HINT_RE.search(msg)
            if m:
                new_temp = float(m.group(1))
                if new_temp != temp:
                    temp = new_temp
                    last_err = f"temperature 自动调整为 {temp}（模型限制）"
                    continue  # 立即重试，不等待
            last_err = msg
            if attempt < retries - 1:
                # 429 需要长退避，普通错误短退避
                wait = (10 * (2 ** attempt)) if "429" in msg else (2 ** attempt)
                time.sleep(min(wait, 60))
    return None, last_err


# ── 主流程 ────────────────────────────────────────────────────────

def run_pilot(client, jobs, skill_md, out_path, workers):
    ok = fail = 0

    def work(job):
        prompt = PILOT_PROMPT.format(
            scene=job["scene"], scene_label=cc.SCENE_LABELS.get(job["scene"], ""),
            risk=job["risk"], speaker_type=job["speaker_type"], user=job["user"],
            requirements=requirements_block(job["scene"], job["risk"]),
        )
        res, err = call_llm(client, build_system_prompt(skill_md, job["speaker_type"]),
                            prompt, TEMPERATURE_PILOT)
        return job, res, err

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, j) for j in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            job, res, err = fut.result()
            if err:
                fail += 1
                print(f"  [{i}/{len(jobs)}] {job['seed_id']} 失败: {err[:60]}")
                continue
            fin = finalize_reply(res["raw"], job["risk"], job["scenes"], job["user"])
            if not fin:
                fail += 1
                print(f"  [{i}/{len(jobs)}] {job['seed_id']} 空回复")
                continue
            sid = f"pilot_{job['seed_id']}_v{job['variant'] + 1}"
            row = make_sample(sid, job["scene"], job["scenes"], job["risk"],
                              job["speaker_type"], skill_md, job["user"], fin,
                              extra={"source": "kimi_pilot", "seed_id": job["seed_id"],
                                     "cell": f"{job['scene']}/{job['risk']}",
                                     "gen_model": GEN_MODEL,
                                     "created_at": datetime.now().isoformat(timespec="seconds")})
            with _write_lock:
                cc.append_jsonl(out_path, [row])
            ok += 1
            flag = "" if fin["pass"] else f"  ⚠️{fin['issues'][:2]}"
            print(f"  [{i}/{len(jobs)}] {sid}  {len(fin['reply'])}字{flag}")
    return ok, fail


def run_expand(client, jobs, skill_md, anchors_by_cell, negatives_by_cell,
               out_path, workers):
    ok = fail = 0

    def work(job):
        cell = (job["scene"], job["risk"])
        cross = f"{job['cross']}（若与主场景不矛盾才加）" if job["cross"] else "无"
        prompt = EXPAND_PROMPT.format(
            need=job["need"], scene=job["scene"],
            scene_label=cc.SCENE_LABELS.get(job["scene"], ""), risk=job["risk"],
            cross=cross,
            speaker="老人本人，或家属代述（请自行在样本中体现差异）",
            anchors=build_anchors_block(anchors_by_cell.get(cell, [])),
            negatives=build_negatives_block(negatives_by_cell.get(cell, [])),
            requirements=requirements_block(job["scene"], job["risk"]),
            variations=build_variations_block(offset=job["batch_no"] * 3),
        )
        res, err = call_llm(client, build_system_prompt(skill_md, "elder_self"),
                            prompt, TEMPERATURE_EXPAND)
        return job, res, err

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, j) for j in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            job, res, err = fut.result()
            if err:
                fail += 1
                print(f"  [{i}/{len(jobs)}] {job['scene']}/{job['risk']} 失败: {err[:60]}")
                continue
            arr = parse_json_array(res["raw"])
            if not arr:
                fail += 1
                print(f"  [{i}/{len(jobs)}] {job['scene']}/{job['risk']} JSON 解析失败")
                continue
            n_ok = 0
            for k, item in enumerate(arr):
                if not isinstance(item, dict):
                    continue
                user = (item.get("user") or "").strip()
                asst = item.get("assistant") or ""
                if not user or not asst.strip():
                    continue
                scenes = [job["scene"]]
                if job["cross"] and not cc.check_multi_scene_risk(
                        [job["scene"], job["cross"]], job["risk"]):
                    scenes.append(job["cross"])
                fin = finalize_reply(asst, job["risk"], scenes, user)
                if not fin:
                    continue
                aid = (f"exp_{job['scene']}_{job['risk']}_b{job['batch_no']}"
                       f"_{k + 1:02d}_{RUN_TAG}")
                anchor = (anchors_by_cell.get((job["scene"], job["risk"])) or [{}])[0]
                row = make_sample(aid, job["scene"], scenes, job["risk"], "elder_self",
                                  skill_md, user, fin,
                                  extra={"source": "deepseek_expand",
                                         "anchor_id": anchor.get("sample_id", ""),
                                         "cell": f"{job['scene']}/{job['risk']}",
                                         "gen_model": GEN_MODEL,
                                         "created_at": datetime.now().isoformat(timespec="seconds")})
                with _write_lock:
                    cc.append_jsonl(out_path, [row])
                n_ok += 1
            ok += n_ok
            print(f"  [{i}/{len(jobs)}] {job['scene']}/{job['risk']} "
                  f"需{job['need']} 得{n_ok}")
    return ok, fail


def main():
    ap = argparse.ArgumentParser(description="小暖语料生成器（pilot / expand）")
    ap.add_argument("--stage", choices=["pilot", "expand"], required=True)
    ap.add_argument("--seeds", default=str(cc.PILOT_SEEDS_PATH))
    ap.add_argument("--anchors", default="", help="expand：审核通过的 pilot JSONL")
    ap.add_argument("--negatives", default="", help="expand：审核驳回的 pilot JSONL")
    ap.add_argument("--targets", default=str(cc.TARGETS_PATH))
    ap.add_argument("--out", default="")
    ap.add_argument("--redundancy", type=float, default=1.5, help="pilot 每格冗余倍数")
    ap.add_argument("--batch-size", type=int, default=6, help="expand 每批条数")
    ap.add_argument("--margin", type=float, default=1.25,
                    help="expand 目标放大系数，为质检淘汰留余量")
    ap.add_argument("--workers", type=int, default=3,
                    help="并发数。Kimi 账号有速率限制，调高容易触发 429")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-seeds", default="",
                    help="只跑指定 seed_id（逗号分隔），用于补跑受限流影响的格子")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--gen-api-key", default="")
    ap.add_argument("--gen-base-url", default="")
    ap.add_argument("--gen-model", default="")
    ap.add_argument("--provider", choices=["gen", "deepseek"], default="gen",
                    help="用哪组环境变量：gen=GEN_*（初始语料用 Kimi），"
                         "deepseek=DEEPSEEK_*（扩展生成用）")
    args = ap.parse_args()

    global GEN_API_KEY, GEN_BASE_URL, GEN_MODEL
    if args.provider == "deepseek":
        # 扩展阶段用另一家模型：两家对同一句话的判定必然不同，
        # 交叉使用能暴露单一模型的系统性偏好。
        GEN_API_KEY = _env("DEEPSEEK_API_KEY")
        GEN_BASE_URL = _env("DEEPSEEK_BASE_URL", default="https://api.deepseek.com")
        GEN_MODEL = _env("DEEPSEEK_MODEL", default="deepseek-chat")
    if args.gen_api_key:
        GEN_API_KEY = args.gen_api_key
    if args.gen_base_url:
        GEN_BASE_URL = args.gen_base_url
    if args.gen_model:
        GEN_MODEL = args.gen_model

    skill_md = cc.SKILL_MD.read_text(encoding="utf-8")

    if args.stage == "pilot":
        seeds = cc.load_jsonl(args.seeds)
        only = {s.strip() for s in args.only_seeds.split(",") if s.strip()} or None
        jobs = plan_pilot_jobs(seeds, args.redundancy, args.limit, only)
    else:
        targets = cc.load_targets(args.targets)
        anchors = cc.load_jsonl(args.anchors) if args.anchors else []
        negatives = cc.load_jsonl(args.negatives) if args.negatives else []
        have = cc.count_matrix(anchors)
        jobs = plan_expand_jobs(targets, have, args.batch_size, args.limit, args.margin)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else (
        cc.CORPUS_DIR / f"{args.stage}_candidates_{ts}.jsonl")

    print("=" * 72)
    print(f"  小暖语料生成 · {args.stage} 阶段")
    print(f"  模型 {GEN_MODEL} @ {GEN_BASE_URL}")
    print(f"  任务数 {len(jobs)}  →  {out_path}")
    print("=" * 72)

    if args.dry_run:
        j = jobs[0] if jobs else None
        if not j:
            print("没有需要生成的任务")
            return
        if args.stage == "pilot":
            print(PILOT_PROMPT.format(
                scene=j["scene"], scene_label=cc.SCENE_LABELS.get(j["scene"], ""),
                risk=j["risk"], speaker_type=j["speaker_type"], user=j["user"],
                requirements=requirements_block(j["scene"], j["risk"])))
        else:
            print(f"格子 {j['scene']}/{j['risk']}  需 {j['need']} 条  "
                  f"附加标签 {j['cross'] or '无'}")
            # 真实展示该格子匹配到的锚点与负样本，便于确认注入是否生效
            ab_dry, nb_dry = {}, {}
            for a in (cc.load_jsonl(args.anchors) if args.anchors else []):
                ab_dry.setdefault(cc.cell_key(a), []).append(a)
            for n in (cc.load_jsonl(args.negatives) if args.negatives else []):
                nb_dry.setdefault(cc.cell_key(n), []).append(n)
            cell = (j["scene"], j["risk"])
            print(f"\n锚点池共 {len(ab_dry)} 个格子，负样本池共 {len(nb_dry)} 个格子")
            print(f"本格子锚点 {len(ab_dry.get(cell, []))} 条，负样本 {len(nb_dry.get(cell, []))} 条")
            print("\n【锚点区】")
            print(build_anchors_block(ab_dry.get(cell, [])))
            neg_block = build_negatives_block(nb_dry.get(cell, []))
            if neg_block:
                print("\n【负样本区】")
                print(neg_block)
            print("\n【变体维度】")
            print(build_variations_block(offset=j["batch_no"] * 3))
        print(f"\n系统提示词长度: {len(build_system_prompt(skill_md, 'elder_self'))} 字")
        return

    if not GEN_API_KEY:
        print("\n[错误] 未找到 API Key。设置 GEN_API_KEY 或 DEEPSEEK_API_KEY。")
        sys.exit(1)

    from openai import OpenAI

    client = OpenAI(api_key=GEN_API_KEY, base_url=GEN_BASE_URL)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.stage == "pilot":
        ok, fail = run_pilot(client, jobs, skill_md, out_path, args.workers)
    else:
        ab = {}
        for a in anchors:
            ab.setdefault(cc.cell_key(a), []).append(a)
        nb = {}
        for n in negatives:
            nb.setdefault(cc.cell_key(n), []).append(n)
        ok, fail = run_expand(client, jobs, skill_md, ab, nb, out_path, args.workers)

    print("\n" + "=" * 72)
    print(f"  生成完成：成功 {ok} | 失败 {fail}")
    print(f"  输出：{out_path}")
    print("=" * 72)

    rows = cc.load_jsonl(out_path)
    if rows:
        passed = [r for r in rows if r.get("pass")]
        print(f"  自动质检通过 {len(passed)} / {len(rows)}")
        if len(passed) < len(rows):
            print("  未通过的原因分布：")
            from collections import Counter
            c = Counter()
            for r in rows:
                if not r.get("pass"):
                    for i in r.get("issues", []):
                        c[i.split(":")[0]] += 1
            for k, v in c.most_common(8):
                print(f"    {k}: {v}")

    if args.stage == "pilot" and rows:
        # 冗余生成后择优：每格只留 1 条进入人工审核，把审核量压回种子数量级
        sel = select_best_per_cell(rows, per_cell=1)
        sel_path = out_path.with_name(out_path.stem + "_selected.jsonl")
        cc.write_jsonl(sel_path, sel)
        print(f"\n  择优后 {len(sel)} 条（每格 1 条）→ {sel_path.name}")
        print("  下一步：跑 validate_outputs --mode generated_sft，再用 build_corpus_review 出审核材料")


if __name__ == "__main__":
    main()
