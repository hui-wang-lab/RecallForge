# Parent Chunk Quality Optimization Spec

## 背景

在手动上传 `测试条款结构.pdf` 后，`rag_parent_chunks` 只生成 1 条记录，而 `rag_chunks` 已生成 43 个 child chunk。导入诊断显示：

- `parser_used=mineru`
- `chunker_used=contract_terms`
- `parent_chunk_count=1`
- `child_chunk_count=43`
- `heading_level_counts={"2": 35}`
- parent 覆盖 page 1-9，`heading_path=["Document"]`

这说明解析与 child 切片并未完全失败，质量问题集中在 parent 分组：保险条款 PDF 主要使用“第 X 条”作为结构边界，没有更高一级“章/篇/部”标题，导致合同模板的 chapter parent 策略退化为整篇文档一个 parent。

## 质量目标

RecallForge 的 small-to-big 策略要求 child chunk 用于向量召回，parent chunk 用于补足上下文。parent chunk 不能过粗，否则会在 parent expansion 阶段引入整篇文档噪声，降低引用粒度和回答可诊断性。

合同/保险条款类文档应满足：

- 有章/篇/部等高层标题时，parent 优先按高层标题分组。
- 没有高层标题但存在多个“第 X 条”或 `article N` 边界时，parent 必须按条款边界兜底分组。
- 每个 parent 必须写入估算 `token_count`，供检索阶段做 parent 截断和质量诊断。
- 兜底分组必须写入 `warnings` / `parse_report`，便于复盘为什么没有使用 chapter parent。

## 规则

### contract_terms parent fallback

`contract_terms` chunker 的 parent 分组顺序为：

1. 先按章/篇/部/section 等高层标题生成 parent。
2. 如果高层分组数量小于等于 1，且文档中识别到至少 3 个条款标题，则启用 article parent fallback。
3. article parent fallback 以每个“第 X 条”或 `article N` 作为新 parent 的开始。
4. 条款前导内容保留为独立 preamble parent，不强行并入第一条。
5. parent metadata 必须记录 `template_rule` 与 fallback 策略，例如 `contract_parent_article`、`parent_fallback=article`.

### token_count

所有通过共享 `make_parent_from_blocks()` 构造的 parent，必须在 metadata 中写入 `token_count`。入库阶段继续复用现有映射：

```python
ParentChunk.metadata["token_count"] -> rag_parent_chunks.token_count
```

### 诊断指标

启用 fallback 时，chunker warning 必须包含：

```text
[contract_parent_fallback] grouped chapterless contract into N article parent chunks
```

## 验收

- 对只有“第 X 条”标题、没有“章/篇/部”的合同文档，`parent_chunk_count > 1`。
- 同一条款被切成多个 child 时，这些 child 仍挂到同一个条款 parent。
- 有章级结构的合同文档继续按章级 parent 分组。
- parent 入库记录的 `token_count` 不再为 `NULL`。
- 单元测试覆盖 article fallback 和 parent token_count 映射。
