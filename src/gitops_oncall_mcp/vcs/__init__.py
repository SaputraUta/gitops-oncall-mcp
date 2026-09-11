"""VCS adapters implementing a common interface."""

from .base import Commit, PipelineResult, PRResult, TagInfo, VCSAdapter

__all__ = ["VCSAdapter", "TagInfo", "Commit", "PipelineResult", "PRResult"]
