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
            raise RuntimeError(f"HTTP {self.status_code}")


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
    def __init__(self, responses=None, imperal_id="user-123", with_user=True):
        self.http = FakeHttp(responses or [])
        self.store = FakeStore()
        self.user = FakeUser(imperal_id) if with_user else None


@pytest.fixture
def resp():
    """Factory for building scripted responses: resp(status, json_dict)."""
    return FakeResponse


@pytest.fixture
def make_ctx():
    """Factory: make_ctx([resp(...), ConnectionError(...), ...])."""
    def _make(responses=None, imperal_id="user-123", with_user=True):
        return FakeCtx(responses, imperal_id, with_user)
    return _make
