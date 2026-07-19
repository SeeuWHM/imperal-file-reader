"""providers/content_ops.py — file_preview: the token-cheap preview tool
(opening excerpt + a second sample from further in for longer files)."""
from __future__ import annotations

from providers import content_ops, lifecycle

from .conftest import make_ready_file


async def test_file_preview_short_file_returns_only_opening(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "text": "Hello world.", "offset": 0,
        "limit": content_ops.PREVIEW_EXCERPT_CHARS, "total_chars": 12, "truncated": False,
        "extraction_method": "text"}})])
    rec = await make_ready_file(ctx)
    results = await content_ops.file_preview(ctx, [rec["file_id"]])
    assert results[0]["status"] == "ok"
    assert results[0]["excerpts"] == [{"label": "opening", "text": "Hello world."}]
    assert results[0]["total_chars"] == 12
    assert results[0]["extraction_method"] == "text"
    assert len(ctx.http.calls) == 1  # short file — no second (middle) probe needed


async def test_file_preview_uses_lower_excerpt_budget():
    assert content_ops.PREVIEW_EXCERPT_CHARS == 500


async def test_file_preview_long_file_includes_middle_excerpt(make_ctx, resp):
    total = content_ops.PREVIEW_EXCERPT_CHARS * 5
    ctx = make_ctx([
        resp(200, {"success": True, "data": {
            "document_id": 1, "text": "opening text", "offset": 0,
            "limit": content_ops.PREVIEW_EXCERPT_CHARS, "total_chars": total, "truncated": True}}),
        resp(200, {"success": True, "data": {
            "document_id": 1,
            "text": "xxxxxxmiddle text\nsecond line",
            "offset": (total // 2) - 120,
            "limit": content_ops.PREVIEW_EXCERPT_CHARS + 120,
            "total_chars": total,
            "truncated": True}}),
    ])
    rec = await make_ready_file(ctx)
    results = await content_ops.file_preview(ctx, [rec["file_id"]])
    assert results[0]["excerpts"] == [
        {"label": "opening", "text": "opening text"},
        {"label": "further in the document", "text": "second line"},
    ]
    _, _, kwargs = ctx.http.calls[1]
    assert kwargs["params"]["offset"] == (total // 2) - 120


async def test_file_preview_middle_excerpt_prefers_next_line_boundary(make_ctx, resp):
    total = content_ops.PREVIEW_EXCERPT_CHARS * 5
    ctx = make_ctx([
        resp(200, {"success": True, "data": {
            "document_id": 1, "text": "opening text", "offset": 0,
            "limit": content_ops.PREVIEW_EXCERPT_CHARS, "total_chars": total, "truncated": True}}),
        resp(200, {"success": True, "data": {
            "document_id": 1,
            "text": "prefix tail\n\nSection B\nuseful content",
            "offset": (total // 2) - 120,
            "limit": content_ops.PREVIEW_EXCERPT_CHARS + 120,
            "total_chars": total,
            "truncated": True}}),
    ])
    rec = await make_ready_file(ctx)
    results = await content_ops.file_preview(ctx, [rec["file_id"]])
    assert results[0]["excerpts"][1] == {
        "label": "further in the document",
        "text": "Section B\nuseful content",
    }


async def test_file_preview_preparing_when_still_indexing(make_ctx):
    ctx = make_ctx()
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 10)
    results = await content_ops.file_preview(ctx, [rec["file_id"]])
    assert results[0]["status"] == "preparing"


async def test_file_preview_no_text_is_error(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "text": "", "offset": 0,
        "limit": content_ops.PREVIEW_EXCERPT_CHARS, "total_chars": 0, "truncated": False}})])
    rec = await make_ready_file(ctx)
    results = await content_ops.file_preview(ctx, [rec["file_id"]])
    assert results[0]["status"] == "error"
    assert results[0]["excerpts"] == []
