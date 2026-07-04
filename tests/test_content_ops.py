"""providers/content_ops.py — the list-based, parallel CONTENT plane. Pins
the "one bad file never fails the batch" invariant and the two search modes."""
from __future__ import annotations

from providers import content_ops, lifecycle


async def _ready_file(ctx, filename="a.txt", document_id=1, chunk_count=1):
    rec = await lifecycle.create_pending(ctx, filename, "text/plain", 10)
    await lifecycle.set_fields(ctx, rec, status=lifecycle.READY, document_id=document_id,
                               chunk_count=chunk_count)
    return rec


# ── read_files ──────────────────────────────────────────────────────────────


async def test_read_files_single_ok(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "text": "hello world", "offset": 0, "limit": 40000,
        "total_chars": 11, "truncated": False}})])
    rec = await _ready_file(ctx)
    results = await content_ops.read_files(ctx, [rec["file_id"]])
    assert results[0]["status"] == "ok"
    assert results[0]["text"] == "hello world"


async def test_read_files_unknown_id_is_isolated_error(make_ctx):
    ctx = make_ctx([])
    results = await content_ops.read_files(ctx, ["ghost"])
    assert results[0]["status"] == "error"


async def test_read_files_preparing_when_still_indexing(make_ctx):
    ctx = make_ctx()
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 10)
    results = await content_ops.read_files(ctx, [rec["file_id"]])
    assert results[0]["status"] == "preparing"


async def test_read_files_expired_reports_expired_message(make_ctx):
    ctx = make_ctx()
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 10)
    await lifecycle.set_fields(ctx, rec, status=lifecycle.EXPIRED)
    results = await content_ops.read_files(ctx, [rec["file_id"]])
    assert results[0]["status"] == "error"
    assert "retention period" in results[0]["message"]


async def test_read_files_batch_one_bad_file_does_not_fail_the_others(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "text": "ok text", "offset": 0, "limit": 4000,
        "total_chars": 7, "truncated": False}})])
    good = await _ready_file(ctx, filename="good.txt", document_id=1)
    bad = await lifecycle.create_pending(ctx, "bad.txt", "text/plain", 10)
    await lifecycle.set_fields(ctx, bad, status=lifecycle.FAILED, error="extraction_failed")
    results = await content_ops.read_files(ctx, [good["file_id"], bad["file_id"]])
    by_id = {r["file_id"]: r for r in results}
    assert by_id[good["file_id"]]["status"] == "ok"
    assert by_id[bad["file_id"]]["status"] == "error"


async def test_read_files_multi_file_uses_smaller_preview_window(make_ctx, resp):
    ctx = make_ctx([
        resp(200, {"success": True, "data": {"document_id": 1, "text": "a" * 100, "offset": 0,
                                             "limit": content_ops.MULTI_READ_LIMIT, "total_chars": 100,
                                             "truncated": False}}),
        resp(200, {"success": True, "data": {"document_id": 2, "text": "b" * 100, "offset": 0,
                                             "limit": content_ops.MULTI_READ_LIMIT, "total_chars": 100,
                                             "truncated": False}}),
    ])
    r1 = await _ready_file(ctx, filename="a.txt", document_id=1)
    r2 = await _ready_file(ctx, filename="b.txt", document_id=2)
    await content_ops.read_files(ctx, [r1["file_id"], r2["file_id"]])
    for _, _, kwargs in ctx.http.calls:
        assert kwargs["params"]["limit"] == content_ops.MULTI_READ_LIMIT


# ── file_overview ─────────────────────────────────────────────────────────────


async def test_file_overview_ready_file_includes_preview(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "source": "filereader", "imperal_id": "user-123", "sha256": "x",
        "filename": "a.txt", "mime": "text/plain", "size_bytes": 10, "preview": "hello…",
        "status": "processed", "stage": "done", "error": None, "error_code": None,
        "chunk_count": 1, "created_at": None, "expires_at": None}})])
    rec = await _ready_file(ctx)
    results = await content_ops.file_overview(ctx, [rec["file_id"]])
    assert results[0]["preview"] == "hello…"


async def test_file_overview_pending_file_has_no_preview_and_no_engine_call(make_ctx):
    ctx = make_ctx([])  # any HTTP call here would raise AssertionError
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 10)
    results = await content_ops.file_overview(ctx, [rec["file_id"]])
    assert results[0]["preview"] is None
    assert results[0]["status"] == lifecycle.PENDING


# ── search_files ──────────────────────────────────────────────────────────────


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
    rec = await _ready_file(ctx, filename="a.txt", document_id=1)
    result = await content_ops.search_files(ctx, "needle", file_ids=[rec["file_id"]])
    assert result["mode"] == "exact"
    assert len(result["results"]) == 1
    assert "NEEDLE" in result["results"][0]["text"]


async def test_search_files_exact_mode_broken_file_contributes_nothing(make_ctx):
    ctx = make_ctx([])
    result = await content_ops.search_files(ctx, "needle", file_ids=["ghost"])
    assert result["results"] == []
