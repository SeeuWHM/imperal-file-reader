"""Shared response-shaping helpers for the CONTENT plane (read_files /
file_overview / file_preview / search_files) — text cleanup, backend
extraction-truth passthrough, and the response-size budgeting that keeps
every reply comfortably under the kernel's serialization ceiling.

Split out of content_ops.py (2026-07-10) to keep that module focused on
orchestration — these are pure helpers with no control flow of their own.
"""
from __future__ import annotations

import re

from . import extractor

# Kernel ceiling (verified 2026-07-06, RE-CONFIRMED LIVE 2026-07-10): Webby's
# agentic tool loop serializes a whole tool result to one string and hard-cuts
# it at ~10_000 chars, mid-JSON, with no awareness of fields or list items
# (orchestration/agentic/loop.py, _tool_result_content_str / _TOOL_RESULT_MAX).
# A response landing near that edge arrives corrupted, not just short — Webby
# then can't trust ANY of it. Live proof 2026-07-10: extractor.read_text's own
# log confirmed the engine returned a full text_len=8000 payload for a real
# read, but Webby reported an empty body 17s later — the previous 8_000 budget
# was NOT leaving enough room once this module's own extraction-truth/quality
# fields (extraction_method, image_ai_used, ocr_used, is_partial, text_quality,
# noise_score — ~200-400 chars of wrapper per item) are added on top of the
# text itself. Cut hard to 4_000 for real headroom instead of nibbling at the
# edge again.
RESPONSE_BUDGET_CHARS = 4_000
_MIN_PER_FILE = 250             # floor per file when read_files batches many at once
_MIN_PER_HIT = 200              # floor per search hit when there are many

DEFAULT_READ_LIMIT = RESPONSE_BUDGET_CHARS   # chars for a single-file read window
MAX_READ_LIMIT = RESPONSE_BUDGET_CHARS       # hard ceiling per file even on an explicit ask —
                                              # nothing bigger survives the kernel intact anyway
FULLTEXT_LIMIT = 5_000_000     # engine cap for exact in-file grep
DEFAULT_SEARCH_K = 6
MAX_SEARCH_K = 20
_CONCURRENCY = 5               # parallel engine calls per bulk op (self-throttle)

_LETTERSPACED_WORD_RE = re.compile(r"(?:\b[A-Za-zА-Яа-яЁё]\s){3,}[A-Za-zА-Яа-яЁё]\b")
_SYMBOL_HEAVY_LINE_RE = re.compile(r"^[\W_\-–—=+|/\\]{4,}$")


def _squash_letterspaced_words(text: str) -> str:
    """Collapse junk like 'E N O f f i c i a l J o u r n a l' into normal words.

    This is a cheap LLM-cleanup heuristic for broken PDF text layers. It is
    intentionally conservative: only long runs of single letters separated by
    spaces are joined, leaving ordinary prose untouched.
    """
    prev = None
    cur = text
    for _ in range(3):
        if cur == prev:
            break
        prev = cur
        cur = _LETTERSPACED_WORD_RE.sub(lambda m: m.group(0).replace(" ", ""), cur)
    return cur


def _looks_like_low_value_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _SYMBOL_HEAVY_LINE_RE.match(stripped):
        return True
    alnum = sum(ch.isalnum() for ch in stripped)
    if alnum == 0:
        return True
    symbol_ratio = sum(not ch.isalnum() and not ch.isspace() for ch in stripped) / max(1, len(stripped))
    if len(stripped) <= 8 and symbol_ratio >= 0.5:
        return True
    return False


def _trim_noisy_lead(text: str) -> str:
    """Drop only the obviously junk opening lines before the first useful block.

    We keep this narrowly scoped to the *leading* fragment so image captions and
    OCR bodies survive intact, while navigation junk / broken OCR lead-ins stop
    wasting tokens at the very top of previews and reads.
    """
    lines = text.split("\n")
    kept: list[str] = []
    started = False
    for line in lines:
        stripped = line.strip()
        if not started:
            if not stripped:
                continue
            if _looks_like_low_value_line(stripped):
                continue
            if len(stripped) < 3:
                continue
            started = True
        kept.append(line)
    return "\n".join(kept).strip()


def attach_extraction_truth(payload: dict, meta: dict | None) -> dict:
    """Copy backend-reported extraction truth onto a response item.

    This stays fail-closed: we only expose fields the backend actually sent
    (or the extractor helper derives directly from those exact fields), never
    mime-based guesses.
    """
    truth = extractor.classify_extraction(meta)
    for key in ("extraction_method", "image_ai_used", "ocr_used",
                "is_partial", "text_quality", "noise_score"):
        payload[key] = truth.get(key)
    return payload


def clean_text(value: str | None) -> str:
    """Normalize extractor text for chat consumption without changing meaning.

    Keeps the payload cheap and readable for Webbee: trims null bytes and outer
    whitespace, normalises newlines, and collapses huge vertical gaps that only
    waste context. It also strips the common AI-vision trailing recap paragraph
    ("The document/image ...") when the file already yielded substantial raw
    text above it: that recap burns tokens, duplicates the evidence, and makes
    exact search less faithful. We keep such prose when it is the ONLY thing we
    have (e.g. a real photo with no text), so image understanding still works.
    """
    if not isinstance(value, str):
        return ""
    text = value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _squash_letterspaced_words(text)
    text = text.strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    text = _trim_noisy_lead(text)

    marker_pos = -1
    for marker in ("\n\nThe document ", "\n\nThis document ", "\n\nThe image ", "\n\nThis image "):
        pos = text.find(marker)
        if pos != -1 and (marker_pos == -1 or pos < marker_pos):
            marker_pos = pos
    if marker_pos != -1:
        before = text[:marker_pos].rstrip()
        after = text[marker_pos + 2 :].strip()
        if before:
            raw_lines = [line.strip() for line in before.split("\n") if line.strip()]
            strong_raw_signal = sum(
                1 for line in raw_lines
                if any(ch.isdigit() for ch in line) or any(ch in ":/@|" for ch in line)
            )
            if len(raw_lines) >= 8 or strong_raw_signal >= 3:
                recap_words = len(after.split())
                body_words = len(before.split())
                if recap_words <= max(120, body_words // 2):
                    text = before
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
    so the caller can report has_more honestly instead of silently dropping.

    For search hits and previews we cut at a natural line boundary when we can,
    because a mid-word hard cut wastes tokens and makes exact/file triage less
    legible than it needs to be.
    """
    total = len(items)
    max_items = max(1, RESPONSE_BUDGET_CHARS // floor)
    kept = items[:max_items]
    per = budget_share(len(kept), floor)
    suffix_tpl = "… [{n} chars total]"
    for it in kept:
        text = it.get(text_key) or ""
        if len(text) <= per:
            continue
        reserve = len(suffix_tpl.format(n=len(text)))
        hard_cap = max(1, per - reserve)
        candidate = text[:hard_cap]
        cut = candidate.rfind("\n")
        if cut >= max(40, hard_cap // 2):
            candidate = candidate[:cut].rstrip()
        else:
            candidate = candidate.rstrip()
        it[text_key] = candidate + suffix_tpl.format(n=len(text))
    return kept, total
