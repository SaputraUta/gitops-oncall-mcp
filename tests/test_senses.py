"""Senses tools with Prometheus, Loki and Alertmanager mocked by respx."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import respx
from fastmcp import FastMCP

from gitops_oncall_mcp.tools import senses

if TYPE_CHECKING:
    pass


def _make_clients():
    return (
        httpx.Client(base_url="http://prometheus.test"),
        httpx.Client(base_url="http://loki.test"),
        httpx.Client(base_url="http://alertmanager.test"),
    )


class _StubVCS:
    """Minimal VCS adapter that returns canned values."""

    def list_tags(self, limit: int = 50):
        from gitops_oncall_mcp.vcs.base import TagInfo

        return [
            TagInfo(tag="v1.0.0-stag", sha="abc123", date="2026-01-01", message="ok"),
            TagInfo(tag="v1.0.0", sha="abc124", date="2026-01-02", message="prod cut"),
            TagInfo(tag="v0.9.0-dev", sha="abc125", date="2026-01-03", message="dev"),
        ]

    def get_commit_diff(self, sha, max_chars=50_000):
        return f"diff for {sha}"

    def get_file_commits(self, path, limit=10):
        from gitops_oncall_mcp.vcs.base import Commit

        return [Commit(sha="aaa", date="2026-01-01", message=f"touched {path}")]

    def trigger_deploy(self, env, ref):
        raise NotImplementedError

    def open_pr(self, **kwargs):
        raise NotImplementedError


def _register(cfg):
    prometheus, loki, alerts = _make_clients()
    mcp = FastMCP("test")
    ctx = senses.SensesCtx(prometheus=prometheus, loki=loki, alerts=alerts, cfg=cfg, vcs=_StubVCS())
    senses.register(mcp, ctx)
    return mcp, ctx


def _tool(mcp, name):
    """Reach the plain function behind a registered FastMCP tool."""
    return asyncio.run(mcp.get_tool(name)).fn


@respx.mock
def test_get_cpu_usage_reports_percent_of_limit(cfg):
    """Cores are meaningless alone; the tool must divide by the pod's own limit."""

    def respond(request):
        q = request.url.params["query"]
        if "container_cpu_usage_seconds_total" in q:
            return httpx.Response(200, json={"data": {"result": [
                {"metric": {"pod": "dora-1"}, "value": [0, "0.3"]},
                {"metric": {"pod": "maps-1"}, "value": [0, "0.08"]},
            ]}})
        return httpx.Response(200, json={"data": {"result": [
            {"metric": {"pod": "dora-1"}, "value": [0, "0.6"]},
        ]}})

    respx.get("http://prometheus.test/api/v1/query").mock(side_effect=respond)
    mcp, _ = _register(cfg)
    rows = _tool(mcp, "get_cpu_usage")("production")

    assert [r["pod"] for r in rows] == ["dora-1", "maps-1"], "busiest pod must sort first"
    assert rows[0]["percent_of_limit"] == 50.0
    assert rows[1]["limit_cores"] is None, "a pod with no limit must still appear"
    assert rows[1]["percent_of_limit"] is None, "unlimited is not zero percent"


@respx.mock
def test_get_error_rate_is_none_without_traffic_and_zero_with_it(cfg):
    """None and 0.0 are different answers and must not be collapsed."""
    respx.get("http://prometheus.test/api/v1/query").respond(
        json={"data": {"result": [{"metric": {}, "value": [0, "NaN"]}]}}
    )
    mcp, _ = _register(cfg)
    assert _tool(mcp, "get_error_rate")("production") is None, "no traffic is not zero errors"

    respx.get("http://prometheus.test/api/v1/query").respond(
        json={"data": {"result": [{"metric": {}, "value": [0, "0"]}]}}
    )
    mcp, _ = _register(cfg)
    assert _tool(mcp, "get_error_rate")("production") == 0.0, "measured zero must stay zero"


@respx.mock
def test_get_error_rate_window_and_zero_guard_reach_the_query(cfg):
    """The lookback must be the caller's, and a genuine zero must be guarded."""
    route = respx.get("http://prometheus.test/api/v1/query").respond(
        json={"data": {"result": [{"metric": {}, "value": [0, "12.456"]}]}}
    )
    mcp, _ = _register(cfg)
    assert _tool(mcp, "get_error_rate")("production", minutes=15) == 12.456

    q = route.calls.last.request.url.params["query"]
    assert "[15m]" in q and "[30m]" not in q, "the window must come from the argument"
    assert "or vector(0)" in q, "an absent 5xx series must read as zero, not as no data"


@respx.mock
def test_get_latency_p95_is_none_without_traffic(cfg):
    respx.get("http://prometheus.test/api/v1/query").respond(
        json={"data": {"result": []}}
    )
    mcp, _ = _register(cfg)
    assert _tool(mcp, "get_latency_p95")("production") is None


