# File Reader API Contract (Federal Grade)

This document defines the target LLM-facing API contract for the File Reader system app. It is intentionally explicit and implementation-oriented so it can be followed by a low-context coding agent without guesswork.

---

## 1. Goal

The File Reader API must stop returning raw extraction blobs as the default interface.

Instead, it must return:
- typed metadata
- structured summaries
- quality signals
- warning flags
- token-efficient previews
- explicit full-read paths
- typed table/document shapes where possible

The default consumer is Webbee, the system agent. The API must be optimized for:
- low hallucination risk
- low token waste
- strong explainability
- deterministic structure
- graceful degradation when extraction quality is poor

---

## 2. Design Rules

1. **Never make raw extracted text the default response shape.**
2. **Every read response must carry quality and warning envelopes.**
3. **Every large document must support preview before full read.**
4. **Tabular files must expose typed structure, not only flattened text.**
5. **Errors must be typed and honest.**
6. **Chunking must be structure-aware, not only length-based.**
7. **The agent must always know if content is partial, noisy, OCR-based, or suspicious.**

---

## 3. Core Endpoint Set

Recommended LLM-facing endpoint family:

1. `list_files`
2. `file_overview`
3. `read_file_preview`
4. `read_file_full`
5. `search_file`
6. `search_files`
7. `describe_table`
8. `sample_table_rows`
9. `read_sections`
10. `read_raw_debug` (non-default, internal/debug use)

If public API surface must stay small, keep tool names compact but preserve the logical split.

---

## 4. Shared Response Envelope

All successful responses should include this top-level shape:

```json
{
  "ok": true,
  "file": {},
  "quality": {},
  "warnings": [],
  "data": {}
}
```

All failed responses should include:

```json
{
  "ok": false,
  "error": {
    "code": "INDEXING_PENDING",
    "message": "File indexing is still in progress.",
    "retryable": true,
    "details": {}
  }
}
```

### Required response rules

- `ok` is mandatory.
- `warnings` is always present on success, even if empty.
- `quality` is always present on success, even if some fields are null.
- `file` is always present on success.
- `data` contains endpoint-specific payload.

---

## 5. Shared File Object

```json
{
  "id": "fr_123",
  "name": "Moldovan companies.xlsx",
  "display_name": "Moldovan companies",
  "type": "spreadsheet",
  "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "size_bytes": 182340,
  "status": "ready",
  "source": "upload",
  "created_at": "2026-07-08T21:00:00Z",
  "updated_at": "2026-07-08T21:05:00Z",
  "language": "ru",
  "languages_detected": ["ru", "en"],
  "hash": "sha256:...",
  "page_count": null,
  "sheet_count": 1,
  "slide_count": null,
  "row_count": 187,
  "column_count": 7
}
```

### Notes

- `type` should be one of a controlled enum:
  - `text`
  - `document`
  - `spreadsheet`
  - `presentation`
  - `pdf`
  - `image`
  - `archive`
  - `unknown`
- Count fields may be null if unknown.
- `status` should be one of:
  - `pending`
  - `indexing`
  - `ready`
  - `failed`
  - `partial`
  - `expired`

---

## 6. Shared Quality Object

```json
{
  "text_quality": 0.91,
  "structure_quality": 0.88,
  "noise_score": 0.12,
  "ocr_used": false,
  "ocr_confidence": null,
  "is_truncated": false,
  "is_partial": false,
  "has_tables": true,
  "has_images": false,
  "has_repeated_headers": false,
  "has_repeated_footers": false,
  "has_suspicious_artifacts": false,
  "dominant_language": "ru",
  "low_coherence_segment_count": 0,
  "anomaly_count": 0,
  "estimated_input_tokens_raw": 22000,
  "estimated_input_tokens_clean": 8300,
  "compression_gain": 0.62
}
```

### Rules

- All scores are `0..1`.
- `noise_score` closer to `1` means more noise.
- `compression_gain` means token savings from cleaning.
- These fields are not optional in the contract, though values may be null if truly unavailable.

---

## 7. Shared Warning Object

Each warning item:

```json
{
  "code": "REPEATED_FOOTERS_REMOVED",
  "severity": "info",
  "message": "Repeated footer blocks were removed during normalization."
}
```

