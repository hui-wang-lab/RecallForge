# M4 高召回检索设计 Spec

## 背景与目标

M4 的目标是构建 RecallForge 第一版强召回检索链路：从用户问题出发，经过 Query Understanding、服务端权限 filter 构造、vector recall、rerank、parent expansion、上下文组装和 references 组装，输出可追溯、可复盘、不越权的检索结果，为 M5 的 Agno Agent 问答和 M6 的召回评测提供完整的检索服务层。

M4 的范围严格限定在"检索链路"这一层：

- M4 **负责** Query Understanding、服务端 metadata filter 构造、vector recall（调用 M3 `VectorStoreAdapter.search()`）、reranker 抽象与默认 `qwen3-rerank` 实现、parent chunk 回查、上下文组装与截断、references 组装、拒答判定、查询日志写入。
- M4 **不负责** Agno Agent 配置与 Tool 封装、HTTP API 层、鉴权中间件、`RequestContext` 的 HTTP 入口创建。这些由 M5 接管。
- M4 **不负责** 评测集构造、eval CLI、指标计算脚本。这些由 M6 接管。
- M4 **不修改** M3 的 `VectorStoreAdapter` 接口签名、`PgVectorStore` 实现、`EmbeddingProvider` 接口或 ADR-0001 多列存储决策。M4 是 M3 的纯消费方。
- M4 需要新增一次数据库迁移：在 `rag_query_logs` 中引入 `retrieved` 状态，并调整 `ck_rag_query_logs_status_payload` CHECK 约束以适配 M4 写入 `answer=NULL` 的 `retrieved` 记录（详见"查询日志"章节）。`rag_query_logs` 表结构已在 M1 创建，M4 只追加状态枚举和约束变更。

设计优先级继续遵循 RecallForge 的北极星：召回质量、引用可追溯、权限隔离和可诊断性优先于吞吐。M4 可以接受较慢的全链路检索延迟，但不能接受越权召回、不可追溯的引用、不可复盘的检索决策或静默的证据不足强答。

## 交付物清单

| 交付物 | 优先级 | 来源拆解 | M4 验收口径 |
| --- | --- | --- | --- |
| Query Understanding 模块 | P0 | ROADMAP M4、AGENTS.md 检索链路 | 空 query 拒绝；过短 query 诊断；可配置 query rewrite / HyDE 开关；多意图 query 拆分接口预留 |
| 服务端 metadata filter 构造 | P0 | AGENTS.md 权限过滤约束 | `tenant_id` 必填；`department` / `access_level` 由服务端展开为允许集合；`status` 默认 `active`；客户端 filters 经白名单校验；越权字段直接拒绝 |
| vector recall 调用 | P0 | ROADMAP M4 | 调用 `VectorStoreAdapter.search()` 完成向量召回，初版 `top_k=50`（与 AGENTS.md 对齐），显式传入 `embedding_model` |
| RerankerProvider 抽象 | P0 | AGENTS.md 默认模型矩阵、ROADMAP M4 | 定义 `RerankerProvider` protocol，暴露 `provider`、`name`、`max_candidates`；`rerank()` 接收 query + candidates |
| DashScope `qwen3-rerank` 实现 | P0 | AGENTS.md 阿里百炼模型调用约束 | 使用 DashScope 原生 reranker 接口；单次最多 500 候选；API Key / endpoint / region 从配置注入 |
| 拒答阈值判定 | P0 | ROADMAP M4 | 可调 `min_rerank_score=0.35`、`min_top1_margin=0.05`；证据不足时返回明确拒答，不强答 |
| parent chunk 回查 | P0 | AGENTS.md small-to-big | 通过 `parent_id` / `parent_key` 回查 `rag_parent_chunks`；同 parent 下多个 child 命中时合并 parent，避免重复上下文 |
| 上下文组装与截断 | P0 | AGENTS.md 上下文组装约束 | `max_context_tokens=24000` 可配置；rerank 分数越高的证据优先保留；parent 超长时保留命中 child 周边窗口 |
| references 组装 | P0 | AGENTS.md 引用约束 | 引用编号 `[1]`、`[2]` 在组装阶段生成；映射到 document、chunk、parent、page、source；答案中只能使用组装阶段的编号 |
| 查询日志写入 | P0 | ROADMAP M4、AGENTS.md 可观测性 | 记录 query understanding、命中 chunk、分数、`score_source`、rerank 顺序、阈值判定、耗时、filters |
| hybrid search 钩子 | P1 | ROADMAP M4 | `search_mode` 支持 `vector`（M4 默认）；`hybrid` / `full_text` 钩子可被调用但不在 M4 完整实现；全文检索字段和 GIN 索引已在 M1 就绪 |
| `RetrievalService` 编排 | P0 | ROADMAP M4 | 单一服务入口串起 Query Understanding → filter → recall → rerank → refusal → parent → context → references → query log |
| `RequestContext` 与 `ContextVar` 定义 | P0 | AGENTS.md 请求上下文与权限传递 | M4 在 `recallforge/context.py` 中定义 `RequestContext` dataclass 和 `current_request_context: ContextVar`；M5 的 API 入口负责创建并注入 |
| Settings 配置项 | P1 | `recallforge/config.py` | 新增 reranker、拒答阈值、上下文预算、query rewrite 开关等配置 |

优先级说明：P0 阻塞 M5 端到端问答和 M6 召回评测，必须随 M4 完成；P1 是完整性和可配置性要求，可以与实现同批落地。

## 设计约束

下列约束作为 M4 评审清单：

- M4 的检索链路只通过 `VectorStoreAdapter.search()` 完成向量召回。禁止在 retrieval 层直接拼 pgvector SQL 或直接引用 `RagChunk.embedding_text_embedding_v4_1024`。
- `embedding_model` 必须显式传入 `VectorStoreAdapter.search()` 和 `EmbeddingProvider.embed_query()`，禁止根据运行时默认配置隐式选择向量列或 provider。
- `tenant_id`、`department`、`access_level` 由 `RequestContext` 注入，retrieval 服务从 context 读取，调用方（LLM / API）不得传入这些权限字段。
- 客户端允许传入的 filter 只限白名单：`doc_type`、`source_uri`、`version`、`date_range`。客户端传入 `tenant_id`、`department`、`access_level`、`status` 时直接拒绝并写审计日志。
- rerank 后的 `score` 是最终排序依据。`VectorSearchHit.score`（vector 阶段）在 rerank 后仅作为诊断字段保留，不参与最终排序。
- parent chunk 回查只在 rerank 后的 top candidates 上执行，不在 vector recall 的全部 `top_k` 候选上展开 parent。
- 上下文组装必须有明确 token 预算。`max_context_tokens` 初版默认 `24000`，不得超过当前 LLM 的安全上下文预算。
- references 编号在上下文组装阶段生成，使用稳定格式 `[1]`、`[2]`；答案中的引用只能使用组装阶段的编号，不允许模型自行发明。
- 查询日志必须记录完整决策链路，便于 M6 评测和事后复盘。日志不得包含跨租户 chunk 原文。
- M4 不引入 Agno Agent、HTTP 端点或鉴权中间件。M4 的输入是 `RetrievalRequest` dataclass + `RequestContext`，输出是 `RetrievalResult` dataclass。
- `user_id` 只用于查询日志审计，不参与 metadata filter 构造，不进入 `VectorSearchFilter`。
- AGENTS.md 不可破坏约束第 6 条要求"向量召回结果必须经过 reranker，再进入最终回答上下文"。M4 对此约束的实现策略：（a）生产路径中 `reranker_model` 为空时，启动 preflight 即报错，不允许检索服务启动；（b）reranker 运行时失败（非 429 重试耗尽、非临时错误）时，允许降级到 vector score 排序并记录 `reranker_fallback=True` 和 `score_source="vector"`，但必须在 query log 的 warnings 中显式标注降级；（c）测试环境可使用 `FakeRerankerProvider`，不需要走降级路径。

## 模块设计

### 目录结构

