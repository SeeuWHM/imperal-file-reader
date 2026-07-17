"""Shared test doubles for the File Reader suite.

The providers/* package is import-clean (no imperal_sdk at module load), so
the engine client + lifecycle + content_ops are unit-tested against a fake
ctx that records HTTP calls / replays scripted responses (FakeHttp) and an
in-memory store (FakeStore) — enough to assert request shape, retry
behaviour, the file state machine, and quota without a live engine or the
SDK. Mirrors Google Drive Connector's tests/conftest.py 1:1 (same doubles,
same shape — deliberately kept identical so patterns transfer directly).
"""
from __future__ import annotations

import pytest

from providers import lifecycle


async def make_ready_file(ctx, filename="a.txt", document_id=1, chunk_count=1):
    """Create a file record already in the READY state — the common setup
    for content_ops tests that read/preview/search an indexed file."""
    rec = await lifecycle.create_pending(ctx, filename, "text/plain", 10)
    await lifecycle.set_fields(ctx, rec, status=lifecycle.READY, document_id=document_id,
                               chunk_count=chunk_count)
    return rec


class FakeResponse:
    """Mimics the SDK HTTPResponse surface the code uses."""
    def __init__(self, status_code=200, json_data=None, text_data=""):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self._text = text_data

    def json(self):
        return self._json

    def text(self):
        return self._text

    def raise_for_status(self):
        if self.status_code >= 400:
            message = None
            try:
                err = (self._json or {}).get("error") or {}
                message = err.get("message")
            except Exception:
                message = None
            suffix = f": {message}" if message else ""
            raise RuntimeError(f"HTTP {self.status_code}{suffix}")


