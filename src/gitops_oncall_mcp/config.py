"""Environment-driven configuration. Loaded once at server startup."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


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
    env_values: dict[str, str] = field(default_factory=dict)  # logical env → label value

    @classmethod
    def from_env(cls) -> LabelConfig:
        raw = _optional("ENV_VALUE_MAP")
        return cls(
            env_key=_optional("ENV_LABEL_KEY", "env"),
            team_key=_optional("TEAM_LABEL_KEY", "team"),
            team_value=_optional("TEAM_LABEL_VALUE"),
            env_values=dict(
                (k.strip(), v.strip())
                for k, _, v in (pair.partition(":") for pair in raw.split(",") if ":" in pair)
            ),
        )

    def selector(self, env: str, extra: str = "") -> str:
        """Return a PromQL/LogQL selector body: env="...",team="..." plus extras."""
        parts = [f'{self.env_key}="{self.env_values.get(env, env)}"']
        if self.team_value:
            parts.append(f'{self.team_key}="{self.team_value}"')
        if extra:
            parts.append(extra)
        return ",".join(parts)
    
@dataclass(frozen=True)
class K8sConfig:
    namespace: str
    argocd_namespace: str = "argocd"
    
    @classmethod
    def from_env(cls) -> K8sConfig:
        return cls(
            namespace=_required("K8S_NAMESPACE"),
            argocd_namespace=_optional("ARGOCD_NAMESPACE", "argocd"),
        )

@dataclass(frozen=True)
class DeployTagConfig:
    """How environments map to git tags."""

    prod_regex: re.Pattern[str]
    nonprod_suffixes: dict[str, str]  # env name → tag suffix
    prod_envs: frozenset[str] = frozenset({"prod", "production"})

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
        """Whether `tag` is a release of `env`.

        Both "prod" and "production" name the production environment: the
        metric tools say "production", so accepting only "prod" here would
        return an empty deploy list rather than an error.
        """
        if env in self.prod_envs:
            return bool(self.prod_regex.match(tag))
        suffix = self.nonprod_suffixes.get(env)
        return bool(suffix) and tag.endswith(suffix)


@dataclass(frozen=True)
class GitHubConfig:
    token: str
    owner: str
    repo: str  # the config repo Argo CD reads, and where pull requests are opened
    deploy_workflow: str
    api_base: str
    app_repos: tuple[str, ...] = ()  # repos that carry release tags
    values_path: str = "envs/{env}/values.yaml"  # {env} is substituted per environment
    app_repos_token: str = ""  # read-only token for app_repos; empty reuses `token`


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
                app_repos=tuple(
                    r.strip() for r in _optional("GITHUB_APP_REPOS").split(",") if r.strip()
                ),
                values_path=_optional("GITHUB_VALUES_PATH", "envs/{env}/values.yaml"),
                app_repos_token=_optional("GITHUB_APP_REPOS_TOKEN"),
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
    k8s: K8sConfig

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            observability=ObservabilityConfig.from_env(),
            labels=LabelConfig.from_env(),
            k8s=K8sConfig.from_env(),
            deploy_tags=DeployTagConfig.from_env(),
            vcs=VCSConfig.from_env(),
            server=ServerConfig.from_env(),
            guardrails=GuardrailsConfig.from_env(),
        )
