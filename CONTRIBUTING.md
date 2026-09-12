# 贡献指南

感谢参与「小暖健康陪护」项目。本项目面向老年人健康陪护，**安全边界是第一优先级**，任何改动都不能削弱安全兜底。

## 环境准备

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip install -r tools/requirements.txt
```

> 只装 `tools/requirements.txt` 无法运行 `backend/tests`（缺 fastapi / httpx）。

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

仓库根目录的 `pytest.ini` 已配置 `pythonpath = .`，也可直接 `pytest`。

## 提交问题

- Bug 与功能建议请走 GitHub Issues，使用 `.github/ISSUE_TEMPLATE/` 中的模板。
- 报告问题时请附上复现步骤、环境信息与日志；**请先脱敏，不要粘贴 API Key 或老人隐私信息**。

## 提交 PR

1. 从 `main` 切出特性分支。
2. 保持改动聚焦，一个 PR 只解决一件事。
3. 新增或修改安全规则时，必须在 `backend/tests/redline_cases.jsonl` 补充对应用例。
4. 确保 `python -m pytest backend/tests -q` 全部通过。
5. 同步更新受影响的文档（README / backend/README.md / docs）。

## 安全红线

以下规则不得放松，改动前请先在 Issue 中讨论：

- 不诊断、不开药、不调药、不劝退就医、不轻视症状。
- 急症（R3）必须引导拨打 120 / 急诊。
- 人身安全（S 类）与心理危机（M0）必须给出求助渠道。
- 修改安全相关默认值（如 `SEMANTIC_CHECK`）需在 PR 说明成本与安全取舍。
