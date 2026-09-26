"""语料库建设的共用底座。

所有语料相关脚本（generate_corpus / build_corpus_review / check_distribution）
统一从这里 import，避免各处重复实现去重、编号、篇幅区间、场景风险规则。

设计原则：**标签由脚本决定，不由模型决定**。
Kimi 与 DeepSeek 对同一句 user 的 risk/scenes 判定必然漂移，且
safety_checker 的 _S1_KEYWORDS 含「难受/疼/酸」，会让大量输入过度判为 S1。
所以生成时由脚本派发 (scene, risk) 格子，模型只负责写正文。
"""

import json
import re
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# ── 路径 ──────────────────────────────────────────────────────────

SKILL_DIR = REPO_ROOT / "skills" / "healthconsult-assistant-skill"
EXAMPLES_DIR = SKILL_DIR / "examples"
CORPUS_DIR = EXAMPLES_DIR / "corpus"

TARGETS_PATH = CORPUS_DIR / "corpus_targets.json"
PILOT_SEEDS_PATH = CORPUS_DIR / "pilot_seeds.jsonl"

SKILL_MD = SKILL_DIR / "SKILL.md"

# ── 标签体系（与 backend/app/dialogue/taxonomy.py 保持一致）────────

RISK_LEVELS = ("R3", "R2b", "R2a", "R1", "R0")
RISK_ORDER = {"R0": 0, "R1": 1, "R2a": 2, "R2b": 3, "R3": 4}

SCENES = (
    "S1", "S2", "S3", "S4",
    "M1", "M2",
    "L1", "L2", "L3", "L4",
    "E1",
    "N1", "N2", "N3",
    "X1", "X2",
)

SCENE_LABELS = {
    "S1": "症状咨询", "S2": "用药管理", "S3": "慢病管理", "S4": "就医引导",
    "M1": "情绪陪伴", "M2": "心理危机",
    "L1": "饮食营养", "L2": "运动康复", "L3": "作息睡眠", "L4": "社交活动",
    "E1": "急症识别",
    "N1": "人身安全", "N2": "环境安全", "N3": "诈骗财产",
    "X1": "闲聊", "X2": "系统功能",
}

# 场景与风险等级的合法组合。用于 B3/B4 自动检查。
#   exact: 只允许这些等级
#   min:   不低于该等级
#   allowed: 允许的等级集合
SCENE_RISK_RULES = {
    "S1": {"min": "R0"},
    "S2": {"min": "R1"},          # 用药管理不会低于 R1
    "S3": {"min": "R0"},
    "S4": {"min": "R0"},
    "M1": {"min": "R0"},
    "M2": {"exact": {"R3"}},      # 心理危机只能是 R3
    "L1": {"min": "R0"},
    "L2": {"min": "R0"},
    "L3": {"min": "R0"},
    "L4": {"min": "R0"},
    "E1": {"allowed": {"R2b", "R3"}},
    "N1": {"exact": {"R3"}},      # 人身安全紧急只能是 R3
    "N2": {"exact": {"R3"}},      # 环境安全紧急只能是 R3
    "N3": {"exact": {"R2b"}},     # 诈骗进行中
    "X1": {"exact": {"R0"}},      # 闲聊只能 R0
    "X2": {"exact": {"R0"}},      # 系统功能只能 R0
}

# 每个场景的「风险地板」：多场景交叉时 risk 必须 ≥ max(floors)
# 用于 B4 检查（多标签取最高风险）
SCENE_RISK_FLOOR = {
    "N1": "R3", "N2": "R3", "N3": "R2b", "M2": "R3",
    "E1": "R2b", "S2": "R1",
    "S1": "R0", "S3": "R0", "S4": "R0", "M1": "R0",
    "L1": "R0", "L2": "R0", "L3": "R0", "L4": "R0",
    "X1": "R0", "X2": "R0",
}

# 抽样审核时视为「必须全检」的安全场景
SAFETY_SCENES = ("E1", "N1", "N2", "N3", "M2")


