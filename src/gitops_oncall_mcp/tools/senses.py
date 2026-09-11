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
    mimir: httpx.Client
    loki: httpx.Client
    grafana: httpx.Client
    cfg: Config
    vcs: VCSAdapter


def _promql(ctx: SensesCtx, q: str) -> list[dict]:
    r = ctx.mimir.get("/api/v1/query", params={"query": q})
    r.raise_for_status()
    return r.json().get("data", {}).get("result", [])


def _scalar_or_zero(result: list[dict], precision: int) -> float:
    if not result:
        return 0.0
    val = float(result[0]["value"][1])
    if math.isnan(val) or math.isinf(val):
        return 0.0
    return round(val, precision)


def register(mcp: FastMCP, ctx: SensesCtx) -> None:
    labels = ctx.cfg.labels

    def ping() -> str:
        """Health check. Returns 'ok'."""
        return "ok"

    def get_cpu_usage(env: str) -> list[dict]:
        """Current CPU usage percent per host in the given environment.

        Args:
            env: Environment name (e.g. 'prod', 'staging', 'dev').

        Returns [{"instance": str, "cpu_percent": float}, ...].
        Call this when asked about CPU load, high CPU, or which host is hot.
        """
        sel = labels.selector(env, 'mode="idle"')
        q = f"100 - (avg by (instance) (rate(node_cpu_seconds_total{{{sel}}}[5m])) * 100)"
        return [
            {"instance": s["metric"].get("instance", "?"), "cpu_percent": round(float(s["value"][1]), 2)}
            for s in _promql(ctx, q)
        ]

    def get_memory_usage(env: str) -> list[dict]:
        """Current memory usage percent per host.

        Args:
            env: Environment name.

        Returns [{"instance": str, "memory_percent": float}, ...].
        Call when asked about RAM, memory pressure, or OOM risk.
        """
        sel = labels.selector(env)
        q = (
            f"100 * (1 - node_memory_MemAvailable_bytes{{{sel}}} "
            f"/ node_memory_MemTotal_bytes{{{sel}}})"
        )
        return [
            {"instance": s["metric"].get("instance", "?"), "memory_percent": round(float(s["value"][1]), 2)}
            for s in _promql(ctx, q)
        ]

    def get_disk_usage(env: str) -> list[dict]:
        """Current root-filesystem disk usage percent per host.

        Args:
            env: Environment name.

        Returns [{"instance": str, "disk_percent": float}, ...].
        Call when asked about disk space, full disk, or filesystem usage.
        """
        sel = labels.selector(env, 'mountpoint="/",fstype!~"tmpfs|overlay"')
        q = f"100 * (1 - node_filesystem_avail_bytes{{{sel}}} / node_filesystem_size_bytes{{{sel}}})"
        return [
            {"instance": s["metric"].get("instance", "?"), "disk_percent": round(float(s["value"][1]), 2)}
            for s in _promql(ctx, q)
        ]

    def get_error_rate(env: str) -> float:
        """Current 5xx error rate (percent of all requests) for the env over the last 5 minutes.

        Args:
            env: Environment name.

        Returns a single float (0..100). Returns 0.0 when there is no traffic.
        Call this when asked about errors, error spike, 5xx, or service health.
        """
        sel_all = labels.selector(env)
        sel_5xx = labels.selector(env, 'status=~"5.."')
        errors = f"sum(rate(http_requests_total{{{sel_5xx}}}[5m]))"
        total = f"sum(rate(http_requests_total{{{sel_all}}}[5m]))"
        q = f"100 * ({errors}) / ({total})"
        return _scalar_or_zero(_promql(ctx, q), precision=3)

    def get_latency_p95(env: str) -> float:
        """Current 95th-percentile HTTP request latency (seconds) for the env.

        Args:
            env: Environment name.

        Returns p95 latency in seconds, over the last 5 minutes. 0.0 when no traffic.
        Call this when asked about latency, slow requests, or response time.
        """
        sel = labels.selector(env)
        q = (
            f"histogram_quantile(0.95, sum by (le) "
            f"(rate(http_request_duration_seconds_bucket{{{sel}}}[5m])))"
        )
        return _scalar_or_zero(_promql(ctx, q), precision=4)

    def get_active_alerts() -> list[dict]:
        """List Grafana alerts currently firing.

        Returns [{"name", "state", "labels", "started_at"}, ...].
        Call when asked about active alerts, what's firing, or current incidents.
        """
        r = ctx.grafana.get(
            "/api/alertmanager/grafana/api/v2/alerts",
            params={"active": "true"},
        )
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
        # Loki stream selector — env (+ optional team) only; app label is not assumed
        selector_parts = [f'{labels.env_key}="{env}"']
        if labels.team_value:
            selector_parts.append(f'{labels.team_key}="{labels.team_value}"')
        q = "{" + ", ".join(selector_parts) + "}" + f' |= "{contains}"'
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
            unit = s["stream"].get("unit") or s["stream"].get("service_name") or "?"
            for tsval in s.get("values", []):
                out.append({"time": tsval[0], "unit": unit, "line": tsval[1]})
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
