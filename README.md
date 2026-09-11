# gitops-oncall-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

An MCP server that gives an LLM agent a typed, auditable on-call surface over a GitOps-on-Kubernetes platform: Kubernetes, Argo CD, Argo Rollouts, Prometheus, Loki, Tempo and GitHub.

Status: work in progress. Forked from [lgtm-oncall-mcp](https://github.com/SaputraUta/lgtm-oncall-mcp), which targets a hosted Grafana LGTM stack. This one targets a cluster you run yourself, and adds the Kubernetes and Argo surface the other project has no reason to carry.

## Why an MCP server and not a shell

The easy version of an on-call agent hands a model `kubectl` and a terminal. It demos well and is indefensible, because the agent's capability becomes "anything the kubeconfig can do", and the only thing standing between a hallucination and a deleted namespace is the model's judgement.

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

Its ServiceAccount carries no write verbs at all, so mutation is unavailable to it even if every other layer fails. A change it proposes becomes a pull request against the config repository, which Argo CD then syncs and Argo Rollouts then canaries. Every safety mechanism the platform already has applies to the agent for free, and undoing it is `git revert`, exactly as it would be for a human.

## Tools

Reads, no side effects:

| Tool | Source | Returns |
|---|---|---|
| `get_error_rate` | Prometheus | 5xx per second for an environment |
| `get_latency_p95` | Prometheus | p95 request duration |
| `get_cpu_usage` | Prometheus | per-service CPU against request |
| `get_memory_usage` | Prometheus | per-service memory against limit |
| `get_disk_usage` | Prometheus | volume utilisation |
| `get_active_alerts` | Alertmanager | currently firing alerts |
| `search_logs` | Loki | log lines matching a substring in a window |
| `get_recent_deploys` | GitHub | release tags and when they shipped |
| `get_commit_diff` | GitHub | the diff for one commit |
| `get_file_commits` | GitHub | history for one path |

Writes, split in two so a single confused turn cannot act:

| Propose | Confirm | Effect |
|---|---|---|
| `propose_rollback` | `confirm_rollback` | dispatch the deploy workflow at an older tag |
| `propose_pr_change` | `confirm_pr_change` | open a pull request against the config repository |

`propose_*` has no side effect and returns a proposal id with a TTL. `confirm_*` refuses without a live id. Both ends are written to the audit log.

Planned, not built yet:

| Source | Reads |
|---|---|
| Kubernetes | pod status and restarts, events, Rollout state, AnalysisRun verdicts |
| Argo CD | Application sync status, health, deployed revision |
| Tempo | traces and span timings |

## Configuration

Every setting is an environment variable. Copy `.env.example` to `.env` and fill it in; the file documents each one.

The observability endpoints are addressed directly rather than through a Grafana datasource proxy, because in a cluster they are Services. That removes a hop, a dependency and a credential.

| Variable | Required | Purpose |
|---|---|---|
| `PROMETHEUS_URL` | yes | Prometheus HTTP API |
| `LOKI_URL` | yes | Loki HTTP API |
| `ALERTMANAGER_URL` | no | Alertmanager HTTP API. Unset makes `get_active_alerts` fail loudly instead of reporting zero alerts |
| `OBSERVABILITY_TOKEN` | no | bearer sent to all three, for setups behind an auth proxy |
| `GITHUB_TOKEN` | yes | fine-grained PAT, scoped to the config repository only |
| `GITHUB_OWNER`, `GITHUB_REPO` | yes | the config repository |
| `MCP_BEARER_TOKEN` | when not loopback | shared secret required on every request |

## Running

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
set -a && . ./.env && set +a
.venv/bin/gitops-oncall-mcp
```

The server speaks MCP over HTTP on `MCP_PORT`, default 8765. Point an MCP client at `http://127.0.0.1:8765/mcp`.

To develop against a real cluster from a laptop, port-forward Prometheus and Loki and set the URLs to localhost.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Tests use `respx` to record HTTP, so the suite needs no cluster and no credentials.

## Licence

MIT. See [LICENSE](./LICENSE).
