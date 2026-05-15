# M6 知识库与文件治理平台设计 Spec

## 背景与目标

M6 的目标是把 M5 已交付的 Knowledge API 与测试台升级为“AI 知识治理平台”的第一版管理闭环。M5 已经能上传文档、触发 ingest/backfill、检索、组装上下文、返回 references 和可选答案；但它仍然以 `tenant_id + source_uri` 管理文档，缺少平台用户真正需要的一级对象：知识库。

从 M6 开始，`KnowledgeBase` 是 RecallForge 的核心产品资源。文档、版本、导入任务、检索请求、评测、权限策略、审计事件和管理 UI 都必须可以归属到具体知识库。M6 的重点不是增加更多检索算法，而是补齐“谁在管理哪一批知识、这些知识是否可用、可控、可追溯、可治理”的平台能力。

M6 的范围严格限定在“知识库与文件治理平台”这一层：

- M6 **负责**知识库 CRUD、单库文件列表与文件 CRUD、知识库级检索范围、知识库成员与应用授权、导入任务中心、文件版本恢复、reindex 入口、审计日志和最小管理 UI。
- M6 **复用** M2 的 ChunkFlow ingest、M3 的 embedding / VectorStoreAdapter、M4 的 RetrievalService、M5 的 HTTP / auth / KnowledgeService 边界，不重写 RAG 主链路。
- M6 **不负责**完整 RAG 评测指标体系、质量评分算法和治理分析看板的深度实现。这些进入 M7，但 M6 需要为其预留 `knowledge_base_id`、审计、任务和统计字段。
- M6 **不引入**上层 Agent Runtime，也不允许 Agent 平台直接访问数据库、向量列、repository 或 `VectorStoreAdapter`。上层应用只能通过服务 API 或受控 SDK 调用知识库能力。

设计优先级遵循 RecallForge 的北极星：召回质量、引用可追溯、权限隔离和可诊断性优先。M6 可以接受后台任务较慢、管理 UI 初版朴素，但不能接受跨知识库越权、删除后仍可召回、文件状态不可解释、任务失败不可追踪或知识库范围被用户 prompt 扩大。

## 交付物清单

| 交付物 | 优先级 | 来源拆解 | M6 验收口径 |
| --- | --- | --- | --- |
| 知识库数据模型 | P0 | ROADMAP M6 | 新增 `rag_knowledge_bases`，知识库成为一级资源；状态、owner、默认策略、统计摘要可查询 |
| 文档归属知识库 | P0 | ROADMAP M6 | `rag_documents`、`rag_parent_chunks`、`rag_chunks`、`rag_ingest_jobs`、`rag_query_logs` 均可追溯 `knowledge_base_id` |
| 知识库 CRUD API | P0 | 用户要求 | 支持创建、列表、详情、更新、归档/删除知识库；所有操作强制 tenant 与成员权限校验 |
| 单库文件列表 API | P0 | 用户要求 | `GET /api/knowledge-bases/{kb_id}/documents` 支持分页、筛选、排序和状态摘要 |
| 单库文件 CRUD API | P0 | 用户要求 | 上传、详情、metadata 更新、逻辑删除文件；删除后 active chunks 不再可召回 |
| 知识库级检索边界 | P0 | AGENTS.md 上层接入边界 | retrieve/context/answer 支持服务端校验后的知识库范围；调用方不能越权扩大 `knowledge_base_ids` |
| 文件版本与恢复 | P1 | ROADMAP M6 | 查看版本历史，恢复历史版本生成新的 active 版本，旧版本默认不进入检索 |
| 导入任务中心 | P1 | ROADMAP M6 | 单库任务列表、任务详情、失败重试、批量 reindex 入口 |
| 权限策略模型 | P1 | ROADMAP M6 | 知识库成员角色 owner/admin/editor/viewer/auditor；API Key / 应用授权绑定知识库范围 |
| 审计日志 | P1 | ROADMAP M6 | 记录知识库、文件、权限、任务、检索越权尝试等关键事件 |
| 最小管理 UI | P1 | ROADMAP M6 | 知识库列表、详情、文件列表、文件详情、任务状态、删除/reindex 入口 |
| 治理预留字段 | P2 | ROADMAP M7 衔接 | 为质量报告、低置信度问题、长期未命中、重复/过期知识等治理能力保留数据入口 |
| 边界扫描与权限测试 | P0 | AGENTS.md 不可破坏约束 | route/service 不绕过 IngestService、RetrievalService、VectorStoreAdapter；跨库权限泄漏为 0 |

优先级说明：P0 是平台闭环和安全边界，必须随 M6 完成；P1 是治理可用性，推荐同批完成；P2 是 M7 质量治理的准备工作，可先完成 schema / metadata / 日志入口。

## 设计约束

下列约束作为 M6 评审清单：

