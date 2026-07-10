# File Reader Implementation Checklist (Federal Grade)

This checklist is the execution companion to `FILE-READER-FEDERAL-GRADE-GUIDE.md` and `FILE-READER-API-CONTRACT.md`.

It is written for a low-context coding agent. Every item should be implemented explicitly. Do not improvise hidden shortcuts.

---

## 0. Mission

Upgrade the File Reader system app from a generic extractor into a federal-grade, LLM-native document interface.

Success means:
- less junk returned to Webbee
- fewer wasted tokens
- more structured and trustworthy responses
- explicit quality signals
- clean split between overview, preview, full read, and debug/raw

---

## 1. Delivery Phases

- **Phase A:** contract and scaffolding
- **Phase B:** backend response shaping
- **Phase C:** extension/tool surface alignment
- **Phase D:** quality, noise, and anomaly logic
- **Phase E:** search and table specialization
- **Phase F:** tests, regression, rollout

Do not skip phase ordering unless there is a very good reason.

---

## 2. Phase A — Contract and Scaffolding

### A1. Freeze target contract
- [ ] Create or update internal API docs to match `FILE-READER-API-CONTRACT.md`
- [ ] Enumerate the exact endpoint/tool names to support
- [ ] Enumerate required request params and response fields
- [ ] Lock shared enums:
  - [ ] file `type`
  - [ ] file `status`
  - [ ] warning `severity`
  - [ ] error `code`
  - [ ] read `mode`
  - [ ] budget `size`

### A2. Add shared response builders on backend
- [ ] Create a shared success response builder
- [ ] Create a shared error response builder
- [ ] Create a shared file metadata serializer
- [ ] Create a shared quality serializer
- [ ] Create a shared warning serializer

### A3. Add internal typed models
- [ ] `FileMeta`
- [ ] `QualityEnvelope`
- [ ] `WarningItem`
- [ ] `ErrorEnvelope`
- [ ] `SectionRef`
- [ ] `DocumentChunk`
- [ ] `TableDescription`
- [ ] `TableRowSample`

### A4. Ban raw-blob defaults
- [ ] Find current endpoints returning plain extracted text by default
- [ ] Mark them deprecated internally
- [ ] Add TODO comments pointing to the new clean contract
- [ ] Ensure new callers do not default to raw mode

---

## 3. Phase B — Backend Response Shaping

### B1. Separate pipeline layers
Backend pipeline must become:
1. raw extraction
2. normalization
3. structural parsing
4. quality scoring
5. response shaping

- [ ] Identify where current code mixes extraction with final response
- [ ] Split normalization into its own function/module
- [ ] Split structural parsing into its own function/module
- [ ] Split quality scoring into its own function/module
- [ ] Ensure LLM-facing handlers consume shaped results, not raw extractor output

### B2. Implement `file_overview`
- [ ] Return minimal metadata
- [ ] Return short description
- [ ] Return tiny structure preview
- [ ] Return quality + warnings
- [ ] Do not return large text bodies

### B3. Implement `read_file_preview`
- [ ] Return summary
- [ ] Return section list if applicable
- [ ] Return representative excerpts
- [ ] Return key points
- [ ] Return next recommended actions
- [ ] Keep payload token-cheap

### B4. Implement `read_file_full`
- [ ] Return structured chunks
- [ ] Include section IDs/titles
- [ ] Include offsets and token estimates
- [ ] Include has_more / next_offset if paginated
- [ ] Never return an unlabeled raw monolith

### B5. Implement `read_raw_debug`
- [ ] Keep it explicit and non-default
- [ ] Include extraction method metadata
- [ ] Include raw char count
- [ ] Ensure chat/default tool paths do not call it automatically

---

## 4. Phase C — Extension / Tool Surface Changes

### C1. Audit current tool names and behavior
- [ ] List current File Reader tools
- [ ] Mark which ones map to:
  - [ ] list
  - [ ] overview
  - [ ] preview
  - [ ] full read
  - [ ] search
  - [ ] forget/delete
- [ ] Note gaps against target contract

### C2. Align tool descriptions for agent usage
- [ ] Update tool descriptions so the agent knows when to use overview vs full read
- [ ] Make preview the preferred first read path
- [ ] Warn against full raw text unless necessary
- [ ] Mention table-first behavior for spreadsheets

### C3. Add missing tools or modes
- [ ] Add `read_file_preview` or equivalent mode
- [ ] Add `describe_table`
- [ ] Add `sample_table_rows`
- [ ] Add `read_sections`
- [ ] Add debug/raw read only if necessary and clearly flagged

