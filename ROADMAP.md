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
| M5 | Knowledge API 与测试台 | 提供导入、检索、上下文组装、问答测试 API，并交付最小上传/问答页面 |
| M6 | 知识库与文件治理平台 | 把 Knowledge API 升级为以知识库为一级资源的管理闭环，支持知识库 CRUD、单库文件 CRUD、任务中心和权限策略 |
| M7 | RAG 评测与质量治理 | 建立黄金集、`eval_run_id` 报告、知识质量检查、回归评测和召回调优闭环 |
| M8 | 稳定化与可观测 | 增强 OpenTelemetry tracing、失败恢复、权限审计、任务恢复和容量边界 |
| M9 | 增强能力 | 完整 hybrid search、Docling/MinerU、外部向量库、管理 UI 与治理看板增强 |

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

## M5 Knowledge API 与测试台

目标：提供可用的端到端知识底座能力，同时不破坏 `VectorStoreAdapter` 和服务端权限边界。M5 不引入内置 Agent Runtime，也不绑定具体 Agent 框架；上层应用通过 API 获取检索结果、上下文、引用和可选答案。

交付物：

- `POST /api/knowledge/documents`，身份来自 JWT 或受限 API Key。
- `POST /api/knowledge/retrieve`，返回 M4 检索结果、references、hit summary 和 refusal 信息。
- `POST /api/knowledge/context`，返回已组装的 LLM 上下文、references 和 trace id。
- `POST /api/knowledge/answer`，可选的问答测试接口，仅基于 RecallForge 组装的 context 生成答案，不承担 Agent Runtime 职责。
- `POST /api/rag/documents`，身份来自 JWT 或受限 API Key。
- `POST /api/rag/query`，请求体只接收 `question` 与白名单业务 filters。
- API schema 不暴露 `tenant_id`、`user_id`、`department`、`access_level`。
- 回答生成提示词，要求基于证据、引用来源、证据不足时拒答。
- 最小测试控制台：上传文件、查看导入任务状态、输入问题、展示答案、references、命中 chunk 与拒答原因。
- API request/response schema。
- 端到端 smoke test。

验收标准：

- 可以提交文档导入任务并返回 `document_id`、`job_id`、`status`。
- 可以完成一次问答并返回 `answer` 与 `references`。
- references 包含文档名、chunk、parent、来源、页码、更新时间等字段。
- 对知识库外问题返回“当前资料无法确认”或等价明确表达。
- API 请求体中的身份字段不能覆盖凭证身份。
- 上层调用不会绕过 rerank、parent expansion、references 和权限过滤。
- 测试页面只服务于知识底座调试，不承载业务工作台或 Agent 应用逻辑。

## M6 知识库与文件治理平台

目标：把 M5 的 RAG 能力服务升级为“AI 知识治理平台”的管理闭环。M6 开始，`KnowledgeBase` 必须成为平台一级资源；文档、权限、任务、检索、评测和审计都应能归属到具体知识库，避免只按 `tenant_id + source_uri` 管理文档。

优先级原则：

1. P0：知识库 CRUD、单库文件列表与文件 CRUD、知识库级检索边界。
2. P1：导入任务中心、批量操作、版本恢复、权限策略。
3. P2：质量报告、治理看板、低置信度与高频问题分析。

交付物：

