# M3 向量适配设计 Spec

## 背景与目标

M3 的目标是建立 RecallForge 的可替换向量库边界：上游消费 M2 已经入库的 child chunks，下游为 M4 高召回检索提供稳定的 `VectorStoreAdapter`。初版只实现 Postgres + pgvector，并完成 M2 遗留的 embedding 回填。

M3 的范围严格限定在"embedding 生成、向量写入、向量检索、向量删除同步和维度校验"这一层：

- M3 **负责** `EmbeddingProvider` 抽象、阿里百炼 / DashScope embedding provider、`VectorStoreAdapter` protocol、`PgVectorStore`、embedding 批量回填、多模型列路由、启动预检和维度校验。
- M3 **不负责** Query Understanding、服务端权限策略计算、rerank、parent expansion、references 组装、Agno Agent 或 HTTP API。这些由 M4 / M5 接管。
- M3 的 `search()` 只返回 child chunk 级 vector hits，`score_source` 初版固定为 `vector`，但接口保留 `search_mode`，为 M4 hybrid search 和后续 BM25 / RRF 预留。
- M3 不修改 ADR-0001 的多列存储决策。新增 embedding 模型只能新增向量列和列映射，不允许原地替换既有向量列维度。

设计优先级继续遵循 RecallForge 的北极星：召回质量、引用可追溯、权限隔离和可诊断性优先于吞吐。M3 可以接受较慢的精确 pgvector 检索，但不能接受维度错配、隐式模型选择、权限字段遗漏或业务层散落 pgvector SQL。

## 交付物清单

| 交付物 | 优先级 | 来源拆解 | M3 验收口径 |
| --- | --- | --- | --- |
| `EmbeddingProvider` 封装 | P0 | ROADMAP M3、AGENTS.md 默认模型矩阵 | 对外暴露 `provider`、`name`、`model_slug`、`dim`、`max_input_tokens`、`distance_metric`；`dim` 是维度单一事实源 |
| `AlibabaBailianEmbeddingProvider` | P0 | AGENTS.md 阿里百炼模型调用约束 | 使用 DashScope 原生接口；文档回填调用 `text_type=document`；查询 embedding 调用 `text_type=query`；API Key、endpoint、region 全部来自配置或 secret |
| embedding 列路由表 | P0 | ADR-0001 多列策略 | `text-embedding-v4@1024` 映射到 `rag_chunks.embedding_text_embedding_v4_1024`、dim `1024`、metric `cosine`；未知模型 fail-fast |
| `VectorChunk` 数据结构 | P0 | AGENTS.md metadata 白名单 | 保存类型化 chunk id、parent id、embedding、embedding 描述和白名单 metadata；禁止包含 `user_id` |
| `VectorSearchHit` 数据结构 | P0 | ROADMAP M3 | 包含 `score`、`score_source`、`rank`、chunk / parent 定位字段和白名单 metadata；`raw_rank` 留到 M4 rerank 后追加 |
| `VectorStoreAdapter` protocol | P0 | AGENTS.md VectorStoreAdapter 接口签名 | 提供 `upsert_chunks()`、`search(query_embedding, embedding_model, filters: VectorSearchFilter, top_k, search_mode="vector")`、`delete_by_document_id(document_id, tenant_id)`；`search()` 必须显式接收 `embedding_model`；`delete` 必须强制租户校验 |
| `PgVectorStore.upsert_chunks()` | P0 | ROADMAP M3、ADR-0001 禁止事项 | 只通过列路由写入对应向量列；更新 `embedding_metadata.<column_name>` 回填状态；业务层不直接写向量列或 JSONB |
| `PgVectorStore.search()` | P0 | ROADMAP M3、AGENTS.md metadata 过滤 | 支持 `VectorSearchFilter` 结构化过滤、cosine 距离、`score_source="vector"`；过滤条件和查询向量全部参数化，向量列名只能来自内部路由表 |
| `PgVectorStore.delete_by_document_id()` | P0 | AGENTS.md 逻辑删除同步 | 必须同时接收 `document_id` 和 `tenant_id`，存储层强制租户校验；同步把相关 chunks 标记为不可召回，并记录向量列失效状态；未来外部向量库实现可删除 collection / points |
| embedding 批量回填任务 | P0 | ROADMAP M3、M2 留给 M3 的接口 | 批量读取 active 且目标向量列为 `NULL` 的 child chunks，调用 provider 生成 document embedding，再经 `VectorStoreAdapter.upsert_chunks()` 回填；provider 内置令牌桶限流 |
| 维度一致性校验 | P0 | ROADMAP M3、ADR-0001 | provider dim、`Settings.embedding_dim`、列路由 dim、SQLAlchemy 模型列 dim、真实数据库 `vector(<dim>)` 必须一致；不一致时失败并给出明确错误 |
| 启动预检 | P0 | AGENTS.md 阿里百炼调用约束 | 启动时检查 provider 配置、region / endpoint、模型可用性、向量列存在与维度、active chunk 描述字段一致性 |
| Settings 配置接线 | P1 | `recallforge/config.py` 现有字段 | 复用现有 `embedding_provider`、`embedding_model`、`embedding_dim`、`openai_api_key`、`openai_base_url`；M3 可新增 DashScope 专用 endpoint / region / batch size / rate limit 字段，但业务代码不得硬编码 |
| 向量诊断日志 | P1 | AGENTS.md 可观测性要求 | 记录 embedding provider、model、dim、region、batch size、耗时、失败原因、chunk ids 摘要；不得泄露跨租户内容 |

优先级说明：P0 阻塞 M4 vector recall 和 M5 端到端问答，必须随 M3 完成；P1 是可运行和可诊断的完整性要求，可以与实现同批落地，但不改变 `VectorStoreAdapter` 的最小契约。

## 设计约束

下列约束作为 M3 评审清单：

- ingest/query 主流程只能依赖 `VectorStoreAdapter`。业务层、retrieval 层、Agent 层不得直接写 pgvector SQL，不得直接引用 `RagChunk.embedding_text_embedding_v4_1024` 做向量读写。
- `embedding_model` 必须显式传入回填、检索和重建路径。禁止根据运行时默认配置隐式选择向量列。
- `EmbeddingProvider.dim` 是维度单一事实源。`Settings.embedding_dim`、列路由、SQLAlchemy 模型、真实数据库列类型都必须与 provider dim 一致。
- 文档入库 embedding 必须使用 `text_type=document`；查询 embedding 必须使用 `text_type=query`。这两个入口由 provider 封装，调用方不能手写 DashScope payload。
- metadata 只允许白名单字段进入 `VectorChunk.metadata` 和 `VectorSearchHit.metadata`。`user_id` 只用于审计，不进入向量 metadata，也不参与向量过滤。
- `PgVectorStore.search()` 使用 `VectorSearchFilter` 结构化过滤，类型层面排除非法 key。运行时仍校验 `VectorSearchFilter` 的字段值不包含 SQL 片段。
- M3 只实现 `search_mode="vector"`。传入 `hybrid`、`full_text`、`bm25`、`rrf` 时必须抛出类型化错误或返回明确 unsupported，而不是静默降级。
- pgvector 初版使用精确检索，暂不新增 HNSW 索引。HNSW 仍按 AGENTS.md 中的容量阈值留到 M7 或数据量评估后启用。
- 任何新增 embedding 模型必须新增向量列和路由配置。禁止 `ALTER COLUMN embedding TYPE vector(<new_dim>)`，禁止复用旧列承载新模型。

