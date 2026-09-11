# gitops-oncall-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

> An MCP server that gives an LLM agent a typed, auditable on-call surface over a GitOps-on-Kubernetes platform: **Kubernetes**, **Argo CD**, **Argo Rollouts**, **Prometheus**, **Loki**, **Tempo** and **GitHub**.

> Status: **work in progress.** Forked from [lgtm-oncall-mcp](https://github.com/SaputraUta/lgtm-oncall-mcp), which targets a Grafana Cloud style LGTM stack. This one targets a cluster you run yourself, and adds the Kubernetes and Argo surface that project has no reason to carry.

---

## Why an MCP server and not a shell

The easy version of an on-call agent is to hand a model `kubectl` and a terminal. It demos well and is indefensible, because the agent's capability becomes "anything the kubeconfig can do", and the only thing between a hallucination and a deleted namespace is the model's judgement.

This inverts it. The agent gets a fixed set of typed tools and nothing else. What it is able to do becomes a code review question rather than a prompting question.

The layering is also the folder structure:

| Layer | Enforced by | Where |
|---|---|---|
| What the identity can do | the cluster | a ServiceAccount with read verbs only |
| What the agent can express | this code | `tools/senses.py` reads, `tools/hands.py` acts |
| What a human agreed to | runtime | `approval.py`, a one-shot proposal id with a TTL |
| What happened | after the fact | `audit.py` |

## The property worth stating

**The agent never writes to the cluster. It writes to Git.**

Its ServiceAccount carries no write verbs at all, so mutation is not available to it even if every other layer fails. A change it proposes becomes a pull request against the config repository, which Argo CD then syncs and Argo Rollouts then canaries. Every safety mechanism the platform already has applies to the agent for free, and undoing it is `git revert`, exactly as it would be for a human.

## Planned surface

| Source | Reads |
|---|---|
| Kubernetes | pod status and restarts, events, Rollout state, AnalysisRun verdicts |
| Argo CD | Application sync status, health, deployed revision |
| Prometheus | error rate, latency, saturation |
| Loki | logs by service and pod |
| Tempo | traces and span timings |
| GitHub | tags, commit diffs, what shipped and when |

| Target | Writes |
|---|---|
| GitHub | open a pull request against the config repository, behind the approval gate |
| Argo Rollouts | abort or promote a rollout, behind the approval gate |

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Tests use `respx` to record HTTP, so the suite needs no cluster.

## Licence

MIT. See [LICENSE](./LICENSE).