class FakeHttp:
    """Replays a scripted list — one item per physical HTTP call, in order.
    FakeResponse → returned; Exception → raised. Records (method, url, kwargs)."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def _next(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        if not self._responses:
            raise AssertionError(f"unexpected extra {method.upper()} {url}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def post(self, url, **kwargs):
        return await self._next("post", url, kwargs)

    async def get(self, url, **kwargs):
        return await self._next("get", url, kwargs)

    async def delete(self, url, **kwargs):
        return await self._next("delete", url, kwargs)

    async def patch(self, url, **kwargs):
        return await self._next("patch", url, kwargs)

    async def put(self, url, **kwargs):
        return await self._next("put", url, kwargs)


class FakeFiles:
    """Faithful stand-in for the kernel's ctx.files (core/files_client.FilesClient
    over core/file_engine.FileEngine). Since the extension now reaches the engine
    THROUGH ctx.files (File Mage Rule 13), the double makes the SAME HTTP calls
    the old in-extension client did — over the shared FakeHttp — so the state-
    machine tests (lifecycle / content_ops) drive it via scripted HTTP responses
    exactly as before. It also records its own method calls in `self.calls` for
    delegation assertions. Mirrors kernel FileEngine's URLs / params / retry /
    unwrap / 404→False / uid-required behaviour 1:1."""

    def __init__(self, http, imperal_id="user-123", token="",
                 base_url="https://api.webhostmost.com/doc-extractor", source="filereader"):
        self._http = http
        self._uid = str(imperal_id or "")
        self._token = (token or "").strip()
        self._source = source
        base = base_url.rstrip("/")
        self._documents_url = f"{base}/v1/documents"
        self._search_url = f"{base}/v1/search"
        self.calls = []

    def _require_uid(self) -> str:
        if not self._uid:
            raise RuntimeError("no user context (imperal_id) — cannot scope file storage")
        return self._uid

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _send(self, method, url, **kwargs):
        last = None
        for _ in range(2):
            try:
                resp = await getattr(self._http, method)(url, **kwargs)
            except Exception as e:  # noqa: BLE001 — network/timeout → retry once
                last = e
                continue
            if getattr(resp, "status_code", 200) >= 500:
                last = RuntimeError(f"engine returned {resp.status_code}")
                continue
            return resp
        raise last if last else RuntimeError("engine request failed")

    async def ingest(self, content, filename, mime_type=None):
        self.calls.append(("ingest", filename, mime_type))
        uid = self._require_uid()
        files = {"files": (filename or "file", content, mime_type or "application/octet-stream")}
        resp = await self._send("post", self._documents_url,
                                data={"source": self._source, "imperal_id": uid},
                                files=files, headers=self._headers(), timeout=120)
        resp.raise_for_status()
        docs = ((resp.json() or {}).get("data") or {}).get("documents") or []
        if not docs:
            raise RuntimeError("engine returned no document")
        return docs[0]

    async def read(self, document_id, offset=0, limit=40_000):
        self.calls.append(("read", document_id, offset, limit))
        uid = self._require_uid()
        resp = await self._send("get", f"{self._documents_url}/{document_id}/text",
                                params={"source": self._source, "imperal_id": uid,
                                        "offset": offset, "limit": limit},
                                headers=self._headers(), timeout=60)
        resp.raise_for_status()
        return (resp.json() or {}).get("data") or {}

    async def search(self, query, k=6):
        self.calls.append(("search", query, k))
        uid = self._require_uid()
        resp = await self._send("post", self._search_url,
                                json={"source": self._source, "imperal_id": uid,
                                      "query": query, "k": k},
                                headers=self._headers(), timeout=60)
        resp.raise_for_status()
        return ((resp.json() or {}).get("data") or {}).get("hits") or []

    async def overview(self, document_id):
        self.calls.append(("overview", document_id))
        uid = self._require_uid()
        resp = await self._send("get", f"{self._documents_url}/{document_id}",
                                params={"source": self._source, "imperal_id": uid},
                                headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return (resp.json() or {}).get("data") or {}

    async def delete(self, document_id):
        self.calls.append(("delete", document_id))
        uid = self._require_uid()
        resp = await self._send("delete", f"{self._documents_url}/{document_id}",
                                params={"source": self._source, "imperal_id": uid},
                                headers=self._headers(), timeout=30)
        if getattr(resp, "status_code", 200) == 404:
            return False
        resp.raise_for_status()
        return True


class _Doc:
    """Mirror of the SDK store Document: `.id` + `.data` (dict)."""
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.data = data


class FakeStore:
    """In-memory store mirroring the subset the extension uses:
    query(collection) -> list of _Doc; create/update/delete by id."""
    def __init__(self):
        self._data = {}   # collection -> {doc_id: data-dict}
        self._seq = 0

    async def query(self, collection, **kwargs):
        return [_Doc(i, dict(d)) for i, d in self._data.get(collection, {}).items()]

    async def create(self, collection, data):
        self._seq += 1
        doc_id = f"d{self._seq}"
        self._data.setdefault(collection, {})[doc_id] = dict(data)
        return _Doc(doc_id, dict(data))

    async def update(self, collection, doc_id, data):
        self._data.setdefault(collection, {})[doc_id] = dict(data)
        return _Doc(doc_id, dict(data))

    async def delete(self, collection, doc_id):
        self._data.get(collection, {}).pop(doc_id, None)

    # ── test helpers (not part of the SDK surface) ──
    def seed(self, collection, records):
        """Insert records directly; returns their assigned doc ids."""
        ids = []
        for r in records:
            self._seq += 1
            doc_id = f"d{self._seq}"
            self._data.setdefault(collection, {})[doc_id] = dict(r)
            ids.append(doc_id)
        return ids

    def rows(self, collection):
        """Current data dicts for assertions."""
        return list(self._data.get(collection, {}).values())


class FakeUser:
    def __init__(self, imperal_id="user-123"):
        self.imperal_id = imperal_id


class FakeCtx:
    def __init__(self, responses=None, imperal_id="user-123", with_user=True, token=""):
        self.http = FakeHttp(responses or [])
        self.store = FakeStore()
        self.user = FakeUser(imperal_id) if with_user else None
        # File Mage: the engine is reached through ctx.files (Rule 13). The
        # double routes to the same FakeHttp, so lifecycle/content_ops tests
        # keep scripting engine responses exactly as before.
        self.files = FakeFiles(self.http, imperal_id=(imperal_id if with_user else ""),
                               token=token)


@pytest.fixture
def resp():
    """Factory for building scripted responses: resp(status, json_dict)."""
    return FakeResponse


@pytest.fixture
def make_ctx():
    """Factory: make_ctx([resp(...), ConnectionError(...), ...])."""
    def _make(responses=None, imperal_id="user-123", with_user=True, token=""):
        return FakeCtx(responses, imperal_id, with_user, token=token)
    return _make
