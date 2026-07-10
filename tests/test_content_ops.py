"""providers/content_ops.py — the list-based, parallel CONTENT plane. Pins
the "one bad file never fails the batch" invariant and the two search modes."""
import json
import logging
from unittest.mock import patch

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
        "total_chars": 11, "truncated": False,
        "extraction_method": "text", "image_ai_used": False, "ocr_used": False}})])
    rec = await _ready_file(ctx)
    results = await content_ops.read_files(ctx, [rec["file_id"]])
    assert results[0]["status"] == "ok"
    assert results[0]["text"] == "hello world"
    assert results[0]["extraction_method"] == "text"
    assert results[0]["image_ai_used"] is False
    assert results[0]["ocr_used"] is False
    assert results[0]["is_inferred"] is False


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


async def test_read_files_empty_engine_text_falls_back_to_overview_preview(make_ctx, resp):
    ctx = make_ctx([
        resp(200, {"success": True, "data": {
            "document_id": 1, "text": "", "offset": 0, "limit": 40000,
            "total_chars": 0, "truncated": False}}),
        resp(200, {"success": True, "data": {
            "document_id": 1, "source": "filereader", "imperal_id": "user-123", "sha256": "x",
            "filename": "a.pdf", "mime": "application/pdf", "size_bytes": 10, "preview": "PDF preview text",
            "status": "processed", "stage": "done", "error": None, "error_code": None,
            "chunk_count": 17, "created_at": None, "expires_at": None}}),
    ])
    rec = await _ready_file(ctx, filename="a.pdf", document_id=1, chunk_count=17)
    results = await content_ops.read_files(ctx, [rec["file_id"]])
    assert results[0]["status"] == "ok"
    assert results[0]["warning"] == "preview_only"
    assert results[0]["text"] == "PDF preview text"
    assert results[0]["returned_chars"] == len("PDF preview text")
    assert results[0]["diagnosis"] == {
        "kind": "empty_extracted_text",
        "document_id": 1,
        "read_text_empty": True,
        "overview_preview_used": True,
        "overview_preview_len": len("PDF preview text"),
        "overview_chunk_count": 17,
    }


async def test_read_files_empty_engine_text_without_preview_is_error(make_ctx, resp):
    ctx = make_ctx([
        resp(200, {"success": True, "data": {
            "document_id": 1, "text": "", "offset": 0, "limit": 40000,
            "total_chars": 0, "truncated": False}}),
        resp(200, {"success": True, "data": {
            "document_id": 1, "source": "filereader", "imperal_id": "user-123", "sha256": "x",
            "filename": "a.pdf", "mime": "application/pdf", "size_bytes": 10, "preview": None,
            "status": "processed", "stage": "done", "error": None, "error_code": None,
            "chunk_count": 17, "created_at": None, "expires_at": None}}),
    ])
    rec = await _ready_file(ctx, filename="a.pdf", document_id=1, chunk_count=17)
    results = await content_ops.read_files(ctx, [rec["file_id"]])
    assert results[0]["status"] == "error"
    assert "empty text" in results[0]["message"]
    assert results[0]["diagnosis"] == {
        "kind": "empty_extracted_text",
        "document_id": 1,
        "read_text_empty": True,
        "overview_preview_used": False,
        "overview_chunk_count": 17,
    }


async def test_read_files_empty_engine_text_logs_diagnostic_context(make_ctx, resp):
    ctx = make_ctx([
        resp(200, {"success": True, "data": {
            "document_id": 1, "text": "", "offset": 3, "limit": 123,
            "total_chars": 0, "truncated": False, "status": "processed", "stage": "done", "chunk_count": 0}}),
        resp(200, {"success": True, "data": {
            "document_id": 1, "source": "filereader", "imperal_id": "user-123", "sha256": "x",
            "filename": "a.pdf", "mime": "application/pdf", "size_bytes": 10, "preview": "diag preview",
            "status": "processed", "stage": "done", "error": None, "error_code": None,
            "chunk_count": 17, "created_at": None, "expires_at": None}}),
    ])
    rec = await _ready_file(ctx, filename="a.pdf", document_id=1, chunk_count=17)
    logger = logging.getLogger("file_reader")
    with patch.object(logger, "warning") as warning:
        await content_ops.read_files(ctx, [rec["file_id"]], offset=3, limit=123)
    warning.assert_called_once()
    assert warning.call_args.args[0] == "file_reader.empty_extracted_text %s"
    diag = warning.call_args.args[1]
    assert diag == {
        "file_id": rec["file_id"],
        "filename": "a.pdf",
        "document_id": 1,
        "offset": 3,
        "limit": 123,
        "read_status": "processed",
        "read_stage": "done",
        "read_chunk_count": 0,
        "overview_status": "processed",
        "overview_stage": "done",
        "overview_chunk_count": 17,
        "preview_len": len("diag preview"),
        "overview_error": None,
    }