- 知识库是一级资源。M6 以后所有新导入文档必须归属一个 `knowledge_base_id`，禁止继续产生无知识库归属的业务文档。
- `knowledge_base_id` 是业务范围字段，不是权限字段本身。服务端必须先校验当前 `RequestContext` 对该知识库的角色或应用授权，再把它注入检索 filters。
- 客户端可以请求限定知识库范围，但不能请求扩大权限范围。传入无权访问的 `knowledge_base_id` 必须拒绝并写审计日志。
- 文档上传、删除、reindex 必须复用 M5/M2/M3 公开服务边界。API 层不能直接写 parent chunk、child chunk、embedding 列或 pgvector SQL。
- 文件删除和知识库删除必须是逻辑删除，并同步失效 parent chunks、child chunks 和向量索引状态。删除后默认检索不能命中相关 active chunks。
- 修改文件标题、业务 metadata、标签等非原文字段不得静默触发重切片；修改影响 chunk、embedding、权限或检索语义的字段必须显式创建 reindex / permission-sync 任务。
- 文件版本恢复不得把旧行直接改回 active。恢复操作必须生成新的 active 文档版本，旧版本保持 `superseded` 或原状态，便于审计。
- 知识库级默认策略不能覆盖凭证身份。`tenant_id`、`user_id`、`department`、`access_level` 仍然来自服务端上下文或凭证 claims。
- 管理 UI 只能调用 M6 API，不直接访问数据库、本地文件系统、repository、向量列或 `VectorStoreAdapter`。
- 审计日志不得泄露跨租户文档正文、chunk 原文、API Key、JWT、数据库 URL 或 provider secret。
- M6 可以新增管理 API，但不能破坏 `/api/knowledge/*` 与 `/api/rag/*` 的 M5 兼容接口；兼容接口若未传知识库范围，应使用服务端默认可见知识库策略。

## 数据模型

M6 需要新增一次数据库迁移。迁移必须通过 Alembic 管理，并配套模型、repository 和测试。

### `rag_knowledge_bases`

知识库主表，表示平台一级知识资源。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK | 知识库 ID |
| `tenant_id` | text | 租户隔离主键 |
| `name` | text | 知识库名称，同租户 active 状态下建议唯一 |
| `description` | text nullable | 描述 |
| `status` | text | `active`、`archived`、`deleted` |
| `owner_user_id` | text | 默认负责人 |
| `default_department` | text | 默认部门策略，导入时可由服务端覆盖 |
| `default_access_level` | text | 默认密级，必须在 `ACCESS_LEVELS` 内 |
| `default_doc_type` | text nullable | 默认文档类型 |
| `default_parser` | text | 默认 `auto` |
| `default_template` | text | 默认 `auto` |
| `default_search_mode` | text | 默认 `vector` |
| `default_top_k` | integer nullable | 知识库级 top_k 上限，不得超过全局配置 |
| `default_final_top_k` | integer nullable | 知识库级 final_top_k 上限 |
| `embedding_model` | text nullable | 默认 embedding 模型，空值表示使用全局当前配置 |
| `reranker_model` | text nullable | 默认 reranker 模型，空值表示使用全局当前配置 |
| `tags` | text[] | 标签 |
| `metadata` | jsonb | 业务扩展字段 |
| `created_by` | text | 创建人 |
| `updated_by` | text | 最近更新人 |
| `created_at` | timestamptz | 创建时间 |
| `updated_at` | timestamptz | 更新时间 |
| `deleted_at` | timestamptz nullable | 删除时间 |

建议索引：

```sql
CREATE INDEX idx_rag_kb_tenant_status_updated
ON rag_knowledge_bases (tenant_id, status, updated_at DESC);

CREATE UNIQUE INDEX uq_rag_kb_tenant_active_name
ON rag_knowledge_bases (tenant_id, name)
WHERE status = 'active';
```

### `rag_knowledge_base_members`

知识库成员与角色表。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK | 成员记录 ID |
| `tenant_id` | text | 租户 |
| `knowledge_base_id` | bigint FK | 知识库 |
| `principal_type` | text | `user`、`department`、`service`、`application` |
| `principal_id` | text | 用户 ID、部门 ID、服务账号或应用 ID |
| `role` | text | `owner`、`admin`、`editor`、`viewer`、`auditor` |
| `created_by` | text | 授权人 |
| `created_at` | timestamptz | 创建时间 |

角色语义：

| 角色 | 权限 |
| --- | --- |
| `owner` | 全部管理能力，包含删除知识库和转移 owner |
| `admin` | 管理成员、策略、文件、任务，不可转移 owner |
| `editor` | 上传、更新 metadata、删除文件、触发 reindex |
| `viewer` | 检索、问答、查看文件列表与详情 |
| `auditor` | 查看审计、任务、评测和质量报告，不可读取完整文档正文，除非另有 viewer 权限 |

