# voice_service · 小暖语音服务（独立进程）

独立于主后端的语音能力服务：**只做音频 <-> 文本**，前端直连。
拥有者：成员 A。契约版本 v1（Day0 冻结）。

## 启动

```powershell
# 在仓库根目录
python -m pip install -r voice_service/requirements.txt
python -m voice_service              # 默认 http://127.0.0.1:8100
```

自测：浏览器打开 `voice_service/demo.html`（A 的独立验证页，不依赖主前端）。

## 对外契约（签名冻结，改动需 A 与组长确认）

| 接口 | 方法 | 入参 | 出参 |
|------|------|------|------|
| `/health` | GET | — | `{"status","service","version","contract","asr","tts"}` |
| `/asr` | POST | body=原始音频字节(wav)；query `language=zh` | `{"text","is_final","confidence"}` |
| `/tts` | POST | json `{"text","voice","format","speed"}` | `audio/wav` 字节流 |
| `/voice/stream` | WS | — | 事件流（见下） |

### WS 事件名（冻结）

- 客户端 → 服务端：`audio_chunk`(二进制) / `commit` / `interrupt` / `ping`
- 服务端 → 客户端：`speech_start` / `partial{text}` / `final{text}` /
  `reply_audio`(二进制) / `interrupt` / `error{code,message}`

## 当前状态（空壳）

- `/health` 永远 200（供探活、打包、接线自检）。
- `/asr` `/tts` `/voice/stream` 返回 **501**，调用方据此回退到打字 / 静默。
- A 的交付标准：把三个 501 换成真实实现，**签名与事件名不许改**。

## 目录

```
voice_service/
├── app.py            # FastAPI 应用（契约入口）
├── __main__.py       # python -m voice_service
├── demo.html         # 独立验证页（A 自用）
├── requirements.txt
├── models/           # 本地模型（不入库，由下载脚本获取）
└── tests/            # 契约测试（空壳也应通过）
```

## 与主系统的关系

- 前端 `frontend/scripts/voice.js`（组长接线）直连本服务 8100 端口。
- 主后端**不依赖**本服务；本服务下线，系统退化为打字版，仍完整可用。
- 离线打包时，由组长把 `voice_service/` 连同模型目录一起纳入发行包。
