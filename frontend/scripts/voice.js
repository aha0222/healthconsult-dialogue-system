/* ==========================================================================
   小暖 · 语音服务接线（独立于主后端的 voice_service，默认 127.0.0.1:8100）

   职责（组长接线，Day0 契约见 voice_service/README.md）：
   - 探活 /health，读取 asr / tts 能力位
   - ASR：麦克风 PCM 采集 → 16kHz 单声道 WAV → POST /asr → 文本
   - TTS：POST /tts/stream 边收边播（云端 PCM 流，首句约 0.4 秒出声）；
     服务端不支持流式时回退 POST /tts 整段合成 —— 先播首块（≤45 字），
     播的同时预取下一块。分块是刻意的：受接口单次时长限制，不切会被截断。
     整段合成而非逐句：逐句时每句没有上下文，音高会一句一个样。
     本地引擎同样走这条路径（CosyVoice2 自己有流式，其它引擎走分块）。
   - 语音服务未启动 / 未实现（501）时：canListen/canSpeak 为 false，
     调用方（chat.js）自动回退浏览器 Web Speech 或打字输入。

   本文件不碰对话逻辑、不持有任何密钥；后端 VOICE_ENABLED=0 时不会被调用。
   ========================================================================== */

(function (global) {
  "use strict";

  var DEFAULT_BASE = "http://127.0.0.1:8100";
  var PROBE_TIMEOUT_MS = 2000;
  var RECORD_SAMPLE_RATE = 16000;

  var state = {
    probed: false,
    asrReady: false,
    ttsReady: false,
    cloudTtsReady: false,
    baseUrl: DEFAULT_BASE,
  };

  // 录音运行时
  var recording = null;

  function engineMode() {
    try {
      var saved = JSON.parse(global.localStorage.getItem("xiaonuan_settings") || "{}");
      return saved.voiceEngine === "cloud" ? "cloud" : "local";
    } catch (e) { return "local"; }
  }

  function baseUrl() {
    try {
      var saved = JSON.parse(global.localStorage.getItem("xiaonuan_settings") || "{}");
      if (saved.voiceUrl && /^https?:\/\//i.test(saved.voiceUrl)) return saved.voiceUrl;
    } catch (e) { /* ignore */ }
    return DEFAULT_BASE;
  }

  function fetchWithTimeout(url, options, timeoutMs) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, timeoutMs || PROBE_TIMEOUT_MS);
    options = options || {};
    options.signal = controller.signal;
    return fetch(url, options).finally(function () { clearTimeout(timer); });
  }

  /* ── 探活 ─────────────────────────────────────────────── */

  function probe() {
    state.probed = false;
    state.asrReady = false;
    state.ttsReady = false;
    state.cloudTtsReady = false;
    lastProbeAt = Date.now();
    var base = baseUrl();
    return fetchWithTimeout(base + "/health")
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (health) {
        state.baseUrl = base;
        state.asrReady = !!health.asr;
        state.ttsReady = !!health.tts;
        state.cloudTtsReady = !!health.cloud_tts;
        state.probed = true;
        return { asr: state.asrReady, tts: state.ttsReady, cloudTts: state.cloudTtsReady };
      })
      .catch(function () {
        state.probed = true;
        return { asr: false, tts: false, cloudTts: false };
      });
  }

  function canListen() { return state.probed && state.asrReady; }
  function canSpeak() {
    if (!state.probed) return false;
    return engineMode() === "cloud"
      ? (state.ttsReady || state.cloudTtsReady)
      : state.ttsReady;
  }

  /* 冷启动时页面常常先于语音服务就绪打开（start.ps1 一拉起来就开浏览器，
     而 Kokoro 加载+预热还要十几秒）：一次探活失败后若不再重试，整场都会
     静默退回浏览器语音，音色和音质都不一样。这里允许按需重探，
     PROBE_RETRY_MS 节流避免连续请求打爆服务。 */
  var PROBE_RETRY_MS = 1500;
  var lastProbeAt = 0;

  function ensureReady() {
    var fresh = Date.now() - lastProbeAt < PROBE_RETRY_MS;
    if (state.probed && (state.asrReady || canSpeak())) {
      return Promise.resolve({ asr: state.asrReady, tts: state.ttsReady, cloudTts: state.cloudTtsReady });
    }
    if (fresh) {
      return Promise.resolve({ asr: state.asrReady, tts: state.ttsReady, cloudTts: state.cloudTtsReady });
    }
    return probe();
  }

  /* ── WAV 编码（Float32 → 16kHz 单声道 16bit PCM WAV）──── */

  function downsample(samples, fromRate, toRate) {
    if (toRate >= fromRate) return samples;
    var ratio = fromRate / toRate;
    var length = Math.floor(samples.length / ratio);
    var result = new Float32Array(length);
    for (var i = 0; i < length; i++) {
      var start = Math.floor(i * ratio);
      var end = Math.min(Math.floor((i + 1) * ratio), samples.length);
      var sum = 0;
      for (var j = start; j < end; j++) sum += samples[j];
      result[i] = end > start ? sum / (end - start) : 0;
    }
    return result;
  }

  function encodeWav(samples, sampleRate) {
    var buffer = new ArrayBuffer(44 + samples.length * 2);
    var view = new DataView(buffer);
    function writeString(offset, str) {
      for (var i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    }
    writeString(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, "WAVE");
    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);          // PCM
    view.setUint16(22, 1, true);          // 单声道
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, "data");
    view.setUint32(40, samples.length * 2, true);
    var offset = 44;
    for (var i = 0; i < samples.length; i++, offset += 2) {
      var s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([view], { type: "audio/wav" });
  }

  /* ── ASR：录音 → /asr ─────────────────────────────────── */

  function startListening(handlers) {
    handlers = handlers || {};
    if (!state.asrReady) {
      // 未就绪：按需重探一次再决定（同 speak，覆盖冷启动窗口）
      ensureReady().then(function (caps) {
        if (caps.asr) startListening(handlers);
        else if (handlers.onError) handlers.onError("unavailable");
      });
      return true;
    }
    if (recording) stopListening();

    var ctx = new (global.AudioContext || global.webkitAudioContext)();
    var stream = null;
    var processor = null;
    var source = null;
    var chunks = [];

    var cleanup = function () {
      try { if (processor) processor.disconnect(); } catch (e) { /* ignore */ }
      try { if (source) source.disconnect(); } catch (e) { /* ignore */ }
      if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
      try { ctx.close(); } catch (e) { /* ignore */ }
      recording = null;
    };

    var p = navigator.mediaDevices
      ? navigator.mediaDevices.getUserMedia({ audio: true })
      : Promise.reject(new Error("浏览器不支持麦克风采集"));

    p.then(function (mediaStream) {
      stream = mediaStream;
      source = ctx.createMediaStreamSource(stream);
      processor = ctx.createScriptProcessor(4096, 1, 1);
      processor.onaudioprocess = function (event) {
        chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      source.connect(processor);
      processor.connect(ctx.destination);
      if (handlers.onStart) handlers.onStart();
    }).catch(function () {
      cleanup();
      if (handlers.onError) handlers.onError("mic_denied");
    });

    recording = {
      stop: function () {
        var finish = function () {
          cleanup();
          if (!chunks.length) {
            if (handlers.onError) handlers.onError("empty");
            return;
          }
          var total = 0;
          chunks.forEach(function (c) { total += c.length; });
          var merged = new Float32Array(total);
          var offset = 0;
          chunks.forEach(function (c) { merged.set(c, offset); offset += c.length; });
          var wav = encodeWav(downsample(merged, ctx.sampleRate, RECORD_SAMPLE_RATE), RECORD_SAMPLE_RATE);
          fetchWithTimeout(state.baseUrl + "/asr?language=zh", {
            method: "POST",
            headers: { "Content-Type": "audio/wav" },
            body: wav,
          }, 15000)
            .then(function (resp) {
              if (resp.status === 501) throw new Error("unimplemented");
              if (!resp.ok) throw new Error("HTTP " + resp.status);
              return resp.json();
            })
            .then(function (data) {
              if (handlers.onFinal) handlers.onFinal((data && data.text) || "");
            })
            .catch(function (err) {
              if (handlers.onError) {
                handlers.onError(err.message === "unimplemented" ? "unimplemented" : "asr_failed");
              }
            });
        };
        // 稍等一拍，让最后一帧音频进入 processor
        setTimeout(finish, 120);
      },
      cancel: function () { cleanup(); },
    };
    return true;
  }

  function stopListening() {
    if (!recording) return;
    var current = recording;
    recording = null;
    current.stop();
  }

  function cancelListening() {
    if (recording) {
      recording.cancel();
      recording = null;
    }
  }

  /* ── TTS：云端走流式（边收边播），本地走整段/分块 ─────────── */

  var currentPlayer = null;
  var speakToken = 0;         // 打断代次：speak/stopSpeaking 递增，旧回调随之失效

  // 流式播放用 Web Audio：一串 AudioBuffer 按时间戳排好，首块到了就能出声
  var audioCtx = null;
  var activeSources = [];
  var streamUntil = 0;        // 已排到的时间点（AudioContext 时钟）
  var streamBroken = false;   // 流式被服务端拒绝过一次就不再试（避免每条回复都先失败一次）
  var streamBrokenFor = null; // 记录是在哪种语音模式下被拒的

  function ensureAudioContext() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audioCtx) {
      try { audioCtx = new Ctx(); } catch (e) { return null; }
    }
    if (audioCtx.state === "suspended") {
      try { audioCtx.resume(); } catch (e) { /* ignore */ }
    }
    return audioCtx;
  }

  function stopStream() {
    for (var i = 0; i < activeSources.length; i++) {
      try { activeSources[i].stop(); } catch (e) { /* ignore */ }
    }
    activeSources = [];
    streamUntil = 0;
  }

  function stopSpeaking() {
    speakToken++;
    stopStream();
    if (currentPlayer) {
      try { currentPlayer.pause(); } catch (e) { /* ignore */ }
      currentPlayer = null;
    }
  }

  // 是否正在播：供前端判断"老人在小暖没说完时插话"（打断信号）
  function isSpeaking() {
    if (currentPlayer) return true;
    return !!audioCtx && audioCtx.currentTime < streamUntil;
  }

  // 请求 /tts 并封装成 Audio 对象（服务端 LRU 缓存使重复文本零合成）
  function requestTts(text, risk) {
    var clean = (text || "").replace(/\s*\[(?:RISK|SCENE):[^\]]+\]\s*/g, " ").trim();
    if (!clean) return Promise.resolve(null);
    return fetchWithTimeout(state.baseUrl + "/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: clean, format: "wav", speed: 0.9, engine: engineMode(), risk: risk || null }),
    }, 120000)
      .then(function (resp) {
        if (resp.status === 501) return null;
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.blob();
      })
      .then(function (blob) {
        if (!blob) return null;
        var url = URL.createObjectURL(blob);
        var audio = new Audio(url);
        audio.onended = audio.onerror = function () {
          URL.revokeObjectURL(url);
        };
        return audio;
      });
  }

  // 按句切分，再把句子拼成"块"：首块小（快出声）、后续块尽量大（少接缝）。
  // 云端与非云端的合成/播放速度差很多，块大小要分开算：
  //   云端 合成约 0.05 秒/字、播放约 0.23 秒/字 → 后续块可以是首块的 3.5 倍
  //   本地 CosyVoice2 合成约 0.17 秒/字、播放约 0.23 秒/字（只快 1.4 倍）
  var CLOUD_FIRST_CHARS = 45;
  var CLOUD_REST_CHARS = 90;
  var LOCAL_FIRST_CHARS = 30;   // 本地合成慢：首块再小一点，约 5 秒能出声
  var LOCAL_REST_CHARS = 60;
  var REST_MIN_CHARS = 20;
  var CHUNK_GAP_MS = 80;        // 块间留一点停顿（服务端已裁掉首尾静音）

  function chunkBudget() {
    return engineMode() === "cloud"
      ? { first: CLOUD_FIRST_CHARS, rest: CLOUD_REST_CHARS, ratio: 3.5 }
      : { first: LOCAL_FIRST_CHARS, rest: LOCAL_REST_CHARS, ratio: 1.4 };
  }

  function splitForPlayback(text) {
    var parts = [];
    var re = /[^。！？；…\n]+[。！？；…\n]*/g;
    var m;
    while ((m = re.exec(text)) !== null) {
      var s = m[0].trim();
      if (s) parts.push(s);
    }
    if (!parts.length && text.trim()) parts.push(text.trim());
    return parts;
  }

  function chunkForPlayback(text) {
    /* 后续块的大小跟着首块走：第 i+1 块必须在第 i 块播完之前合成好，
       所以按"播放/合成"的倍数（云端约 4.7 倍、本地约 1.4 倍）留一点余量。
       首块若很短（比如只有一句"朱阿姨，您说的是血压吧？"），后面就跟着切小一点，
       否则会出现"首句播完了、第二块还在合成"的空档。 */
    var budget = chunkBudget();
    var sentences = splitForPlayback(text);
    var chunks = [];
    var cur = "";
    var restLimit = budget.rest;
    for (var i = 0; i < sentences.length; i++) {
      var limit = chunks.length === 0 ? budget.first : restLimit;
      if (cur && cur.length + sentences[i].length > limit) {
        chunks.push(cur);
        cur = "";
        restLimit = Math.max(
          REST_MIN_CHARS,
          Math.min(budget.rest, Math.floor(chunks[0].length * budget.ratio))
        );
      }
      cur += sentences[i];
    }
    if (cur) chunks.push(cur);
    return chunks;
  }

  /* 流式播放：/tts/stream 返回 PCM16 块，按 AudioContext 时钟顺序排好。
     实测首块 0.7 秒到达（非流式同内容要 3.2 秒），且是一个会话、语调统一。 */
  function streamSpeak(text, risk, myToken) {
    // 本地引擎固定 501，记住"这个模式下别试了"；换成云端要重新给一次机会
    if (streamBroken && streamBrokenFor === engineMode()) return Promise.resolve(false);
    streamBroken = false;
    var ctx = ensureAudioContext();
    if (!ctx || !window.fetch) return Promise.resolve(false);
    return fetchWithTimeout(state.baseUrl + "/tts/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, format: "pcm", speed: 0.9, engine: engineMode(), risk: risk || null }),
    }, 30000)
      .then(function (resp) {
        // 服务端明确拒绝（501/额度/引擎不支持）→ 本次会话不再尝试流式；
        // 网络抖动不算，下次还会试。
        if (!resp.ok || !resp.body) { streamBroken = true; streamBrokenFor = engineMode(); return false; }
        var sampleRate = parseInt(resp.headers.get("X-Sample-Rate") || "24000", 10);
        var reader = resp.body.getReader();
        var carry = null;   // Int16 是 2 字节对齐，跨块时要留半个采样
        var nextTime = ctx.currentTime + 0.08;
        var started = false;

        function schedule(bytes) {
          var buf = bytes;
          if (carry) {
            buf = new Uint8Array(carry.length + bytes.length);
            buf.set(carry, 0);
            buf.set(bytes, carry.length);
            carry = null;
          }
          if (buf.length % 2) {
            carry = buf.slice(buf.length - 1);
            buf = buf.slice(0, buf.length - 1);
          }
          if (!buf.length) return;
          var samples = new Int16Array(buf.buffer, buf.byteOffset, buf.length / 2);
          var audioBuffer = ctx.createBuffer(1, samples.length, sampleRate);
          var data = audioBuffer.getChannelData(0);
          for (var i = 0; i < samples.length; i++) data[i] = samples[i] / 32768;
          var src = ctx.createBufferSource();
          src.buffer = audioBuffer;
          src.connect(ctx.destination);
          var when = Math.max(nextTime, ctx.currentTime + 0.02);
          src.start(when);
          nextTime = when + audioBuffer.duration;
          streamUntil = nextTime;
          activeSources.push(src);
          started = true;
        }

        function pump() {
          return reader.read().then(function (r) {
            if (myToken !== speakToken) {
              try { reader.cancel(); } catch (e) { /* ignore */ }
              return started;
            }
            if (r.done) return started;
            schedule(r.value);
            return pump();
          });
        }
        return pump().catch(function () { return started; });
      })
      .catch(function () { return false; });
  }

  function speak(text, risk) {
    if (!canSpeak()) {
      // 未就绪（含"页面早于服务打开"的首播）：按需重探一次再决定
      return ensureReady().then(function () {
        if (canSpeak()) return speak(text, risk);
        return false;
      });
    }
    stopSpeaking();
    var clean = (text || "").replace(/\s*\[(?:RISK|SCENE):[^\]]+\]\s*/g, " ").trim();
    if (!clean) return Promise.resolve(true);

    /* 先试流式：边合成边播，首块约 0.7 秒（非流式 66 字要 3.2 秒、200 字要 10 秒以上），
       而且整段是一个会话，语调统一。流式不可用（501/网络/浏览器不支持 Web Audio）
       再退回下面的分块方案。 */
    var myToken = speakToken;
    return streamSpeak(clean, risk, myToken).then(function (ok) {
      if (ok) return true;
      if (myToken !== speakToken) return true;   // 已被新一轮接管
      return speakChunked(clean, risk);
    });
  }

  /* 分块 + 预取（流式不可用时的兜底）。为什么不整段一次：云端非流式要等全部合成完，
     200 字要等 10 秒以上。为什么不逐句：每块独立合成，块越小语调跳变越明显
     （实测逐句时相邻三句 233→189→175 Hz）。所以首块 45 字（约 2 秒出声），
     之后每块最多 90 字，第 i 块在播时就去取第 i+1 块——生成远快于播放，不会有空档。 */
  function speakChunked(clean, risk) {
    var chunks = chunkForPlayback(clean);
    var myToken = speakToken;
    var idx = 0;
    var nextPromise = requestTts(chunks[idx++], risk);
    var started = false;   // 是否至少成功起播过一块（决定是否回退浏览器 TTS）

    return new Promise(function (resolve) {
      var settled = false;
      function done(ok) {
        if (!settled) { settled = true; resolve(ok); }
      }
      function playNext() {
        if (myToken !== speakToken) { done(true); return; }  // 被新一轮接管，不回退浏览器
        var cur = nextPromise;
        if (!cur) { done(started); return; }
        nextPromise = idx < chunks.length ? requestTts(chunks[idx++], risk) : null;
        cur.then(function (audio) {
          if (myToken !== speakToken) { done(true); return; }
          if (!audio) { playNext(); return; }                // 501/空块 → 跳过
          currentPlayer = audio;
          var advance = function () {
            audio.onended = audio.onerror = null;
            try { URL.revokeObjectURL(audio.src); } catch (e) { /* ignore */ }
            if (currentPlayer === audio) currentPlayer = null;
            if (myToken !== speakToken) { done(true); return; }
            if (nextPromise) {
              setTimeout(function () { if (myToken === speakToken) playNext(); }, CHUNK_GAP_MS);
            } else {
              done(started);
            }
          };
          audio.onended = advance;
          audio.onerror = advance;
          audio.play().then(function () { started = true; }).catch(function () {
            advance();  // 起播被拦截就跳过这块；一块都没播成才回退浏览器
          });
        }).catch(function () {
          if (myToken !== speakToken) { done(true); return; }
          playNext();
        });
      }
      playNext();
    });
  }

  global.XiaonuanVoice = {
    probe: probe,
    canListen: canListen,
    canSpeak: canSpeak,
    startListening: startListening,
    stopListening: stopListening,
    cancelListening: cancelListening,
    speak: speak,
    stopSpeaking: stopSpeaking,
    isSpeaking: isSpeaking,
  };
})(window);
