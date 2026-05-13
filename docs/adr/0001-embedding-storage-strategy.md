# ADR-0001：Embedding 多模型存储策略

- 状态：Accepted
- 日期：2026-05-13
- 关联里程碑：M1（数据底座）
- 关联文档：[docs/M1-design.md](../M1-design.md)、[AGENTS.md](../../AGENTS.md)

## 背景

RecallForge 的北极星是召回质量、引用可追溯、权限隔离与可诊断性。M6 评测阶段需要在同一份 chunk 语料上对照"模型 A vs 模型 B"，未来也需要在不影响存量召回的前提下平滑切换 embedding 基线（例如从阿里百炼 `text-embedding-v4@1024` 升级到 `text-embedding-v4@2048` 或其他厂商）。

在 M1 数据底座阶段，必须确定 chunk 与向量列之间的存储映射形态，使后续模块满足以下要求：

- 同一 chunk 可以同时挂载多种 embedding 模型产物，便于评测和灰度切换。
- 任何检索调用必须显式声明 `embedding_model`，禁止"按运行时默认配置隐式选择"。
- 维度切换不破坏既有数据：禁止把"换模型"实现为"原表 ALTER 既有向量列维度"。
- migration 与启动校验必须能从 `EmbeddingProvider` 配置反推出 DDL 与列映射，并在维度不一致时立即失败。
- `rag_chunks` 的权限字段、引用字段、parent/child 关系、版本与逻辑删除状态不应因为引入新模型而出现重复或漂移。

## 决策

**M1 采用 `rag_chunks` 多列策略**：

- 基线向量列：`rag_chunks.embedding_text_embedding_v4_1024 VECTOR(1024)`，与默认 `EmbeddingProvider`（阿里百炼 / DashScope `text-embedding-v4@1024`）绑定。
- 新增 embedding 模型时，向 `rag_chunks` 追加新列，例如 `embedding_text_embedding_v4_2048 VECTOR(2048)`、`embedding_<provider>_<slug> VECTOR(<dim>)`，列名使用稳定 slug。
- `rag_chunks.embedding_provider`、`embedding_model`、`embedding_dim` 描述基线/默认向量列，所有向量列的真实回填状态以 `embedding_metadata`（JSONB）为准，按列名为 key 记录 status、provider、model、dim、backfilled_at、latency_ms、retry_count 等诊断信息。
- repository 与 `PgVectorStore` 维护 `embedding_model -> column + dim + metric` 的映射表，所有检索调用必须显式传入 `embedding_model`，由映射路由到对应向量列。
- `EmbeddingProvider.dim` 是维度的单一事实源。migration 与运行时启动阶段都必须读取 provider 配置生成或校验 DDL，与对应向量列类型不一致时立即失败。

## 后果

### 正向

- 5 张核心表保持不变：parent/child、权限、引用、版本、hash 等字段只存一份，避免跨表对齐成本。
- M6 A/B 评测可以在完全相同的 chunk 集合上对比不同模型，结果天然可比。
- 新增模型只需要追加新列、新索引和 `embedding_model -> column` 映射，不需要批量迁移原始 chunk。
- 逻辑删除、版本切换、权限过滤、全文检索钩子等 chunk 级语义对所有模型一致，不会出现"删了文档但向量表还在"的边界态。
- 在 chunk 行内即可看到每个模型的回填状态，运维诊断更直观。

### 负向 / 约束

- `rag_chunks` 表会随模型增多而变宽，需要在 ADR、迁移和代码评审中显式控制列数膨胀。
- 每个向量列需要独立的 HNSW（或其他向量索引）配置与维护窗口，索引体积随模型数线性增长。
- repository 必须维护列映射配置，并在启动阶段做一致性校验；任何业务代码私自写向量列都会绕过这层校验，必须通过 lint / code review 拦截。
- 多列回填状态分散在 `embedding_metadata` JSONB 中，复杂查询需要 JSON 路径表达式，必要时再为常用字段建表达式索引。

## 备选方案：多表策略

| 维度 | 多列策略（采用） | 多表策略（暂不采用） |
| --- | --- | --- |
| 表数量 | 保持 5 张核心表 | 每个模型一张 `rag_chunks_<model_slug>` |
| 元数据冗余 | parent/child、权限、引用字段只存一份 | chunk 原文、权限、引用字段需要在每张模型表内复制或冗余引用 |
| 索引隔离 | 每列独立 HNSW；公用其它索引 | 每张表内索引彻底隔离，但需要重复创建 tenant/status/version 索引 |
| 评测对齐 | 同一 chunk 行内直接对比多个模型 | 必须跨表 join chunk_key 才能保证对齐 |
| 状态同步 | 版本切换、逻辑删除只改一行 | 必须在所有模型表同步状态，易出现状态漂移 |
| 代码复杂度 | repository 维护列映射 | repository 维护表路由 + 跨表事务 |

结论：多表策略在表数量较多时更便于"维度纯净"，但权限、版本、引用字段必须跨表同步，极易在删除、回滚、状态切换场景出现遗漏，与 RecallForge 的"可追溯、可诊断"要求冲突。M1 采用多列策略；如果未来出现"高维向量列过宽"或"向量列数量影响 Postgres 性能"的实测证据，可以新开 ADR 在多列策略基础上扩展为"主表 + 单独的高维向量副表"形态，但不回退到无约束多表方案。

## 禁止事项

1. **禁止**通过 `ALTER COLUMN embedding TYPE vector(<new_dim>)` 或等价语句直接替换既有向量列维度。任何维度变化必须落到新增向量列。
2. **禁止**在 `rag_chunks` 上原地复用旧向量列承载新模型 embedding；新模型必须使用稳定 slug 命名的新列。
3. **禁止**在业务代码中按运行时默认配置隐式选择向量列。检索、回填、删除路径必须显式传入 `embedding_model` 并经过 repository / `VectorStoreAdapter` 的列映射。
4. **禁止**业务代码直接写入 `rag_chunks` 的向量列或 `embedding_metadata` 字段。向量写入必须经过 `VectorStoreAdapter.upsert_chunks()`；`embedding_provider`、`embedding_model`、`embedding_dim` 的 chunk 描述必须由 `EmbeddingProvider` 与列映射推导。
5. **禁止**在未更新 ADR 的情况下引入"多表策略"或将向量列拆分到其它表。新增的存储形态必须先提交后续 ADR 并显式标记本 ADR 状态变更（Superseded 或 Amended）。
