# Metadata 语义召回桥设计 Spec

> 标签：`recall-quality`、`chunk-enrichment`、`query-enrichment`、`multi-vector`、`hybrid`
> 状态：Draft（待 §15 决策点拍板后进入实施）
> 关联：ROADMAP M4 / M7、AGENTS.md “默认模型矩阵 / 检索链路”、`recallforge/retrieval/retrieval_service.py`、`recallforge/storage/models.py`

## 1. 背景

ChunkFlow + 当前 parent / child 切片在结构和粒度上已经达到可用水位。`Recall@50` 的瓶颈不再是“切片切坏了”，而是这类业务场景：

> 「切片质量已经非常高了，但是用户问题和切片内容语义和关键字相关度都很低」

更精确地说，这是经典的 **query-chunk semantic gap**：

| 维度 | 文档侧 | 用户侧 |
| --- | --- | --- |
| 措辞 | 规范、术语、标题化 | 口语、目的化、带情绪 |
| 视角 | 描述「这件事是什么 / 怎么定义」 | 「我现在遇到 X，要怎么办」 |
| 颗粒 | 单一章节、单一规则 | 跨章节、跨规则的综合意图 |
| 词汇 | 全称、产品代号、规范术语 | 缩写、外号、内部黑话 |
| 信息密度 | 高，单 chunk 信息浓缩 | 低，问题极短、信息稀疏 |

embedding 模型再换一个版本，对这类 gap 的收益通常是 5%~15%；但**给每个 chunk 加上「它能回答什么问题 / 它讲了什么概念 / 它会被哪些行话指代」这一层 metadata**，对业务 Recall@50 的收益经常能到 30%~50%。这就是「metadata 比 embedding 更关键」的工程含义。

本 spec 不替换 embedding 模型，也不重做 chunking，而是在 **chunk 侧和 query 侧各架一座语义桥**，让两边在召回前先“走向中间”。

## 2. 设计目标

P0（必须解决）：

1. 对“术语 vs 口语”“描述 vs 任务”“规范 vs 口头别称”场景显著提升 Recall@50 与 ParentRecall@8。
2. 不改 child chunk 原文，不破坏现有引用粒度、parent 回查链路和审计能力。
3. 不绕过 `VectorStoreAdapter`，不让上层 prompt 影响权限范围（继续遵守 AGENTS.md 不可破坏约束 1–3）。
4. 召回 hit 必须仍可追溯到具体 `chunk_id`、`parent_id`，引用文本只用 chunk 原文，**不允许把 enrichment 文本本身作为引用展示给用户**。
5. enrichment 失败可降级：enrichment pipeline 故障不阻塞 ingest 主路径，只降级到“原始向量召回”。

P1（强烈建议）：

6. 支持租户级别的**术语词典 / 别称词典**（中文缩写、产品代号、内部黑话）。
7. enrichment 与 embedding 模型解耦：切换 embedding 模型不强制重做 enrichment。
8. 召回失败诊断可指出本次命中是来自“原文向量”“HyQE 向量”“概念向量”“BM25 文本”还是“别称展开”。

P2（后续优化）：

9. enrichment 质量可被 M7 评测集回归。
10. 针对“高频低置信度问题 / 高频拒答问题”反向补 enrichment（治理闭环）。

非目标：

- 本 spec 不做完整 Hybrid Search 算法选型（留给 M9）。仅在 `score_source` / `search_mode` 接口上对齐。
- 本 spec 不引入新的 chunker。child chunk 仍由 ChunkFlow 生成。
- 本 spec 不替换 reranker（仍是 `qwen3-rerank`）。

## 3. 核心思路：双向语义桥

> 单一 chunk 向量代表的是「这段话怎么说」，但用户问的是「这段话能回答什么」。两者之间需要一座桥。

### 3.1 Chunk 侧（离线，ingest 阶段）

enrichment 的输入不只是 chunk 自身。为了让 LLM / 规则引擎在生成假设问题时拥有正确的业务视角，enrichment 采用**三层上下文**模型：

```
┌─────────────────────────────────────────────────┐
│  KB domain_profile（知识库级）                     │   admin 手写一次
│  "这是保险理赔知识库"                               │
│  ┌─────────────────────────────────────────────┐ │
│  │  Document profile（文档级）                   │ │   系统自动推断 + admin 可选覆盖
│  │  "本文档是医疗险理赔条款，                      │ │
│  │   涵盖等待期、免赔额、报销比例"                  │ │
│  │  ┌─────────────────────────────────────────┐│ │
│  │  │  Chunk content + heading_path           ││ │   ChunkFlow 切片产出
│  │  │  "第十二条 被保险人自保险合同生效之日..."    ││ │
│  │  └─────────────────────────────────────────┘│ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

三层各自的职责：

| 层级 | 来源 | 成本 | 作用 |
| --- | --- | --- | --- |
| **KB domain_profile** | admin 创建 KB 时手写 | 一次性 5 分钟 | 告诉 enrichment generator「这个知识库的领域是什么」 |
| **Document profile** | 系统在 parse 完成后自动推断（§5.1.1） | 零 LLM 成本 | 告诉 generator「这篇文档的类型、主题、核心实体和用户视角是什么」 |
| **Chunk content** | ChunkFlow 切片产出 | 已有 | 实际被 enrichment 的内容 |

Document profile 是本次新增的关键层。同一个 KB 里的文档可能极度异质——产品规格书 vs 退货政策 vs 故障排查手册 vs FAQ，用同一段 KB description 指导所有文档的 HyQE 生成会「模糊」。Document profile 让每篇文档获得更精确的生成视角，但**不增加 admin 负担**（自动推断，仅异常时人工覆盖）。

在三层上下文之上，给每个 child chunk 生成**四类问询侧 metadata**，统称 `ChunkEnrichment`：

| 类别 | 字段 | 作用 | 来源 |
| --- | --- | --- | --- |
| 问题锚点 | `hypothetical_questions` (HyQE) | 「这段话能回答哪 3~8 个用户问题？」 | LLM 生成（三层 context 注入 prompt） |
| 概念锚点 | `concepts` | 这段话讲了哪些核心概念 / 主题 / 任务 | LLM 抽取 |
| 实体锚点 | `entities` | 文中的产品名、模块名、角色、动作动词、关键数字 | 规则 + LLM 抽取 |
| 别称锚点 | `aliases` | 该 chunk 概念在租户内部的常见缩写 / 别称 / 同义词 | 租户词典 + LLM 推断 |

外加两类**结构侧 metadata**（不依赖 LLM、零成本）：

| 类别 | 字段 | 作用 |
| --- | --- | --- |
| 路径锚点 | `breadcrumb` | `["产品手册", "退款政策", "时限"]` 拼成一句话 |
| 意图标签 | `intent_tags` | `definition / how_to / policy / troubleshoot / spec / example / faq` |

### 3.2 Query 侧（在线，retrieval 阶段）

对 query 做对称增强（QueryEnrichment）：

| 类别 | 字段 | 作用 |
| --- | --- | --- |
| 改写 | `rewritten_query` | 已有，M4 已实现，可选启用 |
| 假设答案 | `hyde_passage` | 已有钩子，M4 预留 |
| **概念化** | `query_concepts` | 抽取问题中的核心概念 / 任务，匹配 chunk `concepts` |
| **意图分类** | `query_intent` | 分类为 `how_to / definition / troubleshoot / ...`，匹配 chunk `intent_tags` |
| **别称归一** | `expanded_terms` | 用租户词典把别称 → 全称、缩写 → 全称 |

### 3.3 业务耦合的边界与隔离

「业务相关性是设计成败的核心，但工程上必须把它隔离到少数几个可配置接口里，让管线本身保持通用」。

划分如下：

**业务无关层（通用，开箱即用）**：

- HyQE 通用 prompt 框架（what / how / why / when / colloquial 五类问题模板）
- 概念 / 实体抽取（通用 NER + LLM）
- intent 七分类（`definition / how_to / policy / troubleshoot / spec / example / faq`）
- breadcrumb / 结构化字段
- 多路召回 + RRF + rerank header

**业务相关层（必须沉淀，但被显式隔离到几个表 / 配置项）**：

| 承载位置 | 内容 | 谁写 | 频率 |
| --- | --- | --- | --- |
| `rag_knowledge_bases.metadata.domain_profile` | `{domain, domain_description, intent_taxonomy_extension, forbidden_question_patterns}` | KB admin / owner | 一次性 |
| `rag_documents.metadata.document_profile` | `{document_type_hint, topic_summary, key_entities, user_perspective_hint}` | **系统自动推断**（§5.1.1），admin 可选覆盖 | 自动，极低频覆盖 |
| `DOCUMENT_TYPE_PERSPECTIVE_MAP` | 文档类型 → 用户视角映射表（如 `contract_terms → "用户会用口语问条款规则、时限、金额"` ） | 开发 / admin | 新增文档类型时 |
| `rag_terminology` | 缩写、别称、内部黑话词典 | KB admin / owner + M7 反向挖矿 | 高频 |
| `LLMEnrichmentGenerator` 的三层 context 注入 | 从 KB profile + Doc profile + chunk 拼到 prompt | 系统自动 | 自动 |
| `QueryEnricher` 的 domain 提示 | 从 `domain_profile.domain_description` 注入 | 系统自动 | 自动 |
| M7 评测集 | 业务硬样本子集 | 业务方提供 | 周期性 |

设计原则：

1. **零业务配置下也能跑**：未配置 `domain_profile`、未手写 `document_profile`、未建词典时，系统仍能自动推断 document profile + 用规则生成 enrichment，吃掉「术语 vs 口语 / 全称 vs 缩写」60%+ 的 gap。
2. **业务知识分三级注入，成本递减**：
   - **第一级：术语词典**（admin 高频维护，ROI 最高）
   - **第二级：KB domain_profile**（admin 一次性配置，影响所有文档的 LLM prompt 视角）
   - **第三级：document profile**（99% 自动推断，仅异常文档 admin 手动覆盖）
3. **业务知识不在代码里**：所有业务相关字段都通过 API / DB 维护，可审计、可回滚、可跨环境同步。换业务线不需要改代码。唯一的半代码配置是 `DOCUMENT_TYPE_PERSPECTIVE_MAP`（初版 hardcode 10 条覆盖主要文档类型，后续可升级为配置表）。
4. **所有 profile 不影响权限**：与 QueryEnricher 一样，只影响 score / prompt，不参与 metadata filter（继续守住 AGENTS.md 不可破坏约束 3）。

冷启动 SOP（建议写入运营文档）：

1. KB owner 在创建 KB 时填写 `domain_profile.domain` 与 1~3 句 `domain_description`（10 行配置，5 分钟）。
2. 上传首批文档。系统自动为每篇文档推断 `document_profile`（零 admin 负担）。
3. 系统从 chunk 的 `entities` 聚合候选词典，按词频 + 跨文档覆盖率排序输出 candidates。
4. KB admin 审定 5~30 条核心术语 + 别称，写入 `rag_terminology`，触发增量 enrichment backfill（30 分钟）。
5. 上线观察 1 周，从 `rag_query_logs` 中检视高频拒答 / 低置信度 query，反向补词典。
6. 如果某篇文档的 enrichment 质量明显异常（HyQE 偏通用），admin 可在文件详情页覆盖该文档的 `document_profile`（极低频）。

### 3.4 召回时

不再是单路 `vector(content)` 召回。改为**多路并发召回 + 受保护融合**（见 §6.4），再走原有 rerank → parent expansion 链路。各路返回的仍然是 `chunk_id`，不会产生“虚拟引用”。

```
                     ┌─→ vector(content)        ─┐
                     ├─→ vector(hyqe)            │