```text
recallforge/
  context.py                 # RequestContext dataclass、current_request_context ContextVar
  retrieval/
    __init__.py
    query_understanding.py    # 空 query 拒绝、过短诊断、query rewrite / HyDE 开关
    filter_builder.py         # RequestContext + 客户端 filters → VectorSearchFilter
    reranker/
      __init__.py
      provider.py             # RerankerProvider protocol、RerankedCandidate、错误类型
      dashscope_reranker.py   # DashScope qwen3-rerank 实现
      registry.py             # Settings → reranker factory
    parent_expansion.py       # parent 回查、同 parent 合并、超长截断
    context_assembly.py       # token 预算管理、证据排序、上下文截断
    references.py             # 引用编号生成、structured reference 组装
    refusal.py                # 拒答阈值判定
    retrieval_service.py      # 全链路编排
    errors.py                 # RetrievalError、QueryRejectedError 等
    types.py                  # RetrievalRequest、RetrievalResult、RetrievalContext 等
  config.py                   # 新增 M4 配置项
tests/
  test_query_understanding.py
  test_filter_builder.py
  test_reranker_provider.py
  test_parent_expansion.py
  test_context_assembly.py
  test_references.py
  test_refusal.py
  test_retrieval_service.py
  integration/test_retrieval_pipeline.py
```

M4 需要新增一次数据库迁移：在 `QUERY_STATUSES` 中追加 `retrieved`，调整 `ck_rag_query_logs_status_payload` CHECK 约束。详见"查询日志"章节。

### 现有配置基线

`recallforge/config.py` 当前已提供的 M4 可用字段：

| 字段 | 当前默认值 | M4 使用方式 |
| --- | --- | --- |
| `embedding_model` | `text-embedding-v4@1024` | `EmbeddingProvider.embed_query()` 和 `VectorStoreAdapter.search()` 的显式模型参数 |
| `embedding_dim` | `1024` | 维度校验 |
| `embedding_provider` | `dashscope` | provider registry |
| `default_top_k` | `50` | vector recall 阶段 `top_k` 默认值；与 AGENTS.md `qwen3-rerank` 初版 `top_k=50` 对齐 |
| `final_top_k` | `8` | rerank 后进入最终上下文的候选数 |
| `reranker_model` | `""` | reranker factory 选择实现 |
| `reranker_provider` | `""` | reranker factory 选择实现 |

M4 推荐新增配置项：

| 字段 | 建议默认值 | 用途 |
| --- | --- | --- |
| `reranker_model` | `qwen3-rerank` | reranker factory 选择默认 reranker |
| `reranker_provider` | `dashscope` | reranker provider 标识 |
| `reranker_top_k` | `50` | reranker 接收的最大候选数，vector recall `top_k` 的建议上限 |
| `reranker_api_key` | `""` | DashScope reranker API key；为空时 fallback 到 `dashscope_api_key` |
| `reranker_endpoint` | DashScope 官方 reranker endpoint | reranker HTTP endpoint |
| `reranker_request_timeout_seconds` | `30.0` | reranker HTTP 请求超时 |
| `reranker_max_retries` | `3` | reranker 临时错误重试次数 |
| `min_rerank_score` | `0.35` | 拒答阈值：rerank 后 top-1 分数低于此值时拒答 |
| `min_vector_score` | `0.6` | 拒答阈值：reranker 降级时 vector score top-1 低于此值时拒答；cosine similarity 与 rerank score 尺度不同，需独立校准 |
| `min_top1_margin` | `0.05` | 拒答阈值：top-1 与 top-2 分数差距小于此值时附加低置信度警告 |
| `max_context_tokens` | `24000` | 上下文组装 token 预算 |
| `query_rewrite_enabled` | `False` | query rewrite 开关；M4 初版默认关闭 |
| `hyde_enabled` | `False` | HyDE（Hypothetical Document Embeddings）开关；M4 初版默认关闭 |
| `min_query_length` | `2` | 过短 query 诊断阈值（字符数） |
| `parent_context_window_tokens` | `2000` | parent 超长时，命中 child 周边保留的 token 窗口 |
| `search_mode` | `vector` | 检索模式，初版固定 `vector`；M8 开启 `hybrid` |
| `reranker_required` | `True` | 生产路径中 reranker 是否为必需组件；为 `True` 时，`reranker_model` 为空或 `preflight()` 失败将阻止检索服务启动；测试环境可设为 `False` 并配合 `FakeRerankerProvider` |

所有阈值必须通过 M6 评测校准，M4 只提供初始建议值。

## 数据类型

### `RequestContext`

M4 在 `recallforge/context.py` 中定义 `RequestContext` 和对应的 `ContextVar`。M0 骨架未实际落地此类型，M4 必须自行定义。M5 的 API 入口负责创建并注入 `ContextVar`。

```python
import uuid
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    user_id: str
    department: str
    access_level: str
    request_id: uuid.UUID


current_request_context: ContextVar[RequestContext | None] = ContextVar(
    "current_request_context", default=None,
)
```

M4 不从 `RequestContext` 以外的来源获取权限字段。所有 M4 模块通过 `current_request_context.get()` 或方法参数获取 `RequestContext`。

### `RetrievalRequest`

```python
@dataclass(frozen=True)
class RetrievalRequest:
    question: str
    client_filters: dict[str, Any] = field(default_factory=dict)
    search_mode: str = "vector"
    top_k: int | None = None           # 覆盖 Settings.default_top_k
    final_top_k: int | None = None     # 覆盖 Settings.final_top_k
```

约束：

- `client_filters` 只允许白名单 key：`doc_type`、`source_uri`、`version`、`date_range`。
- `client_filters` 不得包含 `tenant_id`、`user_id`、`department`、`access_level`、`status`。
- `search_mode` 初版只接受 `vector`；传入 `hybrid` / `full_text` 时记录 warning 并降级到 `vector`（M8 实现后移除降级）。
- `embedding_model` 不允许请求级覆盖。M4 初版只支持单一 `EmbeddingProvider`，请求级覆盖会导致 query embedding 与 `VectorStoreAdapter.search()` 的 `embedding_model` 参数不一致，进而路由到错误向量列。如需多模型检索，应在 M8 通过 provider registry 实现。

### `RetrievalResult`

```python
@dataclass
class RetrievalResult:
    status: str                                    # "retrieved" | "refused" | "failed"；M5 生成答案后更新为 "success"
    question: str
    rewritten_query: str | None
    context_text: str                              # 组装后的上下文文本，供 LLM 使用
    references: list[Reference]
    refusal_reason: str | None
    hit_summary: list[HitSummary]
    selected_candidates: list[RankedCandidate]     # rerank 后选中的候选
    latencies_ms: dict[str, int]
    search_config: SearchConfig                    # 记录本次检索使用的全部配置
```

### `Reference`

```python
@dataclass(frozen=True)
class ReferenceChild:
    """同 parent 下单个命中 child 的定位信息。"""
    chunk_id: int
    chunk_key: str
    rerank_score: float
    rerank_rank: int
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class Reference:
    ref_index: int                    # [1], [2], ...
    document_id: int
    document_title: str | None
    chunk_id: int                     # 主 child chunk（rerank 分数最高）
    chunk_key: str                    # 主 child chunk key
    parent_id: int
    parent_key: str
    source_uri: str
    doc_type: str
    page_start: int | None
    page_end: int | None
    heading_path: list[str] | None
    version: int
    rerank_score: float
    rerank_rank: int
    vector_score: float
    vector_rank: int
    score_source: str
    child_chunks: list[ReferenceChild] = field(default_factory=list)  # 同 parent 下所有命中 child
```

约束：

- `chunk_id` / `chunk_key` 为同 parent 下 rerank 分数最高的主 child，用于排序和展示。
- `child_chunks` 包含同 parent 下所有命中 child（含主 child），保留完整的引用可追溯性。
- 同 parent 单 child 命中时，`child_chunks` 只有一个元素，且与主 child 字段一致。

### `HitSummary`

```python
@dataclass(frozen=True)
class HitSummary:
    chunk_id: int
    document_id: int
    parent_id: int
    chunk_key: str
    parent_key: str
    vector_score: float
    vector_rank: int
    rerank_score: float | None
    rerank_rank: int | None
    selected: bool
    refusal_filtered: bool
    score_source: str
```

### `RankedCandidate`

```python
@dataclass
class RankedCandidate:
    chunk_id: int
    document_id: int
    parent_id: int
    chunk_key: str
    parent_key: str
    content: str                      # child chunk 原文
    parent_content: str | None        # parent 原文或截断后的 parent 上下文
    vector_score: float
    vector_rank: int
    rerank_score: float
    rerank_rank: int
    score_source: str                 # "rerank"
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    source_uri: str
    doc_type: str
    version: int
    document_title: str | None
    ref_index: int | None = None      # 组装后填入
```

### `SearchConfig`

