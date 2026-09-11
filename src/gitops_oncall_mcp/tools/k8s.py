"""Read-only eyes on the cluster itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from kubernetes import client as k8s

if TYPE_CHECKING:
    from fastmcp import FastMCP
    
    from ..config import Config
    
@dataclass
class K8sCtx:
    core: k8s.CoreV1Api
    custom: k8s.CustomObjectsApi
    cfg: Config
    
def _age(ts: datetime | None) -> str:
    if ts is None:
        return "?"
    secs = int((datetime.now(timezone.utc) - ts).total_seconds())
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"

def _trouble(pod) -> str | None:
    for cs in pod.status.container_statuses or []:
        state = cs.state
        if state.waiting and state.waiting.reason not in (None, "ContainerCreating"):
            return state.waiting.reason
        if state.terminated and state.terminated.reason not in (None, "Completed"):
            return state.terminated.reason
    return None

def register(mcp: FastMCP, ctx: K8sCtx) -> None:
    ns = ctx.cfg.k8s.namespace

    @mcp.tool
    def get_pods() -> list[dict]:
        """Pods in the configured namespace: phase, readiness, restarts, age.

        Returns [{"name", "phase", "ready", "restarts", "age", "trouble"}, ...].
        Call this first when asked what is broken, crashing, or restarting.
        """
        out = []
        for p in ctx.core.list_namespaced_pod(ns).items:
            statuses = p.status.container_statuses or []
            out.append(
                {
                    "name": p.metadata.name,
                    "phase": p.status.phase,
                    "ready": f"{sum(1 for c in statuses if c.ready)}/{len(statuses)}",
                    "restarts": sum(c.restart_count for c in statuses),
                    "age": _age(p.metadata.creation_timestamp),
                    "trouble": _trouble(p),
                }
            )
        return out