## 模块设计

### 目录结构

```text
recallforge/
  embeddings/
    __init__.py
    provider.py              # EmbeddingProvider protocol、ProviderResult、错误类型
    alibaba_bailian.py       # DashScope 原生 embedding provider
    registry.py              # Settings -> provider factory
    backfill.py              # EmbeddingBackfillService / 批量回填编排
  storage/
    vector_store.py          # VectorChunk、VectorSearchHit、VectorSearchFilter、VectorStoreAdapter
    embedding_columns.py     # embedding_model -> column + dim + metric 路由
    pgvector_store.py        # PgVectorStore 三方法实现
    vector_preflight.py      # 启动预检与维度一致性校验
    repository.py            # 扩展 list_for_embedding_backfill，移除基线列硬编码
  tests/
    test_embedding_provider.py
    test_embedding_columns.py
    test_vector_store_contract.py
    test_vector_preflight.py
    test_embedding_backfill.py
    integration/test_pgvector_store.py
```

M3 不需要新增数据库迁移，因为 M1 已经创建基线列 `rag_chunks.embedding_text_embedding_v4_1024 VECTOR(1024)` 与 `embedding_metadata JSONB`。如果 M3 同批引入第二个 embedding 模型，则必须另起 migration 追加新向量列，并同步更新 ADR 或追加新的 ADR 说明。

### 现有配置基线

`recallforge/config.py` 当前已经提供 M3 最小可用字段：

| 字段 | 当前默认值 | M3 使用方式 |
| --- | --- | --- |
| `database_url` | `postgresql://localhost:5432/recallforge` | `PgVectorStore`、preflight、integration test 连接数据库 |
| `embedding_provider` | `dashscope` | provider factory 选择 `AlibabaBailianEmbeddingProvider` |
| `embedding_model` | `text-embedding-v4@1024` | provider `name`、列路由 key、回填和检索显式模型 |
| `embedding_dim` | `1024` | 与 provider dim、列路由 dim、数据库列维度做一致性校验 |
| `openai_api_key` | `""` | 现有兼容字段。M3 可先作为 DashScope API key fallback，但推荐新增 `dashscope_api_key` |
| `openai_base_url` | `""` | 现有兼容字段。M3 可作为 endpoint fallback，但推荐新增 `dashscope_endpoint` |
| `default_top_k` | `30` | M4 默认召回数；M3 `search()` 仍以显式 `top_k` 为准 |
| `log_level` | `INFO` | embedding 回填和 preflight 诊断日志 |

M3 推荐新增但不强制阻塞设计的字段：

| 字段 | 建议默认值 | 用途 |
| --- | --- | --- |
| `dashscope_api_key` | `""` | DashScope 原生接口密钥，优先级高于 `openai_api_key` |
| `dashscope_endpoint` | DashScope 官方默认 endpoint | provider HTTP client endpoint，不在业务代码硬编码 |
| `dashscope_region` | `""` | 启动预检检查当前 region 是否支持 `text-embedding-v4` 与 `qwen3-rerank` |
| `embedding_batch_size` | `32` | backfill 单批文本数，后续按 provider 限制校准 |
| `embedding_batch_delay_seconds` | `0.0` | backfill 批间延迟秒数；DashScope 有 QPS / RPM 限制，大规模回填时设为 `0.1` 到 `0.5` 可避免限流触发大量重试 |
| `embedding_requests_per_second` | `0` | provider 令牌桶限流速率；`0` 表示不限流；建议 DashScope 用户设为 `5` 到 `10`，根据账号 QPS 配额调整 |
| `embedding_request_timeout_seconds` | `60` | provider HTTP 请求超时 |
| `embedding_max_retries` | `3` | provider 临时错误重试次数 |

如果暂不新增 DashScope 专用字段，provider 必须清晰记录 fallback 来源，例如 `api_key_source="openai_api_key"`，避免运维误以为正在走 OpenAI 兼容接口。默认优先使用 DashScope 原生接口，OpenAI 兼容接口只作为未来兼容层。

### 现有数据库基线

M3 依赖 M1 已落地的 `RagChunk` 字段：

| 字段 | M3 语义 |
| --- | --- |
| `embedding_provider` | 基线 / 默认向量列 provider 描述，默认 `dashscope` |
| `embedding_model` | 基线 / 默认向量列模型描述，默认 `text-embedding-v4@1024` |
| `embedding_dim` | 基线 / 默认向量列维度描述，默认 `1024` |
| `embedding_text_embedding_v4_1024` | M3 回填的基线 pgvector 列，类型 `VECTOR(1024)` |
| `embedding_metadata` | 按向量列名记录回填状态、provider、model、dim、耗时、重试次数等诊断信息 |

`embedding_provider` / `embedding_model` / `embedding_dim` 不表达所有向量列的完整状态。多列策略下，每个向量列的真实回填状态以 `embedding_metadata.<column_name>` 为准。

推荐 `embedding_metadata` 结构：

```json
{
  "embedding_text_embedding_v4_1024": {
    "status": "succeeded",
    "provider": "dashscope",
    "model": "text-embedding-v4@1024",
    "model_slug": "text_embedding_v4_1024",
    "dim": 1024,
    "distance_metric": "cosine",
    "text_type": "document",
    "backfilled_at": "2026-05-13T10:00:00Z",
    "latency_ms": 120,
    "retry_count": 0,
    "backfill_run_id": "optional-uuid"
  }
}
```

失败时目标向量列保持 `NULL`，便于后续重试。M3 最小实现可以把失败写入结构化日志；若需要落库失败状态，也必须由 `PgVectorStore` 或 storage 层 helper 统一维护 `embedding_metadata`，不能让业务层直接拼 JSONB SQL。

## EmbeddingProvider

### Protocol

```python
from typing import Literal, Protocol, Sequence

DistanceMetric = Literal["cosine", "l2", "inner_product"]
EmbeddingTextType = Literal["document", "query"]


class EmbeddingProvider(Protocol):
    provider: str
    name: str
    model_slug: str
    dim: int
    max_input_tokens: int
    distance_metric: DistanceMetric

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    async def embed_query(self, text: str) -> list[float]:
        ...

    async def preflight(self) -> None:
        ...
```

语义约束：

- `provider` 是厂商或兼容层标识，例如 `dashscope`。
- `name` 是配置和日志中的模型名，例如 `text-embedding-v4@1024`。
- `model_slug` 是列名和指标标签使用的稳定 slug，例如 `text_embedding_v4_1024`。
- `dim` 是维度单一事实源。provider 返回的每条 embedding 长度必须等于 `dim`。
- `distance_metric` 初版为 `cosine`，与 pgvector `vector_cosine_ops` 和 `<=>` 距离语义一致。
- `embed_documents()` 内部固定使用 `text_type=document`。
- `embed_query()` 内部固定使用 `text_type=query`。
- provider 必须在返回前校验向量数量和维度；维度不匹配抛 `EmbeddingDimensionMismatch`，不能把错误向量交给 `VectorStoreAdapter`。

