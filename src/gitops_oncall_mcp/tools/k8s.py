"""Read-only eyes on the cluster itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

def _stamp(e) -> datetime | None:
    return e.last_timestamp or e.event_time

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

    @mcp.tool
    def get_events(warnings_only: bool = True, limit: int = 30, within_minutes: int = 60) -> list[dict]:
        """Recent Kubernetes events in the namespace, newest first.

        Events are historical. An event from hours ago may already be resolved —
        confirm current state with get_pods before reporting it as live.

        Args:
            warnings_only: Skip Normal events, which are mostly scheduling noise.
            limit: Max events to return.
            within_minutes: Only events newer than this. Widen it to investigate
                a past incident; keep the default when triaging a live one.

        Returns [{"time", "type", "reason", "object", "message", "count"}, ...].
        Call this after get_pods to find out WHY a pod is unhealthy.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
        events = ctx.core.list_namespaced_event(ns).items
        if warnings_only:
            events = [e for e in events if e.type == "Warning"]
        events = [e for e in events if (s := _stamp(e)) and s >= cutoff]
        events.sort(key=_stamp, reverse=True)
        return [
            {
                "time": _age(_stamp(e)),
                "type": e.type,
                "reason": e.reason,
                "object": f"{e.involved_object.kind}/{e.involved_object.name}",
                "message": e.message,
                "count": e.count or 1,
            }
            for e in events[:limit]
        ]