"""语料审核材料生成 + 自动检查预填 + 结果汇总

对应 docs/corpus_review_checklist.md 的六大类 30 条标准。
自动项（A / B3 / B4 / C 大部分 / E2 / F）在生成评审材料时就跑完并填好，
人工只判标 ★ 的主观项，单条决策可压到 30 秒内。

三种用法：
    # 1. 生成评审材料（pilot 阶段全审 / expand 阶段抽样）
    python tools/build_corpus_review.py --input <candidates.jsonl> --review-rate 1.0

    # 2. 汇总人工审核结果，产出通过版与驳回版
    python tools/build_corpus_review.py --summarize <reviewed.csv> --outdir <目录>

    # 3. 只看自动检查结果，不出评审页
    python tools/build_corpus_review.py --input <candidates.jsonl> --auto-only
"""

import argparse
import csv
import html
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import corpus_common as cc  # noqa: E402

REPO_ROOT = cc.REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.taxonomy import (  # noqa: E402
    extract_tags, format_tags, strip_tags, validate_tags,
)
from backend.app.safety.safety_checker import (  # noqa: E402
    ROLE_MISMATCH_MARKERS,
    check_reply,
    contains_placeholder,
    detect_scenes,
    has_english_residual,
    has_internal_leak,
    has_marker_like_fragment,
    has_medication_confirmation,
    has_prompt_leak,
    has_reasoning_leak,
)

# ── 人工判断项（★）：评审页上要人工点选 ─────────────────────────────

MANUAL_ITEMS = [
    ("B1", "风险等级判定合理", "按「不加干预是否致害」判断，对照自动列给出的本地推断"),
    ("B2", "场景标签与 user 语义匹配", "只有真实交叉才多标；对照自动列给出的检测结果"),
    ("B5", "场景顺序按重要性降序", "首位即该条的主场景"),
    ("C2", "家属场景以老人安全为中心", "说话人是家属时，回复是否落在老人安全上，而非只安抚家属"),
    ("C10", "建议医学上站得住、不诱导涉险", "重点看关键词抓不到的改写句（如「先减半片试试」）"),
    ("D1", "语气符合温婉邻居型", "口语化、先共情后建议、无书面语堆砌、无命令式说教"),
    ("D3", "结尾有可执行的下一步", "不是空泛安慰"),
    ("D4", "无说教感、免责声明不堆砌", "「如有不适请及时就医」不反复出现"),
    ("E1", "user 像真实老人原话", "口语、有具体细节、可以信息不全"),
    ("E3", "越界诱导型 user 应保留", "「你就告诉我该吃几片」这类是必需难例，不要因为「不安全」就毙掉"),
]

AUTO_CHECK_LABELS = {
    "A1": "sample_id 唯一非空",
    "A2": "messages 三角色齐全",
    "A3": "标签格式合法（1 个 RISK + 1-3 个 SCENE）",
    "A4": "正文无残留标记片段",
    "A5": "无占位符",
    "A6": "篇幅在区间内",
    "B3": "场景与风险不矛盾",
    "B4": "多场景时风险取最高",
    "C1": "通用红线检查（不诊断/不开药/不调药/不劝退/不轻视/不贴标签）",
    "C3": "场景必需应急要素齐全",
    "C4": "用药场景提医生/药师",
    "C5": "无 prompt 泄露",
    "C6": "无推理过程泄露",
    "C7": "正文无内部代码/术语",
    "C8": "无英文残留",
    "C9": "无角色错位",
    "F1": "source 标记正确",
}

# 一票否决的类别（任一 fail 即 reject）
FATAL_PREFIXES = ("A", "C")


# ── 自动检查 ──────────────────────────────────────────────────────

def _msg(row, role):
    for m in row.get("messages") or []:
        if m.get("role") == role:
            return m.get("content", "")
    return ""


