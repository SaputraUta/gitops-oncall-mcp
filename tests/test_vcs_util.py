"""Semver-aware tag sort — the fix for the 're-tag at older commit' bug."""

from __future__ import annotations

from gitops_oncall_mcp.vcs.base import TagInfo
from gitops_oncall_mcp.vcs.util import semver_sort_key, sort_tags_newest_first


def test_semver_key_orders_versions_numerically():
    # The classic broken-with-lexical-sort case: 10 should be greater than 9
    keys = sorted(["v0.0.9", "v0.0.10", "v0.0.2"], key=semver_sort_key, reverse=True)
    assert keys == ["v0.0.10", "v0.0.9", "v0.0.2"]


def test_semver_key_groups_by_env_suffix():
    keys = sorted(
        ["v0.0.40-stag", "v0.0.39-stag", "v0.0.40-dev", "v0.0.39"],
        key=semver_sort_key,
        reverse=True,
    )
    # 40 beats 39 regardless of suffix; same-version tags grouped by suffix
    assert keys[0].startswith("v0.0.40")
    assert keys[-1] == "v0.0.39"


def test_sort_tags_newest_first_uses_name_not_commit_date():
    """
    Regression: previously we sorted by commit date, which gave the wrong
    answer whenever a new tag was created at an older commit (re-deploy of
    a prior version). Tag name (semver) is the right signal.
    """
    tags = [
        # NEW tag pointing at an OLD commit (re-deploy scenario)
        TagInfo(tag="v0.0.40-stag", sha="aaa", date="2026-06-01T00:00:00Z", message=""),
        # OLDER tag at a NEWER commit
        TagInfo(tag="v0.0.39-stag", sha="bbb", date="2026-06-02T00:00:00Z", message=""),
        TagInfo(tag="v0.0.38-stag", sha="ccc", date="2026-06-01T12:00:00Z", message=""),
    ]
    sorted_tags = sort_tags_newest_first(tags)
    assert [t.tag for t in sorted_tags] == ["v0.0.40-stag", "v0.0.39-stag", "v0.0.38-stag"]


def test_non_semver_tags_fall_to_the_end():
    tags = [
        TagInfo(tag="hotfix-2024-06", sha="x", date="", message=""),
        TagInfo(tag="v1.2.3", sha="y", date="", message=""),
        TagInfo(tag="release-candidate", sha="z", date="", message=""),
    ]
    sorted_tags = sort_tags_newest_first(tags)
    assert sorted_tags[0].tag == "v1.2.3"  # only valid semver
