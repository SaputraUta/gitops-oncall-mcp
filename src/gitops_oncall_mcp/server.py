"""FastMCP server entrypoint — wires config, clients, VCS adapter, tools."""

from __future__ import annotations

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
    return GitHubAdapter(cfg.vcs.github)


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
    proposals = ProposalStore(default_ttl_seconds=cfg.guardrails.proposal_ttl_seconds)
    audit = AuditLog(file_path=cfg.guardrails.audit_log_path)

    senses_tools.register(
        mcp,
        senses_tools.SensesCtx(prometheus=prometheus, loki=loki, alerts=alerts, cfg=cfg, vcs=vcs),
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

    # Apply bearer-token middleware if configured
    if cfg.server.bearer_token:
        # FastMCP exposes its Starlette app via http_app() in 3.x
        app = mcp.http_app()
        app.add_middleware(BearerTokenAuth, expected=cfg.server.bearer_token)

    mcp.run(transport="http", host=cfg.server.host, port=cfg.server.port)


if __name__ == "__main__":
    main()
