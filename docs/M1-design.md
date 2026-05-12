# M1 数据底座设计 Spec

## 背景与目标

M1 的目标是建立可追溯、可过滤、可重建、支持多 embedding 模型并存的 RAG 数据模型。此阶段只定义 Postgres schema、pgvector 初始化、全文检索钩子和 repository 边界；不实现 ChunkFlow 入库、embedding 生成、向量检索和 Agent 调用链路。

设计优先级遵循 RecallForge 的北极星：召回质量、引用可追溯、权限隔离和可诊断性优先于吞吐。

## 交付物清单

| 交付物 | 优先级 | 来源拆解 | M1 验收口径 |
| --- | --- | --- | --- |
| `rag_documents` | P0 | 文档业务主键、hash、版本、状态 | 可按 `(tenant_id, source_uri)` 定位文档，支持 hash 去重和逻辑删除 |
| `rag_parent_chunks` | P0 | parent chunk 存储 | parent/child 可通过 `parent_id` 与 `parent_key` 稳定关联 |
| `rag_chunks` | P0 | child chunk 原文、权限、引用、embedding 描述 | 保存 child 原文、权限字段、引用字段、`content_hash`、`status`、`embedding_model`、`embedding_dim` |
| pgvector extension 初始化 | P0 | 向量存储基线 | migration 从空库可创建 `vector` extension 与基线向量列 |
| 多 embedding 存储 ADR | P0 | 多列策略 vs 多表策略 | 推荐多列策略，禁止原表直接替换模型维度 |
| 核心索引 | P0 | tenant、doc_type、status、version、document_id、embedding | 查询默认命中 active 最新版本，向量列可建立 HNSW 索引 |
| `rag_ingest_jobs` | P1 | 导入任务状态与解析诊断 | 支持 `pending`、`running`、`success`、`failed`、`skipped_duplicate` |
| `rag_query_logs` | P1 | 查询可复盘日志 | 记录 question、filters、命中摘要、answer、模型、阈值和耗时 |
| repository 层封装 | P1 | 避免业务 SQL 散落 | 业务层通过 repository 读写元数据；向量写入仍由 M3 `VectorStoreAdapter` 接管 |
| 全文检索钩子 | P2 | hybrid search 预留 | `rag_chunks.content_tsv` + GIN 索引 + repository FTS 查询入口 |

优先级说明：P0 阻塞 M2/M3，必须随 M1 完成；P1 是 M1 完整性要求；P2 为 M4 hybrid search 预留，M1 只提供可调用钩子，不承诺召回效果。

## ADR：embedding 多模型存储策略

### 决策

M1 推荐采用 `rag_chunks` 多列策略：

- 基线列：`embedding_text_embedding_v4_1024 vector(1024)`。
- 后续模型新增列，例如 `embedding_text_embedding_v4_2048 vector(2048)`。
- 检索调用必须显式传入 `embedding_model`，repository 或 `PgVectorStore` 根据配置映射到对应向量列。
- 禁止通过 `ALTER COLUMN embedding TYPE vector(<new_dim>)` 直接替换既有维度。

### 备选方案

| 方案 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- |
| 多列策略 | 保持 5 张核心表；parent/child、权限、引用字段只存一份；新增模型只补新列和索引；M6 A/B 评测可在同一 chunk 集合上比较 | 表宽会增长；列映射需要配置和启动校验；每个模型的索引需要单独管理 | 推荐 |
| 多表策略 | 每个模型表维度纯净；索引隔离清晰 | chunk 原文、权限、引用字段容易重复；跨模型评测需要跨表对齐；逻辑删除和版本状态同步更复杂 | 暂不采用 |

### 设计约束

- `EmbeddingProvider.dim` 是维度单一事实源。migration 生成和运行时启动校验都必须读取 provider 配置。
- `rag_chunks.embedding_model`、`rag_chunks.embedding_provider`、`rag_chunks.embedding_dim` 只描述 M1 基线/默认向量列，不表达所有向量列的真实回填状态。repository 写入 chunk 时禁止由调用方随意传入这些字段，必须从 `EmbeddingProvider` 与列映射配置推导。
- 多列策略下，每个向量列的真实回填状态以 `embedding_metadata` 为准。推荐 JSON schema：

```json
{
  "embedding_text_embedding_v4_1024": {
    "status": "succeeded",
    "provider": "dashscope",
    "model": "text-embedding-v4@1024",
    "dim": 1024,
    "backfilled_at": "2026-05-12T10:00:00Z",
    "latency_ms": 120,
    "retry_count": 0
  }
}
```

- `embedding_dim` 与基线向量列类型存在冗余，保留它只是为了查询、日志和评测快速过滤；启动校验和单元测试必须验证它与 `embedding_text_embedding_v4_1024 VECTOR(1024)` 一致。
- 多模型扩展时，新增向量列必须同步更新 repository 的 `embedding_model -> column + dim + metric` 映射。
- M3 的 `VectorStoreAdapter.search()` 必须显式接收 `embedding_model`，不能根据运行时默认配置隐式选择列。
- 查询日志和评测报告记录实际使用的 `embedding_model`、`embedding_dim`、provider、region 和耗时。

### 权限字段基线

