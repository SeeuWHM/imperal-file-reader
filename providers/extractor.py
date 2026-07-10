"""File Reader · doc-extractor engine client — the single storage/RAG plane.

Every uploaded file becomes stored text + chunks + embeddings in the shared
engine, partitioned source="filereader" and scoped to the user's imperal_id
(fail-closed). Unlike Google Drive Connector, there is no external source to
re-fetch from: the raw bytes exist ONLY in memory for the span of the
`ingest()` call. Once this call returns, the caller MUST discard them — from
here on the engine holds the only copy of the extracted text, ever.

No embedding runs on the read path: indexing happens once, in the
background, at ingest. Reads come straight from stored text: no re-extract,
no re-embed, no second look at the original bytes (there is no way to get a
second look — they were never kept).
"""
from __future__ import annotations

import json
import logging

from .helpers import DOC_EXTRACTOR_TOKEN, DOC_EXTRACTOR_URL, SOURCE

log = logging.getLogger("file_reader")


def _diagnostic_payload(data: dict | None) -> str:
    """Compact JSON for logs: enough to compare /text vs /documents payloads
    without dumping full file text."""
    if not isinstance(data, dict):
        return "{}"
    keys = (
        "document_id", "offset", "limit", "total_chars", "truncated",
        "status", "stage", "chunk_count", "error", "error_code", "filename",
    )
    snap = {k: data.get(k) for k in keys if k in data}
    text = data.get("text")
    if isinstance(text, str):
        snap["text_len"] = len(text)
    preview = data.get("preview")
    if isinstance(preview, str):
        snap["preview_len"] = len(preview)
    truth = classify_extraction(data)
    for k in ("extraction_method", "image_ai_used", "ocr_used"):
        if truth.get(k) is not None:
            snap[k] = truth.get(k)
    return json.dumps(snap, ensure_ascii=False, sort_keys=True)


_DOCUMENTS_URL = f"{DOC_EXTRACTOR_URL}/v1/documents"
_SEARCH_URL = f"{DOC_EXTRACTOR_URL}/v1/search"


def _auth_headers() -> dict[str, str]:
    if not DOC_EXTRACTOR_TOKEN:
        return {}
    return {"Authorization": f"Bearer {DOC_EXTRACTOR_TOKEN}"}

# Engine statuses that mean "content is available to read/search".
READY_STATES = ("processed", "cached")
# Engine statuses that are still in-flight: POST /v1/documents returns
# immediately with status=pending and the engine finishes extraction+embedding
# in ITS OWN background (v1.6.0 async redesign, 2026-07-04). The caller must
# poll until the status leaves this set — the POST's transient status is NOT
# the real outcome.
PENDING_STATES = ("pending", "processing")
# Truthful engine-side extraction method labels we surface without guessing.
IMAGE_METHODS = ("ai_vision", "ocr", "hybrid")


def classify_extraction(data: dict | None) -> dict:
    """Return a small, non-fabricated extraction-truth + quality snapshot from
    engine payload fields only. Never invents a value: if the backend did not
    send a field, we report None/False instead of guessing from mime or status.

    This lets handlers and logs stay honest while the backend evolves toward
    image-only AI and classic extractor for everything else. text_quality/
    noise_score are the engine's cheap boilerplate-ratio proxy (see
    whm-doc-extractor-api's extractors/normalize.py) — a signal, not a
    guarantee; is_partial mirrors the engine's own truncation flag.
    """
    if not isinstance(data, dict):
        return {
            "extraction_method": None,
            "image_ai_used": False,
            "ocr_used": False,
            "is_partial": False,
            "text_quality": None,
            "noise_score": None,
        }
    method = data.get("extraction_method")
    ocr_used = data.get("ocr_used")
    if ocr_used is None:
        ocr_used = (method == "ocr")
    image_ai_used = data.get("image_ai_used")
    if image_ai_used is None:
        image_ai_used = method == "ai_vision"
    return {
        "extraction_method": method,
        "image_ai_used": bool(image_ai_used),
        "ocr_used": bool(ocr_used),
        "is_partial": bool(data.get("is_partial")),
        "text_quality": data.get("text_quality"),
        "noise_score": data.get("noise_score"),
    }


