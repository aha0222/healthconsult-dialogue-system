/* ==========================================================================
   小暖 · 语音服务接线（独立于主后端的 voice_service，默认 127.0.0.1:8100）

   职责（组长接线，Day0 契约见 voice_service/README.md）：
   - 探活 /health，读取 asr / tts 能力位
   - ASR：麦克风 PCM 采集 → 16kHz 单声道 WAV → POST /asr → 文本
   - TTS：POST /tts → audio/wav 字节流 → 逐句队列播放（首句快出声，
     播放中预取下一句衔接，可打断）
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
    baseUrl: DEFAULT_BASE,
  };

  // 录音运行时
  var recording = null;

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
        state.probed = true;
        return { asr: state.asrReady, tts: state.ttsReady };
      })
      .catch(function () {
        state.probed = true;
        return { asr: false, tts: false };
      });
  }

  function canListen() { return state.probed && state.asrReady; }
  function canSpeak() { return state.probed && state.ttsReady; }

  /* 冷启动时页面常常先于语音服务就绪打开（start.ps1 一拉起来就开浏览器，
     而 Kokoro 加载+预热还要十几秒）：一次探活失败后若不再重试，整场都会
     静默退回浏览器语音，音色和音质都不一样。这里允许按需重探，
     PROBE_RETRY_MS 节流避免连续请求打爆服务。 */
  var PROBE_RETRY_MS = 1500;
  var lastProbeAt = 0;

  function ensureReady() {
    var fresh = Date.now() - lastProbeAt < PROBE_RETRY_MS;
    if (state.probed && (state.asrReady || state.ttsReady)) {
      return Promise.resolve({ asr: state.asrReady, tts: state.ttsReady });
    }
    if (fresh) {
      return Promise.resolve({ asr: state.asrReady, tts: state.ttsReady });
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

  /* ── TTS：/tts → Audio 播放（逐句队列，可打断）────────── */

  var currentPlayer = null;
  var speakToken = 0;         // 打断代次：speak/stopSpeaking 递增，旧队列回调随之失效
  var SENTENCE_GAP_MS = 100;  // 句间停顿：服务端已裁拖尾静音，由播放端补一拍

  function stopSpeaking() {
    speakToken++;
    if (currentPlayer) {
      try { currentPlayer.pause(); } catch (e) { /* ignore */ }
      currentPlayer = null;
    }
  }

  // 请求 /tts 并封装成 Audio 对象（服务端 LRU 缓存使重复文本零合成）
  function requestTts(text) {
    var clean = (text || "").replace(/\s*\[(?:RISK|SCENE):[^\]]+\]\s*/g, " ").trim();
    if (!clean) return Promise.resolve(null);
    return fetchWithTimeout(state.baseUrl + "/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: clean, format: "wav", speed: 0.9 }),
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

  // 与服务端 split_sentences 对齐：按 。！？；… 与换行切句
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

  function speak(text) {
    if (!state.ttsReady) {
      // 未就绪（含"页面早于服务打开"的首播）：按需重探一次再决定
      return ensureReady().then(function (caps) {
        if (caps.tts) return speak(text);
        return false;
      });
    }
    stopSpeaking();
    var clean = (text || "").replace(/\s*\[(?:RISK|SCENE):[^\]]+\]\s*/g, " ").trim();
    if (!clean) return Promise.resolve(true);

    var parts = splitForPlayback(clean);
    if (!parts.length) return Promise.resolve(true);

    /* 逐句队列：第 i 句一起播就预取第 i+1 句。GPU 合成远快于播放时长，
       句间只剩固定小停顿。旧方案"首句 + 整段 rest"在文本稍长后，
       rest 整段合成慢于首句播放，句间空档随文本变长而拉大。 */
    var myToken = speakToken;
    var idx = 0;
    var nextPromise = requestTts(parts[idx++]);
    var started = false;  // 是否至少成功起播过一句（决定是否回退浏览器 TTS）

    return new Promise(function (resolve) {
      var settled = false;
      function done(ok) {
        if (!settled) { settled = true; resolve(ok); }
      }
      function playNext() {
        if (myToken !== speakToken) { done(true); return; }  // 被新一轮接管，不回退浏览器
        var cur = nextPromise;
        if (!cur) { done(started); return; }
        nextPromise = idx < parts.length ? requestTts(parts[idx++]) : null;
        cur.then(function (audio) {
          if (myToken !== speakToken) { done(true); return; }
          if (!audio) { playNext(); return; }
          currentPlayer = audio;
          var advance = function () {
            audio.onended = audio.onerror = null;
            URL.revokeObjectURL(audio.src);
            currentPlayer = null;
            if (myToken !== speakToken) { done(true); return; }
            if (nextPromise) {
              setTimeout(function () { if (myToken === speakToken) playNext(); }, SENTENCE_GAP_MS);
            } else {
              done(true);
            }
          };
          audio.onended = advance;
          audio.onerror = advance;
          audio.play().then(function () { started = true; }).catch(function () {
            advance();  // 起播失败跳过该句；若首句即失败，started=false → 回退浏览器 TTS
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
  };
})(window);