M1 采用闭合 `access_level` 枚举，默认取值为 `public`、`internal`、`confidential`、`restricted`。权限层可以通过配置定义这些级别的可读顺序，但数据库层必须拒绝未知值，避免越权过滤单测没有稳定输入域。`department='global'` 用于租户级公共资料，仍由服务端注入，不能由客户端或 LLM 决定。

## 表设计

### `rag_documents`

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | `BIGSERIAL` | PK | 文档内部 ID |
| `tenant_id` | `TEXT` | not null | 租户隔离主键 |
| `source_uri` | `TEXT` | not null | 文档业务来源 |
| `source_name` | `TEXT` | nullable | 展示名或文件名 |
| `doc_type` | `TEXT` | not null | `markdown`、`txt`、`pdf`、`docx`、`json`、`csv` 等 |
| `title` | `TEXT` | nullable | 文档标题 |
| `content_hash` | `CHAR(64)` | not null | 规范化内容 SHA-256 |
| `version` | `INTEGER` | not null, `>= 1` | 同一 `(tenant_id, source_uri)` 下单调递增 |
| `status` | `TEXT` | not null | `active`、`superseded`、`deleted` |
| `department` | `TEXT` | not null | 服务端权限字段；租户级公共资料使用 `global` |
| `access_level` | `TEXT` | not null, enum | `public`、`internal`、`confidential`、`restricted` |
| `metadata` | `JSONB` | not null default `{}` | 来源扩展信息 |
| `created_by` | `TEXT` | nullable | 导入用户或服务账号 |
| `updated_by` | `TEXT` | nullable | 最近更新者 |
| `created_at` | `TIMESTAMPTZ` | not null | 创建时间 |
| `updated_at` | `TIMESTAMPTZ` | not null | 更新时间 |
| `deleted_at` | `TIMESTAMPTZ` | nullable | 逻辑删除时间 |

关键约束与索引：

- `UNIQUE (tenant_id, source_uri, version)`。
- `(tenant_id, source_uri, content_hash)` 非唯一索引，用于应用层 hash 去重和显式回滚。
- `UNIQUE (tenant_id, source_uri) WHERE status = 'active'`，默认只允许一个 active 最新版本。
- `(tenant_id, source_uri)`、`(tenant_id, status, doc_type)` 索引。

### `rag_parent_chunks`

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | `BIGSERIAL` | PK | parent chunk ID |
| `tenant_id` | `TEXT` | not null | 冗余租户字段，便于过滤与审计 |
| `document_id` | `BIGINT` | FK -> `rag_documents.id` | 所属文档 |
| `source_uri` | `TEXT` | not null | 来源冗余字段，避免 parent 回查必须 join document |
| `doc_type` | `TEXT` | not null | 文档类型冗余字段 |
| `parent_key` | `TEXT` | not null | ChunkFlow 输出的稳定 parent key |
| `chunk_index` | `INTEGER` | not null | 文档内 parent 顺序 |
| `content` | `TEXT` | not null | parent 原文 |
| `content_hash` | `CHAR(64)` | not null | parent 内容 hash |
| `department` | `TEXT` | not null | 服务端权限字段；租户级公共资料使用 `global` |
| `access_level` | `TEXT` | not null, enum | `public`、`internal`、`confidential`、`restricted` |
| `heading_path` | `TEXT[]` | nullable | 标题层级 |
| `page_start` | `INTEGER` | nullable | 起始页 |
| `page_end` | `INTEGER` | nullable | 结束页 |
| `token_count` | `INTEGER` | nullable | 估算 token 数 |
| `status` | `TEXT` | not null | `active`、`superseded`、`deleted` |
| `version` | `INTEGER` | not null | 冗余文档版本 |
| `metadata` | `JSONB` | not null default `{}` | 解析扩展字段 |
| `created_at` | `TIMESTAMPTZ` | not null | 创建时间 |
| `updated_at` | `TIMESTAMPTZ` | not null | 更新时间 |
| `deleted_at` | `TIMESTAMPTZ` | nullable | 逻辑删除时间 |

关键约束与索引：

- `UNIQUE (document_id, parent_key)`。
- `(tenant_id, document_id, status)`、`(tenant_id, status, version)`、`(tenant_id, doc_type, status)`、`(tenant_id, source_uri, version) WHERE status = 'active'` 索引。
- 页码约束：`page_end >= page_start`，允许两者为空。

