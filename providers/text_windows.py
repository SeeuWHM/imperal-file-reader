"""Line-numbered read windows and exact-match grep over already-extracted
text — same convention as Google Drive Connector's provider of the same
name (format-agnostic; nothing here is specific to any one file type)."""
from __future__ import annotations


def line_window(text: str, offset: int = 0, limit: int | None = None) -> tuple[str, bool, int]:
    """Return (numbered_text, has_more, total_lines). offset is 0-based line
    index into the file; limit is a line count. limit=None reads to the end."""
    lines = text.split("\n")
    start = max(0, offset)
    end = start + limit if limit else len(lines)
    window = lines[start:end]
    numbered = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(window))
    has_more = end < len(lines)
    return numbered, has_more, len(lines)


def grep_lines(text: str, query: str, case_sensitive: bool = False) -> list[tuple[int, str]]:
    """Return [(1-based line number, line content)] for every line containing query."""
    haystack_lines = text.split("\n")
    needle = query if case_sensitive else query.lower()
    matches = []
    for i, line in enumerate(haystack_lines):
        hay = line if case_sensitive else line.lower()
        if needle in hay:
            matches.append((i + 1, line))
    return matches