### `rag_application_grants`

可选但建议在 M6 落地，用于上层应用或 API Key 访问知识库范围。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK | 授权记录 ID |
| `tenant_id` | text | 租户 |
| `application_id` | text | 应用或服务账号标识 |
| `knowledge_base_id` | bigint FK | 授权知识库 |
| `scopes` | text[] | `knowledge:read`、`knowledge:answer`、`documents:write` 等 |
| `status` | text | `active`、`revoked` |
| `expires_at` | timestamptz nullable | 过期时间 |
| `created_by` | text | 创建人 |
| `created_at` | timestamptz | 创建时间 |

M6 可以先把 API Key 到知识库的映射保留在配置中，但服务层必须抽象为 `ApplicationGrantRepository` 或等价接口，避免后续迁移时改动 route 逻辑。

### `rag_audit_events`

审计事件表。M6 至少记录结构化 metadata，不存储文档正文。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK | 事件 ID |
| `event_id` | uuid | 事件 UUID |
| `tenant_id` | text | 租户 |
| `knowledge_base_id` | bigint nullable | 知识库 |
| `document_id` | bigint nullable | 文档 |
| `job_id` | uuid nullable | 任务 |
| `request_id` | uuid nullable | 请求 |
| `actor_user_id` | text | 操作者 |
| `actor_type` | text | `user`、`api_key`、`service`、`dev` |
| `action` | text | 例如 `kb.create`、`document.delete`、`retrieval.forbidden_kb` |
| `resource_type` | text | `knowledge_base`、`document`、`job`、`permission`、`retrieval` |
| `resource_id` | text nullable | 资源 ID |
| `outcome` | text | `success`、`denied`、`failed` |
| `metadata` | jsonb | 结构化诊断，不含正文和 secret |
| `created_at` | timestamptz | 创建时间 |

建议索引：

```sql
CREATE INDEX idx_rag_audit_tenant_kb_created
ON rag_audit_events (tenant_id, knowledge_base_id, created_at DESC);

CREATE INDEX idx_rag_audit_tenant_action_created
ON rag_audit_events (tenant_id, action, created_at DESC);
```

### 现有表扩展

M6 推荐在以下表增加 `knowledge_base_id`：

- `rag_documents`
- `rag_parent_chunks`
- `rag_chunks`
- `rag_ingest_jobs`
- `rag_query_logs`

迁移策略：

1. 新增 `rag_knowledge_bases`。
2. 为历史数据创建一个每租户默认知识库，例如 `Default Knowledge Base`。
3. 给现有表添加 nullable `knowledge_base_id`。
4. 按 `tenant_id` 回填默认知识库 ID。
5. 对新写入路径强制非空。
6. 在确认回填完整后，将关键表 `knowledge_base_id` 改为 non-null。

索引建议：

```sql
CREATE INDEX idx_rag_documents_kb_status_updated
ON rag_documents (tenant_id, knowledge_base_id, status, updated_at DESC);

CREATE INDEX idx_rag_chunks_kb_permission_active
ON rag_chunks (tenant_id, knowledge_base_id, department, access_level, doc_type, status, version);

CREATE INDEX idx_rag_ingest_jobs_kb_status_created
ON rag_ingest_jobs (tenant_id, knowledge_base_id, status, created_at DESC);

CREATE INDEX idx_rag_query_logs_kb_created
ON rag_query_logs (tenant_id, knowledge_base_id, created_at DESC);
```

## API 设计

M6 新增管理 API 使用 `/api/knowledge-bases` 前缀。所有 endpoint 必须复用 M5 鉴权和 `RequestContext` 注入。

### 知识库 API

#### `POST /api/knowledge-bases`

创建知识库。

请求体：

```json
{
  "name": "产品知识库",
  "description": "产品资料、FAQ 和发布说明",
  "tags": ["product", "faq"],
  "default_department": "product",
  "default_access_level": "internal",
  "default_doc_type": "markdown",
  "default_parser": "auto",
  "default_template": "auto",
  "default_search_mode": "vector",
  "metadata": {
    "owner_team": "product-ops"
  }
}
```

响应：

```json
{
  "knowledge_base_id": 101,
  "name": "产品知识库",
  "status": "active",
  "trace_id": "..."
}
```

约束：

- 需要 `knowledge_bases:write` scope 或租户管理员等价权限。
- `tenant_id`、`owner_user_id`、`created_by` 来自 `RequestContext`，请求体不得传入。
- `default_access_level` 必须在允许枚举内。

#### `GET /api/knowledge-bases`

查询当前身份可见知识库列表。

查询参数：

| 参数 | 说明 |
| --- | --- |
| `status` | 默认 `active`，可选 `active`、`archived`、`deleted`，查看 deleted 需要 admin/auditor |
| `tag` | 标签筛选 |
| `owner_user_id` | 负责人筛选，仅管理员可跨 owner 查询 |
| `q` | 名称/描述模糊搜索 |
| `limit` / `cursor` | 分页 |

