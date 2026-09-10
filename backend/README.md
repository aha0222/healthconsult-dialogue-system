# backend · 对话系统后端

负责把「老人说的话」变成「安全、温暖的回复」：载入 skill 作为 system prompt，调用 LLM，解析回复末尾的场景标记，并用安全检查做兜底。

## 状态

**骨架阶段**：目录与职责已确定，对话编排尚未实现。

## 目录

```
backend/
├── requirements.txt          # openai, pytest
├── app/
│   ├── main.py               # 入口（骨架）
│   ├── paths.py              # 仓库根 / skill 目录定位
│   ├── dialogue/             # 会话编排（骨架）
│   └── safety/
│       └── safety_checker.py # 运行时安全检查 + 数据质检核心
└── tests/
    ├── test_safety_checker.py
    ├── redline_cases.jsonl   # 红线测试集
    └── bad_sample_test_llm_bad_cases.csv
```

## 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_safety_checker.py -v
```

## 计划

1. 实现 `app/dialogue/orchestrator.py`：载入 skill -> 调 LLM -> 解析标记 -> 快检。
2. 提供 HTTP API（`app/main.py`），供 `frontend/` 调用。
3. 增加后端集成测试与端到端测试。
