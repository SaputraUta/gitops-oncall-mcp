"""Environment-driven configuration. Loaded once at server startup."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


def _required(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(f"required env var {key} is unset or empty")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


@dataclass(frozen=True)
class ObservabilityConfig:
    """Where to reach the query APIs.

    Addressed directly rather than through a Grafana datasource proxy, because
    in a cluster these are Services and the shortest path is the honest one.
    In-cluster they need no credential at all, so bearer_token is optional and
    shared: if these sit behind one auth proxy it is one token, and if they do
    not it is none.
    """

    prometheus_url: str
    loki_url: str
    alertmanager_url: str | None
    bearer_token: str | None
    ca_cert_path: str | None  # absolute path or None

    @classmethod
    def from_env(cls) -> ObservabilityConfig:
        return cls(
            prometheus_url=_required("PROMETHEUS_URL").rstrip("/"),
            loki_url=_required("LOKI_URL").rstrip("/"),
            alertmanager_url=(_optional("ALERTMANAGER_URL").rstrip("/") or None),
            bearer_token=(_optional("OBSERVABILITY_TOKEN") or None),
            ca_cert_path=(_optional("OBSERVABILITY_CA_CERT_PATH") or None),
        )


@dataclass(frozen=True)
class LabelConfig:
    """How services are labeled in Prometheus and Loki."""

    env_key: str
    team_key: str
    team_value: str  # empty → don't filter by team

    @classmethod
    def from_env(cls) -> LabelConfig:
        return cls(
            env_key=_optional("ENV_LABEL_KEY", "env"),
            team_key=_optional("TEAM_LABEL_KEY", "team"),
            team_value=_optional("TEAM_LABEL_VALUE"),
        )

    def selector(self, env: str, extra: str = "") -> str:
        """Return a PromQL/LogQL selector body: env="...",team="..." plus extras."""
        parts = [f'{self.env_key}="{env}"']
        if self.team_value:
            parts.append(f'{self.team_key}="{self.team_value}"')
        if extra:
            parts.append(extra)
        return ",".join(parts)


@dataclass(frozen=True)
class DeployTagConfig:
    """How environments map to git tags."""

    prod_regex: re.Pattern[str]
    nonprod_suffixes: dict[str, str]  # env name → tag suffix

    @classmethod
    def from_env(cls) -> DeployTagConfig:
        return cls(
            prod_regex=re.compile(_optional("DEPLOY_TAG_PROD_REGEX", r"^v\d+\.\d+\.\d+$")),
            nonprod_suffixes={
                "dev": _optional("DEPLOY_TAG_NONPROD_SUFFIX_DEV", "-dev"),
                "staging": _optional("DEPLOY_TAG_NONPROD_SUFFIX_STAGING", "-stag"),
            },
        )

    def matches(self, env: str, tag: str) -> bool:
        if env == "prod":
            return bool(self.prod_regex.match(tag))
        suffix = self.nonprod_suffixes.get(env)
        return bool(suffix) and tag.endswith(suffix)


@dataclass(frozen=True)
class GitHubConfig:
    token: str
    owner: str
    repo: str
    deploy_workflow: str
    api_base: str


@dataclass(frozen=True)
class VCSConfig:
    """GitHub only for now. VCSAdapter stays an interface so another backend
    is one file plus a discriminator, not a refactor."""

    github: GitHubConfig

    @classmethod
    def from_env(cls) -> VCSConfig:
        return cls(
            github=GitHubConfig(
                token=_required("GITHUB_TOKEN"),
                owner=_required("GITHUB_OWNER"),
                repo=_required("GITHUB_REPO"),
                deploy_workflow=_optional("GITHUB_DEPLOY_WORKFLOW", "deploy.yml"),
                api_base=_optional("GITHUB_API_BASE", "https://api.github.com").rstrip("/"),
            )
        )


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    bearer_token: str  # empty → no auth

    @classmethod
    def from_env(cls) -> ServerConfig:
        return cls(
            host=_optional("MCP_HOST", "127.0.0.1"),
            port=int(_optional("MCP_PORT", "8765")),
            bearer_token=_optional("MCP_BEARER_TOKEN"),
        )


@dataclass(frozen=True)
class GuardrailsConfig:
    """Server-side safety gates for destructive tools."""

    # How long a `propose_*` result stays valid before the matching
    # `confirm_*` must be called. Short window = small attack surface.
    proposal_ttl_seconds: int

    # Optional path for the append-only audit log. Always also emitted to stderr.
    audit_log_path: str | None

    @classmethod
    def from_env(cls) -> GuardrailsConfig:
        return cls(
            # 600s (10 min) default: enough room for a human to read the
            # proposal in chat, think, then reply. 60s was too tight for
            # any realistic human-in-loop flow.
            proposal_ttl_seconds=int(_optional("PROPOSAL_TTL_SECONDS", "600")),
            audit_log_path=_optional("AUDIT_LOG_PATH") or None,
        )


@dataclass(frozen=True)
class Config:
    observability: ObservabilityConfig
    labels: LabelConfig
    deploy_tags: DeployTagConfig
    vcs: VCSConfig
    server: ServerConfig
    guardrails: GuardrailsConfig

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            observability=ObservabilityConfig.from_env(),
            labels=LabelConfig.from_env(),
            deploy_tags=DeployTagConfig.from_env(),
            vcs=VCSConfig.from_env(),
            server=ServerConfig.from_env(),
            guardrails=GuardrailsConfig.from_env(),
        )
