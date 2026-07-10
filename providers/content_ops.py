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
import logging

from . import extractor, lifecycle
from .text_windows import grep_lines


def _attach_extraction_truth(payload: dict, meta: dict | None) -> dict:
    """Copy backend-reported extraction truth onto a response item.

    This stays fail-closed: we only expose fields the backend actually sent
    (or the extractor helper derives directly from those exact fields), never
    mime-based guesses.
    """
    truth = extractor.classify_extraction(meta)
    for key in ("extraction_method", "image_ai_used", "ocr_used", "is_inferred",
                "is_partial", "text_quality", "noise_score"):
        payload[key] = truth.get(key)
    return payload


def _clean_text(value: str | None) -> str:
    """Normalize extractor text for chat consumption without changing meaning.

    Keeps the payload cheap and readable for Webbee: trims null bytes and outer
    whitespace, normalises newlines, and collapses huge vertical gaps that only
    waste context. Does NOT rewrite words or punctuation.
    """
    if not isinstance(value, str):
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = text.strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def _clean_preview(value: str | None) -> str | None:
    text = _clean_text(value)
    return text or None


def _clean_search_snippet(value: str | None) -> str:
    return _clean_text(value)

# Kernel ceiling (verified 2026-07-06): Webby's agentic tool loop serializes
# a whole tool result to one string and hard-cuts it at ~10_000 chars, mid-JSON,
# with no awareness of fields or list items (orchestration/agentic/loop.py,
# _tool_result_content_str / _TOOL_RESULT_MAX). A response that lands right at
# that edge comes through corrupted, not just short — Webby then can't trust
# ANY of it. We stay comfortably under it ourselves so a big ask degrades to
# smaller-but-honest (has_more=True / [N chars total]) instead of broken.
RESPONSE_BUDGET_CHARS = 8_000
_MIN_PER_FILE = 250             # floor per file when read_files batches many at once
_MIN_PER_HIT = 200              # floor per search hit when there are many

DEFAULT_READ_LIMIT = RESPONSE_BUDGET_CHARS   # chars for a single-file read window
MAX_READ_LIMIT = RESPONSE_BUDGET_CHARS       # hard ceiling per file even on an explicit ask —
                                              # nothing bigger survives the kernel intact anyway
FULLTEXT_LIMIT = 5_000_000     # engine cap for exact in-file grep
DEFAULT_SEARCH_K = 6
MAX_SEARCH_K = 20
_CONCURRENCY = 5               # parallel engine calls per bulk op (self-throttle)

log = logging.getLogger("file_reader")


