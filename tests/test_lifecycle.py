"""providers/lifecycle.py — the file state machine, quota, and forget path.
Federal-grade target: every transition and every quota boundary is pinned by
a test, not by re-reading the code later."""
from __future__ import annotations

import time

import pytest

from providers import extractor, lifecycle


async def test_create_pending_is_visible_immediately(make_ctx):
    ctx = make_ctx()
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 100)
    assert rec["status"] == lifecycle.PENDING
    assert rec["document_id"] is None
    files = await lifecycle.all_files(ctx)
    assert len(files) == 1
    assert files[0]["file_id"] == rec["file_id"]


async def test_quota_blocks_too_many_files_per_upload(make_ctx):
    ctx = make_ctx()
    with pytest.raises(RuntimeError, match="At most"):
        await lifecycle.check_quota(ctx, adding=lifecycle.MAX_PER_UPLOAD + 1, adding_bytes=0)


async def test_quota_blocks_over_max_docs(make_ctx):
    ctx = make_ctx()
    ctx.store.seed("filereader_files", [
        {"status": "ready", "size_bytes": 1} for _ in range(lifecycle.MAX_DOCS)
    ])
    with pytest.raises(RuntimeError, match="File limit reached"):
        await lifecycle.check_quota(ctx, adding=1, adding_bytes=1)


async def test_quota_blocks_over_max_bytes(make_ctx):
    ctx = make_ctx()
    ctx.store.seed("filereader_files", [{"status": "ready", "size_bytes": lifecycle.MAX_BYTES}])
    with pytest.raises(RuntimeError, match="Storage limit reached"):
        await lifecycle.check_quota(ctx, adding=1, adding_bytes=1)


async def test_quota_ignores_expired_records(make_ctx):
    ctx = make_ctx()
    ctx.store.seed("filereader_files", [
        {"status": "expired", "size_bytes": lifecycle.MAX_BYTES} for _ in range(5)
    ])
    # expired records don't count against quota — their engine storage is gone
    await lifecycle.check_quota(ctx, adding=1, adding_bytes=100)


async def test_ingest_one_success_marks_ready_with_document_id(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {"documents": [
        {"document_id": 7, "status": "processed", "chunk_count": 3, "error": None,
         "error_code": None, "expires_at": "2026-07-19T00:00:00+00:00"}
    ]}})])
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 5)
    result = await lifecycle.ingest_one(ctx, rec, b"hello")
    assert result["status"] == lifecycle.READY
    assert result["document_id"] == 7
    assert result["chunk_count"] == 3
    assert result["expires_at"] is not None


async def test_ingest_one_engine_unsupported_marks_failed(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {"documents": [
        {"document_id": None, "status": "unsupported", "error": "video is not readable",
         "error_code": "unsupported_format", "chunk_count": 0, "expires_at": None}
    ]}})])
    rec = await lifecycle.create_pending(ctx, "a.mp4", "video/mp4", 5)
    result = await lifecycle.ingest_one(ctx, rec, b"\x00\x01")
    assert result["status"] == lifecycle.FAILED
    assert result["error_code"] == "unsupported_format"


async def test_ingest_one_network_failure_marks_failed_not_raises(make_ctx):
    ctx = make_ctx([ConnectionError("boom"), ConnectionError("boom")])
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 5)
    result = await lifecycle.ingest_one(ctx, rec, b"x")
    assert result["status"] == lifecycle.FAILED
    assert result["error_code"] == "internal_error"


async def test_ingest_many_parallel_one_bad_file_does_not_block_others(make_ctx, resp):
    ctx = make_ctx([
        resp(200, {"success": True, "data": {"documents": [
            {"document_id": 1, "status": "processed", "chunk_count": 1, "error": None,
             "error_code": None, "expires_at": None}]}}),
        ConnectionError("boom"),
        ConnectionError("boom"),
    ])
    r1 = await lifecycle.create_pending(ctx, "ok.txt", "text/plain", 5)
    r2 = await lifecycle.create_pending(ctx, "bad.txt", "text/plain", 5)
    summary = await lifecycle.ingest_many(ctx, [(r1, b"a"), (r2, b"b")], concurrency=2)
    assert summary == {"ingested": 1, "failed": 1}