响应项至少包含：

- `knowledge_base_id`
- `name`
- `description`
- `status`
- `tags`
- `role`
- `document_count`
- `active_chunk_count`
- `last_ingest_status`
- `last_query_at`
- `updated_at`

#### `GET /api/knowledge-bases/{kb_id}`

返回知识库详情。

详情需要包含：

- 基本字段。
- 当前调用者角色。
- 文档统计：active、superseded、deleted、failed ingest。
- chunk 统计：parent、child、embedding missing、embedding model 分布。
- 最近导入任务摘要。
- 最近查询摘要。
- 默认策略。
- 可执行操作列表，例如 `can_upload`、`can_delete`、`can_manage_members`。

#### `PATCH /api/knowledge-bases/{kb_id}`

更新知识库。

允许更新：

- `name`
- `description`
- `tags`
- `default_department`
- `default_access_level`
- `default_doc_type`
- `default_parser`
- `default_template`
- `default_search_mode`
- `default_top_k`
- `default_final_top_k`
- `metadata`

约束：

- 需要 owner/admin。
- 更新默认策略只影响后续导入，不静默改写历史文档权限。
- 如果需要批量同步历史文档权限，必须创建显式 permission-sync 或 reindex 任务。

#### `DELETE /api/knowledge-bases/{kb_id}`

逻辑删除或归档知识库。

请求体：

```json
{
  "mode": "archive",
  "reason": "业务线下线"
}
```

`mode`：

- `archive`：知识库不可再写入，默认检索不命中；文件和 chunks 保留。
- `delete`：知识库和关联文档标记 deleted，chunks 失效，向量删除同步。

约束：

- 需要 owner。
- `delete` 必须创建后台任务；任务完成前知识库状态可为 `deleting` 或 metadata 标记删除中。
- 删除必须写审计日志。

### 单库文件 API

#### `GET /api/knowledge-bases/{kb_id}/documents`

单库文件列表。

查询参数：

| 参数 | 说明 |
| --- | --- |
| `status` | `active`、`superseded`、`deleted`，默认 `active` |
| `doc_type` | 文档类型 |
| `source_uri` | 精确或前缀筛选，具体行为需稳定 |
| `version` | 版本 |
| `created_by` | 上传人 |
| `has_warnings` | 是否有解析警告 |
| `embedding_status` | `complete`、`missing`、`partial`、`failed` |
| `q` | title / source_name / source_uri 搜索 |
| `sort` | `updated_at_desc`、`created_at_desc`、`source_uri_asc` |
| `limit` / `cursor` | 分页 |

响应项：

```json
{
  "document_id": 123,
  "source_uri": "docs/product/faq.md",
  "source_name": "faq.md",
  "title": "产品 FAQ",
  "doc_type": "markdown",
  "version": 3,
  "status": "active",
  "content_hash": "64hex...",
  "department": "product",
  "access_level": "internal",
  "parent_chunk_count": 12,
  "child_chunk_count": 86,
  "embedding_status": "complete",
  "last_ingest_job_id": "...",
  "last_ingest_status": "success",
  "warning_count": 0,
  "created_by": "user-a",
  "updated_by": "user-a",
  "created_at": "...",
  "updated_at": "..."
}
```

文件列表不得返回 chunk 原文。需要查看片段诊断时应走文件详情下的受控 debug endpoint，且需要 editor/auditor 权限。

#### `POST /api/knowledge-bases/{kb_id}/documents`

向指定知识库上传文档。

行为：

- 复用 M5 `POST /api/knowledge/documents` 的上传和 ingest 逻辑。
- `knowledge_base_id` 由 URL 路径注入，不接受 form 或 metadata 覆盖。
- 默认 `department`、`access_level` 可来自知识库默认策略，但仍需经过服务端权限策略计算。
- hash 去重范围应为 `(tenant_id, knowledge_base_id, source_uri)`，避免不同知识库的同名文件互相影响。

响应沿用 M5 `DocumentIngestResponse`，并增加 `knowledge_base_id`。

#### `GET /api/knowledge-bases/{kb_id}/documents/{document_id}`

文件详情。

返回：

- 文档主信息。
- 最新导入任务。
- 版本信息。
- parent / child chunk 计数。
- embedding model 分布与缺失数。
- parse warnings、parse_report 摘要。
- references 字段摘要：页码范围、heading_path 覆盖。
- 可执行操作列表。

默认不返回完整 chunk 原文。

#### `PATCH /api/knowledge-bases/{kb_id}/documents/{document_id}`

更新文件 metadata。

允许更新：

- `title`
- `source_name`
- `doc_type`
- `metadata`
- `department`
- `access_level`