### AlibabaBailianEmbeddingProvider

`AlibabaBailianEmbeddingProvider` 是 M3 默认 provider，对应阿里百炼 / DashScope `text-embedding-v4@1024`。

职责：

- 读取 `Settings.embedding_provider`、`embedding_model`、`embedding_dim`、API key、endpoint、region、timeout、retry 配置。
- 使用 DashScope 原生 embedding 接口，保留 `text_type` 参数。
- `embed_documents(texts)` 对每批文本发送 `text_type=document`。
- `embed_query(text)` 发送 `text_type=query`。
- 对 provider 返回的 embedding 做数量、维度和空值校验。
- 记录 provider、model、dim、region、batch size、latency_ms、retry_count。
- 不在 ingest / retrieval 业务代码中暴露 DashScope SDK 或 HTTP payload 细节。

推荐请求封装形态：

```python
async def _embed(self, texts: Sequence[str], text_type: EmbeddingTextType) -> list[list[float]]:
    payload = {
        "model": self.name,
        "input": {"texts": list(texts)},
        "parameters": {"text_type": text_type},
    }
    ...
```

注意事项：

- `text_type` 只能由 `embed_documents()` / `embed_query()` 两个公开方法决定，调用方不能传任意字符串。
- 查询 embedding 与文档 embedding 的 provider 实例可以相同，但调用路径必须不同，便于审计和测试断言。M3 推荐复用同一 provider 实例（因为 `AlibabaBailianEmbeddingProvider` 内部通过 `text_type` 参数区分文档和查询），但必须通过不同方法入口调用，不能由调用方直接传 `text_type`。
- DashScope 失败、限流或 region 不可用时抛类型化异常，不允许静默降级到其它模型或其它 provider。
- 若配置了 `reranker_model="qwen3-rerank"`，启动预检应检查当前 region 是否可用。M3 不实现 reranker 调用，但不能让明显不可用的 region 进入后续 M4。

## 多模型列路由

### `EmbeddingColumnSpec`

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingColumnSpec:
    provider: str
    model: str
    model_slug: str
    column_name: str
    dim: int
    distance_metric: DistanceMetric
```

基线映射：

| `embedding_model` | `provider` | `model_slug` | `column_name` | `dim` | `distance_metric` |
| --- | --- | --- | --- | --- | --- |
| `text-embedding-v4@1024` | `dashscope` | `text_embedding_v4_1024` | `embedding_text_embedding_v4_1024` | `1024` | `cosine` |

### 路由规则

- `EmbeddingColumnRegistry.resolve(embedding_model)` 是回填和检索选择向量列的唯一入口。
- 向量列名不能来自用户输入，只能来自 `EmbeddingColumnSpec.column_name`。
- `Settings.embedding_model` 必须能在 registry 中解析。
- registry 中的 `dim` 必须等于 provider `dim`。
- `RagChunk` SQLAlchemy model 必须存在同名属性，且类型为 `Vector(dim)`。
- 真实数据库列必须存在，`format_type(atttypid, atttypmod)` 必须等于 `vector(<dim>)`。
- 新增模型时只允许追加新列，例如 `embedding_text_embedding_v4_2048 VECTOR(2048)`，并新增 registry 映射；不得改写旧列维度。

### `ChunkRepository.list_for_embedding_backfill()`

M1 当前已经提供：

```python
list_for_embedding_backfill(
    embedding_model: str,
    limit: int,
    tenant_id: TenantId | None = None,
    statuses: Sequence[DocumentStatus] = ("active",),
) -> list[ChildChunkEmbeddingSource]
```

M3 需要补齐两点：

1. 目标列是否为 `NULL` 必须由 `embedding_model -> column_name` 路由决定，不能继续硬编码 `RagChunk.embedding_text_embedding_v4_1024.is_(None)`。
2. `ChildChunkEmbeddingSource` 必须扩展为足够构造 `VectorChunk` 的源记录，至少包含 metadata 白名单字段：

```python
@dataclass
class ChildChunkEmbeddingSource:
    id: ChunkId
    tenant_id: TenantId
    document_id: DocumentId
    parent_id: ParentChunkId
    chunk_key: str
    parent_key: str
    content: str
    doc_type: str
    chunk_type: str
    template: str | None
    department: str
    access_level: str
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    source_uri: str
    version: int
    status: DocumentStatus
```

这样 backfill service 不需要重新查询 chunk，也不会把 metadata 拼装逻辑散落到业务层。

`embedding_provider`、`embedding_model`、`embedding_dim` 不从 `ChildChunkEmbeddingSource` 读取，因为 `rag_chunks` 的基线描述字段只反映基线列状态，多列场景下不代表目标列的实际 provider/model/dim。backfill service 必须从 `EmbeddingColumnSpec` 获取这三个值来构造 `VectorChunk`，确保与目标列一致。

## 向量数据结构

### Metadata 白名单

`VectorChunk.metadata` 与 `VectorSearchHit.metadata` 只能包含以下字段：

| 字段 | 来源 |
| --- | --- |
| `tenant_id` | `rag_chunks.tenant_id` |
| `document_id` | `rag_chunks.document_id` |
| `chunk_id` | `rag_chunks.id` |
| `chunk_key` | `rag_chunks.chunk_key` |
| `parent_id` | `rag_chunks.parent_id` |
| `parent_key` | `rag_chunks.parent_key` |
| `doc_type` | `rag_chunks.doc_type` |
| `chunk_type` | `rag_chunks.chunk_type` |
| `template` | `rag_chunks.template` |
| `access_level` | `rag_chunks.access_level` |
| `department` | `rag_chunks.department` |
| `heading_path` | `rag_chunks.heading_path` |
| `page_start` | `rag_chunks.page_start` |
| `page_end` | `rag_chunks.page_end` |
| `source_uri` | `rag_chunks.source_uri` |
| `version` | `rag_chunks.version` |
| `embedding_model` | resolved `EmbeddingColumnSpec.model` |
| `embedding_provider` | resolved `EmbeddingColumnSpec.provider` |
| `embedding_dim` | resolved `EmbeddingColumnSpec.dim` |
| `status` | `rag_chunks.status` |

`user_id` 明确禁止进入向量 metadata。用户身份只在请求上下文、审计日志和 M4 / M5 查询日志中使用。

顶层字段与 metadata 的关系：

- `VectorChunk` 和 `VectorSearchHit` 的部分字段同时存在于顶层和 metadata 白名单中（如 `tenant_id`、`document_id`、`chunk_id`、`parent_id`、`chunk_key`、`parent_key`、`embedding_provider`、`embedding_model`、`embedding_dim`）。
- 顶层字段用于 Python 代码层面的类型安全、路由定位和校验（如 `upsert_chunks()` 用 `tenant_id` + `chunk_id` 做行定位，用 `embedding_model` 做列路由）。
- metadata dict 包含白名单全部字段，目的是支持 Qdrant / Milvus 等外部向量库——这些库没有"顶层字段"概念，所有业务信息必须走 metadata。
- 调用方构造 `VectorChunk` 时，不需要在 metadata 中重复填入顶层已有的字段；`PgVectorStore.upsert_chunks()` 实现内部必须从顶层字段自动填充到 metadata 的对应 key，确保最终写入数据库或外部向量库的 metadata 是完整的。
- 白名单校验只针对 metadata dict 中调用方传入的字段，不校验顶层字段（顶层字段由 dataclass 类型约束保证）。

### `VectorChunk`

```python
@dataclass
class VectorChunk:
    chunk_id: ChunkId
    tenant_id: TenantId
    document_id: DocumentId
    parent_id: ParentChunkId
    chunk_key: str
    parent_key: str
    embedding: list[float]
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    metadata: dict[str, Any]
```

约束：

- `len(embedding) == embedding_dim`。
- `embedding_model` 必须能被 `EmbeddingColumnRegistry` 解析。
- `metadata` 必须只包含白名单字段。
- 同一 `VectorChunk` 不携带原文内容，避免日志和外部向量库泄露跨租户内容。原文保留在 `rag_chunks.content`。

### `VectorSearchHit`

```python
@dataclass
class VectorSearchHit:
    chunk_id: ChunkId
    document_id: DocumentId
    parent_id: ParentChunkId
    chunk_key: str
    parent_key: str
    rank: int
    score: float
    distance: float
    score_source: str
    metadata: dict[str, Any]
