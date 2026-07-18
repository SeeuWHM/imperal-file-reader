"""File Reader · upload path.

receive_files is triggered by the panel's ui.FileUpload widget (on_upload),
NOT called conversationally. Live-verified payload contract (2026-07-05): the
`files` param is a list of dicts, each {data_base64: <bare base64, no data: URI>,
name: <filename>, mime_type: <mime>, size: <int>}. The decoder also tolerates a
bare base64 string / data: URI as a harmless fallback. It does the fast,
synchronous part inline (decode + validate + quota + create the pending
records, for instant panel feedback) and hands the heavy engine ingest to a
background task so the upload returns immediately.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from imperal_sdk.chat.action_result import ActionResult

from app import chat
from providers import lifecycle
from schemas import ReceiveFilesParams
from schemas_sdl import ReceiveResult, build_receive_result

log = logging.getLogger("file_reader")

_UNSUPPORTED_PREFIXES = ("video/", "audio/")


def _describe(raw) -> str:
    """Compact, non-destructive description of one on_upload item's shape.
    The ui.FileUpload on_upload payload schema is not publicly documented, so we
    learn it empirically: report the Python type and (for dicts) each key with
    its value type and string length, without ever echoing file bytes."""
    if isinstance(raw, dict):
        parts = []
        for k in sorted(raw.keys()):
            v = raw[k]
            extra = f"[len={len(v)}]" if isinstance(v, str) else (f"={v!r}" if isinstance(v, (int, float, bool)) else "")
            parts.append(f"{k}:{type(v).__name__}{extra}")
        return "dict{" + ", ".join(parts) + "}"
    if isinstance(raw, str):
        head = raw[:24]
        return f"str[len={len(raw)}]{' data-uri' if raw.startswith('data:') else ''} head={head!r}"
    return f"{type(raw).__name__} repr={str(raw)[:80]!r}"


def _decode_one(raw) -> tuple[str, str | None, bytes]:
    """Return (filename, mime_type, content_bytes) from one upload item, or
    raise. Handles a dict payload or a bare base64 string, and strips a
    'data:<mime>;base64,' URI prefix if present. Mirrors the spike's decoder."""
    filename: str | None = None
    mime: str | None = None
    if isinstance(raw, dict):
        # Live-verified on_upload contract (2026-07-05): each item is
        # {data_base64: <bare base64>, name: <filename>, mime_type: <mime>, size: <int>}.
        # Aliases kept only as a harmless forward-compat fallback.
        content = raw.get("data_base64") or raw.get("content") or raw.get("data") or raw.get("base64") or ""
        filename = raw.get("name") or raw.get("filename")
        mime = raw.get("mime_type") or raw.get("type") or raw.get("content_type")
    elif isinstance(raw, str):
        content = raw
    else:
        raise ValueError(f"unrecognized upload item type: {type(raw).__name__}")

    if not isinstance(content, str):
        raise ValueError("upload content is not a base64 string")
    if content.startswith("data:"):
        head, _, b64 = content.partition(",")
        if not mime and len(head) > 5:
            mime = head[5:].split(";", 1)[0] or None
        content = b64
    return (filename or "file"), mime, base64.b64decode(content, validate=False)