- 新增知识库数据模型，例如 `rag_knowledge_bases`，至少包含 `tenant_id`、`name`、`description`、`status`、`owner`、`tags`、默认权限、默认检索参数和 metadata。
- 建立文档与知识库的归属关系：推荐在 `rag_documents` 增加 `knowledge_base_id`，并保证 parent chunk、child chunk、query log、ingest job 能追溯到知识库。
- `POST /api/knowledge-bases`：创建知识库。
- `GET /api/knowledge-bases`：查询当前租户可见知识库列表，支持按状态、标签、负责人、更新时间筛选。
- `GET /api/knowledge-bases/{kb_id}`：查看知识库详情，包含文件数、active chunk 数、最近导入状态、最近查询状态和索引状态摘要。
- `PATCH /api/knowledge-bases/{kb_id}`：更新知识库名称、描述、标签、默认策略和 metadata。
- `DELETE /api/knowledge-bases/{kb_id}`：逻辑删除或归档知识库，并按策略处理关联文档和 chunks。
- `GET /api/knowledge-bases/{kb_id}/documents`：单库文件列表，支持分页、doc_type、status、version、source_uri、上传人、更新时间和导入状态筛选。
- `GET /api/knowledge-bases/{kb_id}/documents/{document_id}`：文件详情，返回版本、hash、权限、chunk 数、embedding 状态、最近导入任务、warnings、parse_report 和引用字段摘要。
- `POST /api/knowledge-bases/{kb_id}/documents`：向指定知识库上传文件，复用 M5 ingest 链路，不允许绕过 `IngestService`。
- `PATCH /api/knowledge-bases/{kb_id}/documents/{document_id}`：更新文件标题、业务 metadata、doc_type、权限策略等非原文内容；需要重建索引的字段必须显式触发任务。
- `DELETE /api/knowledge-bases/{kb_id}/documents/{document_id}`：逻辑删除文件，并通过 `VectorStoreAdapter.delete_by_document_id()` 同步失效向量。
- `POST /api/knowledge-bases/{kb_id}/documents/{document_id}/reindex`：重建指定文件的 chunk、embedding 和向量索引。
- `GET /api/knowledge-bases/{kb_id}/documents/{document_id}/versions`：查看文件版本历史。
- `POST /api/knowledge-bases/{kb_id}/documents/{document_id}/restore-version`：恢复指定历史版本，恢复后生成新的 active 版本。
- `GET /api/knowledge-bases/{kb_id}/ingest-jobs`：单库导入任务列表。
- `POST /api/knowledge-bases/{kb_id}/ingest-jobs/{job_id}/retry`：重试失败导入任务。
- `POST /api/knowledge-bases/{kb_id}/reindex`：批量重建知识库索引，必须支持 dry run 和限流参数。
- 知识库级检索：`/api/knowledge/retrieve`、`/api/knowledge/context`、`/api/knowledge/answer` 支持服务端校验后的 `knowledge_base_id` 或 `knowledge_base_ids`，上层调用不能越权扩大范围。
- 权限策略模型：知识库成员与角色至少覆盖 owner、admin、editor、viewer、auditor；API Key / 上层应用授权必须绑定可访问的知识库范围。
- 审计日志覆盖知识库创建、更新、删除、文件上传、文件删除、权限变更、批量重建、越权访问尝试。
- 最小管理 UI：知识库列表、知识库详情、文件列表、文件详情、导入任务列表、失败原因、删除与重建入口。

验收标准：

- 可以创建、编辑、归档或删除一个知识库。
- 可以在单个知识库内上传、查询、查看详情、更新 metadata、删除文件。
- 文件列表能展示状态、版本、chunk 数、embedding 状态、最近任务和错误摘要。
- 知识库删除或文件删除不会留下可召回的 active chunk。
- 检索请求可以限定知识库范围，且不能通过请求体越权访问其他知识库。
- 单库导入任务、失败任务和重试结果可查询。
- 文件版本恢复后，新版本可被检索，旧版本默认不进入 active 检索。
- 权限评测覆盖跨知识库越权访问，泄漏为 0。
- 管理 UI 只通过服务端 API 操作，不直接访问数据库、向量列或 `VectorStoreAdapter`。

## M7 RAG 评测与质量治理

目标：把“召回能力强”变成可度量、可回归的工程事实。

交付物：

- 黄金评测集格式使用 JSONL，覆盖 FAQ、政策、产品资料、表格文档。
- 每条样例至少包含 `question`、`expected_document_ids`、`expected_chunk_keys`、`expected_answer_type`、`tenant_context`。
- 评测集可以绑定到具体知识库，支持按 `knowledge_base_id` 运行单库评测。
- answerable 和 unanswerable 问题集。
- 权限隔离问题集。
- 自研最小评测 CLI，例如 `recallforge eval run --dataset evals/golden.jsonl`。
- 可选接入 `ragas`、`promptfoo` 或 `trulens`，但初版指标计算不能依赖第三方服务。
- Recall@K、MRR、ParentRecall@K、CitationAccuracy、RefusalAccuracy、PermissionLeakage 计算脚本。
- `rag_eval_runs` 与 `rag_eval_cases` 表，或等价文件报告；每次评测产生唯一 `eval_run_id`。
- 每次调参输出可比较报告，记录 chunk 参数、embedding_model、top_k、reranker、parent expansion、阈值和 LLM。
- 召回失败样例分类：filter 误伤、embedding 未召回、rerank 误排、parent 扩展不足、生成阶段错误。
- 知识质量检查：重复文档、过期文档、低质量切片、缺失来源、缺失页码、无负责人、长期未命中文档、敏感信息和疑似密钥扫描。
- 高频拒答、低置信度问题和无命中问题分析，用于反向驱动知识补全。
- 质量报告可按知识库、文档类型、负责人、时间范围聚合。

