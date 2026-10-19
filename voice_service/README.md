# 小暖 · 语音服务 `voice_service`

> 独立于主后端的语音能力服务：**只做「音频 ↔ 文本」**，前端通过 HTTP / WebSocket 直连。
> `契约 v1` · `离线本地` · `Python 3.9+` · 拥有者：成员 A

---

## 🚀 操作指南（先看这里）

### 开始前 · 两个前提

1. **所有命令都要在「项目根目录」下执行**——就是那个同时包含 `backend/`、`frontend/`、`voice_service/` 三个文件夹的目录。
2. **本指南只负责把语音服务跑起来**；要接进聊天界面（在 frontend 里说话能识别），那是组长的活，见文末「与主系统的关系」。

### 第 1 步：进入项目根目录

```powershell
cd C:\Users\ASUS\Desktop\healthconsult-dialogue-system-main
```

> 上面是你这台电脑的路径；换机器就换成你自己的项目根目录。

### 第 2 步：安装依赖

```powershell
python -m pip install -r voice_service\requirements.txt
```

> 下载慢就换清华镜像：
> `python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r voice_service\requirements.txt`

### 第 3 步：下载模型

```powershell
python voice_service\download_models.py
```

> 自动走国内源（GitHub 代理 + 魔搭），约 300MB，无需翻墙。模型**不进代码库**，缺了再跑一次即可。
>
> **推荐启用 CosyVoice2（更自然、多音字更准）：**
> `python voice_service\download_cosyvoice2.py`（约 4GB，只下推理必需文件，走魔搭）。
>
> 要用 Kokoro 中文合成（可选备选），再跑：
> `python voice_service\download_kokoro.py`（约 360MB，模型 + 100 个中文音色，走魔搭）。

### 第 4 步：启动服务

```powershell
python -m voice_service
```

看到下面这行就是成功：

```text
Uvicorn running on http://127.0.0.1:8100
```

> **这个窗口要保持打开**，关掉就是停止服务。

### 怎么算装好了

1. 浏览器打开 `http://127.0.0.1:8100/health`，应看到 `"asr": true, "tts": true`；
2. 打开 `voice_service\demo.html`，依次点「探活 → 录音识别 → 合成播放 → 流式识别 → 打断」验证。

### 两个必须知道的“坑”（已修好）

- **声音是乱码 / 拉长、听不出字**：这是 `transformers` 版本太新导致的，不是模型坏。
  本项目已把 `transformers` 锁到 `4.51.3`（见 `requirements.txt`），不要再升到 `4.53+` 或 `5.x`。
- **第一次启动要等很久（5-10 分钟才起来）**：这是 numba 在 Windows 上探测缓存目录被 Defender
  拖慢。代码里已经自动修掉，正常约 **20 秒** 就能启动；若换机器后仍慢，看 `voice_service\__init__.py`
  顶部的说明即可。
- **合成一句话要几秒（不是卡死）**：CosyVoice2 质量高、模型大，本机一句约 3 秒；这是本地模型的
  正常速度。服务启动时会自动「预热」一次，避免第一句出现 5-8 秒的假延迟。想再快一点，可设
  `COSYVOICE_FLOW_STEPS=6`（默认 10，越小越快、音质略降，不建议低于 4），或切 Kokoro（见下）。

---

## 🧠 工作原理

整套系统由三部分组成，**前端是中间人**，主后端和语音服务之间不直接通信：

```
┌────────────┐       打字对话(8000)        ┌──────────────┐
│  前端       │ ─────────────────────────► │  主后端       │
│ chat.html  │ ◄───────────────────────── │  backend     │
└────────────┘       回复文字 + 风险等级    │  对话 + 安全  │
      │  ▲                                 └──────────────┘
      │  │ 语音(8100)                              ▲
      ▼  │                                        │ 调用大模型
┌────────────┐                                     │
│ 语音服务    │  ← 只做「声音 ↔ 文字」，不碰大模型/安全
│ voice_service
└────────────┘
```

**一次语音对话的完整链路：**

1. 你说话 → 前端录音 → 调语音服务 `POST /asr` → 得到文字；
2. 前端把文字发给主后端（8000）→ AI 生成回复文字；
3. 前端拿回复文字 → 调语音服务 `POST /tts` → 合成声音 → 播放。

**为什么能离线：** 识别和合成都用本地模型（不联网、不依赖任何 API Key），所以断网也能用。

**内部实现：**

