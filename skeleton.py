"""File Reader · skeleton — the cheap, always-available summary the kernel
caches so Webby knows what's uploaded without a tool call (TTL 300s).

Scalars + one short list, degrade-to-zeros on error — per the skeleton
contract (see imperal-cloud/sdk/skeleton.md)."""
from __future__ import annotations

import logging

from app import ext
from providers import lifecycle

log = logging.getLogger("file_reader")


@ext.skeleton(
    "file_reader_files", ttl=300,
    description="File Reader status — how many files are uploaded, ready/searchable, processing or failed, plus recent file names.",
)
async def skeleton_file_reader(ctx) -> dict:
    try:
        files = await lifecycle.all_files(ctx)
    except Exception as e:  # noqa: BLE001 — skeleton must never raise; degrade to zeros
        log.warning("file_reader skeleton failed, degrading to zeros: %s", e)
        return {"response": {"files_total": 0, "files_ready": 0, "files_processing": 0,
                             "files_failed": 0, "recent_files": []}}
    ready = sum(1 for f in files if f.get("status") == "ready")
    processing = sum(1 for f in files if f.get("status") in ("pending", "indexing"))
    failed = sum(1 for f in files if f.get("status") == "failed")
    return {"response": {
        "files_total": len(files),
        "files_ready": ready,
        "files_processing": processing,
        "files_failed": failed,
        "recent_files": [f.get("filename") or "?" for f in files[:5]],
    }}
