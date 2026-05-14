# AGENTS.md

本文件是 RecallForge 项目的工程协作准则。后续所有编码代理、开发者和自动化任务都应优先遵守这里的约束，再参考具体 issue、spec 或实现细节。

## 项目使命

RecallForge 的第一目标是构建高质量知识召回系统。初版不追求最大吞吐或最多功能，优先追求召回质量上限、可追溯性、权限隔离和可评测性。

当实现方案在性能、复杂度和召回质量之间冲突时，默认选择更有利于召回质量和可诊断性的方案。

## 技术基线

- 初版向量存储：Postgres + pgvector。
- 向量访问边界：所有向量写入、检索、删除必须经过 `VectorStoreAdapter`。
- 文档切分：使用 ChunkFlow 作为统一解析和切片引擎。
- 默认 RAG 形态：服务化 RAG，由上层应用或 Agent 平台通过受控 API 调用检索、上下文组装和引用能力。
- 存储原则：Postgres 保存文档元数据、parent chunk、child chunk、embedding、导入任务、查询日志和权限字段。
- 召回原则：child chunk 用于向量召回，parent chunk 用于 small-to-big 上下文补全。

## 默认模型矩阵

初版必须先定下基线模型，避免在 ingest、检索、rerank 与生成之间出现隐性不一致。所有模型相关字段都必须通过配置注入，**禁止在业务代码中硬编码厂商或模型名**。

| 角色 | 默认实现 | 维度 / 上下文 | 备注 |
| --- | --- | --- | --- |
| Embedding | 阿里百炼 / DashScope `text-embedding-v4@1024` | 1024 维 / 8192 tokens | 付费 API，中文与多语言 RAG 基线 |
| Reranker  | 阿里百炼 / DashScope `qwen3-rerank` | 单次最多重排 500 个候选 | 付费 API，初版只重排 child chunks |
| LLM       | 由上层应用或答案生成配置注入 | 上下文 ≥ 32k | 不在代码中硬编码厂商 |

模型版本管理要求：

- `embedding` 必须有 `EmbeddingProvider` 封装，对外暴露 `provider`、`name`、`model_slug`、`dim`、`max_input_tokens`、`distance_metric`。
- 任何 embedding 切换必须同步更新 `rag_chunks.embedding_model` 与 `rag_chunks.embedding_dim`，禁止"原表换模型"。
- Reranker 与 LLM 切换必须可通过配置完成，不允许业务代码 import 具体厂商 SDK。
- 解析降级与模型降级都必须落到 `rag_ingest_jobs` 或查询日志，禁止静默切换。

阿里百炼模型调用约束：

- 默认 `embedding_model` 写作 `text-embedding-v4@1024`，数据库列名推荐 `embedding_text_embedding_v4_1024`。
- 文档入库 embedding 必须使用 `text_type=document`；查询 embedding 必须使用 `text_type=query`。
- 如需评测 1536 或 2048 维，必须新增向量列或向量表，例如 `text-embedding-v4@2048`，不得复用 1024 维向量列。
- 初版优先使用 DashScope 原生接口，保留 `text_type` 等召回相关能力；OpenAI 兼容接口只作为兼容层。
- API Key、endpoint、region 必须来自 secret/config。启动时必须检查当前 region 是否可用 `text-embedding-v4` 与 `qwen3-rerank`。
- `qwen3-rerank` 只接收 vector recall 后的 child chunk 候选，初版 `top_k=50`；不要把 parent chunk 全文直接送入 reranker。
- 查询日志和评测报告必须记录 `embedding_model`、`embedding_dim`、`reranker_model`、provider、region 与调用耗时。

## 不可破坏的约束