async def test_read_files_empty_engine_text_logs_overview_failure_context(make_ctx, resp):
    ctx = make_ctx([
        resp(200, {"success": True, "data": {
            "document_id": 1, "text": "", "offset": 0, "limit": 40000,
            "total_chars": 0, "truncated": False, "status": "processed", "stage": "done", "chunk_count": 9}}),
        resp(404, {"success": False, "error": {"code": "NOT_FOUND", "message": "gone"}}),
    ])
    rec = await _ready_file(ctx, filename="a.pdf", document_id=1, chunk_count=17)
    logger = logging.getLogger("file_reader")
    with patch.object(logger, "warning") as warning:
        results = await content_ops.read_files(ctx, [rec["file_id"]])
    assert results[0]["status"] == "error"
    assert warning.call_args.args[0] == "file_reader.empty_extracted_text %s"
    diag = warning.call_args.args[1]
    assert diag == {
        "file_id": rec["file_id"],
        "filename": "a.pdf",
        "document_id": 1,
        "offset": 0,
        "limit": content_ops.MAX_READ_LIMIT,
        "read_status": "processed",
        "read_stage": "done",
        "read_chunk_count": 9,
        "overview_status": None,
        "overview_stage": None,
        "overview_chunk_count": None,
        "preview_len": 0,
        "overview_error": "HTTP 404: gone",
    }


async def test_read_files_batched_reads_split_budget(make_ctx, resp):
    expected = content_ops._budget_share(2, content_ops._MIN_PER_FILE)
    ctx = make_ctx([
        resp(200, {"success": True, "data": {"document_id": 1, "text": "a" * 100, "offset": 0,
                                             "limit": expected, "total_chars": 100,
                                             "truncated": False}}),
        resp(200, {"success": True, "data": {"document_id": 2, "text": "b" * 100, "offset": 0,
                                             "limit": expected, "total_chars": 100,
                                             "truncated": False}}),
    ])
    r1 = await _ready_file(ctx, filename="a.txt", document_id=1)
    r2 = await _ready_file(ctx, filename="b.txt", document_id=2)
    await content_ops.read_files(ctx, [r1["file_id"], r2["file_id"]])
    for _, _, kwargs in ctx.http.calls:
        assert kwargs["params"]["limit"] == expected


async def test_read_files_large_batch_never_exceeds_response_budget(make_ctx, resp):
    n = 20
    ctx = make_ctx([
        resp(200, {"success": True, "data": {"document_id": i, "text": "x" * 500, "offset": 0,
                                             "limit": 500, "total_chars": 500, "truncated": True}})
        for i in range(n)
    ])
    recs = [await _ready_file(ctx, filename=f"f{i}.txt", document_id=i) for i in range(n)]
    await content_ops.read_files(ctx, [r["file_id"] for r in recs])
    for _, _, kwargs in ctx.http.calls:
        assert kwargs["params"]["limit"] <= content_ops._budget_share(n, content_ops._MIN_PER_FILE)
    assert sum(kwargs["params"]["limit"] for _, _, kwargs in ctx.http.calls) <= content_ops.RESPONSE_BUDGET_CHARS


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
    rec = await _ready_file(ctx)
    results = await content_ops.file_overview(ctx, [rec["file_id"]])
    assert results[0]["preview"] == "hello…"
    assert results[0]["extraction_method"] == "text"
    assert results[0]["image_ai_used"] is False
    assert results[0]["ocr_used"] is False
    assert results[0]["is_inferred"] is False


