# 贡献指南

感谢参与「小暖健康陪护」项目。本项目面向老年人健康陪护，**安全边界是第一优先级**，任何改动都不能削弱安全兜底。

## 环境准备

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m pip install -r tools/requirements.txt
```

> 只装 `tools/requirements.txt` 无法运行 `backend/tests`（缺 fastapi / httpx）。
> 语音契约测试（`voice_service/tests`）额外需要 `numpy`；跑真实语音能力还需 `voice_service/requirements.txt`。

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 全量：backend/tests + voice_service/tests（348 项）
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

仓库根目录的 `pytest.ini` 已配置 `pythonpath = .` 与 `testpaths`，也可直接 `pytest`。

**安全改动必须过红队回归集**：`backend/tests/redline_cases.jsonl`（54 例，随 CI 执行）。
新增或修改 `safety_checker` 关键词 / 兜底话术 / 风险规则时，必须同步补充用例。

## 目录归属（第三阶段起的隔离约定）

| 目录 | 归属 | 说明 |
|------|------|------|
| `voice_service/` | 成员 A | 语音契约 v1，签名与 WS 事件名改动需组长确认 |
| `backend/app/providers/`、`scripts/offline/` | 成员 B | 离线运行时；对共享文件的改动以补丁形式提交组长 |
| `backend/app/dialogue/speaker.py` | 组长 | 说话人识别，低置信必须回退 elder |
| `backend/app/` 其余、`frontend/`、`skills/`、`tools/` | 组长 | 共享文件 |

能力开关（`VOICE_ENABLED` / `OFFLINE_MODE` / `SPEAKER_DETECT_ENABLED`）**默认必须保持关闭**——
任何能力未交付或被关闭时，系统行为不得改变。

## 提交问题

- Bug 与功能建议请走 GitHub Issues，使用 `.github/ISSUE_TEMPLATE/` 中的模板。
- 报告问题时请附上复现步骤、环境信息与日志；**请先脱敏，不要粘贴 API Key 或老人隐私信息**。

## 提交 PR

1. 从 `main` 切出特性分支。
2. 保持改动聚焦，一个 PR 只解决一件事。
3. 新增或修改安全规则时，必须在 `backend/tests/redline_cases.jsonl` 补充对应用例。
4. 确保 `python -m pytest -q` 全部通过（CI 还会跑 ruff、风险分级基线与语料质检）。
5. 同步更新受影响的文档（README / backend/README.md / docs）。
6. 修改场景 taxonomy（`backend/app/dialogue/taxonomy.py`）时，需同步
   `safety_checker`、`tools/corpus_common.py`、SKILL.md、`rules/taxonomy.md`、
   `frontend/scripts/chat.js` 六处，并重跑 `tools/refresh_skill_snapshot.py` 刷新语料内嵌快照。

## 安全红线

以下规则不得放松，改动前请先在 Issue 中讨论：

- 不诊断、不开药、不调药、不劝退就医、不轻视症状。
- 急症（R3）必须引导拨打 120 / 急诊。
- 人身安全（N1/N2）与心理危机（M2）必须给出求助渠道。
- 修改安全相关默认值（如 `SEMANTIC_CHECK`、`SEMANTIC_CHECK_RISKS`）需在 PR 说明成本与安全取舍；
  离线档放宽复核范围属于「拿延迟换安全」的已知取舍（见 `docs/offline_delivery_report.md` 第 7 节）。
- 风险集合比较一律使用 `canonical_risk()`，不要用 `.upper()`（R2b/R2a 保留小写字母，大写化会静默失配）。
