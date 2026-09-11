"""Helpers shared across VCS adapters."""

from __future__ import annotations

import re
from typing import Any

_SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(.*)$")


def semver_sort_key(tag_name: str) -> tuple[int, int, int, str, str]:
    """Sort key for semver-ish tag names so `sorted(..., reverse=True)` yields
    newest version first — regardless of the underlying commit's date.

    Examples (descending):
        v0.0.40-stag → (0, 0, 40, "-stag", "v0.0.40-stag")
        v0.0.39-stag → (0, 0, 39, "-stag", "v0.0.39-stag")
        v0.0.10      → (0, 0, 10, "",      "v0.0.10")
        v0.0.9       → (0, 0,  9, "",      "v0.0.9")

    Non-semver tags get sorted (0, 0, 0, name, name) — they fall to the end.
    """
    m = _SEMVER_RE.match(tag_name)
    if not m:
        return (0, 0, 0, tag_name, tag_name)
    major, minor, patch, suffix = m.groups()
    return (int(major), int(minor), int(patch), suffix, tag_name)


def sort_tags_newest_first(items: list[Any], *, key: str = "tag") -> list[Any]:
    """Sort dataclass items by their tag-name field, newest version first."""
    return sorted(items, key=lambda t: semver_sort_key(getattr(t, key)), reverse=True)
