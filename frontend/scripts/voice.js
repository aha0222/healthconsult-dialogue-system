/* ==========================================================================
   小暖 · 语音服务接线（独立于主后端的 voice_service，默认 127.0.0.1:8100）

   职责（组长接线，Day0 契约见 voice_service/README.md）：
   - 探活 /health，读取 asr / tts 能力位
   - ASR：麦克风 PCM 采集 → 16kHz 单声道 WAV → POST /asr → 文本
   - TTS：POST /tts → audio/wav 字节流 → Audio 播放（可打断）
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
    if (!state.probed) {
      probe().then(function (caps) {
        if (caps.asr) startListening(handlers);
        else if (handlers.onError) handlers.onError("unavailable");
      });
      return true;
    }
    if (!state.asrReady) {
      if (handlers.onError) handlers.onError("unavailable");
      return false;
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

  /* ── TTS：/tts → Audio 播放 ───────────────────────────── */

  var currentPlayer = null;
  var nextPlayer = null;   // 预加载的下一段（首句优先策略）

  function stopSpeaking() {
    if (currentPlayer) {
      try { currentPlayer.pause(); } catch (e) { /* ignore */ }
      currentPlayer = null;
    }
    if (nextPlayer) {
      try { nextPlayer.pause(); } catch (e) { /* ignore */ }
      nextPlayer = null;
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

  function speak(text) {
    if (!state.probed) {
      return probe().then(function (caps) {
        if (caps.tts) return speak(text);
        return false;
      });
    }
    if (!state.ttsReady) return Promise.resolve(false);
    stopSpeaking();
    var clean = (text || "").replace(/\s*\[(?:RISK|SCENE):[^\]]+\]\s*/g, " ").trim();
    if (!clean) return Promise.resolve(true);

    /* 首句优先：第一句立即合成播放（~1s 出声），剩余段并行预加载衔接。
       拆句规则与服务端一致（按 。！？；），服务端 LRU 缓存使两段请求互不重复合成。 */
    var cut = -1;
    for (var i = 0; i < clean.length && i < 80; i++) {
      if ("。！？；".indexOf(clean[i]) >= 0) { cut = i + 1; break; }
    }
    var first = cut > 0 ? clean.slice(0, cut) : clean;
    var rest = cut > 0 ? clean.slice(cut) : "";

    /* rest 的合成（~6s）常慢于首句播放（~2.4s）：把 Promise 存下来，
       onended 时等待它就绪再接播，而不是放弃。 */
    var restPromise = rest ? requestTts(rest) : Promise.resolve(null);

    return requestTts(first).then(function (audio) {
      if (!audio) return false;
      currentPlayer = audio;
      audio.onended = function () {
        URL.revokeObjectURL(audio.src);
        restPromise.then(function (a2) {
          if (!a2) {
            currentPlayer = null;
            return;
          }
          currentPlayer = a2;
          a2.onended = function () { URL.revokeObjectURL(a2.src); };
          a2.play().catch(function () { /* ignore */ });
        }).catch(function () { currentPlayer = null; });
      };
      audio.play().catch(function () { /* ignore */ });
      return true;
    }).catch(function () {
      return false; // 调用方回退浏览器 TTS
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
