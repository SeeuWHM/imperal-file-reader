"""File Reader — Pydantic parameter models for the @chat.function tools.

The providers/ layer (SDK-free, tested) holds the logic; these are just the
typed inputs the kernel validates before calling a handler.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class EmptyParams(BaseModel):
    pass


class ReceiveFilesParams(BaseModel):
    # Raw payload from ui.FileUpload's on_upload action. Items may be dicts
    # ({filename|name, content|data|base64, mime_type|type}) OR bare base64
    # strings (possibly "data:<mime>;base64,…" URIs) — decoded defensively in
    # handlers_upload (the shape is confirmed live by the fileupload-spike).
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
