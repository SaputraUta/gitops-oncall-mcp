"""FastMCP server entrypoint — wires config, clients, VCS adapter, tools."""

from __future__ import annotations

import dataclasses

import uvicorn
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
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
    # The config repo needs write access to open pull requests; the app repos
    # are only ever read. A fine-grained PAT grants its permissions across every
    # repository it selects, so keeping them on one token would hand write
    # access to the application repos as well. GITHUB_APP_REPOS_TOKEN lets the
    # read half be a read-only token; unset, it reuses the one token.
    token = gh.app_repos_token or gh.token
    return {
        name: GitHubAdapter(dataclasses.replace(gh, repo=name, token=token))
        for name in gh.app_repos
    }


class BearerTokenAuth(BaseHTTPMiddleware):
    def __init__(self, app, expected: str):
        super().__init__(app)
        self._expected = f"Bearer {expected}"

    async def dispatch(self, request: Request, call_next):
        if request.headers.get("authorization") != self._expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


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