```python
@dataclass(frozen=True)
class SearchConfig:
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    reranker_provider: str | None
    reranker_model: str | None
    search_mode: str
    top_k: int
    final_top_k: int
    min_rerank_score: float
    min_top1_margin: float
    max_context_tokens: int
    query_rewrite_enabled: bool
    hyde_enabled: bool
```

## Query Understanding

### 模块职责

`recallforge/retrieval/query_understanding.py` 负责对用户原始 query 进行预处理和质量诊断，输出可用于 embedding 的 query 文本和诊断信息。

```python
@dataclass
class QueryAnalysis:
    original_query: str
    effective_query: str                    # 最终用于 embedding 的 query
    rewritten_query: str | None             # query rewrite 结果（未启用时为 None）
    rejected: bool                          # 是否被拒绝
    rejection_reason: str | None
    warnings: list[str]
    multi_intent_detected: bool             # 多意图检测结果，M4 只检测不拆分
    intent_count: int


class QueryUnderstanding:
    def __init__(self, settings: Settings) -> None: ...

    def analyze(self, question: str) -> QueryAnalysis: ...
```

### 规则

1. **空 query 拒绝**：`question` 为空或 strip 后为空 → `rejected=True`，`rejection_reason="empty_query"`。
2. **过短 query 诊断**：strip 后字符数 < `Settings.min_query_length`（默认 2）→ `rejected=True`，`rejection_reason="query_too_short"`。
3. **query rewrite**（可选）：`Settings.query_rewrite_enabled=True` 时，对 query 进行改写以提升召回。M4 初版默认关闭。开启时改写逻辑为：
   - 去除无意义前缀（"请问"、"帮我查"、"你知道" 等）。
   - 补全省略的主语或限定词（需要 LLM，M4 预留接口但不实现 LLM 调用；初版只做规则化清理）。
   - 改写结果写入 `QueryAnalysis.rewritten_query`，`effective_query` 使用改写后的文本。
4. **HyDE**（可选）：`Settings.hyde_enabled=True` 时，使用 LLM 生成假设性文档片段作为 embedding 输入。M4 初版默认关闭，仅预留开关和接口。
5. **多意图检测**：通过简单规则检测多意图 query（包含"和"、"以及"、"同时"等连接词，或包含问号分隔的多个子句）。M4 只检测并记录 `multi_intent_detected=True`、`intent_count`，不拆分。拆分接口留给后续 milestone。

### 与链路的关系

- `QueryAnalysis.rejected=True` 时，`RetrievalService` 直接返回 `RetrievalResult(status="refused")`，不进入 vector recall。
- `QueryAnalysis.effective_query` 传入 `EmbeddingProvider.embed_query()` 生成 query embedding。
- `QueryAnalysis.warnings` 和 `multi_intent_detected` 写入 `rag_query_logs.metadata`。

## 服务端 Metadata Filter 构造

### 模块职责

`recallforge/retrieval/filter_builder.py` 负责把 `RequestContext` 的权限字段和客户端传入的业务 filters 合并为 `VectorSearchFilter`，同时校验客户端 filters 不包含越权字段。

```python
CLIENT_FILTER_WHITELIST = frozenset({
    "doc_type", "source_uri", "version", "date_range",
})

FORBIDDEN_CLIENT_KEYS = frozenset({
    "tenant_id", "user_id", "department", "access_level", "status",
})


class FilterBuilderError(RuntimeError):
    """Raised when client filters contain forbidden keys."""


class FilterBuilder:
    def __init__(self, settings: Settings) -> None: ...

    def build(
        self,
        ctx: RequestContext,
        client_filters: dict[str, Any],
    ) -> VectorSearchFilter: ...
```

### 规则

1. **白名单校验**：`client_filters` 中的 key 必须在 `CLIENT_FILTER_WHITELIST` 中。发现 `FORBIDDEN_CLIENT_KEYS` 中的 key 时，直接抛 `FilterBuilderError` 并记录审计日志钩子，不执行任何检索。发现其他未知 key 时同样拒绝。
2. **`tenant_id` 强制注入**：从 `ctx.tenant_id` 获取，不接受客户端覆盖。
3. **`department` 展开**：从 `ctx.department` 获取当前用户部门，展开为允许的部门集合。初版策略：
   - `department="global"` 的资料对所有部门可见。
   - 用户只能看到自己部门和 `global` 的资料。
   - 展开结果：`[ctx.department, "global"]`（如果用户部门已经是 `global`，则不重复）。
   - 传入 `VectorSearchFilter.department` 为列表。
4. **`access_level` 展开**：从 `ctx.access_level` 获取当前用户访问级别，展开为允许的级别集合。初版策略采用层级包含：
   - `restricted` 可看 `public` + `internal` + `confidential` + `restricted`。
   - `confidential` 可看 `public` + `internal` + `confidential`。
   - `internal` 可看 `public` + `internal`。
   - `public` 只能看 `public`。
   - 展开结果传入 `VectorSearchFilter.access_level` 为列表。
5. **`status` 强制 `active`**：默认只检索 `active` 状态的 chunks。跨版本召回必须由服务端显式允许（M4 初版不开放）。
6. **`doc_type`**：从 `client_filters` 透传，可选。
7. **`source_uri`**：从 `client_filters` 透传，可选。
8. **`version`**：从 `client_filters` 透传，可选。默认不填，检索所有 active 版本（同一 `(tenant_id, source_uri)` 下只有一个 active 版本）。
9. **`date_range`**：M4 记录到 `rag_query_logs.client_filters` 但暂不支持 date range 过滤（`rag_chunks` 没有独立的文档日期列）。预留接口，M8 可通过 metadata JSONB 或新增列实现。

### access_level 层级矩阵

| 用户 access_level | 可见 chunk access_level |
| --- | --- |
| `public` | `["public"]` |
| `internal` | `["public", "internal"]` |
| `confidential` | `["public", "internal", "confidential"]` |
| `restricted` | `["public", "internal", "confidential", "restricted"]` |

此矩阵必须在代码中显式定义（不能硬编码在字符串比较里），便于 M6 权限泄漏评测覆盖全部组合。

**语义注解**：本项目 `restricted` 表示最高密级（等同于 Top Secret / 绝密），而非"受限访问"。`public` 为最低密级。层级方向为 `public < internal < confidential < restricted`，用户 `access_level` 越高可见范围越大。

## RerankerProvider

### Protocol

```python
@dataclass(frozen=True)
class RerankedCandidate:
    chunk_id: int
    rerank_score: float
    rerank_rank: int
    original_rank: int
    original_score: float


class RerankerProvider(Protocol):
    provider: str
    name: str
    max_candidates: int

    async def rerank(
        self,
        query: str,
        candidates: Sequence[RerankerInput],
        top_k: int | None = None,
    ) -> list[RerankedCandidate]: ...

    async def preflight(self) -> None: ...


@dataclass(frozen=True)
class RerankerInput:
    chunk_id: int
    content: str
    original_rank: int
    original_score: float
```

语义约束：

- `provider` 是厂商标识，例如 `dashscope`。
- `name` 是模型名，例如 `qwen3-rerank`。
- `max_candidates` 是单次 rerank 最大候选数，`qwen3-rerank` 为 `500`。
- `rerank()` 接收 query 和 child chunk 文本候选列表，返回按 rerank 分数降序的 `RerankedCandidate` 列表。
- `top_k` 参数控制 reranker 返回的最大结果数，可选。
- provider 必须在返回前校验结果数量，但不保证所有候选都有 rerank 分数（provider 可能因为 token 限制跳过部分候选）。
- reranker 只接收 child chunk 原文，**不**接收 parent chunk 全文。Parent expansion 在 rerank 后执行。

### DashScopeRerankerProvider

`DashScopeRerankerProvider` 是 M4 默认 reranker，对应阿里百炼 / DashScope `qwen3-rerank`。

职责：

- 读取 `Settings.reranker_model`、`reranker_provider`、API key、endpoint、timeout、retry 配置。
- 使用 DashScope 原生 reranker 接口。
- 对超过 `max_candidates`（500）的候选列表，截断到前 500 个（按 vector score 降序），并在返回结果中标注截断信息。
- API key 缺失或 region 不可用时 `preflight()` 抛类型化异常。
- 记录 provider、model、candidate_count、latency_ms、retry_count。

推荐请求封装：

```python
async def rerank(
    self,
    query: str,
    candidates: Sequence[RerankerInput],
    top_k: int | None = None,
) -> list[RerankedCandidate]:
    payload = {
        "model": self.name,
        "input": {
            "query": query,
            "documents": [c.content for c in candidates],
        },
        "parameters": {
            "top_n": top_k or len(candidates),
        },
    }
    ...
```

