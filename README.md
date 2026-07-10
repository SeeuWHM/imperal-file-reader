# File Reader — Imperal Cloud extension

Upload files in the panel and Webby reads and semantically searches their
contents. Supports PDF, Office (docx/xlsx/pptx), plain text, CSV, HTML, and
images/scans through the extractor backend. Image-reading policy is backend-owned:
this extension does not guess whether a picture was handled by AI vision or OCR — it only reports
what the backend actually returned. **Nothing is stored but the extracted text — your raw files are
never kept.**

- **Developer:** SeeU · **SDK:** imperal-sdk 5.9.x · **Platform:** panel.imperal.io
- **App id:** `file-reader`

## Tools

| Tool | Type | Purpose |
|------|------|---------|
| `receive_files` | write | Triggered by the panel dropzone; starts background indexing of uploaded files |
| `read_files` | read | Read a windowed slice of one or more files' extracted text by `file_id` |
| `search_files` | read | Semantic search across the user's files (optionally scoped to `file_ids`) |
| `file_overview` | read | Name / type / size / status / preview for one or more files |
| `list_files` | read | List all uploaded files with `file_id`, status and searchability |
| `forget_files` | destructive | Permanently delete files (and their engine-stored text) |

## Architecture

Thin SDK layer over an SDK-free `providers/` core (unit-tested with fakes).
The heavy work — text extraction, chunking, embeddings, storage, TTL purge —
runs in a separate engine service (`whm-doc-extractor-api`), reached over its
nginx IP-allowlisted public path. The engine is a dumb mechanism (accept →
stream to disk → extract → store → report state); all policy (quotas, limits,
retry, the pending→indexing→ready state machine) lives here in the extension.

Raw uploaded bytes travel `receive_files` → background job → a single engine
call, and are then discarded — they never touch `ctx.store`.

```
app.py            Extension + ChatExtension
main.py           entry point (module purge + import order)
schemas.py        Pydantic params
schemas_sdl.py    SDL entities + builders
handlers_upload.py    receive_files (+ background ingest)
handlers_content.py   read_files / file_overview / search_files
handlers_files.py     list_files / forget_files
panels.py         dropzone + file list (right slot)
skeleton.py       cached counters (ttl 300s)
providers/        SDK-free core (engine client, lifecycle, content ops)
tests/            pytest (providers, with fakes)
```

## Build

```bash
python -m py_compile *.py providers/*.py
imperal build          # → imperal.json
imperal validate       # → 0 issues
```

Deploy via the Developer Portal git integration (this repo's URL).
