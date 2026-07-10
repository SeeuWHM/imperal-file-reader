"""providers/content_ops.py — file_overview and search_files, plus the
shared text-cleaning behavior both share with read_files. See
test_content_ops_read.py for read_files and test_content_ops_preview.py for
file_preview (split out 2026-07-10 to keep test files under the 300-line
house limit)."""
from __future__ import annotations

from providers import content_ops, lifecycle

from .conftest import make_ready_file


# ── file_overview ─────────────────────────────────────────────────────────────


async def test_file_overview_ready_file_includes_preview(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "source": "filereader", "imperal_id": "user-123", "sha256": "x",
        "filename": "a.txt", "mime": "text/plain", "size_bytes": 10, "preview": "hello…",
        "status": "processed", "stage": "done", "error": None, "error_code": None,
        "chunk_count": 1, "created_at": None, "expires_at": None,
        "extraction_method": "text",
        "image_ai_used": False,
        "ocr_used": False}})])
    rec = await make_ready_file(ctx)
    results = await content_ops.file_overview(ctx, [rec["file_id"]])
    assert results[0]["preview"] == "hello…"
    assert results[0]["extraction_method"] == "text"
    assert results[0]["image_ai_used"] is False
    assert results[0]["ocr_used"] is False


async def test_file_overview_pending_file_has_no_preview_and_no_engine_call(make_ctx):
    ctx = make_ctx([])  # any HTTP call here would raise AssertionError
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 10)
    results = await content_ops.file_overview(ctx, [rec["file_id"]])
    assert results[0]["preview"] is None
    assert results[0]["status"] == lifecycle.PENDING


async def test_file_overview_cleans_preview_for_chat(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1,
        "source": "filereader",
        "imperal_id": "user-123",
        "sha256": "x",
        "filename": "a.txt",
        "mime": "text/plain",
        "size_bytes": 10,
        "preview": "\x00  Preview\r\n\r\n\r\nText  \x00",
        "status": "processed",
        "stage": "done",
        "error": None,
        "error_code": None,
        "chunk_count": 1,
        "created_at": None,
        "expires_at": None,
    }})])
    rec = await make_ready_file(ctx)
    results = await content_ops.file_overview(ctx, [rec["file_id"]])
    assert results[0]["preview"] == "Preview\n\nText"


# ── search_files ──────────────────────────────────────────────────────────────


async def test_search_files_semantic_mode_cleans_snippets(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "query": "invoice", "count": 1,
        "hits": [{"document_id": 1, "filename": "a.pdf", "seq": 0, "text": "\x00Invoice\r\n\r\n\r\n#42\x00", "score": 0.9}],
    }})])
    result = await content_ops.search_files(ctx, "invoice")
    assert result["mode"] == "semantic"
    assert result["results"][0]["text"] == "Invoice\n\n#42"


async def test_search_files_semantic_mode_without_ids(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "query": "invoice", "count": 1,
        "hits": [{"document_id": 1, "filename": "a.pdf", "seq": 0, "text": "Invoice #42", "score": 0.9}],
    }})])
    result = await content_ops.search_files(ctx, "invoice")
    assert result["mode"] == "semantic"
    assert result["results"][0]["text"] == "Invoice #42"
    method, url, kwargs = ctx.http.calls[0]
    assert url.endswith("/v1/search")


async def test_search_files_exact_mode_with_ids_greps_locally(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "text": "line one\nline with NEEDLE here\nline three",
        "offset": 0, "limit": content_ops.FULLTEXT_LIMIT, "total_chars": 40, "truncated": False}})])
    rec = await make_ready_file(ctx, filename="a.txt", document_id=1)
    result = await content_ops.search_files(ctx, "needle", file_ids=[rec["file_id"]])
    assert result["mode"] == "exact"
    assert len(result["results"]) == 1
    assert "NEEDLE" in result["results"][0]["text"]


async def test_search_files_exact_mode_broken_file_contributes_nothing(make_ctx):
    ctx = make_ctx([])
    result = await content_ops.search_files(ctx, "needle", file_ids=["ghost"])
    assert result["results"] == []