query ─ enrich ──────┼─→ vector(concept)         ├─→ RRF/weighted ─→ rerank ─→ parent ─→ context
                     ├─→ BM25(content_tsv)       │
                     └─→ BM25(enrichment_tsv) ───┘
                                  ↑
                          租户词典扩展词
```

这条管线的关键点：**所有候选都是 `chunk_id`，没有任何路径产生“没法定位回原文”的虚拟召回**。enrichment 文本只参与召回打分，不进入最终上下文（除非通过 `parent` 自然包含）。

## 4. 数据模型

### 4.1 新增表：`rag_chunk_enrichment`

不直接膨胀 `rag_chunks`（chunk 是高扇出的核心表，新增列会导致 alter 成本与 hot path 影响）。enrichment 放独立表，1 chunk : 1 enrichment。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK |  |
| `tenant_id` | text NOT NULL | 隔离主键，冗余以支持独立查询 |
| `knowledge_base_id` | bigint NOT NULL | 与 chunk 对齐 |
| `chunk_id` | bigint FK → `rag_chunks.id`, UNIQUE NOT NULL |  |
| `document_id` | bigint NOT NULL | 冗余，方便按文档清理 |
| `parent_id` | bigint NOT NULL | 冗余 |
| `breadcrumb` | text | 标题路径拼成的自然语言句 |
| `intent_tags` | text[] | `definition / how_to / policy / troubleshoot / spec / example / faq` |
| `concepts` | text[] | 5~12 个核心概念 |
| `entities` | jsonb | 结构化抽取，`{"product": [...], "role": [...], "action": [...], "metric": [...]}` |
| `aliases` | text[] | 别称、缩写、内部黑话（含来自租户词典的命中项） |
| `hypothetical_questions` | text[] | 3~8 条假设问题 |
| `enrichment_text` | text | 上述字段拼成的紧凑自然语言文本，用作 BM25 索引和 embedding 输入 |
| `enrichment_text_tsv` | tsvector COMPUTED PERSISTED | `to_tsvector('simple', enrichment_text)`，gin 索引 |
| `embedding_provider` | text NOT NULL | 与主向量列对齐 |
| `embedding_model` | text NOT NULL |  |
| `embedding_dim` | int NOT NULL |  |
| `embedding_hyqe_text_embedding_v4_1024` | vector(1024) | **HyQE 向量**（每条 question 池化后单条向量，见 §6.3） |
| `embedding_concept_text_embedding_v4_1024` | vector(1024) | **概念向量**（concept 列表 + breadcrumb 池化后单条向量） |
| `version` | int NOT NULL | 与 chunk 版本对齐，便于回滚 |
| `status` | text NOT NULL | `active / superseded / deleted`，与 chunk 联动 |
| `generator` | text NOT NULL | 例如 `qwen3-32b-instruct@2026-05` |
| `generator_config_hash` | char(64) | 生成器 prompt + 参数指纹，方便诊断与回归 |
| `quality_score` | numeric(4,3) | 0~1，由 §6.5 启发式打分 |
| `warnings` | jsonb | 生成过程降级、截断、噪声标记 |
| `metadata` | jsonb |  |
| `created_at` / `updated_at` | timestamptz |  |
| `deleted_at` | timestamptz |  |

约束与索引：

```sql
UNIQUE (chunk_id);
CHECK (status IN ('active','superseded','deleted'));
CHECK (embedding_dim > 0);
CHECK (quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1));

CREATE INDEX idx_rag_chunk_enrichment_tenant_kb_active
  ON rag_chunk_enrichment (tenant_id, knowledge_base_id, status)
  WHERE status = 'active';

CREATE INDEX idx_rag_chunk_enrichment_document
  ON rag_chunk_enrichment (document_id);

CREATE INDEX idx_rag_chunk_enrichment_enrichment_tsv_active
  ON rag_chunk_enrichment USING gin (enrichment_text_tsv)
  WHERE status = 'active';

CREATE INDEX idx_rag_chunk_enrichment_hyqe_hnsw
  ON rag_chunk_enrichment
  USING hnsw (embedding_hyqe_text_embedding_v4_1024 vector_cosine_ops)
  WHERE status = 'active';