| 能力 | 用什么 | 说明 |
|------|--------|------|
| 语音识别 ASR | sherpa-onnx · Paraformer 中文 | 本地 CPU 可跑，离线 |
| 语音合成 TTS | **CosyVoice2-0.5B** / Kokoro v1.1 / piper | 本地中文，离线；CosyVoice2 用 BERT 做多音字消歧，最自然 |
| 断句 / 打断 VAD | 能量检测（无模型） | 按音量判断是否在说话 |

---

## 📡 对外契约（冻结，改动需 A 与组长确认）

| 接口 | 方法 | 入参 | 出参 |
|------|------|------|------|
| `/health` | GET | — | `{"status","service","version","contract","asr","tts"}` |
| `/asr` | POST | body=原始音频字节(wav)；query `language=zh` | `{"text","is_final","confidence"}` |
| `/tts` | POST | json `{"text","voice","format","speed"}` | `audio/wav` 字节流 |
| `/voice/stream` | WS | — | 事件流（见下） |

### WS 事件名（冻结）

- 客户端 → 服务端：`audio_chunk`(二进制) / `commit` / `interrupt` / `ping`
- 服务端 → 客户端：`speech_start` / `partial{text}` / `final{text}` / `reply_audio`(二进制) / `interrupt` / `error{code,message}`

> `audio_chunk` 的二进制内容是 **16kHz 单声道 int16 PCM**（无 wav 头）。

### 当前状态

- `/health`：永远 200；模型就绪时 `asr`/`tts` 为 `true`。
- `/asr`：本地 Paraformer 中文识别；模型未下载时返回 501。
- `/tts`：本地中文合成（wav）；有 `llm.pt` 时自动用 CosyVoice2，否则用 Kokoro；模型都未下载时返回 501。
- `/voice/stream`：流式识别（`speech_start`/`partial`/`final`）+ 打断（`interrupt`）。

### TTS 引擎切换

默认自动选择：`models/cosyvoice2/llm.pt` 存在时用 **CosyVoice2**，否则用随包的 **Kokoro**；也可用 `TTS_ENGINE=kokoro|cosyvoice|piper` 强制指定。

补齐 `CosyVoice2/llm.pt` 后可切到 CosyVoice2（中文多音字消歧、自然度最好）：

```powershell
$env:TTS_ENGINE='cosyvoice'; python -m voice_service
```

Kokoro 的启动方式：

```powershell
$env:TTS_ENGINE='kokoro'; python -m voice_service
```

启动时会预加载模型，因此首句合成约 1 秒、后续句约 0.15 秒。`voice` 参数可传中文音色 id（如 `zf_001`、`zm_010`）。

切回 Piper（CPU 兜底）：

```powershell
$env:TTS_ENGINE='piper'; python -m voice_service
```

---

## ❓ 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| 启动报 `[Errno 10048] ... 8100` | 端口被占用（上个窗口没关）。关掉旧窗口，或 `$env:VOICE_PORT="8101"; python -m voice_service` 换端口。 |
| `/health` 里 `asr`/`tts` 是 `false`、接口返回 501 | 模型没下载。回到「第 3 步」运行下载脚本。 |
| demo 页「录音识别」没反应 / 识别为空 | 检查浏览器麦克风权限（地址栏锁图标 → 允许），说话离近点、说满 2～3 秒。 |
| 下载模型报超时 | 脚本内置代理重试，已存在的文件会自动跳过，直接重跑即可。 |

---

## 📁 目录结构

```
voice_service/
├── app.py             # FastAPI 应用（契约入口）
├── __main__.py        # python -m voice_service 启动入口
├── asr.py             # 本地语音识别
├── tts.py             # 本地语音合成（Piper）
├── cosyvoice_tts.py   # 本地语音合成（CosyVoice2，推荐）
├── kokoro_tts.py      # 本地语音合成（Kokoro v1.1，GPU）
├── vad.py             # 能量断句 / 打断检测
├── download_models.py # 模型下载脚本（国内源）
├── download_kokoro.py # Kokoro 模型下载脚本（魔搭）
├── download_cosyvoice2.py # CosyVoice2 模型下载脚本（魔搭）
├── vendor/            # CosyVoice2 / Matcha / wetext 运行时代码（本地离线）
├── demo.html          # 独立验证页
├── requirements.txt   # 依赖清单
├── models/            # 本地模型（不入库，由下载脚本获取）
└── tests/             # 契约测试
```

---

## 🔗 与主系统的关系

- 前端 `frontend/scripts/chat.js` 直连本服务 8100 端口：说话走 `/asr`，播报走 `/tts`。
- 主后端**不依赖**本服务；本服务下线，系统退化为打字版，仍完整可用。
- 离线打包时，由组长把 `voice_service/` 连同模型目录一起纳入发行包。