### Warning severity enum
- `info`
- `warning`
- `critical`

### Example warning codes
- `OCR_USED`
- `LOW_TEXT_CONFIDENCE`
- `PARTIAL_EXTRACTION`
- `REPEATED_HEADERS_REMOVED`
- `REPEATED_FOOTERS_REMOVED`
- `SUSPICIOUS_SEGMENTS_DETECTED`
- `TABLE_SCHEMA_INFERRED`
- `LANGUAGE_MIXED`
- `TEXT_TRUNCATED`
- `IMAGE_ONLY_PAGES`

---

## 8. list_files

### Purpose
Return cheap inventory only. No full preview text.

### Response shape

```json
{
  "ok": true,
  "files": [
    {
      "id": "fr_123",
      "name": "Moldovan companies.xlsx",
      "type": "spreadsheet",
      "size_bytes": 182340,
      "status": "ready",
      "searchable": true,
      "language": "ru",
      "short_description": "Spreadsheet of company contact and outreach data."
    }
  ]
}
```

### Must not include
- full extracted text
- giant preview blobs
- redundant metadata not needed for listing

---

## 9. file_overview

### Purpose
Cheap first-touch inspection before deciding whether to preview or fully read.

### Response

```json
{
  "ok": true,
  "file": {"id": "fr_123", "name": "Moldovan companies.xlsx", "type": "spreadsheet"},
  "quality": {"text_quality": 0.95, "structure_quality": 0.98, "noise_score": 0.04},
  "warnings": [],
  "data": {
    "kind": "lead_table",
    "title": "Moldovan companies",
    "short_description": "Spreadsheet with company names, links, emails, rating and outreach status.",
    "key_stats": {
      "sheets": 1,
      "rows": 187,
      "columns": 7
    },
    "structure_preview": {
      "sheet_names": ["Sheet1"],
      "columns": ["Название", "Link", "Email", "Оценка", "Hosting", "Отправил", "Статус"]
    },
    "representative_excerpts": [],
    "next_actions": ["describe_table", "sample_table_rows"]
  }
}
```

### Rules

- `file_overview` must stay cheap.
- It may include tiny representative excerpts, but never large freeform text dumps.
- It must suggest logical next actions.

---

## 10. read_file_preview

### Purpose
Return a token-efficient, LLM-friendly summary of the file.

### Response for documents

```json
{
  "ok": true,
  "file": {"id": "fr_555", "name": "lab3_prolog_report.docx", "type": "document"},
  "quality": {
    "text_quality": 0.78,
    "structure_quality": 0.71,
    "noise_score": 0.22,
    "has_suspicious_artifacts": true,
    "anomaly_count": 1
  },
  "warnings": [
    {
      "code": "SUSPICIOUS_SEGMENTS_DETECTED",
      "severity": "warning",
      "message": "One segment appears weakly related to the dominant document topic."
    }
  ],
  "data": {
    "title": "Lab 3 Prolog Report",
    "document_kind": "academic_report",
    "short_summary": "Educational report describing a Prolog assignment with examples and explanations.",
    "sections": [
      {"id": "s1", "title": "Introduction", "level": 1},
      {"id": "s2", "title": "Task Description", "level": 1},
      {"id": "s3", "title": "Implementation", "level": 1},
      {"id": "s4", "title": "Conclusion", "level": 1}
    ],
    "key_points": [
      "Describes logic programming assignment goals.",
      "Includes code examples and explanation.",
      "Contains final conclusions section."
    ],
    "representative_excerpts": [
      {
        "section_id": "s2",
        "label": "main_content_excerpt",
        "text": "..."
      },
      {
        "section_id": null,
        "label": "anomaly_excerpt",
        "text": "..."
      }
    ],
    "next_actions": ["read_sections", "read_file_full", "search_file"]
  }
}
```

### Rules

- Preview must prefer summaries, headings, key points, and representative excerpts.
- Preview must avoid returning the first raw N characters blindly.
- If anomalies exist, expose them in warnings and excerpts.

---

## 11. read_file_full

### Purpose
Return full content in structured chunks.

### Response