### `rag_chunks`

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | `BIGSERIAL` | PK | child chunk ID |
| `tenant_id` | `TEXT` | not null | chunk 级租户隔离字段 |
| `document_id` | `BIGINT` | FK -> `rag_documents.id` | 所属文档 |
| `parent_id` | `BIGINT` | FK -> `rag_parent_chunks.id` | small-to-big 回查 |
| `chunk_key` | `TEXT` | not null | ChunkFlow 输出的稳定 child key |
| `parent_key` | `TEXT` | not null | 冗余 parent key，便于跨流程追踪 |
| `chunk_index` | `INTEGER` | not null | 文档内 child 顺序 |
| `content` | `TEXT` | not null | child 原文 |
| `content_hash` | `CHAR(64)` | not null | child 内容 hash |
| `content_tsv` | `TSVECTOR` | generated | 全文检索钩子 |
| `doc_type` | `TEXT` | not null | 文档类型冗余字段 |
| `chunk_type` | `TEXT` | not null default `child` | 初版固定为 child |
| `template` | `TEXT` | nullable | ChunkFlow template |
| `department` | `TEXT` | not null | 服务端权限字段；租户级公共资料使用 `global` |
| `access_level` | `TEXT` | not null, enum | `public`、`internal`、`confidential`、`restricted` |
| `heading_path` | `TEXT[]` | nullable | 标题路径 |
| `page_start` | `INTEGER` | nullable | 起始页 |
| `page_end` | `INTEGER` | nullable | 结束页 |
| `source_uri` | `TEXT` | not null | 来源冗余字段 |
| `version` | `INTEGER` | not null | 文档版本 |
| `status` | `TEXT` | not null | `active`、`superseded`、`deleted` |
| `embedding_provider` | `TEXT` | not null | 基线/默认向量列 provider，例如 `dashscope` |
| `embedding_model` | `TEXT` | not null | 基线/默认向量列模型，例如 `text-embedding-v4@1024` |
| `embedding_dim` | `INTEGER` | not null | 基线/默认向量列维度，例如 `1024` |
| `embedding_text_embedding_v4_1024` | `VECTOR(1024)` | nullable | M3 回填的基线向量 |
| `embedding_metadata` | `JSONB` | not null default `{}` | 每个向量列的回填状态、耗时、版本等诊断信息 |
| `metadata` | `JSONB` | not null default `{}` | ChunkFlow block metadata |
| `created_at` | `TIMESTAMPTZ` | not null | 创建时间 |
| `updated_at` | `TIMESTAMPTZ` | not null | 更新时间 |
| `deleted_at` | `TIMESTAMPTZ` | nullable | 逻辑删除时间 |

关键约束与索引：

- `UNIQUE (document_id, chunk_key)`。
- `(tenant_id, doc_type)`、`(document_id)`、`(tenant_id, source_uri, version) WHERE status = 'active'`。
- `content_tsv` 使用 GIN 索引，为 M4 hybrid search 预留。
- 基线向量列使用 cosine HNSW 索引；小数据量阶段可以通过配置延迟创建或不使用该索引，但 DDL 预览保留质量优先的索引形态。M6 评测后可按数据规模调整 `ef_search`。

### `rag_ingest_jobs`

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | `BIGSERIAL` | PK | 导入任务 ID |
| `tenant_id` | `TEXT` | not null | 租户 |
| `document_id` | `BIGINT` | nullable FK -> `rag_documents.id` | 成功或跳过后关联文档 |
| `source_uri` | `TEXT` | not null | 导入来源 |
| `source_name` | `TEXT` | nullable | 展示名 |
| `doc_type` | `TEXT` | nullable | 文档类型 |
| `status` | `TEXT` | not null | `pending`、`running`、`success`、`failed`、`skipped_duplicate` |
| `content_hash` | `CHAR(64)` | nullable | 本次导入 hash |
| `version` | `INTEGER` | nullable | 本次导入版本 |
| `parser` | `TEXT` | nullable | 请求 parser |
| `template` | `TEXT` | nullable | 请求 template |
| `parser_used` | `TEXT` | nullable | 实际 parser |
| `chunker_used` | `TEXT` | nullable | 实际 chunker |
| `parent_chunk_count` | `INTEGER` | not null default `0` | parent 数量 |
| `child_chunk_count` | `INTEGER` | not null default `0` | child 数量 |
| `warnings` | `JSONB` | not null default `[]` | 解析或切片警告 |
| `parse_report` | `JSONB` | not null default `{}` | ChunkFlow parse report |
| `error_message` | `TEXT` | nullable | 失败原因 |
| `metadata` | `JSONB` | not null default `{}` | 任务扩展信息 |
| `created_by` | `TEXT` | nullable | 用户或服务账号 |
| `started_at` | `TIMESTAMPTZ` | nullable | 开始时间 |
| `finished_at` | `TIMESTAMPTZ` | nullable | 结束时间 |
| `created_at` | `TIMESTAMPTZ` | not null | 创建时间 |
| `updated_at` | `TIMESTAMPTZ` | not null | 更新时间 |

关键索引：

- `(tenant_id, status, created_at DESC)`。
- `(tenant_id, source_uri, created_at DESC)`。
- `(document_id)`。