-- 概念向量索引同理，按数据规模触发
```

设计选择（重要）：

- **不**新建 `rag_chunks_hyqe` 这种「问题型 chunk」表，避免引入「召回的 hit 不指向真实 chunk」的诡异链路。所有命中最终都是 `chunk_id`。
- HyQE 与 Concept 各**只存 1 条向量**（多个问题在生成阶段做 mean-pool 或拼接后 embed 一次），避免单 chunk 衍生 N 条索引行，控制向量库行数。代价是 HyQE 内部多样性会被平均掉一点，但用 BM25(`enrichment_tsv`) 补回这部分关键词命中。
- 不在 `rag_chunks` 上新增列，避免破坏现有 hot path 与已有 0001/0002/0003 迁移结构。

### 4.2 新增表：`rag_terminology`

租户级术语 / 别称词典。这是这套设计里最被低估、但 ROI 最高的一张表。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK |  |
| `tenant_id` | text NOT NULL |  |
| `knowledge_base_id` | bigint NULLABLE | NULL 表示租户全域生效；非 NULL 表示只在该 KB 生效 |
| `canonical` | text NOT NULL | 标准词，例如「订单履约 SLA」 |
| `aliases` | text[] NOT NULL | 别称数组，例如 `["发货时效","履约时间","SLA","承诺多久发货"]` |
| `domain` | text | `product / policy / ops / hr / legal / tech` |
| `lang` | text | `zh / en / mixed` |
| `weight` | numeric(4,3) | 召回时别称扩展的权重，默认 0.7 |
| `status` | text | `active / disabled` |
| `created_by` / `updated_by` / `created_at` / `updated_at` |  |  |

约束：

```sql
UNIQUE (tenant_id, knowledge_base_id, canonical);
CHECK (status IN ('active','disabled'));
CREATE INDEX idx_rag_terminology_tenant_kb_status ON rag_terminology (tenant_id, knowledge_base_id, status);
CREATE INDEX idx_rag_terminology_aliases_gin ON rag_terminology USING gin (aliases);
```

来源：

1. **离线初始化**：在 ingest 完一批文档后，对所有 chunk 的 `entities` + `aliases` 做聚合 + 频次过滤，给租户管理员一份候选词典，由 admin / owner 审定后入库。
2. **运营人工补录**：通过 M6 知识库管理 UI 增删（M6 已有 `KnowledgeBasePermissionService`，权限沿用 admin / owner）。
3. **从查询日志反向学习**：M7 治理阶段，对“高频拒答但已有相关文档”的 query 抽取关键词，反推待补术语。

### 4.3 现有表无变化

`rag_chunks` / `rag_parent_chunks` / `rag_documents` / `rag_ingest_jobs` / `rag_query_logs` 不变。

`rag_query_logs.metadata` 增加几个 key（不改 schema）：
- `enrichment.query_concepts`
- `enrichment.query_intent`
- `enrichment.expanded_terms`
- `enrichment.lanes`：每路召回的 `{name, hits, latency_ms}` 摘要
- `enrichment.score_sources_topk`：top final_top_k 各 hit 来自哪一路

## 5. Ingest 阶段：Enrichment Pipeline

### 5.1 编排位置

在 `recallforge/ingest/ingest_service.py` 的现有调用链里**新增两个独立阶段**：

```
parse → chunk → parent/child 入库 → embedding 回填(M3)
                    ↓
         document profile 推断（本 spec 新增）
                    ↓
         enrichment 阶段（本 spec 新增）
                    ↓
         enrichment embedding 回填
```

约束：

- document profile 推断在 parse 完成后、enrichment 前执行，利用已有结构信息（heading_path、doc_type、template、前几个 parent chunk），**零 LLM 成本**。
- enrichment 是 **post-chunk + post-embedding** 的独立步骤，不阻塞 child chunk 主路径入库。
- enrichment 失败 → `rag_ingest_jobs.warnings` 写入降级信息，`rag_ingest_jobs.status` 仍可为 `success`（child chunk 与原始向量已就绪，主链路可用）。
- enrichment 可以单独重跑：新增 `EnrichmentBackfillService`，按 `(document_id | knowledge_base_id | tenant_id)` 范围批量重建。

### 5.1.1 Document Profile 自动推断

parse 完成后，系统已经拿到文档的全部结构信息。不需要额外调 LLM，用**规则 + 现有元数据**即可推断出一份文档级 profile：

```python
@dataclass
class DocumentProfile:
    document_type_hint: str       # "合同/条款类文档"、"技术文档"、"FAQ"、"规格书" 等
    topic_summary: str            # "本文档涵盖：总则、保险责任、理赔流程、免责条款"
    key_entities: list[str]       # ["等待期", "免赔额", "报销比例", "保险金"]（top 10 高频实体）
    user_perspective_hint: str    # "用户通常用口语询问理赔流程、时限、赔付金额和所需材料"

def infer_document_profile(
    doc_metadata: DocumentMetadata,
    parent_chunks: list[ParentChunk],
    child_chunks: list[ChildChunk],
    kb_domain_profile: DomainProfile | None,
) -> DocumentProfile:
    # 1. document_type_hint：从 doc_type + ChunkFlow template 推断
    #    contract_terms → "合同/条款类文档"
    #    auto + markdown → "技术/产品文档"
    #    faq → "常见问答文档"
    document_type_hint = DOCUMENT_TYPE_PERSPECTIVE_MAP.get(
        doc_metadata.template or doc_metadata.doc_type,
        "通用文档",
    ).type_hint

    # 2. topic_summary：从前 5 个 parent chunk 的 heading_path 提取章节概览
    #    ["医疗险条款", "第一章 总则", "第二章 保险责任", ...]
    #    → "本文档涵盖：总则、保险责任、理赔流程、免责条款"
    topic_summary = summarize_from_headings(parent_chunks[:5])

    # 3. key_entities：全文档 child chunk 高频名词（jieba 分词 + 词频 top 10）
    key_entities = extract_top_entities_by_frequency(child_chunks, top_n=10)

    # 4. user_perspective_hint：从 document_type_hint 查映射表
    #    条款类 → "用户可能用口语问理赔流程、时限、金额"
    #    FAQ 类 → "用户可能直接复述问题标题或用口语描述"
    #    规格类 → "用户可能问参数、兼容性、支持范围"
    user_perspective_hint = DOCUMENT_TYPE_PERSPECTIVE_MAP.get(
        doc_metadata.template or doc_metadata.doc_type,
        "通用文档",
    ).user_perspective

    return DocumentProfile(
        document_type_hint=document_type_hint,
        topic_summary=topic_summary,
        key_entities=key_entities,
        user_perspective_hint=user_perspective_hint,
    )
```

推断结果写入 `rag_documents.metadata.document_profile`（利用现有 jsonb 字段，无 schema 变更）。

`DOCUMENT_TYPE_PERSPECTIVE_MAP` 初版内容（hardcode，后续可升级为配置表）：

| template / doc_type key | `type_hint` | `user_perspective` |
| --- | --- | --- |
| `contract_terms` | 合同/条款类文档 | 用户通常用口语问条款规则、时限、金额、责任归属 |
| `faq` | 常见问答文档 | 用户可能直接复述问题标题，也可能用口语重新描述 |
| `markdown` | 技术/产品文档 | 用户可能问功能用法、配置方式、参数含义、故障排查 |
| `pdf` (default) | 综合文档 | 用户可能从任务视角提问，关注流程、步骤、条件 |
| `csv` / `table_file` | 表格/数据文档 | 用户可能查询特定字段值、对比数据、筛选条件 |
| `txt` | 纯文本文档 | 用户可能做关键词搜索或概念查询 |

admin 覆盖入口：通过 M6 已有的 `PATCH /api/knowledge-bases/{kb_id}/documents/{document_id}` 更新 `metadata.document_profile`，不需要新增 API。覆盖后触发该文档的 enrichment 重生成。

**关键约束**：

- document profile 推断**零 LLM 成本**，完全基于 parse 后已有结构信息。
- 推断失败（如 heading_path 全为空）→ 使用空 profile，enrichment generator 仅凭 KB profile + chunk 内容工作，不阻塞。
- document profile 不参与 metadata filter，只影响 enrichment 生成质量。
- admin 覆盖记录审计事件 `document.update_metadata`（复用 M6 已有审计）。

### 5.2 Enrichment Generator 抽象

```python
@dataclass
class EnrichmentInput:
    # 三层上下文
    kb_domain_profile: DomainProfile | None      # 知识库级
    document_profile: DocumentProfile | None      # 文档级（§5.1.1 自动推断或 admin 覆盖）
    # chunk 自身
    content: str
    heading_path: list[str]
    doc_type: str
    breadcrumb: str
    # 辅助
    tenant_terminology: list[TerminologyEntry]     # 该 KB 或租户的活跃词典条目

class ChunkEnrichmentGenerator(Protocol):
    name: str            # e.g. "qwen3_llm_v1"
    model_slug: str
    prompt_version: str

    async def generate(self, chunk: EnrichmentInput) -> ChunkEnrichment:
        ...
