"""Shared constants for the File Reader provider layer."""
from __future__ import annotations

import os

FILES_COLLECTION = "filereader_files"

# Public route — this extension runs on whm-ai-worker; the engine
# (whm-doc-extractor-api) lives on api-server. Must go through the
# nginx-proxied public path (same pattern as web-tools' WEB_TOOLS_API_URL and
# Google Drive Connector's DOC_EXTRACTOR_URL).
DOC_EXTRACTOR_URL = "https://api.webhostmost.com/doc-extractor"

# Optional bearer token for the extractor API. When unset, the client sends no
# Authorization header so legacy no-auth deployments keep working.
DOC_EXTRACTOR_TOKEN = (os.getenv("DOC_EXTRACTOR_TOKEN") or "").strip()

# Hard-partitions this extension's documents/chunks from Google Drive
# Connector's inside the shared engine — see whm-doc-extractor-api's
# app/documents.py: every row + blob + vector query is scoped by
# (source, imperal_id), fail-closed.
SOURCE = "filereader"

# A `pending` record older than this with no progress means the background
# ingest coroutine died mid-flight (e.g. a worker restart) — the raw bytes
# lived ONLY in that coroutine's memory and are gone for good. Surface it as
# a clear failure instead of leaving the file "processing" forever.
STALE_PENDING_S = 600
