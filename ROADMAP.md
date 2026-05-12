# ROADMAP.md

RecallForge 的路线图围绕一个北极星目标展开：极致召回能力。功能、架构和性能优化都要服务于这个目标。

## 北极星指标

优先优化这些指标：

- `Recall@50`：候选召回阶段能否把正确证据放进候选集。
- `Rerank@8`：reranker 后进入上下文的证据是否足够回答问题。
- `ParentRecall@8`：命中 child chunk 后，parent chunk 是否补足必要上下文。
- `CitationAccuracy`：答案引用是否真实、具体、可追溯。
- `RefusalAccuracy`：知识库外问题是否正确拒答。
- `PermissionLeakage`：越权召回必须为 0。

初版可以牺牲吞吐和延迟，但不能牺牲权限隔离、引用可追溯和召回可诊断。

## 阶段总览

| 阶段 | 主题 | 目标 |
| --- | --- | --- |
| M0 | 项目骨架 | 建立工程边界、配置、模型基线、鉴权骨架和测试入口 |
| M1 | 数据底座 | 完成 Postgres schema、pgvector、多 embedding 模型字段和 repository |
| M2 | ChunkFlow 入库 | 完成文档解析、parent/child 原文与 metadata 入库 |
| M3 | 向量适配 | 实现 VectorStoreAdapter、PgVectorStore、embedding 回填和多模型路由 |
| M4 | 高召回检索 | 完成 Query Understanding、metadata filter、vector recall、rerank、parent expansion、hybrid 钩子 |
| M5 | Agno 问答 | 通过受控 Tool 接入 Agno Agent，提供导入和查询 API |
| M6 | RAG 评测 | 建立黄金集、`eval_run_id` 报告、回归评测和召回调优闭环 |
| M7 | 稳定化 | 增强 OpenTelemetry tracing、失败恢复、权限审计和容量边界 |
| M8 | 增强能力 | 完整 hybrid search、Docling/MinerU、外部向量库、管理 UI |

## M0 项目骨架

目标：让后续开发有清晰边界和可验证入口。

交付物：

- 项目目录结构，采用 `project-root/recallforge/`、`migrations/`、`tests/`、`docs/` 的清晰层级。
- 基础配置加载，包含数据库 URL、DashScope API Key、region、embedding provider、embedding 维度、reranker、LLM、top_k、拒答阈值、上下文 token 预算。
- 默认模型矩阵：阿里百炼 / DashScope `text-embedding-v4@1024` embedding、`qwen3-rerank` reranker、LLM 由配置注入且上下文不少于 32k。
- `RequestContext` 结构和 `contextvars` 注入骨架。
- 鉴权骨架：用户请求使用 Bearer JWT，服务间导入任务使用受限 API Key。
- 测试框架和最小 CI 命令。
- 数据库迁移工具选型和初始化。
- `.env.example` 或等价配置样例。
- 基础日志格式，包含 request id、tenant id、user id、document id、job id。

验收标准：

- 新开发者可以在本地启动测试。
- 配置缺失时给出明确错误。
- 不存在硬编码数据库密码、模型名称、embedding 维度或 LLM 厂商。
- `RequestContext` 能在 API 入口创建，并在工具执行作用域读取。

## M1 数据底座

目标：建立可追溯、可过滤、可重建、支持多 embedding 模型并存的 RAG 数据模型。

交付物：

- `rag_documents`，包含 `content_hash`、`version`、`status`。
- `rag_parent_chunks`。
- `rag_chunks`，包含 child chunk 原文、权限字段、引用字段、`content_hash`、`status`、`embedding_model`、`embedding_dim`。
- 初版 embedding 存储策略二选一并写入 ADR：多列策略或多表策略，禁止"原表直接换模型维度"。
- 为 hybrid search 预留全文检索钩子，例如 `content_tsv` 列或独立全文索引表。
- `rag_ingest_jobs`，状态包含 `pending`、`running`、`success`、`failed`、`skipped_duplicate`。
- `rag_query_logs`。
- pgvector extension 初始化。
- tenant、doc_type、status、version、document_id、embedding 相关索引。
- repository 层封装，避免业务逻辑散落 SQL。

验收标准：

- migration 可从空库创建全部表。
- `EmbeddingProvider.dim` 是 embedding 维度的单一事实源，迁移和运行时校验都依赖它。
- `rag_chunks` 同时保存 child chunk 原文、向量描述、权限字段和引用字段。
- parent/child 能通过 `parent_key` 或 `parent_id` 稳定关联。
- 文档可以按 `(tenant_id, source_uri)` 定位业务主键。
- 删除文档时可以逻辑删除文档，并清理或失效相关 chunk。

## M2 ChunkFlow 入库

目标：把文档解析、结构化切片和 parent/child 映射跑通。M2 只负责 parent/child 原文、metadata、hash、版本和任务状态入库；embedding 列可以保持 NULL，由 M3 负责回填。

交付物：

