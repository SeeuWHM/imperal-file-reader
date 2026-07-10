"""File Reader — SDL entity classes + builders (imperal-sdk 5.9.x).

Every @chat.function's data_model= lives here, with the builders that turn the
providers' plain dicts into SDL entities. Mirrors the Google Drive Connector's
schemas_sdl.py, minus Drive/OAuth/edit specifics.
"""
from __future__ import annotations

import time

from pydantic import Field

from imperal_sdk import sdl


def _expires_in_days(expires_at) -> int | None:
    if not expires_at:
        return None
    try:
        return max(0, int((float(expires_at) - time.time()) / 86400.0))
    except (TypeError, ValueError):
        return None


# ── FILES plane ────────────────────────────────────────────────────────────────


class FileItem(sdl.Entity):
    kind: str = "file"
    file_id: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    status: str | None = None          # pending | indexing | ready | failed | expired
    chunk_count: int = 0
    searchable: bool = False           # chunk_count > 0
    expires_in_days: int | None = None
    error: str | None = None


class FileList(sdl.EntityList[FileItem]):
    pass


class ReceivedFile(sdl.Entity):
    kind: str = "received_file"
    file_id: str | None = None
    filename: str | None = None
    size_bytes: int | None = None
    status: str | None = None


class ReceiveResult(sdl.EntityList[ReceivedFile]):
    rejected: list[dict] = Field(default_factory=list)


class ForgetResult(sdl.Entity):
    kind: str = "forget_result"
    removed: int = 0


# ── CONTENT plane ──────────────────────────────────────────────────────────────


class FileText(sdl.Entity, sdl.Bodied):
    """A windowed, character-addressed slice of a file's extracted text."""
    kind: str = "file_text"
    file_id: str | None = None
    offset: int = 0
    returned_chars: int = 0
    total_chars: int = 0
    has_more: bool = False
    extraction_method: str | None = None
    image_ai_used: bool = False
    ocr_used: bool = False
    is_inferred: bool = False
    is_partial: bool = False
    text_quality: float | None = None
    noise_score: float | None = None
    warning: str | None = None
    message: str | None = None
    diagnosis_json: str | None = None


class SearchHit(sdl.Entity):
    kind: str = "search_hit"
    label: str = ""
    snippet: str = ""
    score: float | None = None


class SearchResults(sdl.EntityList[SearchHit]):
    query: str | None = None
    mode: str | None = None            # semantic | exact


class FileOverview(sdl.Entity, sdl.Excerptable):
    kind: str = "file_overview"
    file_id: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    status: str | None = None
    extraction_method: str | None = None
    image_ai_used: bool = False
    ocr_used: bool = False
    is_inferred: bool = False
    is_partial: bool = False
    text_quality: float | None = None
    noise_score: float | None = None


class FilePreview(sdl.Entity, sdl.Excerptable):
    """Token-cheap preview: real excerpts (opening +, for longer files, a
    sample from further in) — never a fabricated summary or invented section
    titles. `excerpt` (from Excerptable) mirrors the opening excerpt for
    surfaces that only render a single short field."""
    kind: str = "file_preview"
    file_id: str | None = None
    mime_type: str | None = None
    status: str | None = None
    total_chars: int | None = None
    excerpts: list[dict] = Field(default_factory=list)
    extraction_method: str | None = None
    image_ai_used: bool = False
    ocr_used: bool = False
    is_partial: bool = False
    text_quality: float | None = None
    noise_score: float | None = None
    message: str | None = None


class FileTextList(sdl.EntityList[FileText]):
    pass


class FileOverviewList(sdl.EntityList[FileOverview]):
    pass


class FilePreviewList(sdl.EntityList[FilePreview]):
    pass


# ── builders ────────────────────────────────────────────────────────────────────


def build_file_item(rec: dict) -> FileItem:
    cc = rec.get("chunk_count") or 0
    return FileItem(
        id=str(rec.get("file_id")), title=rec.get("filename") or str(rec.get("file_id")),
        file_id=rec.get("file_id"), filename=rec.get("filename"),
        mime_type=rec.get("mime_type"), size_bytes=rec.get("size_bytes"),
        status=rec.get("status"), chunk_count=cc, searchable=cc > 0,
        expires_in_days=_expires_in_days(rec.get("expires_at")), error=rec.get("error"),
    )


def build_file_list(recs: list[dict]) -> FileList:
    return FileList(items=[build_file_item(r) for r in recs], total=len(recs))


