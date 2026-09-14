"""Tool parameters stay plain types.

`str | None` renders as `anyOf: [string, null]`, which not every model client
handles. Use `param: str = ""` and treat empty as absent.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp import FastMCP

from gitops_oncall_mcp.tools import k8s as k8s_tools
from gitops_oncall_mcp.tools import senses as senses_tools


class _StubVCS:
    def list_tag_names(self):
        return []

    def describe_tags(self, names):
        return []

    def get_commit_diff(self, sha, max_chars=50_000):
        return ""

    def get_file_commits(self, path, limit=10):
        return []

    def get_file_content(self, path, ref="main"):
        return ""


def _all_tool_schemas(cfg) -> dict[str, dict]:
    mcp = FastMCP("schema-test")
    client = httpx.Client(base_url="http://unused.test")
    senses_tools.register(
        mcp,
        senses_tools.SensesCtx(
            prometheus=client, loki=client, alerts=None, cfg=cfg, vcs=_StubVCS()
        ),
    )
    k8s_tools.register(
        mcp, k8s_tools.K8sCtx(core=MagicMock(), custom=MagicMock(), cfg=cfg)
    )
    import asyncio

    return {t.name: t.parameters for t in asyncio.run(mcp.list_tools())}


def _nullable_params(schema: dict) -> list[str]:
    bad = []
    for name, spec in (schema.get("properties") or {}).items():
        options = spec.get("anyOf") or spec.get("oneOf") or []
        if any(o.get("type") == "null" for o in options) or spec.get("type") == "null":
            bad.append(name)
    return bad


def test_no_tool_takes_a_nullable_parameter(cfg):
    offenders = {
        tool: params
        for tool, schema in _all_tool_schemas(cfg).items()
        if (params := _nullable_params(schema))
    }
    assert not offenders, (
        f"nullable tool parameters hang the agent with no error: {offenders}. "
        'Use `param: str = ""` and treat empty as absent.'
    )


@pytest.mark.parametrize("tool", ["get_recent_deploys", "get_commit_diff", "get_argocd_apps"])
def test_the_optional_repo_and_name_params_are_plain_strings(cfg, tool):
    """These three carried the bug; keep them named so a revert is obvious."""
    schema = _all_tool_schemas(cfg)[tool]
    optional = {"get_recent_deploys": "repo", "get_commit_diff": "repo", "get_argocd_apps": "name"}
    spec = schema["properties"][optional[tool]]
    assert spec.get("type") == "string", f"{tool} regressed to {spec}"
    assert spec.get("default") == ""