约束：

- `department`、`access_level` 更新属于权限变更，需要 editor/admin 且写审计日志。
- 权限字段更新必须同步 parent chunks、child chunks 的权限字段；不需要重新 embedding，但需要确保后续检索 filter 使用新权限。
- `doc_type` 更新可能影响检索过滤和治理统计，不重切片；如果业务希望按新模板重切片，必须显式 reindex。

#### `DELETE /api/knowledge-bases/{kb_id}/documents/{document_id}`

逻辑删除文件。

响应：

```json
{
  "document_id": 123,
  "knowledge_base_id": 101,
  "status": "deleted",
  "parent_chunk_count": 12,
  "child_chunk_count": 86,
  "vector_delete_status": "succeeded",
  "trace_id": "..."
}
```

约束：

- 删除必须校验 `document_id` 属于 `kb_id` 和当前 `tenant_id`。
- 必须调用 `VectorStoreAdapter.delete_by_document_id(document_id, tenant_id)` 或等价服务层方法，不得只改 `rag_documents.status`。
- 删除失败时不能静默吞掉向量删除错误；若 Postgres 状态已删除但外部向量库删除失败，必须写任务和审计，进入可恢复状态。

### 文件版本 API

#### `GET /api/knowledge-bases/{kb_id}/documents/{document_id}/versions`

返回同一 `(tenant_id, knowledge_base_id, source_uri)` 下的所有版本。

字段：

- `document_id`
- `version`
- `status`
- `content_hash`
- `created_by`
- `created_at`
- `parent_chunk_count`
- `child_chunk_count`
- `embedding_status`
- `restore_available`

#### `POST /api/knowledge-bases/{kb_id}/documents/{document_id}/restore-version`

请求体：

```json
{
  "source_document_id": 122,
  "reason": "恢复上一版正确内容"
}
```

行为：

- `source_document_id` 必须属于同一知识库和同一 `source_uri`。
- 恢复生成新版本，不复用旧版本号。
- 新版本 active 后，旧 active 版本标记 `superseded`。
- 需要同步或复用 embedding，并写审计日志。

### 任务 API

#### `GET /api/knowledge-bases/{kb_id}/ingest-jobs`

返回单库导入任务列表。

支持筛选：

- `status`
- `source_uri`
- `document_id`
- `created_by`
- `created_at_from`
- `created_at_to`

#### `POST /api/knowledge-bases/{kb_id}/ingest-jobs/{job_id}/retry`

重试失败任务。

约束：

- 只能重试 `failed`，不能重试 `success` 或 `skipped_duplicate`。
- 原始上传临时文件若已清理，必须返回 `retry_unavailable_missing_source`，除非系统已持久化原始文件或 source connector 可重新读取。
- 重试应生成新的 `job_id`，并在 metadata 中关联 `retry_of_job_id`。

#### `POST /api/knowledge-bases/{kb_id}/reindex`

批量重建知识库索引。

请求体：

```json
{
  "dry_run": true,
  "document_ids": [123, 124],
  "embedding_model": "text-embedding-v4@1024",
  "force": false,
  "limit": 1000,
  "reason": "切片策略升级"
}
```

M6 初版可只支持创建任务和 dry run 估算；真正 durable worker 可以进入 M8，但 API 和任务记录要先稳定。

### 检索 API 扩展

M6 在 M5 请求 schema 的 `filters` 白名单中加入知识库范围字段：

- `knowledge_base_id`
- `knowledge_base_ids`

处理规则：

1. 用户不传知识库范围：服务端根据当前身份的 viewer/editor/admin 权限选择默认可见知识库集合。若集合为空，返回明确拒绝。
2. 用户传单个或多个知识库 ID：服务端校验当前身份对每个知识库至少有 viewer 权限。
3. 任一知识库无权访问：整个请求拒绝，不做部分成功，写审计日志。
4. 检索 filter 注入 `knowledge_base_id IN (...)` 后再调用 M4 `RetrievalService.retrieve()`。
5. Query log 必须记录最终生效的 `knowledge_base_ids`，用于审计和 M7 评测。

## 模块设计

### 目录结构

```text
recallforge/
  api/
    routes/
      knowledge_bases.py       # M6 知识库与单库文件 API
    schemas.py                 # 新增 M6 request/response schema
    knowledge_service.py       # 复用，并增加 kb-aware ingest/retrieve helper
    governance_service.py      # M6 编排服务
  governance/
    __init__.py
    permissions.py             # 知识库角色、应用授权、可执行操作判定
    audit.py                   # 审计事件写入封装
    tasks.py                   # retry/reindex 任务命令与状态抽象
    stats.py                   # 知识库/文件统计摘要
  storage/
    models.py                  # 新增 KB、member、grant、audit models；扩展现有表字段
    repository.py              # 新增 KnowledgeBaseRepository 等
migrations/
  versions/
    0003_add_knowledge_base_governance.py
tests/
  test_m6_knowledge_base_models.py
  test_m6_knowledge_base_api_schemas.py
  test_m6_knowledge_base_routes.py
  test_m6_document_management.py
  test_m6_kb_permissions.py
  test_m6_retrieval_scope.py
  test_m6_audit_events.py
  test_m6_boundary_scan.py
```

