"""Shared test fixtures."""

from __future__ import annotations

import re

import pytest

from gitops_oncall_mcp.config import (
    Config,
    DeployTagConfig,
    GitHubConfig,
    GuardrailsConfig,
    LabelConfig,
    ObservabilityConfig,
    ServerConfig,
    VCSConfig,
)


@pytest.fixture
def cfg() -> Config:
    """A minimal Config that exercises every dataclass without hitting env vars."""
    return Config(
        observability=ObservabilityConfig(
            prometheus_url="http://prometheus.test",
            loki_url="http://loki.test",
            alertmanager_url="http://alertmanager.test",
            bearer_token=None,
            ca_cert_path=None,
        ),
        labels=LabelConfig(env_key="env", team_key="team", team_value="testteam"),
        deploy_tags=DeployTagConfig(
            prod_regex=re.compile(r"^v\d+\.\d+\.\d+$"),
            nonprod_suffixes={"dev": "-dev", "staging": "-stag"},
        ),
        vcs=VCSConfig(
            github=GitHubConfig(
                token="ghp_test",
                owner="octocat",
                repo="repo",
                deploy_workflow="deploy.yml",
                api_base="https://api.github.com",
            )
        ),
        server=ServerConfig(host="127.0.0.1", port=8765, bearer_token=""),
        guardrails=GuardrailsConfig(proposal_ttl_seconds=600, audit_log_path=None),
    )