错误处理：

- DashScope 返回 429 时，按 `reranker_max_retries` 指数退避重试。
- DashScope 返回非 429 错误时，抛 `RerankerProviderError`，不静默降级。
- API key 缺失时抛 `RerankerConfigurationError`。

### Reranker Registry

```python
def reranker_provider_from_settings(settings: Settings) -> RerankerProvider | None:
    if not settings.reranker_model:
        return None
    ...
```

当 `reranker_model` 为空时返回 `None`。Reranker 为 `None` 时的行为取决于 `Settings.reranker_required`：

- `reranker_required=True`（生产默认）：`RetrievalService` 启动时校验 reranker 不为 `None`，为 `None` 则抛 `RerankerConfigurationError`，阻止检索服务启动。这确保生产路径满足 AGENTS.md 不可破坏约束第 6 条。
- `reranker_required=False`（测试环境）：允许 `None`，`RetrievalService` 在 reranker 为 `None` 时按 vector score 降序截取 top `final_top_k`，并在 query log 中记录 `reranker_model=None`、`score_source="vector"`。

Reranker 运行时失败（非临时错误重试耗尽后）时，允许降级到 vector score 排序，但必须在 query log 的 warnings 中记录 `reranker_fallback=True`，并使用 `min_vector_score` 替代 `min_rerank_score` 做拒答判定。

## Parent Expansion

### 模块职责

`recallforge/retrieval/parent_expansion.py` 负责在 rerank 后的 top candidates 上执行 parent chunk 回查和 small-to-big 上下文补全。

```python
@dataclass
class ExpandedCandidate:
    chunk_id: int
    document_id: int
    parent_id: int
    chunk_key: str
    parent_key: str
    child_content: str
    parent_content: str | None            # 完整或截断后的 parent 内容
    parent_token_count: int | None
    parent_truncated: bool
    heading_path: list[str] | None
    page_start: int | None
    page_end: int | None
    source_uri: str
    doc_type: str
    version: int
    rerank_score: float
    rerank_rank: int
    vector_score: float
    vector_rank: int
    score_source: str


class ParentExpander:
    def __init__(
        self,
        parent_repo: ParentChunkRepository,
        chunk_repo: ChunkRepository,
        settings: Settings,
    ) -> None: ...

    async def expand(
        self,
        candidates: Sequence[RankedCandidate],
        tenant_id: str,
    ) -> list[ExpandedCandidate]: ...
```

### 规则

1. **批量回查**：收集 candidates 中所有唯一 `parent_id`，通过 `ParentChunkRepository.get_by_ids(tenant_id, parent_ids)` 批量查询 parent chunks。
2. **同 parent 合并**：同一 `parent_id` 下多个 child chunk 命中时，parent 内容只保留一份。合并后的候选组以 rerank 分数最高的 child 作为代表排序。
3. **parent 超长截断**：当 parent `token_count` 超过 `Settings.parent_context_window_tokens * 2` 时，执行截断：
   - 定位命中 child chunk 在 parent 原文中的位置（通过子串匹配或 `chunk_index`）。
   - 保留命中 child 前后各 `parent_context_window_tokens` 个 token 的窗口。
   - 如果同一 parent 下有多个命中 child，合并窗口，避免重叠。
   - 截断后的 parent 内容前后附加 `[...]` 标记。
   - 设置 `parent_truncated=True`。
4. **parent 不存在或已删除**：如果 parent chunk 状态不是 `active`，仍可返回 child chunk 但 `parent_content=None`，并在 warnings 中记录。
5. **标题路径保留**：parent 的 `heading_path` 始终保留在 `ExpandedCandidate` 中，即使 parent 内容被截断。

### 与上下文预算的关系

- parent expansion 产出 `ExpandedCandidate`，交给 `ContextAssembler` 在 token 预算内选择和截断。
- parent expansion 本身不做 token 预算控制，只做单个 parent 的窗口截断。
- 最终是否保留 parent 内容由 `ContextAssembler` 决定。

## 拒答判定

### 模块职责

`recallforge/retrieval/refusal.py` 负责根据 rerank 结果判断是否有足够证据回答问题。

```python
@dataclass(frozen=True)
class RefusalDecision:
    should_refuse: bool
    reason: str | None
    confidence: str                       # "high" | "medium" | "low" | "none"
    top1_score: float | None
    top1_margin: float | None             # top1 - top2 差值
    candidates_above_threshold: int


class RefusalJudge:
    def __init__(self, settings: Settings) -> None: ...

    def judge(self, reranked: Sequence[RerankedCandidate]) -> RefusalDecision: ...
```

### 规则

1. **无候选**：rerank 后结果为空 → `should_refuse=True`，`reason="no_candidates"`，`confidence="none"`。
2. **top-1 分数低于阈值**：当 `score_source="rerank"` 时，`reranked[0].rerank_score < min_rerank_score`；当 `score_source="vector"` 时，`reranked[0].rerank_score < min_vector_score` → `should_refuse=True`，`reason="low_confidence"`，`confidence="low"`。
3. **top-1 margin 低**：`reranked[0].rerank_score - reranked[1].rerank_score < min_top1_margin`（当有 ≥ 2 个候选时）→ 不强制拒答，但 `confidence="medium"` 并在 warnings 中记录低 margin 告警。
4. **多候选均达标**：top-1 分数超过对应阈值且 margin 足够 → `should_refuse=False`，`confidence="high"`。
5. 当 reranker 运行时降级时，使用 vector score 替代 rerank score 做判定，`score_source="vector"`，并使用 `min_vector_score` 替代 `min_rerank_score`。两者尺度不同（cosine similarity 通常 0.5-0.9，rerank score 尺度因模型而异），必须独立校准。
6. 所有阈值必须通过 M6 评测校准，M4 提供初始建议值。实际阈值在评测报告中记录。

## 上下文组装

### 模块职责

`recallforge/retrieval/context_assembly.py` 负责把 rerank + parent expansion 后的 candidates 在 token 预算内组装为 LLM 可消费的上下文文本。

```python
@dataclass
class AssembledContext:
    context_text: str
    total_tokens: int
    references: list[Reference]
    selected_candidates: list[RankedCandidate]
    truncation_applied: bool
    candidates_included: int
    candidates_dropped: int


class ContextAssembler:
    def __init__(self, settings: Settings) -> None: ...

    def assemble(
        self,
        expanded: Sequence[ExpandedCandidate],
        refusal: RefusalDecision,
    ) -> AssembledContext: ...
```

### 规则

1. **token 预算**：`max_context_tokens` 初版默认 `24000`，可配置，且不得超过当前 LLM 的安全上下文预算。
2. **优先级排序**：按 rerank 分数降序排列。分数越高的证据优先保留。
3. **同 parent 合并**：同一 parent 下多个 child 命中时，合并为一个上下文块，避免重复的 parent 内容。合并后的上下文块以最高 rerank 分数的 child 代表排序。
4. **截断策略**：
   - 从最高分证据开始，逐个加入上下文。
   - 每个证据块包含：heading path（作为标题）、parent 内容（如果有）、child 内容（高亮标注为核心证据）。
   - 加入当前证据块后 token 数超过预算时，尝试截断 parent 到 child 周边窗口；如果仍超预算则跳过当前证据。
   - 记录 `candidates_dropped` 和 `truncation_applied`。
5. **上下文格式**：

```text
[证据 1]
来源: {source_uri} | 页码: {page_start}-{page_end} | 类型: {doc_type}
标题路径: {heading_path}

{parent_content (if available)}

**核心段落**
{child_content}

---

[证据 2]
...
```

6. **references 编号**：`ContextAssembler` 在组装过程中调用 `ReferenceBuilder` 获取每个选中证据块的稳定编号 `[1]`、`[2]`、...。编号由 `ReferenceBuilder` 统一分配（详见"References 组装"章节），`ContextAssembler` 只消费编号结果，不自行分配。编号顺序与上下文中的出现顺序一致（即 rerank 分数降序）。

### Token 估算

M4 使用 `recallforge/chunking/tokenizer.py` 中已有的 tokenizer 做 token 估算。如果 tokenizer 不可用，fallback 到字符比例估算，根据文本内容自适应：