### C4. Preserve backward compatibility carefully
- [ ] If old tool names must remain, internally remap to new backend contract
- [ ] Add a compatibility wrapper if needed
- [ ] Do not silently drop critical fields from old responses without migration plan

---

## 5. Phase D — Quality, Noise, and Anomaly Logic

### D1. Quality envelope implementation
For every successful read-like response:
- [ ] include `text_quality`
- [ ] include `structure_quality`
- [ ] include `noise_score`
- [ ] include `ocr_used`
- [ ] include `is_truncated`
- [ ] include `is_partial`
- [ ] include `has_tables`
- [ ] include `has_images`
- [ ] include `has_repeated_headers`
- [ ] include `has_repeated_footers`
- [ ] include `has_suspicious_artifacts`
- [ ] include estimated token counts raw vs clean

### D2. Boilerplate suppression
- [ ] Detect repeated page headers
- [ ] Detect repeated page footers
- [ ] Detect duplicated slide template text
- [ ] Remove empty/near-empty artifact lines
- [ ] Collapse repeated identical lines above threshold
- [ ] Keep an audit trail of what was removed for debug mode

### D3. Suspicious segment detection
- [ ] Detect abrupt language switches
- [ ] Detect low-coherence isolated fragments
- [ ] Detect casual/chat-like phrases inside formal documents
- [ ] Detect short garbage blocks with low lexical value
- [ ] Record anomaly count
- [ ] Emit warnings when anomalies exist

### D4. Truncation honesty
- [ ] If preview is shortened, set explicit preview truncation indicators
- [ ] If full read is paginated, expose `has_more`
- [ ] If extraction itself is partial, set `is_partial`
- [ ] Never let the agent infer hidden truncation

---

## 6. Phase E — Structure-Aware Parsing and Table Specialization

### E1. Structure-aware chunking
- [ ] Replace pure fixed-length chunking where possible
- [ ] Chunk by headings for docs
- [ ] Chunk by slide for presentations
- [ ] Chunk by logical text blocks for PDFs
- [ ] Chunk by sheet/row windows for spreadsheets
- [ ] Preserve order fields and source span references

### E2. DOCX/document parsing improvements
- [ ] Extract title separately
- [ ] Extract headings with levels
- [ ] Preserve lists as lists
- [ ] Separate tables from paragraphs
- [ ] Detect appendices/annexes when possible

### E3. PPT/PPTX parsing improvements
- [ ] Preserve slide number
- [ ] Extract slide title
- [ ] Extract bullet hierarchy if possible
- [ ] Separate slide notes from visible text
- [ ] Suppress repeated master/footer text

### E4. Spreadsheet specialization
- [ ] Detect sheet names
- [ ] Detect header row
- [ ] Infer column types
- [ ] Compute row/column counts
- [ ] Provide row samples in typed JSON
- [ ] Track null ratio per column
- [ ] Detect duplicate header rows
- [ ] Expose schema without flattening to prose

### E5. PDF handling improvements
- [ ] Detect page boundaries
- [ ] Detect OCR usage
- [ ] Preserve major headings where possible
- [ ] Track pages with no text
- [ ] Flag if document appears scan-only or image-heavy

---

## 7. Phase F — Search Quality Improvements

### F1. Search indexing unit audit
- [ ] Determine whether current indexing unit is raw chunk, paragraph, page, or full blob
- [ ] Move toward structure-aware chunk indexing

### F2. Quality-weighted ranking
- [ ] Add chunk quality weight to ranking
- [ ] Add title/heading boost
- [ ] Add anomaly penalty
- [ ] Add table/header relevance boost where appropriate

### F3. Search result contract
- [ ] Return file ID and file name
- [ ] Return source location
- [ ] Return score
- [ ] Return snippet
- [ ] Return quality influence fields if available

### F4. Search budget discipline
- [ ] Do not return giant snippets
- [ ] Cap snippet length sensibly
- [ ] Prefer multiple precise hits over one giant blob

---

## 8. Error Handling Checklist

### EHC1. Add typed errors
- [ ] `FILE_NOT_FOUND`
- [ ] `ACCESS_DENIED`
- [ ] `INDEXING_PENDING`
- [ ] `INDEXING_FAILED`
- [ ] `UNSUPPORTED_FILE_TYPE`
- [ ] `NO_TEXT_FOUND`
- [ ] `PARTIAL_EXTRACTION_ONLY`
- [ ] `OCR_REQUIRED`
- [ ] `OCR_FAILED`
- [ ] `INVALID_SECTION_ID`
- [ ] `INVALID_SHEET_NAME`
- [ ] `OFFSET_OUT_OF_RANGE`
- [ ] `INTERNAL_PIPELINE_ERROR`

