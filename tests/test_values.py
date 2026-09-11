"""Editing a values file must change one line and nothing else."""

from __future__ import annotations

import pytest

from gitops_oncall_mcp.values import read_service_tag, set_service_tag

VALUES = """# Procal production
global:
  tag: v0.0.0-never-touch-me

services:
  dora:
    tag: v1.0.4          # pinned by CI
    replicaCount: 2
    autoscaling:
      enabled: true
  boots:
    keda:
      queueLength: 5
    tag: v1.0.1
  maps:
    tag: v1.0.11
    replicaCount: 2
"""


def test_reads_the_tag_of_each_service():
    assert read_service_tag(VALUES, "dora") == "v1.0.4"
    assert read_service_tag(VALUES, "boots") == "v1.0.1"
    assert read_service_tag(VALUES, "maps") == "v1.0.11"


def test_sets_only_the_targeted_line():
    new, old = set_service_tag(VALUES, "maps", "v1.0.9")

    assert old == "v1.0.11"
    changed = [
        (a, b) for a, b in zip(VALUES.splitlines(), new.splitlines(), strict=True) if a != b
    ]
    assert changed == [("    tag: v1.0.11", "    tag: v1.0.9")]


def test_does_not_match_a_top_level_key_of_the_same_name():
    """`global.tag` sits above the services block and must never be touched."""
    new, _ = set_service_tag(VALUES, "dora", "v1.0.3")
    assert "tag: v0.0.0-never-touch-me" in new


def test_finds_the_tag_even_when_it_is_not_the_first_key():
    new, old = set_service_tag(VALUES, "boots", "v1.0.0")
    assert old == "v1.0.1"
    assert "    tag: v1.0.0\n" in new
    assert "queueLength: 5" in new


def test_refuses_an_unknown_service():
    with pytest.raises(ValueError, match="not found"):
        set_service_tag(VALUES, "nope", "v1.0.0")


def test_refuses_a_service_with_no_tag():
    text = "services:\n  dora:\n    replicaCount: 2\n  maps:\n    tag: v1.0.0\n"
    with pytest.raises(ValueError, match="no 'tag:' key"):
        set_service_tag(text, "dora", "v9.9.9")
