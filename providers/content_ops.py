"""Content operations — read_files / file_overview / search_files.

Universal & list-based: each operation takes file_ids and handles 1..N the
same way, fanning out in PARALLEL inside (the kernel runs multi-tool calls
sequentially, so bulk parallelism must happen inside a single tool call —
same proven pattern as Google Drive Connector and the mail extension). All
content comes from the engine's stored text — there is never a second look
at the original bytes, because none are ever kept after ingest.

Per-file errors never fail the batch: a status field on every item
(ok | preparing | error) lets one broken/expired/still-indexing file coexist
with N healthy ones in the same response.

SDK-free → fully unit-testable with a fake ctx.
"""
from __future__ import annotations

import asyncio

from . import extractor, lifecycle
from .text_windows import grep_lines

DEFAULT_READ_LIMIT = 40_000    # chars for a single-file read window
MULTI_READ_LIMIT = 4_000       # per-file window when reading several at once (gist, token-safe)
MAX_READ_LIMIT = 200_000       # hard ceiling per file
FULLTEXT_LIMIT = 5_000_000     # engine cap for exact in-file grep
DEFAULT_SEARCH_K = 6
MAX_SEARCH_K = 20
_CONCURRENCY = 5               # parallel engine calls per bulk op (self-throttle)


async def read_files(ctx, file_ids: list[str], offset: int = 0, limit: int | None = None) -> list[dict]:
    """Read 1..N files in parallel. One id → full window; many → a bounded
    preview each (token-safe). Each result carries status ok|preparing|error
    — a not-ready, failed, or expired file never fails the others."""
    multi = len(file_ids) > 1
    per = max(1, min(limit or (MULTI_READ_LIMIT if multi else DEFAULT_READ_LIMIT), MAX_READ_LIMIT))
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(fid: str) -> dict:
        async with sem:
            try:
                rec = await lifecycle.find(ctx, fid)
            except Exception as e:  # noqa: BLE001 - unknown file_id
                return {"file_id": fid, "status": "error", "message": str(e), "text": ""}
            try:
                document_id = await lifecycle.ensure_ready(ctx, rec)
            except lifecycle.NotReadyError:
                return {"file_id": fid, "filename": rec.get("filename"), "status": "preparing", "text": ""}
            except Exception as e:  # noqa: BLE001 - expired / failed / stale-pending
                return {"file_id": fid, "filename": rec.get("filename"), "status": "error",
                        "message": str(e), "text": ""}
            try:
                data = await extractor.read_text(ctx, document_id, offset=max(0, offset), limit=per)
                text = data.get("text", "")
                return {"file_id": fid, "filename": rec.get("filename"), "text": text,
                        "offset": data.get("offset", 0), "returned_chars": len(text),
                        "total_chars": data.get("total_chars", 0),
                        "has_more": bool(data.get("truncated")), "status": "ok"}
            except Exception as e:  # noqa: BLE001
                if "404" in str(e):
                    await lifecycle.mark_expired_if_gone(ctx, rec)
                    return {"file_id": fid, "filename": rec.get("filename"), "status": "error",
                            "message": "file was deleted after its retention period — please re-upload it",
                            "text": ""}
                return {"file_id": fid, "filename": rec.get("filename"), "status": "error",
                        "message": str(e), "text": ""}

    return await asyncio.gather(*(_one(f) for f in file_ids))


async def file_overview(ctx, file_ids: list[str]) -> list[dict]:
    """Cheap 'what are these files' for 1..N in parallel — metadata + status,
    plus the engine preview if already indexed. Never forces indexing."""
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(fid: str) -> dict:
        async with sem:
            try:
                rec = await lifecycle.find(ctx, fid)
            except Exception as e:  # noqa: BLE001
                return {"file_id": fid, "status": "error", "message": str(e)}
            out = {"file_id": fid, "filename": rec.get("filename"), "mime_type": rec.get("mime_type"),
                   "size_bytes": rec.get("size_bytes"), "status": rec.get("status"), "preview": None}
            if rec.get("status") == lifecycle.READY and rec.get("document_id"):
                try:
                    meta = await extractor.overview(ctx, rec["document_id"])
                    out["preview"] = meta.get("preview")
                except Exception:  # noqa: BLE001 - preview is best-effort
                    pass
            return out

    return await asyncio.gather(*(_one(f) for f in file_ids))


async def search_files(ctx, query: str, file_ids: list[str] | None = None, k: int | None = None) -> dict:
    """Two correct modes under one universal operation:
      - no file_ids → SEMANTIC search across ALL of this user's indexed
        files (one engine call already studies everything — top-K chunks,
        the big token saver);
      - file_ids → EXACT substring grep across THOSE files, in PARALLEL."""
    if file_ids:
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _one(fid: str) -> list[dict]:
            async with sem:
                try:
                    rec = await lifecycle.find(ctx, fid)
                    document_id = await lifecycle.ensure_ready(ctx, rec)
                    data = await extractor.read_text(ctx, document_id, offset=0, limit=FULLTEXT_LIMIT)
                    name = rec.get("filename") or fid
                    return [{"label": f"{name} · line {ln}", "text": line}
                            for ln, line in grep_lines(data.get("text", ""), query)]
                except Exception:  # noqa: BLE001 - a not-ready/broken/expired file contributes nothing
                    return []

        groups = await asyncio.gather(*(_one(f) for f in file_ids))
        results = [hit for g in groups for hit in g]
        return {"query": query, "mode": "exact", "results": results}

    kk = max(1, min(k or DEFAULT_SEARCH_K, MAX_SEARCH_K))
    hits = await extractor.search(ctx, query, k=kk)
    results = [{"label": f"{h.get('filename') or '?'}#{h.get('seq')}",
                "text": h.get("text", ""), "score": h.get("score")} for h in hits]
    return {"query": query, "mode": "semantic", "results": results}
