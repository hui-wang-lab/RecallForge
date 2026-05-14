# M2 ChunkFlow 入库设计 Spec

## 背景与目标

M2 的目标是把文档解析、结构化切片和 parent/child 映射在 RecallForge 内跑通：把 ChunkFlow 的解析与切片能力作为唯一默认引擎迁入 `recallforge/chunking/`，并在 `recallforge/ingest/` 编排出端到端的导入流水线，使任意被允许的源文件能落到 M1 的 `rag_documents`、`rag_parent_chunks`、`rag_chunks`、`rag_ingest_jobs` 四张表。

M2 的范围严格限定在"原文 + 元数据 + 任务诊断"这一层：

- M2 **只**负责 parent/child 原文、metadata、`content_hash`、版本和任务状态入库。
- M2 **不**生成 embedding，`rag_chunks.embedding_text_embedding_v4_1024` 列保持 `NULL`，由 M3 通过 `VectorStoreAdapter` 回填。
- M2 **不**实现 `VectorStoreAdapter`、不实现答案生成、不实现检索 API。这些边界全部由 M3、M4、M5 接管。
- M2 **不**覆盖 DOCX / JSON 格式的导入。这两种格式在 ROADMAP M1 `rag_documents.doc_type` 和 AGENTS.md 中列出，但 ChunkFlow 当前没有成熟的 DOCX / JSON 解析路径，且它们不属于 ROADMAP M2 验收标准。DOCX / JSON 的支持延后到 M8 增强解析器阶段，届时可能引入 Docling / MinerU 等增强解析能力一并覆盖。

设计优先级遵循 RecallForge 的北极星：召回质量、引用可追溯、权限隔离和可诊断性优先于吞吐。导入路径上的每一次失败、降级和跳过都必须能够事后复盘。

## 交付物清单

| 交付物 | 优先级 | 来源拆解 | M2 验收口径 |
| --- | --- | --- | --- |
| ChunkFlow 核心模块迁移 | P0 | `chunkflow/core/`、`ir/`、`parsers/`、`chunkers/`、`postprocess/`、`tokenizer.py`、`pdf_parser.py`、`schema.py` | 全部迁移到 `recallforge/chunking/`，import 路径以 `recallforge.chunking.*` 为唯一入口，旧 `chunkflow.app`/`static`/`chunking.py` 兼容入口不迁入 |
| 统一 `parse_to_chunk_package()` 入口 | P0 | [docs/chunkflow_migration.md](chunkflow_migration.md) "目标调用入口" | `recallforge.chunking.core.pipeline.parse_to_chunk_package(file_path, PipelineConfig(...))` 是 ingest 层唯一调用入口；业务代码不允许直接 import 具体 parser/chunker |
| 格式支持：Markdown / TXT / PDF | P0 | ROADMAP M2 "支持 Markdown、TXT、PDF 三种格式导入" | `.md`、`.markdown`、`.txt` 走 `text_file`；`.pdf` 走 `pypdf` fallback；三种格式端到端导入可生成 parent/child chunk |
| 格式支持：CSV / TSV 表格路径 | P0 | ROADMAP M2 "支持 CSV / TSV 的表格导入路径" | `.csv`、`.tsv` 走 ChunkFlow `table_file` 路径，表格行被切成结构化 child chunk，可回查 parent |
| `build_chunks_for_ingest()` 适配层 | P0 | AGENTS.md 入库映射 + chunkflow_migration.md 入库映射表 | `recallforge.ingest.chunk_adapter.build_chunks_for_ingest(package, ctx) -> IngestChunks(parent_creates, child_drafts_by_parent_key)`，把 ChunkFlow `ChunkPackage` 字段映射到 M1 repository 的 create 输入草稿 |
| `parse_report` / `warnings` / `parser_used` / `chunker_used` 落库 | P0 | ROADMAP M2 验收 + AGENTS.md "解析降级必须落到 `rag_ingest_jobs`" | 在 `IngestJobRepository.mark_success()` / `mark_failed()` / `mark_skipped_duplicate()` 写入对应 JSONB / TEXT 列；禁止只打印日志 |
| 内容 hash 去重 | P0 | AGENTS.md "版本与重复导入策略" + M1 spec "hash 去重" | hash 命中 active 或 superseded 版本时，`rag_ingest_jobs.status = 'skipped_duplicate'`，不调用 `ParentChunkRepository.bulk_create` / `ChunkRepository.bulk_create` |
| 文档版本策略 | P0 | AGENTS.md "版本与重复导入策略" + M1 spec "版本管理" | hash 不同时：在同一事务内先 `DocumentVersionRepository.supersede_source()`，再 `DocumentRepository.create()` + `next_version()`，旧 active 文档与其 parent/child 全部标记 `superseded` |
| 解析器降级链路 | P1 | AGENTS.md "解析降级必须记录" + chunkflow_migration.md "可选增强" | Docling / MinerU 不可用时自动降级到 `pypdf`，降级链路写入 `parse_report.parser_fallback_chain` 与 `rag_ingest_jobs.warnings`；启动时检测可选 parser 可用性 |
| 导入编排服务 | P1 | AGENTS.md 推荐目录 `recallforge/ingest/` | `IngestService.ingest_document(request) -> IngestJobRecord` 串起：job 创建 → `running` 提交 → 解析 → 切片 → hash 判断 → 版本切换 → 入库 → 状态机推进 |
| 大文件保护与超时 | P2 | AGENTS.md "无 query 拒绝/拒答阈值都必须可配置" + 风险章节 | 单文件大小、单文档 child chunk 上限、解析超时全部可配置；超限触发 `failed` job 并写入 `error_message` 与 `metadata.limit_breached` |

优先级说明：P0 阻塞 M3 embedding 回填和 M5 端到端导入 API，必须随 M2 完成；P1 是 M2 完整性要求；P2 是为 M7 容量边界与 M8 增强解析器预留的健壮性钩子，M2 必须落地配置项与失败路径，但具体阈值默认值可继承 M1 配置。

## 设计约束

下列约束写入 spec 一次，并作为 M2 评审清单：

- 所有模型相关字段必须通过配置注入，**禁止**在 ingest / chunking 业务代码中硬编码厂商或模型名。`rag_chunks.embedding_provider` / `embedding_model` / `embedding_dim` 必须从 `EmbeddingProvider` 与列映射推导（详见 M1 ADR-0001），M2 不允许在 `ChildChunkCreate(...)` 调用点写死 `dashscope` 或 `text-embedding-v4@1024` 字符串。
- `PipelineConfig.child_max_tokens` 默认 `450`；`child_min_tokens` 默认 `80`；二者必须可通过 `Settings.child_max_tokens` / `Settings.child_min_tokens` 注入。
- `PipelineConfig.parent_granularity` 默认 `"chapter"`；必须可通过 `Settings.parent_granularity` 注入。
- 导入任务状态机直接使用 M1 已定义的 `IngestJobRepository` 接口（`create` → `mark_running` → `mark_success` / `mark_failed` / `mark_skipped_duplicate`），不允许在 M2 引入新的终态或绕过 repository 直接 UPDATE。
- `parse_report` 与 `warnings` 必须以 JSONB 结构化落库到 `rag_ingest_jobs.parse_report` / `rag_ingest_jobs.warnings`，**禁止**仅打印日志或塞进自由文本 `error_message`。
- ChunkFlow `ChunkPackage` → Postgres 列的映射只允许在唯一适配层 `recallforge.ingest.chunk_adapter` 中完成；ingest 主流程或 chunking 子模块不得绕过适配层直接构造 `ChildChunkCreate` / `ParentChunkCreate`。
- 同一导入请求里 `rag_documents`、`rag_parent_chunks`、`rag_chunks`、`rag_ingest_jobs` 的状态变更必须在**同一事务**内完成；版本切换、hash 判定、批量 chunk 写入、job 终态写入是同一事务的多个步骤，禁止跨事务分裂。
- `IngestRequest.doc_type` 与 `file_path.suffix` 应保持一致；若两者明显矛盾（例如 `.md` 文件声明 `doc_type="pdf"`），`IngestService` 必须写入 `rag_ingest_jobs.warnings` 一条告警（`{"level": "warning", "message": "doc_type 'pdf' conflicts with file extension '.md'", "source": "ingest_service"}`），但不阻止导入流程。此校验为 P2 级别增强。

## 模块设计

### 目录结构