async def test_file_overview_pending_file_has_no_preview_and_no_engine_call(make_ctx):
    ctx = make_ctx([])  # any HTTP call here would raise AssertionError
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 10)
    results = await content_ops.file_overview(ctx, [rec["file_id"]])
    assert results[0]["preview"] is None
    assert results[0]["status"] == lifecycle.PENDING


async def test_read_files_cleans_backend_text_for_chat(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1,
        "text": "\r\n\x00Hello\r\n\r\n\r\nWorld\x00\n\n",
        "offset": 0,
        "limit": 40000,
        "total_chars": 20,
        "truncated": False,
        "extraction_method": "text",
    }})])
    rec = await _ready_file(ctx)
    results = await content_ops.read_files(ctx, [rec["file_id"]])
    assert results[0]["status"] == "ok"
    assert results[0]["text"] == "Hello\n\nWorld"
    assert results[0]["returned_chars"] == len("Hello\n\nWorld")


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
    rec = await _ready_file(ctx)
    results = await content_ops.file_overview(ctx, [rec["file_id"]])
    assert results[0]["preview"] == "Preview\n\nText"


async def test_search_files_semantic_mode_cleans_snippets(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "query": "invoice", "count": 1,
        "hits": [{"document_id": 1, "filename": "a.pdf", "seq": 0, "text": "\x00Invoice\r\n\r\n\r\n#42\x00", "score": 0.9}],
    }})])
    result = await content_ops.search_files(ctx, "invoice")
    assert result["mode"] == "semantic"
    assert result["results"][0]["text"] == "Invoice\n\n#42"


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


# ── file_preview ────────────────────────────────────────────────────────────────


async def test_file_preview_short_file_returns_only_opening(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "text": "Hello world.", "offset": 0,
        "limit": content_ops.PREVIEW_EXCERPT_CHARS, "total_chars": 12, "truncated": False,
        "extraction_method": "text"}})])
    rec = await _ready_file(ctx)
    results = await content_ops.file_preview(ctx, [rec["file_id"]])
    assert results[0]["status"] == "ok"
    assert results[0]["excerpts"] == [{"label": "opening", "text": "Hello world."}]
    assert results[0]["total_chars"] == 12
    assert results[0]["extraction_method"] == "text"
    assert len(ctx.http.calls) == 1  # short file — no second (middle) probe needed


async def test_file_preview_long_file_includes_middle_excerpt(make_ctx, resp):
    total = content_ops.PREVIEW_EXCERPT_CHARS * 5
    ctx = make_ctx([
        resp(200, {"success": True, "data": {
            "document_id": 1, "text": "opening text", "offset": 0,
            "limit": content_ops.PREVIEW_EXCERPT_CHARS, "total_chars": total, "truncated": True}}),
        resp(200, {"success": True, "data": {
            "document_id": 1, "text": "middle text", "offset": total // 2,
            "limit": content_ops.PREVIEW_EXCERPT_CHARS, "total_chars": total, "truncated": True}}),
    ])
    rec = await _ready_file(ctx)
    results = await content_ops.file_preview(ctx, [rec["file_id"]])
    assert results[0]["excerpts"] == [
        {"label": "opening", "text": "opening text"},
        {"label": "further in the document", "text": "middle text"},
    ]
    _, _, kwargs = ctx.http.calls[1]
    assert kwargs["params"]["offset"] == total // 2


async def test_file_preview_preparing_when_still_indexing(make_ctx):
    ctx = make_ctx()
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 10)
    results = await content_ops.file_preview(ctx, [rec["file_id"]])
    assert results[0]["status"] == "preparing"


async def test_file_preview_no_text_is_error(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "text": "", "offset": 0,
        "limit": content_ops.PREVIEW_EXCERPT_CHARS, "total_chars": 0, "truncated": False}})])
    rec = await _ready_file(ctx)
    results = await content_ops.file_preview(ctx, [rec["file_id"]])
    assert results[0]["status"] == "error"
    assert results[0]["excerpts"] == []
