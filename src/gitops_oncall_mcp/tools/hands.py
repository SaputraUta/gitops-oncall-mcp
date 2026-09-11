"""Destructive actions, split into propose / confirm pairs.

Every destructive tool is split in two:
  - `propose_*` validates inputs, creates an audit-logged proposal, returns
    `proposal_id` (no side effects).
  - `confirm_*` consumes the id and executes. One-shot. Expired after
    `PROPOSAL_TTL_SECONDS` (default 60s).

This forces TWO deliberate tool calls — even if an LLM loose-interprets
a user reply as approval, it cannot accidentally fire a destructive action
without a fresh proposal_id from a separate, user-initiated propose_* call.

Audit events:
  proposal_created    — propose_* succeeded
  proposal_consumed   — confirm_* consumed a proposal (about to execute)
  action_executed     — execution succeeded; payload includes result
  action_failed       — execution raised; payload includes the error string
  proposal_rejected   — confirm_* called with bad/expired/wrong-tool id
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..approval import ProposalStore
from ..audit import AuditLog


def _iso_utc(epoch_seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))

if TYPE_CHECKING:  # pragma: no cover
    from fastmcp import FastMCP

    from ..config import Config
    from ..vcs.base import VCSAdapter


@dataclass
class HandsCtx:
    cfg: Config
    vcs: VCSAdapter
    proposals: ProposalStore
    audit: AuditLog


# Tool names used for both proposal.tool and audit event scoping
_T_ROLLBACK = "rollback_deploy"
_T_PR = "propose_fix_pr"


def register(mcp: FastMCP, ctx: HandsCtx) -> None:
    rules = ctx.cfg.deploy_tags
    ttl = ctx.cfg.guardrails.proposal_ttl_seconds

    # ─────────────────────────────────────────────────────────
    # rollback_deploy — propose + confirm
    # ─────────────────────────────────────────────────────────

    def propose_rollback(env: str, target_tag: str) -> dict:
        """Propose a rollback. Returns a short-lived `proposal_id`.

        Does NOT execute. Validates inputs, audit-logs the intent, returns
        the id. Call `confirm_rollback(proposal_id)` within the TTL window
        (default 600s / 10 min) to execute.

        Args:
            env: Environment to roll back ('prod', 'staging', 'dev', ...).
                 The tag must match this env's naming convention.
            target_tag: Existing tag name, e.g. 'v1.2.3' or 'v1.2.3-stag'.

        Returns {"proposal_id", "expires_in_seconds", "expires_at_utc",
                 "tool", "env", "target_tag"}.

        When relaying this to a human (chat/Telegram), ALWAYS include the
        `expires_at_utc` so they know the deadline. Example:
            "Proposed rollback of staging to v1.2.3-stag.
             Expires at 2026-06-06T14:30:00Z. Reply yes to confirm."
        """
        if not rules.matches(env, target_tag):
            ctx.audit.emit(
                "proposal_rejected",
                tool=_T_ROLLBACK,
                reason="tag_env_mismatch",
                env=env,
                target_tag=target_tag,
            )
            raise ValueError(
                f"tag {target_tag!r} doesn't match the naming convention for env={env!r}"
            )
        p = ctx.proposals.create(
            tool=_T_ROLLBACK,
            payload={"env": env, "target_tag": target_tag},
            ttl_seconds=ttl,
        )
        ctx.audit.emit(
            "proposal_created",
            tool=_T_ROLLBACK,
            proposal_id=p.proposal_id,
            args=p.payload,
            expires_in_s=p.ttl_seconds,
        )
        return {
            "proposal_id": p.proposal_id,
            "expires_in_seconds": p.ttl_seconds,
            "expires_at_utc": _iso_utc(p.expires_at()),
            "tool": _T_ROLLBACK,
            "env": env,
            "target_tag": target_tag,
        }

    def confirm_rollback(proposal_id: str) -> dict:
        """Execute a previously-proposed rollback.

        Args:
            proposal_id: The id returned by propose_rollback.

        Returns the pipeline result dict. Raises if the id is unknown,
        expired, or was created for a different tool.
        This is a destructive, one-shot action.
        """
        try:
            p = ctx.proposals.consume(proposal_id, expected_tool=_T_ROLLBACK)
        except (KeyError, TimeoutError, ValueError) as e:
            ctx.audit.emit(
                "proposal_rejected",
                tool=_T_ROLLBACK,
                proposal_id=proposal_id,
                reason=type(e).__name__,
                error=str(e),
            )
            raise

        ctx.audit.emit(
            "proposal_consumed",
            tool=_T_ROLLBACK,
            proposal_id=proposal_id,
            args=p.payload,
        )
        try:
            result = ctx.vcs.trigger_deploy(
                env=p.payload["env"], ref=p.payload["target_tag"]
            )
        except Exception as e:
            ctx.audit.emit(
                "action_failed",
                tool=_T_ROLLBACK,
                proposal_id=proposal_id,
                error=str(e),
            )
            raise

        out = {
            "pipeline_id": result.pipeline_id,
            "build_number": result.build_number,
            "url": result.url,
            "state": result.state,
        }
        ctx.audit.emit(
            "action_executed",
            tool=_T_ROLLBACK,
            proposal_id=proposal_id,
            result=out,
        )
        return out

    # ─────────────────────────────────────────────────────────
    # propose_fix_pr — propose + confirm
    # ─────────────────────────────────────────────────────────

    def propose_pr_change(
        branch_name: str,
        file_path: str,
        new_content: str,
        title: str,
        body: str,
        base_branch: str = "main",
    ) -> dict:
        """Propose a single-file PR. Returns a short-lived `proposal_id`.

        Does NOT open the PR. Records the intent + file size in the audit
        log, returns the id. Call `confirm_pr_change(proposal_id)` within
        the TTL window (default 600s / 10 min) to actually open the branch
        + commit + PR.

        When relaying to a human, ALWAYS include the `expires_at_utc` from
        the response so they know the deadline.

        Args:
            branch_name: New branch name, e.g. 'ai-fix/null-form-1717'.
            file_path: Path within the repo, e.g. 'internal/handlers/form.go'.
            new_content: Complete new file content.
            title: PR title.
            body: PR description (markdown ok).
            base_branch: Branch to branch from + open PR against (default 'main').

        Returns {"proposal_id", "expires_in_seconds", "tool", "branch_name",
                 "file_path", "title", "content_bytes"}.
        """
        payload = {
            "branch_name": branch_name,
            "file_path": file_path,
            "new_content": new_content,
            "title": title,
            "body": body,
            "base_branch": base_branch,
        }
        p = ctx.proposals.create(tool=_T_PR, payload=payload, ttl_seconds=ttl)
        ctx.audit.emit(
            "proposal_created",
            tool=_T_PR,
            proposal_id=p.proposal_id,
            # Don't dump full content into audit — it can be huge. Summarize.
            args={
                "branch_name": branch_name,
                "file_path": file_path,
                "title": title,
                "base_branch": base_branch,
                "content_bytes": len(new_content),
            },
            expires_in_s=p.ttl_seconds,
        )
        return {
            "proposal_id": p.proposal_id,
            "expires_in_seconds": p.ttl_seconds,
            "expires_at_utc": _iso_utc(p.expires_at()),
            "tool": _T_PR,
            "branch_name": branch_name,
            "file_path": file_path,
            "title": title,
            "content_bytes": len(new_content),
        }

    def confirm_pr_change(proposal_id: str) -> dict:
        """Execute a previously-proposed single-file PR.

        Args:
            proposal_id: The id returned by propose_pr_change.

        Returns {"pr_url", "pr_id", "branch"}.
        Destructive, one-shot.
        """
        try:
            p = ctx.proposals.consume(proposal_id, expected_tool=_T_PR)
        except (KeyError, TimeoutError, ValueError) as e:
            ctx.audit.emit(
                "proposal_rejected",
                tool=_T_PR,
                proposal_id=proposal_id,
                reason=type(e).__name__,
                error=str(e),
            )
            raise

        ctx.audit.emit(
            "proposal_consumed",
            tool=_T_PR,
            proposal_id=proposal_id,
            args={
                "branch_name": p.payload["branch_name"],
                "file_path": p.payload["file_path"],
                "title": p.payload["title"],
                "content_bytes": len(p.payload["new_content"]),
            },
        )
        try:
            result = ctx.vcs.open_pr(**p.payload)
        except Exception as e:
            ctx.audit.emit(
                "action_failed",
                tool=_T_PR,
                proposal_id=proposal_id,
                error=str(e),
            )
            raise

        out = {"pr_url": result.pr_url, "pr_id": result.pr_id, "branch": result.branch}
        ctx.audit.emit(
            "action_executed",
            tool=_T_PR,
            proposal_id=proposal_id,
            result=out,
        )
        return out

    for fn in (propose_rollback, confirm_rollback, propose_pr_change, confirm_pr_change):
        mcp.tool(fn)