- 如果文本中 CJK 统一汉字（U+4E00-U+9FFF）占比 > 30%，使用 `len(text) / 1.5`（中文约 1.5-2 字符 / token）。
- 否则使用 `len(text) / 4`（英文约 4 字符 / token）。
- 不再使用 `len(text) / 3` 的全局折中——该系数对中文严重低估（1.5-2 字符/token 的文本被按 3 字符/token 估算），会导致上下文实际 token 数远超预算。

## References 组装

### 模块职责

`recallforge/retrieval/references.py` 是引用编号的唯一分配者，负责把 `ExpandedCandidate` 转换为结构化 `Reference` 列表。`ContextAssembler` 通过调用 `ReferenceBuilder` 获取编号，不自行分配。

```python
class ReferenceBuilder:
    def build(
        self,
        selected: Sequence[ExpandedCandidate],
        document_titles: dict[int, str | None] | None = None,
    ) -> list[Reference]: ...
```

`document_titles` 为 `{document_id: title}` 映射，由 `RetrievalService` 在调用 `ReferenceBuilder.build()` 前从 `DocumentRepository` 批量获取。

### 规则

1. 引用编号从 `1` 开始，按 rerank 分数降序分配。`ReferenceBuilder` 是编号的唯一来源，`ContextAssembler` 不得自行分配编号。
2. 同一 `parent_id` 下多个 child chunk 命中时，合并为一个引用条目：主 child 为 rerank 分数最高者，`child_chunks` 列表保留所有命中 child 的 `chunk_id`、`chunk_key`、`rerank_score`、`rerank_rank`、`page_start`、`page_end`。
3. 每个 `Reference` 必须包含 `document_id`、`chunk_id`、`parent_id`、`parent_key`、`source_uri`、`doc_type`、`page_start`、`page_end`、`heading_path`、`version`、`rerank_score`、`vector_score`、`child_chunks`。
4. `document_title` 从 `document_titles` 映射填充。如果映射中不存在对应 `document_id`，`document_title=None`。
5. references 使用结构化字段输出，**不**从答案文本中反向解析引用。
6. M5 生成答案时，Agent instructions 中约束模型只能使用 `ReferenceBuilder` 分配的 `[1]`、`[2]` 编号引用；不允许模型自行发明引用。

## Hybrid Search 钩子

M4 不完整实现 hybrid search，但必须为 M8 预留可调用的钩子：

1. `RetrievalRequest.search_mode` 接受 `vector`（M4 默认）、`hybrid`、`full_text`。
2. 当 `search_mode="hybrid"` 时，M4 记录 warning 并降级到 `vector`。
3. `ChunkRepository.search_full_text()` 已在 M1 实现，M4 可选择在 `RetrievalService` 中预留调用入口（例如一个 `_full_text_recall()` 方法），但 M4 不实现 RRF 融合。
4. `VectorSearchHit.score_source` 在 M4 保持 `vector`。M8 引入 hybrid 后新增 `bm25`、`hybrid`、`rrf`。
5. M4 的 `HitSummary` 和 query log 已经包含 `score_source` 字段，M8 可直接复用。

## RetrievalService 编排

### 全链路流程

`recallforge/retrieval/retrieval_service.py` 是 M4 的核心编排模块，串起检索全流程。

```python
class RetrievalService:
    def __init__(
        self,
        settings: Settings,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStoreAdapter,
        reranker: RerankerProvider | None,
        session: AsyncSession,
        parent_repo_type: type[ParentChunkRepository],
        chunk_repo_type: type[ChunkRepository],
        query_log_repo_type: type[QueryLogRepository],
        doc_repo_type: type[DocumentRepository],
    ) -> None:
        # 所有 repository 共享同一 session，确保单次检索在同一事务快照内完成
        self._settings = settings
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._reranker = reranker
        self._session = session
        self._parent_repo = parent_repo_type(session)
        self._chunk_repo = chunk_repo_type(session)
        self._query_log_repo = query_log_repo_type(session)
        self._doc_repo = doc_repo_type(session)
        # 启动校验：生产路径 reranker 必须已配置
        if settings.reranker_required and reranker is None:
            raise RerankerConfigurationError(
                "reranker_required=True but reranker_model is empty; "
                "configure reranker_model or set reranker_required=False for testing"
            )

    async def retrieve(
        self,
        request: RetrievalRequest,
        ctx: RequestContext,
    ) -> RetrievalResult: ...
```

构造说明：

- 所有 repository 共享注入的 `AsyncSession`，确保单次 `retrieve()` 调用中读到的数据在同一事务快照内，避免多 session 不一致问题（例如 chunk 刚被删除但 parent 还是旧状态）。
- 注入 repository **类型**而非实例，由 `RetrievalService` 用共享 session 构造实例。这与 M3 `EmbeddingBackfillService` 使用 `session_factory` 的模式一致。
- 调用方负责 session 的创建、提交和关闭；推荐使用 async context manager 管理 session 生命周期。

`retrieve()` 内部流程：

1. **Query Understanding**：`QueryUnderstanding.analyze(request.question)`。`rejected=True` → 直接写 query log（`status="refused"`），返回 `RetrievalResult(status="refused")`。
2. **Filter 构造**：`FilterBuilder.build(ctx, request.client_filters)`。`FilterBuilderError` → 写 query log（`status="failed"`），返回 `RetrievalResult(status="failed")`。
3. **Query Embedding**：`EmbeddingProvider.embed_query(analysis.effective_query)`。记录 embedding latency。
4. **Vector Recall**：`VectorStoreAdapter.search(query_embedding, embedding_model, filters, top_k, search_mode="vector")`。记录 search latency。
5. **读取 child chunk 原文**：vector recall 只返回 `VectorSearchHit`，不含原文。通过 `ChunkRepository.get_by_ids(tenant_id, chunk_ids)` 批量读取命中 child chunks 的原文。
6. **Reranker**：如果 reranker 已配置，构造 `RerankerInput` 列表，调用 `RerankerProvider.rerank(effective_query, candidates, final_top_k)`。记录 rerank latency。如果 reranker 运行时失败（非临时错误重试耗尽后），降级到 vector score 降序截取 top `final_top_k`，记录 `reranker_fallback=True` 和 `score_source="vector"`。
7. **拒答判定**：`RefusalJudge.judge(reranked_candidates, score_source)`。当 `score_source="rerank"` 时使用 `min_rerank_score`，当 `score_source="vector"` 时使用 `min_vector_score`。`should_refuse=True` → 写 query log（`status="refused"`），返回 `RetrievalResult(status="refused", refusal_reason=...)`。
8. **Parent Expansion**：`ParentExpander.expand(top_candidates, ctx.tenant_id)`。记录 parent lookup latency。
9. **获取 document_title**：收集 expanded candidates 中所有唯一 `document_id`，通过 `DocumentRepository.get_by_ids(tenant_id, document_ids)` 批量查询文档标题，构造 `{document_id: title}` 映射。
10. **上下文组装**：`ContextAssembler.assemble(expanded, refusal_decision, references)`。`ContextAssembler` 从 `ReferenceBuilder` 获取编号，不自行分配。
11. **References 组装**：`ReferenceBuilder.build(selected_candidates, document_titles)`。统一分配引用编号 `[1]`、`[2]`、...。
12. **Query Log 写入**：`QueryLogRepository.create(QueryLogCreate(...))`，记录完整决策链路。检索成功时 `status="retrieved"`、`answer=None`（M5 生成答案后更新为 `"success"`）。
13. **返回**：`RetrievalResult(status="retrieved", ...)`。

### 错误处理

| 阶段 | 错误场景 | 行为 |
| --- | --- | --- |
| Query Understanding | 空 query / 过短 query | `status="refused"`，`refusal_reason` 描述原因 |
| Filter 构造 | 客户端传入越权 filter | `status="failed"`，`error_message` 描述越权字段；审计日志钩子 |
| Query Embedding | provider 不可用 / API 失败 | `status="failed"`，`error_message` 记录 provider 错误（不含 API key） |
| Vector Recall | `VectorStoreAdapter.search()` 失败 | `status="failed"` |
| 读取 chunk 原文 | 部分 chunk 已被删除 | 过滤掉已删除 chunk，继续处理剩余候选 |
| Reranker | reranker 运行时失败（重试耗尽） | 降级到 vector score 排序，`score_source="vector"`，warnings 包含 `reranker_fallback=True`；使用 `min_vector_score` 替代 `min_rerank_score` |
| Parent Expansion | parent 不存在或已删除 | `parent_content=None`，写入 warnings，不阻塞 |
| 获取 document_title | document 不存在或已删除 | `document_title=None`，不阻塞 |
| 上下文组装 | 所有候选 token 超预算 | `status="refused"`，`refusal_reason="context_budget_exceeded"` |