async def test_ensure_ready_returns_document_id_when_ready(make_ctx):
    ctx = make_ctx()
    rec = {"status": lifecycle.READY, "document_id": 5, "filename": "a.txt"}
    assert await lifecycle.ensure_ready(ctx, rec) == 5


async def test_ensure_ready_raises_not_ready_for_fresh_pending(make_ctx):
    ctx = make_ctx()
    rec = {"status": lifecycle.PENDING, "uploaded_at": time.time(), "filename": "a.txt"}
    with pytest.raises(lifecycle.NotReadyError):
        await lifecycle.ensure_ready(ctx, rec)


async def test_ensure_ready_reports_stale_pending_as_interrupted(make_ctx):
    ctx = make_ctx()
    rec = {"status": lifecycle.PENDING,
           "uploaded_at": time.time() - lifecycle.STALE_PENDING_S - 1, "filename": "a.txt"}
    with pytest.raises(RuntimeError, match="interrupted"):
        await lifecycle.ensure_ready(ctx, rec)


async def test_ensure_ready_raises_for_expired(make_ctx):
    ctx = make_ctx()
    rec = {"status": lifecycle.EXPIRED, "filename": "a.txt"}
    with pytest.raises(RuntimeError, match="retention period"):
        await lifecycle.ensure_ready(ctx, rec)


async def test_ensure_ready_raises_for_failed_with_reason(make_ctx):
    ctx = make_ctx()
    rec = {"status": lifecycle.FAILED, "filename": "a.txt", "error": "extraction_failed: corrupt file"}
    with pytest.raises(RuntimeError, match="corrupt file"):
        await lifecycle.ensure_ready(ctx, rec)


async def test_mark_expired_if_gone_clears_document_id(make_ctx):
    ctx = make_ctx()
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 5)
    await lifecycle.set_fields(ctx, rec, status=lifecycle.READY, document_id=9)
    updated = await lifecycle.mark_expired_if_gone(ctx, rec)
    assert updated["status"] == lifecycle.EXPIRED
    assert updated["document_id"] is None


async def test_forget_deletes_record_and_calls_engine_delete(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {"deleted": True, "document_id": 3}})])
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 5)
    await lifecycle.set_fields(ctx, rec, status=lifecycle.READY, document_id=3)
    await lifecycle.forget(ctx, rec["file_id"])
    assert await lifecycle.all_files(ctx) == []
    assert ctx.http.calls[0][0] == "delete"


async def test_forget_unknown_file_id_raises(make_ctx):
    ctx = make_ctx()
    with pytest.raises(RuntimeError, match="not found"):
        await lifecycle.forget(ctx, "does-not-exist")


async def test_forget_many_parallel_removes_all_and_skips_unknown(make_ctx, resp):
    ctx = make_ctx([
        resp(200, {"success": True, "data": {"deleted": True, "document_id": 1}}),
        resp(200, {"success": True, "data": {"deleted": True, "document_id": 2}}),
    ])
    r1 = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 5)
    await lifecycle.set_fields(ctx, r1, status=lifecycle.READY, document_id=1)
    r2 = await lifecycle.create_pending(ctx, "b.txt", "text/plain", 5)
    await lifecycle.set_fields(ctx, r2, status=lifecycle.READY, document_id=2)
    removed = await lifecycle.forget_many(ctx, [r1["file_id"], r2["file_id"], "unknown-id"])
    assert removed == 2
    assert await lifecycle.all_files(ctx) == []


async def test_forget_many_pending_file_never_calls_engine_delete(make_ctx):
    ctx = make_ctx([])  # no responses scripted — a call would raise AssertionError
    rec = await lifecycle.create_pending(ctx, "a.txt", "text/plain", 5)
    removed = await lifecycle.forget_many(ctx, [rec["file_id"]])
    assert removed == 1