```text
recallforge/
  chunking/                       # 迁移自 ChunkFlow
    __init__.py
    core/                         # pipeline.py / debug.py / document_type.py / ids.py / snapshot.py
    ir/                           # models.py / normalize.py / section_tree.py / layout_noise.py / validators.py
    parsers/                      # text_file.py / table_file.py / pypdf_fallback.py / docling_pdf.py / mineru_pdf.py / utils.py / base.py
    chunkers/                     # registry.py / generic_structured.py / qa.py / table_data.py / book.py / laws.py / manual.py / paper.py / picture_pdf.py / contract_terms.py / template_utils.py / base.py
    postprocess/                  # boundary_repair.py / media_context.py / overlong_split.py / quality.py / small_chunk_merge.py
    tokenizer.py
    pdf_parser.py
    schema.py
  ingest/
    __init__.py
    chunk_adapter.py              # ChunkPackage → repository create dataclasses
    hashing.py                    # 规范化内容 hash
    pipeline_config.py            # Settings → PipelineConfig 注入
    ingest_service.py             # 编排：job → parse → chunk → dedupe → version → persist
    errors.py                     # IngestError / ParserUnavailable / OversizeError 等
```

不在 M2 范围：`recallforge/embeddings/`、`recallforge/retrieval/`、`recallforge/console/`、`recallforge/api/`。

### `recallforge/chunking/`

迁移后的 ChunkFlow 模块，是 M2 的唯一默认切片引擎，承担文档解析、结构化 IR 构建、parent/child 切片、postprocess 修复和质量度量。

**职责**：

- 暴露 `parse_to_chunk_package(file_path, PipelineConfig) -> ChunkPackage`。
- 提供 `available_parsers() -> dict[str, bool]`，供启动检查使用。
- 维护 parser 优先级（`docling -> mineru -> pypdf`）和自动 fallback 链路；fallback 链路必须被 `ParsedDocument.parser_fallback_chain` 与 `ParseReport.warnings` 记录。
- 暴露 IR 数据结构 `ParentChunk`、`ChildChunk`、`ParseReport`、`ChunkPackage`，作为 ingest 适配层的输入契约。

**接口**：

```python
from recallforge.chunking.core.pipeline import PipelineConfig, parse_to_chunk_package
from recallforge.chunking.ir.models import ChunkPackage, ParentChunk, ChildChunk, ParseReport

package: ChunkPackage = parse_to_chunk_package(
    file_path,
    PipelineConfig(
        parser="auto",
        template="auto",
        child_max_tokens=settings.child_max_tokens,
        child_min_tokens=settings.child_min_tokens,
        parent_granularity=settings.parent_granularity,
        include_blocks=True,
    ),
)
```

**迁移范围**（与 [docs/chunkflow_migration.md](chunkflow_migration.md) 一致）：

- 迁移：`chunkflow/core/`、`chunkflow/ir/`、`chunkflow/parsers/`、`chunkflow/chunkers/`、`chunkflow/postprocess/`、`chunkflow/tokenizer.py`、`chunkflow/pdf_parser.py`、`chunkflow/schema.py`。
- 不迁移：`chunkflow/app.py`（独立 FastAPI UI）、`chunkflow/static/`（前端页面）、`chunkflow/chunking.py`（旧版兼容入口）。
- 迁移时把 import 改为 `recallforge.chunking.*`，移除任何指向 `chunkflow.*` 旧路径的硬引用。

**数据流**：

```text
file_path
   │
   ▼
parsers (text_file | table_file | pypdf | docling | mineru)
   │  ParsedDocument
   ▼
ir.layout_noise → ir.section_tree → ir.validators
   │  ParsedDocument(with section_tree)
   ▼
chunkers (registry → 选择 chunker 模板)
   │  parent_chunks + child_chunks
   ▼
postprocess (boundary_repair → small_chunk_merge → overlong_split → quality)
   │  ChunkPackage(parse_report, warnings, metadata)
   ▼
返回到 recallforge.ingest 层
```

### `recallforge/ingest/`

导入编排层，是 M2 的"业务面"。它把 ChunkFlow 的纯计算输出粘到 M1 repository 上，并维护 job 状态机。

#### `pipeline_config.py`

把 `recallforge.config.Settings` 翻译成 `PipelineConfig`，确保 `child_max_tokens` / `child_min_tokens` / `parent_granularity` 全部经由配置注入。M2 不接受调用方在业务路径里手工构造 `PipelineConfig`，避免默认值漂移。

```python
def build_pipeline_config(settings: Settings, *, parser_hint: str = "auto") -> PipelineConfig:
    return PipelineConfig(
        parser=parser_hint,
        template="auto",
        child_max_tokens=settings.child_max_tokens,
        child_min_tokens=settings.child_min_tokens,
        parent_granularity=settings.parent_granularity,
        include_blocks=True,
    )
```

`parent_granularity` 合法值参考（由 ChunkFlow chunker registry 定义，M2 不穷举所有值，但需明确基线）：

| 值 | 语义 | 适用场景 |
| --- | --- | --- |
| `"chapter"` | 按章节/标题层级切分 parent | 通用文档（默认值） |
| `"section"` | 比章节更细粒度的节切分 | 手册、政策文档 |
| `"document"` | 整个文档作为一个 parent | 短文档、FAQ |
| `"paragraph"` | 按段落切分 parent | 法律条款、合同 |

`Settings.parent_granularity` 的值不在白名单内时，`build_pipeline_config` 不做校验（由 ChunkFlow 在运行时决定是否支持），但 `IngestService` 应在 `warnings` 中记录一条信息性告警。

#### `hashing.py`

```python
def compute_content_hash(canonical_text: str) -> str:
    """Return 64-char lowercase hex SHA-256 of normalized content."""
```

规范化策略（与 M1 hash 去重约束保持一致）：

- 输入是 ChunkFlow 解析后的 `ParsedDocument`，把所有 `Block.text` 按 `reading_order` 串接为单一字符串，再做以下规范化：
  - 统一换行符 `\r\n` / `\r` → `\n`。
  - 去除每行结尾空白。
  - 折叠连续空行到最多两个。
  - 去除 UTF-8 BOM。
  - 不做大小写折叠（保留中文/英文原始 case，避免误判同名异义）。
- 对 table_file 路径，使用 `ParsedDocument.blocks` 中所有表格 cell 的拼接结果作为输入，避免文件级 byte hash 受换行/编码差异影响。
- 输出 `hashlib.sha256(...).hexdigest()`，与 `rag_documents.content_hash`、`rag_chunks.content_hash` 的 64 位小写 hex CHECK 一致。
- child chunk 的 `content_hash` 单独按 child `text` 规范化计算，不复用文档级 hash；这一点用于后续可能的 chunk 级幂等比对。

#### `chunk_adapter.py`

唯一允许把 `ChunkPackage` 映射到 M1 repository 输入的层。

```python
@dataclass
class IngestContext:
    tenant_id: str
    user_id: str | None              # 仅用于审计和 rag_ingest_jobs.created_by，不参与向量 metadata
    source_uri: str
    source_name: str | None
    doc_type: str
    department: str
    access_level: str
    document_version: int
    embedding_provider: str   # 从 EmbeddingProvider 配置注入
    embedding_model: str      # 从 EmbeddingProvider 配置注入
    embedding_dim: int        # 从 EmbeddingProvider 配置注入

@dataclass
class ChildChunkDraft:
    """Adapter output before parent rows exist.

    It carries every ChildChunkCreate field except parent_id; ingest_service
    fills parent_id after ParentChunkRepository.bulk_create() returns.
    """
    parent_key: str
    chunk_key: str
    chunk_index: int
    content: str
    content_hash: str
    doc_type: str
    department: str
    access_level: str
    source_uri: str
    version: int
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    chunk_type: str = "child"
    template: str | None = None
    heading_path: list[str] | None = None
    page_start: int | None = None
    page_end: int | None = None
    embedding_metadata: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_create(self, parent_id: int) -> ChildChunkCreate: ...

@dataclass
class IngestChunks:
    parent_creates: list[ParentChunkCreate]
    child_drafts_by_parent_key: dict[str, list[ChildChunkDraft]]

def build_chunks_for_ingest(package: ChunkPackage, ctx: IngestContext) -> IngestChunks: ...
```

适配层的契约：