1. 不允许业务代码直接散落 pgvector SQL。需要新增向量能力时，先扩展 `VectorStoreAdapter` 或其实现。
2. 不允许绕过服务端权限过滤。`tenant_id`、`department`、`access_level`、`doc_type`、`status`、`version` 等过滤条件必须由服务端强制注入；`user_id` 只用于身份、审计和策略计算，不作为 chunk 级向量过滤字段。
3. 不允许让用户 prompt 决定权限范围。
4. 不允许没有引用地生成知识库答案。回答必须能追溯到文档、chunk、parent、来源和页码等引用信息。
5. 无命中、低置信度或证据不足时，必须明确说明无法从当前资料确认。
6. 向量召回结果必须经过 reranker，再进入最终回答上下文。
7. 导入失败、解析降级、切片警告和检索命中必须落库或进入可观测日志。
8. 同一文档重复导入必须通过 hash 或版本机制避免重复 chunk 污染召回结果。

## 建议目录结构

项目初始阶段可采用以下结构，后续可按框架约定微调，但不要破坏职责边界：

```text
project-root/
  recallforge/
    api/               # HTTP API, request/response schemas
    console/           # 最小测试控制台：上传文件、问答测试、查看引用
    chunking/          # 从 ChunkFlow 迁移来的解析和切片能力
    embeddings/        # embedding 模型封装、维度配置、批量生成
    ingest/            # 文档导入、清洗、切片、入库编排
    retrieval/         # 检索、rerank、parent 回查、引用组装
    storage/           # Postgres repository、VectorStoreAdapter
    evals/             # 召回评测集、评测脚本、回归样例
    observability/     # tracing、query log、质量诊断工具
  migrations/          # 数据库迁移
  tests/               # 单元、集成、端到端和评测测试
  docs/                # 设计文档、ADR、迁移说明和运维说明
```

## 核心模块边界

### 文档管理

文档管理负责接收本地文件、文本或 URL 文档，并记录来源、版本、状态、权限范围和扩展元数据。

初版支持类型：

- Markdown
- TXT
- PDF
- DOCX
- JSON
- CSV

CSV、XLSX、XLSM 等表格类文档应通过 ChunkFlow 的 `table_file` 路径处理，XLSX / XLSM 需要 `openpyxl` 依赖。

#### 版本与重复导入策略

文档版本策略必须在 M2 前落地，避免重复 chunk 污染召回结果：

- 文档以 `(tenant_id, source_uri)` 作为业务主键。
- 每次导入计算规范化内容 hash，并记录在 `rag_documents.content_hash` 与 `rag_chunks.content_hash`。
- hash 相同：不新建 chunk，不写入 embedding，`rag_ingest_jobs.status` 记为 `skipped_duplicate`。
- hash 不同：创建新版本，推荐版本号为单调递增整数或时间戳版本；旧版本 chunk 标记为 `superseded`。
- 查询默认只命中 `status='active'` 的最新版本 chunk。
- 跨版本召回必须由服务端显式允许，并通过白名单 filter 指定 `version`。
- 删除文档默认逻辑删除：document 标记为 `deleted`，关联 chunk 标记为 `deleted`，并通过 `VectorStoreAdapter.delete_by_document_id()` 同步向量索引状态。

### ChunkFlow 切片

ChunkFlow 是唯一默认切片引擎。迁移范围、暂不迁移范围和验证清单维护在 [docs/chunkflow_migration.md](docs/chunkflow_migration.md)，`AGENTS.md` 只保留长期工程约束。

目标调用形态：

```python
from rag.chunking.core.pipeline import PipelineConfig, parse_to_chunk_package

package = parse_to_chunk_package(
    file_path,
    PipelineConfig(
        parser="auto",
        template="auto",
        child_max_tokens=450,
        child_min_tokens=80,
        parent_granularity="chapter",
        include_blocks=True,
    ),
)
```

入库映射要求：

- `package.parent_chunks` 写入 `rag_parent_chunks`。
- `package.child_chunks` 写入 `rag_chunks`，并通过 `VectorStoreAdapter` 写入 pgvector。
- `package.parse_report`、`package.warnings`、`package.metadata` 写入导入任务或文档 metadata。
- child chunk 必须保留 `parent_key`，用于命中后回查 parent chunk。

