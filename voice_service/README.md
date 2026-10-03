# voice_service · 小暖语音服务

独立于主后端的语音能力服务：**只做「音频 ↔ 文本」**，前端通过 HTTP / WebSocket 直连。
拥有者：成员 A · 契约版本 v1（Day0 冻结，签名不得改动）。

## 它能做什么

| 能力 | 接口 | 说明 |
|------|------|------|
| 语音识别（听） | `POST /asr` | 把 wav 音频转成中文文字，离线本地模型 |
| 语音合成（说） | `POST /tts` | 把文字合成中文语音（wav），离线本地模型 |
| 流式识别 + 打断 | `WS /voice/stream` | 边说边出字，支持打断 |
| 探活 | `GET /health` | 检查服务是否在跑、模型是否就绪 |

所有能力均为**本地离线**，不联网、不依赖大模型 Key。

## 快速开始（4 步）

### 第 1 步：安装依赖

在**项目根目录**执行：

```powershell
python -m pip install -r voice_service\requirements.txt
```

国内网络可加清华镜像，更快：

```powershell
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r voice_service\requirements.txt
```

### 第 2 步：下载模型

```powershell
python voice_service\download_models.py
```

脚本会自动走**国内可用源**（GitHub 代理 + 魔搭 ModelScope），无需翻墙。会下载：

| 模型 | 大小 | 用途 |
|------|------|------|
| `models/asr/model.int8.onnx` + `tokens.txt` | 约 232 MB | 中文语音识别 |
| `models/tts/zh_CN-huayan-medium.onnx` + `.json` | 约 60 MB | 中文语音合成（女声） |

模型文件**不入库**，缺了就再跑一次本脚本。

### 第 3 步：启动服务

```powershell
python -m voice_service
```

看到下面这行就说明启动成功：

```text
Uvicorn running on http://127.0.0.1:8100
```

> **这个窗口要保持打开**，关掉就停止服务。

### 第 4 步：验证

1. 浏览器打开 `http://127.0.0.1:8100/health`，应看到 `asr: true, tts: true`；
2. 打开 `voice_service\demo.html`，依次点「探活 → 录音识别 → 合成播放 → 流式识别 → 打断」验证。

## 环境要求

- Python **3.9 及以上**（本项目在 3.11 验证通过）
- 依赖：`fastapi` / `uvicorn` / `sherpa-onnx` / `piper-tts` / `soundfile` / `numpy`
- 模型文件由 `download_models.py` 下载，约 300 MB 磁盘空间
- 浏览器：Chrome / Edge（录音用 Web Audio，需允许麦克风权限）

## 常见问题

**启动报 `[Errno 10048] ... 8100`（端口被占用）**
说明已经有一个语音服务在跑（比如上个窗口没关）。关掉之前那个黑色窗口，或换端口：
`$env:VOICE_PORT="8101"; python -m voice_service`（同时把 demo 页的服务地址改成 8101）。

**`/health` 里 `asr`/`tts` 是 `false`，接口返回 501**
说明模型没下载。回到「第 2 步」运行 `python voice_service\download_models.py`。

**demo 页点「录音识别」没反应 / 识别为空**
先确认浏览器已允许麦克风权限（地址栏左侧锁图标 → 麦克风 → 允许），说话时离麦克风近一点、说满 2～3 秒。

**下载模型报超时**
脚本已内置代理重试；若仍失败，可手动换 `ghfast.top` 或稍后重试（脚本支持断点跳过已存在的文件）。

## 对外契约（冻结，改动需 A 与组长确认）

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

## 当前状态（已实现）

- `/health`：永远 200；模型就绪时 `asr`/`tts` 为 `true`。
- `/asr`：本地 Paraformer 中文识别；模型未下载时返回 501。
- `/tts`：本地 Piper 中文合成（wav）；模型未下载时返回 501。
- `/voice/stream`：流式识别（`speech_start`/`partial`/`final`）+ 打断（`interrupt`）。

## 目录结构

```
voice_service/
├── app.py            # FastAPI 应用（契约入口）
├── __main__.py       # python -m voice_service 启动入口
├── asr.py            # 本地语音识别
├── tts.py            # 本地语音合成
├── vad.py            # 能量断句 / 打断检测
├── download_models.py# 模型下载脚本（国内源）
├── demo.html         # 独立验证页
├── requirements.txt  # 依赖清单
├── models/           # 本地模型（不入库，由下载脚本获取）
└── tests/            # 契约测试
```

## 与主系统的关系

- 前端 `frontend/scripts/voice.js`（组长接线）直连本服务 8100 端口。
- 主后端**不依赖**本服务；本服务下线，系统退化为打字版，仍完整可用。
- 离线打包时，由组长把 `voice_service/` 连同模型目录一起纳入发行包。
