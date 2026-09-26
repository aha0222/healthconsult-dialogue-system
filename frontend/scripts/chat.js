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
  var USER_KEY = "xiaonuan_user_id";

  var PERSONALITY_DESCS = {
    "温婉邻居型": "温婉端庄，先安抚再建议",
    "贴心闺女型": "亲切软糯，情感模式",
    "素朴家常型": "朴素接地气，大白话",
    "从容守护型": "淡定从容，一二三讲清楚",
  };

  var RISK_LABEL_MAP = {
    R3: "极高风险", R2b: "高风险", R2a: "中高风险",
    R1: "中风险", R0: "低风险",
  };

  var SCENE_LABEL_MAP = {
    S1: "症状咨询", S2: "用药管理", S3: "慢病管理", S4: "就医引导",
    M1: "情绪陪伴", M2: "心理危机",
    L1: "饮食营养", L2: "运动康复", L3: "作息睡眠", L4: "社交活动",
    E1: "急症识别",
    N1: "人身安全", N2: "环境安全", N3: "诈骗财产",
    X1: "闲聊", X2: "系统功能",
  };

  var COLLECTED_LABELS = {
    name: "称呼",
    age: "年龄",
    living: "居住",
    conditions: "基础病",
    medications: "用药",
    allergies: "过敏史",
    healthConcerns: "健康困扰",
    mobility: "行动/自理",
    emergencyContact: "紧急联系人",
    emergencyPhone: "紧急联系电话",
  };

  var PROFILE_KEYS = ["conditions", "medications", "family", "preferences", "notes"];

  var PROFILE_LABELS = {
    conditions: "慢病/健康状况",
    medications: "用药",
    family: "家属",
    preferences: "偏好",
    notes: "其他",
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
    $("btnMemory").addEventListener("click", openMemoryDialog);
    $("btnRecoverUser").addEventListener("click", function () {
      var d = $("profileDialog");
      if (d) d.close();
      openSwitchUser();
    });
    var btnRecoverOnboard = $("btnRecoverOnboard");
    if (btnRecoverOnboard) {
      btnRecoverOnboard.addEventListener("click", openSwitchUser);
    }
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

  /* ── 用户档案：写入后端记忆 + 可视化展示 ───────────────── */
  function getUserId() {
    return localStorage.getItem(USER_KEY) || "";
  }

  function setUserId(id) {
    if (id) localStorage.setItem(USER_KEY, id);
    else localStorage.removeItem(USER_KEY);
  }

  function maskPhone(value) {
    var digits = String(value || "").replace(/\D/g, "");
    if (!digits) return String(value || "");
    if (digits.length <= 7) {
      return digits.length > 1 ? digits[0] + "****" + digits[digits.length - 1] : "****";
    }
    return digits.slice(0, 3) + "****" + digits.slice(-4);
  }

  function localCollected(fields) {
    var out = {};
    Object.keys(COLLECTED_LABELS).forEach(function (key) {
      var value = String((fields && fields[key]) || "").trim();
      out[key] = key === "emergencyPhone" ? maskPhone(value) : value;
    });
    return out;
  }

  function splitList(value) {
    if (!value) return [];
    return String(value)
      .split(/[,，、;；/\\|\n]+/)
      .map(function (s) { return s.trim(); })
      .filter(Boolean);
  }

  function collectedToProfile(collected) {
    var profile = {
      conditions: splitList(collected && collected.conditions),
      medications: splitList(collected && collected.medications),
      family: [],
      preferences: [],
      notes: [],
    };
    if (collected && collected.allergies) profile.notes.push("过敏史：" + collected.allergies);
    if (collected && collected.healthConcerns) profile.notes.push("健康困扰：" + collected.healthConcerns);
    if (collected && collected.mobility) profile.notes.push("行动/自理：" + collected.mobility);
    return profile;
  }

  function diffList(full, subtract) {
    var sub = {};
    (subtract || []).forEach(function (v) {
      sub[String(v).trim().toLowerCase()] = true;
    });
    return (full || []).filter(function (v) {
      return !sub[String(v).trim().toLowerCase()];
    });
  }

  function saveProfile(fields) {
    var base = backendBase();
    if (!base) return Promise.reject(new Error("未配置后端地址"));
    var body = {
      user_id: getUserId() || null,
      name: (fields && fields.name) || "",
      age: (fields && fields.age) || "",
      living: (fields && fields.living) || "",
      conditions: (fields && fields.conditions) || "",
      medications: (fields && fields.medications) || "",
      allergies: (fields && fields.allergies) || "",
      healthConcerns: (fields && fields.healthConcerns) || "",
      mobility: (fields && fields.mobility) || "",
      emergencyContact: (fields && fields.emergencyContact) || "",
      emergencyPhone: (fields && fields.emergencyPhone) || "",
    };
    return fetch(base + "/api/profile", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify(body),
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().catch(function () { return {}; }).then(function (err) {
            throw new Error(err.detail || ("HTTP " + resp.status));
          });
        }
        return resp.json();
      })
      .then(function (detail) {
        if (detail && detail.id) setUserId(detail.id);
        return detail;
      });
  }

  function hasMemoryData(collected, profile, summary) {
    var hasCollected = false;
    if (collected) {
      hasCollected = Object.keys(COLLECTED_LABELS).some(function (key) {
        return !!collected[key];
      });
    }
    var hasProfile = PROFILE_KEYS.some(function (key) {
      var arr = profile && profile[key];
      return Array.isArray(arr) && arr.length > 0;
    });
    return hasCollected || hasProfile || !!summary;
  }

  function buildMemoryHeadline(collected, profile) {
    var parts = [];
    if (collected && collected.name) parts.push(collected.name);
    if (collected && collected.conditions) {
      parts.push(String(collected.conditions).split(/[,，、;；]/)[0]);
    }
    if (collected && collected.allergies) {
      parts.push("过敏：" + String(collected.allergies).split(/[,，、;；]/)[0]);
    }
    return parts.join(" · ") || "已记住您的情况";
  }

  function memoryRow(label, value) {
    var row = document.createElement("div");
    row.className = "memory-row";
    var lab = document.createElement("div");
    lab.className = "memory-row__label";
    lab.textContent = label;
    var val = document.createElement("div");
    val.className = "memory-row__value";
    val.textContent = value;
    row.appendChild(lab);
    row.appendChild(val);
    return row;
  }

  function memorySection(title) {
    var sec = document.createElement("div");
    sec.className = "memory-section";
    var h = document.createElement("h3");
    h.textContent = title;
    sec.appendChild(h);
    return sec;
  }

  function renderMemory(data) {
    var collected = (data && data.collected) || {};
    var profile = (data && data.profile) || {};
    var summary = (data && data.conversation_summary) || (data && data.summary) || "";

    var panel = $("memoryPanel");
    var headlineEl = $("memoryPanelHeadline");
    if (panel) {
      panel.hidden = !hasMemoryData(collected, profile, summary);
      if (headlineEl) headlineEl.textContent = buildMemoryHeadline(collected, profile);
    }

    var body = $("memoryBody");
    if (body) {
      body.textContent = "";

      var info = memorySection("您告诉小暖的");
      ["name", "age", "living", "conditions", "medications", "allergies", "healthConcerns", "mobility", "emergencyContact", "emergencyPhone"].forEach(function (key) {
        var value = collected[key];
        if (value) info.appendChild(memoryRow(COLLECTED_LABELS[key], value));
      });
      if (info.children.length > 1) body.appendChild(info);

      var selfProfile = collectedToProfile(collected);
      var learned = memorySection("小暖从对话中记住的");
      PROFILE_KEYS.forEach(function (key) {
        var items = diffList(profile[key], selfProfile[key]);
        if (items.length) {
          learned.appendChild(memoryRow(PROFILE_LABELS[key], items.join("、")));
        }
      });
      if (learned.children.length > 1) body.appendChild(learned);

      if (summary) {
        var sum = memorySection("近期摘要");
        var p = document.createElement("p");
        p.className = "memory-summary";
        p.textContent = summary;
        sum.appendChild(p);
        body.appendChild(sum);
      }

      if (body.children.length === 0) {
        var empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "还没有已记住的信息，填写个人信息后就会显示在这里。";
        body.appendChild(empty);
      }
    }
  }

  function openMemoryDialog() {
    var dialog = $("memoryDialog");
    if (dialog) dialog.showModal();
  }

  function openSwitchUser() {
    var dialog = $("switchUserDialog");
    if (dialog) dialog.showModal();

    var listEl = $("switchUserList");
    if (!listEl) return;
    listEl.innerHTML = "";
    var loading = document.createElement("p");
    loading.className = "empty";
    loading.textContent = "加载中…";
    listEl.appendChild(loading);

    var base = backendBase();
    if (!base) {
      renderSwitchUserEmpty("请先在设置里配置后端地址");
      return;
    }
    fetch(base + "/api/users", { headers: authHeaders() })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(renderSwitchUserList)
      .catch(function (e) {
        renderSwitchUserEmpty("加载失败：" + e.message);
      });
  }

  function renderSwitchUserEmpty(text) {
    var listEl = $("switchUserList");
    if (!listEl) return;
    listEl.innerHTML = "";
    var p = document.createElement("p");
    p.className = "empty";
    p.textContent = text;
    listEl.appendChild(p);
  }

  function renderSwitchUserList(users) {
    var listEl = $("switchUserList");
    if (!listEl) return;
    if (!users || !users.length) {
      renderSwitchUserEmpty("还没有已保存的档案");
      return;
    }
    listEl.innerHTML = "";
    users.forEach(function (u) {
      var row = document.createElement("div");
      row.className = "session-item";

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "session-item__open";
      btn.addEventListener("click", function () { bindUser(u.id); });

      var info = document.createElement("div");
      info.className = "session-item__info";
      var title = document.createElement("div");
      title.className = "session-item__title";
      title.textContent = u.display_name || "未命名档案";
      var sub = document.createElement("div");
      sub.className = "session-item__sub tnum";
      var phone = (u.collected && u.collected.emergencyPhone) || "";
      sub.textContent = phone ? "电话 " + phone : "已采集资料";
      info.appendChild(title);
      info.appendChild(sub);
      btn.appendChild(info);
      row.appendChild(btn);
      listEl.appendChild(row);
    });
  }

  function bindUser(userId) {
    var base = backendBase();
    if (!base) return;
    fetch(base + "/api/users/" + encodeURIComponent(userId), { headers: authHeaders() })
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (detail) {
        setUserId(detail.id);
        if (window.Xiaonuan.setLocalProfile) {
          window.Xiaonuan.setLocalProfile(detail.collected || {});
        }
        renderMemory(detail);

        sessionId = null;
        messages = [];
        messagesEl.textContent = "";
        welcomeEl.hidden = false;
        setRiskAmbient(null);

        var dialog = $("switchUserDialog");
        if (dialog) dialog.close();
        var profileDialog = $("profileDialog");
        if (profileDialog) profileDialog.close();
        showSystemMsg("已恢复档案：" + (detail.display_name || "未命名档案"));
      })
      .catch(function (e) {
        showSystemMsg("恢复失败：" + e.message, true);
      });
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
    var clean = (text || "").replace(/\s*\[(?:RISK|SCENE):[^\]]+\]\s*/g, " ").trim();
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

  function addMessage(role, content, risk, scenes, silent) {
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

    msg.appendChild(avatarNode(role));
    msg.appendChild(body);
    messagesEl.appendChild(msg);

    var wrapper = { el: msg, bubble: bubble, meta: meta };
    if (risk || (scenes && scenes.length)) updateMessageRisk(wrapper, risk, scenes);

    if (role === "assistant" && autoTtsEnabled && content && !silent) speak(bubble.textContent);
    scrollToBottom();
    return wrapper;
  }

  function setBubbleText(msg, text) {
    msg.bubble.innerHTML = renderMarkdown(text);
    scrollToBottom();
  }

  function updateMessageRisk(msg, risk, scenes) {
    var stale = msg.meta.querySelectorAll("[data-tag]");
    Array.prototype.forEach.call(stale, function (node) { node.remove(); });

    var frag = document.createDocumentFragment();
    var label = RISK_LABEL_MAP[risk];
    if (label) {
      var span = document.createElement("span");
      span.className = "risk--" + risk;
      span.setAttribute("data-tag", "risk");
      span.textContent = label;
      frag.appendChild(span);
    }
    (scenes || []).forEach(function (scene) {
      var text = SCENE_LABEL_MAP[scene];
      if (!text) return;
      var chip = document.createElement("span");
      chip.className = "scene-chip";
      chip.setAttribute("data-tag", "scene");
      chip.textContent = text;
      frag.appendChild(chip);
    });
    if (frag.childNodes.length) msg.meta.insertBefore(frag, msg.meta.firstChild);
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
    if (detail.user_id) setUserId(detail.user_id);
    messages = [];
    messagesEl.textContent = "";
    welcomeEl.hidden = true;

    (detail.messages || []).forEach(function (m) {
      addMessage(
        m.role,
        m.content,
        m.role === "assistant" ? m.risk : null,
        m.role === "assistant" ? m.scenes : null,
        true
      );
      messages.push({ role: m.role, content: m.content });
    });

    if (detail.personality) {
      currentPersonality = detail.personality;
      personalitySelect.value = detail.personality;
      updateStatus();
    }
    renderMemory(detail);
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

    addMessage("user", text, null, null);
    messages.push({ role: "user", content: text });

    inputEl.value = "";
    inputEl.dataset.finalText = "";
    autoResize(inputEl);
    setTyping(true);
    isGenerating = true;

    var payload = { message: text, personality: currentPersonality };
    if (sessionId) payload.session_id = sessionId;
    else payload.history = messages.slice(0, -1);
    var userId = getUserId();
    if (userId) payload.user_id = userId;

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
    var msg = addMessage("assistant", "", null, null);
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
            updateMessageRisk(msg, evt.data.risk, evt.data.scenes);
            if (evt.data.fallback_used) msg.el.classList.add("msg--fallback");
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

  /* 供欢迎流程 / 个人信息弹窗复用的对外接口 */
  window.Xiaonuan = {
    getUserId: getUserId,
    setUserId: setUserId,
    saveProfile: saveProfile,
    renderMemory: renderMemory,
    openMemory: openMemoryDialog,
    maskPhone: maskPhone,
    localCollected: localCollected,
    notify: function (text) { showSystemMsg(text); },
  };

  init();
})();
