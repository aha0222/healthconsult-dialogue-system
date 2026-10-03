# voice_service/models · 本地模型目录

模型文件**不入库**，由下载脚本获取（在项目根目录执行）：

```powershell
python voice_service/download_models.py
```

下载后结构：

```
models/
├── asr/
│   ├── model.int8.onnx   # sherpa-onnx 中文 Paraformer（语音识别）
│   └── tokens.txt
└── tts/
    ├── zh_CN-huayan-medium.onnx       # piper 中文女声（语音合成）
    └── zh_CN-huayan-medium.onnx.json
```

- 缺模型时，`/asr`、`/tts` 返回 501，调用方自动回退到打字 / 静默。
- 网络受限时可先切换镜像再下载：
  `$env:HF_ENDPOINT="https://hf-mirror.com"`
