/* ==========================================================================
   小暖 · 人格选型评测报告页逻辑
   数据来源：frontend/data/personality_evaluation.js
            （由 tools/export_persona_report_data.py 从 tests/results/ 生成）
   本文件只负责渲染，不含任何硬编码的评分结论。
   ========================================================================== */

(function () {
  "use strict";

  var DATA = window.__PERSONA_EVAL__;
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function fmt(n) {
    return (n === null || n === undefined) ? "—" : n;
  }

  function renderFallback() {
    var msg = "评测数据未加载。请先运行 tools/export_persona_report_data.py " +
      "生成 frontend/data/personality_evaluation.js。";
    ["personaGrid", "coverageTable", "crossTable", "dimTable",
      "findingsGrid", "conclusionBox", "robustBox"].forEach(function (id) {
      var node = $(id);
      if (node) node.textContent = msg;
    });
  }

  /* ── 人格卡片 ───────────────────────────────────────────── */
  function renderPersonas() {
    var grid = $("personaGrid");
    DATA.personas.forEach(function (p) {
      var card = el("article", "persona");
      card.style.setProperty("--persona-color", p.color);
      card.appendChild(el("div", "persona__rank tnum", String(fmt(p.absolute.rank))));
      card.appendChild(el("div", "persona__icon", p.icon));
      card.appendChild(el("h3", "persona__name", p.name));
      card.appendChild(el("p", "persona__tagline", p.tagline));
      p.traits.forEach(function (t) {
        card.appendChild(el("div", "persona__trait", t));
      });

      var ranks = el("div", "persona__ranks");
      [["绝对打分", p.absolute.rank], ["人工抽检", p.human.rank], ["强制排序", p.ranking.rank]]
        .forEach(function (pair) {
          ranks.appendChild(el("span", "rank-chip", pair[0] + " #" + fmt(pair[1])));
        });
      card.appendChild(ranks);

      var score = el("div", "persona__score");
      score.appendChild(el("span", "val tnum", fmt(p.absolute.mean)));
      score.appendChild(el("span", "max", "/ 5 绝对打分"));
      card.appendChild(score);
      grid.appendChild(card);
    });
  }

  /* ── 场景覆盖表 ─────────────────────────────────────────── */
  function renderCoverage() {
    var table = $("coverageTable");
    var thead = el("thead");
    var hr = el("tr");
    ["风险等级", "分组", "内容", "场景数"].forEach(function (h) {
      var th = el("th", null, h);
      th.scope = "col";
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el("tbody");
    var total = 0;
    DATA.scenario_coverage.forEach(function (c) {
      total += c.count;
      var row = el("tr");
      var th = el("th", null, c.risk);
      th.scope = "row";
      row.appendChild(th);
      row.appendChild(el("td", null, c.group));
      var desc = el("td", null, c.desc);
      desc.style.textAlign = "left";
      row.appendChild(desc);
      row.appendChild(el("td", "tnum", String(c.count)));
      tbody.appendChild(row);
    });
    var sum = el("tr", "total");
    sum.appendChild(el("th", null, "合计"));
    sum.appendChild(el("td", null, ""));
    sum.appendChild(el("td", null, ""));
    sum.appendChild(el("td", "tnum", String(total)));
    tbody.appendChild(sum);
    table.appendChild(tbody);
  }

  /* ── 绝对打分柱状图 ─────────────────────────────────────── */
  function renderBars() {
    var chart = $("barChart");
    var max = 5;
    var base = 3.5; // 从 3.5 起画，突出差异
    DATA.personas.forEach(function (p) {
      var v = p.absolute.mean || 0;
      var col = el("div", "bar-col");
      var bar = el("div", "bar tnum", fmt(v));
      var h = Math.max(0.4, ((v - base) / (max - base)) * 12).toFixed(2);
      bar.style.height = h + "rem";
      bar.style.background = "linear-gradient(180deg," + p.color + "," + p.color + "cc)";
      col.appendChild(bar);
      col.appendChild(el("div", "bar-label", p.name));
      var ci = p.absolute.lo != null
        ? "95% CI [" + p.absolute.lo + ", " + p.absolute.hi + "]" : "";
      col.appendChild(el("div", "bar-sub", ci));
      chart.appendChild(col);
    });
  }

  /* ── 三法交叉表 + 稳健性判断 ────────────────────────────── */
  function renderCross() {
    var table = $("crossTable");
    var thead = el("thead");
    var hr = el("tr");
    ["人格", "强制排序", "人工抽检", "绝对打分", "综合判断"].forEach(function (h) {
      var th = el("th", null, h);
      th.scope = "col";
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el("tbody");
    var robust = [];
    var excluded = [];
    DATA.cross_method.forEach(function (c) {
      var ranks = [c.ranking, c.human, c.absolute];
      var verdict;
      if (ranks.every(function (r) { return r != null && r <= 2; })) {
        verdict = "稳健候选";
        robust.push(c.persona);
      } else if (ranks.every(function (r) { return r != null && r >= 3; })) {
        verdict = "建议排除";
        excluded.push(c.persona);
      } else {
        verdict = "方法依赖";
      }
      var row = el("tr");
      var th = el("th", null, c.persona);
      th.scope = "row";
      row.appendChild(th);
      row.appendChild(el("td", "tnum", "#" + fmt(c.ranking)));
      row.appendChild(el("td", "tnum", "#" + fmt(c.human)));
      row.appendChild(el("td", "tnum", "#" + fmt(c.absolute)));
      var v = el("td", null, verdict);
      if (verdict === "稳健候选") v.style.color = "#2E7D46";
      if (verdict === "建议排除") v.style.color = "#B42318";
      if (verdict === "方法依赖") v.style.color = "#8A6D3B";
      row.appendChild(v);
      tbody.appendChild(row);
    });
    table.appendChild(tbody);

    var box = $("robustBox");
    box.textContent = "";
    var h3 = el("h3", null, "跨方法稳健性");
    box.appendChild(h3);
    box.appendChild(el("p", null,
      "稳健候选：" + (robust.join("、") || "无") +
      "；建议排除：" + (excluded.join("、") || "无") +
      "。结论基于「跨方法一致的部分」，而非任一方法的最高分。"));
    var a = DATA.agreement || {};
    box.appendChild(el("p", null,
      "人机一致性：人工较大模型系统性偏严 " + Math.abs(fmt(a.bias)) + " 分" +
      "（逐条相关 ρ = " + fmt(a.rho) + "，完全一致 " + fmt(a.exact_pct) + "%，" +
      "相差 ≤1 分 " + fmt(a.within1_pct) + "%）——大模型评分只能作辅助信号，不能替代人工。"));
  }

  /* ── 分维度表 ───────────────────────────────────────────── */
  function renderDimensions() {
    var table = $("dimTable");
    var names = DATA.personas.map(function (p) { return p.name; });
    var thead = el("thead");
    var hr = el("tr");
    ["维度"].concat(names).concat(["区分度"]).forEach(function (h) {
      var th = el("th", null, h);
      th.scope = "col";
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el("tbody");
    DATA.dimensions.forEach(function (d) {
      var row = el("tr");
      var th = el("th", null, d.name);
      th.scope = "row";
      row.appendChild(th);
      names.forEach(function (n) {
        row.appendChild(el("td", "tnum", fmt(d.values[n])));
      });
      var spread = el("td", "tnum", fmt(d.spread));
      if (d.spread >= 0.5) spread.style.fontWeight = "700";
      row.appendChild(spread);
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
  }

  /* ── 方法学发现 ─────────────────────────────────────────── */
  function renderFindings() {
    var grid = $("findingsGrid");
    DATA.findings.forEach(function (f) {
      var card = el("article", "response");
      card.appendChild(el("h3", "response__title", f.title));
      card.appendChild(el("p", "response__text", f.body));
      grid.appendChild(card);
    });
  }

  /* ── 结论 ───────────────────────────────────────────────── */
  function renderConclusion() {
    var box = $("conclusionBox");
    box.appendChild(el("h3", null, "🏆 " + DATA.conclusion.title));
    DATA.conclusion.paragraphs.forEach(function (t) {
      box.appendChild(el("p", null, t));
    });
    var meta = DATA.meta || {};
    box.appendChild(el("p", null,
      "数据规模：场景 " + fmt(meta.scenarios) + " · 回复 " + fmt(meta.replies) +
      " · 强制排序 " + fmt(meta.ranking_rounds) + " 轮 · 人工抽检 " + fmt(meta.human_reviews) +
      " 条 · 模型 " + fmt(meta.model) + "。"));
  }

  /* ── 滚动入场 ───────────────────────────────────────────── */
  function initReveal() {
    var items = document.querySelectorAll(".reveal");
    function showAll() {
      Array.prototype.forEach.call(items, function (n) { n.classList.add("is-visible"); });
    }
    var reduce = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !("IntersectionObserver" in window)) {
      showAll();
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add("is-visible");
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.12 });
    Array.prototype.forEach.call(items, function (n) { io.observe(n); });
    setTimeout(showAll, 700);
  }

  if (!DATA) {
    renderFallback();
    initReveal();
    return;
  }

  renderPersonas();
  renderCoverage();
  renderBars();
  renderCross();
  renderDimensions();
  renderFindings();
  renderConclusion();
  initReveal();
})();