### 服务边界

M6 推荐新增 `GovernanceService`，负责知识库平台操作编排：

```python
class GovernanceService:
    async def create_knowledge_base(self, command, ctx): ...
    async def list_knowledge_bases(self, query, ctx): ...
    async def get_knowledge_base(self, kb_id, ctx): ...
    async def update_knowledge_base(self, kb_id, command, ctx): ...
    async def delete_knowledge_base(self, kb_id, command, ctx): ...
    async def list_documents(self, kb_id, query, ctx): ...
    async def get_document(self, kb_id, document_id, ctx): ...
    async def upload_document(self, kb_id, command, ctx): ...
    async def update_document(self, kb_id, document_id, command, ctx): ...
    async def delete_document(self, kb_id, document_id, command, ctx): ...
    async def list_ingest_jobs(self, kb_id, query, ctx): ...
    async def retry_ingest_job(self, kb_id, job_id, command, ctx): ...
    async def reindex_knowledge_base(self, kb_id, command, ctx): ...
```

边界要求：

- `GovernanceService.upload_document()` 调用 M5 `KnowledgeService.ingest_document()` 或 M2 `IngestService.ingest_document()` 的公开入口，不直接调用 ChunkFlow。
- `GovernanceService.delete_document()` 调用 repository 逻辑删除和 `VectorStoreAdapter.delete_by_document_id()`，并写审计。
- `GovernanceService` 可以调用 repository 做列表、详情、统计和审计，但不能直接拼 pgvector SQL。
- route handler 只做鉴权、schema、依赖注入和响应序列化。

### 权限判定

建议实现 `KnowledgeBasePermissionService`：

```python
class KnowledgeBasePermissionService:
    async def require_role(self, ctx, kb_id, allowed_roles): ...
    async def list_accessible_kbs(self, ctx, action): ...
    async def validate_retrieval_scope(self, ctx, requested_kb_ids): ...
    async def allowed_actions(self, ctx, kb_id): ...
```

动作到角色映射：

| 动作 | 最低角色 |
| --- | --- |
| 查看知识库列表 | viewer |
| 查看文件列表/详情 | viewer |
| 检索/问答 | viewer |
| 上传文件 | editor |
| 更新文件 metadata | editor |
| 删除文件 | editor |
| reindex 文件或知识库 | editor |
| 查看审计 | auditor 或 admin |
| 管理成员 | admin |
| 更新知识库默认策略 | admin |
| 归档/删除知识库 | owner |

部门级授权规则：

- `principal_type='department'` 表示当前 `ctx.department` 匹配时获得对应角色。
- 用户级授权优先于部门级授权。
- owner/admin 可以授予或撤销低于自身的角色。

应用授权规则：

- API Key 或 service principal 必须同时满足 scope 和知识库 grant。
- 例如拥有 `knowledge:read` scope 但没有 KB grant，不能检索该知识库。
- 应用 grant 不应自动继承用户部门权限。

## 管理 UI

M6 最小管理 UI 可以继续使用静态 HTML/CSS/JS，不引入前端构建工具。页面应是工作台，不是营销页。

必备视图：

- 知识库列表：名称、状态、角色、文件数、chunk 数、最近导入、最近查询。
- 知识库详情：基础信息、默认策略、统计摘要、可执行操作。
- 文件列表：分页、筛选、状态、版本、chunk 数、embedding 状态、错误摘要。
- 文件详情：metadata、版本、最近任务、warnings、parse_report 摘要。
- 导入任务：单库任务列表、失败原因、重试入口。
- 操作确认：删除知识库、删除文件、reindex 必须二次确认，并显示影响范围。

UI 约束：

- 不允许编辑 `tenant_id`、`user_id`、`department`、`access_level` 等身份字段。
- 权限策略字段可以管理，但必须以角色/策略方式呈现，不暴露可伪造身份输入。
- token 不写入 `localStorage`。
- 所有操作调用 `/api/knowledge-bases/*` 或 M5 兼容 API。
- 删除、重试、reindex 等操作显示 `trace_id` / `job_id`。

## 可观测与审计

M6 必须把平台管理操作纳入可追溯链路。

审计事件至少覆盖：

- `kb.create`
- `kb.update`
- `kb.archive`
- `kb.delete`
- `kb.member.add`
- `kb.member.remove`
- `document.upload`
- `document.update_metadata`
- `document.permission_update`
- `document.delete`
- `document.restore_version`
- `job.retry`
- `kb.reindex_requested`
- `retrieval.forbidden_kb`
- `retrieval.scope_resolved`

