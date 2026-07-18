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


async def test_active_hashes_only_counts_held_records(make_ctx):
    # idempotency key set: only pending/indexing/ready records with a hash count,
    # so a failed/expired file can be re-uploaded but a live one is deduped.
    ctx = make_ctx()
    ctx.store.seed("filereader_files", [
        {"status": lifecycle.READY, "content_hash": "aaa"},
        {"status": lifecycle.INDEXING, "content_hash": "bbb"},
        {"status": lifecycle.FAILED, "content_hash": "ccc"},
        {"status": lifecycle.EXPIRED, "content_hash": "ddd"},
        {"status": lifecycle.PENDING, "content_hash": None},
    ])
    assert await lifecycle.active_hashes(ctx) == {"aaa", "bbb"}


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


async def test_ingest_now_processed_marks_ready_with_document_id(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {"documents": [
        {"document_id": 7, "status": "processed", "chunk_count": 3, "error": None,
         "error_code": None, "expires_at": "2026-07-19T00:00:00+00:00"}
    ]}})])
    result = await lifecycle.ingest_now(ctx, "a.txt", "text/plain", b"hello", content_hash="h1")
    assert result["status"] == lifecycle.READY
    assert result["document_id"] == 7
    assert result["chunk_count"] == 3
    assert result["expires_at"] is not None
    # record persisted with its content hash (the idempotency key)
    assert (await lifecycle.all_files(ctx))[0]["content_hash"] == "h1"


async def test_ingest_now_engine_pending_marks_indexing(make_ctx, resp):
    # The engine is async: POST returns pending at once; we store the
    # document_id and leave the record 'indexing' for reconcile to finish.
    ctx = make_ctx([resp(200, {"success": True, "data": {"documents": [
        {"document_id": 11, "status": "pending", "chunk_count": 0, "error": None,
         "error_code": None, "expires_at": None}
    ]}})])
    result = await lifecycle.ingest_now(ctx, "a.txt", "text/plain", b"hello")
    assert result["status"] == lifecycle.INDEXING
    assert result["document_id"] == 11


async def test_ingest_now_engine_unsupported_marks_failed(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {"documents": [
        {"document_id": None, "status": "unsupported", "error": "video is not readable",
         "error_code": "unsupported_format", "chunk_count": 0, "expires_at": None}
    ]}})])
    result = await lifecycle.ingest_now(ctx, "a.mp4", "video/mp4", b"\x00\x01")
    assert result["status"] == lifecycle.FAILED
    assert result["error_code"] == "unsupported_format"


async def test_ingest_now_network_failure_marks_failed_not_raises(make_ctx):
    ctx = make_ctx([ConnectionError("boom"), ConnectionError("boom")])
    result = await lifecycle.ingest_now(ctx, "a.txt", "text/plain", b"x")
    assert result["status"] == lifecycle.FAILED
    assert result["error_code"] == "internal_error"


async def test_reconcile_promotes_indexing_to_ready(make_ctx, resp):
    # engine finished in its own drain loop → reconcile pulls the outcome in
    ctx = make_ctx([resp(200, {"success": True, "data":
        {"document_id": 7, "status": "processed", "chunk_count": 2, "error": None,
         "error_code": None, "expires_at": None}})])
    ctx.store.seed("filereader_files", [
        {"status": lifecycle.INDEXING, "document_id": 7, "filename": "a.txt",
         "uploaded_at": time.time(), "chunk_count": 0}
    ])
    await lifecycle.reconcile_pending(ctx)
    rec = (await lifecycle.all_files(ctx))[0]
    assert rec["status"] == lifecycle.READY
    assert rec["chunk_count"] == 2


async def test_reconcile_leaves_still_processing_as_indexing(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data":
        {"document_id": 9, "status": "processing", "chunk_count": 0, "error": None,
         "error_code": None, "expires_at": None}})])
    ctx.store.seed("filereader_files", [
        {"status": lifecycle.INDEXING, "document_id": 9, "filename": "a.txt",
         "uploaded_at": time.time()}
    ])
    await lifecycle.reconcile_pending(ctx)
    assert (await lifecycle.all_files(ctx))[0]["status"] == lifecycle.INDEXING


async def test_reconcile_fails_stale_pending_without_document(make_ctx):
    # a pending record that never got an engine doc (interrupted upload) and is
    # stale → failed so it can be re-uploaded; NO engine call is made for it
    ctx = make_ctx([])
    ctx.store.seed("filereader_files", [
        {"status": lifecycle.PENDING, "document_id": None, "filename": "a.txt",
         "uploaded_at": time.time() - lifecycle.STALE_PENDING_S - 1}
    ])
    await lifecycle.reconcile_pending(ctx)
    assert (await lifecycle.all_files(ctx))[0]["status"] == lifecycle.FAILED


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


# ── pre-ingested references (adopt_reference / is_reference_item) ─────────────


def test_is_reference_item():
    assert lifecycle.is_reference_item(
        {"document_id": 7, "content_hash": "h", "name": "a.pdf"})
    assert not lifecycle.is_reference_item(
        {"data_base64": "QQ==", "name": "a.pdf"})
    # bytes win when both are present (back-compat)
    assert not lifecycle.is_reference_item(
        {"document_id": 7, "data_base64": "QQ=="})
    assert not lifecycle.is_reference_item("QQ==")
    assert not lifecycle.is_reference_item({"name": "a.pdf"})


async def test_adopt_reference_processed_marks_ready(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 7, "status": "processed", "chunk_count": 3,
        "error": None, "error_code": None,
        "expires_at": "2026-08-03T00:00:00+00:00"}})])
    rec = await lifecycle.adopt_reference(ctx, "big.pdf", "application/pdf",
                                          5_000_000, 7, content_hash="abc123")
    assert rec["status"] == lifecycle.READY
    assert rec["document_id"] == 7
    assert rec["chunk_count"] == 3
    assert rec["content_hash"] == "abc123"
    assert rec["size_bytes"] == 5_000_000
    # the engine was consulted via overview — never a (re-)ingest
    assert ctx.files.calls[0][0] == "overview"
    assert all(c[0] != "ingest" for c in ctx.files.calls)


async def test_adopt_reference_still_draining_marks_indexing(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 8, "status": "processing", "chunk_count": 0,
        "error": None, "error_code": None, "expires_at": None}})])
    rec = await lifecycle.adopt_reference(ctx, "b.docx", None, 100, 8)
    assert rec["status"] == lifecycle.INDEXING
    assert rec["document_id"] == 8


async def test_adopt_reference_bogus_id_creates_no_record(make_ctx, resp):
    ctx = make_ctx([resp(404, {"error": {"message": "not found"}})])
    with pytest.raises(Exception):
        await lifecycle.adopt_reference(ctx, "x.pdf", None, 10, 999)
    assert await lifecycle.all_files(ctx) == []  # fail-closed: nothing persisted


async def test_adopt_reference_engine_5xx_raises_no_record(make_ctx, resp):
    ctx = make_ctx([resp(500, {"error": {"message": "boom"}}),
                    resp(500, {"error": {"message": "boom"}})])  # retry eats both
    with pytest.raises(Exception):
        await lifecycle.adopt_reference(ctx, "x.pdf", None, 10, 999)
    assert await lifecycle.all_files(ctx) == []
