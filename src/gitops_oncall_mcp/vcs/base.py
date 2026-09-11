"""VCS adapter interface. GitHub is the only backend today."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TagInfo:
    tag: str
    sha: str
    date: str
    message: str


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    message: str


@dataclass(frozen=True)
class PipelineResult:
    pipeline_id: str
    build_number: int | None
    url: str
    state: str


@dataclass(frozen=True)
class PRResult:
    pr_url: str
    pr_id: int
    branch: str


class VCSAdapter(Protocol):
    """Common interface every VCS backend must implement.

    Adapters are constructed with their provider-specific config and live
    for the duration of the server process.
    """

    def list_tags(self, limit: int = 50) -> list[TagInfo]:
        """Most recent tags, newest first."""
        ...

    def get_commit_diff(self, sha: str, max_chars: int = 50_000) -> str:
        """Unified diff of a single commit, truncated."""
        ...

    def get_file_content(self, path: str, ref: str = "main") -> str:
        """Raw text of one file at a branch, tag or commit."""
        ...

    def get_file_commits(self, path: str, limit: int = 10) -> list[Commit]:
        """Recent commits that touched a specific file path."""
        ...

    def trigger_deploy(self, env: str, ref: str) -> PipelineResult:
        """Run the deploy pipeline/workflow against the given ref (tag/branch)."""
        ...

    def open_pr(
        self,
        branch_name: str,
        file_path: str,
        new_content: str,
        title: str,
        body: str,
        base_branch: str = "main",
    ) -> PRResult:
        """Create a branch off `base_branch`, commit a single-file change, open a PR."""
        ...