def _budget_share(n: int, floor: int) -> int:
    """Split RESPONSE_BUDGET_CHARS across n items, never below floor."""
    return max(floor, RESPONSE_BUDGET_CHARS // max(1, n))


def _fit_text_budget(items: list[dict], text_key: str, floor: int) -> tuple[list[dict], int]:
    """Cap item count and per-item text so the total stays within
    RESPONSE_BUDGET_CHARS. Returns (possibly-shortened items, original count)
    so the caller can report has_more honestly instead of silently dropping."""
    total = len(items)
    max_items = max(1, RESPONSE_BUDGET_CHARS // floor)
    kept = items[:max_items]
    per = _budget_share(len(kept), floor)
    for it in kept:
        text = it.get(text_key) or ""
        if len(text) > per:
            it[text_key] = text[:per] + f"… [{len(text)} chars total]"
    return kept, total


async def read_files(ctx, file_ids: list[str], offset: int = 0, limit: int | None = None) -> list[dict]:
    """Read 1..N files in parallel. One id → a budget-sized window; many →
    RESPONSE_BUDGET_CHARS split across them. Each result carries status
    ok|preparing|error — a not-ready, failed, or expired file never fails the
    others. A smaller window just means more of it reports has_more=True —
    never missing content."""
    multi = len(file_ids) > 1
    cap = _budget_share(len(file_ids), _MIN_PER_FILE) if multi else MAX_READ_LIMIT
    per = max(1, min(limit or cap, cap))
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
                text = _clean_text(data.get("text", ""))
                total_chars = data.get("total_chars", 0)
                returned_chars = len(text)
                if not text and total_chars == 0:
                    preview = None
                    meta = None
                    overview_error = None
                    try:
                        meta = await extractor.overview(ctx, document_id)
                        preview = meta.get("preview")
                    except Exception as e:
                        overview_error = str(e)
                        preview = None
                    diag = {
                        "file_id": fid,
                        "filename": rec.get("filename"),
                        "document_id": document_id,
                        "offset": data.get("offset", 0),
                        "limit": per,
                        "read_status": data.get("status"),
                        "read_stage": data.get("stage"),
                        "read_chunk_count": data.get("chunk_count"),
                        "overview_status": meta.get("status") if isinstance(meta, dict) else None,
                        "overview_stage": meta.get("stage") if isinstance(meta, dict) else None,
                        "overview_chunk_count": meta.get("chunk_count") if isinstance(meta, dict) else None,
                        "preview_len": len(preview) if isinstance(preview, str) else 0,
                        "overview_error": overview_error,
                    }
                    log.warning("file_reader.empty_extracted_text %s", diag)
                    if preview:
                        preview = _clean_preview(preview)
                    if preview:
                        return _attach_extraction_truth({
                            "file_id": fid,
                            "filename": rec.get("filename"),
                            "text": preview,
                            "offset": 0,
                            "returned_chars": len(preview),
                            "total_chars": len(preview),
                            "has_more": False,
                            "status": "ok",
                            "warning": "preview_only",
                            "message": "full extracted text was empty at /v1/documents/{id}/text; showing /v1/documents preview instead",
                            "diagnosis": {
                                "kind": "empty_extracted_text",
                                "document_id": document_id,
                                "read_text_empty": True,
                                "overview_preview_used": True,
                                "overview_preview_len": len(preview),
                                "overview_chunk_count": meta.get("chunk_count") if isinstance(meta, dict) else None,
                            },
                        }, meta)
                    return _attach_extraction_truth({
                        "file_id": fid,
                        "filename": rec.get("filename"),
                        "status": "error",
                        "message": "engine returned empty text at /v1/documents/{id}/text and no preview at /v1/documents/{id}",
                        "text": "",
                        "diagnosis": {
                            "kind": "empty_extracted_text",
                            "document_id": document_id,
                            "read_text_empty": True,
                            "overview_preview_used": False,
                            "overview_chunk_count": meta.get("chunk_count") if isinstance(meta, dict) else None,
                        },
                    }, meta)
                return _attach_extraction_truth({"file_id": fid, "filename": rec.get("filename"), "text": text,
                        "offset": data.get("offset", 0), "returned_chars": returned_chars,
                        "total_chars": total_chars,
                        "has_more": bool(data.get("truncated")), "status": "ok"}, data)
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
                    out["preview"] = _clean_preview(meta.get("preview"))
                    _attach_extraction_truth(out, meta)
                except Exception:  # noqa: BLE001 - preview is best-effort
                    pass
            return out

    results = await asyncio.gather(*(_one(f) for f in file_ids))
    trimmed, _ = _fit_text_budget(list(results), "preview", _MIN_PER_HIT)
    return trimmed


PREVIEW_EXCERPT_CHARS = 700   # per excerpt — token-cheap on purpose, this is NOT a full read


async def file_preview(ctx, file_ids: list[str]) -> list[dict]:
    """Token-cheap preview for 1..N files: the opening of the (cleaned) text
    plus, for anything longer than two excerpt-windows, a second real sample
    from further in — two honest data points instead of pretending the first
    N characters ARE the document (the anti-pattern this tool exists to
    avoid; see FILE-READER-API-CONTRACT.md section 20 #1). No summarization,
    no invented section titles — if the engine hasn't told us about
    structure, we don't fabricate any."""
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(fid: str) -> dict:
        async with sem:
            try:
                rec = await lifecycle.find(ctx, fid)
            except Exception as e:  # noqa: BLE001
                return {"file_id": fid, "status": "error", "message": str(e)}
            out = {"file_id": fid, "filename": rec.get("filename"), "mime_type": rec.get("mime_type")}
            try:
                document_id = await lifecycle.ensure_ready(ctx, rec)
            except lifecycle.NotReadyError:
                return {**out, "status": "preparing"}
            except Exception as e:  # noqa: BLE001 - expired / failed / stale-pending
                return {**out, "status": "error", "message": str(e)}
            try:
                opening = await extractor.read_text(ctx, document_id, offset=0, limit=PREVIEW_EXCERPT_CHARS)
            except Exception as e:  # noqa: BLE001
                return {**out, "status": "error", "message": str(e)}
            _attach_extraction_truth(out, opening)
            total = opening.get("total_chars", 0)
            excerpts = []
            opening_text = _clean_text(opening.get("text", ""))
            if opening_text:
                excerpts.append({"label": "opening", "text": opening_text})
            if total > PREVIEW_EXCERPT_CHARS * 2:
                try:
                    mid = await extractor.read_text(ctx, document_id, offset=total // 2, limit=PREVIEW_EXCERPT_CHARS)
                    mid_text = _clean_text(mid.get("text", ""))
                    if mid_text:
                        excerpts.append({"label": "further in the document", "text": mid_text})
                except Exception:  # noqa: BLE001 - the opening excerpt alone is still a valid preview
                    pass
            out["total_chars"] = total
            out["excerpts"] = excerpts
            out["status"] = "ok" if excerpts else "error"
            if not excerpts:
                out["message"] = "file has no extractable text to preview"
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
        trimmed, total = _fit_text_budget(results, "text", _MIN_PER_HIT)
        return {"query": query, "mode": "exact", "results": trimmed, "total_matches": total}

    kk = max(1, min(k or DEFAULT_SEARCH_K, MAX_SEARCH_K))
    hits = await extractor.search(ctx, query, k=kk)
    results = [{"label": f"{h.get('filename') or '?'}#{h.get('seq')}",
                "text": _clean_search_snippet(h.get("text", "")), "score": h.get("score")} for h in hits]
    trimmed, total = _fit_text_budget(results, "text", _MIN_PER_HIT)
    return {"query": query, "mode": "semantic", "results": trimmed, "total_matches": total}