- 按 [docs/chunkflow_migration.md](docs/chunkflow_migration.md) 迁移 ChunkFlow 核心模块。
- 统一 `parse_to_chunk_package()` 调用入口。
- 支持 Markdown、TXT、PDF 三种格式导入。
- 支持 CSV / TSV 的表格导入路径。
- `build_chunks_for_ingest()` 适配层。
- `parse_report`、`warnings`、`parser_used`、`chunker_used` 落库。
- 内容 hash 去重策略。
- 文档版本策略：hash 相同跳过，hash 不同建立新版本，旧版本 chunk 标记为 `superseded`。

验收标准：

- 至少 3 种文档格式可生成 parent/child chunk。
- child chunk 默认 `child_max_tokens=450`、`child_min_tokens=80`。
- parent 默认 `parent_granularity="chapter"`。
- 每个 child chunk 都能回查 parent chunk。
- 重复导入相同内容时产生 `skipped_duplicate` job，不生成重复 chunk。
- 解析失败时记录失败原因，解析降级时记录降级路径。

## M3 向量适配

目标：建立可替换向量库边界，初版实现 pgvector，并回填 M2 已入库的 child chunks。

交付物：

- `EmbeddingProvider` 封装，对外暴露 `provider`、`name`、`model_slug`、`dim`、`max_input_tokens`、`distance_metric`。
- `AlibabaBailianEmbeddingProvider`，文档入库使用 `text_type=document`，查询使用 `text_type=query`。
- `VectorChunk`、`VectorSearchHit` 数据结构；`VectorSearchHit` 包含 `score`、`score_source`、原始 rank。
- `VectorStoreAdapter` protocol 或 abstract base class，`search()` 显式接收 `embedding_model` 并预留 `search_mode`。
- `PgVectorStore.upsert_chunks()`。
- `PgVectorStore.search()`，支持 metadata filter、cosine 距离和 `score_source='vector'`。
- `PgVectorStore.delete_by_document_id()`。
- embedding 批量生成与回填任务。
- embedding 维度一致性校验。
- 多 embedding 模型路由策略，检索时必须显式声明 `embedding_model`。

验收标准：

- ingest/query 主流程只依赖 `VectorStoreAdapter`。
- 业务层没有散落的 pgvector SQL。
- metadata 中包含权限字段、版本字段、状态字段和 parent-child 关联字段。
- embedding 维度不匹配时失败并给出明确错误。
- 可以针对同一批 chunk 重建指定 embedding 模型的向量。

## M4 高召回检索

目标：完成第一版强召回链路，并为后续 hybrid search 留好接口。

交付物：

- Query Understanding 阶段：
  - 空 query 拒绝。
  - 过短 query 诊断。
  - 可配置 query rewrite / HyDE 开关。
  - 多意图 query 的后续拆分接口。
- 服务端强制 metadata filter，包含 `tenant_id`、`department`、`access_level`、`doc_type`、`status='active'`、默认最新版本。
- vector recall，初版 `top_k=30-50`。
- hybrid search 钩子：`search_mode`、`score_source`、全文索引字段或表已可被调用。
- reranker 接口和默认 `qwen3-rerank` 实现。
- 可调拒答阈值，初始建议 `min_rerank_score=0.35`、`min_top1_margin=0.05`。
- rerank 后 top 5 到 8 进入最终上下文。
- parent chunk 回查与 small-to-big context expansion。
- 上下文组装预算，初版 `max_context_tokens=24000`。
- references 组装，引用编号由系统生成。
- 查询日志记录 query understanding、命中 chunk、分数、`score_source`、rerank 顺序、阈值判定和耗时。

验收标准：

- 可以按 `tenant_id`、`doc_type`、`department`、`access_level`、`status`、`version` 过滤检索。
- 越权数据不会进入召回候选。
- LLM 即使传入 `tenant_id='*'` 或越权 filter，也会被服务端拒绝。
- 命中 child chunk 后能补全 parent 上下文。
- 超长 parent 不会让上下文超过预算。
- 每次查询可复盘 query 改写、候选、重排、阈值判定和最终上下文。
- 知识库外问题不会强答。

## M5 Agno 问答与 API

目标：提供可用的端到端 RAG 能力，同时不破坏 `VectorStoreAdapter` 和服务端权限边界。

交付物：

- Agno `Agent` 配置。
- 受控 Agno Tool：`search_internal_kb(query, filters)`，内部调用 M4 完整检索链路。
- 明确禁止直接使用 `agno.knowledge.Knowledge(vector_db=PgVector(...))` 作为默认路径。
- Tool schema 不暴露 `tenant_id`、`user_id`、`department`、`access_level`。
- `POST /api/rag/documents`，身份来自 JWT 或受限 API Key。
- `POST /api/rag/query`，请求体只接收 `question` 与白名单业务 filters。
- 回答生成提示词，要求基于证据、引用来源、证据不足时拒答。
- API request/response schema。
- 端到端 smoke test。

验收标准：

- 可以提交文档导入任务并返回 `document_id`、`job_id`、`status`。
- 可以完成一次问答并返回 `answer` 与 `references`。
- references 包含文档名、chunk、parent、来源、页码、更新时间等字段。
- 对知识库外问题返回“当前资料无法确认”或等价明确表达。
- API 请求体中的身份字段不能覆盖凭证身份。
- Agent 工具调用不会绕过 rerank、parent expansion、references 和权限过滤。