### VectorStoreAdapter

所有向量库实现必须符合统一接口：

```python
class VectorStoreAdapter:
    def upsert_chunks(self, chunks: list[VectorChunk]) -> None:
        ...

    def search(
        self,
        query_embedding: list[float],
        embedding_model: str,
        filters: dict,
        top_k: int,
        search_mode: str = "vector",
    ) -> list[VectorSearchHit]:
        ...

    def delete_by_document_id(self, document_id: int) -> None:
        ...
```

初版实现为 `PgVectorStore`。后续可以增加 `QdrantStore`、`MilvusStore`、`PineconeStore`，但 ingest/query 主流程不应因此改写。

向量 metadata 必须至少包含：

- `tenant_id`
- `document_id`
- `chunk_id`
- `chunk_key`
- `parent_id`
- `parent_key`
- `doc_type`
- `chunk_type`
- `template`
- `access_level`
- `department`
- `heading_path`
- `page_start`
- `page_end`
- `source_uri`
- `version`
- `embedding_model`
- `embedding_provider`
- `embedding_dim`
- `status`

`tenant_id` 是 chunk 级别隔离主键。`user_id` 仅用于查询审计与日志，不进入向量 metadata，也不参与向量过滤；按用户的访问控制通过 `access_level` 与 `department` 表达。

`VectorSearchHit` 必须保留 `score`、`score_source` 与原始排序信息。`score_source` 初版可以只有 `vector`，但接口必须预留 `bm25`、`hybrid`、`rrf`，为后续 hybrid search 做兼容。

`search()` 必须显式接收 `embedding_model`，并由 repository 或 store 实现路由到对应向量列或向量表，禁止仅凭运行时默认配置隐式选择模型。

### 检索链路

标准检索流程：

1. Query Understanding：识别空 query、过短 query、多意图 query；初版至少完成空 query 拒绝和可配置的 query rewrite / HyDE 开关。
2. 服务端构造权限和业务 metadata filters。
3. 对问题或改写后的 query 生成 query embedding。
4. 通过 `VectorStoreAdapter.search()` 召回 child chunks，初版 `top_k` 为 30 到 50。
5. 对召回结果执行 rerank。
6. 根据可调阈值做证据判定，初始建议 `min_rerank_score=0.35`、`min_top1_margin=0.05`；实际阈值必须通过 M6 评测校准。
7. 最终选择 top 5 到 8 个结果。
8. 通过 `parent_id` 或 `parent_key` 回查 parent chunk。
9. 按上下文预算组装回答上下文和 references。
10. 写入 `rag_query_logs`，包含问题、filters、命中摘要、答案和耗时。

#### 上下文组装与截断

上下文组装必须有明确预算，避免 parent 扩展把单次问答推到不可控长度：

- `max_context_tokens` 初版默认 24000，必须可配置，且不得超过当前 LLM 的安全上下文预算。
- rerank 分数越高的证据优先保留；同一 parent 下多个 child 命中时合并 parent，避免重复上下文。
- parent chunk 超长时，优先保留命中 child 周边窗口和标题路径；需要章节全文时再使用 parent 摘要或分段截断。
- references 编号在上下文组装阶段生成，使用稳定格式 `[1]`、`[2]`，并映射到 document、chunk、parent、page、source。
- 答案中的引用只能使用组装阶段生成的编号，不允许模型自行发明引用。

召回调优时优先观察：

- query 是否被正确 embedding。
- metadata filter 是否误伤候选集。
- child chunk 粒度是否过细或过宽。
- parent 回查是否补足必要上下文。
- reranker 是否把强相关证据排到前列。

### 上层应用接入边界

RecallForge 是企业知识底座，不内置 Agent Runtime，也不绑定具体 Agent 框架。上层应用、Aimate 或其他 Agent 平台需要知识能力时，必须通过 RecallForge 的服务 API 或受控 SDK 调用，不得直接持有数据库、向量库或 `VectorStoreAdapter` 句柄。

