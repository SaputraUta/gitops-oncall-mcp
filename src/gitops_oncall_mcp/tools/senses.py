"""Read-only senses: metrics, logs, alerts, VCS history."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:  # pragma: no cover
    from fastmcp import FastMCP

    from ..config import Config
    from ..vcs.base import VCSAdapter


@dataclass
class SensesCtx:
    prometheus: httpx.Client
    loki: httpx.Client
    alerts: httpx.Client | None
    cfg: Config
    vcs: VCSAdapter


def _promql(ctx: SensesCtx, q: str) -> list[dict]:
    r = ctx.prometheus.get("/api/v1/query", params={"query": q})
    r.raise_for_status()
    return r.json().get("data", {}).get("result", [])


def _scalar_or_none(result: list[dict], precision: int) -> float | None:
    """Round the first sample, or None when the window held no data.

    None and 0.0 are different answers: 0.0 means measured and zero, None means
    nothing was scraped in the window. Collapsing them hides an outage as health.
    """
    if not result:
        return None
    val = float(result[0]["value"][1])
    if math.isnan(val) or math.isinf(val):
        return None
    return round(val, precision)


def register(mcp: FastMCP, ctx: SensesCtx) -> None:
    labels = ctx.cfg.labels

    def ping() -> str:
        """Health check. Returns 'ok'."""
        return "ok"

    def get_cpu_usage(env: str, minutes: int = 5) -> list[dict]:
        """CPU used per pod, against that pod's own limit.

        Args:
            env: Environment name.
            minutes: Averaging window for the rate.

        Returns [{"pod", "cores", "limit_cores", "percent_of_limit"}, ...].
        percent_of_limit is None when the pod declares no CPU limit, which means
        it can burst freely and the raw core count is the only signal.
        Call this when asked about CPU load, throttling, or which pod is hot.
        """
        sel = labels.selector(env, 'container!=""')
        used = {
            s["metric"]["pod"]: float(s["value"][1])
            for s in _promql(ctx, f"sum by (pod) (rate(container_cpu_usage_seconds_total{{{sel}}}[{minutes}m]))")
        }
        lim_sel = labels.selector(env, 'resource="cpu"')
        limits = {
            s["metric"]["pod"]: float(s["value"][1])
            for s in _promql(ctx, f"sum by (pod) (kube_pod_container_resource_limits{{{lim_sel}}})")
        }
        return [
            {
                "pod": pod,
                "cores": round(cores, 4),
                "limit_cores": limits.get(pod),
                "percent_of_limit": round(100 * cores / limits[pod], 1) if limits.get(pod) else None,
            }
            for pod, cores in sorted(used.items(), key=lambda kv: -kv[1])
        ]

    def get_memory_usage(env: str) -> list[dict]:
        """Memory used per pod, against that pod's own limit.

        Args:
            env: Environment name.

        Returns [{"pod", "mib", "limit_mib", "percent_of_limit"}, ...].
        percent_of_limit approaching 100 predicts an OOMKill. None means the pod
        declares no memory limit.
        Call this when asked about RAM, memory pressure, OOM, or restarts.
        """
        sel = labels.selector(env, 'container!=""')
        used = {
            s["metric"]["pod"]: float(s["value"][1])
            for s in _promql(ctx, f"sum by (pod) (container_memory_working_set_bytes{{{sel}}})")
        }
        lim_sel = labels.selector(env, 'resource="memory"')
        limits = {
            s["metric"]["pod"]: float(s["value"][1])
            for s in _promql(ctx, f"sum by (pod) (kube_pod_container_resource_limits{{{lim_sel}}})")
        }
        mib = 1024 * 1024
        return [
            {
                "pod": pod,
                "mib": round(b / mib, 1),
                "limit_mib": round(limits[pod] / mib, 1) if limits.get(pod) else None,
                "percent_of_limit": round(100 * b / limits[pod], 1) if limits.get(pod) else None,
            }
            for pod, b in sorted(used.items(), key=lambda kv: -kv[1])
        ]

    def get_disk_usage() -> list[dict]:
        """Root-filesystem usage per node.

        Returns [{"node", "disk_percent"}, ...], worst first.
        Takes no environment: disk belongs to the node, and every environment in
        this cluster shares the same nodes. A node above ~85% gets tainted with
        DiskPressure and the kubelet starts evicting pods regardless of namespace.
        Call this when asked about disk space, full disk, or eviction.
        """
        sel = 'mountpoint="/",fstype!~"tmpfs|overlay"'
        q = f"100 * (1 - node_filesystem_avail_bytes{{{sel}}} / node_filesystem_size_bytes{{{sel}}})"
        rows = [
            {"node": s["metric"].get("instance", "?"), "disk_percent": round(float(s["value"][1]), 1)}
            for s in _promql(ctx, q)
        ]
        return sorted(rows, key=lambda r: -r["disk_percent"])

    def get_error_rate(env: str, minutes: int = 30) -> float | None:
        """5xx error rate as a percent of all requests for the env.

        Args:
            env: Environment name.
            minutes: Lookback window. Widen it on a low-traffic service, where a
                short window holds no requests at all.

        Returns a float (0..100), or None when no requests were seen in the
        window. None is not health — it means there is nothing to judge, so say
        so rather than reporting zero errors.
        Call this when asked about errors, error spike, 5xx, or service health.
        """
        sel_all = labels.selector(env)
        sel_5xx = labels.selector(env, 'status=~"5.."')
        # `or vector(0)` keeps a genuine zero from collapsing into an empty
        # vector: with no 5xx series at all, the division would yield no result
        # and read as "no data" rather than "no errors".
        errors = f"(sum(rate(http_requests_total{{{sel_5xx}}}[{minutes}m])) or vector(0))"
        total = f"sum(rate(http_requests_total{{{sel_all}}}[{minutes}m]))"
        q = f"100 * ({errors}) / ({total})"
        return _scalar_or_none(_promql(ctx, q), precision=3)

    def get_latency_p95(env: str, minutes: int = 30) -> float | None:
        """95th-percentile HTTP request latency in seconds for the env.

        Args:
            env: Environment name.
            minutes: Lookback window. Widen it on a low-traffic service, where a
                short window holds no requests at all.

        Returns p95 in seconds, or None when no requests were seen in the window.
        None is not a fast service — it means there is nothing to measure.
        Call this when asked about latency, slow requests, or response time.
        """
        sel = labels.selector(env)
        q = (
            f"histogram_quantile(0.95, sum by (le) "
            f"(rate(http_request_duration_seconds_bucket{{{sel}}}[{minutes}m])))"
        )
        return _scalar_or_none(_promql(ctx, q), precision=4)

    def get_active_alerts() -> list[dict]:
        """List alerts currently firing in Alertmanager.

        Returns [{"name", "state", "labels", "started_at"}, ...].
        Call when asked about active alerts, what's firing, or current incidents.
        """
        if ctx.alerts is None:
            raise RuntimeError(
                "ALERTMANAGER_URL is not configured, so alert state cannot be read. "
                "This is not the same as there being no alerts."
            )
        r = ctx.alerts.get("/api/v2/alerts", params={"active": "true"})
        r.raise_for_status()
        return [
            {
                "name": a.get("labels", {}).get("alertname", "?"),
                "state": a.get("status", {}).get("state", "?"),
                "labels": a.get("labels", {}),
                "started_at": a.get("startsAt", ""),
            }
            for a in r.json()
        ]

    def search_logs(env: str, contains: str, minutes: int = 60, limit: int = 50) -> list[dict]:
        """Search logs in Loki for lines containing a substring.

        Args:
            env: Environment name.
            contains: Case-sensitive substring (Loki line-filter |=).
            minutes: Look-back window in minutes (default 60).
            limit: Max log lines to return (default 50).

        Returns [{"time", "unit", "line"}, ...].
        Call when looking for an error message, stack trace, or phrase in logs.
        """
        now_ns = int(time.time() * 1e9)
        start_ns = int(now_ns - minutes * 60 * 1e9)
        needle = contains.replace("\\", "\\\\").replace('"', '\\"')
        q = "{" + labels.selector(env) + "}" + f' |= "{needle}"'
        r = ctx.loki.get(
            "/loki/api/v1/query_range",
            params={
                "query": q,
                "start": str(start_ns),
                "end": str(now_ns),
                "limit": str(limit),
                "direction": "backward",
            },
        )
        r.raise_for_status()
        out: list[dict] = []
        for s in r.json().get("data", {}).get("result", []):
            stream = s["stream"]
            pod = stream.get("pod") or stream.get("app") or stream.get("service_name") or "?"
            for tsval in s.get("values", []):
                out.append({"time": tsval[0], "pod": pod, "line": tsval[1]})
        return out[:limit]

    def get_recent_deploys(env: str, limit: int = 5) -> list[dict]:
        """Recent deployment tags for the given environment.

        Args:
            env: Environment name.
            limit: Number of recent tags to return (default 5).

        Returns [{"tag", "sha", "date", "message"}, ...] — most recent first.
        Call when asked what shipped recently, or to correlate an issue with a deploy.
        """
        tags = ctx.vcs.list_tags(limit=50)
        rules = ctx.cfg.deploy_tags
        out = []
        for t in tags:
            if not rules.matches(env, t.tag):
                continue
            out.append(
                {"tag": t.tag, "sha": t.sha, "date": t.date, "message": t.message}
            )
            if len(out) >= limit:
                break
        return out

    def get_commit_diff(sha: str, max_chars: int = 50_000) -> str:
        """Unified diff of a single commit.

        Args:
            sha: Commit SHA (full or short).
            max_chars: Truncate diff to this many characters (default 50000).

        Call when investigating what changed in a recent deploy.
        """
        return ctx.vcs.get_commit_diff(sha, max_chars=max_chars)

    def get_file_commits(path: str, limit: int = 10) -> list[dict]:
        """Recent commits that touched a specific file.

        Args:
            path: File path in the repo, e.g. 'internal/handlers/form.go'.
            limit: Number of commits to return (default 10).

        Returns [{"sha", "date", "message"}, ...].
        Call when a log/stack trace points to a specific file and you need the
        commit that introduced the bug. Use BEFORE get_commit_diff.
        """
        commits = ctx.vcs.get_file_commits(path, limit=limit)
        return [{"sha": c.sha, "date": c.date, "message": c.message} for c in commits]

    # Register every tool
    for fn in (
        ping,
        get_cpu_usage,
        get_memory_usage,
        get_disk_usage,
        get_error_rate,
        get_latency_p95,
        get_active_alerts,
        search_logs,
        get_recent_deploys,
        get_commit_diff,
        get_file_commits,
    ):
        mcp.tool(fn)