```json
{
  "ok": true,
  "file": {"id": "fr_555", "name": "lab3_prolog_report.docx", "type": "document"},
  "quality": {...},
  "warnings": [...],
  "data": {
    "content_mode": "clean",
    "chunking": {
      "strategy": "structure_aware",
      "chunk_count": 6
    },
    "chunks": [
      {
        "chunk_id": "c1",
        "section_id": "s1",
        "section_title": "Introduction",
        "order": 1,
        "char_start": 0,
        "char_end": 1400,
        "estimated_tokens": 320,
        "quality": {
          "noise_score": 0.05,
          "low_confidence": false
        },
        "text": "..."
      }
    ],
    "has_more": false,
    "next_offset": null
  }
}
```

### Rules

- `read_file_full` may paginate if necessary.
- Chunks must align to sections, slides, or table regions where possible.
- Each chunk must expose local quality hints.

---

## 12. search_file / search_files

### Purpose
Find the most relevant chunks or rows without reading the whole file.

### Response

```json
{
  "ok": true,
  "query": "email columns and outreach status",
  "results": [
    {
      "file_id": "fr_123",
      "file_name": "Moldovan companies.xlsx",
      "location": {
        "kind": "table",
        "sheet": "Sheet1",
        "row_range": "1:25"
      },
      "score": 0.93,
      "quality_weight": 0.98,
      "snippet": "Columns: Название, Link, Email, Оценка, Hosting, Отправил, Статус"
    }
  ]
}
```

### Ranking guidance

Search ranking should combine:
- semantic relevance
- structure importance
- title boost
- anomaly penalty
- chunk quality weight

---

## 13. describe_table

### Purpose
Expose table schema as first-class structured data.

### Response

```json
{
  "ok": true,
  "file": {"id": "fr_123", "name": "Moldovan companies.xlsx", "type": "spreadsheet"},
  "quality": {"text_quality": 0.95, "structure_quality": 0.99, "noise_score": 0.03},
  "warnings": [],
  "data": {
    "sheet": "Sheet1",
    "table_kind": "rectangular",
    "header_row_index": 1,
    "row_count": 187,
    "column_count": 7,
    "columns": [
      {
        "name": "Название",
        "type": "text",
        "nullable": false,
        "sample_values": ["Company A", "Company B"]
      },
      {
        "name": "Link",
        "type": "url",
        "nullable": true,
        "sample_values": ["https://...", "https://..."]
      },
      {
        "name": "Email",
        "type": "email",
        "nullable": true,
        "sample_values": ["info@...", "sales@..."]
      }
    ],
    "stats": {
      "empty_row_count": 0,
      "duplicate_header_rows": 0,
      "column_null_ratio": {
        "Email": 0.42,
        "Hosting": 0.77
      }
    }
  }
}
```

### Rules

- This endpoint must not flatten rows into prose.
- Type inference must be explicit and marked as inferred if not guaranteed.

---

## 14. sample_table_rows

### Purpose
Read row data in a budgeted, typed way.

### Request inputs
- `sheet`
- `offset`
- `limit`
- optional filters
- optional columns

### Response

```json
{
  "ok": true,
  "file": {"id": "fr_123", "name": "Moldovan companies.xlsx", "type": "spreadsheet"},
  "quality": {"text_quality": 0.95, "structure_quality": 0.99, "noise_score": 0.03},
  "warnings": [],
  "data": {
    "sheet": "Sheet1",
    "offset": 0,
    "limit": 3,
    "row_count_total": 187,
    "rows": [
      {
        "row_index": 2,
        "cells": {
          "Название": "Example SRL",
          "Link": "https://example.md",
          "Email": "info@example.md",
          "Оценка": "A",
          "Hosting": "unknown",
          "Отправил": "no",
          "Статус": "new"
        }
      }
    ],
    "has_more": true,
    "next_offset": 3
  }
}
```

---

## 15. read_sections

### Purpose
Allow focused reading of specific sections instead of the full document.

### Response

```json
{
  "ok": true,
  "file": {"id": "fr_555", "name": "lab3_prolog_report.docx", "type": "document"},
  "quality": {...},
  "warnings": [...],
  "data": {
    "sections": [
      {
        "section_id": "s3",
        "title": "Implementation",
        "text": "...",
        "estimated_tokens": 540
      }
    ]
  }
}
```

