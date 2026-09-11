"""hands.py — verifies the propose→confirm flow via a stub VCS adapter."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastmcp import FastMCP

from gitops_oncall_mcp.approval import ProposalStore
from gitops_oncall_mcp.audit import AuditLog
from gitops_oncall_mcp.tools import hands
from gitops_oncall_mcp.vcs.base import PipelineResult, PRResult


class _StubVCS:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.next_pipeline = PipelineResult(
            pipeline_id="pip-1", build_number=42, url="https://example/42", state="PENDING"
        )
        self.next_pr = PRResult(pr_url="https://example/pr/1", pr_id=1, branch="ai-fix/x")
        self.fail = False

    # Senses unused here, stubbed:
    def list_tags(self, limit=50):
        return []

    def get_commit_diff(self, sha, max_chars=50_000):
        return ""

    def get_file_commits(self, path, limit=10):
        return []

    def trigger_deploy(self, env: str, ref: str):
        self.calls.append(("trigger_deploy", {"env": env, "ref": ref}))
        if self.fail:
            raise RuntimeError("vcs explosion")
        return self.next_pipeline

    def open_pr(self, **kwargs: Any):
        self.calls.append(("open_pr", kwargs))
        if self.fail:
            raise RuntimeError("pr explosion")
        return self.next_pr


def _make_ctx(cfg, audit_path=None):
    mcp = FastMCP("test")
    vcs = _StubVCS()
    proposals = ProposalStore(default_ttl_seconds=cfg.guardrails.proposal_ttl_seconds)
    audit = AuditLog(file_path=audit_path)
    ctx = hands.HandsCtx(cfg=cfg, vcs=vcs, proposals=proposals, audit=audit)
    hands.register(mcp, ctx)
    return mcp, ctx, vcs


def _get_tools(mcp: FastMCP):
    """Return dict of the bare callables registered on the FastMCP instance.

    FastMCP wraps each function in a FunctionTool whose `.fn` attribute is the
    original callable — handy for direct invocation in tests.
    """
    # Tools are stored in mcp._tools (private but stable in 3.x). Fall back
    # via the `list_tools` plumbing if that changes.
    try:
        return {t.name: t.fn for t in mcp._tools.values()}  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover
        import asyncio

        async def _gather():
            return await mcp.list_tools()

        return {t.name: t.fn for t in asyncio.run(_gather())}


# ─────────────────────────────────────────────────────────
# Happy paths
# ─────────────────────────────────────────────────────────


def test_propose_rollback_returns_id_no_side_effect(cfg):
    _, ctx, vcs = _make_ctx(cfg)
    tools = _get_tools(_)
    result = tools["propose_rollback"](env="staging", target_tag="v1.2.3-stag")
    assert result["proposal_id"]
    assert result["tool"] == "rollback_deploy"
    assert vcs.calls == []  # nothing executed yet
    # proposal exists in store
    assert ctx.proposals.peek(result["proposal_id"]) is not None


def test_confirm_rollback_executes_and_removes_proposal(cfg):
    _, ctx, vcs = _make_ctx(cfg)
    tools = _get_tools(_)
    p = tools["propose_rollback"](env="staging", target_tag="v1.2.3-stag")
    out = tools["confirm_rollback"](proposal_id=p["proposal_id"])
    assert out["pipeline_id"] == "pip-1"
    assert out["state"] == "PENDING"
    # VCS was called exactly once
    assert vcs.calls == [("trigger_deploy", {"env": "staging", "ref": "v1.2.3-stag"})]
    # proposal consumed
    assert ctx.proposals.peek(p["proposal_id"]) is None


def test_propose_then_confirm_pr_change(cfg):
    _, ctx, vcs = _make_ctx(cfg)
    tools = _get_tools(_)
    p = tools["propose_pr_change"](
        branch_name="ai-fix/x",
        file_path="src/a.py",
        new_content="print('hi')\n",
        title="Fix it",
        body="why",
    )
    assert p["content_bytes"] == len("print('hi')\n")
    out = tools["confirm_pr_change"](proposal_id=p["proposal_id"])
    assert out["pr_url"] == "https://example/pr/1"
    assert vcs.calls[0][0] == "open_pr"


# ─────────────────────────────────────────────────────────
# Sad paths — the safety properties
# ─────────────────────────────────────────────────────────


def test_propose_rollback_rejects_bad_tag(cfg):
    _, _ctx, vcs = _make_ctx(cfg)
    tools = _get_tools(_)
    with pytest.raises(ValueError, match="doesn't match"):
        tools["propose_rollback"](env="prod", target_tag="v1.2.3-stag")
    assert vcs.calls == []


def test_confirm_with_unknown_id_raises_and_no_side_effect(cfg):
    _, _ctx, vcs = _make_ctx(cfg)
    tools = _get_tools(_)
    with pytest.raises(KeyError):
        tools["confirm_rollback"](proposal_id="not-real")
    assert vcs.calls == []


def test_confirm_is_one_shot(cfg):
    _, _ctx, vcs = _make_ctx(cfg)
    tools = _get_tools(_)
    p = tools["propose_rollback"](env="staging", target_tag="v1.2.3-stag")
    tools["confirm_rollback"](proposal_id=p["proposal_id"])
    with pytest.raises(KeyError):
        tools["confirm_rollback"](proposal_id=p["proposal_id"])
    assert len(vcs.calls) == 1


def test_confirm_pr_id_cannot_execute_rollback(cfg):
    """Tool binding: a propose_pr_change id is not valid for confirm_rollback."""
    _, _ctx, vcs = _make_ctx(cfg)
    tools = _get_tools(_)
    p = tools["propose_pr_change"](
        branch_name="b",
        file_path="f",
        new_content="c",
        title="t",
        body="b",
    )
    with pytest.raises(ValueError, match="propose_fix_pr"):
        tools["confirm_rollback"](proposal_id=p["proposal_id"])
    assert vcs.calls == []


def test_proposal_expires(cfg):
    # Build a config with TTL=1s
    from dataclasses import replace

    short_cfg = replace(cfg, guardrails=replace(cfg.guardrails, proposal_ttl_seconds=1))
    _, _ctx, vcs = _make_ctx(short_cfg)
    tools = _get_tools(_)
    p = tools["propose_rollback"](env="staging", target_tag="v1.2.3-stag")
    time.sleep(1.1)
    with pytest.raises(KeyError):
        tools["confirm_rollback"](proposal_id=p["proposal_id"])
    assert vcs.calls == []


# ─────────────────────────────────────────────────────────
# Audit log
# ─────────────────────────────────────────────────────────


def test_audit_log_records_propose_and_confirm(cfg, tmp_path):
    log_file = tmp_path / "audit.jsonl"
    _, _ctx, vcs = _make_ctx(cfg, audit_path=str(log_file))
    tools = _get_tools(_)
    p = tools["propose_rollback"](env="staging", target_tag="v1.2.3-stag")
    tools["confirm_rollback"](proposal_id=p["proposal_id"])

    events = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    types = [e["event"] for e in events]
    assert "proposal_created" in types
    assert "proposal_consumed" in types
    assert "action_executed" in types


def test_audit_log_records_action_failure(cfg, tmp_path):
    log_file = tmp_path / "audit.jsonl"
    _, _ctx, vcs = _make_ctx(cfg, audit_path=str(log_file))
    vcs.fail = True
    tools = _get_tools(_)
    p = tools["propose_rollback"](env="staging", target_tag="v1.2.3-stag")
    with pytest.raises(RuntimeError, match="explosion"):
        tools["confirm_rollback"](proposal_id=p["proposal_id"])
    events = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
    types = [e["event"] for e in events]
    assert "action_failed" in types
    # And no action_executed
    assert "action_executed" not in types