- `parent_creates[i].parent_key = package.parent_chunks[i].parent_id`。
- `child_drafts_by_parent_key[parent_key]` 保留与 ChunkFlow 输出一致的顺序。
- 不在适配层调用 repository；适配层只产数据结构，repository 调用在 `ingest_service.py` 中完成。
- 适配层不构造 `ChildChunkCreate`，因为 `ChildChunkCreate.parent_id` 是必填字段；插入 parent 后由 `ingest_service` 拿到 `ParentChunkRecord.id`，再把 `ChildChunkDraft` 转成 `ChildChunkCreate`（详见服务层）。
- 不允许在适配层硬编码 embedding 描述，所有 embedding 字段都从 `ctx` 注入。

#### `ingest_service.py`

```python
@dataclass
class EmbeddingProviderConfig:
    """M2 阶段仅需要的 embedding 配置形态，用于填充 IngestContext 中的基线描述字段。
    M3 的 EmbeddingProvider 会扩展为完整接口（含 SDK 调用、max_input_tokens、distance_metric 等），
    M2 只依赖此最小配置。"""
    provider: str      # e.g. "dashscope"
    model_slug: str    # e.g. "text-embedding-v4@1024"
    dim: int           # e.g. 1024

@dataclass
class IngestRequest:
    tenant_id: str
    user_id: str | None              # 从鉴权上下文注入，用于审计和 created_by
    source_uri: str
    source_name: str | None
    doc_type: str | None = None
    department: str
    access_level: str
    file_path: Path
    title: str | None = None
    created_by: str | None = None    # 若为空，使用 user_id 作为 created_by
    parser_hint: str = "auto"
    template_hint: str = "auto"
    metadata: dict[str, Any] = field(default_factory=dict)

class IngestService:
    def __init__(
        self,
        session_factory: AsyncSessionFactory,
        settings: Settings,
        embedding_provider: EmbeddingProviderConfig,  # 注入 provider/model/dim，禁止硬编码
    ): ...

    async def ingest_document(self, request: IngestRequest) -> IngestJobRecord: ...
```

`ingest_document` 内部流程（每一步对应 M1 repository 已存在的方法）：