@respx.mock
def test_get_active_alerts_falls_back_to_prometheus(cfg):
    """Without Alertmanager the alerts still come back, labelled as the lesser view."""
    respx.get("http://prometheus.test/api/v1/alerts").respond(
        json={"data": {"alerts": [
            {"labels": {"alertname": "KubeCPUOvercommit", "severity": "warning"},
             "annotations": {"summary": "Cluster has overcommitted CPU."},
             "state": "firing", "activeAt": "2026-09-11T00:00:00Z"},
            {"labels": {"alertname": "KubeSchedulerDown", "severity": "critical"},
             "annotations": {}, "state": "firing", "activeAt": "2026-09-11T00:00:00Z"},
            {"labels": {"alertname": "NotYet", "severity": "critical"},
             "annotations": {}, "state": "pending", "activeAt": "2026-09-11T00:00:00Z"},
        ]}}
    )
    prometheus, loki, _ = _make_clients()
    mcp = FastMCP("test")
    ctx = senses.SensesCtx(prometheus=prometheus, loki=loki, alerts=None, cfg=cfg, vcs=_StubVCS())
    senses.register(mcp, ctx)
    alerts = _tool(mcp, "get_active_alerts")()

    assert [a["name"] for a in alerts] == ["KubeSchedulerDown", "KubeCPUOvercommit"], (
        "pending is not firing, and critical sorts above warning"
    )
    assert alerts[0]["source"].startswith("prometheus"), "the weaker view must say so"


@respx.mock
def test_get_active_alerts(cfg):
    respx.get("http://alertmanager.test/api/v2/alerts").respond(
        json=[
            {
                "labels": {"alertname": "ErrorRateHigh", "severity": "critical"},
                "status": {"state": "active"},
                "startsAt": "2026-01-01T00:00:00Z",
            }
        ]
    )
    prometheus, loki, alerts = _make_clients()
    r = alerts.get("/api/v2/alerts", params={"active": "true"})
    alerts = r.json()
    assert len(alerts) == 1
    assert alerts[0]["labels"]["alertname"] == "ErrorRateHigh"


def test_label_selector_used_in_query(cfg):
    """Verify the label config affects what selector ends up in queries."""
    sel = cfg.labels.selector("staging")
    assert sel == 'env="staging",team="testteam"'

    sel2 = cfg.labels.selector("staging", 'status=~"5.."')
    assert sel2 == 'env="staging",team="testteam",status=~"5.."'


def test_vcs_recent_deploys_filters_by_env(cfg):
    """get_recent_deploys uses the configured tag rules to filter."""
    # Manually exercise the rule via the stub VCS
    vcs = _StubVCS()
    tags = vcs.list_tags()
    staging_tags = [t for t in tags if cfg.deploy_tags.matches("staging", t.tag)]
    prod_tags = [t for t in tags if cfg.deploy_tags.matches("prod", t.tag)]
    assert any(t.tag == "v1.0.0-stag" for t in staging_tags)
    assert any(t.tag == "v1.0.0" for t in prod_tags)
    assert not any(t.tag == "v1.0.0" for t in staging_tags)

@respx.mock
def test_search_logs_escapes_quotes(cfg):
    route = respx.get("http://loki.test/loki/api/v1/query_range").respond(
        json={"data": {"result": []}}
    )
    mcp, _ = _register(cfg)
    tool = asyncio.run(mcp.get_tool("search_logs"))
    tool.fn(env="prod", contains='say "hi"')

    q = route.calls.last.request.url.params["query"]
    assert q == '{env="prod",team="testteam"} |= "say \\"hi\\""'

def test_get_recent_deploys_merges_repos_by_date(cfg):
    """Tags live per repo; the tool must merge them into one timeline."""

    class _Repo:
        """Counts describe calls, because the cost of this tool is one request
        per described tag and the whole point is to describe as few as possible."""

        def __init__(self, tags):
            self._tags = dict(tags)
            self.described = 0

        def list_tag_names(self):
            return list(self._tags)

        def describe_tags(self, names):
            from gitops_oncall_mcp.vcs.base import TagInfo

            self.described += len(names)
            return [TagInfo(tag=n, sha="s", date=self._tags[n], message="m") for n in names]

    prometheus, loki, alerts = _make_clients()
    mcp = FastMCP("test")
    ctx = senses.SensesCtx(
        prometheus=prometheus,
        loki=loki,
        alerts=alerts,
        cfg=cfg,
        vcs=_StubVCS(),
        app_vcs={
            "dora": _Repo([("v1.0.4", "2026-09-07T00:00:00Z"), ("v0.9.0-dev", "2026-09-01T00:00:00Z")]),
            "maps": _Repo([("v1.0.11", "2026-09-08T00:00:00Z")]),
        },
    )
    senses.register(mcp, ctx)
    rows = _tool(mcp, "get_recent_deploys")("production")


    assert [(r["repo"], r["tag"]) for r in rows] == [("maps", "v1.0.11"), ("dora", "v1.0.4")]
    assert all("-dev" not in r["tag"] for r in rows), "dev tags are not production releases"
    assert ctx.app_vcs["dora"].described == 1, "the filtered-out dev tag must never be described"

    only = _tool(mcp, "get_recent_deploys")("production", repo="dora")
    assert [r["repo"] for r in only] == ["dora"]
