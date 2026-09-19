"""小暖四版人格 · 人工抽检材料生成

从大模型评分结果中分层抽取样本，产出供人工复核的评审材料，用于验证
「大模型评分是否可信」——这是整个结论链条上唯一无法由机器替代的环节。

── 两种模式 ────────────────────────────────────────────────────
  --mode single（默认）：单条评分。人工按同一套五维量表给单条回复打分。
                        与 tools/score_replies.py 的绝对打分口径一致。
  --mode pair           ：配对比较。人工判断两条回复哪条更好，用于配对设计。

── 产出 ────────────────────────────────────────────────────────
  manual_review_*.html        自包含评审页，浏览器直接打开，点选后导出 CSV
  manual_review_all.csv       Excel 备用版（UTF-8 BOM）
  manual_review_KEY_*.csv     答案键：每条对应哪版人格、大模型给了多少分
                              **评完之前不要打开**

── 盲化 ────────────────────────────────────────────────────────
  页面内嵌数据不含人格名。但请评分员评完前不要打开 key 文件，
  也不要用开发者工具翻看页面数据结构。

── 重叠集 ──────────────────────────────────────────────────────
  默认留出一批单元由全部评分员共评。没有它，当「人 vs 大模型一致性」偏低时
  无法判断原因——是大模型评不准，还是这个任务本身就有主观性。
  重叠集给出「人-人一致性」这个天花板参照。

用法：
    python tools/build_manual_review.py --mode single --reviewers 4
    python tools/build_manual_review.py --mode single --overlap 20 --reviewers 4
    python tools/build_manual_review.py --mode pair --reviewers 4
"""

import argparse
import csv
import html
import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import RESULTS_DIR, EXAMPLES_DIR  # noqa: E402

PERSONAS = ["温婉邻居型", "贴心闺女型", "素朴家常型", "从容守护型"]

# 量表与 model 评委共用同一份定义（tools/_rubric.py）。
# 人工和大模型必须拿同一把尺子，否则算出来的"一致性"没有意义。
from _rubric import DIMENSION_NAMES as DIMENSIONS  # noqa: E402
from _rubric import (  # noqa: E402
    DIMENSIONS as RUBRIC,
    LENGTH_TARGETS,
    SCALE_NOTE,
)

DIMENSION_HINT = {n: RUBRIC[n]["hint"] for n in DIMENSIONS}
DIMENSION_ANCHORS = {
    n: "　".join(f"<b>{k}分</b> {v}" for k, v in sorted(RUBRIC[n]["anchors"].items()))
    for n in DIMENSIONS
}

# 单条模式的抽样比例。高危场景过采样，但控制总量在 25% 左右。
SINGLE_RATE_BY_RISK = {
    "S0": 0.40, "S1": 0.40, "S2": 0.40,
    "M0": 0.40, "M1": 0.40,
    "R3": 0.35, "R2b": 0.35,
    "R2a": 0.12, "R1": 0.12, "R0": 0.12, "X": 0.12,
}

# 配对模式的抽样比例
PAIR_RATE_BY_RISK = {
    "S0": 1.0, "S1": 1.0, "S2": 1.0,
    "M0": 1.0, "M1": 1.0,
    "R3": 0.5, "R2b": 0.5,
    "R2a": 0.15, "R1": 0.15, "R0": 0.15, "X": 0.15,
}

SINGLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>小暖回复人工评审</title>
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
  .wrap{max-width:820px;margin:20px auto 80px;padding:0 16px}
  .card{background:#fff;border-radius:12px;padding:22px 24px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
  .card.done{border-left:4px solid #34c759}
  .qid{font-size:12px;color:#86868b;letter-spacing:.5px;margin-bottom:10px}
  .elder{background:#f5f5f7;padding:12px 16px;border-radius:8px;margin-bottom:14px;font-size:15px}
  .elder b{color:#6e6e73;font-weight:600;font-size:13px}
  .reply{border:1px solid #e5e5ea;border-radius:8px;padding:14px 16px;font-size:15px;white-space:pre-wrap}
  .reply h3{margin:0 0 8px;font-size:13px;color:#0071e3;font-weight:600}
  .meta{font-size:12px;color:#86868b;margin-top:8px}
  .dims{margin-top:16px}
  .dim{padding:9px 0;border-top:1px solid #f0f0f2}
  .dim:first-child{border-top:none}
  .dimhead{display:flex;align-items:baseline;gap:8px;font-size:14px}
  .dimhead b{width:76px;flex:none;font-weight:600}
  .dimhead span{color:#86868b;font-size:12.5px}
  .scale{display:flex;gap:18px;margin-top:9px;padding-left:76px}
  .rubric{background:#fff;border-radius:12px;padding:4px 20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
  .rubric summary{cursor:pointer;font-weight:600;font-size:14px;padding:14px 0;color:#0071e3;list-style:none}
  .rubric summary::-webkit-details-marker{display:none}
  .rubric summary::before{content:"▸ ";display:inline-block;transition:transform .15s}
  .rubric[open] summary::before{transform:rotate(90deg)}
  .rubric-body{padding:0 0 16px;font-size:13.5px;line-height:1.8}
  .rubric-body h4{margin:16px 0 6px;font-size:14px;color:#1d1d1f}
  .rubric-body h4:first-child{margin-top:0}
  .rubric-body p{margin:2px 0}
  .rubric-body .a{margin-left:14px;color:#3a3a3c}
  .rubric-body .a b{color:#1d1d1f;display:inline-block;min-width:34px}
  .rubric-body .scalebox{background:#f5f5f7;border-radius:8px;padding:12px 14px;margin-bottom:14px;white-space:pre-wrap}
  .rubric-body .len{margin-left:14px;color:#3a3a3c}
  .scale label{cursor:pointer;user-select:none;font-size:14px}
  .scale input{margin-right:3px}
  textarea{width:100%;margin-top:12px;padding:9px 12px;border:1px solid #e5e5ea;border-radius:8px;font:inherit;font-size:14px;resize:vertical}
  .hint{color:#86868b;font-size:13px;margin:4px 0 0}
</style></head><body>
<div class="top">
  <h1>小暖回复人工评审</h1>
  <span class="prog" id="prog">0 / 0</span>
  <div class="bar"><i id="bar"></i></div>
  <button class="ghost" id="save">暂存说明</button>
  <button id="export">导出结果 CSV</button>
</div>
<div class="wrap">
  <details class="rubric" open>
    <summary>评分标准（务必先读一遍，点此折叠）</summary>
    <div class="rubric-body">__RUBRIC_HTML__</div>
  </details>
  <p class="hint">选择会自动保存在本机浏览器，关掉页面不丢。评分标准与大模型评委完全一致。</p>
  <div id="list"></div>
</div>
<script>
const DIMS = __DIMS__;
const HINTS = __HINTS__;
const DATA = __DATA__;
const KEY = "xiaonuan_single_v2___REVIEWER__";
let state = JSON.parse(localStorage.getItem(KEY) || "{}");

function render(){
  const list = document.getElementById("list");
  list.innerHTML = "";
  DATA.forEach((item, i) => {
    const s = state[item.id] || {};
    const card = document.createElement("div");
    const filled = DIMS.every(d => (s.dims||{})[d]);
    card.className = "card" + (filled ? " done" : "");
    const dimHTML = DIMS.map(d => {
      const v = (s.dims||{})[d] || "";
      const opts = [1,2,3,4,5].map(n =>
        `<label><input type="radio" name="${item.id}_${d}" value="${n}"
          ${String(v)===String(n)?"checked":""} data-q="${item.id}" data-d="${d}">${n}</label>`).join("");
      return `<div class="dim"><div class="dimhead"><b>${d}</b><span>${HINTS[d]||""}</span></div>
        <div class="scale">${opts}</div></div>`;
    }).join("");
    card.innerHTML = `
      <div class="qid">#${i+1} / ${DATA.length}　${item.id}</div>
      <div class="elder"><b>老人说</b><br>${item.user}</div>
      <div class="reply"><h3>助手回复</h3>${item.reply}</div>
      <div class="dims">${dimHTML}</div>
      <textarea data-q="${item.id}" data-d="note" placeholder="备注（可选）：哪里让你犹豫？">${s.note||""}</textarea>
    `;
    list.appendChild(card);
  });
  updateProgress();
}
function updateProgress(){
  const done = DATA.filter(d => DIMS.every(x => ((state[d.id]||{}).dims||{})[x])).length;
  document.getElementById("prog").textContent = done + " / " + DATA.length;
  document.getElementById("bar").style.width = (done/DATA.length*100) + "%";
}
document.addEventListener("change", e => {
  const t = e.target, q = t.dataset.q, d = t.dataset.d;
  if(!q || !d) return;
  state[q] = state[q] || {dims:{}};
  state[q].dims = state[q].dims || {};
  state[q].dims[d] = t.value;
  localStorage.setItem(KEY, JSON.stringify(state));
  const card = t.closest(".card");
  if(card) card.classList.toggle("done", DIMS.every(x => state[q].dims[x]));
  updateProgress();
});
document.addEventListener("input", e => {
  const t = e.target;
  if(t.tagName !== "TEXTAREA") return;
  state[t.dataset.q] = state[t.dataset.q] || {dims:{}};
  state[t.dataset.q].note = t.value;
  localStorage.setItem(KEY, JSON.stringify(state));
});
document.getElementById("save").onclick = () =>
  alert("已保存在本机浏览器（localStorage）。可直接关闭页面，下次打开自动恢复。");
document.getElementById("export").onclick = () => {
  const head = ["review_id", ...DIMS, "note"];
  const rows = [head.join(",")];
  DATA.forEach(item => {
    const s = state[item.id] || {};
    const row = [item.id, ...DIMS.map(d => (s.dims||{})[d] || "")];
    row.push((s.note||"").replace(/"/g,'""').replace(/\\r?\\n/g," "));
    rows.push(row.map(v => `"${v}"`).join(","));
  });
  const blob = new Blob(["\\ufeff" + rows.join("\\n")], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "__REVIEWER___review_result.csv";
  a.click();
};
render();
</script></body></html>
"""

PAIR_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>小暖人格评审 · 配对比较</title>
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
  .card{background:#fff;border-radius:12px;padding:22px 24px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
  .card.done{border-left:4px solid #34c759}
  .qid{font-size:12px;color:#86868b;letter-spacing:.5px;margin-bottom:10px}
  .elder{background:#f5f5f7;padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:15px}
  .elder b{color:#6e6e73;font-weight:600;font-size:13px}
  .replies{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:720px){.replies{grid-template-columns:1fr}}
  .reply{border:1px solid #e5e5ea;border-radius:8px;padding:12px 14px;font-size:14.5px;white-space:pre-wrap}
  .reply h3{margin:0 0 8px;font-size:13px;color:#0071e3;font-weight:600}
  .dim{display:flex;align-items:center;gap:14px;padding:7px 0;border-top:1px solid #f0f0f2;font-size:14px}
  .dim:first-child{border-top:none}
  .dim>span{width:110px;color:#6e6e73;flex:none}
  .dim label{cursor:pointer;user-select:none}
  .dim input{margin-right:4px}
  .overall{margin-top:14px;padding-top:14px;border-top:2px solid #f0f0f2;font-weight:600}
  textarea{width:100%;margin-top:12px;padding:9px 12px;border:1px solid #e5e5ea;border-radius:8px;font:inherit;font-size:14px;resize:vertical}
  .hint{color:#86868b;font-size:13px;margin:4px 0 0}
</style></head><body>
<div class="top">
  <h1>小暖人格评审</h1>
  <span class="prog" id="prog">0 / 0</span>
  <div class="bar"><i id="bar"></i></div>
  <button class="ghost" id="save">暂存</button>
  <button id="export">导出结果 CSV</button>
</div>
<div class="wrap">
  <p class="hint">比较两段回复，按维度选出更好的那一段。选择自动保存在本机浏览器，关掉页面不丢。</p>
  <div id="list"></div>
</div>
<script>
const DIMS = __DIMS__;
const DATA = __DATA__;
const KEY = "xiaonuan_pair_v1___REVIEWER__";
let state = JSON.parse(localStorage.getItem(KEY) || "{}");
function render(){
  const list = document.getElementById("list");
  list.innerHTML = "";
  DATA.forEach((item, i) => {
    const s = state[item.id] || {};
    const card = document.createElement("div");
    card.className = "card" + (s.overall ? " done" : "");
    const dimHTML = DIMS.map(d => {
      const v = (s.dims||{})[d] || "";
      const opt = (val,label) => `<label><input type="radio" name="${item.id}_${d}" value="${val}"
        ${v===val?"checked":""} data-q="${item.id}" data-d="${d}">${label}</label>`;
      return `<div class="dim"><span>${d}</span>${opt("一","回复一")}${opt("二","回复二")}${opt("平","平")}</div>`;
    }).join("");
    const ov = (val,label) => `<label><input type="radio" name="${item.id}_overall" value="${val}"
      ${s.overall===val?"checked":""} data-q="${item.id}" data-d="__overall">${label}</label>`;
    card.innerHTML = `
      <div class="qid">#${i+1} / ${DATA.length}　${item.id}</div>
      <div class="elder"><b>老人说</b><br>${item.user}</div>
      <div class="replies">
        <div class="reply"><h3>回复一</h3>${item.a}</div>
        <div class="reply"><h3>回复二</h3>${item.b}</div>
      </div>
      <div class="dims">${dimHTML}</div>
      <div class="dim overall"><span>总体上</span>${ov("一","回复一更好")}${ov("二","回复二更好")}${ov("平","差不多")}</div>
      <textarea data-q="${item.id}" data-d="note" placeholder="备注（可选）">${s.note||""}</textarea>
    `;
    list.appendChild(card);
  });
  updateProgress();
}
function updateProgress(){
  const done = DATA.filter(d => (state[d.id]||{}).overall).length;
  document.getElementById("prog").textContent = done + " / " + DATA.length;
  document.getElementById("bar").style.width = (done/DATA.length*100) + "%";
}
document.addEventListener("change", e => {
  const t = e.target, q = t.dataset.q, d = t.dataset.d;
  if(!q || !d) return;
  state[q] = state[q] || {dims:{}};
  if(d === "__overall") state[q].overall = t.value;
  else { state[q].dims = state[q].dims || {}; state[q].dims[d] = t.value; }
  localStorage.setItem(KEY, JSON.stringify(state));
  const card = t.closest(".card");
  if(card) card.classList.toggle("done", !!state[q].overall);
  updateProgress();
});
document.addEventListener("input", e => {
  const t = e.target;
  if(t.tagName !== "TEXTAREA") return;
  state[t.dataset.q] = state[t.dataset.q] || {dims:{}};
  state[t.dataset.q].note = t.value;
  localStorage.setItem(KEY, JSON.stringify(state));
});
document.getElementById("save").onclick = () =>
  alert("已保存在本机浏览器（localStorage）。可直接关闭页面，下次打开自动恢复。");
document.getElementById("export").onclick = () => {
  const head = ["review_id","overall",...DIMS.map(d=>"dim_"+d),"note"];
  const rows = [head.join(",")];
  DATA.forEach(item => {
    const s = state[item.id] || {};
    const row = [item.id, s.overall||""];
    DIMS.forEach(d => row.push((s.dims||{})[d]||""));
    row.push((s.note||"").replace(/"/g,'""').replace(/\\r?\\n/g," "));
    rows.push(row.map(v=>`"${v}"`).join(","));
  });
  const blob = new Blob(["\\ufeff"+rows.join("\\n")], {type:"text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "__REVIEWER___review_result.csv";
  a.click();
};
render();
</script></body></html>
"""


def load_jsonl(path: Path) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def stratified_sample(items: list, rate_by_risk: dict, seed: int, key=lambda x: x["risk"]):
    rng = random.Random(seed)
    by_risk = defaultdict(list)
    for it in items:
        by_risk[key(it)].append(it)
    picked = []
    for risk in sorted(by_risk):
        pool = sorted(by_risk[risk], key=lambda u: json.dumps(u, sort_keys=True, ensure_ascii=False))
        rate = rate_by_risk.get(risk, 0.15)
        n = len(pool) if rate >= 1.0 else max(1, round(len(pool) * rate))
        picked.extend(rng.sample(pool, min(n, len(pool))))
    rng.shuffle(picked)
    return picked


def split_with_overlap(items: list, n: int, overlap_size: int, seed: int):
    """切成 n 份，并额外构造所有人共评的「重叠集」（用于算人-人一致性）。"""
    if n <= 1:
        return [("all", list(items))], []
    rng = random.Random(seed + 2000)
    shuffled = list(items)
    rng.shuffle(shuffled)
    overlap_size = max(0, min(overlap_size, len(shuffled) - n))
    overlap = shuffled[:overlap_size]
    rest = shuffled[overlap_size:]
    chunks = [[] for _ in range(n)]
    for i, it in enumerate(rest):
        chunks[i % n].append(it)
    groups = []
    for i, chunk in enumerate(chunks):
        own = chunk + overlap
        rng.shuffle(own)
        groups.append((f"r{i + 1}", own))
    return groups, overlap


# ══════════════════════════════════════════════════════════════
# 单条模式
# ══════════════════════════════════════════════════════════════

def build_single_items(rows: list) -> list:
    return [
        {
            "review_id": "",  # 抽样后统一编号
            "key": f"{r['scenario_id']}|{r['personality']}|{r['run']}",
            "scenario_id": r["scenario_id"],
            "risk": r["risk"],
            "category": r["category"],
            "user": r["user"],
            "reply": r["reply"],
            "personality": r["personality"],
            "run": r["run"],
            "llm_scores": r.get("scores") or {},
            "llm_mean": r.get("mean_score"),
        }
        for r in rows
    ]


def render_rubric_html() -> str:
    """把量表渲染成评审页顶部的说明块——人工看到的标准必须与模型评委逐字一致。"""
    import re as _re

    def md(s: str) -> str:
        return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)

    parts = [f'<div class="scalebox">{md(SCALE_NOTE)}</div>']
    for n in DIMENSIONS:
        d = RUBRIC[n]
        parts.append(
            f"<h4>{n}　<span style='color:#86868b;font-weight:400'>{d['desc']}</span></h4>"
        )
        for k, v in sorted(d["anchors"].items()):
            parts.append(f'<p class="a"><b>{k} 分</b>{v}</p>')
    parts.append("<h4>各场景篇幅对照（用于「简洁度」）</h4>")
    for line in LENGTH_TARGETS.strip().splitlines():
        parts.append(f'<p class="len">{line.lstrip("- ")}</p>')
    return "".join(parts)


def write_single_html(items: list, out_path: Path, reviewer: str) -> None:
    payload = [
        {
            "id": it["review_id"],
            "user": html.escape(it["user"]).replace("\n", "<br>"),
            "reply": html.escape(it["reply"]),
        }
        for it in items
    ]
    doc = (SINGLE_HTML
           .replace("__RUBRIC_HTML__", render_rubric_html())
           .replace("__DIMS__", json.dumps(DIMENSIONS, ensure_ascii=False))
           .replace("__HINTS__", json.dumps(DIMENSION_HINT, ensure_ascii=False))
           .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
           .replace("__REVIEWER__", reviewer))
    out_path.write_text(doc, encoding="utf-8")


def write_single_key(items: list, out_path: Path, overlap_ids: set) -> None:
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "scenario_id", "risk", "category", "是否重叠集",
                    "人格", "第几次", "大模型均分"]
                   + [f"大模型_{d}" for d in DIMENSIONS])
        for it in items:
            w.writerow([it["review_id"], it["scenario_id"], it["risk"], it["category"],
                        "是" if it["review_id"] in overlap_ids else "",
                        it["personality"], it["run"], it["llm_mean"]]
                       + [it["llm_scores"].get(d, "") for d in DIMENSIONS])


def write_single_csv(items: list, out_path: Path) -> None:
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "老人说", "助手回复"] + [f"你给_{d}" for d in DIMENSIONS] + ["备注"])
        for it in items:
            w.writerow([it["review_id"], it["user"], it["reply"]] + [""] * len(DIMENSIONS) + [""])


# ══════════════════════════════════════════════════════════════
# 配对模式
# ══════════════════════════════════════════════════════════════

def build_pair_items(rows: list) -> list:
    idx = defaultdict(list)
    for r in rows:
        idx[(r["scenario_id"], r["personality"])].append(r)
    meta = {r["scenario_id"]: r for r in rows}
    items = []
    for si, sid in enumerate(sorted(meta)):
        for p1, p2 in combinations(PERSONAS, 2):
            a_pool = sorted(idx.get((sid, p1), []), key=lambda r: r["run"])
            b_pool = sorted(idx.get((sid, p2), []), key=lambda r: r["run"])
            if not a_pool or not b_pool:
                continue
            a, b = a_pool[si % len(a_pool)], b_pool[si % len(b_pool)]
            items.append({
                "scenario_id": sid, "risk": meta[sid]["risk"], "category": meta[sid]["category"],
                "user": meta[sid]["user"],
                "reply_1": a["reply"], "reply_2": b["reply"],
                "persona_1": p1, "persona_2": p2, "run_1": a["run"], "run_2": b["run"],
            })
    return items


def blind_pairs(items: list, seed: int) -> list:
    rng = random.Random(seed + 1000)
    out = []
    for i, u in enumerate(items, 1):
        flip = rng.random() < 0.5
        out.append({
            "review_id": f"r{i:03d}",
            "scenario_id": u["scenario_id"], "risk": u["risk"], "category": u["category"],
            "user": u["user"],
            "a": u["reply_2"] if flip else u["reply_1"],
            "b": u["reply_1"] if flip else u["reply_2"],
            "pos1_persona": u["persona_2"] if flip else u["persona_1"],
            "pos2_persona": u["persona_1"] if flip else u["persona_2"],
            "pos1_run": u["run_2"] if flip else u["run_1"],
            "pos2_run": u["run_1"] if flip else u["run_2"],
        })
    return out


def write_pair_html(items: list, out_path: Path, reviewer: str) -> None:
    payload = [{"id": it["review_id"], "user": html.escape(it["user"]).replace("\n", "<br>"),
                "a": html.escape(it["a"]), "b": html.escape(it["b"])} for it in items]
    doc = (PAIR_HTML
           .replace("__DIMS__", json.dumps(DIMENSIONS, ensure_ascii=False))
           .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
           .replace("__REVIEWER__", reviewer))
    out_path.write_text(doc, encoding="utf-8")


def write_pair_key(items: list, out_path: Path, overlap_ids: set) -> None:
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "scenario_id", "risk", "category", "是否重叠集",
                    "回复一_人格", "回复一_第几次", "回复二_人格", "回复二_第几次"])
        for it in items:
            w.writerow([it["review_id"], it["scenario_id"], it["risk"], it["category"],
                        "是" if it["review_id"] in overlap_ids else "",
                        it["pos1_persona"], it["pos1_run"], it["pos2_persona"], it["pos2_run"]])


def write_pair_csv(items: list, out_path: Path) -> None:
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "老人说", "回复一", "回复二", "总体上", "备注"]
                   + [f"维度_{d}" for d in DIMENSIONS])
        for it in items:
            w.writerow([it["review_id"], it["user"], it["a"], it["b"], "", ""] + [""] * len(DIMENSIONS))


# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="生成人工抽检材料")
    ap.add_argument("--mode", choices=["single", "pair"], default="single")
    ap.add_argument("--input", default="")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--reviewers", type=int, default=4)
    ap.add_argument("--overlap", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if args.input:
        in_path = Path(args.input)
    else:
        pattern = "personality_scores_*.jsonl" if args.mode == "single" else "personality_gen_fair_*.jsonl"
        cands = sorted(p for p in RESULTS_DIR.glob(pattern) if not p.name.startswith("_"))
        if not cands:
            print(f"[错误] 未找到输入文件（{pattern}）")
            sys.exit(1)
        in_path = cands[-1]

    outdir = Path(args.outdir) if args.outdir else (EXAMPLES_DIR / "manual_review")
    outdir.mkdir(parents=True, exist_ok=True)

    rows = load_jsonl(in_path)
    print("=" * 70)
    print(f"  人工抽检材料生成 · {args.mode} 模式")
    print(f"  输入: {in_path.name}（{len(rows)} 条）")
    print("=" * 70)

    if args.mode == "single":
        pool = build_single_items(rows)
        picked = stratified_sample(pool, SINGLE_RATE_BY_RISK, args.seed)
        for i, it in enumerate(picked, 1):
            it["review_id"] = f"v{i:03d}"
        write_html, write_key, write_csv = write_single_html, write_single_key, write_single_csv
    else:
        pairs = build_pair_items(rows)
        picked = stratified_sample(pairs, PAIR_RATE_BY_RISK, args.seed)
        picked = blind_pairs(picked, args.seed)
        write_html, write_key, write_csv = write_pair_html, write_pair_key, write_pair_csv

    total_pool = len(rows) if args.mode == "single" else len(build_pair_items(rows))
    print(f"\n  候选 {total_pool} → 抽中 {len(picked)}"
          f"（{len(picked) / total_pool * 100:.0f}%）")
    dist = defaultdict(int)
    for it in picked:
        dist[it["risk"]] += 1
    print("  分布: " + "  ".join(f"{k}×{dist[k]}" for k in sorted(dist)))

    groups, overlap = split_with_overlap(picked, args.reviewers, args.overlap, args.seed)
    overlap_ids = {it["review_id"] for it in overlap}

    for reviewer, items in groups:
        name = ("manual_review.html" if reviewer == "all"
                else f"manual_review_{args.mode}_{reviewer}.html")
        write_html(items, outdir / name, reviewer)
        n_over = sum(1 for it in items if it["review_id"] in overlap_ids)
        print(f"\n  评审页 → {outdir / name}")
        print(f"            {len(items)} 条 = 独有 {len(items) - n_over} + 重叠 {n_over}")

    total_judgments = sum(len(items) for _, items in groups)
    if len(groups) > 1:
        print(f"\n  重叠集: {len(overlap_ids)} 条由全部 {len(groups)} 位评分员共评"
              f"（用于算人-人一致性）")
        print(f"  人工判断总量: {total_judgments} 条（人均 {total_judgments // len(groups)} 条）")

    csv_name = ("manual_review_all.csv" if len(groups) > 1
                else "manual_review.csv")
    write_csv(picked, outdir / csv_name)
    print(f"\n  Excel 备用 → {outdir / csv_name}")

    key_path = outdir / f"manual_review_KEY_{args.mode}_请勿提前打开.csv"
    write_key(picked, key_path, overlap_ids)
    print(f"  答案键 → {key_path}   ⚠️ 评完再看")
    print(f"\n  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