def build_receive_result(received: list[dict], rejected: list[dict]) -> ReceiveResult:
    items = [
        ReceivedFile(id=str(r.get("file_id")), title=r.get("filename") or "",
                     file_id=r.get("file_id"), filename=r.get("filename"),
                     size_bytes=r.get("size_bytes"), status=r.get("status"))
        for r in received
    ]
    return ReceiveResult(items=items, total=len(items), rejected=rejected)


def build_forget_result(removed: int) -> ForgetResult:
    return ForgetResult(id="forget", title=f"Removed {removed} file(s)", removed=removed)


def build_file_text(data: dict) -> FileText:
    fid = data.get("file_id")
    off = data.get("offset", 0)
    status = data.get("status") or "ok"
    if status == "preparing":
        body = "(preparing — indexing in progress, ask again in a moment)"
    elif status in ("error", "expired"):
        body = f"({status}: {data.get('message', '')})"
    else:
        body = data.get("text", "")
    title = (data.get("filename") or str(fid)) + (f" (from char {off})" if status == "ok" else f" [{status}]")
    diagnosis = data.get("diagnosis")
    return FileText(
        id=str(fid), title=title, body=body, body_format="plain",
        file_id=fid, offset=off, returned_chars=data.get("returned_chars", 0),
        total_chars=data.get("total_chars", 0), has_more=bool(data.get("has_more")),
        extraction_method=data.get("extraction_method"),
        image_ai_used=bool(data.get("image_ai_used")),
        ocr_used=bool(data.get("ocr_used")),
        is_inferred=bool(data.get("is_inferred")),
        is_partial=bool(data.get("is_partial")),
        text_quality=data.get("text_quality"),
        noise_score=data.get("noise_score"),
        warning=data.get("warning"),
        message=data.get("message"),
        diagnosis_json=(__import__("json").dumps(diagnosis, ensure_ascii=False, sort_keys=True)
                        if isinstance(diagnosis, dict) else None),
    )


def build_file_text_list(results: list[dict]) -> FileTextList:
    return FileTextList(items=[build_file_text(r) for r in results], total=len(results))


def build_file_overview(data: dict) -> FileOverview:
    return FileOverview(
        id=str(data.get("file_id")), title=data.get("filename") or str(data.get("file_id")),
        excerpt=data.get("preview"), file_id=data.get("file_id"),
        mime_type=data.get("mime_type"), size_bytes=data.get("size_bytes"), status=data.get("status"),
        extraction_method=data.get("extraction_method"),
        image_ai_used=bool(data.get("image_ai_used")),
        ocr_used=bool(data.get("ocr_used")),
        is_inferred=bool(data.get("is_inferred")),
        is_partial=bool(data.get("is_partial")),
        text_quality=data.get("text_quality"),
        noise_score=data.get("noise_score"),
    )


def build_file_overview_list(results: list[dict]) -> FileOverviewList:
    return FileOverviewList(items=[build_file_overview(r) for r in results], total=len(results))


def build_file_preview(data: dict) -> FilePreview:
    fid = data.get("file_id")
    excerpts = data.get("excerpts") or []
    opening = next((e["text"] for e in excerpts if e.get("label") == "opening"), None)
    return FilePreview(
        id=str(fid), title=data.get("filename") or str(fid), excerpt=opening,
        file_id=fid, mime_type=data.get("mime_type"), status=data.get("status"),
        total_chars=data.get("total_chars"), excerpts=excerpts,
        extraction_method=data.get("extraction_method"),
        image_ai_used=bool(data.get("image_ai_used")), ocr_used=bool(data.get("ocr_used")),
        is_partial=bool(data.get("is_partial")),
        text_quality=data.get("text_quality"), noise_score=data.get("noise_score"),
        message=data.get("message"),
    )


def build_file_preview_list(results: list[dict]) -> FilePreviewList:
    return FilePreviewList(items=[build_file_preview(r) for r in results], total=len(results))


def build_search_results(data: dict) -> SearchResults:
    items = [
        SearchHit(id=str(i), title=(r.get("label") or r.get("filename") or ""),
                  label=(r.get("label") or r.get("filename") or ""),
                  snippet=r.get("text", ""), score=r.get("score"))
        for i, r in enumerate(data.get("results", []))
    ]
    total = data.get("total_matches", len(items))
    return SearchResults(items=items, total=total, has_more=len(items) < total,
                         query=data.get("query"), mode=data.get("mode"))