```

`PgVectorStore.search()` 使用 cosine distance 排序，距离越小越相关。返回给上层的 `score` 统一为相似度分数：

```text
score = 1 - cosine_distance
score_source = "vector"
```

M3 的 `rank` 即为数据库返回的排序序号。M4 / M6 引入 hybrid 或 rerank 后，可追加 `raw_rank` 保留向量排序阶段的原始序号，并让 `rank` 反映最终排序。M3 不提前预留 `raw_rank`，避免初版两字段等值造成的理解混淆。

## VectorStoreAdapter

为匹配项目的 async SQLAlchemy repository，M3 实现使用 async protocol；语义签名与 AGENTS.md 保持一致。

### VectorSearchFilter

`filters` 参数使用结构化 dataclass 而非 `Mapping[str, Any]`，在类型层面约束合法 filter key，避免运行时才发现非法字段：

```python
@dataclass(frozen=True)
class VectorSearchFilter:
    tenant_id: str                              # 必填
    department: str | list[str] | None = None
    access_level: str | list[str] | None = None
    doc_type: str | None = None
    status: str | None = None                   # 默认 "active"
    version: int | None = None
    source_uri: str | None = None
    document_id: DocumentId | None = None
```

约束：

- `tenant_id` 必填，缺失时 `search()` 直接抛 `VectorFilterError`。
- `department` 和 `access_level` 支持单值（等值过滤）或列表（`IN` 过滤），由 M4 展开权限策略后传入。
- `status` 默认为 `active`，`search()` 内部在 filters 未显式指定时强制追加。
- 所有字段均为 `str`、`int` 或其列表，不接受任意 `Any` 类型。
- 运行时仍需校验 `department` / `access_level` 列表元素不包含 SQL 片段，但类型层面已排除 `user_id` 等非法 key。

### VectorStoreAdapter Protocol

```python
from typing import Protocol, Sequence


class VectorStoreAdapter(Protocol):
    async def upsert_chunks(self, chunks: Sequence[VectorChunk]) -> None:
        ...

    async def search(
        self,
        query_embedding: Sequence[float],
        embedding_model: str,
        filters: VectorSearchFilter,
        top_k: int,
        search_mode: str = "vector",
    ) -> list[VectorSearchHit]:
        ...

    async def delete_by_document_id(self, document_id: DocumentId, tenant_id: TenantId) -> None:
        ...
