"""File Reader — Pydantic parameter models for the @chat.function tools.

The providers/ layer (SDK-free, tested) holds the logic; these are just the
typed inputs the kernel validates before calling a handler.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class EmptyParams(BaseModel):
    pass


class ReceiveFilesParams(BaseModel):
    # Raw payload from ui.FileUpload's on_upload action. Live-verified shape
    # (2026-07-05): list of dicts, each {data_base64, name, mime_type, size} —
    # decoded in handlers_upload._decode_one.
    # Items: inline bytes {data_base64,name,mime_type,size} OR a pre-ingested
    # engine reference {document_id,content_hash,name,mime_type,size} (no bytes).
    files: list = Field(default_factory=list)


class ReadFilesParams(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int | None = None


class SearchFilesParams(BaseModel):
    query: str
    file_ids: list[str] = Field(default_factory=list)   # empty → semantic over ALL
    k: int | None = None


class FileIdsParams(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
