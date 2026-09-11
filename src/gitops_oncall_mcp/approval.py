"""In-memory proposal store for the two-step approval pattern.

Destructive tools are split into a `propose_*` step (returns a short-lived
`proposal_id`) and a `confirm_*` step (requires that id). This forces a
deliberate second call before anything destructive runs — even if the LLM
loose-interprets a user's response as approval.

Proposals expire after `ttl_seconds` (default 60s). Expired proposals are
swept lazily on access. Storage is in-memory: a server restart invalidates
all open proposals. That's a feature, not a bug — restarts are rare and
"forgetting" pending destructive actions is the safe default.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class Proposal:
    proposal_id: str
    tool: str  # e.g. "rollback_deploy"
    payload: dict[str, Any]  # args to execute on confirm
    created_at: float
    ttl_seconds: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def expires_at(self) -> float:
        return self.created_at + self.ttl_seconds

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at()


class ProposalStore:
    """Thread-safe in-memory store with TTL and one-shot consumption."""

    def __init__(self, default_ttl_seconds: int = 60):
        self._items: dict[str, Proposal] = {}
        self._lock = Lock()
        self._default_ttl = default_ttl_seconds

    def create(
        self,
        tool: str,
        payload: dict[str, Any],
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Proposal:
        """Make a new proposal. Returns the full record (caller will surface the id)."""
        proposal_id = secrets.token_urlsafe(12)  # ~16 chars, unguessable
        p = Proposal(
            proposal_id=proposal_id,
            tool=tool,
            payload=dict(payload),
            created_at=time.time(),
            ttl_seconds=ttl_seconds if ttl_seconds is not None else self._default_ttl,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._sweep_locked()
            self._items[proposal_id] = p
        return p

    def consume(self, proposal_id: str, expected_tool: str) -> Proposal:
        """Pop and return the proposal. Raises if unknown, expired, or wrong tool.

        One-shot: a confirmed proposal can't be replayed.
        """
        with self._lock:
            self._sweep_locked()
            p = self._items.pop(proposal_id, None)
        if p is None:
            raise KeyError(f"proposal {proposal_id!r} not found or already consumed")
        if p.is_expired():
            # Already swept usually, but belt-and-suspenders
            raise TimeoutError(f"proposal {proposal_id!r} has expired")
        if p.tool != expected_tool:
            # Don't let a propose_rollback id be used to confirm a propose_pr_change
            raise ValueError(
                f"proposal {proposal_id!r} was created for {p.tool!r}, "
                f"not {expected_tool!r}"
            )
        return p

    def peek(self, proposal_id: str) -> Proposal | None:
        """Read without consuming. For tests / introspection."""
        with self._lock:
            self._sweep_locked()
            return self._items.get(proposal_id)

    def _sweep_locked(self) -> list[str]:
        """Remove expired entries. Caller must hold `_lock`. Returns swept ids."""
        now = time.time()
        expired = [pid for pid, p in self._items.items() if p.is_expired(now)]
        for pid in expired:
            del self._items[pid]
        return expired