@chat.function(
    "receive_files", action_type="write", event="file_reader.files_received",
    effects=["create:file"],
    data_model=ReceiveResult,
    description=(
        "Receive files uploaded through the File Reader panel dropzone and start indexing them "
        "in the background. Triggered by the upload widget — not something the user calls directly. "
        "Items may alternatively be pre-ingested engine references "
        "{document_id, content_hash, name, mime_type, size} — no bytes."
    ),
)
async def fn_receive_files(ctx, params: ReceiveFilesParams) -> ActionResult:
    received: list[dict] = []
    rejected: list[dict] = []
    # Each candidate: {fn, mime, size, hash, content: bytes|None, document_id|None}.
    # Two accepted item shapes: inline bytes {data_base64,name,mime_type,size} and
    # a pre-ingested engine reference {document_id,content_hash,name,mime_type,size}
    # (bytes were shipped to the engine out-of-band — only the reference crosses
    # the call boundary, so the payload stays tiny).
    candidates: list[dict] = []

    for raw in (params.files or []):
        if lifecycle.is_reference_item(raw):
            fn = raw.get("name") or raw.get("filename") or "file"
            mime = raw.get("mime_type") or raw.get("type") or raw.get("content_type")
            size = int(raw.get("size") or 0)
            if (mime or "").startswith(_UNSUPPORTED_PREFIXES):
                rejected.append({"filename": fn, "reason": "video/audio is not supported"})
                continue
            if size > lifecycle.MAX_SINGLE_FILE_BYTES:
                mb = lifecycle.MAX_SINGLE_FILE_BYTES // (1024 * 1024)
                rejected.append({"filename": fn, "reason": f"exceeds the {mb} MB per-file limit"})
                continue
            candidates.append({"fn": fn, "mime": mime, "size": size,
                               "hash": raw.get("content_hash") or None,
                               "content": None, "document_id": raw["document_id"]})
            continue
        try:
            fn, mime, content = _decode_one(raw)
        except Exception as e:  # noqa: BLE001 — reject this item, keep the rest
            rejected.append({"filename": "?", "reason": f"could not read upload: {e}"})
            continue
        if (mime or "").startswith(_UNSUPPORTED_PREFIXES):
            rejected.append({"filename": fn, "reason": "video/audio is not supported"})
            continue
        if len(content) == 0:
            rejected.append({"filename": fn, "reason": "empty file"})
            continue
        if len(content) > lifecycle.MAX_SINGLE_FILE_BYTES:
            mb = lifecycle.MAX_SINGLE_FILE_BYTES // (1024 * 1024)
            rejected.append({"filename": fn, "reason": f"exceeds the {mb} MB per-file limit"})
            continue
        candidates.append({"fn": fn, "mime": mime, "size": len(content),
                           "hash": None, "content": content, "document_id": None})

    if not candidates:
        # DIAGNOSTIC (temporary): the on_upload payload shape is undocumented and
        # nothing decoded — surface the exact shape the frontend sent so we can
        # write a precise decoder. Never echoes file bytes.
        shapes = " | ".join(_describe(r) for r in (params.files or [])[:3]) or "files=[] (empty payload)"
        log.warning("receive_files: no files accepted; payload shape → %s", shapes)
        return ActionResult.success(
            data=build_receive_result([], rejected),
            summary=f"No files were accepted (diagnostic). Payload shape → {shapes}",
        )

    # Idempotency: the engine is content-addressed by sha256, so a re-upload or
    # a re-fired on_upload (e.g. panel refresh) must NOT create a duplicate
    # record. Dedup by content hash within this batch AND against records we
    # already hold (see the duplicate-panel-entries bug, 2026-07-05).
    # Map content_hash -> existing record so a RE-attached (deduped) file still
    # returns its EXISTING file_id. The composer needs the id to reference the
    # file, and the kernel surfaces it to Webbee; without this a dedup returned
    # an empty file_id and the attachment looked like "nothing attached".
    all_recs = await lifecycle.all_files(ctx)
    by_hash = {
        r["content_hash"]: r for r in all_recs
        if r.get("content_hash") and r.get("status") in (lifecycle.PENDING, lifecycle.INDEXING, lifecycle.READY)
    }
    fresh: list[dict] = []
    seen: set[str] = set()
    already = 0
    for c in candidates:
        h = c["hash"] or (hashlib.sha256(c["content"]).hexdigest()
                          if c["content"] is not None else None)
        c["hash"] = h
        if h:  # a reference without content_hash skips dedup (legacy hashless record)
            if h in seen:
                already += 1
                continue
            seen.add(h)
            existing = by_hash.get(h)
            if existing:
                # Already held — return its existing id so the caller can reference it.
                received.append({"file_id": existing["file_id"], "filename": c["fn"],
                                 "size_bytes": existing.get("size_bytes") or c["size"],
                                 "status": existing.get("status")})
                already += 1
                continue
        fresh.append(c)

    if not fresh:
        return ActionResult.success(
            data=build_receive_result(received, rejected),
            summary=(f"Already have {already} file(s)." if already else "Nothing new to index.")
                    + (f" {len(rejected)} rejected." if rejected else ""),
        )

    try:
        await lifecycle.check_quota(ctx, len(fresh), sum(c["size"] for c in fresh))
    except Exception as e:  # noqa: BLE001 — quota is a user-facing, non-retryable decision
        return ActionResult.error(str(e), retryable=False)

    # Synchronous ingest: POST each file to the engine and record its immediate
    # status. The POST only stages the upload (fast, any file type/size); the
    # engine's own durable drain loop does the heavy extraction+embedding and the
    # panel/read paths reconcile the outcome. We do NOT spawn a background task —
    # it would be cancelled on return, freezing the record (2026-07-05 bug).
    # Reference items adopt the already-ingested engine doc instead (fail-closed:
    # a bogus/foreign document_id rejects THIS item only, creates no record).
    for c in fresh:
        if c["document_id"] is not None:
            try:
                rec = await lifecycle.adopt_reference(ctx, c["fn"], c["mime"], c["size"],
                                                      c["document_id"], content_hash=c["hash"])
            except Exception as e:  # noqa: BLE001 — reject this item, keep the rest
                log.warning("receive_files: reference %r rejected: %s", c["document_id"], e)
                rejected.append({"filename": c["fn"],
                                 "reason": "unknown or inaccessible document reference"})
                continue
        else:
            rec = await lifecycle.ingest_now(ctx, c["fn"], c["mime"], c["content"],
                                             content_hash=c["hash"])
        received.append({"file_id": rec["file_id"], "filename": c["fn"],
                         "size_bytes": c["size"], "status": rec.get("status")})

    summary = f"{len(received)} file(s) received and indexing"
    if already:
        summary += f", {already} already present"
    if rejected:
        summary += f", {len(rejected)} rejected"
    return ActionResult.success(data=build_receive_result(received, rejected), summary=summary + ".")
