# File Reader

[![Imperal SDK](https://img.shields.io/badge/imperal--sdk-5.9.3-blue)](https://pypi.org/project/imperal-sdk/)
[![Version](https://img.shields.io/badge/version-0.1.0-green)](https://github.com/SeeuWHM/imperal-file-reader/releases)
[![License](https://img.shields.io/badge/license-LGPL--2.1-orange)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Imperal%20Cloud-purple)](https://panel.imperal.io)

**Universal file reader extension for [Imperal Cloud](https://panel.imperal.io).**

Upload files in the panel — Webbee reads and semantically searches their contents. PDF, Office (docx/xlsx/pptx), plain text, CSV, HTML, and images/scans via the extractor backend. Image-reading policy is backend-owned: the extension never guesses whether a picture was handled by AI vision or OCR — it only reports what the backend actually returned. Nothing is stored but the extracted text — raw files are never kept.

---

## What It Does

Talk to it naturally:

```
"search my uploaded files for the Q3 budget numbers"
"summarize the contract I just uploaded"
"what files do I have and which are ready to search"
```

Or just drag a file onto the panel dropzone — uploading indexes it automatically, no command needed.

---

## Tools

| Tool | Type | Purpose |
|------|------|---------|
| `receive_files` | write | Triggered by the panel dropzone; starts background indexing of uploaded files |
| `list_files` | read | List all uploaded files with `file_id`, status and searchability |
| `file_overview` | read | Name / type / size / status / preview for one or more files |
| `read_file_preview` | read | Token-cheap preview (opening excerpt + a second sample further in) plus extraction quality — check before a full read |
| `read_files` | read | Read a windowed slice of one or more files' extracted text by `file_id` |
| `search_files` | read | Semantic search across the user's files (optionally scoped to `file_ids`) |
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
app.py                Extension + ChatExtension
main.py                entry point (module purge + import order)
schemas.py             Pydantic params
schemas_sdl.py         SDL entities + builders
handlers_upload.py     receive_files (+ background ingest)
handlers_content.py    read_files / file_overview / read_file_preview / search_files
handlers_files.py      list_files / forget_files
panels.py              dropzone + file list (right slot)
skeleton.py            cached counters (ttl 300s)
providers/             SDK-free core (engine client, lifecycle, content ops, response shaping, text windows)
tests/                 pytest (providers, with fakes)
```

## Build

```bash
python -m py_compile *.py providers/*.py
imperal build          # → imperal.json
imperal validate       # → 0 issues
```

Deploy via the Developer Portal git integration (this repo's URL).

---

## Built with

- [imperal-sdk](https://github.com/imperalcloud/imperal-sdk) 5.9
- [Imperal Cloud](https://panel.imperal.io)
