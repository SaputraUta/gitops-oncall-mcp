"""httpx clients for Grafana datasource proxy + Grafana UI."""

from __future__ import annotations

import httpx

from .config import GrafanaConfig


def _verify(grafana: GrafanaConfig) -> str | bool:
    """Return the cert verification arg for httpx (path or True)."""
    return grafana.ca_cert_path if grafana.ca_cert_path else True


def mimir_client(grafana: GrafanaConfig, timeout: float = 15.0) -> httpx.Client:
    """Mimir/Prometheus client via Grafana datasource proxy."""
    return httpx.Client(
        base_url=f"{grafana.url}/api/datasources/proxy/uid/{grafana.mimir_ds_uid}",
        headers={"Authorization": f"Bearer {grafana.token}"},
        verify=_verify(grafana),
        timeout=timeout,
    )


def loki_client(grafana: GrafanaConfig, timeout: float = 20.0) -> httpx.Client:
    """Loki client via Grafana datasource proxy."""
    return httpx.Client(
        base_url=f"{grafana.url}/api/datasources/proxy/uid/{grafana.loki_ds_uid}",
        headers={"Authorization": f"Bearer {grafana.token}"},
        verify=_verify(grafana),
        timeout=timeout,
    )


def grafana_client(grafana: GrafanaConfig, timeout: float = 15.0) -> httpx.Client:
    """Grafana UI client (for alerts, dashboards, search)."""
    return httpx.Client(
        base_url=grafana.url,
        headers={"Authorization": f"Bearer {grafana.token}"},
        verify=_verify(grafana),
        timeout=timeout,
    )
