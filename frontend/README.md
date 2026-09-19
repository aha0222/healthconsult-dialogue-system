# frontend · 前端界面

纯静态网页，**无需构建**，用浏览器直接打开即可。

## 目录

| 路径 | 作用 |
|------|------|
| `index.html` | 四版人格选型评测报告页（三法交叉，适合汇报） |
| `chat.html` | 在线对话界面 |
| `data/personality_evaluation.js` | 报告页数据（由 `tools/export_persona_report_data.py` 自动生成，勿手改） |
| `styles/tokens.css` | 设计令牌：颜色 / 字体 / 间距 / 圆角 / 阴影 / 动效 |
| `styles/base.css` | 重置、排版、无障碍基类、暖色环境光与颗粒 |
| `styles/components.css` | 通用组件：按钮、卡片、徽章、表单、弹窗、开关、加载点 |
| `styles/chat.css` | 对话页布局 |
| `styles/report.css` | 报告页布局 |
| `scripts/chat.js` | 对话页逻辑（流式对话 / 会话 / 语音 / 适老化设置） |
| `scripts/report.js` | 报告页渲染（读 `data/personality_evaluation.js`，不含硬编码评分） |
| `assets/` | 优化后的 WebP 素材与图标 |

## 设计方向

- **原型**：Organic / Natural（暖色自然）。
- **差异化锚点**：「暖光对话面」——暖色环境光 + 纸张质感卡片，且环境光随最新风险等级轻微变化。
- **适老化**：正文 ≥18px、触控目标 ≥48px、字体三档、高对比模式、TTS / STT 保留。

## 使用

直接用浏览器打开 `index.html` 或 `chat.html`。

`index.html` 的评测数据来自 `data/personality_evaluation.js`。重跑评测后，
在仓库根目录执行 `python tools/export_persona_report_data.py` 刷新即可。

`chat.html` 通过 HTTP 调用 `backend/` 的 API，**浏览器不接触大模型 API Key**（密钥保存在服务端环境变量）。
页面里填写的「后端访问密钥」是 `BACKEND_API_KEY`（`X-API-Key`），仅在部署方开启鉴权时需要。

启动后端：

```powershell
set DEEPSEEK_API_KEY=sk-xxx
python -m uvicorn backend.app.main:app --port 8000
```

然后浏览器打开 `chat.html`，在设置里确认后端地址（默认 `http://127.0.0.1:8000`）。

> 直接用 `file://` 打开即可查看界面；但调用后端接口建议用本地静态服务器
> （如 `python -m http.server`）以避免个别浏览器对 `file://` 跨域的限制。

## 功能

- 流式输出（SSE）：回复逐字显示。
- 会话持久化：自动携带 `session_id` 续接上下文；顶栏「新对话」可重开。
- 历史对话：顶栏打开会话列表，可回看、切换、删除。
- 语音：浏览器语音输入（STT）与回复播报（TTS）。
- 适老化：字体档位、高对比模式。
- 无障碍：语义化标签、可见焦点、`aria-live` 状态播报、原生 `<dialog>` 焦点陷阱、跳转链接、`prefers-reduced-motion`。

## 计划

- 会话标题（自动摘要）与搜索。
- 用户身份与多设备同步。
- 深色模式。
