"""FastMCP server entrypoint — wires config, clients, VCS adapter, tools."""

from __future__ import annotations

import dataclasses

import uvicorn
from fastmcp import FastMCP
from starlette.responses import JSONResponse

from .approval import ProposalStore
from .audit import AuditLog
from .clients import alertmanager_client, k8s_clients, loki_client, prometheus_client
from .config import Config
from .tools import hands as hands_tools
from .tools import k8s as k8s_tools
from .tools import senses as senses_tools
from .vcs.base import VCSAdapter
from .vcs.github import GitHubAdapter


def _build_vcs(cfg: Config) -> VCSAdapter:
    """Adapter for the config repo — the one Argo CD reads and PRs land in."""
    return GitHubAdapter(cfg.vcs.github)


def _build_app_vcs(cfg: Config) -> dict[str, VCSAdapter]:
    """One adapter per application repo, keyed by repo name.

    Release tags live on the application repos, not on the config repo, so
    "what shipped" cannot be answered from a single adapter. The adapter code
    is per-repo already, so this is a mapping rather than a rewrite.
    """
    gh = cfg.vcs.github
    # A fine-grained PAT applies its permissions to every repo it selects, so a
    # separate read-only token keeps write access off the application repos.
    token = gh.app_repos_token or gh.token
    return {
        name: GitHubAdapter(dataclasses.replace(gh, repo=name, token=token))
        for name in gh.app_repos
    }


class BearerTokenAuth:
    """Reject requests without the shared secret, as pure ASGI middleware.

    Deliberately not a BaseHTTPMiddleware. That class buffers the response
    through call_next, which breaks the long-lived streaming responses MCP
    rides on: the stream never completes and starlette raises "No response
    returned" while the client waits forever. A plain ASGI wrapper inspects the
    request headers and then hands the untouched send/receive pair through, so
    streaming behaves exactly as if nothing were wrapping it.

    Worth knowing: curl does not stream, so a curl probe passes against the
    broken version. Only a real MCP client exercises this path.
    """

    def __init__(self, app, expected: str):
        self._app = app
        self._expected = f"Bearer {expected}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        supplied = dict(scope.get("headers") or {}).get(b"authorization")
        if supplied != self._expected:
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self._app(scope, receive, send)


def build_server() -> FastMCP:
    cfg = Config.from_env()
    mcp = FastMCP("gitops-oncall-mcp")

    prometheus = prometheus_client(cfg.observability)
    loki = loki_client(cfg.observability)
    alerts = alertmanager_client(cfg.observability)
    core, custom = k8s_clients()
    vcs = _build_vcs(cfg)
    app_vcs = _build_app_vcs(cfg)
    proposals = ProposalStore(default_ttl_seconds=cfg.guardrails.proposal_ttl_seconds)
    audit = AuditLog(file_path=cfg.guardrails.audit_log_path)

    senses_tools.register(
        mcp,
        senses_tools.SensesCtx(
            prometheus=prometheus, loki=loki, alerts=alerts, cfg=cfg, vcs=vcs, app_vcs=app_vcs
        ),
    )
    k8s_tools.register(mcp, k8s_tools.K8sCtx(core=core, custom=custom, cfg=cfg))
    hands_tools.register(
        mcp,
        hands_tools.HandsCtx(cfg=cfg, vcs=vcs, proposals=proposals, audit=audit),
    )

    return mcp


def main() -> None:
    cfg = Config.from_env()
    mcp = build_server()

    app = mcp.http_app()
    if cfg.server.bearer_token:
        app.add_middleware(BearerTokenAuth, expected=cfg.server.bearer_token)

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port)


if __name__ == "__main__":
    main()
