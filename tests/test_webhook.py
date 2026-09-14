"""Turning a Grafana payload into a triage prompt."""

from __future__ import annotations

from agent.webhook import describe

FIRING = {
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "HighErrorRate", "severity": "critical", "namespace": "procal"},
            "annotations": {"summary": "maps is returning 5xx"},
        }
    ]
}


def test_a_firing_alert_becomes_a_prompt():
    prompt = describe(FIRING)
    assert "HighErrorRate" in prompt
    assert "critical" in prompt
    assert "maps is returning 5xx" in prompt


def test_resolved_alerts_are_ignored():
    """Triaging something that already cleared costs a model call and says nothing."""
    payload = {"alerts": [{**FIRING["alerts"][0], "status": "resolved"}]}
    assert describe(payload) is None


def test_a_mixed_batch_keeps_only_what_is_firing():
    payload = {
        "alerts": [
            {**FIRING["alerts"][0], "status": "resolved"},
            {
                "status": "firing",
                "labels": {"alertname": "KubeCPUOvercommit", "severity": "warning"},
                "annotations": {},
            },
        ]
    }
    prompt = describe(payload)
    assert "KubeCPUOvercommit" in prompt
    assert "HighErrorRate" not in prompt


def test_an_empty_payload_is_not_a_turn():
    assert describe({}) is None
    assert describe({"alerts": []}) is None
