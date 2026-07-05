"""File Reader · file lifecycle — the extension-side brain.

Owns the uploaded-file store record and its state machine so the engine can
stay a dumb cache. ONE authority (this module) means the panel and the
engine never drift: the same code that forgets a file also deletes its
engine doc.

Record shape (filereader_files):
    file_id        = the ctx.store record id itself — the ONLY identifier
                     used in every tool's params (no external id exists,
                     unlike Google Drive Connector's Drive file_id).
    filename, mime_type, size_bytes    # as reported at upload time
    status         pending | indexing | ready | failed | expired
    document_id    engine doc id once ingested (None otherwise)
    chunk_count    0 = not (yet) searchable — a readable-but-not-searchable
                   file is still `ready`, just with chunk_count=0
    error, error_code
    uploaded_at    epoch seconds
    expires_at     epoch seconds | None — MIRROR of the engine's expires_at,
                   informational only; the ENGINE (not this extension)
                   deletes on TTL, via its own purge job.

Unlike Google Drive Connector, there is no external source to re-fetch from:
once a file's engine doc is gone (TTL purge), it is gone for good — status
flips to `expired`, never re-ingested (there is nothing left to re-ingest).
Raw bytes are NEVER persisted here or anywhere else in this extension: they
exist only in the background ingest job's memory, between receipt and the
single engine call in `ingest_one`.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

from . import extractor
from .helpers import FILES_COLLECTION, STALE_PENDING_S

# ── States ────────────────────────────────────────────────────────────────────
PENDING, INDEXING, READY, FAILED, EXPIRED = "pending", "indexing", "ready", "failed", "expired"


class NotReadyError(RuntimeError):
    """Raised when a read/search hits a file that is still (freshly)
    indexing. Distinct from a STALE pending (see ensure_ready), which is
    reported as a hard failure instead of an infinite 'ask again' loop."""


# ── Policy (config-in-code — one line each to tune) ───────────────────────────
MAX_DOCS = 200
MAX_BYTES = 1024 * 1024 * 1024              # 1 GiB total per user
MAX_PER_UPLOAD = 10                          # files accepted in one receive_files call
MAX_SINGLE_FILE_BYTES = 100 * 1024 * 1024    # 100 MiB — product policy for the dropzone. The
                                              # old 12 MiB was a WORKAROUND for a non-memory-safe
                                              # engine that OOM-killed on 20-30MB; the engine is
                                              # now RAM-zero (streams to disk, extracts page-by-
                                              # page, bounded drain — v1.6.0), so this is a plain
                                              # product cap, not an infra guard. Tune freely; the
                                              # engine imposes no size limit of its own.


def _now() -> float:
    return time.time()


def _to_epoch(dt) -> float | None:
    """DocumentOut.expires_at arrives as an ISO datetime string (or None)
    from the engine — normalize to epoch seconds for local comparisons."""
    if not dt:
        return None
    if isinstance(dt, (int, float)):
        return float(dt)
    try:
        return datetime.fromisoformat(str(dt).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ── Records ───────────────────────────────────────────────────────────────────


async def all_files(ctx) -> list[dict]:
    docs = await ctx.store.query(FILES_COLLECTION)
    out = []
    for d in docs:
        item = dict(d.data)
        item["file_id"] = d.id
        out.append(item)
    return out


async def find(ctx, file_id: str) -> dict:
    files = await all_files(ctx)
    match = next((f for f in files if f["file_id"] == file_id), None)
    if not match:
        raise RuntimeError(f"File {file_id!r} not found.")
    return match


async def set_fields(ctx, rec: dict, **fields) -> dict:
    rec.update(fields)
    await ctx.store.update(FILES_COLLECTION, rec["file_id"],
                           {k: v for k, v in rec.items() if k != "file_id"})
    return rec


# ── Quota ─────────────────────────────────────────────────────────────────────


async def quota_state(ctx) -> tuple[int, int]:
    """(doc_count, total_bytes) currently held — expired records don't count
    against quota (their engine storage is already gone)."""
    files = await all_files(ctx)
    live = [f for f in files if f.get("status") != EXPIRED]
    return len(live), sum(int(f.get("size_bytes") or 0) for f in live)


async def check_quota(ctx, adding: int, adding_bytes: int) -> None:
    """Raise a user-facing error if this upload would exceed the caps.
    Checked BEFORE any engine call — cheap, synchronous, instant feedback."""
    if adding > MAX_PER_UPLOAD:
        raise RuntimeError(f"At most {MAX_PER_UPLOAD} files per upload.")
    count, total = await quota_state(ctx)
    if count + adding > MAX_DOCS:
        raise RuntimeError(f"File limit reached ({MAX_DOCS} files). Remove some files before adding more.")
    if total + adding_bytes > MAX_BYTES:
        gb = MAX_BYTES / (1024 ** 3)
        raise RuntimeError(f"Storage limit reached ({gb:.0f} GB). Remove some files before adding more.")


# ── Creation + ingest (heavy path — background) ───────────────────────────────


async def create_pending(ctx, filename: str, mime_type: str | None, size_bytes: int) -> dict:
    """Create the record BEFORE the engine call — so the file is visible
    (as 'pending') to list_files/skeleton immediately, even before the
    background job has run."""
    created = await ctx.store.create(FILES_COLLECTION, {
        "filename": filename, "mime_type": mime_type, "size_bytes": size_bytes,
        "status": PENDING, "document_id": None, "chunk_count": 0,
        "error": None, "error_code": None,
        "uploaded_at": _now(), "expires_at": None,
    })
    rec = dict(created.data)
    rec["file_id"] = created.id
    return rec


async def ingest_one(ctx, rec: dict, content: bytes) -> dict:
    """Ingest ONE file's bytes into the engine. `content` must never outlive
    this call — the background job holds it in a local variable only, never
    in the store. Idempotent by sha256 on the engine side (a resend of the
    same bytes is a fast `cached` hit)."""
    rec = await set_fields(ctx, rec, status=INDEXING, error=None, error_code=None)
    try:
        doc = await extractor.ingest(
            ctx, filename=rec.get("filename") or "file", content=content,
            mime_type=rec.get("mime_type"),
        )
        # The engine is async: POST returns pending/processing and finishes in
        # its own background. Wait for the real terminal outcome before deciding
        # ready vs failed (POST's transient status is not the result).
        if doc.get("status") in extractor.PENDING_STATES and doc.get("document_id"):
            doc = await extractor.wait_until_done(ctx, doc["document_id"])
    except Exception as e:  # noqa: BLE001 - record the failure, don't raise into the batch
        return await set_fields(ctx, rec, status=FAILED, error=str(e), error_code="internal_error")
    if doc.get("status") not in extractor.READY_STATES:
        return await set_fields(
            ctx, rec, status=FAILED,
            error=doc.get("error") or "could not process this file",
            error_code=doc.get("error_code"),
        )
    return await set_fields(
        ctx, rec, status=READY, document_id=doc.get("document_id"),
        chunk_count=doc.get("chunk_count") or 0, error=doc.get("error"),
        error_code=doc.get("error_code"), expires_at=_to_epoch(doc.get("expires_at")),
    )


async def ingest_many(ctx, items: list[tuple[dict, bytes]], concurrency: int = 4) -> dict:
    """Ingest several (record, content) pairs in PARALLEL, bounded by
    `concurrency`. One bad file never stops the batch. This is the body of
    the background job kicked from receive_files."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(rec, content) -> bool:
        async with sem:
            result = await ingest_one(ctx, rec, content)
            return result.get("status") == READY

    results = await asyncio.gather(*(_one(rec, content) for rec, content in items))
    ok = sum(1 for r in results if r)
    return {"ingested": ok, "failed": len(results) - ok}