为了让权限过滤、rerank、parent 扩展、引用组装这条链路不被绕过：

1. **禁止**上层应用直接访问 RecallForge 的向量库、数据库表或向量列。
2. **禁止**把 `VectorStoreAdapter` 暴露给上层 Agent 框架自管理。
3. 检索、上下文组装和问答测试必须通过 RecallForge API 完整走：
   - 服务端 metadata filter 注入
   - query embedding（按当前 `EmbeddingProvider`）
   - `VectorStoreAdapter.search()`
   - rerank
   - parent chunk 回查
   - references 组装
4. 如未来需要适配某个 Agent 框架，必须放在可选 adapter 中，且内部只能调用 RecallForge API 或应用服务，不得新增"直连数据库"路径。

#### 请求上下文与权限传递

上层调用可能来自页面、服务端应用或 Agent Runtime，必须保证执行时能拿到当前请求的权限上下文，且用户输入或模型输出无法伪造：

- 定义 `RequestContext(tenant_id, user_id, department, access_level, request_id)`，在 API 入口创建并通过 `contextvars.ContextVar` 注入到执行作用域。
- API 或应用服务内部从 `ContextVar` 读取 `RequestContext`，构造服务端 metadata filter。
- 对外请求 schema **不得**暴露 `tenant_id`、`user_id`、`department`、`access_level` 等字段给用户或模型。调用方只能传业务维度的 `filters`（例如 `doc_type`、`source_uri`、`date_range`）。
- 服务端在调用 `VectorStoreAdapter.search()` 之前对 LLM 传入的 filters 做白名单校验，发现越权字段直接抛错并写入审计日志。
- 必须有单元测试覆盖："调用方在 filters 中传入 `tenant_id='*'` 或试图越权时，调用被拒绝且不留下任何召回痕迹"。

上层应用本身不负责判断知识权限边界。权限过滤一律在 RecallForge 服务端和检索层完成。

## 数据库要求

初版至少包含以下表：

- `rag_documents`
- `rag_parent_chunks`
- `rag_chunks`
- `rag_ingest_jobs`
- `rag_query_logs`

`rag_chunks` 同时保存 child chunk 原文和 embedding。距离度量初版使用 cosine。

### 多 embedding 模型共存

`embedding_dim` 由当前 `EmbeddingProvider` 决定，但项目必须**支持多模型并存**，以便 M6 做"模型 A vs B"评测以及未来切换基线：

- `rag_chunks` 必须包含 `embedding_model`（字符串）与 `embedding_dim`（整数）两个字段，作为 chunk 与向量列的描述。
- 初版采用**多列策略**或**多表策略**之一并写入文档，避免"原表 alter 维度"。推荐策略：
  - 多列：`rag_chunks.embedding_text_embedding_v4_1024 vector(1024)`、`rag_chunks.embedding_<next> vector(...)`，查询时按 `embedding_model` 选择列。
  - 多表：`rag_chunks_<model_slug>`，由 repository 层根据当前 provider 路由。
- 任何检索调用必须显式声明 `embedding_model`，禁止"按运行时配置隐式选择"。
- 数据库迁移工具读取 `EmbeddingProvider.dim` 生成或校验 DDL，运行时启动阶段做一致性校验，维度不匹配立即失败。

### 索引与检索性能

数据量小时可优先使用精确检索以保留召回质量；数据增长后再启用 HNSW 索引。建议触发阈值：单租户 active chunk 数超过 50 万，或单表 active chunk 数超过 200 万时，开启 HNSW。

建议索引：

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE INDEX idx_rag_chunks_embedding_text_embedding_v4_1024_hnsw
ON rag_chunks
USING hnsw (embedding_text_embedding_v4_1024 vector_cosine_ops);

CREATE INDEX idx_rag_chunks_tenant_doc_type
ON rag_chunks (tenant_id, doc_type);