```

Generator 拿到的输入已经包含三层上下文。具体怎么用取决于实现：

**`LLMEnrichmentGenerator`**（调用配置注入的 LLM，DashScope `qwen3-32b-instruct` 或同级）：

- 三层 context（KB profile + document profile + chunk）拼到 system prompt 头部（见 §5.3.3 完整模板）。
- prompt 中明确：「你正在为 RAG 系统生成 chunk metadata。所有内容必须严格基于给定 chunk，不允许编造。」
- 输出 JSON schema 强约束，使用 `response_format=json_object` 或等价能力，并在解析失败时降级到 heuristic 分支。
- 单 chunk LLM 调用上限：1 次。多个字段在同一次调用中产出。
- 三层 context + chunk content 合计超过 `enrichment_max_input_tokens`（默认 1800）时，按优先级截断：先截 chunk content（保留头 + 尾 + heading_path），再截 `document_profile.topic_summary`，最后截 `kb_domain_profile.domain_description`。三层 context 本身通常不超过 200 token。

**`HeuristicEnrichmentGenerator`**（零 LLM 成本兜底）：

- `breadcrumb` ← heading_path 拼接
- `intent_tags` ← 规则匹配（含「如何 / 怎么 / 步骤 / 流程」→ `how_to`；「是什么 / 定义 / 含义」→ `definition`；「为什么 / 报错 / 失败 / 异常」→ `troubleshoot`；含 `[政策|条款|规定|不得|必须]` → `policy`）；如果 `document_profile.document_type_hint` 存在，额外注入文档类型关联意图（如 `contract_terms` → 强制包含 `policy`）
- `concepts` ← `document_profile.key_entities` 与 chunk 内容的交集（该文档的高频实体在当前 chunk 中出现的子集）
- `entities` ← 简单 NER（jieba + 自定义词表），优先识别 `document_profile.key_entities` 中的词
- `aliases` ← 仅做租户词典匹配，不做推断
- `hypothetical_questions` ← 空（heuristic 模式不生成 HyQE）

启动时若 LLM 不可用，自动降级到 heuristic + 词典模式，并在 `rag_ingest_jobs.warnings` 写入 `enrichment_downgraded_to_heuristic`。

注意：**heuristic 模式同样受益于 document profile**——`document_profile.key_entities` 让规则引擎的 concepts / entities 抽取更精确，而这部分是零 LLM 成本的。

### 5.3 Prompt 设计要点（HyQE 部分）

HyQE 是这个 spec 的核心，prompt 质量直接决定 ROI。要点：

1. **强约束基于原文**：「问题必须能从下面这段话直接得到答案；不能扩展到其他段落」。
2. **多样化指令**：要求生成 5 类问题：
   - what：定义型，「X 是什么？」
   - how：任务型，「怎么做 X？」
   - why：因果型，「为什么会出现 X？」
   - when：触发型，「什么情况下需要 X？」
   - colloquial：口语型，「用普通话再问一次，假设是新员工 / 普通用户问的」
3. **文档视角约束**：当 `document_profile.user_perspective_hint` 存在时，要求至少 2 条 HyQE 采用该视角表述（例如条款类文档生成「买了保险多久能赔」而非「第十二条说了什么」）。
4. **强制使用别称**：在 prompt 里附上租户词典里命中的 canonical/aliases，要求至少 1 条问题使用别称。这是别称扩展的最低成本入口。
5. **去重 + 长度上下限**：单条问题 8~40 字，整体 3~8 条，自动 dedupe。
6. **拒绝幻觉**：当 chunk 内容不足以支持某类问题（如 chunk 只是表格头部），允许返回少于 3 条。
7. **不允许跨 chunk 推断**：禁止生成需要其他章节信息才能回答的问题。

输出示例（chunk：「3.1.4 在 7 天内可以无理由退货，运费由买家承担」；KB domain：电商平台；Doc profile：退货政策文档，`user_perspective` 为用户通常用口语问退换货流程、时限、运费）：

```json
{
  "hypothetical_questions": [
    "下单后多少天内可以退货？",
    "无理由退货的运费谁出？",
    "我后悔了，七天内能退吗？",
    "退货运费是平台还是我自己承担？",
    "什么情况算无理由退货？"
  ],
  "concepts": ["无理由退货", "退货时限", "运费承担"],
  "entities": {"action": ["退货"], "metric": ["7 天"]},
  "aliases": ["7 天无理由", "七天无理由退货"],
  "intent_tags": ["policy", "how_to"]
}
```

#### 5.3.3 三层 context 的 prompt 拼接模板

LLM enrichment generator 的 system prompt 结构如下（英文骨架便于实现，运行时按 locale 渲染）：

```
[System]
You are generating chunk metadata for a RAG system.
All content must be strictly based on the given chunk. Do not fabricate.

[Knowledge Base]
Domain: {kb_domain_profile.domain}
Description: {kb_domain_profile.domain_description}

[Document]
Document type: {document_profile.document_type_hint}
Topic: {document_profile.topic_summary}
Key entities: {document_profile.key_entities | join(', ')}
User perspective: {document_profile.user_perspective_hint}

[Terminology]
{matched terminology entries with canonical + aliases}

[Chunk]
Heading path: {heading_path | join(' > ')}
---
{chunk.content}
```

When any layer is missing, skip that section (no empty placeholders).

Effect comparison for chunk "Article 12: The insured must wait 180 days from policy effective date for illness claims...":

| Prompt layers | Generated HyQE quality |
| --- | --- |
| Chunk only (no profiles) | Generic: "What does Article 12 say?" / "What does 180 days mean?" |
| + KB profile | Better: "What waiting period rules apply after policy takes effect?" |
| + KB profile + **Doc profile** | Best: "How long after buying medical insurance can I claim?" / "Is the waiting period 180 or 90 days?" / "Can I claim for illness diagnosed right after buying?" |

The third layer shifts LLM from "reading a clause" to "reading an illness insurance waiting period clause", producing questions that match real user phrasing.

### 5.4 Enrichment Embedding 计算

- `embedding_hyqe_*`：将 `hypothetical_questions` 拼成一段以换行分隔的文本，调用 `embed_document()`（不是 `embed_query`！这里它们是“文档侧”），写入 hyqe 向量列。
  - 备选策略：每条问题分别 embed，再 mean-pool。两种都可，初版用拼接 + 单次调用，控制成本。
- `embedding_concept_*`：将 `breadcrumb + concepts + aliases` 拼成一段紧凑文本，单次 `embed_document()`。
- `enrichment_text`：把上面所有结构化字段渲染成一段可读文本：
  ```
  路径：产品手册 > 退款政策 > 时限
  概念：无理由退货 / 退货时限 / 运费承担
  别称：7 天无理由 / 七天无理由退货
  常见问题：下单后多少天内可以退货？退货运费谁出？……
  ```
  这段文本进入 `enrichment_text_tsv`，作为 BM25 路的索引。

`enrichment_text` **不**进入 LLM 上下文，**不**作为引用展示，**不**替代 chunk 原文。

### 5.5 质量打分与门控

每条 enrichment 落库前做启发式打分 `quality_score ∈ [0,1]`，规则：

| 检查 | 影响 |
| --- | --- |
| HyQE 数量 ≥ 3 | +0.20 |
| HyQE 平均长度 8~40 字 | +0.15 |
| concepts ≥ 3 | +0.15 |
| HyQE 与 chunk 原文余弦相似度 mean ≥ 0.4 | +0.20 |
| HyQE 与 chunk 余弦相似度 std ≥ 0.05（有多样性） | +0.10 |
| 别称命中租户词典 ≥ 1 条 | +0.10 |
| 没有触发幻觉检测（HyQE 中出现租户词典外的“专有名词”但 chunk 中无此词） | +0.10 |

`quality_score < 0.4` 的 enrichment 仍写入但 `status='active'`，同时 `warnings` 里打 `low_quality`，由治理流程后续处理。可以在召回路由处加权降级（见 §6.4）。

### 5.6 触发与版本

- 新文档 ingest：自动跑 enrichment。
- 文档新版本（hash 变化）：旧版本 enrichment 跟 chunk 一起标 `superseded`，新版本重新生成。
- 文档逻辑删除：enrichment 同步标 `deleted`。
- 术语词典更新：触发**增量 backfill** 任务，按 `(tenant_id | knowledge_base_id)` 扫描 active chunk，仅重生成被新增 alias 影响的 chunk（通过原 chunk content 是否包含 alias 的 canonical 或 alias 自身做粗筛）。
- 评测发现某类 query 持续召回失败：M7 反向写入 `enrichment_revision_requests`，由治理流程定向重生成。

## 6. Retrieval 阶段：QueryEnrichment & 多路召回

### 6.1 流程改动

`retrieval_service.py` 的 `retrieve()` 在 `query_understanding` 与 `filter_build` 之后、`vector_search` 之前，插入一个 `query_enrichment` 步骤：

```
query_understanding ─→ filter_build ─→ query_enrichment ─→ multi_lane_search ─→ rrf_fusion ─→ rerank → parent → context
```

### 6.2 QueryEnricher

```python
class QueryEnricher:
    def __init__(self, settings, llm_client=None, terminology_repo=None): ...

    async def enrich(self, query: str, ctx: RequestContext) -> QueryEnrichment:
        # 1) 从租户词典命中 expanded_terms（别称 → canonical）
        # 2) 用 LLM（轻量）或规则抽取 query_concepts、query_intent
        # 3) 生成 hyde_passage（M4 hyde_enabled=True 时复用，否则跳过）
        ...
