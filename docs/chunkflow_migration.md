# ChunkFlow Migration

本文件记录 ChunkFlow 到 RecallForge 的迁移范围和验证清单。长期工程约束见 [../AGENTS.md](../AGENTS.md)。

## 来源

```text
C:\Users\wanghui\Desktop\工作空间2026\project\ChunkFlow
```

## 推荐迁移范围

- `chunkflow/core/`
- `chunkflow/ir/`
- `chunkflow/parsers/`
- `chunkflow/chunkers/`
- `chunkflow/postprocess/`
- `chunkflow/tokenizer.py`
- `chunkflow/pdf_parser.py`
- `chunkflow/schema.py`

## 暂不迁移

- `chunkflow/app.py`：独立 FastAPI UI，当前 RAG 服务不需要。
- `chunkflow/static/`：ChunkFlow 前端页面，当前不需要。
- `chunkflow/chunking.py`：旧版兼容入口，优先使用新版 `core.pipeline`。

## 目标调用入口

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

## 入库映射

| ChunkFlow 字段 | RAG 存储位置 | 说明 |
| --- | --- | --- |
| `ParentChunk.parent_id` | `rag_parent_chunks.parent_key` | parent chunk 稳定标识 |
| `ParentChunk.text` | `rag_parent_chunks.content` | 大上下文文本 |
| `ParentChunk.heading_path` | `rag_parent_chunks.heading_path` | 标题路径 |
| `ParentChunk.page_span` | `rag_parent_chunks.page_start/page_end` | 页码范围 |
| `ChildChunk.chunk_id` | `rag_chunks.chunk_key` | child chunk 稳定标识 |
| `ChildChunk.parent_id` | `rag_chunks.parent_key` | 回查 parent |
| `ChildChunk.text` | `rag_chunks.content` | 向量化文本 |
| `ChildChunk.chunk_type` | `rag_chunks.chunk_type` | 切片类型 |
| `ChildChunk.template` | `rag_chunks.template` | 使用模板 |
| `ChildChunk.token_count` | `rag_chunks.token_count` | token 统计 |
| `ChildChunk.bbox_refs` | `rag_chunks.bbox_refs` | 版面引用 |
| `ChildChunk.metadata` | `rag_chunks.metadata` | 扩展元数据 |

## 初版支持格式

- PDF：`pypdf` fallback。
- Markdown / TXT：`text_file`。
- CSV / TSV：`table_file`。
- XLSX / XLSM：`table_file`，需要 `openpyxl`。

## 可选增强

- Docling：结构化 PDF 解析。
- MinerU：复杂版式、表格密集、OCR 场景。
- Docling / MinerU 不可用时必须自动降级到 `pypdf`，并记录降级原因。

## 验证清单

- 至少 3 种文档格式能生成 parent/child chunk。
- 每个 child chunk 都有稳定 `chunk_key` 与 `parent_key`。
- `parse_report`、`warnings`、`parser_used`、`chunker_used` 能落库到导入任务。
- 相同内容重复导入不会生成重复 chunk。
- 解析降级和解析失败都有可复盘日志。
