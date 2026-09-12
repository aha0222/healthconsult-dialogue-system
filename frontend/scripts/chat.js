/* ==========================================================================
   小暖 · 对话界面逻辑
   - 流式对话（SSE）
   - 会话历史 / 删除
   - 人格切换、后端地址与访问密钥
   - 语音输入（STT）与语音播报（TTS）
   - 适老化：字体档位、高对比模式
   ========================================================================== */

(function () {
  "use strict";

  var STORAGE_KEY = "xiaonuan_settings";

  var PERSONALITY_DESCS = {
    "温婉邻居型": "温婉端庄，先安抚再建议",
    "贴心闺女型": "亲切软糯，情感模式",
    "素朴家常型": "朴素接地气，大白话",
    "从容守护型": "淡定从容，一二三讲清楚",
  };

  var RISK_LABEL_MAP = {
    S0: "人身安全", S1: "环境安全", S2: "防诈骗",
    M0: "心理危机", M1: "情绪困扰",
    R3: "急症120", R2b: "紧急就医", R2a: "尽快就医",
    R1: "一般关注", R0: "日常", X: "非健康",
  };

  var ASSISTANT_AVATAR = "assets/温婉晚辈头像.webp";
  var USER_AVATAR = "assets/老人卡通形象头像.webp";

  var $ = function (id) { return document.getElementById(id); };
  var chatMain = $("chatMain");
  var messagesEl = $("messages");
  var welcomeEl = $("welcome");
  var typingEl = $("typing");
  var inputEl = $("userInput");
  var sendBtn = $("sendBtn");
  var micBtn = $("micBtn");
  var statusText = $("statusText");
  var liveStatus = $("liveStatus");
  var composerForm = $("composerForm");
  var settingsDialog = $("settingsDialog");
  var historyDialog = $("historyDialog");
  var sessionListEl = $("sessionList");
  var personalitySelect = $("personalitySelect");
  var backendUrlEl = $("backendUrl");
  var backendApiKeyEl = $("backendApiKey");
  var autoTtsEl = $("autoTts");
  var hcModeEl = $("hcMode");
  var fontSizeSeg = $("fontSizeSeg");

  var currentPersonality = "温婉邻居型";
  var autoTtsEnabled = false;
  var currentFontSize = "normal";
  var hcEnabled = false;
  var isGenerating = false;
  var isListening = false;
  var messages = [];
  var sessionId = null;

  var synth = window.speechSynthesis;
  var zhVoice = null;
  var recognition = null;

  var dateFmt = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });

  /* ── 小工具 ─────────────────────────────────────────────── */
  function icon(name, cls) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", cls || "icon");
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#" + name);
    svg.appendChild(use);
    return svg;
  }

  function announce(text) {
    if (liveStatus) liveStatus.textContent = text;
  }

  function formatTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d.getTime()) ? "" : dateFmt.format(d);
  }

  function scrollToBottom() {
    chatMain.scrollTop = chatMain.scrollHeight;
  }

  function setRiskAmbient(risk) {
    if (risk) document.body.setAttribute("data-risk", risk);
    else document.body.removeAttribute("data-risk");
  }

  /* ── Markdown 渲染（先转义，再生成受控标签，避免 XSS） ──── */
  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function inlineMarkdown(text) {
    return text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
  }

  function renderMarkdown(text) {
    var lines = escapeHtml(text).split(/\r?\n/);
    var html = [];
    var listType = null;
    var inCode = false;
    var para = [];

    function closeList() {
      if (listType) { html.push("</" + listType + ">"); listType = null; }
    }
    function flushPara() {
      if (para.length) {
        html.push("<p>" + inlineMarkdown(para.join("<br>")) + "</p>");
        para = [];
      }
    }

    lines.forEach(function (line) {
      if (/^\s*```/.test(line)) {
        if (inCode) { html.push("</code></pre>"); inCode = false; }
        else { flushPara(); closeList(); html.push("<pre><code>"); inCode = true; }
        return;
      }
      if (inCode) { html.push(line); return; }

      var trimmed = line.trim();
      if (!trimmed) { flushPara(); closeList(); return; }

      var heading = trimmed.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        flushPara(); closeList();
        var level = heading[1].length;
        html.push("<h" + level + ">" + inlineMarkdown(heading[2]) + "</h" + level + ">");
        return;
      }

      if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
        flushPara(); closeList(); html.push("<hr>"); return;
      }

      var bullet = trimmed.match(/^[-*+]\s+(.*)$/);
      if (bullet) {
        flushPara();
        if (listType !== "ul") { closeList(); html.push("<ul>"); listType = "ul"; }
        html.push("<li>" + inlineMarkdown(bullet[1]) + "</li>");
        return;
      }

      var ordered = trimmed.match(/^\d+[.)]\s+(.*)$/);
      if (ordered) {
        flushPara();
        if (listType !== "ol") { closeList(); html.push("<ol>"); listType = "ol"; }
        html.push("<li>" + inlineMarkdown(ordered[1]) + "</li>");
        return;
      }

      var quote = trimmed.match(/^&gt;\s?(.*)$/);
      if (quote) {
        flushPara(); closeList();
        html.push("<blockquote>" + inlineMarkdown(quote[1]) + "</blockquote>");
        return;
      }

      para.push(trimmed);
    });

    if (inCode) html.push("</code></pre>");
    flushPara();
    closeList();
    return html.join("");
  }

  /* ── 初始化 ─────────────────────────────────────────────── */
  function init() {
    loadVoices();
    if (synth && synth.onvoiceschanged !== undefined) {
      synth.onvoiceschanged = loadVoices;
    }
    initSpeechRecognition();
    loadSettings();
    bindEvents();
    updateStatus();
  }

  function loadSettings() {
    var saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch (e) {
      saved = {};
    }
    if (saved.backendUrl) backendUrlEl.value = saved.backendUrl;
    if (saved.backendApiKey) backendApiKeyEl.value = saved.backendApiKey;
    if (saved.personality && PERSONALITY_DESCS[saved.personality]) {
      personalitySelect.value = saved.personality;
      currentPersonality = saved.personality;
    }
    autoTtsEnabled = !!saved.autoTts;
    autoTtsEl.checked = autoTtsEnabled;
    hcEnabled = !!saved.hc;
    hcModeEl.checked = hcEnabled;
    applyHighContrast(hcEnabled);
    setFontSize(saved.fontSize || "normal", false);
  }

  function saveSettings() {
    var personality = personalitySelect.value;
    var backendUrl = backendUrlEl.value.trim();
    var backendApiKey = backendApiKeyEl.value.trim();
    var autoTts = autoTtsEl.checked;
    var hc = hcModeEl.checked;

    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      backendUrl: backendUrl,
      backendApiKey: backendApiKey,
      personality: personality,
      autoTts: autoTts,
      fontSize: currentFontSize,
      hc: hc,
    }));

    if (personality !== currentPersonality) {
      currentPersonality = personality;
      showSystemMsg("已切换为「" + personality + "」");
    }
    autoTtsEnabled = autoTts;
    hcEnabled = hc;
    applyHighContrast(hc);
    updateStatus();
    settingsDialog.close();
  }

  function updateStatus() {
    var desc = PERSONALITY_DESCS[currentPersonality] || "";
    statusText.textContent = currentPersonality + " · " + desc;
  }

  function applyHighContrast(on) {
    document.documentElement.classList.toggle("hc", on);
  }

  function setFontSize(size, save) {
    currentFontSize = size;
    document.documentElement.classList.remove("fs-normal", "fs-large", "fs-xlarge");
    document.documentElement.classList.add("fs-" + size);
    Array.prototype.forEach.call(fontSizeSeg.querySelectorAll("button"), function (btn) {
      btn.setAttribute("aria-pressed", String(btn.dataset.size === size));
    });
    if (save) {
      var saved = {};
      try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch (e) {}
      saved.fontSize = size;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    }
  }

  /* ── 事件绑定 ───────────────────────────────────────────── */
  function bindEvents() {
    $("btnSettings").addEventListener("click", function () { settingsDialog.showModal(); });
    $("btnHistory").addEventListener("click", openHistory);
    $("btnNew").addEventListener("click", newConversation);
    $("saveSettings").addEventListener("click", saveSettings);

    Array.prototype.forEach.call(document.querySelectorAll("[data-close]"), function (btn) {
      btn.addEventListener("click", function () {
        var d = btn.closest("dialog");
        if (d) d.close();
      });
    });

    fontSizeSeg.addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-size]");
      if (btn) setFontSize(btn.dataset.size, true);
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-quick]"), function (btn) {
      btn.addEventListener("click", function () { quickAsk(btn.dataset.quick); });
    });

    composerForm.addEventListener("submit", function (e) {
      e.preventDefault();
      sendMessage();
    });

    inputEl.addEventListener("input", function () { autoResize(inputEl); });
    inputEl.addEventListener("keydown", handleKeyDown);
    micBtn.addEventListener("click", toggleVoiceInput);
  }

  function autoResize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 144) + "px";
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      sendMessage();
    }
  }

  function quickAsk(text) {
    inputEl.value = text;
    autoResize(inputEl);
    sendMessage();
  }

  function authHeaders() {
    var headers = { "Content-Type": "application/json" };
    var key = backendApiKeyEl.value.trim();
    if (key) headers["X-API-Key"] = key;
    return headers;
  }

  function backendBase() {
    return backendUrlEl.value.trim().replace(/\/+$/, "");
  }

  /* ── 语音播报 TTS ───────────────────────────────────────── */
  function loadVoices() {
    if (!synth) return;
    var voices = synth.getVoices();
    zhVoice = voices.find(function (v) {
      return v.lang && v.lang.toLowerCase().indexOf("zh") === 0;
    }) || voices.find(function (v) {
      return v.lang && v.lang.toLowerCase().indexOf("cmn") === 0;
    }) || null;
  }

  function speak(text) {
    if (!window.SpeechSynthesisUtterance || !synth) {
      showSystemMsg("当前浏览器不支持语音播报");
      return;
    }
    if (synth.speaking) synth.cancel();
    var clean = (text || "").replace(/\s*\[(?:SITUATION|S|MENTAL|RISK|OTHER):[^\]]+\]\s*$/g, "").trim();
    if (!clean) return;
    var u = new SpeechSynthesisUtterance(clean);
    u.voice = zhVoice;
    u.lang = "zh-CN";
    u.rate = 0.9;
    u.pitch = 1;
    synth.speak(u);
  }

  /* ── 语音输入 STT ───────────────────────────────────────── */
  function initSpeechRecognition() {
    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return;
    recognition = new SpeechRecognition();
    recognition.lang = "zh-CN";
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onresult = function (event) {
      var finalText = "";
      var interim = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var t = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += t;
        else interim += t;
      }
      inputEl.value = (inputEl.dataset.finalText || "") + finalText + interim;
      autoResize(inputEl);
      if (finalText) inputEl.dataset.finalText = (inputEl.dataset.finalText || "") + finalText;
    };

    recognition.onerror = function (event) {
      console.warn("语音识别错误", event.error);
      showSystemMsg("语音识别出错，请检查麦克风权限", true);
      stopListening();
    };

    recognition.onend = function () {
      if (isListening) {
        try { recognition.start(); } catch (e) { /* ignore */ }
      } else {
        stopListening();
      }
    };
  }

  function toggleVoiceInput() {
    if (!recognition) {
      showSystemMsg("您的浏览器不支持语音识别", true);
      return;
    }
    if (isListening) {
      isListening = false;
      try { recognition.stop(); } catch (e) { /* ignore */ }
      stopListening();
    } else {
      inputEl.value = "";
      inputEl.dataset.finalText = "";
      autoResize(inputEl);
      isListening = true;
      micBtn.classList.add("is-listening");
      micBtn.setAttribute("aria-pressed", "true");
      try { recognition.start(); } catch (e) { /* ignore */ }
      showSystemMsg("正在听，请说话…");
      announce("正在听，请说话");
    }
  }

  function stopListening() {
    isListening = false;
    micBtn.classList.remove("is-listening");
    micBtn.setAttribute("aria-pressed", "false");
  }

  /* ── 消息渲染 ───────────────────────────────────────────── */
  function avatarNode(role) {
    var span = document.createElement("div");
    span.className = "msg__avatar";
    var img = document.createElement("img");
    img.width = 46;
    img.height = 46;
    img.alt = "";
    img.src = role === "user" ? USER_AVATAR : ASSISTANT_AVATAR;
    img.onerror = function () {
      img.remove();
      span.textContent = role === "user" ? "🧓" : "🌿";
    };
    span.appendChild(img);
    return span;
  }

  function addMessage(role, content, risk, silent) {
    welcomeEl.hidden = true;

    var msg = document.createElement("div");
    msg.className = "msg msg--" + role;

    var body = document.createElement("div");
    body.className = "msg__body";

    var bubble = document.createElement("div");
    bubble.className = "bubble";
    if (role === "assistant") bubble.innerHTML = renderMarkdown(content);
    else bubble.textContent = content || "";
    body.appendChild(bubble);

    var meta = document.createElement("div");
    meta.className = "msg__meta";
    body.appendChild(meta);

    if (role === "assistant") {
      var tts = document.createElement("button");
      tts.type = "button";
      tts.className = "tts-btn";
      tts.setAttribute("aria-label", "朗读这条回复");
      tts.title = "朗读";
      tts.appendChild(icon("i-speaker"));
      tts.addEventListener("click", function () { speak(bubble.textContent); });
      meta.appendChild(tts);
    }

    if (risk) updateMessageRisk(msg, risk);

    msg.appendChild(avatarNode(role));
    msg.appendChild(body);
    messagesEl.appendChild(msg);

    if (role === "assistant" && autoTtsEnabled && content && !silent) speak(bubble.textContent);
    scrollToBottom();
    return { el: msg, bubble: bubble, meta: meta };
  }

  function setBubbleText(msg, text) {
    msg.bubble.innerHTML = renderMarkdown(text);
    scrollToBottom();
  }

  function updateMessageRisk(msg, risk) {
    var existing = msg.meta.querySelector("[class^='risk--']");
    if (existing) existing.remove();
    var label = RISK_LABEL_MAP[risk];
    if (!label) return;
    var span = document.createElement("span");
    span.className = "risk--" + risk;
    span.textContent = label;
    msg.meta.insertBefore(span, msg.meta.firstChild);
  }

  function showSystemMsg(text, isError) {
    welcomeEl.hidden = true;
    var div = document.createElement("div");
    div.className = "status-line" + (isError ? " status-line--error" : "");
    div.textContent = text;
    messagesEl.appendChild(div);
    scrollToBottom();
  }

  function setTyping(show) {
    typingEl.hidden = !show;
    sendBtn.disabled = show;
    if (show) announce("小暖正在回复");
  }

  function newConversation() {
    if (isGenerating) return;
    sessionId = null;
    messages = [];
    messagesEl.textContent = "";
    welcomeEl.hidden = false;
    setRiskAmbient(null);
    showSystemMsg("已开始新对话");
  }

  /* ── 历史对话 ───────────────────────────────────────────── */
  function openHistory() {
    historyDialog.showModal();
    var base = backendBase();
    if (!base) {
      renderSessionEmpty("请先在设置里配置后端地址");
      return;
    }
    sessionListEl.innerHTML = "";
    var loading = document.createElement("p");
    loading.className = "empty";
    loading.textContent = "加载中…";
    sessionListEl.appendChild(loading);

    fetch(base + "/api/sessions", { headers: authHeaders() })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(renderSessionList)
      .catch(function (e) {
        renderSessionEmpty("加载失败：" + e.message);
      });
  }

  function renderSessionEmpty(text) {
    sessionListEl.innerHTML = "";
    var p = document.createElement("p");
    p.className = "empty";
    p.textContent = text;
    sessionListEl.appendChild(p);
  }

  function renderSessionList(sessions) {
    if (!sessions || !sessions.length) {
      renderSessionEmpty("还没有历史对话");
      return;
    }
    sessionListEl.innerHTML = "";
    sessions.forEach(function (s) {
      var row = document.createElement("div");
      row.className = "session-item";

      var open = document.createElement("button");
      open.type = "button";
      open.className = "session-item__open" + (s.id === sessionId ? " is-active" : "");
      open.addEventListener("click", function () { loadSession(s.id); });

      var info = document.createElement("div");
      info.className = "session-item__info";
      var title = document.createElement("div");
      title.className = "session-item__title";
      title.textContent = s.personality || "小暖";
      var sub = document.createElement("div");
      sub.className = "session-item__sub tnum";
      sub.textContent = (s.message_count || 0) + " 条消息 · " + formatTime(s.updated_at);
      info.appendChild(title);
      info.appendChild(sub);
      open.appendChild(info);

      var del = document.createElement("button");
      del.type = "button";
      del.className = "session-del";
      del.setAttribute("aria-label", "删除会话：" + (s.personality || "小暖"));
      del.appendChild(icon("i-trash", "icon icon--sm"));
      del.addEventListener("click", function () { deleteSession(s.id); });

      row.appendChild(open);
      row.appendChild(del);
      sessionListEl.appendChild(row);
    });
  }

  function loadSession(id) {
    fetch(backendBase() + "/api/sessions/" + encodeURIComponent(id), { headers: authHeaders() })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (detail) {
        renderSessionMessages(detail);
        historyDialog.close();
      })
      .catch(function (e) {
        showSystemMsg("加载会话失败：" + e.message, true);
      });
  }

  function renderSessionMessages(detail) {
    sessionId = detail.id;
    messages = [];
    messagesEl.textContent = "";
    welcomeEl.hidden = true;

    (detail.messages || []).forEach(function (m) {
      addMessage(m.role, m.content, m.role === "assistant" ? m.risk : null, true);
      messages.push({ role: m.role, content: m.content });
    });

    if (detail.personality) {
      currentPersonality = detail.personality;
      personalitySelect.value = detail.personality;
      updateStatus();
    }
    showSystemMsg("已载入历史对话");
  }

  function deleteSession(id) {
    if (!window.confirm("确定删除这段对话吗？")) return;
    fetch(backendBase() + "/api/sessions/" + encodeURIComponent(id), {
      method: "DELETE",
      headers: authHeaders(),
    })
      .then(function (resp) {
        if (!resp.ok && resp.status !== 404) throw new Error("HTTP " + resp.status);
        if (id === sessionId) newConversation();
        openHistory();
      })
      .catch(function (e) {
        showSystemMsg("删除失败：" + e.message, true);
      });
  }

  /* ── SSE 解析 ───────────────────────────────────────────── */
  function parseSSEBlock(block) {
    var event = "message";
    var dataLines = [];
    block.split("\n").forEach(function (line) {
      if (line.indexOf("event:") === 0) event = line.slice(6).trim();
      else if (line.indexOf("data:") === 0) dataLines.push(line.slice(5).trim());
    });
    if (!dataLines.length) return null;
    try {
      return { event: event, data: JSON.parse(dataLines.join("\n")) };
    } catch (e) {
      return null;
    }
  }

  /* ── 发送消息 ───────────────────────────────────────────── */
  function sendMessage() {
    if (isGenerating) return;
    var text = inputEl.value.trim();
    if (!text) return;

    var base = backendBase();
    if (!base) {
      showSystemMsg("请先点击右上角「设置」配置后端地址", true);
      settingsDialog.showModal();
      return;
    }

    if (isListening) toggleVoiceInput();

    addMessage("user", text, null);
    messages.push({ role: "user", content: text });

    inputEl.value = "";
    inputEl.dataset.finalText = "";
    autoResize(inputEl);
    setTyping(true);
    isGenerating = true;

    var payload = { message: text, personality: currentPersonality };
    if (sessionId) payload.session_id = sessionId;
    else payload.history = messages.slice(0, -1);

    fetch(base + "/api/chat/stream", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(payload),
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().catch(function () { return {}; }).then(function (err) {
            throw new Error(err.detail || ("HTTP " + resp.status));
          });
        }
        if (!resp.body) throw new Error("当前浏览器不支持流式响应");
        return readStream(resp.body);
      })
      .catch(function (e) {
        showSystemMsg("请求出错：" + e.message, true);
        announce("请求出错：" + e.message);
      })
      .then(function () {
        setTyping(false);
        isGenerating = false;
      });
  }

  function readStream(body) {
    var msg = addMessage("assistant", "", null);
    msg.el.classList.add("is-thinking");
    var reader = body.getReader();
    var decoder = new TextDecoder("utf-8");
    var buffer = "";
    var assistantText = "";
    var finished = false;

    function pump() {
      return reader.read().then(function (result) {
        if (result.done) {
          if (!finished) {
            setBubbleText(msg, assistantText);
            messages.push({ role: "assistant", content: assistantText });
          }
          msg.el.classList.remove("is-thinking");
          return;
        }
        buffer += decoder.decode(result.value, { stream: true });
        var idx;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          var block = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          var evt = parseSSEBlock(block);
          if (!evt) continue;
          if (evt.event === "delta") {
            assistantText += evt.data.text || "";
            setBubbleText(msg, assistantText);
          } else if (evt.event === "done") {
            assistantText = evt.data.reply || assistantText;
            setBubbleText(msg, assistantText);
            updateMessageRisk(msg, evt.data.risk);
            setRiskAmbient(evt.data.risk);
            if (evt.data.violations && evt.data.violations.length) {
              console.warn("[小暖质检]", evt.data.violations);
            }
            if (evt.data.session_id) sessionId = evt.data.session_id;
            messages.push({ role: "assistant", content: assistantText });
            if (autoTtsEnabled) speak(msg.bubble.textContent);
            finished = true;
            announce("小暖已回复");
          } else if (evt.event === "error") {
            throw new Error(evt.data.detail || "流式请求出错");
          }
        }
        return pump();
      });
    }
    return pump();
  }

  init();
})();
