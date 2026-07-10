"""providers/extractor.py — the engine client. Verifies request shape
(multipart files=, source=filereader, imperal_id scoping), the one-retry-on-5xx
policy, and how engine responses map to raised/returned values."""
from __future__ import annotations

import pytest

from providers import extractor


async def test_ingest_sends_real_multipart_with_source_and_imperal_id(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {"documents": [
        {"document_id": 1, "source": "filereader", "imperal_id": "user-123",
         "sha256": "abc", "filename": "a.txt", "mime": "text/plain", "size_bytes": 5,
         "preview": "hello", "status": "processed", "stage": "done", "error": None,
         "error_code": None, "chunk_count": 1, "created_at": None, "expires_at": None}
    ]}})])
    doc = await extractor.ingest(ctx, filename="a.txt", content=b"hello", mime_type="text/plain")
    assert doc["document_id"] == 1
    assert doc["status"] == "processed"

    method, url, kwargs = ctx.http.calls[0]
    assert method == "post"
    assert url.endswith("/v1/documents")
    assert kwargs["data"] == {"source": "filereader", "imperal_id": "user-123"}
    assert kwargs["files"]["files"] == ("a.txt", b"hello", "text/plain")
    assert kwargs["headers"] == {}


async def test_ingest_raises_without_imperal_id(make_ctx):
    ctx = make_ctx([], with_user=False)
    with pytest.raises(RuntimeError, match="no user context"):
        await extractor.ingest(ctx, filename="a.txt", content=b"x")


async def test_ingest_retries_once_on_5xx_then_succeeds(make_ctx, resp):
    ctx = make_ctx([
        resp(502, {}),
        resp(200, {"success": True, "data": {"documents": [
            {"document_id": 2, "source": "filereader", "imperal_id": "user-123", "sha256": "x",
             "filename": "b.txt", "mime": "text/plain", "size_bytes": 1, "preview": None,
             "status": "processed", "stage": "done", "error": None, "error_code": None,
             "chunk_count": 0, "created_at": None, "expires_at": None}
        ]}}),
    ])
    doc = await extractor.ingest(ctx, filename="b.txt", content=b"x")
    assert doc["document_id"] == 2
    assert len(ctx.http.calls) == 2


async def test_ingest_raises_after_persistent_5xx(make_ctx, resp):
    ctx = make_ctx([resp(500, {}), resp(500, {})])
    with pytest.raises(RuntimeError, match="engine returned 500"):
        await extractor.ingest(ctx, filename="c.txt", content=b"x")


async def test_ingest_raises_when_engine_returns_no_documents(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {"documents": []}})])
    with pytest.raises(RuntimeError, match="no document"):
        await extractor.ingest(ctx, filename="d.txt", content=b"x")


async def test_read_text_returns_window(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "text": "hello world", "offset": 0, "limit": 40000,
        "total_chars": 11, "truncated": False,
    }})])
    data = await extractor.read_text(ctx, 1, offset=0, limit=40000)
    assert data["text"] == "hello world"
    method, url, kwargs = ctx.http.calls[0]
    assert kwargs["params"]["source"] == "filereader"
    assert kwargs["params"]["imperal_id"] == "user-123"
    assert kwargs["headers"] == {}


async def test_read_text_logs_payload_shape(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "text": "hello world", "offset": 0, "limit": 40000,
        "total_chars": 11, "truncated": False, "status": "processed", "stage": "done", "chunk_count": 3,
    }})])
    logger = extractor.log
    with pytest.MonkeyPatch.context() as mp:
        calls = []
        mp.setattr(logger, "info", lambda fmt, document_id, payload: calls.append((fmt, document_id, payload)))
        data = await extractor.read_text(ctx, 1, offset=0, limit=40000)
    assert data["text"] == "hello world"
    assert calls == [
        (
            "doc_extractor.read_text document_id=%s payload=%s",
            1,
            '{"chunk_count": 3, "document_id": 1, "image_ai_used": false, "is_inferred": false, "limit": 40000, "ocr_used": false, "offset": 0, "stage": "done", "status": "processed", "text_len": 11, "total_chars": 11, "truncated": false}',
        )
    ]


async def test_read_text_raises_on_404(make_ctx, resp):
    ctx = make_ctx([resp(404, {"success": False, "error": {"code": "NOT_FOUND", "message": "gone"}})])
    with pytest.raises(RuntimeError, match="HTTP 404"):
        await extractor.read_text(ctx, 999)


