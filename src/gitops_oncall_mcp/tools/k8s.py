"""Read-only eyes on the cluster itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    
ARGO_GROUP = "argoproj.io"
ARGO_VERSION = "v1alpha1"


def _images(spec: dict) -> list[str]:
    """Container images as `name:tag`, dropping the registry host and path.

    The tag is the part an on-call reader needs; the registry prefix is noise
    that pushes the useful half out of a narrow terminal.
    """
    out = []
    for c in spec.get("template", {}).get("spec", {}).get("containers", []):
        image = c.get("image", "")
        out.append(image.rsplit("/", 1)[-1] if "/" in image else image)
    return out


def _age(ts: datetime | None) -> str:
    if ts is None:
        return "?"
    secs = int((datetime.now(UTC) - ts).total_seconds())
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"

def _parse(ts: str | None) -> datetime | None:
    """Kubernetes RFC3339 timestamp to datetime, tolerating a missing value."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


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
        cutoff = datetime.now(UTC) - timedelta(minutes=within_minutes)
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

    @mcp.tool
    def get_rollouts() -> list[dict]:
        """Argo Rollouts state for every rollout in the namespace.

        Returns [{"name", "images", "phase", "step", "canary_weight",
        "fully_rolled_out", "aborted", "message"}, ...].

        fully_rolled_out is False while a canary is in progress: the stable and
        current pod hashes differ, so some traffic is on the new version and
        some is not. aborted means the rollout refused to continue and traffic
        stayed on the previous version — Git will name a version the cluster is
        deliberately not serving, which is not drift. Call this to find out
        which version is actually live, or why a release stopped.
        """
        objs = ctx.custom.list_namespaced_custom_object(
            ARGO_GROUP, ARGO_VERSION, ns, "rollouts"
        )["items"]
        out = []
        for o in objs:
            st, spec = o.get("status", {}), o.get("spec", {})
            steps = spec.get("strategy", {}).get("canary", {}).get("steps", [])
            weights = st.get("canary", {}).get("weights", {})
            out.append(
                {
                    "name": o["metadata"]["name"],
                    "images": _images(spec),
                    "phase": st.get("phase", "?"),
                    "step": f"{st.get('currentStepIndex', '?')}/{len(steps)}" if steps else "n/a",
                    "canary_weight": weights.get("canary", {}).get("weight"),
                    "fully_rolled_out": bool(st.get("stableRS"))
                    and st.get("stableRS") == st.get("currentPodHash"),
                    "aborted": bool(st.get("abort", False)),
                    "message": st.get("message"),
                }
            )
        return out

    @mcp.tool
    def get_analysis_runs(rollout: str = "", limit: int = 10) -> list[dict]:
        """Verdicts from Argo Rollouts analysis, newest first.

        Args:
            rollout: Only runs for this rollout. Omit for all of them.
            limit: Max runs to return.

        Returns [{"name", "rollout", "phase", "age", "message", "metrics"}, ...].
        An analysis run is what decides whether a canary advances or aborts, so
        its message is the reason a release was refused. Call this after
        get_rollouts reports an abort or a Degraded phase.
        """
        objs = ctx.custom.list_namespaced_custom_object(
            ARGO_GROUP, ARGO_VERSION, ns, "analysisruns"
        )["items"]
        runs = []
        for o in objs:
            meta, st = o["metadata"], o.get("status", {})
            owner = next(
                (r["name"] for r in meta.get("ownerReferences", []) if r.get("kind") == "Rollout"),
                meta["name"].rsplit("-", 2)[0],
            )
            if rollout and owner != rollout:
                continue
            runs.append(
                {
                    "name": meta["name"],
                    "rollout": owner,
                    "phase": st.get("phase", "?"),
                    "age": _age(_parse(meta.get("creationTimestamp"))),
                    "created": meta.get("creationTimestamp", ""),
                    "message": st.get("message"),
                    "metrics": [
                        {
                            "name": m.get("name"),
                            "phase": m.get("phase"),
                            "successful": m.get("successful", 0),
                            "failed": m.get("failed", 0),
                        }
                        for m in st.get("metricResults", [])
                    ],
                }
            )
        runs.sort(key=lambda r: r["created"], reverse=True)
        for r in runs:
            del r["created"]
        return runs[:limit]

    @mcp.tool
    def get_argocd_apps(name: str = "") -> list[dict]:
        """Argo CD sync and health for the applications it manages.

        Args:
            name: One application. Omit for all of them.

        Returns [{"name", "sync", "health", "target_revision", "repo",
        "last_sync", "last_sync_message"}, ...].

        Synced means the cluster matches what Git asks for, which is not the
        same as the release having succeeded: a rollout can be aborted while its
        Application still reads Synced, because Argo CD compares the spec and
        the refusal lives in the rollout's status. Check get_rollouts too.
        """
        objs = ctx.custom.list_namespaced_custom_object(
            ARGO_GROUP, ARGO_VERSION, ctx.cfg.k8s.argocd_namespace, "applications"
        )["items"]
        out = []
        for o in objs:
            meta, spec, st = o["metadata"], o.get("spec", {}), o.get("status", {})
            if name and meta["name"] != name:
                continue
            sources = spec.get("sources") or ([spec["source"]] if "source" in spec else [])
            op = st.get("operationState", {})
            out.append(
                {
                    "name": meta["name"],
                    "sync": st.get("sync", {}).get("status", "?"),
                    "health": st.get("health", {}).get("status", "?"),
                    "target_revision": [s.get("targetRevision") for s in sources if s.get("targetRevision")],
                    "repo": [
                        s["repoURL"].rsplit("/", 1)[-1].removesuffix(".git")
                        for s in sources
                        if s.get("repoURL")
                    ],
                    "last_sync": op.get("finishedAt"),
                    "last_sync_message": op.get("message"),
                }
            )
        return out