### `rag_query_logs`

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | `BIGSERIAL` | PK | 查询日志 ID |
| `request_id` | `UUID` | not null unique | 请求 ID |
| `tenant_id` | `TEXT` | not null | 租户 |
| `user_id` | `TEXT` | not null | 审计身份，不进入 chunk metadata；服务调用使用 `service:<name>` |
| `department` | `TEXT` | not null | 请求权限上下文；租户级公共资料使用 `global` |
| `access_level` | `TEXT` | not null, enum | `public`、`internal`、`confidential`、`restricted` |
| `question` | `TEXT` | not null | 原始问题 |
| `rewritten_query` | `TEXT` | nullable | query rewrite 或 HyDE 输出 |
| `filters` | `JSONB` | not null default `{}` | 服务端注入后的最终 filters |
| `client_filters` | `JSONB` | not null default `{}` | 客户端原始业务 filters |
| `search_mode` | `TEXT` | not null | `vector`、`full_text`、`hybrid` |
| `embedding_provider` | `TEXT` | nullable | 查询 embedding provider |
| `embedding_model` | `TEXT` | nullable | 查询 embedding model |
| `embedding_dim` | `INTEGER` | nullable | 查询 embedding dim |
| `reranker_provider` | `TEXT` | nullable | reranker provider |
| `reranker_model` | `TEXT` | nullable | reranker model |
| `top_k` | `INTEGER` | nullable | recall 候选数 |
| `final_top_k` | `INTEGER` | nullable | 最终上下文数量 |
| `min_rerank_score` | `NUMERIC(6,4)` | nullable | 拒答阈值 |
| `min_top1_margin` | `NUMERIC(6,4)` | nullable | top1 margin 阈值 |
| `max_context_tokens` | `INTEGER` | nullable | 上下文预算 |
| `hit_summary` | `JSONB` | not null default `[]` | 命中 chunk、分数、rank、`score_source` 摘要 |
| `selected_references` | `JSONB` | not null default `[]` | 最终 references |
| `answer` | `TEXT` | nullable | 最终回答 |
| `refusal_reason` | `TEXT` | nullable | 拒答原因 |
| `latencies_ms` | `JSONB` | not null default `{}` | embedding、search、rerank、LLM 等耗时 |
| `metadata` | `JSONB` | not null default `{}` | 扩展诊断字段；M6 可写入 `eval_run_id` |
| `status` | `TEXT` | not null | `success`、`refused`、`failed` |
| `error_message` | `TEXT` | nullable | 异常摘要 |
| `created_at` | `TIMESTAMPTZ` | not null | 创建时间 |

关键索引：

- `UNIQUE (request_id)`。
- `(tenant_id, created_at DESC)`。
- `(tenant_id, user_id, created_at DESC)`。
- `(status, created_at DESC)`、`(tenant_id, status, created_at DESC)`。

关键 CHECK：

- `status='success'` 时 `answer` 必须非空。
- `status='refused'` 时 `refusal_reason` 必须非空。
- `status='failed'` 时 `error_message` 必须非空。
- `search_mode IN ('vector', 'hybrid')` 时 `embedding_provider`、`embedding_model`、`embedding_dim` 必须非空。

## DDL 预览

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE rag_documents (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_name TEXT,
    doc_type TEXT NOT NULL,
    title TEXT,
    content_hash CHAR(64) NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'deleted')),
    department TEXT NOT NULL,
    access_level TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT ck_rag_documents_content_hash
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_rag_documents_access_level
        CHECK (access_level IN ('public', 'internal', 'confidential', 'restricted')),
    CONSTRAINT uq_rag_documents_source_version
        UNIQUE (tenant_id, source_uri, version)
);

CREATE UNIQUE INDEX uq_rag_documents_active_source
ON rag_documents (tenant_id, source_uri)
WHERE status = 'active';

CREATE INDEX idx_rag_documents_tenant_source
ON rag_documents (tenant_id, source_uri);

CREATE INDEX idx_rag_documents_tenant_status_doc_type
ON rag_documents (tenant_id, status, doc_type);

CREATE INDEX idx_rag_documents_source_hash
ON rag_documents (tenant_id, source_uri, content_hash);

CREATE TABLE rag_parent_chunks (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    document_id BIGINT NOT NULL REFERENCES rag_documents(id) ON DELETE RESTRICT,
    source_uri TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    parent_key TEXT NOT NULL,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    department TEXT NOT NULL,
    access_level TEXT NOT NULL,
    heading_path TEXT[],
    page_start INTEGER CHECK (page_start IS NULL OR page_start >= 1),
    page_end INTEGER CHECK (page_end IS NULL OR page_end >= 1),
    token_count INTEGER CHECK (token_count IS NULL OR token_count >= 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'deleted')),
    version INTEGER NOT NULL CHECK (version >= 1),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT ck_rag_parent_chunks_content_hash
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_rag_parent_chunks_access_level
        CHECK (access_level IN ('public', 'internal', 'confidential', 'restricted')),
    CONSTRAINT ck_rag_parent_chunks_pages
        CHECK (page_start IS NULL OR page_end IS NULL OR page_end >= page_start),
    CONSTRAINT uq_rag_parent_chunks_document_key
        UNIQUE (document_id, parent_key)
);

CREATE INDEX idx_rag_parent_chunks_tenant_document_status
ON rag_parent_chunks (tenant_id, document_id, status);

CREATE INDEX idx_rag_parent_chunks_tenant_status_version
ON rag_parent_chunks (tenant_id, status, version);

CREATE INDEX idx_rag_parent_chunks_tenant_doc_type_status
ON rag_parent_chunks (tenant_id, doc_type, status);

CREATE INDEX idx_rag_parent_chunks_active_version
ON rag_parent_chunks (tenant_id, source_uri, version)
WHERE status = 'active';