### Rules

- This endpoint is often cheaper than full-read.
- The agent should be encouraged to use this after preview.

---

## 16. read_raw_debug

### Purpose
Expose raw extraction only for debugging, audits, and pipeline validation.

### Rules

- Never use as default LLM path.
- Must be gated or internal.
- Must include extraction source and raw method metadata.

### Response

```json
{
  "ok": true,
  "file": {"id": "fr_555", "name": "lab3_prolog_report.docx", "type": "document"},
  "data": {
    "extraction_method": "docx_xml_text_pass_v2",
    "raw_text": "...",
    "raw_char_count": 82144
  }
}
```

---

## 17. Error Contract

### Mandatory error shape

```json
{
  "ok": false,
  "error": {
    "code": "NO_TEXT_FOUND",
    "message": "The file was parsed, but no extractable text was found.",
    "retryable": false,
    "details": {
      "file_type": "image"
    }
  }
}
```

### Recommended error codes

- `FILE_NOT_FOUND`
- `ACCESS_DENIED`
- `INDEXING_PENDING`
- `INDEXING_FAILED`
- `UNSUPPORTED_FILE_TYPE`
- `NO_TEXT_FOUND`
- `PARTIAL_EXTRACTION_ONLY`
- `OCR_REQUIRED`
- `OCR_FAILED`
- `PAGE_LIMIT_EXCEEDED`
- `INTERNAL_PIPELINE_ERROR`
- `INVALID_SECTION_ID`
- `INVALID_SHEET_NAME`
- `OFFSET_OUT_OF_RANGE`

### Rules

- `message` must be user-safe and honest.
- `details` may contain technical context, but avoid leaking internal secrets.
- `retryable` must be explicitly set.

---

## 18. Budget Modes

Every read-like endpoint should optionally accept a budget or mode hint.

### Supported budget values
- `small`
- `medium`
- `large`

### Semantics
- `small` = metadata + ultra-compact summary
- `medium` = normal preview / representative sections
- `large` = richer response, still structured

Do not make the client calculate how much content is safe. The backend should shape the payload.

---

## 19. Clean vs Raw Presentation Mode

### Required modes
- `clean` (default)
- `raw` (debug only)

### `clean` mode must apply
- duplicate line suppression
- repeated header/footer removal
- empty block suppression
- artifact filtering
- section-aware normalization
- formatting collapse where useful

### `raw` mode must not apply aggressive cleanup
- used only for debugging extraction quality

---

## 20. Anti-Patterns to Forbid

1. Returning the first N raw characters as “preview”.
2. Returning giant plain-text payloads without quality info.
3. Flattening spreadsheets into prose by default.
4. Hiding partial extraction status.
5. Mixing clean and raw modes silently.
6. Returning chunks without section identity.
7. Search results without source location.
8. No warning when anomalies were detected.
9. No explicit truncation signal.
10. No distinction between unsupported, failed, and pending.

---

## 21. Minimum Acceptance Criteria

An implementation is not federal-grade until all of the following are true:

- every success response includes `file`, `quality`, `warnings`
- preview and full-read are separated
- tables can be described structurally
- search returns source locations
- partial/noisy extraction is explicitly flagged
- raw extraction is not the default path
- errors are typed and retry behavior is honest
- token-heavy content is chunked structurally

---

## 22. Recommended Implementation Order

### Phase 1
1. Add shared response envelope.
2. Add quality and warning objects.
3. Split overview / preview / full.

### Phase 2
4. Add table-first endpoints.
5. Add structure-aware chunking.
6. Add representative excerpt selection.

### Phase 3
7. Add anomaly detection.
8. Add clean/raw modes.
9. Add budget hints and compression stats.

### Phase 4
10. Improve ranking with quality weighting.
11. Add richer OCR diagnostics.
12. Add section-level confidence.

---

## 23. Final Instruction to Implementers

If anything in the pipeline is uncertain, the API must say so explicitly in machine-readable fields.

Silence is not acceptable.
Implicit trust is not acceptable.
Raw blobs are not acceptable as the main interface.

The system agent must be able to tell:
- what this file is
- how trustworthy the extraction is
- where the useful parts are
- what noise was removed
- whether more reading is worth the tokens

That is the bar.
