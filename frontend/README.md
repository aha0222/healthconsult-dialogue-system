# frontend · 前端界面

纯静态网页，无需构建。用浏览器直接打开即可。

## 文件

| 文件 | 作用 |
|------|------|
| `index.html` | 四版人格对比展示页（适合汇报） |
| `chat.html` | 在线对话界面，支持切换人格与浏览器语音播报 |
| `assets/` | 页面图片素材 |

## 使用

直接用浏览器打开 `index.html` 或 `chat.html`。

`chat.html` 通过 HTTP 调用 `backend/` 的 API，**浏览器不接触 API Key**（密钥保存在服务端环境变量）。

启动后端：

```powershell
set DEEPSEEK_API_KEY=sk-xxx
python -m uvicorn backend.app.main:app --port 8000
```

然后浏览器打开 `chat.html`，在设置里确认后端地址（默认 `http://127.0.0.1:8000`）。

## 功能

- 流式输出（SSE）：回复逐字显示。
- 会话持久化：自动携带 `session_id` 续接上下文；右上角 🆕 可开始新对话。
- 历史对话：右上角 📋 打开会话列表，可回看历史、切换会话、删除会话。
- 访问密钥：后端开启鉴权（`BACKEND_API_KEY`）时，在设置里填写 `X-API-Key`。

## 计划

- 会话标题（自动摘要）与搜索。
- 用户身份与多设备同步。