审计 metadata 建议包含：

- `before` / `after` 的结构化差异，但不包含文档正文。
- `reason`。
- `requested_knowledge_base_ids`。
- `effective_knowledge_base_ids`。
- `scope`。
- `actor_subject_type`。
- 错误 code。

Query log 扩展：

- `knowledge_base_id` 或 `knowledge_base_ids`。
- `requested_knowledge_base_ids`。
- `effective_knowledge_base_ids`。
- 检索范围解析耗时。

## 配置项

M6 建议新增或确认以下配置：

| 字段 | 默认值 | 用途 |
| --- | --- | --- |
| `default_knowledge_base_name` | `Default Knowledge Base` | 历史数据回填和开发环境默认 KB |
| `require_knowledge_base_scope` | `True` | 新写入是否强制 KB 归属 |
| `allow_implicit_all_accessible_kbs` | `True` | 未传 KB 范围时是否检索所有可见 KB |
| `max_knowledge_bases_per_query` | `20` | 单次检索最多知识库数 |
| `kb_list_default_limit` | `20` | 知识库列表默认分页 |
| `document_list_default_limit` | `50` | 文件列表默认分页 |
| `document_delete_vector_sync_required` | `True` | 删除时向量同步失败是否视为业务失败 |
| `kb_delete_requires_empty` | `False` | 删除知识库是否要求先清空文件 |
| `reindex_max_documents_per_request` | `1000` | 批量 reindex 上限 |
| `audit_enabled` | `True` | 是否写审计日志 |

生产环境不允许关闭 `require_knowledge_base_scope` 和 `audit_enabled`，除非明确处于迁移维护模式。

## 错误语义

统一错误 code：

| 场景 | HTTP | code |
| --- | --- | --- |
| 知识库不存在或不可见 | 404 | `knowledge_base_not_found` |
| 无权查看知识库 | 403 | `knowledge_base_forbidden` |
| 无权执行操作 | 403 | `insufficient_kb_role` |
| 请求了无权检索的知识库 | 403 | `forbidden_knowledge_base_scope` |
| 知识库名称重复 | 409 | `knowledge_base_name_conflict` |
| 文件不属于知识库 | 404 | `document_not_found` |
| 删除后向量同步失败 | 503 | `vector_delete_failed` |
| 重试任务源文件不可用 | 409 | `retry_unavailable_missing_source` |
| reindex 请求超过上限 | 400 | `reindex_limit_exceeded` |
| 知识库正在删除或归档 | 409 | `knowledge_base_not_writable` |

错误响应不得暴露跨租户资源是否存在的细节。对当前身份不可见的资源，默认按 not found 处理；明确越权字段或越权 KB scope 需要写审计。

## 测试策略

### 单元测试 `tests/test_m6_knowledge_base_models.py`

- migration 从空库创建 M6 新表和字段。
- `rag_knowledge_bases.status` 只接受合法状态。
- 同一租户 active 知识库名称唯一。
- 历史数据回填默认知识库后，现有 document/chunk/job/query log 均有 `knowledge_base_id`。

### 单元测试 `tests/test_m6_knowledge_base_api_schemas.py`

- 创建知识库请求体不接受 `tenant_id`、`owner_user_id`、`created_by`。
- `default_access_level` 非法值被拒绝。
- 文件 PATCH 不接受 `content_hash`、`embedding_model`、`status` 等服务端字段。
- retrieve/context/answer filters 支持合法 `knowledge_base_id` / `knowledge_base_ids`。
- filters 中混入 `tenant_id='*'` 仍被拒绝。

### 单元测试 `tests/test_m6_kb_permissions.py`

- viewer 可以检索和查看文件列表，不能上传。
- editor 可以上传、更新文件 metadata、删除文件、触发 reindex。
- admin 可以更新知识库策略和成员。
- owner 可以归档/删除知识库。
- department principal 匹配 `ctx.department` 时获得角色。
- API Key 必须同时满足 scope 和 KB grant。
- 无权 KB scope 被拒绝且写审计。

### 单元测试 `tests/test_m6_document_management.py`

- 单库文件列表只返回当前 KB 的文档。
- 文件详情校验 `tenant_id + knowledge_base_id + document_id`。
- 文件删除同步标记 document、parent chunks、child chunks 为 deleted。
- 文件删除调用 `VectorStoreAdapter.delete_by_document_id()`。
- PATCH 权限字段同步 parent/child chunks。
- 版本恢复生成新的 active 版本，旧 active 标记 superseded。

### 单元测试 `tests/test_m6_retrieval_scope.py`

