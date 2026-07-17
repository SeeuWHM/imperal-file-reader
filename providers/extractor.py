"""File Reader · engine access — delegated to the kernel's ctx.files primitive.

File Mage Rule 13 (2026-07-18): there is ONE engine client in the platform —
the kernel's (core/file_engine.FileEngine, surfaced to every extension as
``ctx.files``). This extension no longer carries its own HTTP plumbing to the
doc-extractor engine; it reads/searches/ingests/deletes THROUGH ctx.files,
which is the same client the brain uses, bound to this user and the
``filereader`` storage partition. Engine URL, auth, retry, and the
(source, imperal_id) scoping all live in the kernel now — change them once,
there.

What stays here is extension-side, not engine-side: the extraction-truth
classifier (``classify_extraction``), the ready/pending state vocabulary the
lifecycle maps against, and the compact diagnostic log line. These operate on
the dicts ctx.files returns; they make no network call.
"""
from __future__ import annotations

import json
import logging

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


def _files(ctx):
    """The kernel's per-user file client (ctx.files). It is wired by the
    kernel context factory (File Mage L1) for every dispatch; its absence means
    the platform is misconfigured, not a user error — fail loudly, never
    silently fall back to a second HTTP client (Rule 13)."""
    files = getattr(ctx, "files", None)
    if files is None:
        raise RuntimeError("file engine unavailable (ctx.files not wired)")
    return files


async def ingest(ctx, *, filename: str, content: bytes, mime_type: str | None = None) -> dict:
    """Hand the engine the raw bytes through ctx.files. Idempotent by
    (source, imperal_id, sha256) on the engine side: re-sending an identical
    file is a fast `cached` hit. Returns the DocumentOut dict. The caller must
    not retain `content` after this call — the engine holds the only copy of
    the extracted text from here on."""
    return await _files(ctx).ingest(content, filename, mime_type=mime_type)


async def read_text(ctx, document_id: int, offset: int = 0, limit: int = 40_000) -> dict:
    """Windowed plain text from the engine's stored blob. Returns
    {text, offset, limit, total_chars, truncated}. Raises on 404 (deleted by
    TTL purge) / 409 (no text yet) so the caller can react (mark expired)."""
    data = await _files(ctx).read(document_id, offset=offset, limit=limit)
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
    return await _files(ctx).search(query, k=k)


async def overview(ctx, document_id: int) -> dict:
    """Cheap recall — metadata + preview, no full read. Returns DocumentOut."""
    data = await _files(ctx).overview(document_id)
    log.info(
        "doc_extractor.overview document_id=%s payload=%s",
        document_id,
        _diagnostic_payload(data),
    )
    return data


async def delete(ctx, document_id: int) -> bool:
    """Evict a document from the engine (PG cascade + NC blob). Used by
    forget_files. 404 = already gone → returns False, not an error."""
    return await _files(ctx).delete(document_id)