def run_auto_checks(row: dict, dup_ids: set = None, others: list = None) -> dict:
    """跑全部自动检查，返回 {check_id: {"status": "pass|fail|warn", "detail": str}}。"""
    dup_ids = dup_ids or set()
    out = {}

    def put(cid, ok, detail="", warn=False):
        out[cid] = {"status": ("pass" if ok else ("warn" if warn else "fail")),
                    "detail": detail}

    user = _msg(row, "user")
    assistant = _msg(row, "assistant")
    body = strip_tags(assistant)
    risk = row.get("risk_level") or ""
    scenes = row.get("scenes") or []

    # A 类
    sid = row.get("sample_id", "")
    put("A1", bool(sid) and sid not in dup_ids, f"id={sid or '空'}")
    roles = {m.get("role") for m in (row.get("messages") or [])}
    put("A2", {"system", "user", "assistant"} <= roles, f"角色={sorted(roles)}")

    risk_tag, scene_tags = extract_tags(assistant)
    tag_issues = validate_tags(risk_tag, scene_tags)
    tag_ok = (not tag_issues) and risk_tag == risk and sorted(scene_tags) == sorted(scenes)
    put("A3", tag_ok,
        f"标记 risk={risk_tag} scenes={scene_tags} / 字段 risk={risk} scenes={scenes}"
        + (f" / {tag_issues}" if tag_issues else ""))

    put("A4", not has_marker_like_fragment(body), "")
    ph = contains_placeholder(body)
    put("A5", not ph, "发现占位符" if ph else "")
    scene0 = scenes[0] if scenes else ""
    lo, hi = cc.length_range(risk, scene0)
    n = len(body)
    put("A6", cc.length_ok(body, risk, scene0), f"{n} 字，区间 {lo}-{hi}")

    # B 类
    conflicts = [cc.check_scene_risk(s, risk) for s in scenes]
    conflicts = [c for c in conflicts if c]
    multi = cc.check_multi_scene_risk(scenes, risk)
    put("B3", not conflicts, "；".join(conflicts))
    put("B4", not multi, multi)

    # C 类
    issues = check_reply(body, risk, scenes, user)
    put("C1", not issues, "；".join(issues[:3]))
    # C3 的应急要素缺失也由 check_reply 报出，单独标出便于人工看
    missing_emerg = [i for i in issues if "missing" in i]
    put("C3", not missing_emerg, "；".join(missing_emerg))
    if "S2" in scenes:
        med = has_medication_confirmation(body)
        put("C4", med, "未提及医生/药师/医院" if not med else "")
    else:
        out["C4"] = {"status": "skip", "detail": "非用药场景"}

    pl = has_prompt_leak(body)
    put("C5", not pl, "疑似复述规则" if pl else "")
    rl = has_reasoning_leak(body)
    put("C6", not rl, "疑似暴露推理过程" if rl else "")
    il = has_internal_leak(body)
    put("C7", not il, "正文出现内部代码/术语（现有质检未覆盖，本脚本补上）" if il else "")
    en = has_english_residual(body)
    put("C8", not en, "有英文残留" if en else "", warn=True)
    role_bad = [m for m in ROLE_MISMATCH_MARKERS if m in body]
    put("C9", not role_bad, f"角色错位词：{role_bad}" if role_bad else "")

    # F 类
    put("F1", bool(row.get("source")), f"source={row.get('source', '空')}")

    # 辅助信息（供人工判断 B1/B2）
    if others is not None:
        dup, jac = find_near_dup(user, others)
        put("E2", not dup, f"与 {jac:.0%} 相似的已有条目重复：{dup}" if dup else "")

    return out


def find_near_dup(user: str, others: list, threshold: float = 0.6):
    """查找近重复（改写型）。

    完全相同的不算——pilot 阶段每个种子故意生成多条变体，user 文本本就相同，
    那是设计使然；到 Phase 5 合并时再由 dedup_rows 统一去掉。
    这里只抓「换了说法但意思一样」的情况。
    """
    for o in others:
        if not o or o == user:
            continue
        j = cc.ngram_jaccard(user, o)
        if j >= threshold:
            return o[:30], j
    return None, 0.0