async def test_search_returns_hits(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "query": "invoice", "count": 1,
        "hits": [{"document_id": 1, "filename": "a.pdf", "seq": 0, "text": "Invoice #42", "score": 0.83}],
    }})])
    hits = await extractor.search(ctx, "invoice", k=6)
    assert hits[0]["filename"] == "a.pdf"
    method, url, kwargs = ctx.http.calls[0]
    assert kwargs["json"] == {"source": "filereader", "imperal_id": "user-123", "query": "invoice", "k": 6}
    assert kwargs["headers"] == {}


async def test_overview_returns_document_out(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {
        "document_id": 1, "source": "filereader", "imperal_id": "user-123", "sha256": "x",
        "filename": "a.pdf", "mime": "application/pdf", "size_bytes": 10, "preview": "hi",
        "status": "processed", "stage": "done", "error": None, "error_code": None,
        "chunk_count": 2, "created_at": None, "expires_at": None,
    }})])
    doc = await extractor.overview(ctx, 1)
    assert doc["preview"] == "hi"


async def test_delete_returns_true_on_success(make_ctx, resp):
    ctx = make_ctx([resp(200, {"success": True, "data": {"deleted": True, "document_id": 1}})])
    assert await extractor.delete(ctx, 1) is True


async def test_delete_returns_false_on_404_already_gone(make_ctx, resp):
    ctx = make_ctx([resp(404, {"success": False, "error": {"code": "NOT_FOUND", "message": "gone"}})])
    assert await extractor.delete(ctx, 1) is False


async def test_classify_extraction_returns_truth_without_guessing():
    assert extractor.classify_extraction(None) == {
        "extraction_method": None,
        "image_ai_used": False,
        "ocr_used": False,
        "is_inferred": False,
        "is_partial": False,
        "text_quality": None,
        "noise_score": None,
    }
    assert extractor.classify_extraction({"extraction_method": "ai_vision"}) == {
        "extraction_method": "ai_vision",
        "image_ai_used": True,
        "ocr_used": False,
        "is_inferred": False,
        "is_partial": False,
        "text_quality": None,
        "noise_score": None,
    }
    assert extractor.classify_extraction({"extraction_method": "ocr"}) == {
        "extraction_method": "ocr",
        "image_ai_used": False,
        "ocr_used": True,
        "is_inferred": False,
        "is_partial": False,
        "text_quality": None,
        "noise_score": None,
    }
    assert extractor.classify_extraction({"image_ai_used": True, "ocr_used": False}) == {
        "extraction_method": None,
        "image_ai_used": True,
        "ocr_used": False,
        "is_inferred": False,
        "is_partial": False,
        "text_quality": None,
        "noise_score": None,
    }
    assert extractor.classify_extraction({"extraction_method": "ocr", "is_partial": True,
                                          "text_quality": 0.8, "noise_score": 0.2}) == {
        "extraction_method": "ocr",
        "image_ai_used": False,
        "ocr_used": True,
        "is_inferred": False,
        "is_partial": True,
        "text_quality": 0.8,
        "noise_score": 0.2,
    }


async def test_diagnostic_payload_includes_backend_extraction_truth_without_text_fields():
    payload = extractor._diagnostic_payload({
        "document_id": 5,
        "extraction_method": "ai_vision",
        "image_ai_used": True,
        "ocr_used": False,
    })
    assert payload == '{"document_id": 5, "extraction_method": "ai_vision", "image_ai_used": true, "is_inferred": false, "ocr_used": false}'


async def test_ingest_sends_bearer_when_token_configured(make_ctx, resp, monkeypatch):
    monkeypatch.setattr(extractor, "DOC_EXTRACTOR_TOKEN", "secret-token")
    ctx = make_ctx([resp(200, {"success": True, "data": {"documents": [{
        "document_id": 3, "source": "filereader", "imperal_id": "user-123",
        "sha256": "abc", "filename": "secured.txt", "mime": "text/plain", "size_bytes": 5,
        "preview": "hello", "status": "processed", "stage": "done", "error": None,
        "error_code": None, "chunk_count": 1, "created_at": None, "expires_at": None}
    ]}})])

    doc = await extractor.ingest(ctx, filename="secured.txt", content=b"hello", mime_type="text/plain")
    assert doc["document_id"] == 3
    _, _, kwargs = ctx.http.calls[0]
    assert kwargs["headers"] == {"Authorization": "Bearer secret-token"}
