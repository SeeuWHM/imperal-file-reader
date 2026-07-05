"""File Reader · read/search tools — thin wrappers over providers.content_ops.

read_files / file_overview / search_files all take file_id(s) — the ctx.store
record id shown by list_files and the panel. They accept 1..N ids and return a
real SDL EntityList, so Webby can read or search across many files in one call.
"""
from __future__ import annotations

import logging

from imperal_sdk.chat.action_result import ActionResult

from app import chat
from providers import content_ops, lifecycle
from schemas import FileIdsParams, ReadFilesParams, SearchFilesParams
from schemas_sdl import (
    FileOverviewList, FileTextList, SearchResults,
    build_file_overview_list, build_file_text_list, build_search_results,
)

log = logging.getLogger("file_reader")


@chat.function(
    "read_files", action_type="read", data_model=FileTextList,
    description=(
        "Read the extracted text of one or more uploaded files by file_id. Returns a windowed slice "
        "(offset/limit in characters) per file; use has_more + returned_chars to page through large files. "
        "A file still indexing comes back as 'preparing' — ask again shortly."
    ),
)
async def fn_read_files(ctx, params: ReadFilesParams) -> ActionResult:
    await lifecycle.reconcile_pending(ctx)
    try:
        results = await content_ops.read_files(ctx, params.file_ids, params.offset, params.limit)
    except Exception as e:  # noqa: BLE001
        return ActionResult.error(str(e), retryable=False)
    ok = sum(1 for r in results if (r.get("status") or "ok") == "ok")
    preparing = sum(1 for r in results if r.get("status") == "preparing")
    summary = f"Read {ok}/{len(results)} file(s)" + (f", {preparing} still preparing" if preparing else "") + "."
    return ActionResult.success(data=build_file_text_list(results), summary=summary)


@chat.function(
    "file_overview", action_type="read", data_model=FileOverviewList,
    description=(
        "Get a quick overview (name, type, size, status, short preview) for one or more uploaded files "
        "by file_id — use before reading to see what a file is and whether it's ready."
    ),
)
async def fn_file_overview(ctx, params: FileIdsParams) -> ActionResult:
    await lifecycle.reconcile_pending(ctx)
    try:
        results = await content_ops.file_overview(ctx, params.file_ids)
    except Exception as e:  # noqa: BLE001
        return ActionResult.error(str(e), retryable=False)
    return ActionResult.success(data=build_file_overview_list(results), summary=f"{len(results)} file(s).")


@chat.function(
    "search_files", action_type="read", data_model=SearchResults,
    description=(
        "Semantically search across the user's uploaded files and return the most relevant passages. "
        "Pass file_ids to restrict the search to specific files; leave it empty to search everything. "
        "Best for 'find where X is discussed' — use read_files when you already know which file to read."
    ),
)
async def fn_search_files(ctx, params: SearchFilesParams) -> ActionResult:
    await lifecycle.reconcile_pending(ctx)
    try:
        data = await content_ops.search_files(ctx, params.query, file_ids=(params.file_ids or None), k=params.k)
    except Exception as e:  # noqa: BLE001
        return ActionResult.error(str(e), retryable=False)
    n = len(data.get("results", []))
    summary = f"{n} result(s) ({data.get('mode', 'semantic')})." if n else "No matches found."
    return ActionResult.success(data=build_search_results(data), summary=summary)
