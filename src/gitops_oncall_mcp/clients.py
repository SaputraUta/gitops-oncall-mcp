"""httpx clients for the observability query APIs.

One builder per backend. Each returns a client already pointed at the base URL
and carrying the shared bearer token when one is configured, so callers only
ever write the API path.
"""

from __future__ import annotations

import httpx
from kubernetes import client, config

from .config import ObservabilityConfig


def _kwargs(obs: ObservabilityConfig, timeout: float) -> dict:
    headers = {"Authorization": f"Bearer {obs.bearer_token}"} if obs.bearer_token else {}
    return {
        "headers": headers,
        "verify": obs.ca_cert_path if obs.ca_cert_path else True,
        "timeout": timeout,
    }


def prometheus_client(obs: ObservabilityConfig, timeout: float = 15.0) -> httpx.Client:
    """Prometheus HTTP API. Paths look like /api/v1/query."""
    return httpx.Client(base_url=obs.prometheus_url, **_kwargs(obs, timeout))


def loki_client(obs: ObservabilityConfig, timeout: float = 20.0) -> httpx.Client:
    """Loki HTTP API. Paths look like /loki/api/v1/query_range."""
    return httpx.Client(base_url=obs.loki_url, **_kwargs(obs, timeout))


def alertmanager_client(obs: ObservabilityConfig, timeout: float = 15.0) -> httpx.Client | None:
    """Alertmanager HTTP API, or None when no URL is configured.

    Optional on purpose: a cluster can run without Alertmanager, and a tool that
    says so is better than one that reports zero alerts.
    """
    if not obs.alertmanager_url:
        return None
    return httpx.Client(base_url=obs.alertmanager_url, **_kwargs(obs, timeout))

def k8s_clients() -> tuple[client.CoreV1Api, client.CustomObjectsApi]:
    """Authenticate in-cluster if possible, else fall back to the local kubeconfig.

    A bare except here would swallow a broken kubeconfig too, so the fallback is
    narrowed to the one error that means "not running in a pod".
    """
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api(), client.CustomObjectsApi()