# ── 篇幅区间（SKILL.md 第 8 节的机器可读副本）──────────────────────

def length_range(risk: str, scene: str = "") -> tuple:
    """返回 (下限, 上限) 字符数。按 SKILL.md 第 8 节，从特殊到一般匹配。

    注意：SKILL.md 里「用药边界（R1/R2a）」的括号写的是**典型风险等级**，
    真正的分类依据是**场景**。所以这里按 scene == S2 判定，而不是 risk == R2a——
    否则 S4/R2a（就医引导）这类非用药场景会被错误套用 100-180 的区间。
    """
    if scene in ("X1", "X2"):
        return (30, 80)          # 非陪护
    if scene in ("N1", "N2"):
        return (80, 160)         # 人身/环境安全
    if scene in ("M1", "M2"):
        return (100, 220)        # 心理健康
    if scene == "S2":
        return (100, 180)        # 用药边界（按场景判定）
    if risk in ("R2b", "R3"):
        return (120, 280)        # 紧急就医（按风险判定，无对应场景）
    return (100, 220)            # 日常建议（R0/R1）


def length_ok(text: str, risk: str, scene: str = "", tolerance: float = 0.20) -> bool:
    """篇幅是否落在区间内（默认允许 ±20% 容差）。"""
    lo, hi = length_range(risk, scene)
    n = len(text or "")
    return lo * (1 - tolerance) <= n <= hi * (1 + tolerance)


# ── 文本规范化与去重 ──────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[\s　，。！？、；：,.!?;:·…—\-—～~\"'“”‘’()（）【】\[\]{}]+")


def norm_text(text: str) -> str:
    """规范化文本用于去重键：去空白、去标点、全角转半角字母数字。"""
    t = (text or "").strip()
    # 全角字母数字 → 半角
    out = []
    for ch in t:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    t = "".join(out)
    return _PUNCT_RE.sub("", t).lower()


def row_user_text(row: dict) -> str:
    """取 user 文本，兼容两种行格式。

    训练格式把内容放在 messages[] 里，扁平格式用顶层 user 字段。
    两者都支持——否则对 messages 格式的行会取到空串，
    去重时被当成「无有效文本」整批丢弃。
    """
    if row.get("user"):
        return row["user"]
    for m in row.get("messages") or []:
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def dedup_rows(rows: list, key=None) -> tuple:
    """按规范化文本去重。返回 (保留, 丢弃)。默认按 user 文本比较。"""
    key = key or row_user_text
    seen = set()
    kept, dropped = [], []
    for r in rows:
        k = norm_text(key(r))
        if not k:
            dropped.append(r)
            continue
        if k in seen:
            dropped.append(r)
            continue
        seen.add(k)
        kept.append(r)
    return kept, dropped


def ngram_jaccard(a: str, b: str, n: int = 5) -> float:
    """5-gram Jaccard 相似度，用于检测改写型近重复（无需 embedding）。"""
    na, nb = norm_text(a), norm_text(b)
    if len(na) < n or len(nb) < n:
        return 1.0 if na == nb and na else 0.0
    sa = {na[i:i + n] for i in range(len(na) - n + 1)}
    sb = {nb[i:i + n] for i in range(len(nb) - n + 1)}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ── 格子与分布 ────────────────────────────────────────────────────

def primary_scene(row: dict) -> str:
    """取主场景 = scenes[0]。"""
    scenes = row.get("scenes") or []
    return scenes[0] if scenes else ""


def cell_key(row: dict) -> tuple:
    """返回 (主场景, 风险等级) 格子。"""
    risk = row.get("risk_level") or row.get("risk") or ""
    return (primary_scene(row), risk)