- 未传 KB 范围时解析为当前用户可见 KB 集合。
- 传入一个合法 KB 时，只检索该 KB。
- 传入多个合法 KB 时，filters 注入集合。
- 传入任一无权 KB 时整个请求拒绝，不调用 `RetrievalService.retrieve()`。
- query log 记录 requested 和 effective KB scope。

### 单元测试 `tests/test_m6_audit_events.py`

- 创建、更新、删除知识库写审计。
- 上传、删除、恢复文件写审计。
- 权限变更写审计。
- 越权 KB 检索尝试写审计。
- 审计 metadata 不包含文档正文、API key、JWT、数据库 URL。

### 边界扫描 `tests/test_m6_boundary_scan.py`

- `recallforge/api/routes/knowledge_bases.py` 不 import `PgVectorStore`。
- route handler 不调用 `.search(`、`.embed_query(`、`parse_to_chunk_package`。
- 文件上传路径复用 `KnowledgeService` / `IngestService`，不直接写 chunks。
- 文件删除通过 service 层调用 vector delete，不在 route 层拼 SQL。
- 管理 UI JS 不出现可编辑 `tenant_id`、`user_id`、`department`、`access_level` 输入字段。

### 端到端 smoke test

建议新增 `tests/e2e/test_m6_knowledge_base_governance_smoke.py`：

1. 使用 fake auth 创建两个知识库 A/B。
2. 向 A 上传 Markdown，向 B 上传另一份 Markdown。
3. 查询 A 文件列表，只看到 A 文件。
4. 用 viewer 权限检索 A，返回 A references。
5. 尝试检索 B，无权限时返回 403，且不调用 retrieval。
6. 删除 A 文件后再次检索 A，不再命中该文件。
7. 查看审计日志，包含 create/upload/retrieval/delete/forbidden 事件。

## 实现文件清单

| 路径 | 职责 |
| --- | --- |
| `docs/M6-design.md` | 本 spec |
| `migrations/versions/0003_add_knowledge_base_governance.py` | 新表、字段、索引、历史数据回填 |
| `recallforge/storage/models.py` | KB、member、grant、audit SQLAlchemy models；现有表增加 `knowledge_base_id` |
| `recallforge/storage/repository.py` | `KnowledgeBaseRepository`、`KnowledgeBaseMemberRepository`、`AuditEventRepository`、文档列表/详情查询 |
| `recallforge/governance/permissions.py` | KB 权限判定 |
| `recallforge/governance/audit.py` | 审计封装 |
| `recallforge/governance/tasks.py` | retry/reindex 命令与任务抽象 |
| `recallforge/governance/stats.py` | 知识库与文件统计摘要 |
| `recallforge/api/routes/knowledge_bases.py` | M6 API routes |
| `recallforge/api/schemas.py` | M6 request/response schema |
| `recallforge/api/app.py` | 注册 M6 router |
| `recallforge/api/knowledge_service.py` | kb-aware ingest/retrieve 衔接 |
| `recallforge/console/static/*` | 最小管理 UI 扩展或新增页面 |
| `tests/test_m6_*.py` | 单元、边界和 smoke 测试 |

## 已知限制

- M6 初版可以不实现 durable worker。retry/reindex API 可以先创建任务记录或同步执行小批量任务，但必须有稳定状态和审计。
- M6 初版可以不做复杂质量评分；质量治理指标进入 M7。
- M6 初版不实现跨知识库智能路由策略；未传 KB 范围时使用“当前身份可见知识库集合”或配置控制的默认集合。
- M6 初版不实现完整成员管理 UI；但成员/授权模型和 API 边界应先稳定。
- M6 初版不持久化原始上传文件时，失败任务重试可能不可用，必须返回明确错误。
- M6 初版不引入独立 RBAC 系统；知识库角色可以先建立在现有 JWT/API Key claims 与数据库成员表之上。

## M6 完成定义

- 可以创建、查看、更新、归档或删除知识库。
- 新导入文档必须归属知识库。
- 可以查询单库文件列表，列表包含状态、版本、chunk 数、embedding 状态、最近任务和错误摘要。
- 可以查看单个文件详情、更新 metadata、删除文件。
- 删除文件或知识库后，相关 active chunks 不再可召回。
- 可以查看文件版本历史，并恢复历史版本为新的 active 版本。
- 可以查询单库导入任务，失败任务有明确原因和可重试语义。
- 检索请求可以限定知识库范围，且无权 KB 会被拒绝并写审计。
- API Key / 上层应用授权不能绕过知识库范围限制。
- 审计日志覆盖知识库、文件、任务、权限和越权检索事件。
- 最小管理 UI 可以完成知识库列表、文件列表、文件详情、任务状态和删除/reindex 操作入口。
- 边界扫描证明 M6 API 没有绕过 IngestService、RetrievalService、VectorStoreAdapter 或服务端权限过滤。
- 跨知识库权限泄漏评测为 0。