CREATE TABLE rag_chunks (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    document_id BIGINT NOT NULL REFERENCES rag_documents(id) ON DELETE RESTRICT,
    parent_id BIGINT NOT NULL REFERENCES rag_parent_chunks(id) ON DELETE RESTRICT,
    chunk_key TEXT NOT NULL,
    parent_key TEXT NOT NULL,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    content_tsv TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple'::regconfig, coalesce(content, ''))
    ) STORED,
    doc_type TEXT NOT NULL,
    chunk_type TEXT NOT NULL DEFAULT 'child' CHECK (chunk_type = 'child'),
    template TEXT,
    department TEXT NOT NULL,
    access_level TEXT NOT NULL,
    heading_path TEXT[],
    page_start INTEGER CHECK (page_start IS NULL OR page_start >= 1),
    page_end INTEGER CHECK (page_end IS NULL OR page_end >= 1),
    source_uri TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'deleted')),
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_dim INTEGER NOT NULL CHECK (embedding_dim > 0),
    embedding_text_embedding_v4_1024 VECTOR(1024),
    embedding_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT ck_rag_chunks_content_hash
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_rag_chunks_access_level
        CHECK (access_level IN ('public', 'internal', 'confidential', 'restricted')),
    CONSTRAINT ck_rag_chunks_pages
        CHECK (page_start IS NULL OR page_end IS NULL OR page_end >= page_start),
    CONSTRAINT uq_rag_chunks_document_key
        UNIQUE (document_id, chunk_key)
);

CREATE INDEX idx_rag_chunks_tenant_doc_type
ON rag_chunks (tenant_id, doc_type);

CREATE INDEX idx_rag_chunks_document
ON rag_chunks (document_id);

CREATE INDEX idx_rag_chunks_parent
ON rag_chunks (parent_id);

CREATE INDEX idx_rag_chunks_active_version
ON rag_chunks (tenant_id, source_uri, version)
WHERE status = 'active';

CREATE INDEX idx_rag_chunks_permission_active
ON rag_chunks (tenant_id, department, access_level, doc_type, status, version);

CREATE INDEX idx_rag_chunks_embedding_model_active
ON rag_chunks (tenant_id, embedding_model, status)
WHERE status = 'active';

CREATE INDEX idx_rag_chunks_content_tsv_active
ON rag_chunks USING gin (content_tsv)
WHERE status = 'active';

CREATE INDEX idx_rag_chunks_embedding_text_embedding_v4_1024_hnsw
ON rag_chunks
USING hnsw (embedding_text_embedding_v4_1024 vector_cosine_ops)
WITH (m = 16, ef_construction = 200)
WHERE status = 'active' AND embedding_text_embedding_v4_1024 IS NOT NULL;

CREATE TABLE rag_ingest_jobs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    document_id BIGINT REFERENCES rag_documents(id) ON DELETE RESTRICT,
    source_uri TEXT NOT NULL,
    source_name TEXT,
    doc_type TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'success', 'failed', 'skipped_duplicate')
    ),
    content_hash CHAR(64),
    version INTEGER CHECK (version IS NULL OR version >= 1),
    parser TEXT,
    template TEXT,
    parser_used TEXT,
    chunker_used TEXT,
    parent_chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (parent_chunk_count >= 0),
    child_chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (child_chunk_count >= 0),
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    parse_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_rag_ingest_jobs_content_hash
        CHECK (content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX idx_rag_ingest_jobs_tenant_status_created
ON rag_ingest_jobs (tenant_id, status, created_at DESC);

CREATE INDEX idx_rag_ingest_jobs_tenant_source_created
ON rag_ingest_jobs (tenant_id, source_uri, created_at DESC);

CREATE INDEX idx_rag_ingest_jobs_document
ON rag_ingest_jobs (document_id);

CREATE TABLE rag_query_logs (
    id BIGSERIAL PRIMARY KEY,
    request_id UUID NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    department TEXT NOT NULL,
    access_level TEXT NOT NULL,
    question TEXT NOT NULL,
    rewritten_query TEXT,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    client_filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_mode TEXT NOT NULL CHECK (search_mode IN ('vector', 'full_text', 'hybrid')),
    embedding_provider TEXT,
    embedding_model TEXT,
    embedding_dim INTEGER CHECK (embedding_dim IS NULL OR embedding_dim > 0),
    reranker_provider TEXT,
    reranker_model TEXT,
    top_k INTEGER CHECK (top_k IS NULL OR top_k > 0),
    final_top_k INTEGER CHECK (final_top_k IS NULL OR final_top_k > 0),
    min_rerank_score NUMERIC(6,4),
    min_top1_margin NUMERIC(6,4),
    max_context_tokens INTEGER CHECK (max_context_tokens IS NULL OR max_context_tokens > 0),
    hit_summary JSONB NOT NULL DEFAULT '[]'::jsonb,
    selected_references JSONB NOT NULL DEFAULT '[]'::jsonb,
    answer TEXT,
    refusal_reason TEXT,
    latencies_ms JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('success', 'refused', 'failed')),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_rag_query_logs_access_level
        CHECK (access_level IN ('public', 'internal', 'confidential', 'restricted')),
    CONSTRAINT ck_rag_query_logs_status_payload
        CHECK (
            (status = 'success' AND answer IS NOT NULL)
            OR (status = 'refused' AND refusal_reason IS NOT NULL)
            OR (status = 'failed' AND error_message IS NOT NULL)
        ),
    CONSTRAINT ck_rag_query_logs_vector_embedding
        CHECK (
            search_mode = 'full_text'
            OR (
                embedding_provider IS NOT NULL
                AND embedding_model IS NOT NULL
                AND embedding_dim IS NOT NULL
            )
        )
);

