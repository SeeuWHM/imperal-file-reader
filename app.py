"""File Reader — Extension instance (imperal-sdk 5.9.x).

The user drops files in the panel → the engine (whm-doc-extractor-api) extracts
their text → Webby reads and semantically searches them by file_id. Raw bytes
are NEVER persisted anywhere in this extension: they live only in the
background ingest coroutine's memory, between receipt and the single engine
call. The engine is reached over the nginx-proxied public path; bearer auth is
optional via DOC_EXTRACTOR_TOKEN (see providers/helpers.py).

Simpler than the Google Drive Connector: no OAuth, no app secrets, no Picker —
the file_id IS the ctx.store record id, the only identifier any tool uses.
"""
from __future__ import annotations

import logging

from imperal_sdk import Extension
from imperal_sdk.chat import ChatExtension

log = logging.getLogger("file_reader")

ext = Extension(
    "file-reader",
    version="0.3.1",
    system=True,
    display_name="File Reader",
    description=(
        "Upload files (PDF, Office, text, CSV, images, scans, and more) and Webby "
        "reads and semantically searches their contents using the extractor backend. "
        "Image-reading policy is owned by the backend; the extension reports whatever "
        "method the backend actually used. Nothing is stored but the extracted text — "
        "your raw files are never kept."
    ),
    icon="icon.svg",
    actions_explicit=True,
    capabilities=["store:read", "store:write"],
)

chat = ChatExtension(
    ext=ext,
    tool_name="tool_file_reader_chat",
    description=(
        "File Reader — the user uploads files in the panel dropzone; read their extracted text "
        "and semantically search across them by file_id. Uploading a file indexes it automatically; "
        "no command is needed to start. Use list_files to see what's available and its file_id."
    ),
)


@ext.health_check
async def health(ctx) -> dict:
    return {"status": "ok", "version": ext.version}


@ext.on_install
async def on_install(ctx):
    pass