### EHC2. Retryability discipline
- [ ] Mark only truly retryable cases as retryable
- [ ] Do not mark unsupported formats retryable
- [ ] Mark indexing-pending retryable
- [ ] Mark internal transient failures retryable only if justified

### EHC3. Honest user-safe messages
- [ ] Error messages should be understandable
- [ ] Do not leak secrets, raw stack traces, or private paths
- [ ] Do include enough detail for diagnosis where appropriate

---

## 9. Metrics and Observability

### O1. Add extraction stats
- [ ] raw char count
- [ ] clean char count
- [ ] deduped char count
- [ ] token estimate raw
- [ ] token estimate clean
- [ ] compression gain

### O2. Add quality telemetry
- [ ] number of files with OCR
- [ ] average noise score by mime type
- [ ] anomaly rate by file type
- [ ] preview-to-full-read ratio
- [ ] average payload size by endpoint

### O3. Add failure telemetry
- [ ] indexing failures by type
- [ ] no-text-found counts
- [ ] OCR failure counts
- [ ] unsupported format counts

### O4. Add token waste telemetry
- [ ] average response chars sent to agent by endpoint
- [ ] percent removable boilerplate
- [ ] average snippet size in search results

---

## 10. Tests

### T1. Unit tests
- [ ] response builders
- [ ] quality scoring helpers
- [ ] warning generation
- [ ] boilerplate suppression
- [ ] anomaly detection
- [ ] type inference for spreadsheet columns

### T2. Contract tests
- [ ] every success response includes `ok`, `file`, `quality`, `warnings`, `data`
- [ ] every error response includes `ok=false` and typed `error`
- [ ] preview responses stay under expected size budget
- [ ] full read responses include chunk metadata

### T3. Regression samples
Build a fixture set covering:
- [ ] clean text doc
- [ ] noisy DOCX with repeated headers
- [ ] PPTX with footer duplication
- [ ] spreadsheet with clean schema
- [ ] spreadsheet with duplicate header rows
- [ ] OCR PDF
- [ ] image-only PDF
- [ ] mixed-language document
- [ ] document with suspicious random text fragment

### T4. Behavior tests
- [ ] overview does not dump full text
- [ ] preview is meaningfully shorter than full read
- [ ] search returns source locations
- [ ] table endpoints do not flatten rows to prose by default
- [ ] raw mode differs from clean mode in expected ways

---

## 11. Rollout Checklist

### R1. Internal rollout
- [ ] deploy behind feature flag if possible
- [ ] test with known files first
- [ ] compare old vs new payload sizes
- [ ] compare old vs new answer usefulness for Webbee tasks

### R2. Compatibility review
- [ ] identify existing consumers relying on old raw text shape
- [ ] patch or shim them before hard cutover
- [ ] document migration path

### R3. Safety review
- [ ] verify no debug/raw path is used as default
- [ ] verify partial extraction is never hidden
- [ ] verify warnings are surfaced

### R4. Final go/no-go criteria
Do not call the rollout complete until:
- [ ] overview/preview/full split is live
- [ ] quality envelope is live
- [ ] table-first path is live
- [ ] typed errors are live
- [ ] regression tests pass
- [ ] token payloads improved materially

---

## 12. Claude-Proof Implementation Notes

This section exists specifically because a low-quality agent may otherwise make dumb shortcuts.

### Hard rules
- [ ] Do not solve preview by substringing the first 2000 chars.
- [ ] Do not call flattened spreadsheet text “structured”.
- [ ] Do not add quality fields with fake constants just to satisfy schema.
- [ ] Do not silently swallow anomalies.
- [ ] Do not hide `is_partial=true` to make outputs look nicer.
- [ ] Do not expose raw extraction as the main response just because it is easier.
- [ ] Do not invent section titles if none exist; use null or fallback labels honestly.

### If uncertain
- [ ] prefer explicit nulls over invented values
- [ ] emit warning instead of pretending confidence
- [ ] keep response shape stable even when extraction quality is poor

---

## 13. Definition of Done

The File Reader upgrade is done only when all of the following are true:

- [ ] Webbee can inspect a file cheaply before reading it deeply
- [ ] Webbee can tell whether extraction quality is good or bad
- [ ] Webbee receives far less boilerplate and duplicate junk
- [ ] spreadsheets are available as typed tables
- [ ] long files are chunked structurally
- [ ] anomalies and partial extraction are visible
- [ ] error states are typed and honest
- [ ] raw extraction is debug-only, not the main interface

If any of the above is false, the system is not federal-grade yet.
