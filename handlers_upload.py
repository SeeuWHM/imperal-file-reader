"""File Reader · upload path.

receive_files is triggered by the panel's ui.FileUpload widget (on_upload),
NOT called conversationally. It must be defensive about the payload shape the
frontend actually sends — dicts ({filename|name, content|data|base64,
mime_type|type}) or bare base64 strings, possibly "data:<mime>;base64,…" URIs
(the exact shape is confirmed live by the fileupload-spike). It does the fast,
synchronous part inline (decode + validate + quota + create the pending
records, for instant panel feedback) and hands the heavy engine ingest to a
background task so the upload returns immediately.
"""
from __future__ import annotations

import base64
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
        content = raw.get("content") or raw.get("data") or raw.get("base64") or ""
        filename = raw.get("filename") or raw.get("name")
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


async def _ingest_job(ctx, items: list[tuple[dict, bytes]]) -> ActionResult:
    """Background: push each file's bytes into the engine. Bytes live ONLY in
    this coroutine's memory, never in the store. Returns an ActionResult (a
    completion message + a panel refresh) per the SDK's background contract."""
    res = await lifecycle.ingest_many(ctx, items)
    tail = f" ({res['failed']} failed)" if res.get("failed") else ""
    return ActionResult.success(
        data=build_receive_result([], []),
        summary=f"✅ Indexed {res.get('ingested', 0)} file(s){tail}.",
        refresh_panels=["file_reader_files"],
    )


@chat.function(
    "receive_files", action_type="write", event="file_reader.files_received",
    effects=["create:file"],
    data_model=ReceiveResult,
    description=(
        "Receive files uploaded through the File Reader panel dropzone and start indexing them "
        "in the background. Triggered by the upload widget — not something the user calls directly."
    ),
)
async def fn_receive_files(ctx, params: ReceiveFilesParams) -> ActionResult:
    received: list[dict] = []
    rejected: list[dict] = []
    decoded: list[tuple[str, str | None, bytes]] = []
    total_bytes = 0

    for raw in (params.files or []):
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
        decoded.append((fn, mime, content))
        total_bytes += len(content)

    if not decoded:
        # DIAGNOSTIC (temporary): the on_upload payload shape is undocumented and
        # nothing decoded — surface the exact shape the frontend sent so we can
        # write a precise decoder. Never echoes file bytes.
        shapes = " | ".join(_describe(r) for r in (params.files or [])[:3]) or "files=[] (empty payload)"
        log.warning("receive_files: no files accepted; payload shape → %s", shapes)
        return ActionResult.success(
            data=build_receive_result([], rejected),
            summary=f"No files were accepted (diagnostic). Payload shape → {shapes}",
        )

    try:
        await lifecycle.check_quota(ctx, len(decoded), total_bytes)
    except Exception as e:  # noqa: BLE001 — quota is a user-facing, non-retryable decision
        return ActionResult.error(str(e), retryable=False)

    to_ingest: list[tuple[dict, bytes]] = []
    for fn, mime, content in decoded:
        rec = await lifecycle.create_pending(ctx, fn, mime, len(content))
        received.append({"file_id": rec["file_id"], "filename": fn,
                         "size_bytes": len(content), "status": "queued"})
        to_ingest.append((rec, content))

    try:
        await ctx.background_task(_ingest_job(ctx, to_ingest), long_running=True, name="filereader-ingest")
    except Exception as e:  # noqa: BLE001 — no background hook (e.g. dev): ingest inline
        log.warning("could not start background ingest, running inline: %s", e)
        await lifecycle.ingest_many(ctx, to_ingest)

    summary = f"{len(received)} file(s) received and indexing"
    if rejected:
        summary += f", {len(rejected)} rejected"
    return ActionResult.success(data=build_receive_result(received, rejected), summary=summary + ".")