def imperal_id(ctx) -> str:
    """Canonical user id scoping ALL engine storage. Missing → hard error: we
    must never ingest/read under an unscoped or wrong identity."""
    user = getattr(ctx, "user", None)
    uid = getattr(user, "imperal_id", None) if user else None
    if not uid:
        raise RuntimeError("no user context (imperal_id) — cannot scope file storage")
    return uid


async def _send(ctx, method: str, url: str, **kwargs):
    """One retry on transient 5xx / network error — absorbs the platform's
    'first call fails, retry works' infra transients. Real 4xx are returned
    as-is for the caller to interpret (e.g. 404 → expired/gone)."""
    call = getattr(ctx.http, method)
    last: Exception | None = None
    for _ in range(2):
        try:
            resp = await call(url, **kwargs)
        except Exception as e:  # noqa: BLE001 - network/timeout → retry once
            last = e
            continue
        if resp.status_code >= 500:
            last = RuntimeError(f"engine returned {resp.status_code}")
            continue
        return resp
    raise last if last else RuntimeError("engine request failed")


async def ingest(ctx, *, filename: str, content: bytes, mime_type: str | None = None) -> dict:
    """Hand the engine the raw bytes directly (real multipart, not a
    URL-fetch — there is nothing external to fetch from). Idempotent by
    (source, imperal_id, sha256) on the engine side: re-sending an identical
    file is a fast `cached` hit, no re-extract/re-embed. Returns the
    DocumentOut dict. The caller must not retain `content` after this call
    returns — this is the ONLY place raw bytes exist in this extension."""
    files = {"files": (filename or "file", content, mime_type or "application/octet-stream")}
    resp = await _send(ctx, "post", _DOCUMENTS_URL, data={
        "source": SOURCE, "imperal_id": imperal_id(ctx),
    }, files=files, headers=_auth_headers(), timeout=120)
    resp.raise_for_status()
    docs = ((resp.json() or {}).get("data") or {}).get("documents") or []
    if not docs:
        raise RuntimeError("engine returned no document")
    return docs[0]


async def read_text(ctx, document_id: int, offset: int = 0, limit: int = 40_000) -> dict:
    """Windowed plain text from the engine's stored blob. Returns
    {text, offset, limit, total_chars, truncated}. Raises on 404 (deleted by
    TTL purge) / 409 (no text yet) so the caller can react (mark expired)."""
    resp = await _send(ctx, "get", f"{_DOCUMENTS_URL}/{document_id}/text", params={
        "source": SOURCE, "imperal_id": imperal_id(ctx), "offset": offset, "limit": limit,
    }, headers=_auth_headers(), timeout=60)
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or {}
    log.info(
        "doc_extractor.read_text document_id=%s payload=%s",
        document_id,
        _diagnostic_payload(data),
    )
    return data


async def search(ctx, query: str, k: int = 6) -> list[dict]:
    """Semantic RAG over THIS user's filereader chunks only — top-K most
    relevant chunks (not whole files). Returns
    [{document_id, filename, seq, text, score}]."""
    resp = await _send(ctx, "post", _SEARCH_URL, json={
        "source": SOURCE, "imperal_id": imperal_id(ctx), "query": query, "k": k,
    }, headers=_auth_headers(), timeout=60)
    resp.raise_for_status()
    return ((resp.json() or {}).get("data") or {}).get("hits") or []


async def overview(ctx, document_id: int) -> dict:
    """Cheap recall — metadata + preview, no full read. Returns DocumentOut."""
    resp = await _send(ctx, "get", f"{_DOCUMENTS_URL}/{document_id}", params={
        "source": SOURCE, "imperal_id": imperal_id(ctx),
    }, headers=_auth_headers(), timeout=30)
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or {}
    log.info(
        "doc_extractor.overview document_id=%s payload=%s",
        document_id,
        _diagnostic_payload(data),
    )
    return data


async def delete(ctx, document_id: int) -> bool:
    """Evict a document from the engine (PG cascade + NC blob). Used by
    forget_files. 404 = already gone (e.g. TTL already purged it) → treat as
    done, not an error."""
    resp = await _send(ctx, "delete", f"{_DOCUMENTS_URL}/{document_id}", params={
        "source": SOURCE, "imperal_id": imperal_id(ctx),
    }, headers=_auth_headers(), timeout=30)
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return True