```

类型说明：

- `filters` 使用 `VectorSearchFilter` 而非 `Mapping[str, Any]`，非法 filter key 在类型检查阶段即被拦截，不再完全依赖运行时白名单校验。`VectorSearchFilter` 是 frozen dataclass，`VectorStoreAdapter` 不修改过滤条件。
- `search()` 必须显式接收 `embedding_model`，禁止根据运行时默认配置隐式选择向量列。
- `delete_by_document_id()` 必须同时接收 `document_id` 和 `tenant_id`，与 `search()` 的 filters 必含 `tenant_id` 保持一致。实现中必须校验 `document_id` 属于指定的 `tenant_id`，不允许跨租户删除。`tenant_id` 由调用方从 `RequestContext` 传入，存储层不依赖外部上下文。

调用边界：

- M2 / backfill 只通过 `upsert_chunks()` 写向量。
- M4 只通过 `search()` 做 vector recall。
- 文档逻辑删除或版本失效同步只通过 `delete_by_document_id()` 让向量索引不可召回。
- `VectorStoreAdapter` 不生成 embedding。embedding 由 `EmbeddingProvider` 完成，适配器只负责向量存储和检索。

## PgVectorStore

### 构造依赖

```python
class PgVectorStore:
    def __init__(
        self,
        session: AsyncSession,
        columns: EmbeddingColumnRegistry,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        ...
```

`PgVectorStore` 是唯一允许拼接 pgvector 距离表达式和向量列名的模块。列名必须来自 `EmbeddingColumnRegistry`，不能来自 filters 或用户输入。

Session 生命周期：

- `PgVectorStore` 持有的 `session` 由调用方注入，不自行创建或关闭。
- 单次 `search()` / `upsert_chunks()` / `delete_by_document_id()` 调用在同一事务内完成，不跨调用持有事务状态。
- 调用方（如 M4 retrieval service）负责 session 的创建、提交和关闭；推荐使用 async context manager 管理 session 生命周期。
- `EmbeddingBackfillService` 使用独立的 `session_factory`，每批创建新会话，避免长事务阻塞。

### `upsert_chunks()`

职责：

1. 按 `chunk.embedding_model` 分组，逐组解析 `EmbeddingColumnSpec`。
2. 校验每个 chunk 的 `embedding_dim`、`len(embedding)` 与 spec dim 完全一致。
3. 校验 metadata 白名单，发现 `user_id` 或未知字段直接抛 `VectorMetadataError`。
4. 按 `(tenant_id, chunk_id)` 更新 `rag_chunks` 的目标向量列。
5. 对基线列回填时，确保 `embedding_provider`、`embedding_model`、`embedding_dim` 与 spec 一致；对非基线新增列，不覆盖基线描述字段。
6. JSON merge 更新 `embedding_metadata.<column_name>`，写入 `status="succeeded"`、provider、model、model_slug、dim、distance_metric、text_type（必须为 `"document"`，因为 `upsert_chunks()` 只处理文档入库 embedding）、backfilled_at、latency_ms、retry_count、backfill_run_id。
7. 刷新 `updated_at`。

失败策略：

- 任何 chunk 维度不匹配，整批失败，不写部分向量。
- 找不到目标 chunk 或 tenant 不匹配，整批失败，并在错误中包含 chunk id 摘要。
- provider 已经返回成功 embedding 但 DB 写入失败时，目标列保持原值，由 backfill retry 重新生成或复用缓存，M3 不引入跨请求 embedding 缓存。

### `search()`

职责：

1. `search_mode` 必须为 `vector`。
2. `embedding_model` 必须解析到唯一 `EmbeddingColumnSpec`。
3. `len(query_embedding)` 必须等于 spec dim。
4. `top_k` 必须大于 0，初版建议由 M4 传入 `30` 到 `50`。
5. `VectorSearchFilter.tenant_id` 必须非空。缺失 `tenant_id` 直接失败。
6. 默认强制 `status="active"`，除非服务端显式传入白名单允许的 `status`。
7. 只检索目标向量列非空的 child chunks。
8. 使用 cosine distance 排序，返回 `VectorSearchHit`。

允许的 filter key：

| key | 语义 | M3 行为 |
| --- | --- | --- |
| `tenant_id` | 租户隔离主键 | 必填，等值过滤 |
| `department` | 服务端注入的部门权限 | 等值过滤；M4 可提前展开策略为多值后传 `IN` 列表 |
| `access_level` | 服务端注入的访问级别 | M3 支持等值或 `IN` 列表过滤；M4 负责把用户级别展开为允许集合 |
| `doc_type` | 业务过滤 | 等值过滤 |
| `status` | chunk 状态 | 默认 `active`，可显式传 `active` / `superseded` / `deleted` 供测试或管理任务 |
| `version` | 文档版本 | 等值过滤；跨版本召回必须由服务端显式允许 |
| `source_uri` | 文档来源 | 等值过滤 |
| `document_id` | 文档内部 id | 等值过滤，主要用于测试或重建 |

M3 filter 语义约定：

- 等值过滤：`field = :value`，适用于单值传入。
- 列表过滤：`field IN (:v1, :v2, ...)`，适用于 `access_level` 和 `department` 在 M4 中被展开为多值白名单的场景。M3 实现必须支持动态参数数量的 `IN` 子句。
- 所有 filter 值必须参数化，禁止拼接 SQL。

拒绝的 filter key：

- `user_id`。
- 任意 SQL 片段、列名、operator、order_by。
- 未在白名单中的字段，例如 `tenant_id_override`、`embedding_column`、`raw_where`。

SQL 形态预览（基线模型示例；实际列名来自 `EmbeddingColumnRegistry`，不硬编码）：

```sql
SELECT
    id AS chunk_id,
    document_id,
    parent_id,
    chunk_key,
    parent_key,
    {column_from_registry} <=> :query_embedding AS distance,        -- FROM registry, NOT hardcoded
    1 - ({column_from_registry} <=> :query_embedding) AS score,     -- FROM registry, NOT hardcoded
    tenant_id,
    doc_type,
    chunk_type,
    template,
    access_level,
    department,
    heading_path,
    page_start,
    page_end,
    source_uri,
    version,
    status
FROM rag_chunks
WHERE tenant_id = :tenant_id
  AND status = :status
  AND {column_from_registry} IS NOT NULL                             -- FROM registry, NOT hardcoded
  AND (:doc_type IS NULL OR doc_type = :doc_type)
  AND (:source_uri IS NULL OR source_uri = :source_uri)
  AND (:version IS NULL OR version = :version)
ORDER BY {column_from_registry} <=> :query_embedding                 -- FROM registry, NOT hardcoded
LIMIT :top_k;
```

基线模型下 `{column_from_registry}` 为 `embedding_text_embedding_v4_1024`。

IN 列表过滤 SQL 形态（适用于 `access_level`、`department` 等多值过滤）：

```sql
-- access_level 列表过滤（M4 将用户级别展开为允许集合后传入）
AND (
  :access_level_list IS NULL
  OR access_level IN (:access_level_0, :access_level_1, ...)
)

-- department 列表过滤
AND (
  :department_list IS NULL
  OR department IN (:department_0, :department_1, ...)
)
```

`IN` 子句的参数数量由调用方传入的列表长度决定，实现中必须动态生成参数占位符，不能固定参数数量。

实际实现中，`embedding_text_embedding_v4_1024` 只能来自 registry 的安全列名；其它值全部参数化。列名无法通过 SQL 参数化绑定，因此推荐通过 SQLAlchemy ORM 层访问列属性来避免裸字符串拼接：

```python
# 推荐：通过 ORM 模型属性获取列对象，由 SQLAlchemy 生成安全的列引用
column = getattr(RagChunk, spec.column_name)  # spec.column_name 来自 registry

# 禁止：直接拼接列名字符串到 SQL
# f"ORDER BY {column_name} <=> :query_embedding"  # 不允许
```

### `delete_by_document_id()`

Postgres 初版不需要删除外部 collection。为了让已删除文档不可召回，`delete_by_document_id(document_id, tenant_id)` 必须：

- 校验 `document_id` 属于指定的 `tenant_id`；不属于时抛 `VectorFilterError`，不执行任何删除。
- 把 `rag_chunks.document_id = :document_id AND tenant_id = :tenant_id` 的 chunks 标记为 `status="deleted"`，刷新 `deleted_at` 与 `updated_at`。
- 对每个已配置向量列，在 `embedding_metadata.<column_name>` 中写入 `status="deleted"`、`deleted_at`、`delete_reason="document_deleted"`。
- 不默认清空向量列，保留审计与离线恢复空间；物理清理留给 M7 离线任务。
- `search()` 默认强制 `status="active"`，因此 deleted chunks 不会进入候选。

如果未来接入 Qdrant / Milvus / Pinecone，该方法在外部实现中负责同步删除或失效外部向量 points。

## Embedding 批量回填

### `EmbeddingBackfillService`

```python
class EmbeddingBackfillService:
    def __init__(
        self,
        session_factory: Callable[[], AsyncContextManager[AsyncSession]],
        provider: EmbeddingProvider,
        columns: EmbeddingColumnRegistry,
        vector_store: VectorStoreAdapter,
        repository: ChunkRepository,
        settings: Settings,
    ) -> None:
        ...
```

构造说明：

- `session_factory`：异步会话工厂，每批创建独立会话。与 M2 `IngestService` 保持一致的会话管理方式。
- `vector_store`：通过 `VectorStoreAdapter.upsert_chunks()` 写入向量，backfill 不直接操作数据库向量列。
- `repository`：用于调用 `list_for_embedding_backfill()` 读取待回填 chunks。

事务策略：

- **每批独立事务**。一批 embedding 生成 + upsert 成功后立即提交；下一批使用新的会话和事务。
- 某批 provider 失败或 DB upsert 失败时，只回滚当前批次，已成功的批次不受影响。
- 批间延迟 `embedding_batch_delay_seconds` 秒，避免触发 DashScope 限流。
- 全部批次结束后返回 `BackfillResult`，汇总 succeeded / failed / skipped 计数。

API 限流策略：

- `embedding_batch_delay_seconds` 控制批间延迟，适用于简单场景。
- `AlibabaBailianEmbeddingProvider` 内部实现令牌桶限流：按 `Settings.embedding_requests_per_second`（默认 `0`，即不限流）控制每秒最大请求数。当配置大于 0 时，provider 在发送请求前检查令牌桶，超出速率的请求阻塞等待，而非直接发送后依赖 DashScope 429 响应。
- DashScope 返回 429 时，provider 按 `embedding_max_retries` 指数退避重试；429 不计入业务 `failed` 计数，但计入 `retry_count` 诊断字段。
- 若限流策略不足导致持续 429，backfill service 应记录 warning 并继续，而不是终止整个回填任务。

```python
@dataclass
class BackfillRequest:
    embedding_model: str
    tenant_id: str | None = None
    chunk_ids: list[int] | None = None
    limit: int = 1000
    batch_size: int | None = None
    force: bool = False


@dataclass
class BackfillResult:
    embedding_model: str
    attempted: int
    succeeded: int
    failed: int
    skipped: int
    backfill_run_id: str
```

流程：

1. 通过 provider registry 解析 `embedding_model`，得到 `EmbeddingProvider`。
2. 通过 column registry 解析 `EmbeddingColumnSpec`。
3. 执行 startup / runtime 维度校验，确认 provider dim、spec dim 和数据库列 dim 一致。
4. 调用 `ChunkRepository.list_for_embedding_backfill(embedding_model, limit, tenant_id, statuses=("active",))` 读取待回填 chunks。`force=True` 或 `chunk_ids` 模式可选择已经有向量的 chunks，用于重建指定模型向量。
5. 按 `embedding_batch_size` 切批，调用 `provider.embed_documents(texts)`，provider 内部发送 `text_type=document`。
6. 把返回 embedding 与 chunk source 组装为 `VectorChunk`。
7. 调用 `VectorStoreAdapter.upsert_chunks(vector_chunks)`。
8. 记录 `backfill_run_id`、attempted、succeeded、failed、latency_ms、provider、model、dim、region。

重建指定模型向量：

- `force=False`：只选择目标向量列为 `NULL` 的 chunks。
- `force=True`：选择匹配 filters 的 chunks，即使目标向量列已有值也重新生成，并覆盖该列与 `embedding_metadata.<column_name>`。
- `chunk_ids` 非空时，只重建指定 chunk 集合；仍必须校验这些 chunks 属于同一 tenant 或由调用方提供明确 tenant filter。
- 任意重建都必须显式传入 `embedding_model`，不能用当前默认模型隐式决定。

失败与重试：

- provider 整批失败：该批 chunk 目标列保持 `NULL`，记录结构化日志，`failed += batch_size`。
- provider 返回数量不一致或维度不一致：视为 provider contract failure，整批失败。
- DB upsert 失败：整批回滚，记录 chunk id 摘要。
- M3 不引入任务表；若需要持久化 backfill job 状态，可在 M7 或运维层追加。M3 的可复盘信息来自 `embedding_metadata` 成功状态和结构化日志。

并发控制与幂等性：

- `EmbeddingBackfillService` 初版不引入分布式锁，但必须保证幂等性：同一 `backfill_run_id` 对同一批 chunk 重复执行时，结果应与单次执行一致。
- `force=False` 时，只选择目标向量列为 `NULL` 的 chunks，天然避免并发冲突（多个 backfill 任务同时运行不会重复生成 embedding）。
- `force=True` 时，允许覆盖已有向量列。`PgVectorStore.upsert_chunks()` 在写入时追加乐观锁条件：`WHERE embedding_metadata->:column->>'backfill_run_id' IS NULL OR embedding_metadata->:column->>'backfill_run_id' = :current_run_id`。若乐观锁冲突（即同一 chunk 正被另一个 `force=True` 回填写入），当前批次中冲突的 chunk 跳过，记录 `skipped`，不抛异常。这保证同一 chunk 不会被两个并发 `force` 回填互相覆盖。
- 未来 M7 若需要调度 backfill 任务，推荐在任务表中引入 `status IN ('pending', 'running', 'succeeded', 'failed')` 和 `locked_by` 字段，或在消息队列中设置幂等键。

## 维度校验与启动预检

### 维度校验

`validate_embedding_dimensions(settings, provider, columns, session)` 必须检查：

1. `settings.embedding_model == provider.name`。
2. `settings.embedding_provider == provider.provider`。
3. `settings.embedding_dim == provider.dim`。
4. `columns.resolve(provider.name).dim == provider.dim`。
5. `columns.resolve(provider.name).distance_metric == provider.distance_metric`。
6. `RagChunk` SQLAlchemy model 上存在 `column_name`，且 `Vector.dim == provider.dim`。
7. live database 中 `rag_chunks.<column_name>` 类型为 `vector(<provider.dim>)`。
8. active chunks 中同一 baseline 描述不允许出现维度错配：

```sql
SELECT count(*)
FROM rag_chunks
WHERE status = 'active'
  AND embedding_model = :embedding_model
  AND embedding_dim <> :embedding_dim;
```

任何失败都抛 `EmbeddingConfigurationError`，错误信息必须包含 provider、model、expected_dim、actual_dim、column_name。

### live DB 列类型检查

推荐使用 `format_type`，避免依赖 pgvector typmod 细节：

```sql
SELECT format_type(a.atttypid, a.atttypmod) AS type_name
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = current_schema()
  AND c.relname = 'rag_chunks'
  AND a.attname = :column_name
  AND a.attnum > 0
  AND NOT a.attisdropped;
```

期望返回 `vector(1024)`。返回空、返回非 vector、返回 `vector(1536)` 都必须 fail-fast。

### 启动预检

`run_vector_startup_preflight(settings, session)` 执行顺序：

1. 构造 provider registry 和 column registry。
2. 解析 `Settings.embedding_model`，确认支持的 provider 和列路由存在。
3. 检查 API key、endpoint、region 等远程 provider 必需配置。
4. 执行 provider `preflight()`：确认当前 region 可用 `text-embedding-v4`。若配置了 `reranker_model="qwen3-rerank"`，同时检查 reranker 可用性；若 reranker 未配置，M3 可记录 warning，M4 前必须变成强校验。
5. 执行维度校验。
6. 检查 `rag_chunks.embedding_metadata` 是否存在非 object JSON；存在则 fail-fast 或列出修复建议。
7. 输出一条结构化日志：

```json
{
  "event": "vector_preflight_succeeded",
  "provider": "dashscope",
  "embedding_model": "text-embedding-v4@1024",
  "embedding_dim": 1024,
  "column_name": "embedding_text_embedding_v4_1024",
  "distance_metric": "cosine",
  "region": "configured-region"
}
```

预检失败时，应用不应继续启动检索或回填 worker。

## 与 M1 / M2 / M4 / M5 的边界

### M3 使用 M1 提供的能力

| M1 能力 | M3 使用方式 |
| --- | --- |
| `rag_chunks.embedding_text_embedding_v4_1024 VECTOR(1024)` | `PgVectorStore.upsert_chunks()` 写入基线向量，`search()` 精确检索 |
| `rag_chunks.embedding_metadata JSONB` | 按列名记录回填状态和诊断信息 |
| `ChunkRepository.list_for_embedding_backfill()` | 选择待回填 child chunks；M3 修改其列路由逻辑 |
| `rag_chunks` 权限 / 引用 / parent 字段 | 构造 `VectorChunk.metadata` 和 `VectorSearchHit.metadata` |
| ADR-0001 多列策略 | 作为所有列路由、维度校验和禁止事项的单一设计依据 |

### M3 消费 M2 输出

- M2 成功导入的 child chunks 处于 `status="active"`。
- M2 不写向量列，基线列应为 `NULL`。
- M2 已填 `embedding_provider`、`embedding_model`、`embedding_dim` 基线描述。
- M2 已保留 `parent_id`、`parent_key`、引用字段和权限字段，M3 只把这些字段复制进向量 metadata，不重新解释权限。

### M3 留给 M4 的接口

- `EmbeddingProvider.embed_query(question)`，内部使用 `text_type=query`。
- `VectorStoreAdapter.search(query_embedding, embedding_model, filters: VectorSearchFilter, top_k, search_mode="vector")`。
- `VectorSearchHit` 中保留 `score`、`score_source`、`rank` 和 metadata。M4 引入 rerank 后可追加 `raw_rank`。
- M4 负责 Query Understanding、服务端权限 filter 构造、rerank、parent lookup、context assembly、references 和 query log。

### M3 不提供给 M5 / Agent 的能力

- M3 不暴露 pgvector store 给 Agno Agent。
- Agent 不允许直接持有 `PgVectorStore` 或任何 VectorDB 句柄。
- M5 的受控 Tool 必须调用 M4 retrieval service，而不是直接调用 M3 `search()`。

## 错误处理与边界条件

| 场景 | 错误类型 | 行为 |
| --- | --- | --- |
| 未知 `embedding_model` | `UnknownEmbeddingModelError` | fail-fast，提示支持的模型列表 |
| provider dim 与 settings dim 不一致 | `EmbeddingConfigurationError` | 启动失败 |
| DB 向量列维度不一致 | `EmbeddingConfigurationError` | 启动失败，提示 column 和 actual type |
| provider 返回向量长度不等于 dim | `EmbeddingDimensionMismatch` | 当前批次失败，不写向量 |
| `search()` query embedding 维度不匹配 | `EmbeddingDimensionMismatch` | 请求失败，不查 DB |
| `VectorSearchFilter` 缺少 `tenant_id` | `VectorFilterError` | 请求失败 |
| `VectorSearchFilter` 含非法字段（如通过手动构造绕过类型检查） | `VectorFilterError` | 请求失败，并记录审计日志钩子 |
| `search_mode != "vector"` | `UnsupportedSearchModeError` | 请求失败，留给 M4 / M8 实现 |
| `top_k <= 0` | `VectorSearchError` | 请求失败 |
| 回填过程中 chunk 被删除 | `VectorUpsertConflict` 或 skipped | 重新读取状态；deleted / superseded 不写入 |
| `delete_by_document_id()` 找不到 chunks 或 `document_id` 不属于 `tenant_id` | `VectorFilterError`（租户不匹配）或 no-op + debug log（找不到 chunks） | 租户不匹配时抛错；找不到 chunks 时幂等成功 |

M3 的错误信息必须包含可定位字段，例如 `embedding_model`、`column_name`、`expected_dim`、`actual_dim`、`chunk_ids_sample`。日志不得包含 chunk 原文。

## 测试策略

### 单元测试 `tests/test_embedding_provider.py`

- fake provider 满足 `EmbeddingProvider` protocol，暴露全部字段。
- `AlibabaBailianEmbeddingProvider.embed_documents()` mock HTTP payload，断言 `parameters.text_type == "document"`。
- `AlibabaBailianEmbeddingProvider.embed_query()` mock HTTP payload，断言 `parameters.text_type == "query"`。
- provider 返回维度错误时抛 `EmbeddingDimensionMismatch`。
- API key 缺失或 region 不可用时 `preflight()` 抛类型化异常。

### 单元测试 `tests/test_embedding_columns.py`

- `text-embedding-v4@1024` 能解析到 `embedding_text_embedding_v4_1024`、dim `1024`、metric `cosine`。
- 未知模型抛 `UnknownEmbeddingModelError`。
- slug 生成稳定：`text-embedding-v4@1024 -> text_embedding_v4_1024`。
- 新增模型必须显式注册，不能由字符串猜列名后直接使用。

### 单元测试 `tests/test_vector_store_contract.py`

- `VectorChunk.metadata` 含白名单字段时通过。
- metadata 含 `user_id` 或未知字段时失败。
- `VectorSearchHit` 必须包含 `score`、`score_source`、`rank`。
- `VectorSearchFilter` 中不含 `tenant_id` 时 `search()` 抛 `VectorFilterError`。
- `VectorSearchFilter` 中不含 `user_id` 字段（类型层面已排除）。
- `VectorStoreAdapter.search()` 不允许省略 `embedding_model`。

### 单元测试 `tests/test_vector_preflight.py`

- settings dim、provider dim、column dim 一致时通过。
- 任一维度不一致时失败，并包含 expected / actual。
- live DB `format_type` 返回 `vector(1536)` 时失败。
- `Settings.embedding_model` 不在 registry 中时失败。

### 单元测试 `tests/test_embedding_backfill.py`

- 只读取目标向量列为 `NULL` 的 active chunks。
- `force=True` 可以重建同一批 chunk 的指定 embedding 模型。
- 批量调用 provider 时使用 `embed_documents()`，不调用 `embed_query()`。
- provider 成功后只通过 `VectorStoreAdapter.upsert_chunks()` 写入。
- provider 失败时不调用 `upsert_chunks()`，记录 failed 计数。
- `ChildChunkEmbeddingSource` 字段足够构造完整 `VectorChunk.metadata`；`embedding_provider`、`embedding_model`、`embedding_dim` 从 `EmbeddingColumnSpec` 获取，不从 source 读取。

### 集成测试 `tests/integration/test_pgvector_store.py`

需要真实 Postgres + pgvector：

- 执行 M1 migration 后，确认 `rag_chunks.embedding_text_embedding_v4_1024` 类型为 `vector(1024)`。
- 插入 3 条 active child chunks，调用 `upsert_chunks()` 回填 1024 维向量；断言目标列非空，`embedding_metadata.embedding_text_embedding_v4_1024.status == "succeeded"`。
- 用 1024 维 query embedding 调用 `search()`，断言按 cosine 距离排序，`score_source == "vector"`，返回 `rank`。
- `VectorSearchFilter` 覆盖 `tenant_id`、`doc_type`、`department`、`access_level`、`status`、`version`、`source_uri`；越权 tenant 不返回结果。
- query embedding 维度为 1536 时失败。
- `VectorSearchFilter` 含 `user_id`（如通过手动构造绕过类型检查）或未知 key 时失败。
- 调用 `delete_by_document_id(document_id, tenant_id)` 后，默认 `search()` 不再返回该文档 chunks；传入不属于该 `tenant_id` 的 `document_id` 时抛 `VectorFilterError`。

### 代码扫描测试

M3 需要一个轻量测试防止 pgvector SQL 散落：

- 允许 `recallforge/storage/pgvector_store.py` 和 migration 文件包含 pgvector 距离操作符、向量列更新。
- 禁止 `recallforge/ingest/`、`recallforge/retrieval/`、`recallforge/agents/`、`recallforge/api/` 直接引用 `embedding_text_embedding_v4_1024` 或 `<=>`。
- `ChunkRepository.list_for_embedding_backfill()` 可以引用列属性，但必须通过 `EmbeddingColumnRegistry` 解析后访问。

## 实现文件清单

| 路径 | 职责 | 最小验收口径 |
| --- | --- | --- |
| `recallforge/embeddings/provider.py` | provider protocol、结果类型、错误类型 | 暴露 `EmbeddingProvider`、`EmbeddingDimensionMismatch`、`EmbeddingConfigurationError`、`EmbeddingProviderError` 等 |
| `recallforge/embeddings/alibaba_bailian.py` | DashScope provider | `embed_documents()` 使用 `text_type=document`；`embed_query()` 使用 `text_type=query`；令牌桶限流；配置缺失 fail-fast |
| `recallforge/embeddings/registry.py` | Settings -> provider factory | 根据 `embedding_provider` 构造 provider；业务代码不 import 具体厂商 |
| `recallforge/embeddings/backfill.py` | 批量回填编排 | 从 repository 读待回填 chunks，调用 provider，再调用 `VectorStoreAdapter.upsert_chunks()` |
| `recallforge/storage/embedding_columns.py` | 多模型列路由 | 基线模型映射到 `embedding_text_embedding_v4_1024`；未知模型失败；维度来自 provider |
| `recallforge/storage/vector_store.py` | 向量适配抽象 | 定义 `VectorChunk`、`VectorSearchHit`、`VectorSearchFilter`、`VectorStoreAdapter`、metadata 白名单和向量错误类型（`VectorFilterError`、`VectorMetadataError`、`VectorUpsertConflict`、`VectorSearchError`、`UnsupportedSearchModeError`） |
| `recallforge/storage/pgvector_store.py` | pgvector 实现 | 实现 `upsert_chunks()`、`search()`、`delete_by_document_id()`；所有 pgvector SQL 集中在此处 |
| `recallforge/storage/vector_preflight.py` | 启动预检 | 校验 provider、registry、SQLAlchemy model、live DB 列维度和 region 可用性 |
| `recallforge/storage/repository.py` | backfill source 查询 | `list_for_embedding_backfill()` 使用列路由，不再硬编码基线列；source 结构包含 metadata 白名单字段 |
| `recallforge/config.py` | M3 配置项 | 复用现有 embedding 字段；按需新增 DashScope endpoint / region / batch / retry 字段 |
| `tests/test_embedding_provider.py` | provider 单测 | 覆盖 text_type、维度校验、配置缺失 |
| `tests/test_embedding_columns.py` | 列路由单测 | 覆盖基线映射、未知模型、新模型显式注册 |
| `tests/test_vector_preflight.py` | 预检单测 | 覆盖维度不匹配、列缺失、live DB 类型不一致 |
| `tests/test_embedding_backfill.py` | 回填单测 | 覆盖批量、force rebuild、失败不写入 |
| `tests/integration/test_pgvector_store.py` | pgvector 集成测试 | 覆盖 upsert/search/delete/filter/dim mismatch |

## 已知限制

- `search()` 不支持分页（无 offset / cursor），只能通过 `top_k` 控制返回数量。M4 retrieval 如需分页浏览或深度检索，需要在业务层通过 `top_k` + 去重实现，或在后续 milestone 追加分页参数。
- `VectorSearchFilter` 的 `department` 和 `access_level` 列表过滤依赖 M4 构造合法值；M3 不负责权限策略展开，直接将传入值参数化到 SQL。
- `PgVectorStore` 持有调用方注入的 `AsyncSession`，不自行管理事务。调用方必须确保 session 在 `PgVectorStore` 方法调用期间有效。
- M3 不引入 backfill 任务表和分布式锁。`force=True` 通过乐观锁防止并发覆盖，但不防止同一 backfill 请求被重复发起。

## M3 完成定义

- `EmbeddingProvider` protocol 和默认 `AlibabaBailianEmbeddingProvider` 已实现，provider 字段全部来自配置，文档和查询 embedding 分别使用 `text_type=document` 与 `text_type=query`。
- `EmbeddingColumnRegistry` 明确维护 `embedding_model -> column + dim + metric` 映射，基线模型路由到 `embedding_text_embedding_v4_1024 VECTOR(1024)`。
- `VectorChunk`、`VectorSearchHit`、`VectorSearchFilter`、`VectorStoreAdapter` 已定义，metadata 白名单包含权限字段、版本字段、状态字段和 parent-child 关联字段，且禁止 `user_id`。
- `PgVectorStore.upsert_chunks()`、`search()`、`delete_by_document_id()` 已实现；业务层没有散落 pgvector SQL。
- `ChunkRepository.list_for_embedding_backfill()` 不再硬编码基线列，能针对指定 `embedding_model` 找到目标列待回填 chunks。
- `EmbeddingBackfillService` 可以对 M2 已入库的 active child chunks 批量生成并回填基线 embedding，也可以 `force=True` 重建指定模型向量。
- 启动预检覆盖 provider 配置、DashScope region / endpoint、列路由、SQLAlchemy 模型、live DB `vector(<dim>)` 和 active chunk 描述字段。
- 任意维度错配都会 fail-fast，并给出 provider、model、column、expected_dim、actual_dim。
- 单元测试覆盖 provider、列路由、metadata 白名单、preflight、backfill；集成测试覆盖 pgvector upsert/search/delete 和 filters。
- M3 不实现 rerank、parent expansion、references、Agno Tool 或 HTTP API；这些能力留给 M4 / M5。

## 自检：对照 ROADMAP M3 验收标准

| ROADMAP M3 验收 | 本 spec 覆盖位置 |
| --- | --- |
| ingest/query 主流程只依赖 `VectorStoreAdapter` | 设计约束第 1 条；`VectorStoreAdapter` 章节；`PgVectorStore` 作为唯一实现；M3 完成定义第 4 条 |
| 业务层没有散落的 pgvector SQL | 设计约束第 1 条；`PgVectorStore` 构造依赖说明；测试策略"代码扫描测试"；实现文件清单限定 pgvector SQL 只在 `pgvector_store.py` 和 migration |
| metadata 中包含权限字段、版本字段、状态字段和 parent-child 关联字段 | "Metadata 白名单"表完整列出 `tenant_id`、`department`、`access_level`、`version`、`status`、`chunk_id`、`parent_id`、`parent_key` 等字段；`VectorChunk` / `VectorSearchHit` 章节约束只使用白名单 |
| embedding 维度不匹配时失败并给出明确错误 | `EmbeddingProvider` 维度约束；"维度校验与启动预检"全章；错误处理表；测试策略 `test_vector_preflight.py` 和 pgvector 集成测试 |
| 可以针对同一批 chunk 重建指定 embedding 模型的向量 | "Embedding 批量回填"中 `BackfillRequest.embedding_model`、`chunk_ids`、`force=True`；测试策略 `test_embedding_backfill.py`；M3 完成定义第 6 条 |

