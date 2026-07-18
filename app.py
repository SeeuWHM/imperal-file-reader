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
    version="0.3.3",
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
        "File Reader — read and semantically search the CONTENT of files the user UPLOADED or "
        "ATTACHED here (panel dropzone or the chat paperclip), by file_id. Uploading indexes a file "
        "automatically; no command needed. Use list_files to see what's available and its file_id. "
        "This is the system document reader for in-panel uploads/attachments — it is NOT external "
        "cloud storage like Google Drive (that is a separate storage connector). For 'what's in this "
        "file', an attached/uploaded file, or a screenshot, this is the right tool."
    ),
)


@ext.health_check
async def health(ctx) -> dict:
    return {"status": "ok", "version": ext.version}


@ext.on_install
async def on_install(ctx):
    pass