关键原则：

- embedding provider 失败和 vector store 失败视为硬错误，不降级。
- reranker 运行时失败视为可降级错误，降级到 vector score 排序并记录 `reranker_fallback=True`；降级后使用 `min_vector_score` 做拒答判定。生产路径中 reranker 未配置时，`RetrievalService` 构造即报错，不允许降级启动。
- parent 缺失和 document_title 缺失视为软错误，不阻塞检索。
- 所有错误路径都必须写 query log，确保可复盘。

### Latency 记录

`latencies_ms` 字典记录每个阶段的耗时：

```python
{
    "query_understanding_ms": 1,
    "filter_build_ms": 0,
    "embedding_ms": 120,
    "vector_search_ms": 45,
    "chunk_read_ms": 15,
    "rerank_ms": 200,
    "parent_expansion_ms": 30,
    "doc_title_read_ms": 5,
    "context_assembly_ms": 5,
    "total_ms": 421,
}
```

## 查询日志

### 写入时机

`RetrievalService.retrieve()` 在返回前必须写入 `rag_query_logs`。无论 `status` 是 `retrieved`、`refused` 还是 `failed`，都必须写入。

M4 引入 `retrieved` 状态，表示检索成功但答案尚未生成。生命周期为：M4 写入 `retrieved` → M5 Agent 生成答案后更新为 `success`。这解决 M1 `ck_rag_query_logs_status_payload` CHECK 约束中 `status='success' AND answer IS NOT NULL` 与 M4 写入 `answer=NULL` 的冲突。

迁移要求：

- `QUERY_STATUSES` 从 `("success", "refused", "failed")` 扩展为 `("success", "retrieved", "refused", "failed")`。
- `ck_rag_query_logs_status_payload` 调整为：
  ```sql
  (status = 'success' AND answer IS NOT NULL)
  OR (status = 'retrieved' AND answer IS NULL)
  OR (status = 'refused' AND refusal_reason IS NOT NULL)
  OR (status = 'failed' AND error_message IS NOT NULL)
  ```

### 字段映射

| `rag_query_logs` 字段 | M4 写入来源 |
| --- | --- |
| `request_id` | `ctx.request_id` |
| `tenant_id` | `ctx.tenant_id` |
| `user_id` | `ctx.user_id` |
| `department` | `ctx.department` |
| `access_level` | `ctx.access_level` |
| `question` | `request.question` |
| `rewritten_query` | `analysis.rewritten_query` |
| `filters` | 服务端构造后的最终 `VectorSearchFilter` 序列化 |
| `client_filters` | `request.client_filters` 原样记录 |
| `search_mode` | `request.search_mode`（M4 初版 `vector`） |
| `embedding_provider` | `provider.provider` |
| `embedding_model` | `provider.name`（显式传入的模型） |
| `embedding_dim` | `provider.dim` |
| `reranker_provider` | `reranker.provider` 或 `None` |
| `reranker_model` | `reranker.name` 或 `None` |
| `top_k` | 实际使用的 vector recall top_k |
| `final_top_k` | 实际使用的 final_top_k |
| `min_rerank_score` | 当前阈值 |
| `min_top1_margin` | 当前阈值 |
| `max_context_tokens` | 当前预算 |
| `hit_summary` | `[HitSummary]` 序列化 |
| `selected_references` | `[Reference]` 序列化 |
| `answer` | `None`（M4 写入 `retrieved` 状态时 answer 为 NULL；M5 Agent 生成答案后通过 `QueryLogRepository.update_answer()` 回写，状态更新为 `success`） |
| `refusal_reason` | `refusal_decision.reason` |
| `latencies_ms` | 各阶段耗时 |
| `metadata` | `query_analysis.warnings`、`multi_intent_detected`、`reranker_fallback`、`parent_missing`、`doc_title_missing` 等诊断信息 |
| `status` | `retrieved` / `refused` / `failed` |
| `error_message` | 失败时的错误摘要（不含 API key、不含跨租户内容） |

### 日志安全约束

- query log 不得记录 chunk 原文全文。`hit_summary` 只记录 chunk 定位字段和分数。
- query log 不得泄露 API key 或其他 secret。
- query log 中 `filters` 字段序列化后不包含 SQL 片段。
- 跨租户内容不得通过 `error_message` 或 `metadata` 泄露。

### 审计日志钩子

M4 定义最小审计日志接口，用于记录越权访问尝试等安全事件：

```python
from typing import Callable, Any

# 审计日志钩子：接收事件名称和上下文字典
AuditLogHook = Callable[[str, dict[str, Any]], None]


def default_audit_log_hook(event: str, context: dict[str, Any]) -> None:
    """默认实现：写结构化日志。"""
    import logging
    logging.getLogger("recallforge.audit").warning(
        "audit_event=%s context=%s", event, context,
    )
```

审计事件清单（M4 初版）：

| 事件 | 触发条件 | context 字段 |
| --- | --- | --- |
| `client_filter_forbidden` | `client_filters` 包含 `FORBIDDEN_CLIENT_KEYS` | `forbidden_keys`、`tenant_id`、`user_id`、`request_id` |
| `client_filter_unknown` | `client_filters` 包含非白名单 key | `unknown_keys`、`tenant_id`、`user_id`、`request_id` |

审计日志钩子在 `Settings` 中可配置，初版默认使用 `default_audit_log_hook`。钩子不得抛异常影响主流程。

## 与 M3 / M5 / M6 的边界

### M4 使用 M3 提供的能力

| M3 能力 | M4 使用方式 |
| --- | --- |
| `EmbeddingProvider.embed_query(question)` | 生成 query embedding，`text_type=query` |
| `VectorStoreAdapter.search()` | vector recall，显式传入 `embedding_model` 和 `VectorSearchFilter` |
| `VectorSearchHit` | 提取 chunk 定位字段、vector score、score_source |
| `EmbeddingColumnRegistry` | 维度校验（间接，通过 provider dim） |
| `VectorSearchFilter` | 结构化 filter 传入 |

### M4 留给 M5 的接口

- `RetrievalService.retrieve(request, ctx) -> RetrievalResult` 是 M5 受控 Agno Tool `search_internal_kb` 的核心实现入口。
- M5 Tool 实现内部从 `ContextVar` 读取 `RequestContext`，构造 `RetrievalRequest`，调用 `RetrievalService.retrieve()`。
- M5 不应在 Tool 内直接调用 `VectorStoreAdapter.search()` 或 `EmbeddingProvider.embed_query()`。
- `RetrievalResult.context_text` 是 M5 传给 LLM 的上下文内容。
- `RetrievalResult.references` 是 M5 组装回答时的引用来源。
- M5 在 Agent 生成答案后，通过 `QueryLogRepository.update_answer(request_id, tenant_id, answer)` 回写 `answer` 字段，并将 `status` 从 `retrieved` 更新为 `success`。M4 需在 `QueryLogRepository` 中新增此方法。

### M4 留给 M6 的接口

- `RetrievalResult.hit_summary` 包含每个候选的 vector score、rerank score、是否被选中、是否被拒答过滤，M6 可直接用于计算 Recall@K、MRR。
- `RetrievalResult.references` 包含引用的 document、chunk、parent、page、source，M6 可用于计算 CitationAccuracy。
- `RetrievalResult.refusal_reason` 和 `status` 可用于计算 RefusalAccuracy。
- `SearchConfig` 记录当前检索的全部配置，M6 评测报告可直接引用。
- query log 中记录的 `hit_summary`、`selected_references`、`latencies_ms`、`metadata` 可用于 M6 召回失败归因。

### M4 不提供给 Agent 的能力

- M4 不暴露 `VectorStoreAdapter` 或 `PgVectorStore` 给 Agno Agent。
- Agent 不允许直接调用 `RetrievalService` 的内部方法（如 `_vector_recall()`、`_rerank()`）。
- M5 的受控 Tool 是 Agent 与检索链路之间的唯一桥梁。

## 测试策略

### 单元测试 `tests/test_query_understanding.py`

- 空字符串 → `rejected=True`，`rejection_reason="empty_query"`。
- 纯空白字符串 → `rejected=True`，`rejection_reason="empty_query"`。
- 单字符 query（`min_query_length=2`）→ `rejected=True`，`rejection_reason="query_too_short"`。
- 正常 query → `rejected=False`，`effective_query` 等于 strip 后的 query。
- `query_rewrite_enabled=False` 时 `rewritten_query=None`。
- 包含多意图连接词 → `multi_intent_detected=True`，`intent_count >= 2`。
- 纯标点符号 query → `rejected=True`。