```

返回：

```python
@dataclass
class QueryEnrichment:
    query_concepts: list[str]       # ["退货", "运费"]
    query_intent: str | None        # "policy" / "how_to"
    expanded_terms: list[ExpandedTerm]   # [{alias:"7天无理由", canonical:"无理由退货", weight:1.0}, ...]
    hyde_passage: str | None
    warnings: list[str]
```

调用约束：

- query enrichment 必须有**严格延迟预算**，默认 250ms，超时降级为「仅词典匹配 + 规则意图」。
- 对短 query（<8 字）跳过 LLM，仅走词典 + 规则。
- 多意图 query（M4 已识别）拆分后**逐意图独立 enrich**，召回结果合并。

### 6.3 多路召回（lanes）

`VectorStoreAdapter.search()` 在初版扩展为：

```python
async def search(
    self,
    query_embedding: list[float],
    embedding_model: str,
    filters: VectorSearchFilter,
    top_k: int,
    search_mode: str = "vector",
    lane: str = "content",        # 新增
) -> list[VectorSearchHit]:
    ...
```

`lane` ∈ `{ "content", "hyqe", "concept" }`，对应不同向量列。`PgVectorStore` 根据 lane 路由到不同列：

- `content` → `rag_chunks.embedding_text_embedding_v4_1024`（现状）
- `hyqe` → `rag_chunk_enrichment.embedding_hyqe_text_embedding_v4_1024`，JOIN 回 `rag_chunks`
- `concept` → `rag_chunk_enrichment.embedding_concept_text_embedding_v4_1024`，JOIN 回 `rag_chunks`

BM25 路独立接口：

```python
async def keyword_search(
    self,
    query_terms: list[str],           # 已做别称展开
    filters: VectorSearchFilter,
    top_k: int,
    field: str = "content",           # "content" | "enrichment"
) -> list[VectorSearchHit]:
    ...
```

初版实现走 Postgres `tsvector @@ to_tsquery(...)`，并附带 `ts_rank_cd` 作为 score。`score_source` 取值为 `bm25_content` / `bm25_enrichment`。

`RetrievalService` 内部新增 `MultiLaneSearcher`：

```python
async def search_all_lanes(
    self,
    enriched_query: EnrichedQuery,
    filters: VectorSearchFilter,
    top_k: int,
) -> dict[str, list[VectorSearchHit]]:
    # 并发跑 5 路；每路 top_k 单独配
```

每路默认 `top_k`：

| lane | top_k | embed 输入 | 备注 |
| --- | --- | --- | --- |
| `vector_content` | 30 | `effective_query`（已 rewrite/hyde） | 当前主路 |
| `vector_hyqe` | 30 | `effective_query` | 命中“能回答这个问题的 chunk” |
| `vector_concept` | 20 | `query_concepts` 拼接后 embed | 命中“讲了这个概念的 chunk” |
| `bm25_content` | 30 | `expanded_terms ∪ query tokens` | 关键词路 |
| `bm25_enrichment` | 30 | 同上 | 在 enrichment_text 上做 BM25，可以击穿口语-术语差异 |

### 6.4 融合：Best-Rank-Protected RRF + lane 保送

**为什么不用纯 RRF**：纯 RRF 把多路 rank 简单求和，会出现「某 lane rank=1 的真 hit 被多路都中等 rank 的『看起来好但不对』的 chunk 反超」。这是「多路召回带来噪声、好 chunk 被埋」最直接的失败模式。

我们用一个有保护机制的变体融合，由三部分组成：

#### 6.4.1 Best-Rank-Protected RRF（默认融合）

```
weighted_rrf(chunk, lane) = w_lane * 1 / (k_rrf + rank_lane(chunk))

final_score(chunk) =
    max_lane( weighted_rrf(chunk, lane) )                            # 任一路的最高加权 RRF（保护强信号）
  + bonus * sum_lane( 1[rank_lane(chunk) <= rank_bonus_threshold] )  # 多路确认奖励
```

含义：

- `max` 部分确保 **任意一路强推（rank 很高）的 chunk 一定有高分**，不被多路平庸推荐稀释。
- `bonus` 部分给「多路同时推荐」的 chunk 小幅加分，保留多路确认的价值。
- 该变体在工业 hybrid search 是已知做法（与单纯 RRF 相比，硬样本子集 Recall 提升通常 5%~10%）。

默认参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `k_rrf` | 60 | 标准 RRF 常数 |
| `bonus` | 0.05 | 多路命中奖励，远小于 `max` 部分主导 |
| `rank_bonus_threshold` | 10 | 只对 top 10 名内的多路命中给奖励 |
| `w_vector_content` | 1.0 | baseline |
| `w_vector_hyqe` | 1.2 | HyQE 通常对「口语 query」收益最大 |
| `w_vector_concept` | 0.8 | 概念路噪声偏多，权重偏低 |
| `w_bm25_content` | 0.8 |  |
| `w_bm25_enrichment` | 1.0 | 别称命中击穿能力强 |

可被 `Settings`、`RagKnowledgeBase.metadata.recall_weights` 覆盖（KB 级覆盖优先于全局）。

如果某条 chunk 的 enrichment `quality_score < 0.4`，它在 `vector_hyqe` / `vector_concept` / `bm25_enrichment` 三路的 rank 贡献按 0.5x 折扣，避免低质量 enrichment 把好的原文召回挤掉。

#### 6.4.2 每路保送：`top_n_per_lane_protected`

融合截断不是简单"取 top N"。送入 reranker 的候选集 = **每路前 N 名保送 ∪ 融合 top N 兜底**：

```
protected = ⋃_lanes top_n_per_lane_protected_first_n_hits(lane)        # 默认每路保送 2 个
filled    = top_(top_k_after_fusion - |protected|)_by_final_score      # 兜底填到 top_k_after_fusion

candidates_for_rerank = protected ∪ filled
```

意义：**保证每路最强的 hit 一定有机会被 reranker 看到**，不被融合阶段误杀。这是对「某 lane rank=1 的真 hit 因为其他路都没召回到，融合后排不到候选集」这一具体失败模式的针对性兜底。

默认参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `top_n_per_lane_protected` | **2** | 每路保送数；5 路全开时占 10 个名额（占 30 的 1/3） |
| `top_k_after_fusion` | **30** | 送入 reranker 的总候选上限（含保送）；与 baseline 持平 |

#### 6.4.3 为什么 rerank 输入不能盲目调大

`qwen3-rerank` 单次最多 500 候选（AGENTS.md）只是「塞得下」，不等于「应该塞那么多」。盲目调大 rerank 输入会沿着链路产生 4 层耦合成本：

**成本 1：rerank 自身的 token 与延迟成本（线性）**

| `top_k_after_fusion` | rerank token | rerank 延迟（参考）|
| --- | --- | --- |
| 30 | 30 × 450 ≈ 13.5k | ~600ms |
| 80 | 80 × 450 ≈ 36k | ~1500ms |

2.7× token 成本和 2.5× 延迟，换硬样本子集 < 5% 的 Recall 增量，ROI 不成立。

**成本 2：rerank 的 calibration 漂移**

`qwen3-rerank` 是 pointwise 打分，单个分数确实不随候选集大小变化。**但拒答阈值是相对判断**：

- `min_rerank_score=0.35` 是绝对阈值；候选越多，假阳性越多，分数分布抬高，noise floor 上升，真 hit 不够「突出」。
- `min_top1_margin=0.05` 是相对阈值；候选越多，top-2 越可能是另一个看起来相关的噪声，margin 缩小，更易触发拒答。
- 工程上观察到 `top_k_after_fusion` 从 30 调到 100 时，`refusal_decision` 会被错误升级，即使真 hit 还在 top-3。

**成本 3：rerank 输出 → parent expansion 的下游污染**

rerank 输出 final_top_k 不是终点。若 reranker 在大候选集下错排，把 1~2 个噪声 chunk 排进 final_top_k：
- 它们对应的 parent 进入 context assembly
- 挤占其他真 hit 的 parent 的 context 预算（`max_context_tokens=24000`）
- 触发 `truncation_applied` 或 `context_budget_exceeded`

「LLM 上下文窗口有限」的真正威胁不是 rerank 输入直接撑爆 context，而是 **rerank 阶段错排沿着 parent expansion 链路污染最终上下文**。

**成本 4：失去对失败的可诊断性**

候选越多，"hit 死在哪一步"越难复盘。30 候选的 rerank trace 容易人工 review，100 候选的不行。AGENTS.md 强调「可诊断」优先于「最大吞吐」。

#### 6.4.4 工程原则：reranker 是精排器，不是兜底机器

**正确做法**：让前置的多路召回 + Best-Rank-Protected RRF + 每路保送做硬筛，给 reranker 一个「少而精」的候选集。漏召问题应该在召回/融合阶段结构性解决，而不是把漏召风险推给 reranker 扛。

约束：

```
rerank_token_budget = top_k_after_fusion × avg_chunk_tokens
                    ≤ 15k        # 硬约束
