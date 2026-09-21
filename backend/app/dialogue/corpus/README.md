# 场景/风险标注语料库

本目录存放 Retriever 使用的标注语料。**正式语料库后续搭建**，当前为占位。

## 文件

- `scene_risk_corpus.jsonl` — 每行一条标注样本（当前为空占位）

## 行格式

```json
{"id": "C001", "user": "血压这两天有点高，是不是该加药了？", "risk": "R1", "scenes": ["S2", "S3"], "keywords": ["血压", "加药"], "source": "risk_cases"}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 稳定编号，约定 `C` + 3 位序号（`C001`…），全局唯一 |
| `user` | 是 | 用户原话（用于嵌入检索） |
| `risk` | 是 | 风险等级 `R3/R2b/R2a/R1/R0` |
| `scenes` | 是 | 场景类别列表，1~3 个 |
| `keywords` | 否 | 辅助关键词，参与重排加分 |
| `source` | 否 | 来源标记，便于溯源 |

## 生成与校验

```bash
# 从现有标注数据生成种子语料（稳定编号、按 user 去重）
python tools/build_scene_risk_corpus.py --output backend/app/dialogue/corpus/scene_risk_corpus.jsonl
```

## 说明

- 语料为空时，Retriever 直接返回空列表，分类器走"关键词快路径 + LLM 兜底"，功能不受影响。
- 向量缓存写入 `<repo>/.cache/embeddings/`（已 gitignore），不随仓库分发。
- 本地嵌入模型权重存放在 HuggingFace 缓存（用户主目录），不提交仓库；可用 `EMBEDDING_MODEL_PATH` 指向内网共享目录。