### 单元测试 `tests/test_filter_builder.py`

- `client_filters` 包含 `tenant_id` → 抛 `FilterBuilderError`。
- `client_filters` 包含 `user_id` → 抛 `FilterBuilderError`。
- `client_filters` 包含 `department` → 抛 `FilterBuilderError`。
- `client_filters` 包含 `access_level` → 抛 `FilterBuilderError`。
- `client_filters` 包含 `status` → 抛 `FilterBuilderError`。
- `client_filters` 包含未知 key → 抛 `FilterBuilderError`。
- 合法 `client_filters={"doc_type": "pdf"}` → `VectorSearchFilter.doc_type="pdf"`。
- `ctx.access_level="internal"` → `VectorSearchFilter.access_level=["public", "internal"]`。
- `ctx.access_level="restricted"` → `VectorSearchFilter.access_level=["public", "internal", "confidential", "restricted"]`。
- `ctx.department="engineering"` → `VectorSearchFilter.department=["engineering", "global"]`。
- `ctx.department="global"` → `VectorSearchFilter.department=["global"]`（不重复）。
- `VectorSearchFilter.tenant_id` 始终等于 `ctx.tenant_id`。
- `VectorSearchFilter.status` 默认 `active`。

### 单元测试 `tests/test_reranker_provider.py`

- fake reranker 满足 `RerankerProvider` protocol。
- `DashScopeRerankerProvider.rerank()` mock HTTP 返回，断言返回按分数降序。
- 候选数超过 `max_candidates` 时截断到前 500 个。
- API key 缺失时 `preflight()` 抛 `RerankerConfigurationError`。
- DashScope 返回 429 时重试；超过 `max_retries` 后抛 `RerankerProviderError`。
- 空候选列表 → 返回空列表。

### 单元测试 `tests/test_refusal.py`

- 空候选 → `should_refuse=True`，`reason="no_candidates"`。
- `score_source="rerank"` 时 `top1_score < min_rerank_score` → `should_refuse=True`，`reason="low_confidence"`。
- `score_source="vector"` 时 `top1_score < min_vector_score` → `should_refuse=True`，`reason="low_confidence"`。
- `top1_score >= min_rerank_score` 且 margin 足够 → `should_refuse=False`，`confidence="high"`。
- `top1_score >= min_rerank_score` 但 margin < `min_top1_margin` → `should_refuse=False`，`confidence="medium"`。
- 只有 1 个候选且 score 达标 → `should_refuse=False`（无 margin 可计算）。

### 单元测试 `tests/test_parent_expansion.py`

- 3 个命中来自 2 个不同 parent → 批量查询 2 个 parent，每个 child 关联到正确 parent。
- 2 个命中来自同一 parent → parent 内容只出现一份。
- parent `token_count` 超长 → `parent_truncated=True`，内容包含 `[...]`。
- parent 不存在 → `parent_content=None`，记录 warning。

### 单元测试 `tests/test_context_assembly.py`

- 3 个候选，total tokens 在预算内 → 全部包含，`candidates_dropped=0`。
- 5 个候选，token 超预算 → 按 rerank 分数降序保留前 N 个，`candidates_dropped > 0`。
- 同 parent 多个 child → parent 内容合并，不重复。
- references 编号从 `[1]` 开始，顺序与 rerank 分数降序一致。
- 空候选 → 空 context，`candidates_included=0`。

### 单元测试 `tests/test_references.py`

- 3 个候选 → 3 个 references，编号 `[1]`、`[2]`、`[3]`。
- reference 包含 `document_id`、`chunk_id`、`parent_id`、`source_uri`、`page_start`、`page_end`、`child_chunks`。
- 同 parent 下 2 个 child → 合并为 1 个 reference，以最高分 child 为主，`child_chunks` 包含 2 个 `ReferenceChild`。
- 单 child 命中 → `child_chunks` 只有 1 个元素，且与主 `chunk_id`/`chunk_key` 一致。
- `document_titles` 映射传入时 → `document_title` 被填充；未传入或 document_id 不存在 → `document_title=None`。

### 单元测试 `tests/test_retrieval_service.py`

使用 fake/mock 注入所有依赖：

- **happy path**：构造 3 个 `VectorSearchHit`，mock reranker 返回排序结果，mock parent repo 返回 parent → `status="retrieved"`，`references` 非空，query log 写入一条 `status="retrieved"` 记录。
- **空 query**：`status="refused"`，query log 写入 `refusal_reason="empty_query"`。
- **越权 filter**：`client_filters={"tenant_id": "evil"}` → `status="failed"`，审计日志钩子被调用。
- **无召回**：vector search 返回空列表 → `status="refused"`，`refusal_reason="no_candidates"`。
- **reranker 失败降级**：mock reranker 抛异常 → 降级到 vector score 排序，`status="retrieved"`，warnings 包含 `reranker_fallback=True`，拒答判定使用 `min_vector_score`。
- **低置信度拒答**：mock reranker 返回全部低分 → `status="refused"`，`refusal_reason="low_confidence"`。
- **embedding 失败**：mock provider 抛异常 → `status="failed"`，error_message 非空。
- **权限隔离**：构造两个 tenant 的 chunks，调用时传入 tenant A → 只返回 tenant A 的结果（由 `VectorSearchFilter.tenant_id` 保证）。
- **reranker_required=True 且 reranker=None**：构造时抛 `RerankerConfigurationError`。
- **document_title 获取**：mock doc_repo 返回标题 → `references[0].document_title` 非空。

### 集成测试 `tests/integration/test_retrieval_pipeline.py`

需要真实 Postgres + pgvector：

- 通过 M2 `IngestService` 导入一份 Markdown 测试文档，通过 M3 `EmbeddingBackfillService` 回填 embedding（或用 mock embedding），然后调用 `RetrievalService.retrieve()` 完成端到端检索。
- 断言 `status="retrieved"`，`references` 非空，query log 写入数据库且 `answer=NULL`。
- 使用不同 tenant 的 `RequestContext` 调用 → 断言无法看到其他 tenant 的 chunks。
- 使用 `access_level="public"` 的 `RequestContext` → 断言无法看到 `internal` / `confidential` / `restricted` chunks。
- 使用越权 `client_filters={"tenant_id": "*"}` → 断言 `status="failed"`。

### 代码扫描测试

- 禁止 `recallforge/retrieval/` 直接引用 `embedding_text_embedding_v4_1024` 或 `<=>`。
- 禁止 `recallforge/retrieval/` 直接 import `pgvector`。
- 允许 `recallforge/retrieval/` import `recallforge.storage.vector_store`（抽象）和 `recallforge.storage.repository`。
- 禁止 `recallforge/retrieval/` import `recallforge.storage.pgvector_store`（具体实现）。

## 实现文件清单