CREATE INDEX idx_rag_query_logs_tenant_created
ON rag_query_logs (tenant_id, created_at DESC);

CREATE INDEX idx_rag_query_logs_tenant_user_created
ON rag_query_logs (tenant_id, user_id, created_at DESC);

CREATE INDEX idx_rag_query_logs_status_created
ON rag_query_logs (status, created_at DESC);

CREATE INDEX idx_rag_query_logs_tenant_status_created
ON rag_query_logs (tenant_id, status, created_at DESC);
```

## Repository 层接口签名

Repository 输入输出类型由 M1 先命名，具体可以用 Pydantic model、dataclass 或 TypedDict 实现。所有方法默认在调用方提供的事务上下文中执行；涉及版本切换、hash 去重和批量 chunk 写入的流程必须在同一事务中完成。

### 共享类型

```python
TenantId = str
DocumentId = int
ParentChunkId = int
ChunkId = int
JobId = int
RequestId = UUID
DocumentStatus = Literal["active", "superseded", "deleted"]

DocumentCreate -> DocumentRecord
ParentChunkCreate -> ParentChunkRecord
ChildChunkCreate -> ChildChunkRecord
IngestJobCreate -> IngestJobRecord
QueryLogCreate -> QueryLogRecord
ChunkFilters -> tenant_id, department, access_level, doc_type, status, version, source_uri
FullTextHit -> chunk_id, document_id, parent_id, rank, score, score_source
SupersedeResult -> document_count, parent_chunk_count, child_chunk_count
```

### `DocumentRepository`

```python
create(input: DocumentCreate) -> DocumentRecord
get(document_id: DocumentId, tenant_id: TenantId, statuses: Sequence[DocumentStatus] = ("active",)) -> DocumentRecord | None
get_latest_by_source(
    tenant_id: TenantId,
    source_uri: str,
    statuses: Sequence[DocumentStatus] = ("active",),
) -> DocumentRecord | None
find_by_source_hash(
    tenant_id: TenantId,
    source_uri: str,
    content_hash: str,
    statuses: Sequence[DocumentStatus] = ("active", "superseded"),
) -> DocumentRecord | None
next_version(tenant_id: TenantId, source_uri: str) -> int
mark_deleted(document_id: DocumentId, tenant_id: TenantId, deleted_by: str | None = None) -> DocumentRecord
```

`find_by_source_hash()` 默认包含 `superseded`，排除 `deleted`，并按 `version DESC` 返回最近一条匹配。普通导入路径使用它做 hash 去重；用户要把内容回滚到历史版本时，必须显式调用 `DocumentVersionRepository.restore_version()`，不能通过普通导入隐式回滚。

### `DocumentVersionRepository`

```python
supersede_source(tenant_id: TenantId, source_uri: str) -> SupersedeResult
delete_document_tree(document_id: DocumentId, tenant_id: TenantId, deleted_by: str | None = None) -> SupersedeResult
restore_version(source_document_id: DocumentId, tenant_id: TenantId, restored_by: str | None = None) -> DocumentRecord
```

`supersede_source()` 是版本切换的领域方法，必须在同一事务内把旧 active document、parent chunks、child chunks 全部标记为 `superseded`。M2 不应手工拼接三个 repository update，避免漏掉 parent/child 状态联动。

`restore_version()` 必须创建一个新的单调递增 active document，并克隆历史 parent/child；不得把旧版本原地改回 active。

### `ParentChunkRepository`

```python
bulk_create(document_id: DocumentId, chunks: Sequence[ParentChunkCreate]) -> list[ParentChunkRecord]
get(parent_id: ParentChunkId, tenant_id: TenantId, statuses: Sequence[DocumentStatus] = ("active",)) -> ParentChunkRecord | None
get_by_document_and_key(
    document_id: DocumentId,
    parent_key: str,
    statuses: Sequence[DocumentStatus] = ("active",),
) -> ParentChunkRecord | None
get_by_ids(
    tenant_id: TenantId,
    parent_ids: Sequence[ParentChunkId],
    statuses: Sequence[DocumentStatus] = ("active",),
) -> list[ParentChunkRecord]
mark_by_document_status(document_id: DocumentId, tenant_id: TenantId, status: DocumentStatus) -> int
```

### `ChunkRepository`

```python
bulk_create(document_id: DocumentId, chunks: Sequence[ChildChunkCreate]) -> list[ChildChunkRecord]
get(chunk_id: ChunkId, tenant_id: TenantId, statuses: Sequence[DocumentStatus] = ("active",)) -> ChildChunkRecord | None
get_by_ids(
    tenant_id: TenantId,
    chunk_ids: Sequence[ChunkId],
    statuses: Sequence[DocumentStatus] = ("active",),
) -> list[ChildChunkRecord]
get_by_parent_id(
    tenant_id: TenantId,
    parent_id: ParentChunkId,
    statuses: Sequence[DocumentStatus] = ("active",),
) -> list[ChildChunkRecord]
list_for_embedding_backfill(
    embedding_model: str,
    limit: int,
    tenant_id: TenantId | None = None,
    statuses: Sequence[DocumentStatus] = ("active",),
) -> list[ChildChunkEmbeddingSource]
mark_by_document_status(document_id: DocumentId, tenant_id: TenantId, status: DocumentStatus) -> int
search_full_text(query: str, filters: ChunkFilters, limit: int) -> list[FullTextHit]
```

`list_for_embedding_backfill()` 只返回待回填的 child 原文和定位字段；实际向量写入由 M3 的 `VectorStoreAdapter.upsert_chunks()` 完成，避免业务层直接写 pgvector 列。

### `IngestJobRepository`

```python
create(input: IngestJobCreate) -> IngestJobRecord
get(job_id: JobId, tenant_id: TenantId) -> IngestJobRecord | None
mark_running(job_id: JobId, tenant_id: TenantId) -> IngestJobRecord
mark_success(job_id: JobId, tenant_id: TenantId, result: IngestJobSuccess) -> IngestJobRecord
mark_failed(job_id: JobId, tenant_id: TenantId, error_message: str, diagnostics: Mapping[str, Any]) -> IngestJobRecord
mark_skipped_duplicate(job_id: JobId, tenant_id: TenantId, document_id: DocumentId, content_hash: str) -> IngestJobRecord
list_recent(tenant_id: TenantId, status: str | None, limit: int) -> list[IngestJobRecord]
```

### `QueryLogRepository`

```python
create(input: QueryLogCreate) -> QueryLogRecord
get_by_request_id(request_id: RequestId, tenant_id: TenantId) -> QueryLogRecord | None
list_recent(tenant_id: TenantId, user_id: str | None, limit: int) -> list[QueryLogRecord]
mark_failed(request_id: RequestId, tenant_id: TenantId, error_message: str, latencies_ms: Mapping[str, int]) -> QueryLogRecord
```

## 全文检索钩子

M1 采用 `rag_chunks.content_tsv` 作为全文检索钩子：

- `content_tsv` 是 stored generated column，由 `content` 生成。
- `idx_rag_chunks_content_tsv_active` 使用 GIN 索引，只覆盖 active chunks。
- `ChunkRepository.search_full_text()` 负责注入服务端 filters，并返回 `FullTextHit`。
- 初版使用 PostgreSQL `simple` regconfig，不把它作为中文召回主力；M4 可以在 hybrid search 中把它作为候选补充。
- 未来如果接入中文分词，不修改或删除既有 generated column；新增 `content_tsv_zh TSVECTOR` 普通列并由分词任务回填，避免破坏 M1 migration 的兼容性。
- `score_source` 在该路径中使用 `full_text`。若未来引入真正 BM25 或 RRF，再分别使用 `bm25`、`rrf`、`hybrid`。

预期查询形态：

```sql
SELECT
    id AS chunk_id,
    document_id,
    parent_id,
    ts_rank_cd(content_tsv, plainto_tsquery('simple', :query)) AS score
