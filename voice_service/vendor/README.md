# vendor 第三方运行时源码

这里是从上游仓库取出的**推理所需源码**，随包提交以保证离线可运行；模型权重不在本目录
（`voice_service/models/` 已被忽略，用 `voice_service/download_cosyvoice2.py` 等脚本下载）。

| 目录 | 上游 | 许可证 |
| --- | --- | --- |
| `cosyvoice/` | [FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | Apache-2.0 |
| `matcha/` | [shivammehta25/Matcha-TTS](https://github.com/shivammehta25/Matcha-TTS) | MIT（`matcha/hifigan/LICENSE` 另见其内） |
| `wetext/` | [wenet-e2e/WeTextProcessing](https://github.com/wenet-e2e/WeTextProcessing) | Apache-2.0 |
| `zero_shot_prompt.wav` | CosyVoice 仓库示例参考音色 | 随上游 Apache-2.0 |

这些文件由本项目按原样 vendored 使用，不参与本仓库的 ruff/测试；升级上游时整目录替换即可。
`pyproject.toml` 的 ruff `exclude` 已包含 `voice_service/vendor`。
