"""Config loading + label/tag rule behavior."""

from __future__ import annotations

import re

import pytest

from gitops_oncall_mcp.config import (
    Config,
    DeployTagConfig,
    LabelConfig,
)


def test_label_selector_with_team():
    lab = LabelConfig(env_key="env", team_key="team", team_value="payments")
    sel = lab.selector("staging")
    assert 'env="staging"' in sel
    assert 'team="payments"' in sel


def test_label_selector_without_team():
    lab = LabelConfig(env_key="env", team_key="team", team_value="")
    sel = lab.selector("prod")
    assert sel == 'env="prod"'
    assert "team" not in sel


def test_label_selector_with_extra():
    lab = LabelConfig(env_key="env", team_key="team", team_value="")
    sel = lab.selector("staging", 'status=~"5.."')
    assert 'env="staging"' in sel
    assert 'status=~"5.."' in sel


def test_label_selector_custom_keys():
    lab = LabelConfig(env_key="environment", team_key="org", team_value="acme")
    sel = lab.selector("prod")
    assert 'environment="prod"' in sel
    assert 'org="acme"' in sel


def test_tag_match_prod():
    rules = DeployTagConfig(
        prod_regex=re.compile(r"^v\d+\.\d+\.\d+$"),
        nonprod_suffixes={"dev": "-dev", "staging": "-stag"},
    )
    assert rules.matches("prod", "v1.2.3") is True
    assert rules.matches("prod", "v1.2.3-stag") is False
    assert rules.matches("prod", "release-1.2.3") is False


def test_tag_match_staging():
    rules = DeployTagConfig(
        prod_regex=re.compile(r"^v\d+\.\d+\.\d+$"),
        nonprod_suffixes={"dev": "-dev", "staging": "-stag"},
    )
    assert rules.matches("staging", "v1.2.3-stag") is True
    assert rules.matches("staging", "v1.2.3") is False
    assert rules.matches("staging", "v1.2.3-dev") is False


def test_tag_match_unknown_env():
    rules = DeployTagConfig(
        prod_regex=re.compile(r"^v\d+\.\d+\.\d+$"),
        nonprod_suffixes={"dev": "-dev"},
    )
    assert rules.matches("qa", "v1.2.3-qa") is False  # qa not in suffix map


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("GRAFANA_URL", "https://g.example.com")
    monkeypatch.setenv("GRAFANA_TOKEN", "tok")
    monkeypatch.setenv("MIMIR_DS_UID", "m-uid")
    monkeypatch.setenv("LOKI_DS_UID", "l-uid")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_OWNER", "octocat")
    monkeypatch.setenv("GITHUB_REPO", "repo")

    cfg = Config.from_env()
    assert cfg.grafana.url == "https://g.example.com"
    assert cfg.vcs.github.owner == "octocat"
    assert cfg.vcs.github.api_base == "https://api.github.com"


def test_config_missing_required(monkeypatch):
    monkeypatch.delenv("GRAFANA_URL", raising=False)
    with pytest.raises(RuntimeError, match="GRAFANA_URL"):
        Config.from_env()
