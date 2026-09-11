"""Proposal store contract: create, consume, expire, one-shot, tool-binding."""

from __future__ import annotations

import time

import pytest

from gitops_oncall_mcp.approval import ProposalStore


def test_create_returns_id_and_stores_payload():
    store = ProposalStore(default_ttl_seconds=60)
    p = store.create(tool="rollback_deploy", payload={"env": "staging", "target_tag": "v1-stag"})
    assert p.proposal_id
    assert p.tool == "rollback_deploy"
    assert p.payload == {"env": "staging", "target_tag": "v1-stag"}


def test_consume_returns_proposal_and_removes_it():
    store = ProposalStore()
    p = store.create("rollback_deploy", {"env": "staging", "target_tag": "v1-stag"})
    got = store.consume(p.proposal_id, expected_tool="rollback_deploy")
    assert got.proposal_id == p.proposal_id
    # Second consume must fail — one-shot semantics
    with pytest.raises(KeyError):
        store.consume(p.proposal_id, expected_tool="rollback_deploy")


def test_consume_wrong_tool_rejected():
    """A proposal_id created for rollback can't be used to confirm a PR."""
    store = ProposalStore()
    p = store.create("rollback_deploy", {"env": "staging", "target_tag": "v1-stag"})
    with pytest.raises(ValueError, match="rollback_deploy"):
        store.consume(p.proposal_id, expected_tool="propose_fix_pr")


def test_consume_unknown_id_rejected():
    store = ProposalStore()
    with pytest.raises(KeyError):
        store.consume("not-a-real-id", expected_tool="rollback_deploy")


def test_consume_expired_rejected():
    store = ProposalStore(default_ttl_seconds=60)
    p = store.create("rollback_deploy", {"env": "x", "target_tag": "y"}, ttl_seconds=1)
    time.sleep(1.1)
    with pytest.raises(KeyError):
        # Sweep on access already removed it
        store.consume(p.proposal_id, expected_tool="rollback_deploy")


def test_peek_does_not_consume():
    store = ProposalStore()
    p = store.create("rollback_deploy", {"env": "x", "target_tag": "y"})
    assert store.peek(p.proposal_id) is not None
    # Still consumable
    store.consume(p.proposal_id, expected_tool="rollback_deploy")


def test_ids_are_unique():
    store = ProposalStore()
    ids = {store.create("t", {}).proposal_id for _ in range(50)}
    assert len(ids) == 50