def distribute_counts(total: int, weights: dict, floors: dict = None) -> dict:
    """按权重把 total 分配到各 key，用最大余数法保证合计恰好等于 total。

    floors: {key: 最小条数}，先满足下限，剩余按权重分配。
    """
    floors = floors or {}
    keys = list(weights)
    result = {k: int(floors.get(k, 0)) for k in keys}
    remaining = total - sum(result.values())
    if remaining <= 0:
        return result

    total_w = sum(weights[k] for k in keys) or 1.0
    exact = {k: remaining * weights[k] / total_w for k in keys}
    for k in keys:
        result[k] += int(exact[k])
    # 余数补齐
    left = total - sum(result.values())
    if left > 0:
        order = sorted(keys, key=lambda k: exact[k] - int(exact[k]), reverse=True)
        for i in range(left):
            result[order[i % len(order)]] += 1
    return result


def load_targets(path: Path = None) -> dict:
    path = Path(path) if path else TARGETS_PATH
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def flatten_targets(targets: dict) -> dict:
    """把 corpus_targets.json 的矩阵展平成 {(scene, risk): count}。"""
    matrix = targets.get("matrix", targets)
    out = {}
    for scene, risks in matrix.items():
        if scene.startswith("_"):
            continue
        for risk, n in risks.items():
            if n:
                out[(scene, risk)] = int(n)
    return out


# ── 标签合法性检查 ────────────────────────────────────────────────

def check_scene_risk(scene: str, risk: str) -> str:
    """检查单个场景与风险是否矛盾。合法返回空串，否则返回原因。"""
    rule = SCENE_RISK_RULES.get(scene)
    if not rule:
        return f"unknown_scene:{scene}"
    if risk not in RISK_ORDER:
        return f"unknown_risk:{risk}"
    if "exact" in rule and risk not in rule["exact"]:
        return f"scene_risk_conflict:{scene}只允许{'/'.join(sorted(rule['exact']))}，实为{risk}"
    if "allowed" in rule and risk not in rule["allowed"]:
        return f"scene_risk_conflict:{scene}只允许{'/'.join(sorted(rule['allowed']))}，实为{risk}"
    if "min" in rule and RISK_ORDER[risk] < RISK_ORDER[rule["min"]]:
        return f"risk_below_floor:{scene}不低于{rule['min']}，实为{risk}"
    return ""


def check_multi_scene_risk(scenes, risk: str) -> str:
    """多场景交叉时，risk 必须 ≥ 各场景风险地板的最大值。"""
    floors = [SCENE_RISK_FLOOR.get(s) for s in (scenes or [])]
    floors = [f for f in floors if f]
    if not floors or risk not in RISK_ORDER:
        return ""
    need = max(floors, key=lambda f: RISK_ORDER[f])
    if RISK_ORDER[risk] < RISK_ORDER[need]:
        return f"multi_scene_risk_too_low:含{scenes}时风险应≥{need}，实为{risk}"
    return ""


# ── JSONL 读写 ────────────────────────────────────────────────────

def load_jsonl(path) -> list:
    path = Path(path)
    if not path.is_file():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path, rows) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_jsonl(path, rows) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ── 统计 ──────────────────────────────────────────────────────────

def count_matrix(rows: list) -> Counter:
    return Counter(cell_key(r) for r in rows)


def tag_stats(rows: list) -> dict:
    scenes = Counter()
    risks = Counter()
    total_tags = 0
    for r in rows:
        ss = r.get("scenes") or []
        scenes.update(ss)
        total_tags += len(ss)
        risks[r.get("risk_level") or r.get("risk") or ""] += 1
    return {
        "total": len(rows),
        "scene_dist": dict(scenes),
        "risk_dist": dict(risks),
        "scene_coverage": len(scenes),
        "risk_coverage": len([k for k in risks if k]),
        "avg_scenes": round(total_tags / len(rows), 3) if rows else 0.0,
    }


def shell_hint() -> str:
    """给命令行工具用的简短说明。"""
    return (
        f"语料目录: {CORPUS_DIR}\n"
        f"目标矩阵: {TARGETS_PATH}\n"
        f"种子文件: {PILOT_SEEDS_PATH}"
    )
