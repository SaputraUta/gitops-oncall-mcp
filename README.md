# gitops-oncall-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

An MCP server that gives an LLM agent a typed, auditable on-call surface over a GitOps-on-Kubernetes platform: Kubernetes, Argo CD, Argo Rollouts, Prometheus, Loki, Tempo and GitHub.

Requires a Kubernetes cluster. The resource tools read cAdvisor and kube-state-metrics per pod, and the delivery tools read Argo CD and Argo Rollouts custom resources, so there is no meaningful way to run this against plain hosts.

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
| `get_error_rate` | Prometheus | 5xx as a percentage of all requests, or `None` when the window held no traffic |
| `get_latency_p95` | Prometheus | p95 request duration in seconds, or `None` when the window held no traffic |
| `get_cpu_usage` | Prometheus | cores per pod, against that pod's own limit |
| `get_memory_usage` | Prometheus | working-set MiB per pod, against that pod's own limit |
| `get_disk_usage` | Prometheus | root filesystem percent per node |
| `get_active_alerts` | Alertmanager, else Prometheus | firing alerts, worst severity first |
| `search_logs` | Loki | log lines matching a substring in a window, labelled by pod |
| `get_pods` | Kubernetes | phase, readiness, restarts, age, and what looks wrong |
| `get_events` | Kubernetes | recent warnings, within a recency window |
| `get_rollouts` | Argo Rollouts | running image tag, canary step, whether traffic is fully shifted |
| `get_analysis_runs` | Argo Rollouts | the verdict that advanced or aborted a canary, and why |
| `get_argocd_apps` | Argo CD | sync status, health, target revision, last sync |
| `get_recent_deploys` | GitHub | release tags across every application repo, newest first |
| `get_commit_diff` | GitHub | the diff for one commit |
| `get_file_commits` | GitHub | history for one path |

Two of these carry a deliberate warning in their docstring, because the obvious
reading is wrong. Events are historical, so an event from hours ago may already
be resolved. And an Argo CD Application can read `Synced` while a rollout is
aborted, because Argo CD compares the spec and the refusal lives in the
rollout's status.

Writes, split in two so a single confused turn cannot act:

| Propose | Confirm | Effect |
|---|---|---|
| `propose_rollback` | `confirm_rollback` | dispatch the deploy workflow at an older tag |
| `propose_pr_change` | `confirm_pr_change` | open a pull request against the config repository |

`propose_*` has no side effect and returns a proposal id with a TTL. `confirm_*` refuses without a live id. Both ends are written to the audit log.

Planned, not built yet:

| Source | Reads |
|---|---|
| Tempo | traces and span timings |

`propose_rollback` still dispatches a deploy workflow, inherited from the fork.
In this platform a rollback is a commit to the config repository, so that pair
is due to be rebuilt on the pull-request path.

## Configuration

Every setting is an environment variable. Copy `.env.example` to `.env` and fill it in; the file documents each one.

The observability endpoints are addressed directly rather than through a Grafana datasource proxy, because in a cluster they are Services. That removes a hop, a dependency and a credential.

| Variable | Required | Purpose |
|---|---|---|
| `PROMETHEUS_URL` | yes | Prometheus HTTP API |
| `LOKI_URL` | yes | Loki HTTP API |
| `ALERTMANAGER_URL` | no | Alertmanager HTTP API. Unset falls back to Prometheus, which evaluates the alerts but cannot see silences |
| `OBSERVABILITY_TOKEN` | no | bearer sent to all three, for setups behind an auth proxy |
| `K8S_NAMESPACE` | yes | the one namespace the Kubernetes tools may read |
| `ARGOCD_NAMESPACE` | no | where Argo CD runs, default `argocd` |
| `ENV_LABEL_KEY` | no | label that identifies the environment, default `env`; set to `namespace` when environments are namespaces |
| `ENV_VALUE_MAP` | no | maps the environment names the agent uses onto the label values your metrics carry |
| `GITHUB_TOKEN` | yes | fine-grained PAT, scoped to the repositories below and nothing else |
| `GITHUB_OWNER`, `GITHUB_REPO` | yes | the config repository, where pull requests are opened |
| `GITHUB_APP_REPOS` | no | application repositories, which is where release tags live |
| `MCP_BEARER_TOKEN` | when not loopback | shared secret required on every request |

The namespace is configuration rather than a tool argument on purpose: the model
chooses what to ask about, never what it has access to.

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