CREATE INDEX idx_rag_chunks_document
ON rag_chunks (document_id);

CREATE INDEX idx_rag_chunks_active_version
ON rag_chunks (tenant_id, source_uri, version)
WHERE status = 'active';
```

## API 初版边界

### 鉴权与身份字段来源

外部 API 必须先鉴权再进入 RAG 链路。初版基线：

- 用户请求使用 Bearer JWT；服务间导入任务可以使用受限 API Key。
- `tenant_id`、`user_id`、`department`、`access_level` 从凭证 claims 或服务端会话解析。
- 请求体里的同名身份字段一律拒绝或忽略，不能覆盖凭证身份。
- 客户端允许传入的 filter 只限业务白名单，例如 `doc_type`、`source_uri`、`version`、`date_range`。
- `tenant_id`、`department`、`access_level`、`status` 等权限字段由服务端注入，客户端不得传入。
- 鉴权失败、越权 filter、跨租户访问尝试必须写入审计日志。

### 导入文档

`POST /api/rag/documents`

必须返回：

- `document_id`
- `job_id`
- `status`

导入任务需要记录 parser、template、parser_used、chunker_used、parent chunk 数、child chunk 数、warnings、parse_report、错误信息和开始结束时间。

### 查询问答

`POST /api/rag/query`

请求体必须接收：

- `question`
- `filters`

身份和权限字段必须由鉴权层注入到 `RequestContext`，不得来自请求体。

必须返回：

- `answer`
- `references`

references 至少包含 document、chunk、parent、page、source 等可追溯字段。

## 质量门槛

每个涉及 RAG 行为的改动都应考虑以下验证：

- 单元测试：metadata filter、hash 去重、ChunkFlow 映射、VectorStoreAdapter 契约。
- 集成测试：导入文档、写入 Postgres、写入向量、按权限检索。
- 端到端测试：导入至少 3 种文档格式后完成问答并返回 references。
- 召回评测：构造黄金问题集，跟踪 Recall@K、ParentRecall@K、MRR、引用准确率。
- 拒答评测：知识库外问题必须返回无法确认，不得编造。
- 权限评测：跨租户、跨部门、越权 access_level 的数据不得被召回。

初版可接受较慢，但不可接受不可解释、不可评测或越权的召回结果。

## 开发守则

- 优先保持模块边界清晰，再考虑抽象复用。
- 新增配置必须有默认值或明确失败信息，不能静默降级到错误模型或错误维度。
- embedding 维度、距离度量、reranker 类型、top_k、parent 扩展策略都必须可配置。
- 拒答阈值、上下文 token 预算、query rewrite / HyDE 开关都必须可配置，并在评测报告中记录。
- 解析器降级必须记录，例如 Docling / MinerU 不可用时降级到 `pypdf`。
- 日志中可以记录质量诊断信息，但不得泄露不该暴露给用户的跨租户内容。
- 引用组装应使用结构化字段，不要从答案文本里反向解析引用。
- 对 schema 的变更必须通过迁移管理。

## 初版完成定义

初版完成需要同时满足：

- 可以导入至少 3 种文档格式。
- 可以通过 ChunkFlow 生成 parent/child chunk。
- 可以按 `tenant_id`、`doc_type`、`department`、`access_level`、`status`、`version` 过滤检索。
- 可以完成一次端到端问答并返回 references。
- Postgres 中能看到文档、parent chunk、child chunk、embedding、导入任务、查询日志和评测报告。
- 检索调用必须通过 `VectorStoreAdapter` 完成。
- 上层应用检索必须通过 RecallForge 服务 API 完成，不能绕过服务端权限、rerank 和 parent expansion。
- 检索命中 child chunk 后，可以通过 `parent_key` 回查 parent chunk。
- 对知识库外问题能明确返回“当前资料无法确认”。
- 权限泄漏评测为 0。
- 至少有一份可重复运行、带 `eval_run_id` 的 RAG eval 报告。
