"""Topology icon upload validation (plan v3 §4.6).

Accepts PNG / SVG only, by magic bytes (not the filename), caps size, and
strips/rejects dangerous SVG. SVG is a known XSS vector, so defence is layered:
 1. reject DOCTYPE/ENTITY outright (blocks XXE + billion-laughs);
 2. reject <script>, event handlers, <foreignObject>, javascript:, external refs;
 3. the frontend renders icons via <img src=…>, in which SVG cannot execute script.

ponytail: this is a best-effort regex screen for a SUPERADMIN-only upload, not a
full sanitizer. If untrusted uploaders ever gain access, swap in a real sanitizer
(e.g. a vetted library) — the upgrade path, not needed for one trusted admin.
"""
from __future__ import annotations

import re

MAX_ICON_BYTES = 256 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# dangerous SVG constructs (case-insensitive, over decoded text)
_SVG_BAD = (
    re.compile(r"<\s*script", re.I),
    re.compile(r"<\s*foreignobject", re.I),
    re.compile(r"<\s*iframe", re.I),
    re.compile(r"\son\w+\s*=", re.I),                       # event handlers: onload=, onclick=…
    re.compile(r"javascript\s*:", re.I),
    re.compile(r"(?:xlink:)?href\s*=\s*['\"]?\s*(?:https?:|//|data:)", re.I),  # external/data refs
    re.compile(r"<!\s*doctype", re.I),                      # blocks XXE
    re.compile(r"<!\s*entity", re.I),                       # blocks entity-expansion bombs
)


class IconValidationError(ValueError):
    pass


def validate_icon(content: bytes) -> str:
    """Return the file extension ('png'|'svg') if valid, else raise IconValidationError."""
    if not content:
        raise IconValidationError("empty file")
    if len(content) > MAX_ICON_BYTES:
        raise IconValidationError(f"icon exceeds {MAX_ICON_BYTES // 1024} KB")
    if content[:8] == _PNG_MAGIC:
        return "png"
    head = content[:2048].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        text = content.decode("utf-8", "replace")
        for pat in _SVG_BAD:
            if pat.search(text):
                raise IconValidationError(f"SVG contains a disallowed construct ({pat.pattern})")
        return "svg"
    raise IconValidationError("only PNG and SVG icons are accepted")