0. **并发控制预检**：在任何解析或写库动作前，必须先获得 `(tenant_id, source_uri)` 粒度的导入锁，确保同一业务主键的导入被串行化。M2 baseline 使用进程内 `asyncio.Lock`（单实例部署）；如果部署多实例，必须改用 Postgres advisory lock、Redis `SETNX` 或队列分区，不能只依赖 `SELECT ... FOR UPDATE`。`SELECT ... FOR UPDATE` 只能锁定已经存在的 active 文档行，无法保护首次导入时"没有行可锁"的场景。
1. **创建 job (pending)**：在独立事务里调用 `IngestJobRepository.create(IngestJobCreate(...))`，写入 `tenant_id`、`source_uri`、`doc_type`、`parser`、`template`、`metadata`、`created_by`，提交事务，把 `job_id` 返给调用方。失败立即抛错，不进入解析阶段。
2. **mark_running 并提交**：开启独立事务，`IngestJobRepository.mark_running(job_id, tenant_id)`，立即提交。后续任意业务事务 rollback 后，`mark_failed()` 仍能命中已提交的 `running` job。
3. **预检：文件大小**：检查 `request.file_path.stat().st_size` 是否超过 `Settings.ingest_max_file_bytes`（默认 100 MiB）。超限 → 抛 `OversizeError(file_size=actual, limit=limit)`，进入"大文件失败"分支（见 [错误处理与降级](#错误处理与降级)）。
4. **解析**：调用 `parse_to_chunk_package(file_path, build_pipeline_config(settings, parser_hint))`。该调用被 `asyncio.wait_for(..., timeout=settings.ingest_parse_timeout_seconds)` 包裹，并通过 `run_in_executor` 在后台线程执行。超时后取消子任务（接受 `pypdf` 不可中断的局限，见 R4 风险章节）。失败进入"解析失败"分支。
5. **预检：chunk 数量**：解析成功后，立即检查 `len(package.child_chunks)` 是否超过 `Settings.ingest_max_child_chunks_per_document`（默认 20000）。超限 → 抛 `OversizeError(chunk_count=actual, limit=limit)`，进入"大文件失败"分支。此预检在 hash 判定之前，避免无意义的 hash 计算。
6. **开启入库事务并做 hash 判定**：解析完成后再开启短事务；用 `hashing.compute_content_hash(...)` 计算 `content_hash`，调用 `DocumentRepository.find_by_source_hash(tenant_id, source_uri, content_hash, statuses=("active", "superseded"))`。命中 → 在同一事务内进入 `skipped_duplicate` 分支，不调用任何 chunk repository。
7. **版本切换**：未命中 hash → 在同一入库事务内先通过 `SELECT id, version FROM rag_documents WHERE tenant_id = :tenant_id AND source_uri = :source_uri AND status = 'active' FOR UPDATE` 锁定现有 active 文档行（若不存在则跳过锁定），再执行 `DocumentVersionRepository.supersede_source(tenant_id, source_uri)`（即使没有 active 版本也无副作用），最后 `DocumentRepository.next_version(tenant_id, source_uri)` 拿到 `new_version`。并发正确性由步骤 0 的导入锁保证；`FOR UPDATE` 和唯一约束只是数据库侧兜底。
8. **创建 active document**：`DocumentRepository.create(DocumentCreate(content_hash=..., version=new_version, status="active", ...))`，拿到 `document_id`。
9. **构造 chunks**：`IngestContext(user_id=request.user_id, document_version=new_version, embedding_provider=cfg.provider, embedding_model=cfg.model_slug, embedding_dim=cfg.dim, ...)`；`build_chunks_for_ingest(package, ctx)`。`user_id` 仅用于审计，不进入 chunk metadata。
10. **写 parent**：`ParentChunkRepository.bulk_create(document_id, parent_creates)`，得到 `parent_records: list[ParentChunkRecord]`，按 `parent_key` 建索引。
11. **写 child**：对每个 `parent_record`，从 `IngestChunks.child_drafts_by_parent_key[parent_record.parent_key]` 取出 `ChildChunkDraft`，用 `parent_record.id` 转成 `ChildChunkCreate`，再 `ChunkRepository.bulk_create(document_id, all_child_creates)`。
12. **job 终态**：`IngestJobRepository.mark_success(job_id, tenant_id, IngestJobSuccess(document_id=document_id, content_hash=..., version=new_version, parser_used=package.parser_used, chunker_used=package.chunker_used, parent_chunk_count=len(package.parent_chunks), child_chunk_count=len(package.child_chunks), warnings=package.warnings, parse_report=package.parse_report.to_dict()))`。
13. **commit**：入库事务整体提交。任意步骤抛错时 rollback 入库事务，并进入 `mark_failed`（在独立事务里执行；`running` 状态已在步骤 2 提交，不会被业务 rollback 一起回退）。

服务层是 M2 唯一允许直接调用 M1 repository 的"业务面"。`recallforge/chunking/` 内部不得 import repository；`recallforge/ingest/chunk_adapter.py` 也不得 import repository。

### 模块依赖矩阵

| 模块 | 允许依赖 | 禁止依赖 |
| --- | --- | --- |
| `recallforge/chunking/**` | 第三方解析库（pypdf、openpyxl、docling、mineru、tiktoken 等）、标准库 | `recallforge.storage.*`、`recallforge.ingest.*`、`recallforge.embeddings.*` |
| `recallforge/ingest/chunk_adapter.py` | `recallforge.chunking.ir.models`、`recallforge.storage.repository` 中的 `*Create` dataclass | `recallforge.storage.repository` 中的 `*Repository` 类 |
| `recallforge/ingest/hashing.py` | 标准库 | `recallforge.storage.*` |
| `recallforge/ingest/ingest_service.py` | 上述全部 + `recallforge.storage.repository.*`、`recallforge.config.Settings`、未来注入的 `EmbeddingProviderConfig` | 直接 import `pgvector` / 直接拼 SQL |

## 入库映射

ChunkFlow `ChunkPackage` 字段到 M1 表列的精确对照。映射在 `recallforge.ingest.chunk_adapter.build_chunks_for_ingest()` 中完成。基础映射来自 [docs/chunkflow_migration.md](chunkflow_migration.md)，本表在 M2 spec 中确认每一条都对应到具体 `*Create` 字段。

### `rag_documents` 字段填充

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `tenant_id` | `IngestRequest.tenant_id` | 来自鉴权上下文 |
| `source_uri` | `IngestRequest.source_uri` | 业务来源 |
| `source_name` | `IngestRequest.source_name` 或 `package.metadata["filename"]` | 展示名兜底 |
| `doc_type` | `IngestRequest.doc_type` | 由 API 入口或服务调用方决定。M2 阶段的默认推导策略：若 `IngestRequest.doc_type` 为空，`IngestService` 根据 `file_path.suffix` 映射到白名单：`{'.md': 'markdown', '.markdown': 'markdown', '.txt': 'text', '.pdf': 'pdf', '.csv': 'table', '.tsv': 'table'}`。未知扩展名抛 `IngestError("Unsupported file type: {suffix}")`。M5 API 层应在鉴权后校验或注入 `doc_type`，避免依赖自动推导。 |
| `title` | `IngestRequest.title`（可空） | 文档标题，可由上层注入 |
| `content_hash` | `hashing.compute_content_hash(parsed_document)` | 64 位小写 hex |
| `version` | `DocumentRepository.next_version(...)` | hash 不同分支才生效 |
| `status` | 固定 `"active"` | 由 `DocumentRepository.create` 默认写入 |
| `department` | `IngestRequest.department` | 服务端权限字段；不接受客户端覆盖 |
| `access_level` | `IngestRequest.access_level` | 闭合枚举 |
| `metadata` | `package.metadata`（filename、file_type、section_count、document_type_detection、chunker_config）与 `IngestRequest.metadata` 浅合并；key 冲突时 `IngestRequest.metadata` 优先 | M2 metadata 不携带敏感身份信息 |
| `created_by` / `updated_by` | `IngestRequest.created_by` | 服务账号或用户 ID |

### `rag_parent_chunks` 字段填充

| RagParentChunk 字段 | ChunkPackage / 上下文来源 |
| --- | --- |
| `tenant_id` | `ctx.tenant_id` |
| `document_id` | 上一步创建的 active document |
| `source_uri` | `ctx.source_uri` |
| `doc_type` | `ctx.doc_type` |
| `parent_key` | `ParentChunk.parent_id` |
| `chunk_index` | parent 在 `package.parent_chunks` 中的顺序（从 0 起） |
| `content` | `ParentChunk.text` |
| `content_hash` | `hashing.compute_content_hash(ParentChunk.text)` |
| `department` | `ctx.department` |
| `access_level` | `ctx.access_level` |
| `heading_path` | `ParentChunk.heading_path` |
| `page_start` | `ParentChunk.page_span[0]` |
| `page_end` | `ParentChunk.page_span[1]` |
| `token_count` | 由 `recallforge.chunking.tokenizer` 计算（若 `ParentChunk.metadata` 中已有 `token_count` 则直接复用） |
| `version` | `ctx.document_version` |
| `metadata` | `ParentChunk.metadata` 合并 `section_id`、`title`、`source_block_ids`、`child_chunk_ids` |

### `rag_chunks` 字段填充

| RagChunk 字段 | ChunkPackage / 上下文来源 |
| --- | --- |
| `tenant_id` | `ctx.tenant_id` |
| `document_id` | 上一步创建的 active document |
| `parent_id` | 写完 parent 后注入的 `ParentChunkRecord.id` |
| `chunk_key` | `ChildChunk.chunk_id` |
| `parent_key` | `ChildChunk.parent_id`，必须等于其归属 parent 的 `parent_key` |
| `chunk_index` | child 在 `package.child_chunks` 中的顺序（从 0 起） |
| `content` | `ChildChunk.text` |
| `content_hash` | `hashing.compute_content_hash(ChildChunk.text)` |
| `doc_type` | `ctx.doc_type` |
| `chunk_type` | `ChildChunk.chunk_type`（M1 schema CHECK 要求恒为 `"child"`） |
| `template` | `ChildChunk.template` |
| `department` | `ctx.department` |
| `access_level` | `ctx.access_level` |
| `heading_path` | `ChildChunk.heading_path` |
| `page_start` | `ChildChunk.page_span[0]` |
| `page_end` | `ChildChunk.page_span[1]` |
| `source_uri` | `ctx.source_uri` |
| `version` | `ctx.document_version` |
| `embedding_provider` | `ctx.embedding_provider`（从 `EmbeddingProvider` 配置注入） |
| `embedding_model` | `ctx.embedding_model`（从配置注入） |
| `embedding_dim` | `ctx.embedding_dim`（从配置注入） |
| `embedding_text_embedding_v4_1024` | M2 不填充，保持 `NULL`，由 M3 通过 `VectorStoreAdapter.upsert_chunks()` 回填 |
| `embedding_metadata` | M2 写入 `{}`；M3 回填时按 ADR-0001 推荐 schema 更新 |
| `metadata` | `ChildChunk.metadata` 合并 `source_block_ids`、`bbox_refs`（序列化后的 dict 列表）、`context_before`、`context_after`、`token_count` |

### `rag_ingest_jobs` 字段填充

| RagIngestJob 字段 | 写入时机 | 来源 |
| --- | --- | --- |
| `job_id` | `create()` 时由 repository 自动生成 UUID | M1 已有逻辑 |
| `tenant_id` | `create()` | `IngestRequest.tenant_id` |
| `source_uri` / `source_name` / `doc_type` | `create()` | `IngestRequest.*` |
| `parser` / `template` | `create()` | `IngestRequest.parser_hint` / `template_hint` |
| `metadata` | `create()` 写入 `IngestRequest.metadata`，并在每个阶段做局部扩展（例如 `metadata.limit_breached`、`metadata.version_conflict`） | 服务层；parser fallback 链路只写 `parse_report.parser_fallback_chain` |
| `parser_used` / `chunker_used` | `mark_success()` 或 `mark_skipped_duplicate()` | `package.parser_used` / `package.chunker_used` |
| `parent_chunk_count` / `child_chunk_count` | `mark_success()` 或 `mark_skipped_duplicate()` | `len(package.parent_chunks)` / `len(package.child_chunks)` |
| `warnings` | `mark_success()`、`mark_failed()` 或 `mark_skipped_duplicate()` | `package.warnings`（含 parser fallback warnings、postprocess warnings、validator warnings） |
| `parse_report` | `mark_success()`、`mark_failed()` 或 `mark_skipped_duplicate()` | `package.parse_report.to_dict()`（结构定义见下方） |
| `content_hash` / `version` / `document_id` | `mark_success()` 或 `mark_skipped_duplicate()` | hash 判定后的实际值 |
| `error_message` | `mark_failed()` | 异常信息摘要（不含敏感栈） |
| `started_at` / `finished_at` | M1 repository 在状态切换时自动写入 | M1 已有逻辑 |

#### `parse_report` 结构定义

`parse_report` 是 `ParseReport` dataclass 的序列化形态（`to_dict()`），用于 M3 诊断、M6 评测和事后复盘。完整 schema：

```python
@dataclass
class ParseReport:
    # 文档结构统计
    page_count: int = 0               # 总页数（PDF）或 0（text/markdown）
    block_count: int = 0              # 解析后的 block 总数
    table_count: int = 0              # 表格 block 数量
    figure_count: int = 0             # 图片/图表 block 数量
    
    # 解析器信息
    parser_used: str = ""             # 实际使用的解析器名（如 "pypdf", "docling"）
    parser_fallback_chain: list[str] = field(default_factory=list)  
                                      # fallback 路径，如 ["docling", "mineru", "pypdf"]
    
    # 切片信息
    parent_chunk_count: int = 0       # 生成的 parent chunk 数量
    child_chunk_count: int = 0        # 生成的 child chunk 数量
    
    # 质量度量（供 M3/M6 使用）
    metrics: dict[str, Any] = field(default_factory=dict)
    # metrics 建议包含：
    #   - "token_count_total": int        # 所有 child chunk 的 token 总数
    #   - "token_count_mean": float       # 平均 token 数
    #   - "token_count_p50": float        # 中位数
    #   - "token_count_p95": float        # 95 分位
    #   - "text_density": float           # 每页文本量（字符数/page_count），PDF 专用
    #   - "chunker_config": dict          # 实际使用的 chunker 配置（child_max_tokens 等）
    
    # 警告列表
    warnings: list[dict[str, Any]] = field(default_factory=list)
    # 每个 warning 包含：
    #   - "level": "info" | "warning" | "error"
    #   - "message": str
    #   - "source": str                   # 来源，如 "parser", "chunker", "postprocess"
    #   - "context": dict                 # 可选上下文（如 page number、chunk_id）
    
    def to_dict(self) -> dict:
        """序列化为 JSONB 兼容字典。"""
        ...
```

**写入时机**：
- `mark_success()` 时写入完整 `parse_report`。
- `mark_failed()` 时若解析阶段失败（`package` 不存在），`parse_report` 写入 `{"error_phase": "parse", "parser_hint": request.parser_hint, "exception_type": ...}`，`warnings` 写入一条结构化 error warning。
- `mark_skipped_duplicate()` 时写入完整 `parse_report`（解析已完成，诊断信息有价值）；同时在 `metadata_patch` 中记录 `dedupe_existing_version` 和 `skipped_reason`。

**注意事项**：
- `parse_report.parser_fallback_chain` 是单一数据源，服务层不复制、不修改。
- `parse_report.metrics.chunker_config` 必须包含实际的 `child_max_tokens`、`child_min_tokens`、`parent_granularity`，用于 M6 评测参数归因。
- M3 可通过 `parse_report.metrics.token_count_*` 预估 embedding API 调用预算。

## 错误处理与降级

M2 的失败语义必须对得上 M1 的 `pending → running → success | failed | skipped_duplicate` 状态机，不能引入新终态。

### 异常类型

`ingest_document` 在 job 状态写入终态后，会向上抛出类型化异常，供 M5 API 层映射 HTTP 状态码。异常与 job 状态不是互斥的：job 始终记录了完整诊断，异常只是让调用方区分错误类别。

| 异常类 | 对应 job 终态 | HTTP 状态码建议 | 场景 |
| --- | --- | --- | --- |
| `IngestSuccess`（非异常，返回 `IngestJobRecord`） | `success` | 200 / 201 | 正常完成 |
| `IngestSkippedDuplicate`（非异常，返回 `IngestJobRecord`） | `skipped_duplicate` | 200 | hash 去重命中 |
| `ParserUnavailableError` | `failed` | 400 | 请求的 parser 不存在或不可用 |
| `OversizeError` | `failed` | 413 | 文件大小或 chunk 数量超限 |
| `ChunkKeyConflictError` | `failed` | 500 | ChunkFlow 输出 key 冲突（内部错误） |
| `IngestError`（基类） | `failed` | 500 | 其他未分类错误 |

所有异常继承自 `IngestError`（定义在 `recallforge/ingest/errors.py`），都携带 `job_id: str` 和 `tenant_id: str` 字段，便于调用方关联查询。`OversizeError` 额外携带 `limit_name: str`、`limit: int`、`actual: int`。

### 解析失败

触发条件：

- `parse_to_chunk_package` 抛 `FileNotFoundError`、`RuntimeError("No parser could parse ...")` 或具体 parser 异常。
- ChunkFlow validators 报硬错误（例如 `validate_parsed_document` 返回禁止级别 warnings，目前 ChunkFlow 仅返回 warnings，但服务层保留 fail-fast 钩子）。

处理流程：

1. 让当前业务事务回滚（document/parent/child 都未写入）。
2. 在独立事务里调用 `IngestJobRepository.mark_failed(job_id, tenant_id, error_message=str(exc), diagnostics={"phase": "parse", "exception_type": type(exc).__name__, "parser_hint": request.parser_hint}, warnings=[{"level": "error", "message": str(exc), "source": "ingest_service", "phase": "parse"}], parse_report={"error_phase": "parse", "parser_hint": request.parser_hint, "exception_type": type(exc).__name__})`。
3. `metadata` 保留机器可聚合的错误诊断；`parse_report` / `warnings` 保留给人读和 M6 评测归因的结构化摘要。禁止只把失败原因塞进 `error_message`。
4. 向上抛出类型化异常，同时 job 状态已写入 `failed`。调用方（M5 API 层）可根据异常类型映射 HTTP 状态码（见下方异常类型表）。

### 解析降级

触发条件：

- 请求 `parser="auto"` 且 PDF 文件落到 `pypdf` fallback（`docling` / `mineru` `is_available()` 为 False 或 `parse()` 抛错）。
- 请求显式 `parser="docling"` 或 `parser="mineru"` 但适配器不可用，被 `configured_parser_priority` 显式补 `pypdf`。

记录方式（不影响 `success` 状态）：

- **单一数据源原则**：`parser_fallback_chain` 只存在于一处——`ParseReport.parser_fallback_chain`（由 ChunkFlow 在 `_parse_with_fallback` 中直接写入 `ParseReport`）。`ChunkPackage.parse_report` 是 `ParseReport` 的序列化形态。
- `ParsedDocument.parser_fallback_chain`（若 ChunkFlow 内部维护该字段）必须在构建 `ParseReport` 时拷贝到 `ParseReport.parser_fallback_chain`，后续服务层**只做透传**，不再从 `package.metadata` 复制到 `parse_report`。
- `parser_used` 落库的是真正成功的那个解析器名（例如 `pypdf`），而不是请求的 `parser_hint`。该值从 `ChunkPackage.parser_used` 读取，由 ChunkFlow 在 fallback 完成后设置。
- 任何降级警告必须出现在 `package.warnings` 中（ChunkFlow `_parse_with_fallback` 已保证），后续直接落到 `rag_ingest_jobs.warnings`。
- **数据流**：`ChunkFlow._parse_with_fallback()` → `ParsedDocument.parser_fallback_chain` → `ParseReport.parser_fallback_chain` → `ChunkPackage.parse_report.parser_fallback_chain` → `IngestJobRepository.mark_success(...).parse_report.parser_fallback_chain` → `rag_ingest_jobs.parse_report.parser_fallback_chain`。全程不经过 `package.metadata`。

### hash 去重

触发条件：

- `DocumentRepository.find_by_source_hash(tenant_id, source_uri, content_hash, statuses=("active", "superseded"))` 返回非空。

处理流程：

1. 不调用 `supersede_source`、不调用 `DocumentRepository.create`，不调用 `ParentChunkRepository.bulk_create` / `ChunkRepository.bulk_create`。
2. 把命中的 `existing_document.id` / `existing_document.content_hash` / `existing_document.version` 以及本次解析产出的诊断信息，通过 `IngestJobRepository.mark_skipped_duplicate(job_id, tenant_id, IngestJobSkippedDuplicate(document_id=existing_document.id, content_hash=existing_document.content_hash, version=existing_document.version, parser_used=package.parser_used, chunker_used=package.chunker_used, parent_chunk_count=len(package.parent_chunks), child_chunk_count=len(package.child_chunks), warnings=package.warnings, parse_report=package.parse_report.to_dict(), metadata_patch={"dedupe_existing_version": existing_document.version, "skipped_reason": "content_hash_match"}))` 写回 job。
3. **必须落库诊断**：既然已经花费 CPU 做了解析，`parser_used`、`chunker_used`、`parent_chunk_count`、`child_chunk_count`、`warnings`、`parse_report` 必须写入 job，为 M6 评测提供对照数据（例如同一文档用不同 parser 的 chunk 数量差异）。`metadata_patch` 中的 `dedupe_existing_version` 和 `skipped_reason` 也会合并到 `rag_ingest_jobs.metadata`。
4. 提交事务。

如果用户希望把内容回退到一个 hash 命中但已经 superseded 的旧版本，必须显式调用 `DocumentVersionRepository.restore_version()`，普通 ingest 路径不做隐式回滚（与 M1 spec 一致）。

### 版本冲突

触发条件：

- `DocumentRepository.create(...)` 因 `uq_rag_documents_source_version` 或 `uq_rag_documents_active_source` 失败。
- `ParentChunkRepository.bulk_create` 命中 `uq_rag_parent_chunks_document_key`；`ChunkRepository.bulk_create` 命中 `uq_rag_chunks_document_key`。

处理流程：

- 视为业务错误，整事务 rollback；通过 `mark_failed` 把诊断写入 job：`metadata.version_conflict = {"document_id": ..., "violated_constraint": ...}`。
- ChunkFlow 输出 `parent_id` / `chunk_id` 冲突（同文档内 key 重复）必须 fail-fast：M2 在写库前在适配层做一次本地校验，发现重复直接抛 `ChunkKeyConflictError`，并把冲突的 key 列表写入 `metadata.chunk_key_conflicts`，避免依赖 DB 唯一索引报错才发现。

### 并发导入竞态

两个导入请求同时处理同一 `(tenant_id, source_uri)` 时，如果没有步骤 0 的导入锁，会出现以下竞态：

1. 首次导入时没有 active 文档行，`SELECT ... FOR UPDATE` 没有可锁对象。
2. 两者都通过 `find_by_source_hash` 未命中。
3. 两者都尝试 `supersede_source` + `create`，其中一个会在唯一约束上失败并进入 `mark_failed`，虽然不会产生脏数据，但会把本可串行完成的合法请求记成失败。

M2 的串行化策略是分层防御：

- **入口锁**：`ingest_document` 步骤 0 是正确性前提。单实例用进程内 `asyncio.Lock` 即可；多实例必须换成 Postgres advisory lock、Redis `SETNX` 或队列分区。
- **数据库层**：步骤 7 的 `SELECT ... FOR UPDATE` 锁定现有 active 文档行，缩小已存在文档的版本切换竞态窗口，但不能替代入口锁。
- **兜底**：唯一约束 `uq_rag_documents_active_source` 仍是最终一致性保障；若入口锁失效，冲突事务必须 rollback 并通过 `mark_failed` 写入 `metadata.version_conflict`，不会产生脏数据。

### 解析器可用性预检

启动阶段（`IngestService.__init__` 或服务启动钩子）做一次 `available_parsers()` 调用：

- 把结果写入结构化日志（`logger.info("parser_availability", extra={...})`），同时缓存到 `IngestService._available_parsers` 字典。
- 不在启动阶段 fail；ChunkFlow 已内置运行时降级。
- 当 `pypdf` 也不可用时（极端情况），保留为运行时错误：第一次遇到 `.pdf` 文件时让 ChunkFlow 抛 `RuntimeError("No parser could parse ...")`，走解析失败路径。

### 大文件 / 过长 chunk

可配置阈值（默认值可继承 M1 `Settings`；M2 新增的配置项作为 P2 默认值）：

- `Settings.ingest_max_file_bytes`：单文件大小硬上限，默认 `100 * 1024 * 1024`（100 MiB）。
- `Settings.ingest_max_child_chunks_per_document`：单文档 child chunk 数量硬上限，默认 `20000`。
- `Settings.ingest_parse_timeout_seconds`：解析阶段单次超时，默认 `300`。

任何阈值触发：rollback 业务事务，`mark_failed` 写入 `error_message` 与 `metadata.limit_breached = {"name": ..., "limit": ..., "actual": ...}`。

## 与 M1 / M3 的边界

M2 是"用 M1，给 M3 铺路"的纯应用层，不向下修改数据库 schema，也不向上提供 HTTP API。

### M2 用 M1 提供的能力

| 用途 | 调用入口 | M2 使用方式 |
| --- | --- | --- |
| 创建文档与版本切换 | `DocumentRepository.create / find_by_source_hash / next_version` + `DocumentVersionRepository.supersede_source` | 同一事务内 supersede 旧 active → create 新 active |
| 写入 parent chunk | `ParentChunkRepository.bulk_create` | 一次性写入当前文档全部 parent |
| 写入 child chunk | `ChunkRepository.bulk_create` | parent 写完后填 `parent_id` 再批量写入 child；embedding 列保持 `NULL` |
| 任务状态机 | `IngestJobRepository.create / mark_running / mark_success / mark_failed / mark_skipped_duplicate` | 严格按状态机调用，不绕过 repository |
| 逻辑删除联动 | `DocumentRepository.mark_deleted` | M2 不主动调用；提供给后续运维或测试 fixture |

M2 实施前必须补齐 M1 repository 的两个契约缺口：

- `IngestJobRepository.mark_skipped_duplicate(job_id, tenant_id, result: IngestJobSkippedDuplicate)` 必须接收并写入 `parser_used`、`chunker_used`、`parent_chunk_count`、`child_chunk_count`、`warnings`、`parse_report`，并把 `result.metadata_patch` JSON merge 到 `rag_ingest_jobs.metadata`。
- `IngestJobRepository.mark_failed(...)` 必须支持可选 `warnings` 与 `parse_report` 参数；解析失败没有 `ChunkPackage` 时也要写入结构化失败摘要，不能只写 `error_message` 或 `metadata`。

### M2 不碰的边界

- `rag_chunks.embedding_text_embedding_v4_1024`：M2 不写入，由 M3 `VectorStoreAdapter.upsert_chunks()` 回填。
- `rag_chunks.embedding_metadata`：M2 写入 `{}`，回填状态由 M3 维护。
- `rag_query_logs`：完全不在 M2 范围。
- `recallforge.embeddings`、`recallforge.retrieval`、`recallforge.agents`、`recallforge.api`：M2 不创建/不调用。
- `VectorStoreAdapter`、`PgVectorStore`、`EmbeddingProvider` 的具体实现：M2 仅依赖一个**配置形态**的 `EmbeddingProviderConfig`（包含 `provider` / `model_slug` / `dim`），用于在 `IngestContext` 中填字段，避免 `ChildChunkCreate` 中硬编码 `dashscope` 与 `text-embedding-v4@1024`。具体 SDK 调用一律由 M3 完成。

### M2 留给 M3 的接口

- 所有 child chunk 写入后处于"`embedding_text_embedding_v4_1024 IS NULL` 且 `status='active'`"状态，可被 M3 的 `ChunkRepository.list_for_embedding_backfill()` 选中。
- `embedding_provider` / `embedding_model` / `embedding_dim` 已经记录基线列描述，M3 可以按 `embedding_model` 路由到对应向量列。
- `parse_report.metrics` 中保留 token 估算与 chunk 长度分布，M3 / M6 可直接复用诊断 embedding 调用预算与失败模式。

### M2 留给 M5 的接口

- `IngestService.ingest_document(IngestRequest) -> IngestJobRecord` 是 M5 `POST /api/rag/documents` 的核心实现入口；M5 仅负责鉴权、`RequestContext` 注入、把请求体转换为 `IngestRequest`、把返回的 `IngestJobRecord` 序列化为 `document_id` / `job_id` / `status`。
- M5 不应在 API 层再次调用 `parse_to_chunk_package` 或 repository。

## 测试策略

测试分层与 M1 保持一致：单元 → 集成 → 端到端。M2 不需要在本阶段引入新的 LLM mock，但需要小型示例文件。

### 单元测试 `tests/test_ingest_chunk_adapter.py`

覆盖 `build_chunks_for_ingest()` 的映射正确性。

- 构造一个最小 `ChunkPackage`（2 个 parent，每个 parent 各 2 个 child，包含 `heading_path`、`page_span`、`token_count`、`bbox_refs`），验证：
  - `parent_creates[i].parent_key == package.parent_chunks[i].parent_id`。
  - `parent_creates[i].page_start/page_end` 等于 `ParentChunk.page_span`。
  - `child_drafts_by_parent_key` 包含且只包含正确数量的 child；同 parent 内的 child 顺序与 ChunkFlow 输出一致。
  - `embedding_provider` / `embedding_model` / `embedding_dim` 从 `IngestContext` 透传，不允许出现默认硬编码字符串。
  - `metadata` 合并后包含 `source_block_ids`、`bbox_refs`、`context_before` / `context_after`。
- 异常路径：同文档内出现重复 `parent_id` 或 `chunk_id` 时抛 `ChunkKeyConflictError`，并把冲突 key 列表暴露到异常字段。

### 单元测试 `tests/test_ingest_hashing.py`

覆盖 `compute_content_hash()` 的规范化与稳定性。

- 输入文本里包含 `\r\n`、行尾空格、UTF-8 BOM、连续空行 → 输出 hash 与去掉这些噪声后的结果一致。
- 输入 64 位 hex 输出，正则匹配 `^[0-9a-f]{64}$`。
- 同一份 `ParsedDocument` 反复调用 hash 函数稳定不变。
- child chunk hash 与文档级 hash 不同；两个不同 child 文本生成不同 hash。

### 单元测试 `tests/test_ingest_pipeline_config.py`

覆盖配置注入。

- 当 `Settings.child_max_tokens=450`、`child_min_tokens=80`、`parent_granularity="chapter"` 时，`build_pipeline_config(settings)` 返回的 `PipelineConfig` 字段精确匹配。
- 当配置被显式改写为非默认值（例如 `child_max_tokens=300`）时，`PipelineConfig` 同步生效。
- `parser_hint` 透传到 `PipelineConfig.parser`。

### 单元测试 `tests/test_ingest_service_state_machine.py`

针对 `IngestService.ingest_document` 的状态机逻辑，使用 in-memory repository fake 或 mock 注入。

- **happy path**：返回 `status='success'`，`document_id`、`content_hash`、`version`、`parser_used`、`chunker_used`、`parent_chunk_count`、`child_chunk_count`、`warnings`、`parse_report` 全部按映射表落到 job。
- **hash duplicate**：mock `find_by_source_hash` 命中 → 状态为 `skipped_duplicate`；断言 `ParentChunkRepository.bulk_create` 与 `ChunkRepository.bulk_create` 都**未**被调用；断言 `next_version` 与 `supersede_source` 都**未**被调用；断言 `mark_skipped_duplicate` 的 `IngestJobSkippedDuplicate` 参数包含 `parser_used`、`chunker_used`、`parent_chunk_count`、`child_chunk_count`、`warnings`、`parse_report` 和 `metadata_patch`。
- **version bump**：mock `find_by_source_hash` miss + 之前已有 active document → 同事务内 `supersede_source` 在 `create` 之前被调用；新 document `version = max(old_version) + 1`；旧 parent/child 状态变为 `superseded`。
- **parse failure**：mock `parse_to_chunk_package` 抛 `RuntimeError` → 业务事务 rollback；`mark_failed` 在独立事务内被调用；job `error_message` 非空；`document_id` 仍为 `None`。
- **chunk key conflict**：让适配层抛 `ChunkKeyConflictError` → `mark_failed` 写入 `metadata.chunk_key_conflicts`，不写入任何 parent/child。
- **parser fallback**：mock ChunkFlow 返回 `package.parser_used="pypdf"`、`parser_fallback_chain=["docling","mineru","pypdf"]` → job `parser_used == "pypdf"`，`parse_report.parser_fallback_chain == [...]`，且 `warnings` 包含 fallback 提示。

### 集成测试 `tests/integration/test_ingest_pipeline.py`

要求一个真实的 Postgres + pgvector 实例（沿用 M1 `tests/test_migrations.py` 的 fixture）。

- **3 种格式端到端**：在 `tests/fixtures/m2/` 准备一份 `.md`、一份 `.txt`、一份 `.pdf`（小尺寸，纯文本类，无需 OCR）、以及一份 `.csv`，分别走完整 ingest。每种格式断言：
  - `rag_documents` 出现一条 `status='active'` 记录，`content_hash` 满足正则。
  - `rag_parent_chunks` 数量 ≥ 1。
  - `rag_chunks` 数量 ≥ 1，所有行 `embedding_text_embedding_v4_1024 IS NULL`、`embedding_metadata` 为空字典（Python 中 `== {}`，SQL 中 `'{}'::jsonb`）、`status='active'`。
  - 每个 child chunk 都能用 `parent_key` 在 `rag_parent_chunks` 中找到唯一 parent。
- **chunk 参数继承配置**：通过环境变量把 `CHILD_MAX_TOKENS=450`、`CHILD_MIN_TOKENS=80`、`PARENT_GRANULARITY=chapter` 注入；导入后从 `rag_ingest_jobs.parse_report.metrics` 或 `metadata.chunker_config` 中读取并断言。
- **重复导入跳过**：先成功导入再用完全相同的文件再次导入 → 第二次 job `status='skipped_duplicate'`，`rag_chunks` 数量不变；断言 `skipped_duplicate` job 的 `parser_used`、`chunker_used`、`parent_chunk_count`、`child_chunk_count`、`warnings`、`parse_report` 非空。
- **版本升级**：第二次提交修改后的文件 → 旧文档 `status='superseded'`、旧 child / parent `status='superseded'`、新文档 `version=2 AND status='active'`。
- **逻辑删除**：调用 `DocumentRepository.mark_deleted(...)` 后，对应 child / parent 全部 `status='deleted'`，再次导入相同内容 → 视为新导入还是 skipped？M2 spec 取定：**`find_by_source_hash` 默认 `statuses=("active", "superseded")`**，命中 deleted 不算重复，会生成新 active 版本。集成测试断言这一行为。

  **业务决策理由**：删除文档是用户的显式操作，表示"不再需要该文档及其召回结果"。删除后重新导入相同内容应视为新文档，理由如下：
  1. 删除操作可能源于内容过期、权限变更或误上传，重新导入意味着用户确认需要恢复该知识。
  2. 若将 deleted 纳入去重范围，用户删除后将无法重新导入相同内容（会被标记为 `skipped_duplicate`），需要额外的"恢复版本"流程，增加复杂度。
  3.  deleted 状态的 chunk 已从向量索引中移除（通过 `VectorStoreAdapter.delete_by_document_id()`），不会造成召回污染。
  4. 新文档会获得新版本号（`version = max(all_versions) + 1`），保留完整的审计轨迹。

### 集成测试 `tests/integration/test_ingest_parser_fallback.py`

- mock `docling` / `mineru` 适配器的 `is_available()` 为 `False`，导入 PDF → `parser_used='pypdf'`、`parser_fallback_chain` 包含 `["docling","mineru","pypdf"]`、`warnings` 至少包含一条 `Parser docling is not available.`、`Parser mineru is not available.` 文案的告警。

### 测试 fixture 约束

- 所有示例文件放在 `tests/fixtures/m2/`，文件大小 ≤ 50 KB，避免 CI 拉满磁盘。项目统一使用 `tests/fixtures/<milestone>/` 子目录结构（`m1/`、`m2/`...），避免跨里程碑 fixture 冲突。
- PDF fixture 使用 `pypdf` 可直接读取的简单文档。**推荐生成方法**：
  1. 使用 `reportlab` 库生成简单纯文本 PDF（示例脚本放在 `tests/fixtures/m2/generate_pdf_fixture.py`）：
     ```python
     from reportlab.lib.pagesizes import letter
     from reportlab.pdfgen import canvas
     c = canvas.Canvas("tests/fixtures/m2/sample.pdf", pagesize=letter)
     c.drawString(100, 750, "This is a simple test document for M2 integration tests.")
     c.drawString(100, 730, "It contains multiple lines to verify pypdf parsing works correctly.")
     c.save()
     ```
  2. 确保 PDF 不包含加密、字体嵌入、复杂布局或图片。
  3. 提交生成的 PDF 文件到版本控制，避免 CI 动态生成导致的不确定性。
  4. **降级策略**：如果 pypdf 在特定 CI 环境下无法解析测试 PDF（例如版本兼容性问题），允许跳过该用例并记录 warning，但必须在 CI 报告中明确标注。
- 如果未来 Docling / MinerU 被引入 CI，再扩展 fixture 覆盖复杂 PDF（扫描件、多栏排版、表格）。
- 测试不允许写远端文件路径或网络资源；`source_uri` 用 `file://` 协议或纯字符串占位。

## 风险与待定

### R1：ChunkFlow 依赖项

ChunkFlow 当前依赖 `pypdf`、`openpyxl`（XLSX/XLSM）、`tiktoken` 等第三方库；可选依赖还有 `docling`、`mineru`。M2 必须明确：

- 必需依赖：`pypdf`、`openpyxl`、`tiktoken`。`openpyxl` 即使 M2 暂不在 ROADMAP 中列出 XLSX 格式，但 `chunkflow.parsers.table_file` 在加载时已经 import，必须装齐避免 `ImportError`。
- 可选依赖：`docling`、`mineru`（含 `magic-pdf` 等子依赖）默认不安装，通过 `pyproject.toml` extras（例如 `recallforge[docling]`、`recallforge[mineru]`）按需启用。
- 版本锁定：每个 ChunkFlow 第三方解析库都必须在 `pyproject.toml` 中固定上限（`<x.y`），避免上游版本升级破坏当前 IR 解析行为。

待定：是否在 M2 引入 `pyproject.toml` extras 块。当前倾向"M2 落地必需依赖 + 可选 extras 占位"，把 Docling / MinerU 真正接入留到 M8。

### R2：PDF 解析器可用性

`pypdf` fallback 对扫描件、复杂表格、多栏排版的还原度较低，可能导致 child chunk 文本碎片化，进而拖低 M6 召回评测的 baseline。M2 不解决这个问题，但需要：

- 在 `rag_ingest_jobs.parse_report` 中保留 `page_count`、`block_count`、`table_count`、`figure_count`、`warnings`，便于 M6 失败样例归因。
- 在 README / `docs/` 里明确"M2 PDF baseline 是 `pypdf`，对扫描件不可用"。

待定：是否在 M2 加入 `parse_report.metrics.text_density`（每页文本量）告警，触发后写 warning。可在 P2 落地。

### R3：大文件与单文档 chunk 数量

`Settings.ingest_max_file_bytes=100 MiB` 与 `Settings.ingest_max_child_chunks_per_document=20000` 是 M2 提出的默认值，未经过真实数据校准。风险：

- 大型扫描 PDF / XLSM 可能超过 100 MiB，需要在 M8 评估提升上限或引入分块上传。
- 单文档 chunk 数量超过 2 万时，单事务批量插入压力大；M2 不引入分批写入，但在风险章节标注。
- M2 集成测试必须在接近上限的场景下验证事务可行性：构造一个产出 ≥ 5000 child chunk 的文档（或 mock ChunkFlow 返回对应数量的 child），断言单事务 `bulk_create` + commit 耗时 < 30 秒。如果 CI 环境下超时，则必须在 M2 引入分批 `bulk_create`（每批 500 child），分批仍需在同一事务内完成。

待定：是否在 M2 内引入分批 `bulk_create`（例如每批 500 child）。当前倾向"M2 不分批，单事务 commit"，理由是 ROADMAP M2 不追求吞吐；如果集成测试发现事务过长，再拆批。

### R4：解析超时

`Settings.ingest_parse_timeout_seconds=300` 默认值在同步调用语境下需要：

- 用 `asyncio.wait_for` 包住 `parse_to_chunk_package` 的同步执行（通过 `run_in_executor`），超时后取消子任务。
- `run_in_executor` 必须使用默认线程池（`loop.run_in_executor(None, ...)`），不允许 M2 引入自定义进程池或线程池，避免进程管理复杂度。如果未来 M7 需要进程隔离（强制中断、资源回收），届时再引入进程池。
- 取消能力依赖 ChunkFlow 内部 parser 是否能优雅中断；`pypdf` 大文件解析在 Python 层难以强制中断。

待定：M2 是否真的能 enforce 超时。当前倾向"M2 设置超时阈值并尝试 `wait_for`，但接受 `pypdf` 不可中断；超时后只保证主流程不再继续，子线程可能继续跑到自然结束"。如果出现资源泄漏，M7 引入进程隔离。

### R5：ChunkFlow API 漂移

迁入 RecallForge 后，ChunkFlow 上游的修改不会自动同步。约束：

- 迁移时记录 ChunkFlow 源 commit hash，写入 `docs/chunkflow_migration.md`（已有"来源"章节，可在 M2 实施时补 commit hash）。
- 任何对 `recallforge.chunking.*` 的修改都在 RecallForge 内合并，不期望从上游 ChunkFlow 拉取。
- 未来如果上游 ChunkFlow 引入新解析器或新 chunker，仅作为参考实现移植，不引入运行时下载。

### R6：embedding 描述硬编码风险

M1 `ChildChunkCreate` 当前对 `embedding_provider` / `embedding_model` / `embedding_dim` 提供了默认值，标注了 `TODO(M3)`。M2 在 `IngestContext` 与 `build_chunks_for_ingest()` 中必须**显式**注入这些字段，禁止依赖 `ChildChunkCreate` 的默认值，否则一旦 M3 切换 baseline 模型，M2 已经入库的 chunk 描述会与实际 embedding 列错配。M2 代码必须有 lint / 测试断言阻止"使用 dataclass 默认 embedding 描述"的路径。

## M2 完成定义

- `recallforge/chunking/` 完成 ChunkFlow 核心模块迁移，`parse_to_chunk_package()` 是 ingest 层唯一调用入口。
- `recallforge/ingest/` 提供 `IngestService.ingest_document(IngestRequest) -> IngestJobRecord`，串起 job 创建、`running` 提交、解析、hash 判定、版本切换、parent/child 入库、状态机推进。
- 至少 4 种格式（Markdown / TXT / PDF / CSV）可以端到端导入并生成 parent/child chunk；child chunk 默认 `child_max_tokens=450`、`child_min_tokens=80`、parent 默认 `parent_granularity="chapter"`，全部通过配置注入。
- 每个 child chunk 都能通过 `parent_key` 在 `rag_parent_chunks` 中找到唯一 parent；通过 `parent_id` 外键也能定位。
- 重复导入完全相同的内容时，第二次 job 状态为 `skipped_duplicate`，不生成新 parent/child；`skipped_duplicate` job 必须包含 `parser_used`、`chunker_used`、`parent_chunk_count`、`child_chunk_count`、`warnings`、`parse_report` 等诊断字段；hash 不同则旧版本被 `supersede_source`，新版本以 `version+1` 落地。
- 解析失败与解析降级都在 `rag_ingest_jobs.parse_report`、`warnings`、`parser_used` 中可复盘；降级链路的单一事实源是 `parse_report.parser_fallback_chain`，不止打印日志。
- `rag_chunks.embedding_text_embedding_v4_1024` 列全部为 `NULL`，`embedding_metadata` 为 `{}`，等待 M3 回填。
- M2 不引入 `VectorStoreAdapter`、`PgVectorStore`、答案生成、HTTP API；不修改 M1 数据库 schema。M2 不覆盖 DOCX / JSON 格式，延后到 M8。
- 单元测试覆盖适配层映射、hash 规范化、配置注入与状态机分支；集成测试覆盖至少 3 种格式 + 重复导入 + 版本升级 + 解析降级。
- `IngestService` 在 job 状态写入终态后向上抛出类型化异常，M5 可据此映射 HTTP 状态码。异常类型定义在 `recallforge/ingest/errors.py`。
- `IngestService` 与 ChunkFlow 之间没有反向依赖；适配层是 `ChunkPackage` → repository create dataclass / `ChildChunkDraft` 的唯一桥。

## 自检：对照 ROADMAP M2 验收标准

| ROADMAP M2 验收 | 本 spec 覆盖位置 |
| --- | --- |
| 至少 3 种文档格式可生成 parent/child chunk | 交付物清单"格式支持：Markdown / TXT / PDF"+"CSV / TSV 表格路径"；集成测试 `test_ingest_pipeline.py` 覆盖 4 种格式；M2 完成定义第 3 条 |
| child chunk 默认 `child_max_tokens=450`、`child_min_tokens=80` | 设计约束第 2 条；`pipeline_config.py` `build_pipeline_config`；`test_ingest_pipeline_config.py`；M2 完成定义第 3 条 |
| parent 默认 `parent_granularity="chapter"` | 设计约束第 3 条；`pipeline_config.py`；`test_ingest_pipeline_config.py` |
| 每个 child chunk 都能回查 parent chunk | 入库映射 `rag_chunks.parent_id` / `parent_key`；`chunk_adapter.py` 契约（`child_drafts_by_parent_key`）；集成测试断言"每个 child chunk 都能用 parent_key 找到唯一 parent"；M2 完成定义第 4 条 |
| 重复导入相同内容产生 `skipped_duplicate` job，不生成重复 chunk | 错误处理"hash 去重"；交付物清单"内容 hash 去重"；`test_ingest_service_state_machine.py` "hash duplicate" 用例；集成测试"重复导入跳过"；M2 完成定义第 5 条；`skipped_duplicate` job 必须包含解析诊断字段 |
| 解析失败时记录失败原因，解析降级时记录降级路径 | 错误处理"解析失败" + "解析降级"；入库映射 `rag_ingest_jobs.parse_report` / `warnings` / `parser_used` / `parse_report.parser_fallback_chain`；`test_ingest_service_state_machine.py` "parse failure" + "parser fallback"；集成测试 `test_ingest_parser_fallback.py`；M2 完成定义第 6 条 |
| 迁移 ChunkFlow 核心模块（交付物） | 模块设计"迁移范围"；目录结构；M2 完成定义第 1 条 |
| 统一 `parse_to_chunk_package()` 调用入口（交付物） | 模块设计"`recallforge/chunking/`"；交付物清单第 2 行 |
| `build_chunks_for_ingest()` 适配层（交付物） | 模块设计"`chunk_adapter.py`"；入库映射全章；`test_ingest_chunk_adapter.py` |
| `parse_report` / `warnings` / `parser_used` / `chunker_used` 落库（交付物） | 入库映射 `rag_ingest_jobs` 表；错误处理各分支；设计约束第 5 条；`skipped_duplicate` 同样落库 |
| 文档版本策略：hash 不同建立新版本，旧版本 chunk 标记 `superseded`（交付物） | 错误处理"hash 去重" + "版本冲突"；模块设计"ingest_service.py 流程 5/6"；集成测试"版本升级" |
