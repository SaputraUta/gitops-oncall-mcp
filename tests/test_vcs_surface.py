"""Every adapter must implement the whole VCSAdapter protocol.

`get_commit_diff` was deleted by an unrelated edit and the entire suite stayed
green, because nothing asserted the adapter's method surface. The tool that
depended on it failed only in production, as a bare AttributeError relayed to
the user as "the tool encountered an error".
"""

from __future__ import annotations

import pytest

from gitops_oncall_mcp.config import GitHubConfig
from gitops_oncall_mcp.vcs.base import VCSAdapter
from gitops_oncall_mcp.vcs.github import GitHubAdapter

_PROTOCOL_METHODS = sorted(
    name
    for name in dir(VCSAdapter)
    if not name.startswith("_") and callable(getattr(VCSAdapter, name))
)


@pytest.fixture
def adapter() -> GitHubAdapter:
    return GitHubAdapter(
        GitHubConfig(
            token="t",
            owner="o",
            repo="r",
            deploy_workflow="deploy.yml",
            api_base="https://api.github.test",
        )
    )


def test_the_protocol_is_not_empty():
    """Guards the guard: a broken introspection would make the next test vacuous."""
    assert len(_PROTOCOL_METHODS) >= 6, _PROTOCOL_METHODS


@pytest.mark.parametrize("method", _PROTOCOL_METHODS)
def test_github_adapter_implements(adapter, method):
    assert callable(getattr(adapter, method, None)), (
        f"GitHubAdapter is missing {method}(); the tool that calls it will fail "
        "at runtime with AttributeError and nothing else will notice"
    )