| 路径 | 职责 | 最小验收口径 |
| --- | --- | --- |
| `recallforge/retrieval/types.py` | 数据类型定义 | `RetrievalRequest`、`RetrievalResult`、`Reference`、`ReferenceChild`、`HitSummary`、`RankedCandidate`、`SearchConfig` |
| `recallforge/retrieval/errors.py` | 错误类型 | `RetrievalError`、`QueryRejectedError`、`FilterBuilderError`、`RerankerError` |
| `recallforge/context.py` | 请求上下文 | `RequestContext` dataclass、`current_request_context` ContextVar |
| `recallforge/retrieval/query_understanding.py` | Query Understanding | 空 query 拒绝、过短诊断、rewrite 开关、多意图检测 |
| `recallforge/retrieval/filter_builder.py` | Filter 构造 | 白名单校验、越权拒绝、department/access_level 展开、审计日志钩子调用 |
| `recallforge/retrieval/reranker/provider.py` | Reranker protocol | `RerankerProvider`、`RerankedCandidate`、`RerankerInput`、错误类型 |
| `recallforge/retrieval/reranker/dashscope_reranker.py` | DashScope reranker | `qwen3-rerank` 调用、重试、限流、配置校验 |
| `recallforge/retrieval/reranker/registry.py` | Reranker factory | `Settings → RerankerProvider`；`reranker_required` 校验 |
| `recallforge/retrieval/refusal.py` | 拒答判定 | 阈值判定（rerank / vector 双轨）、confidence 分级 |
| `recallforge/retrieval/parent_expansion.py` | Parent expansion | 批量回查、同 parent 合并、超长截断 |
| `recallforge/retrieval/context_assembly.py` | 上下文组装 | token 预算、证据排序、parent 合并、截断；编号由 ReferenceBuilder 分配 |
| `recallforge/retrieval/references.py` | References 组装 | 编号生成（唯一来源）、结构化引用、`child_chunks` 合并 |
| `recallforge/retrieval/retrieval_service.py` | 全链路编排 | 串起全流程、共享 session、document_title 查询、错误处理、query log 写入、latency 记录 |
| `recallforge/config.py` | M4 配置项 | 新增 reranker、拒答阈值（含 `min_vector_score`）、上下文预算、rewrite 开关、`reranker_required` |
| `migrations/` | 数据库迁移 | `QUERY_STATUSES` 追加 `retrieved`；`ck_rag_query_logs_status_payload` 调整 |
| `tests/test_query_understanding.py` | QU 单测 | 覆盖空 query、过短、rewrite、多意图 |
| `tests/test_filter_builder.py` | Filter 单测 | 覆盖越权拒绝、展开、白名单 |
| `tests/test_reranker_provider.py` | Reranker 单测 | 覆盖 protocol、mock HTTP、截断、重试 |
| `tests/test_refusal.py` | 拒答单测 | 覆盖 rerank/vector 双轨阈值、空候选、低 margin |
| `tests/test_parent_expansion.py` | Parent 单测 | 覆盖批量、合并、截断、缺失 |
| `tests/test_context_assembly.py` | 组装单测 | 覆盖预算、排序、合并、编号来自 ReferenceBuilder |
| `tests/test_references.py` | 引用单测 | 覆盖编号、child_chunks 合并、document_title 填充 |
| `tests/test_retrieval_service.py` | 服务单测 | 覆盖 happy path（`retrieved`）、拒答、降级、越权、embedding 失败、reranker_required 校验 |
| `tests/integration/test_retrieval_pipeline.py` | 集成测试 | 覆盖端到端检索、权限隔离、越权拒绝、`retrieved` 状态落库 |

## 已知限制

- `query_rewrite_enabled` 和 `hyde_enabled` 初版默认关闭。M4 预留开关和接口，但不实现 LLM 调用。实际 query rewrite 和 HyDE 需要在 M5 Agent 可用后（或引入独立 LLM 客户端后）才能完整实现。
- `date_range` filter 预留但不生效。`rag_chunks` 没有独立的文档日期列，需要 M8 在 metadata JSONB 或新增列中支持。
- reranker 运行时降级到 vector score 排序时，M4 使用独立的 `min_vector_score` 做拒答判定，但 cosine similarity 与 rerank score 尺度不同，`min_vector_score=0.6` 为初始建议值，必须通过 M6 评测校准。
- parent 超长截断使用子串匹配定位 child 在 parent 中的位置，对于 postprocess 修改过的 child 文本可能匹配不到。Fallback 策略：按 `chunk_index` 等比例定位，取前后窗口。
- token 估算使用 tokenizer 或自适应字符比例估算（CJK 占比 > 30% 时 `/1.5`，否则 `/4`），与实际 LLM tokenizer 可能有偏差。M6 可通过评测发现偏差并校准 `max_context_tokens`。
- `search_mode="hybrid"` 在 M4 会降级到 `vector`。hybrid search 的 RRF 融合、BM25 权重等留给 M8 实现。
- M4 的 `RetrievalService` 接受调用方注入的 `AsyncSession`，所有 repository 共享同一 session。Session 生命周期由调用方管理，`RetrievalService` 不自行创建、提交或关闭 session。
- `embedding_model` 不允许请求级覆盖。M4 初版只支持单一 `EmbeddingProvider`，多模型检索留给 M8。

## M4 完成定义

- `RequestContext` 在 `recallforge/context.py` 中定义，包含 `tenant_id`、`user_id`、`department`、`access_level`、`request_id`；`current_request_context` ContextVar 可在工具执行作用域读取。
- `QueryUnderstanding` 可以拒绝空 query 和过短 query，支持 query rewrite / HyDE 开关（初版关闭），可检测多意图 query。
- `FilterBuilder` 可以从 `RequestContext` 构造 `VectorSearchFilter`，`department` 和 `access_level` 正确展开为允许集合，客户端越权 filter 被拒绝并触发审计日志钩子。
- `RerankerProvider` protocol 已定义，`DashScopeRerankerProvider` 使用 `qwen3-rerank` 实现 rerank。生产路径 `reranker_required=True` 时 reranker 未配置则启动报错；运行时失败可降级到 vector score 排序。
- `RefusalJudge` 使用 `min_rerank_score` / `min_vector_score` 和 `min_top1_margin` 做双轨拒答判定，证据不足时明确返回拒答。
- `ParentExpander` 在 rerank 后的 top candidates 上批量回查 parent chunk，同 parent 合并，超长截断。
- `ContextAssembler` 在 `max_context_tokens` 预算内按 rerank 分数降序组装上下文，同 parent 不重复。引用编号从 `ReferenceBuilder` 获取，不自行分配。
- `ReferenceBuilder` 是引用编号的唯一来源，生成 `[1]`、`[2]` 编号的结构化引用，`child_chunks` 保留同 parent 下所有命中 child。
- `RetrievalService.retrieve(request, ctx)` 串起 Query Understanding → filter → recall → rerank → refusal → parent → doc_title → context → references → query log，返回 `RetrievalResult(status="retrieved")`。所有 repository 共享同一 `AsyncSession`。
- 每次查询可复盘 query 改写、候选列表、重排顺序、阈值判定、最终上下文和引用。
- 越权数据不会进入召回候选：`tenant_id` 强制注入；`department` / `access_level` 按层级展开；客户端越权 filter 被拒绝。
- LLM 即使在 `client_filters` 中传入 `tenant_id='*'` 或越权 filter，也会被服务端拒绝。
- 知识库外问题不会强答：`RefusalJudge` 在证据不足时返回 `should_refuse=True`。
- 所有配置项（top_k、final_top_k、min_rerank_score、min_vector_score、min_top1_margin、max_context_tokens、query_rewrite_enabled、hyde_enabled、reranker_required）都可配置。
- `rag_query_logs` 支持 `retrieved` 状态，M4 写入 `status="retrieved"` + `answer=NULL`，M5 生成答案后更新为 `status="success"`。
- M4 不实现 Agno Agent、HTTP API 或鉴权中间件；这些由 M5 接管。
- M4 不实现评测集、eval CLI 或指标计算；这些由 M6 接管。
- 单元测试覆盖 Query Understanding、filter 构造、reranker、拒答（双轨阈值）、parent expansion、上下文组装、references 和服务编排；集成测试覆盖端到端检索、权限隔离和 `retrieved` 状态落库。

## 自检：对照 ROADMAP M4 验收标准

| ROADMAP M4 验收 | 本 spec 覆盖位置 |
| --- | --- |
| 可以按 `tenant_id`、`doc_type`、`department`、`access_level`、`status`、`version` 过滤检索 | "服务端 Metadata Filter 构造"全章；`FilterBuilder` 展开策略；`VectorSearchFilter` 字段映射；`test_filter_builder.py` |
| 越权数据不会进入召回候选 | 设计约束第 3、4 条；`FilterBuilder` 白名单校验；access_level 层级矩阵；`test_filter_builder.py` 越权测试；集成测试权限隔离 |
| LLM 即使传入 `tenant_id='*'` 或越权 filter，也会被服务端拒绝 | `FilterBuilder` `FORBIDDEN_CLIENT_KEYS`；`test_filter_builder.py` 越权 key 测试；`test_retrieval_service.py` "越权 filter" 用例 |
| 命中 child chunk 后能补全 parent 上下文 | "Parent Expansion"全章；`ParentExpander.expand()`；`test_parent_expansion.py` |
| 超长 parent 不会让上下文超过预算 | "上下文组装"截断策略；`parent_context_window_tokens`；`max_context_tokens`；`test_context_assembly.py` 超预算用例 |
| 每次查询可复盘 query 改写、候选、重排、阈值判定和最终上下文 | "查询日志"全章；`HitSummary`；`SearchConfig`；`latencies_ms`；query log 字段映射表 |
| 知识库外问题不会强答 | "拒答判定"全章；`RefusalJudge`；`test_refusal.py`；M4 完成定义倒数第 4 条 |