验收标准：

- 任一检索策略变更都能跑评测。
- 任一知识库都可以运行独立评测并生成 `eval_run_id`。
- 可以比较两次 `eval_run_id` 的指标差异。
- 可以看到召回失败样例和失败原因分类。
- 可以用评测结果指导 chunk 参数、embedding 模型和 reranker 调整。
- 可以看到知识库质量报告，并定位重复、过期、低质量或不可追溯文件。
- 高频拒答和低置信度问题能进入治理待办。
- 权限泄漏测试持续为 0。

## M8 稳定化与可观测

目标：让系统可以被诊断、恢复和安全运行。

交付物：

- 导入任务状态机，包含 pending、running、success、failed、skipped_duplicate。
- 重试策略和幂等保护。
- 逻辑删除和向量删除同步。
- OpenTelemetry 作为 tracing 基线；后续可桥接 Langfuse、AgentOS 或其他观测平台。
- 查询 tracing，覆盖 auth、query understanding、embedding、vector search、rerank、parent lookup、context assembly、LLM。
- 慢查询和低置信度样例记录。
- 权限审计日志。
- 知识库、文件、导入任务、评测任务和查询日志之间可通过 trace id / request id / job id 关联。
- 后台任务恢复：导入、embedding backfill、reindex、eval run 支持失败重试、幂等保护和进度查询。
- 平台级审计：谁创建了知识库、谁上传或删除了文件、哪个应用调用了哪个知识库、哪些请求被拒绝。
- pgvector 容量边界监控：单租户 active chunk 超过 50 万或单表 active chunk 超过 200 万时评估 HNSW；更大规模时评估分区或外部向量库。
- 运维文档。

验收标准：

- 导入失败不会留下不可解释的半成品数据。
- 重复导入不会污染向量结果。
- 可以定位一次低质量回答是鉴权、解析、切片、filter、召回、rerank、上下文组装还是生成问题。
- 可以安全重建向量索引或外部向量库 collection。
- tracing span 与 query log 可以通过 request id 关联。
- 可以从知识库详情追溯到文件、任务、查询、评测和审计事件。

## M9 后续增强

目标：在初版稳定后继续提高召回上限和适用场景。

候选方向：

- 完整 Hybrid Search：BM25 / full-text + vector + RRF。
- Docling 解析器，增强结构化 PDF。
- MinerU 解析器，增强复杂版式、表格密集和 OCR 场景。
- 更完整的 small-to-big retrieval，例如邻近 chunk、标题路径、章节摘要扩展。
- Qdrant / Milvus / Pinecone 的 `VectorStoreAdapter` 实现。
- 文档增量更新和删除同步。
- AgentOS / Langfuse 等深度观测集成。
- 后台管理 UI 增强，用于治理看板、跨知识库质量对比、评测报告和失败样例分析。
- 跨知识库检索策略：按业务域、标签、应用授权和用户权限动态选择知识库集合。
- 知识冲突检测和过期知识提醒。
- 文档级使用热度、引用热度和知识缺口分析。

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
- 可以创建知识库，并在指定知识库内管理文件。
- 可以查询单库文件列表、文件详情、版本和导入任务。
- 可以通过 ChunkFlow 生成 parent/child chunk。
- 可以按 `tenant_id`、`doc_type`、`department`、`access_level` 过滤检索。
- 可以按服务端校验后的 `knowledge_base_id` 限定检索范围。
- 可以完成一次端到端问答并返回 references。
- Postgres 中能看到文档、parent chunk、child chunk、embedding、导入任务、查询日志和评测报告。
- 检索调用必须通过 `VectorStoreAdapter` 完成。
- 上层应用检索必须通过 RecallForge 服务 API 完成，不能绕过服务端权限、rerank 和 parent expansion。
- 检索命中 child chunk 后，可以通过 `parent_key` 回查 parent chunk。
- 对知识库外问题能明确返回“当前资料无法确认”。
- 权限泄漏评测为 0。
- 至少有一份可重复运行、带 `eval_run_id` 的 RAG eval 报告。