def verdict_from_checks(checks: dict) -> str:
    """根据自动检查给出预判：reject / edit / pass。"""
    for cid, r in checks.items():
        if r["status"] == "fail" and cid[0] in FATAL_PREFIXES:
            return "reject"
    for cid, r in checks.items():
        if r["status"] in ("fail", "warn"):
            return "edit"
    return "pass"


# ── 抽样 ──────────────────────────────────────────────────────────

def sample_for_review(rows: list, rate_by_risk: dict, scene_floor: int, seed: int,
                      review_rate: float = 0.0):
    """风险分层 + 场景保底抽样。review_rate>0 时全量按比例抽（pilot 用 1.0）。"""
    rng = random.Random(seed)
    if review_rate >= 1.0:
        picked = list(rows)
        rng.shuffle(picked)
        return picked

    picked = []
    seen_ids = set()

    def take(r):
        if r["sample_id"] not in seen_ids:
            seen_ids.add(r["sample_id"])
            picked.append(r)

    # 场景保底：每个主场景至少 1 条
    by_scene = defaultdict(list)
    for r in rows:
        by_scene[cc.primary_scene(r)].append(r)
    for scene, items in by_scene.items():
        if scene and items:
            take(rng.choice(items))

    # 风险分层
    by_risk = defaultdict(list)
    for r in rows:
        by_risk[r.get("risk_level", "")].append(r)
    for risk, items in by_risk.items():
        rate = rate_by_risk.get(risk, 0.1)
        n = max(1, round(len(items) * rate))
        for r in rng.sample(items, min(n, len(items))):
            take(r)

    rng.shuffle(picked)
    return picked


# ── 评审页 ────────────────────────────────────────────────────────