## M6 RAG 评测与调优闭环

目标：把“召回能力强”变成可度量、可回归的工程事实。

交付物：

- 黄金评测集格式使用 JSONL，覆盖 FAQ、政策、产品资料、表格文档。
- 每条样例至少包含 `question`、`expected_document_ids`、`expected_chunk_keys`、`expected_answer_type`、`tenant_context`。
- answerable 和 unanswerable 问题集。
- 权限隔离问题集。
- 自研最小评测 CLI，例如 `recallforge eval run --dataset evals/golden.jsonl`。
- 可选接入 `ragas`、`promptfoo` 或 `trulens`，但初版指标计算不能依赖第三方服务。
- Recall@K、MRR、ParentRecall@K、CitationAccuracy、RefusalAccuracy、PermissionLeakage 计算脚本。
- `rag_eval_runs` 与 `rag_eval_cases` 表，或等价文件报告；每次评测产生唯一 `eval_run_id`。
- 每次调参输出可比较报告，记录 chunk 参数、embedding_model、top_k、reranker、parent expansion、阈值和 LLM。
- 召回失败样例分类：filter 误伤、embedding 未召回、rerank 误排、parent 扩展不足、生成阶段错误。

验收标准：

- 任一检索策略变更都能跑评测。
- 可以比较两次 `eval_run_id` 的指标差异。
- 可以看到召回失败样例和失败原因分类。
- 可以用评测结果指导 chunk 参数、embedding 模型和 reranker 调整。
- 权限泄漏测试持续为 0。

## M7 稳定化与可观测

目标：让系统可以被诊断、恢复和安全运行。

交付物：

- 导入任务状态机，包含 pending、running、success、failed、skipped_duplicate。
- 重试策略和幂等保护。
- 逻辑删除和向量删除同步。
- OpenTelemetry 作为 tracing 基线；后续可桥接 Langfuse、AgentOS 或其他观测平台。
- 查询 tracing，覆盖 auth、query understanding、embedding、vector search、rerank、parent lookup、context assembly、LLM。
- 慢查询和低置信度样例记录。
- 权限审计日志。
- pgvector 容量边界监控：单租户 active chunk 超过 50 万或单表 active chunk 超过 200 万时评估 HNSW；更大规模时评估分区或外部向量库。
- 运维文档。

验收标准：

- 导入失败不会留下不可解释的半成品数据。
- 重复导入不会污染向量结果。
- 可以定位一次低质量回答是鉴权、解析、切片、filter、召回、rerank、上下文组装还是生成问题。
- 可以安全重建向量索引或外部向量库 collection。
- tracing span 与 query log 可以通过 request id 关联。

## M8 后续增强

目标：在初版稳定后继续提高召回上限和适用场景。

候选方向：

- 完整 Hybrid Search：BM25 / full-text + vector + RRF。
- Docling 解析器，增强结构化 PDF。
- MinerU 解析器，增强复杂版式、表格密集和 OCR 场景。
- 更完整的 small-to-big retrieval，例如邻近 chunk、标题路径、章节摘要扩展。
- Qdrant / Milvus / Pinecone 的 `VectorStoreAdapter` 实现。
- 文档增量更新和删除同步。
- AgentOS / Langfuse 等深度观测集成。
- 后台管理 UI，用于导入状态、文档列表、评测报告和失败样例分析。

## 召回优先的调参顺序

遇到回答质量不佳时，按以下顺序排查：

1. 确认 Query Understanding 没有错误改写或过度扩展。
2. 确认权限和 metadata filter 没有误过滤。
3. 检查正确证据是否进入 `top_k=50` 候选。
4. 如果没有进入候选，调整 chunk 策略、embedding 模型，或启用 hybrid search。
5. 如果进入候选但没有进入上下文，调整 reranker。
6. 如果 child chunk 命中但上下文不足，调整 parent expansion。
7. 如果上下文超长，调整 `max_context_tokens`、parent 截断和同 parent 合并策略。
8. 如果上下文足够但答案错误，调整 Agent instructions 和引用约束。
9. 如果答案没有引用，修正 references 组装和回答格式约束。
10. 如果低置信度问题强答，调整拒答阈值并加入 unanswerable 评测集。

## 初版最终验收

初版发布前必须满足：

- 可以导入至少 3 种文档格式。
- 可以通过 ChunkFlow 生成 parent/child chunk。
- 可以按 `tenant_id`、`doc_type`、`department`、`access_level` 过滤检索。
- 可以完成一次端到端问答并返回 references。
- Postgres 中能看到文档、parent chunk、child chunk、embedding、导入任务、查询日志和评测报告。
- 检索调用必须通过 `VectorStoreAdapter` 完成。
- Agent 检索必须通过受控 Tool 完成，不能绕过服务端权限、rerank 和 parent expansion。
- 检索命中 child chunk 后，可以通过 `parent_key` 回查 parent chunk。
- 对知识库外问题能明确返回“当前资料无法确认”。
- 权限泄漏评测为 0。
- 至少有一份可重复运行、带 `eval_run_id` 的 RAG eval 报告。
