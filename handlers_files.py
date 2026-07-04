"""File Reader · file management tools — list and forget.

forget_files is the single authority that removes both the local record and
the engine's stored text together (see providers.lifecycle.forget_many), so
the panel and the engine never drift.
"""
from __future__ import annotations

import logging

from imperal_sdk.chat.action_result import ActionResult

from app import chat
from providers import lifecycle
from schemas import EmptyParams, FileIdsParams
from schemas_sdl import FileList, ForgetResult, build_file_list, build_forget_result

log = logging.getLogger("file_reader")


@chat.function(
    "list_files", action_type="read", data_model=FileList,
    description=(
        "List all files the user has uploaded to File Reader, with each file_id, name, type, size, "
        "status (pending/indexing/ready/failed/expired) and whether it's searchable. Use this to find "
        "the file_id to pass to read_files / search_files / file_overview / forget_files."
    ),
)
async def fn_list_files(ctx, params: EmptyParams) -> ActionResult:
    recs = await lifecycle.all_files(ctx)
    ready = sum(1 for r in recs if r.get("status") == "ready")
    return ActionResult.success(
        data=build_file_list(recs),
        summary=f"{len(recs)} file(s), {ready} ready." if recs else "No files uploaded yet.",
    )


@chat.function(
    "forget_files", action_type="destructive", event="file_reader.files_forgotten",
    effects=["delete:file"],
    data_model=ForgetResult,
    description=(
        "Permanently delete one or more uploaded files by file_id — removes their extracted text from "
        "the engine as well. Irreversible; the raw file was never kept, so a deleted file must be "
        "re-uploaded to use again."
    ),
)
async def fn_forget_files(ctx, params: FileIdsParams) -> ActionResult:
    removed = await lifecycle.forget_many(ctx, params.file_ids)
    return ActionResult.success(
        data=build_forget_result(removed),
        summary=f"Removed {removed} file(s).",
        refresh_panels=["file_reader_files"],
    )