```

超出就要回头审视是不是融合策略偷懒。

#### 6.4.5 多路召回的真正价值：在 parent expansion 阶段兑现

候选集不需要为了 hit 多路召回的"覆盖优势"而扩大。**同 parent 合并机制让多路重复发现转化为"置信度提升"而不是"上下文膨胀"**：

```
rerank top 8 child hits
          ↓
按 parent_id 去重合并
          ↓
~4~6 unique parents（多路同 parent 命中 → 合并）
          ↓
context assembly（按 parent 组织，每个 parent 一段）
```

效果：
- 多路命中同一 parent 下不同 child → **不增加 context 预算**，但 parent 的置信度更高
- `final_top_k` 语义对齐为「unique parent 数」上限，而不是 child 数
- 多路召回的覆盖优势在 parent expansion 阶段被兑现，**不必通过扩大 rerank 输入来兑现**

#### 6.4.6 动态 lane 数：用 query 类型决定送几路

不是所有 query 都该走 5 路全开。`QueryEnricher` 在意图分类时同时输出 `query_mode`，决定启用哪些 lane：

| query_mode | 触发条件 | 启用 lane | rerank 实际候选数 |
| --- | --- | --- | --- |
| `structured_exact` | 含 SKU / 订单号 / 字段名 / 错误码 / GUID 等结构化标识 | 仅 `vector_content` | ~5~10 |
| `short_keyword` | query 长度 < 8 字且无意图关键词 | `vector_content + bm25_content` | ~10~15 |
| `standard` | 默认 | 5 路全开 | ~20~30 |
| `colloquial_heavy` | 触发口语标志（如「怎么搞」「咋办」「啥意思」） | 5 路全开，`w_vector_hyqe ×1.5` | ~20~30 |
| `understanding_rejected` | `query_understanding.rejected=True` | 不进入召回 | 0 |

平均 rerank 候选数 ≈ 20，最坏不超过 30；rerank token 成本平均在 9k 左右。

#### 6.4.7 候选集大小的诊断指标

为了让团队能持续观察候选集是否设置合理，每次 retrieval 必须记录：

| metric | 含义 |
| --- | --- |
| `candidate_count_after_fusion` | 实际送 rerank 的候选数 |
| `candidate_tokens_total` | 该次 rerank 实际消耗 token |
| `truncated_by_fusion` | 是否因 `top_k_after_fusion` 截断（有真 hit 排在 31~50 名被砍） |
| `final_top_k_unique_parents` | rerank 输出去重后的 unique parent 数 |
| `context_truncation_applied` | parent expansion 后是否触发 context 截断 |

`truncated_by_fusion` 长期为 0 → `top_k_after_fusion` 可以更小；持续 > 5% → 需要调大 lane 权重 / 保护数，而**不是**调大 `top_k_after_fusion`。

### 6.5 rerank 输入增强（重要）

reranker 仍然是 `qwen3-rerank`，输入仍然是 `(query, chunk_content)`，但拼接的 chunk 文本前**显式前置一段 metadata header**，让 reranker 看到结构信息：

```
[路径] 产品手册 > 退款政策 > 时限
[概念] 无理由退货 / 退货时限 / 运费承担
[原文]
3.1.4 在 7 天内可以无理由退货，运费由买家承担
```

设计要点：

- 不加 HyQE（HyQE 是召回侧的桥，加到 rerank 输入会让 reranker 倾向于“问题相似度”而非“答案相关度”，反而劣化）。
- header 字段顺序固定，token 预算上限 80（防止溢出 reranker context）。
- header 必须可关闭：`rerank_metadata_header_enabled`，默认 True，A/B 评测用。

### 6.6 引用与上下文

- references 仍然只引用 `rag_chunks.content` + parent 上下文，**不展示 enrichment 文本给用户**。
- `RagQueryLog.hit_summary` 每个 hit 增加 `lane_sources: ["vector_hyqe", "bm25_enrichment"]`，记录该 chunk 通过哪些路被召回。
- `RagQueryLog.metadata.enrichment.*` 记录 query 侧增强（见 §4.3）。

## 7. 模块设计

### 7.1 目录结构

```text
recallforge/
  enrichment/                          # 新增
    __init__.py
    types.py                           # ChunkEnrichment / QueryEnrichment / DocumentProfile 数据类
    document_profile.py                # DocumentProfileInferrer + DOCUMENT_TYPE_PERSPECTIVE_MAP
    generators/
      base.py                          # ChunkEnrichmentGenerator Protocol + EnrichmentInput
      llm_generator.py                 # LLMEnrichmentGenerator (three-layer context prompt)
      heuristic_generator.py           # HeuristicEnrichmentGenerator (uses doc profile key_entities)
    quality.py                         # quality_score 打分
    terminology.py                     # TerminologyService（租户词典）
    query_enricher.py                  # QueryEnricher
    backfill.py                        # EnrichmentBackfillService（单文档 / KB 范围）
  retrieval/
    multi_lane.py                      # 新增：MultiLaneSearcher
    fusion.py                          # 新增：RRF / 加权融合
  storage/
    models.py                          # 新增 RagChunkEnrichment / RagTerminology
    repository.py                      # 新增 EnrichmentRepository / TerminologyRepository
  api/
    routes/
      terminology.py                   # 新增：词典 CRUD API（admin/owner）
migrations/
  versions/
    0004_add_chunk_enrichment_and_terminology.py
tests/
  test_enrichment_generators.py
  test_enrichment_quality.py
  test_query_enricher.py
  test_multi_lane_search.py
  test_rrf_fusion.py
  test_terminology_repo.py
  test_e2e_recall_gap.py               # 黄金集：术语 vs 口语场景
