"""Shared response-shaping helpers for the CONTENT plane (read_files /
file_overview / file_preview / search_files) — text cleanup, backend
extraction-truth passthrough, and the response-size budgeting that keeps
every reply comfortably under the kernel's serialization ceiling.

Split out of content_ops.py (2026-07-10) to keep that module focused on
orchestration — these are pure helpers with no control flow of their own.
"""
from __future__ import annotations

from . import extractor

# Kernel ceiling (verified 2026-07-06): Webby's agentic tool loop serializes
# a whole tool result to one string and hard-cuts it at ~10_000 chars, mid-JSON,
# with no awareness of fields or list items (orchestration/agentic/loop.py,
# _tool_result_content_str / _TOOL_RESULT_MAX). A response that lands right at
# that edge comes through corrupted, not just short — Webby then can't trust
# ANY of it. We stay comfortably under it ourselves so a big ask degrades to
# smaller-but-honest (has_more=True / [N chars total]) instead of broken.
RESPONSE_BUDGET_CHARS = 8_000
_MIN_PER_FILE = 250             # floor per file when read_files batches many at once
_MIN_PER_HIT = 200              # floor per search hit when there are many

DEFAULT_READ_LIMIT = RESPONSE_BUDGET_CHARS   # chars for a single-file read window
MAX_READ_LIMIT = RESPONSE_BUDGET_CHARS       # hard ceiling per file even on an explicit ask —
                                              # nothing bigger survives the kernel intact anyway
FULLTEXT_LIMIT = 5_000_000     # engine cap for exact in-file grep
DEFAULT_SEARCH_K = 6
MAX_SEARCH_K = 20
_CONCURRENCY = 5               # parallel engine calls per bulk op (self-throttle)


def attach_extraction_truth(payload: dict, meta: dict | None) -> dict:
    """Copy backend-reported extraction truth onto a response item.

    This stays fail-closed: we only expose fields the backend actually sent
    (or the extractor helper derives directly from those exact fields), never
    mime-based guesses.
    """
    truth = extractor.classify_extraction(meta)
    for key in ("extraction_method", "image_ai_used", "ocr_used", "is_inferred",
                "is_partial", "text_quality", "noise_score"):
        payload[key] = truth.get(key)
    return payload


def clean_text(value: str | None) -> str:
    """Normalize extractor text for chat consumption without changing meaning.

    Keeps the payload cheap and readable for Webbee: trims null bytes and outer
    whitespace, normalises newlines, and collapses huge vertical gaps that only
    waste context. Does NOT rewrite words or punctuation.
    """
    if not isinstance(value, str):
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = text.strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def clean_preview(value: str | None) -> str | None:
    text = clean_text(value)
    return text or None


def clean_search_snippet(value: str | None) -> str:
    return clean_text(value)


def budget_share(n: int, floor: int) -> int:
    """Split RESPONSE_BUDGET_CHARS across n items, never below floor."""
    return max(floor, RESPONSE_BUDGET_CHARS // max(1, n))


def fit_text_budget(items: list[dict], text_key: str, floor: int) -> tuple[list[dict], int]:
    """Cap item count and per-item text so the total stays within
    RESPONSE_BUDGET_CHARS. Returns (possibly-shortened items, original count)
    so the caller can report has_more honestly instead of silently dropping."""
    total = len(items)
    max_items = max(1, RESPONSE_BUDGET_CHARS // floor)
    kept = items[:max_items]
    per = budget_share(len(kept), floor)
    for it in kept:
        text = it.get(text_key) or ""
        if len(text) > per:
            it[text_key] = text[:per] + f"… [{len(text)} chars total]"
    return kept, total