# ── Read-path resolution ───────────────────────────────────────────────────────


async def ensure_ready(ctx, rec: dict) -> int:
    """Return a usable engine document_id, or raise a clear, specific error.
    Distinguishes: freshly indexing (NotReadyError — ask again shortly) from
    a STALE pending (background job died mid-flight, e.g. worker restart —
    the raw bytes lived only in that job's memory and are gone for good) from
    a genuinely failed/expired file (permanent, no retry will help)."""
    if rec.get("status") == READY and rec.get("document_id"):
        return rec["document_id"]
    if rec.get("status") == EXPIRED:
        raise RuntimeError(f"'{rec.get('filename')}' was deleted after its retention period — please re-upload it.")
    if rec.get("status") == FAILED:
        raise RuntimeError(rec.get("error") or f"'{rec.get('filename')}' could not be processed.")
    age = _now() - float(rec.get("uploaded_at") or 0)
    if rec.get("status") == PENDING and age > STALE_PENDING_S:
        raise RuntimeError(f"Upload of '{rec.get('filename')}' was interrupted — please re-upload it.")
    raise NotReadyError(f"'{rec.get('filename')}' is still being processed — ask again in a moment.")


async def mark_expired_if_gone(ctx, rec: dict) -> dict:
    """Call after the engine 404s a document lookup — its TTL purge already
    ran. One-way: there is no source to re-ingest from, unlike Google Drive
    Connector's cold-evict-then-self-heal."""
    return await set_fields(ctx, rec, status=EXPIRED, document_id=None)


# ── Forget (single authority — panel + engine together, no drift) ─────────────


async def forget(ctx, file_id: str) -> None:
    rec = await find(ctx, file_id)
    doc_id = rec.get("document_id")
    if doc_id:
        try:
            await extractor.delete(ctx, doc_id)  # engine (PG+NC), best-effort
        except Exception:  # noqa: BLE001
            pass
    await ctx.store.delete(FILES_COLLECTION, rec["file_id"])


async def forget_many(ctx, file_ids: list[str]) -> int:
    """BULK disconnect: remove many files (records + engine docs) in
    parallel. Unknown ids are skipped. Returns the count removed."""
    targets = []
    for fid in file_ids:
        try:
            targets.append(await find(ctx, fid))
        except RuntimeError:
            continue

    async def _one(rec: dict) -> None:
        doc_id = rec.get("document_id")
        if doc_id:
            try:
                await extractor.delete(ctx, doc_id)
            except Exception:  # noqa: BLE001
                pass
        await ctx.store.delete(FILES_COLLECTION, rec["file_id"])

    await asyncio.gather(*(_one(r) for r in targets))
    return len(targets)