```

### 7.2 边界要求

- `recallforge/enrichment/` 不能 import `recallforge/api/`、`recallforge/console/`。
- `LLMEnrichmentGenerator` 不能 hardcode 厂商；通过 `Settings.enrichment_llm_*` 注入。
- `MultiLaneSearcher` 只能调用 `VectorStoreAdapter` 与 `EnrichmentRepository`，不许直接拼 pgvector SQL。
- `QueryEnricher` 输出**永远不能**进入 metadata filter；它只影响“怎么找”，不影响“能找谁”（权限）。
- `enrichment_text` 必须经过和 chunk 内容相同的脱敏管线（如 future PII redaction）。

### 7.3 与 M6 衔接

- 词典 API 走 `/api/knowledge-bases/{kb_id}/terminology`（或租户级 `/api/terminology`），权限沿用 KB admin / owner。
- enrichment backfill 任务接入 `rag_ingest_jobs`，新 `job_type='enrichment_backfill'`（需要在 `rag_ingest_jobs.metadata` 写入或新增一列；初版用 metadata key 即可，避免 schema 改动）。
- 审计事件新增：
  - `terminology.create / update / delete`
  - `enrichment.backfill_requested`
  - `enrichment.quality_alert`

## 8. 配置项

新增配置项（全局默认，可被 KB / tenant 覆盖）：

| key | default | 说明 |
| --- | --- | --- |
| `enrichment_enabled` | `True` | 全局开关 |
| `enrichment_generator` | `"llm"` | `llm` / `heuristic` / `auto` |
| `enrichment_llm_model` | （来自全局 LLM 配置） | 不允许 hardcode 厂商 |
| `enrichment_max_input_tokens` | `1800` |  |
| `enrichment_min_hyqe` | `3` |  |
| `enrichment_max_hyqe` | `8` |  |
| `enrichment_quality_threshold` | `0.4` | 低于此值打 `low_quality` |
| `document_profile_auto_infer` | `True` | 自动推断 document profile |
| `document_profile_perspective_map` | (built-in) | `DOCUMENT_TYPE_PERSPECTIVE_MAP`，初版 hardcode，后续可配置化 |
| `enrichment_lane_weights` | `{...}` | §6.4 默认权重 |
| `enrichment_k_rrf` | `60` |  |
| `query_enricher_enabled` | `True` |  |
| `query_enricher_timeout_ms` | `250` |  |
| `query_enricher_min_query_len` | `8` | 短于此长度只走规则 + 词典 |
| `multi_lane_top_k` | `{vector_content:30, vector_hyqe:30, vector_concept:20, bm25_content:30, bm25_enrichment:30}` | 各路独立召回数 |
| `top_k_after_fusion` | `30` | RRF 后送 rerank 的上限，与 baseline 持平 |
| `top_n_per_lane_protected` | `2` | 每路保送数；5 路 × 2 = 10 个保护名额 |
| `rerank_token_budget` | `15000` | rerank 单次 token 硬上限，超出自动收紧 `top_k_after_fusion` |
| `enable_dynamic_lane_selection` | `True` | 启用 §6.4.6 动态路数 |
| `final_top_k_semantic` | `"unique_parents"` | `unique_parents` / `children`，默认按 parent 去重计数 |
| `rerank_metadata_header_enabled` | `True` |  |
| `terminology_max_aliases_per_term` | `20` |  |
| `enrichment_backfill_batch_size` | `100` |  |

### 8.1 链路 Token 预算路径分析

整个召回 → 上下文链路的 token 流必须显式留痕，避免任一阶段悄悄吃掉下游预算：

| 阶段 | token 流（标准 query） | 延迟参考 |
| --- | --- | --- |
| 5 路并发召回 | 0（索引层完成） | max(单路) ≈ 80ms |
| QueryEnricher（含 LLM） | query 增强 ≤ 1k | ≤ 250ms（含 timeout 上限） |
| Best-Rank-Protected RRF + 保送 | 0 | < 10ms |
| **Rerank 输入（硬约束）** | **`top_k_after_fusion` × 450 ≤ 15k** | ~600ms（30 候选） |
| Rerank 输出 | top 8 child | - |
| Parent expansion + 同 parent 合并 | 8 child → ~4~6 unique parents | DB read ~50ms |
| **Context assembly（硬约束）** | **≤ `max_context_tokens=24000`** | < 50ms |
| LLM 答案生成 | input ≈ 24k | 2~5s（业务侧负担） |

两条硬约束的关系：

- **`rerank_token_budget`（≤ 15k）**：保护 reranker 不被无谓 token 拉低 calibration 与延迟。
- **`max_context_tokens`（≤ 24k）**：保护 LLM 上下文。受 rerank 输出质量直接影响——rerank 错排会让噪声 parent 进入并挤占预算。

观察口径：每条查询日志 `latencies_ms` 与 `metadata.context` 已记录上述阶段，新增以下字段（写入 `rag_query_logs.metadata`，无需 schema 变更）：

- `metadata.tokens.rerank_input`
- `metadata.tokens.context_assembled`
- `metadata.candidates.candidate_count_after_fusion`
- `metadata.candidates.truncated_by_fusion`
- `metadata.candidates.unique_parents_in_final`

## 9. 失败与降级

| 故障 | 行为 |
| --- | --- |
| Document profile 推断失败（heading_path 全空、child chunks 为空） | 使用空 profile，enrichment generator 仅凭 KB profile + chunk 内容工作，不阻塞；warnings 记 `document_profile_infer_failed` |
| LLM enrichment 失败 | 降级到 `HeuristicEnrichmentGenerator`，warnings 记 `enrichment_llm_failed` |
| 整个 enrichment 阶段失败 | 主 ingest 仍标 `success`；`rag_ingest_jobs.warnings` 记 `enrichment_failed`；该文档 chunk 在召回时只走 `vector_content + bm25_content` 两路 |
| QueryEnricher 超时 | 跳过概念 / 意图增强，仅走词典展开 |
| 任一 lane 超时 | 该 lane 视为 0 hits，warnings 记 `lane_timeout:<name>`，融合不阻塞 |
| enrichment 行不存在但 chunk active | 不报错；该 chunk 不出现在 HyQE / concept / enrichment_bm25 三路，仍可被 `vector_content` / `bm25_content` 命中 |
| 词典更新 backfill 中 | 当前 query 仍使用旧 enrichment；不阻塞在线检索 |

## 10. 安全 & 权限

必须维持 AGENTS.md 不可破坏约束：

1. enrichment 表与 chunk 表使用**完全相同**的 metadata filter（`tenant_id`、`knowledge_base_id`、`department`、`access_level`、`doc_type`、`status='active'`、`version`）。
2. `MultiLaneSearcher` 在每一路调用前都用 `FilterBuilder.build(ctx, client_filters)` 重新构造 filter；不允许复用上一次结果。
3. 用户 prompt **不能**通过 query enrichment 注入新的 filter 字段（QueryEnrichment 不参与 filter，只参与 score）。
4. enrichment 文本中如果含有 PII / secret，与 chunk 同等脱敏。
5. 词典 API 必须 KB admin / owner，不可由 viewer 编辑。
6. 跨租户测试：词典查询、enrichment 查询、HyQE 召回必须通过 `tenant_id` 过滤；新增 `tests/test_enrichment_permission.py` 验证。

## 11. 评测口径（接入 M7）

### 11.1 阶段化双指标（核心）

把召回链路拆成两个独立可观测的阶段，**先保 Candidate-Recall，再优化 Final-Recall**：

| 指标 | 衡量阶段 | 失败时的归因方向 |
| --- | --- | --- |
| `Candidate-Recall@top_k_after_fusion` | 多路召回 + 融合后、rerank 前 | 融合阶段漏召 → 调整 lane 权重、保护策略、`top_n_per_lane_protected`、`top_k_after_fusion` |
| `Final-Recall@final_top_k` | rerank 后 | reranker 阶段错排 → 调整 metadata header、阈值、reranker 模型 |

调参硬规则：**永远先调到 Candidate-Recall 高，再调 Final-Recall**。把这两个指标分开是定位「真 hit 被埋在哪一步死掉的」的唯一方法，避免调参盲打。

### 11.2 lane 贡献分解

每次 eval run 输出每个 hit 的 lane 贡献，至少包含：

| 字段 | 含义 |
| --- | --- |
| `recall_by_lane@K` | 单 lane 独立 Recall@K，定位「某 lane 是否值得维护」 |
| `lane_unique_contribution@K` | 该 lane 独占召回（其他 lane 都没召到）的 case 占比 |
| `protected_save_rate` | 通过 `top_n_per_lane_protected` 保护机制保下来的真 hit 占比 |
| `fusion_displacement_rate` | 某 lane rank≤3 但最终未进 rerank 输入的 case 占比（异常信号） |

`fusion_displacement_rate > 5%` 视为融合策略需要回归调参。

### 11.3 硬样本子集

构造四类硬样本子集，跟踪硬样本 Recall：

- `gap.terminology_vs_colloquial`：文档术语，query 口语
- `gap.full_name_vs_abbr`：文档全称，query 缩写 / 别称
- `gap.description_vs_task`：文档描述「X 是什么」，query 问「怎么做 X」
- `gap.cross_section_intent`：query 意图需要跨多个章节信息

### 11.4 验收口径

- 硬样本子集 `Candidate-Recall@30` 相对 baseline（M4 单路）提升 ≥ 30%
- 硬样本子集 `Final-Recall@8`（unique parent 数）相对 baseline 提升 ≥ 20%
- `CitationAccuracy` 不下降
- `PermissionLeakage` 仍为 0
- `fusion_displacement_rate < 5%`
- `truncated_by_fusion < 5%`（候选集截断不应是召回失败主因）
- `rerank_token_p95 < 15k`（rerank 成本上界）
- `context_truncation_applied` 比例不显著上升（多路召回不应让下游上下文劣化）

## 12. 实施计划

按收益从高到低分三步走，每步可独立上线、可独立评测、可独立回滚。

### Step 1（最小可用，预计 1 周）

- 0004 迁移：`rag_chunk_enrichment` + `rag_terminology`。
- `DocumentProfileInferrer`：自动推断 document profile（零 LLM 成本），写入 `rag_documents.metadata.document_profile`。
- `HeuristicEnrichmentGenerator`：利用 document profile 的 `key_entities` 提升 concepts / entities 精度 + 租户词典加载。
- Ingest 阶段挂上 document profile 推断 + enrichment（仅 `breadcrumb / concepts / aliases / intent_tags`，无 HyQE）。
- 召回：新增 `bm25_enrichment` 一路 + 原 `vector_content` 共两路，Best-Rank-Protected RRF 融合。
- rerank metadata header 启用。
- 接入 M7 评测，跑 baseline → step1 对比。

**单这一步**就能解决 60%+ 的「术语 vs 口语 / 别称」场景，且零 LLM 成本（仅 BM25 + 词典）。

### Step 2（加 HyQE 与 Query Enricher，预计 1~2 周）

- `LLMEnrichmentGenerator`（三层 context prompt）+ HyQE / concept 向量。
- `QueryEnricher`（LLM 概念化、意图分类、动态 lane 选择）。
- 新增 `vector_hyqe / vector_concept / bm25_content` 三路。
- KB 级 lane 权重覆盖。
- 评测对比 step1。

### Step 3（治理闭环 + 增量 backfill，预计 1 周）

- 词典 CRUD API + 管理 UI 入口（含 document profile 覆盖入口）。
- 增量 backfill 服务（含 document profile 变更触发 enrichment 重生成）。
- 失败样例反向写入 `enrichment_revision_requests`。
- 治理报告：低质量 enrichment 列表、长期未命中 chunk、高频 alias miss。

## 13. 与现有架构边界检查

- ✅ 不绕过 `VectorStoreAdapter`：所有向量召回都通过 adapter 的 `search(lane=...)` 接口路由，业务层不直连 pgvector。
- ✅ 不绕过权限过滤：每路 lane 都用 `FilterBuilder.build()` 重新构造 filter。
- ✅ 不让 prompt 决定权限：`QueryEnrichment` 只产生 score 信号，不参与 filter。
- ✅ 不产生“没法回溯到 chunk”的虚拟召回：HyQE / concept 索引行通过 `chunk_id` 强绑定到 `rag_chunks`。
- ✅ 兼容多 embedding 模型：新增向量列命名沿用 `embedding_<model_slug>` 约定；切换模型时主向量列与 enrichment 向量列分别迁移。
- ✅ 不破坏 parent 回查：HyQE 命中的 chunk 仍走 `ParentExpander`，small-to-big 不变。
- ✅ 不破坏引用：references 不展示 enrichment 文本。
- ✅ 兼容 M6 KB 治理：enrichment 与词典都按 `(tenant_id, knowledge_base_id)` 范围管理。

## 14. 已知风险与缓解

| 风险 | 缓解 |
| --- | --- |
| LLM 生成 HyQE 出现幻觉（编造 chunk 里没有的事实） | quality_score 中加入「问题 vs chunk 相似度」检查；硬幻觉用「问题包含的实体是否出现在 chunk」规则二次过滤 |
| HyQE 同质化（5 条问题几乎相同） | quality_score 中加入「问题间相似度方差」；过低则降级到 heuristic 兜底 |
| LLM 成本爆炸 | 单 chunk 1 次 LLM 调用上限；input token 截断；按 KB 灰度开启；提供 `enrichment_generator=heuristic` 兜底 |
| 词典 alias 误展开（如 “Apple” 同时是公司和水果） | alias 命中需要在 chunk 上下文里能被 BM25 同时命中 canonical 或同 domain entity；alias 只在 BM25 路与 `vector_hyqe` 路生效，不影响主向量 |
| 多路召回导致延迟劣化 | 5 路并发；每路独立 timeout；超时降级；监控 lane-level p95 延迟 |
| HyQE 向量喧宾夺主，导致引用对不上 | rerank 输入只放 chunk 原文 + breadcrumb header，不放 HyQE；最终引用只用 chunk 原文 |
| 某 lane 的 rank=1 真 hit 被多路平庸推荐稀释（融合阶段死） | Best-Rank-Protected RRF（§6.4.1）的 `max` 部分主导；`top_n_per_lane_protected=3` 每路保送（§6.4.2）；监控 `fusion_displacement_rate` |
| 候选集变大让 rerank 阶段 calibration 漂移 / 拒答阈值误触发 / 下游 context 被污染 | §6.4.3 四层耦合成本分析；`top_k_after_fusion=30` 与 baseline 持平；`rerank_token_budget=15k` 作为硬约束；动态 lane 数（§6.4.6）让平均候选数 ≈ 20 |
| 多路召回需要扩大 rerank 输入才能兑现覆盖优势 | 误解。多路覆盖在 parent expansion 阶段通过「同 parent 合并」兑现为「置信度提升」而非「上下文膨胀」（§6.4.5） |
| 业务知识硬编码到代码 / prompt | 业务知识只允许沉淀到 `domain_profile`、`document_profile`（自动推断 + 可选覆盖）、`rag_terminology`、M7 评测集（§3.3）；代码评审拒绝在 generator / enricher 里 hardcode 行业术语 |
| Document profile 自动推断偏差（错判文档类型导致 HyQE 偏题） | `DOCUMENT_TYPE_PERSPECTIVE_MAP` 可配置；admin 可在文档详情覆盖 `metadata.document_profile` 并触发 backfill；M7 硬样本子集跟踪 per-doc-type HyQE 质量 |
| 高度结构化 query（含 SKU、订单号、字段名）被多路误展开 | `QueryEnricher` 检测结构化特征自动退化为单路 `vector_content`（§6.4.4） |
| enrichment 滞后于 chunk 更新 | enrichment 与 chunk 同步打 superseded / deleted；查询默认 `status='active'` 过滤；后台 backfill 任务恢复 |
| 多租户词典污染（A 租户的 alias 误用到 B 租户） | 词典强制 tenant 隔离，召回路径 filter 中 tenant_id 二次校验；新增 `test_enrichment_permission.py` 验证 |

## 15. 决策清单（需要你拍板）

下面 4 个问题我都给了**推荐答案**和**理由**。如果你同意推荐，回一句“按推荐走”即可；想改请直接指出哪条改成什么。

### Q1. enrichment 生成方式

> 离线为每个 child chunk 生成 HyQE / concepts / entities / aliases，最贵的部分是 LLM 调用。

- A. 全部用 LLM（每 chunk 1 次调用，质量最好，成本最高）
- B. 全部规则（heuristic + 词典，成本零，HyQE 那一路基本失效）
- **C. 推荐：分两步上线。Step 1 只跑规则 + 词典，Step 2 再加 LLM。这样能快速验证「metadata 比 embedding 更关键」的假设，再为 HyQE 的 LLM 成本买单。**

### Q2. enrichment 存储模型

- A. 直接把字段加到 `rag_chunks`（hot path 风险，alter 重）
- B. 把 HyQE 当独立“问题型 chunk”插入 `rag_chunks`（召回路径乱，引用语义破坏）
- **C. 推荐：独立表 `rag_chunk_enrichment`（1 chunk : 1 enrichment），新增向量列与 chunk 完全解耦。HyQE 单向量 + BM25 兜底多样性。**

### Q3. 别称词典维护

- A. 不引入词典，全靠 LLM 推断
- **B. 推荐：引入 `rag_terminology` 表，租户级 + KB 级双层；初版用「ingest 后聚合候选 → admin 审定入库」，运营人工补录与 M7 反向学习作为 P1 / P2。这是本设计 ROI 最高的部分。**
- C. 只引入租户全域词典，不做 KB 级

### Q4. 落地范围与节奏

- A. 一次性发完整三步
- **B. 推荐：先发 Step 1（heuristic + 词典 + bm25_enrichment lane + rerank header），跑一轮 M7 评测看 Recall@50 提升；确认提升后再上 Step 2（LLM + HyQE + QueryEnricher）；最后 Step 3 做治理闭环。**
- C. 只发 Step 1 + Step 2，不做治理闭环（不推荐，否则词典会随时间烂掉）

---

## 16. 完成定义

本 spec 作为 M4.5（在 M4 与 M7 之间的质量增强子里程碑）落地完成需要：

- 0004 迁移建立 `rag_chunk_enrichment` 与 `rag_terminology`。
- ingest 路径包含 document profile 自动推断（零 LLM），并在不影响主链路成功率的前提下生成 enrichment，失败可降级。
- 召回路径默认开启多路召回 + RRF + rerank header。
- 至少有一份带 `eval_run_id` 的评测报告：硬样本子集 Recall@50 相对 baseline 提升 ≥ 20%；CitationAccuracy 不降；PermissionLeakage 仍为 0。
- 跨租户测试：词典与 enrichment 跨租户不可见。
- 配置可以一键 `enrichment_enabled=False` 回退到 M4 主链路。
