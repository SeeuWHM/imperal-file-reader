"""File Reader · panel (right slot).

A dropzone on top (ui.FileUpload → receive_files) and, below it, the list of
uploaded files with their indexing status and a per-file Remove action.

max_size_mb note: the ENGINE handles very large files (500MB proven, RAM-zero),
but an uploaded file travels to receive_files as base64 THROUGH the kernel
tool-call (a Temporal payload path with size limits — extension-plan §0.3), so
the dropzone cap is deliberately conservative until a live upload confirms how
large a file that path tolerates. This is the real limiter, not the engine.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui

from app import ext
from providers import lifecycle

log = logging.getLogger("file_reader")

# Colours the panel uses (green/red/orange/blue) — safe across light/dark themes.
_STATUS_COLOR = {"ready": "green", "failed": "red", "expired": "orange"}

# Conservative front-line cap for the base64-through-kernel upload path (see
# module docstring). lifecycle.MAX_SINGLE_FILE_BYTES is a higher backstop.
_DROPZONE_MAX_MB = 25


def _human_size(size_bytes) -> str:
    try:
        n = float(size_bytes or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _file_items(files: list[dict]) -> list:
    items = []
    for f in files:
        name = f.get("filename") or "?"
        status = f.get("status") or "pending"
        subtitle = " · ".join(p for p in (_human_size(f.get("size_bytes")), status) if p)
        items.append(ui.ListItem(
            id=f["file_id"], title=name, subtitle=subtitle,
            badge=ui.Badge(status, color=_STATUS_COLOR.get(status, "blue")),
            actions=[{"label": "Remove", "icon": "Trash2",
                      "on_click": ui.Call("forget_files", file_ids=[f["file_id"]])}],
        ))
    return items


@ext.panel("file_reader_files", slot="right", title="File Reader", icon="FileText")
async def build_file_reader_panel(ctx, **kwargs) -> ui.UINode:
    try:
        await lifecycle.reconcile_pending(ctx)
        files = await lifecycle.all_files(ctx)
    except Exception as exc:  # noqa: BLE001
        log.error(f"file_reader panel error: {exc}")
        return ui.Stack([
            ui.Header(text="File Reader", level=3),
            ui.Alert(message=f"Error loading panel: {exc}", type="error"),
        ], gap=2)

    files_block = (
        ui.List(items=_file_items(files), searchable=True) if files
        else ui.Empty(message="No files yet — drop files above to have Webby read them", icon="FileText")
    )

    return ui.Stack([
        ui.Header(text="File Reader", level=3),
        ui.Text("Drop files here and Webby will read and search them. Uploading a file indexes it — "
                "no command needed.", variant="caption"),
        ui.FileUpload(multiple=True, max_size_mb=_DROPZONE_MAX_MB, accept="*",
                      on_upload=ui.Call("receive_files")),
        ui.Divider(),
        ui.Text("Your files", variant="caption"),
        files_block,
    ], gap=2, className="pb-4")
