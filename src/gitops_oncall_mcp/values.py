"""Surgical edits to a Helm values file.

Deliberately line-based rather than a YAML round-trip. Loading and re-dumping
would reformat the whole file, drop comments and reorder keys, turning a
one-line rollback into a diff no reviewer can read. The proposal is meant to be
approved by a human, so the diff has to stay legible.
"""

from __future__ import annotations

import re

_KEY = re.compile(r"^(\s*)([\w.-]+):\s*$")
# A trailing comment is common on a pinned tag and must survive the edit.
_TAG = re.compile(r"^(\s*)tag:\s*(\S+)([ \t]*(?:#.*)?)$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _find_tag_line(lines: list[str], service: str) -> tuple[int, str, str]:
    """Index, current value, and trailing comment of `service`'s tag line.

    Raises ValueError when the service or its tag is absent, rather than
    guessing: a rollback that edits the wrong line is worse than one that
    refuses to run.
    """
    for i, line in enumerate(lines):
        m = _KEY.match(line)
        if not m or m.group(2) != service:
            continue
        block_indent = len(m.group(1))
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt.strip():
                continue
            if _indent(nxt) <= block_indent:
                break  # left the block without finding a tag
            t = _TAG.match(nxt)
            if t:
                return j, t.group(2), t.group(3)
        raise ValueError(f"service {service!r} has no 'tag:' key")
    raise ValueError(f"service {service!r} not found")


def read_service_tag(text: str, service: str) -> str:
    """The tag currently pinned for `service`."""
    _, tag, _ = _find_tag_line(text.splitlines(), service)
    return tag


def set_service_tag(text: str, service: str, tag: str) -> tuple[str, str]:
    """Return (new_text, previous_tag), changing exactly one line."""
    lines = text.splitlines(keepends=True)
    idx, old, comment = _find_tag_line([ln.rstrip("\n") for ln in lines], service)
    indent = _indent(lines[idx])
    newline = "\n" if lines[idx].endswith("\n") else ""
    lines[idx] = f"{' ' * indent}tag: {tag}{comment}{newline}"
    return "".join(lines), old