FROM rag_chunks
WHERE tenant_id = :tenant_id
  AND status = 'active'
  AND content_tsv @@ plainto_tsquery('simple', :query)
  AND doc_type = ANY(:allowed_doc_types)
ORDER BY score DESC
LIMIT :limit;
```

权限字段如 `tenant_id`、`department`、`access_level`、`status` 和版本范围必须由服务端构造，不允许客户端或 LLM 直接决定。

## 与 M2、M3 的衔接

### M2：ChunkFlow 入库

M2 调用 M1 repository 完成：

1. 创建 `rag_ingest_jobs`，状态从 `pending` 进入 `running`。
2. 计算 64 位小写 hex 规范化内容 hash。
3. 通过 `DocumentRepository.find_by_source_hash()` 判断重复导入；该查询默认覆盖 active 与 superseded，排除 deleted。
4. hash 命中时，不新增 document/chunk，任务状态写成 `skipped_duplicate`。如果用户意图回滚到历史内容，必须走显式 `restore_version()`，普通导入路径不做隐式回滚。
5. hash 不命中时，在同一事务内先调用 `DocumentVersionRepository.supersede_source()`，把旧 active document、parent chunk、child chunk 标记为 `superseded`。
6. 通过 `next_version()` 创建新的 active document。先 supersede 再插入 active，是为了满足 `uq_rag_documents_active_source` 部分唯一索引。
7. M2 写入前必须校验 `(document_id, parent_key)` 与 `(document_id, chunk_key)` 唯一；如果 ChunkFlow 输出 key 冲突，必须 fail-fast，并把诊断写入 `rag_ingest_jobs.warnings` 或 `error_message`。
8. `package.parent_chunks` 写入 `rag_parent_chunks`。
9. `package.child_chunks` 写入 `rag_chunks`，embedding 列保持 `NULL`。
10. `parse_report`、`warnings`、`parser_used`、`chunker_used` 回写 `rag_ingest_jobs`。

### M3：向量适配

M3 在 M1 schema 上完成：

1. `EmbeddingProvider` 提供 `provider`、`model_slug`、`dim`、`distance_metric`。
2. 启动时校验 `embedding_text_embedding_v4_1024` 的维度与 `EmbeddingProvider.dim`、`rag_chunks.embedding_dim` 基线描述一致。
3. `ChunkRepository.list_for_embedding_backfill()` 找到 active 且对应向量列为 `NULL` 的 child chunks。
4. 文档入库 embedding 使用 `text_type=document`。
5. `VectorStoreAdapter.upsert_chunks()` 通过 `PgVectorStore` 写入对应向量列，并同步更新 `embedding_metadata.<column_name>` 的回填状态。
6. 查询 embedding 使用 `text_type=query`。
7. `VectorStoreAdapter.search(query_embedding, embedding_model, filters, top_k, search_mode="vector")` 根据 `embedding_model` 显式路由到对应向量列。
8. `delete_by_document_id()` 对 Postgres 初版实现为同步标记相关 chunks 失效；如果未来外接向量库，则同步删除或失效外部索引。

## 边界条件

### document/chunk 状态机

`rag_documents`、`rag_parent_chunks`、`rag_chunks` 共享 `active / superseded / deleted` 语义：

```text
active     -- 新版本创建 --> superseded
active     -- 逻辑删除 ----> deleted
superseded -- 显式恢复 ----> active 的新版本
superseded -- 逻辑删除 ----> deleted
deleted    -- 离线清理 ----> 物理删除
```

- `superseded -> active` 不允许原地修改旧版本，只能由 `restore_version()` 创建新的单调递增版本，并克隆历史 parent/child。
- `deleted` 对在线业务是终态；物理删除只能由离线清理或测试 fixture 显式执行。
- 所有 update 路径必须显式维护 `updated_at = now()`；M1 不强制 trigger，repository 契约负责落地。

### ingest job 状态机

```text
pending -> running -> success
pending -> running -> failed
pending -> running -> skipped_duplicate
```

- `create()` 只创建 `pending` job。
- `mark_running()` 只能从 `pending` 进入 `running`；对已经是 `running` 的同一 job 可以幂等返回，但不得重置 `started_at`。
- `mark_success()`、`mark_failed()`、`mark_skipped_duplicate()` 只能从 `running` 进入终态。
- `success`、`failed`、`skipped_duplicate` 都是终态。失败重试必须新建 job，不允许把旧 job 改回 `pending`。
- 每个 `mark_*` 方法必须校验 `tenant_id`、合法前置状态，并刷新 `updated_at`。

### 多 embedding model 并存

- 新模型只能通过新增向量列或未来 ADR 批准的新表策略接入。
- 新增向量列命名使用稳定 slug，例如 `embedding_text_embedding_v4_2048`。
- 每个向量列必须有独立维度校验和可选 HNSW 索引。
- `rag_chunks.embedding_provider`、`embedding_model`、`embedding_dim` 是基线列描述，不是所有向量列的完整状态；多列回填结果以 `embedding_metadata` 为准。
- 检索、评测和查询日志必须记录实际使用模型，不允许依赖默认模型隐式推断。

### 逻辑删除

- 删除文档默认只把 `rag_documents.status` 标记为 `deleted`，同时把关联 parent/child chunks 标记为 `deleted`。
- 所有检索 repository 默认强制 `status = 'active'`。
- `deleted_at` 只用于审计和后续清理任务，不作为权限判断依据。
- 物理删除仅允许在离线清理或测试 fixture 中显式执行。

### hash 去重

- hash 来源是规范化文档内容，不使用原始文件路径或上传时间，格式必须是 64 位小写 hex。
- 同一 `(tenant_id, source_uri, content_hash)` 命中 active 或 superseded 文档时，普通导入任务写 `skipped_duplicate`。
- `skipped_duplicate` 不写 parent chunk、child chunk，也不触发 embedding。
- 若历史 superseded 版本 hash 命中，也视为重复内容，避免重复 chunk 污染评测和召回。
- 数据库层不对 `(tenant_id, source_uri, content_hash)` 加唯一约束，只保留非唯一索引；是否跳过由 repository 在事务内判定，为显式 `restore_version()` 和数据修复保留空间。

### 版本管理

- `(tenant_id, source_uri)` 是文档业务主键。
- `version` 在同一业务主键下单调递增。
- hash 变化时创建新版本，并将旧 active 版本标记为 `superseded`。为满足 active 部分唯一索引，必须在插入新 active 之前先 supersede 旧 active，且两步必须在同一事务内。
- 默认检索只命中 active 最新版本。
- 跨版本召回必须由服务端显式允许，并通过白名单 filter 指定 `version`。
- 回滚旧内容必须走显式 `restore_version()`，普通导入不会把历史 superseded 文档重新激活。

## M1 完成定义

- migration 可从空 Postgres 创建 5 张核心表和 pgvector extension。
- DDL 含基线 1024 维向量列、全文检索列和核心索引。
- 数据模型能表达 parent/child、权限字段、引用字段、hash、版本和逻辑删除。
- repository 接口覆盖 M2 入库、M3 回填和 M4 全文检索钩子的最小需要。
- 多 embedding 模型共存策略有明确 ADR，不存在直接替换向量列维度的路径。