REVIEW_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>语料人工审核</title>
<style>
  *{box-sizing:border-box}
  body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:0;background:#f5f5f7;color:#1d1d1f;line-height:1.7}
  .top{position:sticky;top:0;background:#fff;border-bottom:1px solid #d2d2d7;padding:12px 20px;display:flex;align-items:center;gap:16px;z-index:10}
  .top h1{font-size:16px;margin:0;font-weight:600}
  .prog{font-size:13px;color:#6e6e73}
  .bar{flex:1;height:6px;background:#e5e5ea;border-radius:3px;overflow:hidden}
  .bar>i{display:block;height:100%;background:#0071e3;width:0;transition:width .2s}
  button{font:inherit;padding:7px 16px;border-radius:8px;border:none;background:#0071e3;color:#fff;cursor:pointer}
  button.ghost{background:#e5e5ea;color:#1d1d1f}
  .wrap{max-width:900px;margin:20px auto 80px;padding:0 16px}
  .card{background:#fff;border-radius:12px;padding:20px 22px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
  .card.done{border-left:4px solid #34c759}
  .card.reject{border-left:4px solid #ff3b30}
  .qid{font-size:12px;color:#86868b;margin-bottom:10px;letter-spacing:.3px}
  .sec{background:#f5f5f7;padding:11px 14px;border-radius:8px;margin-bottom:10px;font-size:14.5px}
  .sec b{color:#6e6e73;font-size:12.5px;font-weight:600;display:block;margin-bottom:3px}
  .reply{white-space:pre-wrap;font-size:14.5px}
  .tag{display:inline-block;background:#e8f0fe;color:#1a56c4;border-radius:4px;padding:1px 7px;font-size:12px;margin-right:5px}
  table{width:100%;border-collapse:collapse;font-size:12.5px;margin:12px 0}
  td{padding:4px 8px;border-bottom:1px solid #f0f0f2;vertical-align:top}
  td.k{width:230px;color:#3a3a3c}
  .p{color:#34c759;font-weight:600}.f{color:#ff3b30;font-weight:600}.w{color:#ff9500;font-weight:600}.s{color:#c7c7cc}
  .manual{border-top:2px solid #f0f0f2;padding-top:12px;margin-top:8px}
  .mi{display:flex;align-items:flex-start;gap:10px;padding:5px 0;font-size:14px}
  .mi>label{flex:1}
  .mi small{color:#86868b;display:block;font-size:12px}
  .verdict{margin-top:12px;font-weight:600;display:flex;gap:18px;align-items:center}
  .verdict label{cursor:pointer;font-weight:400}
  textarea{width:100%;margin-top:10px;padding:9px 12px;border:1px solid #e5e5ea;border-radius:8px;font:inherit;font-size:13.5px;resize:vertical}
  .hint{color:#86868b;font-size:13px;margin:4px 0 0}
</style></head><body>
<div class="top">
  <h1>语料人工审核</h1>
  <span class="prog" id="prog">0 / 0</span>
  <div class="bar"><i id="bar"></i></div>
  <button class="ghost" id="save">暂存说明</button>
  <button id="export">导出结果 CSV</button>
</div>
<div class="wrap">
  <p class="hint"><b>自动检查已跑完并预填</b>（绿色通过 / 红色失败 / 橙色警告）。你只需要判断下方标 ★ 的主观项，
  然后给出结论。判定规则：<b>A 类或 C 类任一 fail 即一票否决</b>；标「修改后通过」时<b>必须填写修改后的回复全文</b>。</p>
  <div id="list"></div>
</div>
<script>
const DATA = __DATA__;
const MANUAL = __MANUAL__;
const KEY = "xiaonuan_corpus_review_v1___NS_____REVIEWER__";
let state = JSON.parse(localStorage.getItem(KEY) || "{}");

function render(){
  const list = document.getElementById("list"); list.innerHTML = "";
  DATA.forEach((item, i) => {
    const s = state[item.id] || {};
    const card = document.createElement("div");
    card.className = "card" + (s.verdict === "pass" ? " done" : (s.verdict === "reject" ? " reject" : ""));
    const autoRows = item.auto.map(a =>
      `<tr><td class="k">${a.label}</td><td class="${a.status[0]}">${
        {pass:"通过",fail:"失败",warn:"警告",skip:"不适用"}[a.status]}</td><td>${a.detail||""}</td></tr>`).join("");
    const manualHTML = MANUAL.map(m => {
      const v = (s.manual||{})[m.id] || "";
      return `<div class="mi">
        <label><b>★ ${m.id} ${m.title}</b><small>${m.hint}</small></label>
        <label><input type="radio" name="${item.id}_${m.id}" value="ok" data-q="${item.id}" data-m="${m.id}" ${v==="ok"?"checked":""}> 通过</label>
        <label><input type="radio" name="${item.id}_${m.id}" value="bad" data-q="${item.id}" data-m="${m.id}" ${v==="bad"?"checked":""}> 有问题</label>
      </div>`;
    }).join("");
    const vd = (val, label) => `<label><input type="radio" name="${item.id}_vd" value="${val}"
      data-q="${item.id}" data-m="__verdict" ${s.verdict===val?"checked":""}> ${label}</label>`;
    card.innerHTML = `
      <div class="qid">#${i+1} / ${DATA.length}　${item.id}　${item.cell}　${item.source}</div>
      <div class="sec"><b>老人说</b>${item.user}</div>
      <div class="sec"><b>小暖回复</b><div class="reply">${item.reply}</div>
        <div style="margin-top:6px">${item.tags}</div></div>
      <table>${autoRows}</table>
      <div class="manual">${manualHTML}</div>
      <div class="verdict">结论：${vd("pass","通过")}${vd("edit","修改后通过")}${vd("reject","驳回")}</div>
      <textarea data-q="${item.id}" data-m="__edited" placeholder="标「修改后通过」时，在这里写修改后的回复全文（含末尾标签）">${s.edited||""}</textarea>
      <textarea data-q="${item.id}" data-m="__notes" rows="2" placeholder="备注：为什么这么判（驳回时必填）">${s.notes||""}</textarea>
    `;
    list.appendChild(card);
  });
  updateProgress();
}
function updateProgress(){
  const done = DATA.filter(d => (state[d.id]||{}).verdict).length;
  document.getElementById("prog").textContent = done + " / " + DATA.length;
  document.getElementById("bar").style.width = (done/DATA.length*100) + "%";
}
document.addEventListener("change", e => {
  const t = e.target, q = t.dataset.q, m = t.dataset.m;
  if(!q || !m) return;
  state[q] = state[q] || {manual:{}};
  if(m === "__verdict") state[q].verdict = t.value;
  else { state[q].manual = state[q].manual || {}; state[q].manual[m] = t.value; }
  localStorage.setItem(KEY, JSON.stringify(state));
  const card = t.closest(".card");
  if(card) card.className = "card" + (state[q].verdict==="pass"?" done":(state[q].verdict==="reject"?" reject":""));
  updateProgress();
});
document.addEventListener("input", e => {
  const t = e.target;
  if(t.tagName !== "TEXTAREA") return;
  state[t.dataset.q] = state[t.dataset.q] || {manual:{}};
  if(t.dataset.m === "__edited") state[t.dataset.q].edited = t.value;
  else state[t.dataset.q].notes = t.value;
  localStorage.setItem(KEY, JSON.stringify(state));
});
document.getElementById("save").onclick = () =>
  alert("已保存在本机浏览器（localStorage）。可直接关闭页面，下次打开自动恢复。");
document.getElementById("export").onclick = () => {
  const head = ["review_id","verdict","edited_assistant","reviewer_notes",...MANUAL.map(m=>"m_"+m.id)];
  const rows = [head.join(",")];
  DATA.forEach(item => {
    const s = state[item.id] || {};
    const row = [item.id, s.verdict||"", s.edited||"", s.notes||""];
    MANUAL.forEach(m => row.push((s.manual||{})[m.id]||""));
    rows.push(row.map(v=>`"${String(v).replace(/"/g,'""').replace(/\\r?\\n/g," ")}"`).join(","));
  });
  const blob = new Blob(["\\ufeff"+rows.join("\\n")], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "__NS_____REVIEWER___corpus_review.csv";
  a.click();
};
render();
</script></body></html>
"""


def build_review_items(rows: list) -> list:
    dup_ids = {k for k, v in Counter(r.get("sample_id", "") for r in rows).items() if v > 1}
    users = [ _msg(r, "user") for r in rows ]
    items = []
    for r in rows:
        checks = run_auto_checks(r, dup_ids, users)
        assistant = _msg(r, "assistant")
        risk_tag, scene_tags = extract_tags(assistant)
        body = strip_tags(assistant)
        items.append({
            "id": r.get("sample_id", ""),
            "cell": r.get("cell", ""),
            "source": r.get("source", ""),
            "user": html.escape(_msg(r, "user")),
            "reply": html.escape(body),
            "tags": "".join(f'<span class="tag">{html.escape(t)}</span>'
                            for t in ([f"[RISK:{risk_tag}]"] if risk_tag else [])
                            + [f"[SCENE:{s}]" for s in scene_tags]),
            "auto": [{"id": cid, "label": AUTO_CHECK_LABELS.get(cid, cid),
                      "status": v["status"], "detail": html.escape(v["detail"])}
                     for cid, v in checks.items()],
            "preset": verdict_from_checks(checks),
            "row": r,
        })
    return items


def write_review_html(items, path: Path, reviewer: str, namespace: str = ""):
    """namespace 决定浏览器 localStorage 的 key。

    每个审核轮的进度存在浏览器本机，key 若固定不变，第二轮会直接读到
    第一轮的残留（曾出现 Phase 2 的改写内容在 Phase 4 页面里「自己冒出来」），
    人工会误以为已经填过而跳过。故按输入文件取命名空间。
    """
    payload = [{"id": it["id"], "cell": it["cell"], "source": it["source"],
                "user": it["user"], "reply": it["reply"], "tags": it["tags"],
                "auto": it["auto"]} for it in items]
    doc = (REVIEW_HTML
           .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
           .replace("__MANUAL__", json.dumps(
               [{"id": a, "title": b, "hint": c} for a, b, c in MANUAL_ITEMS],
               ensure_ascii=False))
           .replace("__NS__", namespace)
           .replace("__REVIEWER__", reviewer))
    path.write_text(doc, encoding="utf-8")


def write_review_csv(items, path: Path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "cell", "source", "老人说", "小暖回复",
                    "自动预判", "自动检查摘要", "结论", "修改后回复", "备注"]
                   + [f"manual_{a}" for a, _, _ in MANUAL_ITEMS])
        for it in items:
            fails = [f"{a['id']}:{a['status']}" for a in it["auto"]
                     if a["status"] in ("fail", "warn")]
            w.writerow([it["id"], it["cell"], it["source"],
                        html.unescape(it["user"]), html.unescape(it["reply"]),
                        it["preset"], " ".join(fails) or "全部通过",
                        "", "", ""] + [""] * len(MANUAL_ITEMS))


# ── 汇总 ──────────────────────────────────────────────────────────

def summarize(reviewed_path: Path, candidates_path: Path, outdir: Path):
    rows = list(csv.DictReader(open(reviewed_path, encoding="utf-8-sig")))
    cand = {r.get("sample_id"): r for r in cc.load_jsonl(candidates_path)}

    approved, rejected, edited = [], [], []
    for r in rows:
        sid = r.get("review_id", "")
        verdict = (r.get("结论") or r.get("verdict") or "").strip()
        base = cand.get(sid)
        if not base:
            continue
        rec = dict(base)
        rec["review_verdict"] = verdict
        rec["review_notes"] = r.get("备注") or r.get("reviewer_notes") or ""
        edited_text = (r.get("修改后回复") or r.get("edited_assistant") or "").strip()
        if edited_text:
            # 人工改写时常常把末尾标签漏掉。这里统一以脚本指定的标签为准补回——
            # 否则该条会通不过 A3 检查，进库后模型也学不到「这类输入该标什么」。
            if not extract_tags(edited_text)[0]:
                edited_text = strip_tags(edited_text) + format_tags(
                    base.get("risk_level", ""), base.get("scenes") or [])
            rec["assistant_final"] = edited_text
            rec["was_edited"] = True
        else:
            rec["assistant_final"] = _msg(base, "assistant")
            rec["was_edited"] = False
        if verdict == "pass":
            approved.append(rec)
        elif verdict == "edit":
            edited.append(rec)
            approved.append(rec)      # 改后可用，进锚点池
        elif verdict == "reject":
            rejected.append(rec)

    outdir.mkdir(parents=True, exist_ok=True)
    cc.write_jsonl(outdir / "corpus_approved.jsonl", approved)
    cc.write_jsonl(outdir / "corpus_rejected.jsonl", rejected)

    n_edited = sum(1 for r in approved if r.get("was_edited"))
    print(f"审核 {len(rows)} 条")
    print(f"  通过（原样采纳） {len(approved) - n_edited}")
    print(f"  通过（人工改写） {n_edited}")
    print(f"  驳回             {len(rejected)}")
    if rows:
        print(f"  驳回率   {len(rejected) / len(rows) * 100:.1f}%")
    print(f"\n通过版 → {outdir / 'corpus_approved.jsonl'}（{len(approved)} 条）")
    print(f"驳回版 → {outdir / 'corpus_rejected.jsonl'}（{len(rejected)} 条，将作为负样本）")

    if rejected:
        print("\n驳回原因分布：")
        c = Counter()
        for r in rows:
            if (r.get("结论") or r.get("verdict")) == "reject":
                for k, v in r.items():
                    if k.startswith("manual_") and v == "bad":
                        c[k] += 1
        for k, v in c.most_common():
            print(f"    {k}: {v}")


def main():
    ap = argparse.ArgumentParser(description="语料审核材料生成与汇总")
    ap.add_argument("--input", default="", help="候选语料 JSONL")
    ap.add_argument("--outdir", default="", help="输出目录")
    ap.add_argument("--review-rate", type=float, default=0.0, help="1.0 = 全审（pilot 用）")
    ap.add_argument("--rate-by-risk", default="R3=1.0,R2b=1.0,R2a=0.5,R1=0.2,R0=0.1")
    ap.add_argument("--scene-floor", type=int, default=1, help="每个主场景至少抽几条")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--auto-only", action="store_true", help="只跑自动检查，不出评审页")
    ap.add_argument("--summarize", default="", help="人工审核结果 CSV 路径")
    ap.add_argument("--candidates", default="", help="汇总时对应的候选 JSONL")
    args = ap.parse_args()

    if args.summarize:
        cand = args.candidates or ""
        if not cand:
            print("[错误] 汇总需要 --candidates 指定候选语料文件")
            sys.exit(1)
        summarize(Path(args.summarize), Path(cand),
                  Path(args.outdir) if args.outdir else cc.CORPUS_DIR / "reviewed")
        return

    if not args.input:
        cands = sorted(cc.CORPUS_DIR.glob("*_selected.jsonl")) or \
                sorted(cc.CORPUS_DIR.glob("*_candidates_*.jsonl"))
        cands = [p for p in cands if not p.name.startswith("_")]
        if not cands:
            print(f"[错误] 在 {cc.CORPUS_DIR} 下找不到候选语料")
            sys.exit(1)
        in_path = cands[-1]
    else:
        in_path = Path(args.input)

    rows = cc.load_jsonl(in_path)
    print("=" * 70)
    print(f"  语料审核材料生成")
    print(f"  输入 {in_path.name}（{len(rows)} 条）")
    print("=" * 70)

    if args.auto_only:
        dup_ids = {k for k, v in Counter(r.get("sample_id", "") for r in rows).items() if v > 1}
        users = [_msg(r, "user") for r in rows]
        agg = Counter()
        presets = Counter()
        for r in rows:
            checks = run_auto_checks(r, dup_ids, users)
            for cid, v in checks.items():
                if v["status"] in ("fail", "warn"):
                    agg[f"{cid} {AUTO_CHECK_LABELS.get(cid, '')} [{v['status']}]"] += 1
            presets[verdict_from_checks(checks)] += 1
        print(f"\n自动预判：{dict(presets)}")
        print(f"\n有问题的检查项（按出现次数）：")
        for k, v in agg.most_common(20):
            print(f"  {v:>4}  {k}")
        return

    rate_map = {}
    for part in args.rate_by_risk.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            rate_map[k.strip()] = float(v)

    picked = sample_for_review(rows, rate_map, args.scene_floor, args.seed, args.review_rate)
    print(f"\n抽中 {len(picked)} 条（{len(picked)/len(rows)*100:.0f}%）")
    dist = Counter(r.get("risk_level", "") for r in picked)
    print("风险分布: " + "  ".join(f"{k}×{dist[k]}" for k in sorted(dist)))

    outdir = Path(args.outdir) if args.outdir else cc.CORPUS_DIR / "review_pending"
    outdir.mkdir(parents=True, exist_ok=True)

    items = build_review_items(picked)
    presets = Counter(it["preset"] for it in items)
    print(f"\n自动预判：{dict(presets)}")

    html_path = outdir / "corpus_review.html"
    csv_path = outdir / "corpus_review.csv"
    # localStorage 命名空间：取输入文件名，避免两轮审核在浏览器里串档
    namespace = re.sub(r"[^0-9A-Za-z_]", "_", in_path.stem)[:60]
    write_review_html(items, html_path, "all", namespace)
    write_review_csv(items, csv_path)
    print(f"\n本轮进度存储 key: xiaonuan_corpus_review_v1_{namespace}_all")

    (outdir / "_source.txt").write_text(str(in_path), encoding="utf-8")

    print(f"\n评审页 → {html_path}")
    print(f"CSV 备用 → {csv_path}")
    print(f"\n人工审核完成后，用这条命令汇总：")
    print(f"  python tools/build_corpus_review.py --summarize <导出的csv> --candidates {in_path}")


if __name__ == "__main__":
    main()
