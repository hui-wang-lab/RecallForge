# RecallForge

RecallForge 是一个面向企业级知识库的高质量 RAG（Retrieval-Augmented Generation）系统。初版优先追求召回质量上限、可追溯性、权限隔离和可评测性。

## 技术栈

- **文档切分引擎**: ChunkFlow（parent/child 双层切片）
- **向量存储**: PostgreSQL + pgvector
- **配置管理**: pydantic-settings
- **数据库迁移**: Alembic

## 当前阶段

M0 - 项目骨架搭建（配置、日志、迁移框架、目录结构）

## 快速开始

```bash
# 安装依赖（需要 uv）
uv sync

# 复制环境变量模板
cp .env.example .env
# 编辑 .env 填入实际值

# 运行测试
uv run pytest
```

## 项目结构

```
recallforge/
  api/               # HTTP API, request/response schemas
  console/           # 最小测试控制台：上传文件、问答测试、查看引用
  chunking/          # ChunkFlow 解析和切片能力
  embeddings/        # embedding 模型封装、维度配置
  ingest/            # 文档导入、清洗、切片、入库编排
  retrieval/         # 检索、rerank、parent 回查、引用组装
  storage/           # Postgres repository、VectorStoreAdapter
  evals/             # 召回评测集、评测脚本
  observability/     # tracing、query log、质量诊断
migrations/          # 数据库迁移
tests/               # 单元、集成、端到端和评测测试
```
