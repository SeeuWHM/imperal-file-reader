# File Reader — AI-only assessment

Date: 2026-07-09

## Decision request reviewed

Requested direction: keep File Reader on an AI-first path instead of trusting plain OCR alone for image/text understanding.

## What is true right now

### Current extension reality
- The `file-reader` extension is a thin client over `whm-doc-extractor-api`.
- The extension itself does **not** run OCR or LLM extraction.
- It only:
  - uploads bytes to the extractor backend,
  - reads extracted text,
  - searches indexed chunks,
  - exposes file metadata and lifecycle.

### Current backend contract reality
- The extractor backend is the component that decides how text is extracted.
- In current local extension docs and metadata, image support is still described in OCR-oriented language.
- Therefore, switching to “AI-only” for image/text understanding is **not** an extension-only toggle. It is primarily a backend extraction-policy change.

## What I fixed locally in this milestone

### 1. Auth readiness is already in place
Confirmed in code:
- `providers/helpers.py` supports `DOC_EXTRACTOR_TOKEN`
- `providers/extractor.py` sends `Authorization: Bearer ...` when configured

This closes the biggest confirmed extension↔backend integration gap.

### 2. Removed misleading OCR wording from extension-facing metadata
Updated:
- `file-reader/app.py`
- `file-reader/README.md`
- `file-reader/imperal.json`
- `file-reader/app.ir.json`

Why:
- if we are moving toward AI-first extraction, the extension should not hard-claim a specific OCR path in user-facing metadata.
- the truthful statement is that images/scans are handled through the extractor backend.

## Detailed assessment of remaining weaknesses

## A. Confirmed extension weaknesses

### A1. Tool surface is still too raw
Current tools:
- `receive_files`
- `read_files`
- `file_overview`
- `search_files`
- `list_files`
- `forget_files`

Missing versus the federal target contract:
- `read_file_preview`
- `read_file_full`
- `describe_table`
- `sample_table_rows`
- `read_sections`
- explicit debug/raw path separation

Impact:
- Webbee can read and search, but she does not yet get a clean low-cost preview path vs deep structured read path.
- This wastes tokens and weakens answer planning.

### A2. Quality envelope is not implemented
Missing in normal success responses:
- `text_quality`
- `structure_quality`
- `noise_score`
- extraction-method visibility
- partial/truncation honesty as a first-class typed envelope
- warnings list with stable codes/severities

Impact:
- Webbee cannot reliably tell whether a file was read well, badly, partially, or noisily.
- This matters even more if image understanding moves to an LLM path.

### A3. Search results are still relatively shallow
Current search output is practical, but not federal-grade.
Missing:
- source-location richness
- chunk quality weighting
- warning/quality influence in ranking
- table-aware retrieval strategy

Impact:
- semantic search works, but ranking quality can drift on noisy files.

### A4. Typed error taxonomy is incomplete
There are honest messages, but not the full stable typed set described by the target contract.
Still missing as a proper surfaced contract:
- `NO_TEXT_FOUND`
- `PARTIAL_EXTRACTION_ONLY`
- `OCR_FAILED` / future AI-vision equivalent
- `OFFSET_OUT_OF_RANGE`
- `INVALID_SECTION_ID`
- `INVALID_SHEET_NAME`
- `INTERNAL_PIPELINE_ERROR`

Impact:
- user honesty is decent already,
- but machine-usable downstream behavior is not yet strict enough.

### A5. Plans/checklists are ahead of implementation
The docs describe a more advanced system than the one currently exposed by tools.
That is not fatal, but it means:
- roadmap items are mixed with current truth,
- some docs still assume OCR-centric framing,
- status accounting is noisy.

## B. Confirmed backend-coupled weaknesses

These are real, but they cannot be fully solved from the extension alone.

### B1. Extraction policy lives in backend, not extension
If you want “File Reader should use a real LLM for image/text understanding,” the extension cannot enforce that by itself.
It needs backend support for at least one of these models:
- vision-first extraction for images,
- OCR + LLM cleanup,
- OCR fallback only,
- hybrid routing by mime type and quality.

### B2. AI-only has real tradeoffs
Pure AI-only extraction sounds sexy, but there is a catch:
- LLM vision is often better than raw OCR on messy screenshots,
- but it is more expensive,
- slower,
- harder to make deterministic,
- and can hallucinate if not grounded carefully.

So the strongest production design is usually **AI-first reasoning with explicit extraction-method metadata**, not blind “LLM for everything.”

### B3. Need extraction-method transparency
If backend moves toward LLM image reading, every successful read-like response should expose at least:
- `extraction_method`
- `method_version`
- `is_partial`
- `confidence` or quality estimate
- warnings when the model inferred or reconstructed uncertain text

Without that, Webbee cannot stay honest.

## C. Realistic best target design for Imperal

If doing this properly, the best design is:

### For normal text documents
- keep deterministic text extraction where available
- then run cleanup/normalization/quality scoring
- avoid paying an LLM tax where plain extraction is already strong

### For screenshots, scans, image-heavy PDFs, messy photos
- use AI vision as primary interpretation path
- keep method metadata explicit
- optionally keep cheap OCR as a supporting fallback, not the public promise

### For user-facing behavior
Webbee should receive:
- preview-first output,
- quality envelope,
- warning codes,
- structured sections/chunks,
- clear extraction method labeling.

That gives the “real LLM” feel without making the system sloppy.

## What I can fix next from this repo

I can do these next, directly in the extension codebase:

1. add preview/full mode split
2. add a first real quality/warnings envelope to tool responses
3. improve typed error shapes
4. improve tool descriptions so Webbee prefers cheap overview/preview first
5. align docs/checklists with current truth and next-phase AI-first backend plan

## What requires backend/server work

These need extractor/backend implementation, not just extension edits:

1. true AI-vision image extraction
2. extraction-method metadata from backend
3. confidence reporting for image/text understanding
4. hybrid routing rules by file type/quality
5. cost/latency controls for AI-heavy extraction

## Bottom line

Your instinct is good:
- plain OCR alone is not enough for the quality bar you want.

But the correct production move is:
- **AI-first where image understanding is needed**,
- **not blind AI-only for every file**,
- and **the extension must expose quality/method truth cleanly to Webbee**.

Right now, the biggest extension-side blocker is no longer auth.
The biggest remaining product gaps are:
1. no preview/full/table split,
2. no quality envelope,
3. no extraction-method transparency,
4. backend policy still owns image-reading behavior.
