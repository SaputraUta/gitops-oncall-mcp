# gitops-oncall-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

An MCP server that gives an LLM agent a typed, auditable on-call surface over a GitOps-on-Kubernetes platform: Kubernetes, Argo CD, Argo Rollouts, Prometheus, Loki, Tempo and GitHub.

Requires a Kubernetes cluster. The resource tools read cAdvisor and kube-state-metrics per pod, and the delivery tools read Argo CD and Argo Rollouts custom resources, so there is no meaningful way to run this against plain hosts.

Runs in-cluster as two pods, driven from Telegram and by Alertmanager. Forked from [lgtm-oncall-mcp](https://github.com/SaputraUta/lgtm-oncall-mcp), which targets a hosted Grafana LGTM stack. This one targets a cluster you run yourself, and adds the Kubernetes and Argo surface the other project has no reason to carry.

## How it fits together

Two pods. The server holds every credential and every tool; the agent holds
none and reaches them over HTTP with a bearer token.

```
Prometheus rules (155)                      a human, on Telegram
      │ severity=critical                          │
      ▼                                            ▼
 Alertmanager ──── webhook ────► gitops-oncall-agent ◄── one conversation,
                                  (Strands + LiteLLM)     so a proposal made
                                         │                now is still live
                                         │ MCP over HTTP  when you reply
                                         ▼
                                gitops-oncall-mcp ── ServiceAccount: get/list/watch
                                         │
              ┌──────────────┬───────────┴───────────┬──────────────┐
              ▼              ▼                       ▼              ▼
        Kubernetes      Prometheus                 Loki          GitHub
        Argo CD                                                    │
        Argo Rollouts                                              ▼
                                                        pull request, which a
                                                        human merges and Argo CD
                                                        then syncs and canaries
```

The agent image does not install this package. It has no tool code, no
Kubernetes client and no GitHub token, so "the agent can only act through MCP"
is a property of the deployment rather than a rule it is asked to follow.

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
| `propose_rollback` | `confirm_rollback` | open a pull request pinning one service to an older tag |
| `propose_pr_change` | `confirm_pr_change` | open a pull request against the config repository |

`propose_*` has no side effect and returns a proposal id with a TTL. `confirm_*` refuses without a live id. Both ends are written to the audit log.

Planned, not built yet:

| Source | Reads |
|---|---|
| Tempo | traces and span timings |

A rollback is a one-line change to the environment's values file, not a
pipeline trigger: `propose_rollback` reads the tag pinned right now and returns
both ends of the move, so the human approving it sees `v1.0.11 -> v1.0.9`
before anything happens. The file is edited line-by-line rather than through a
YAML round-trip, which would reformat the whole file and bury a one-line change
in an unreadable diff.

## The agent

`agent/` holds a reference on-call agent built on [Strands](https://strandsagents.com),
pointed at any OpenAI-compatible endpoint. It is deliberately given no tools of
its own — `strands-agents-tools` ships `shell`, `file_write` and `python_repl`,
which is the terminal this project exists to avoid handing a model.

It is woken two ways:

| Trigger | Path |
|---|---|
| A human asks | Telegram long polling, so nothing inbound reaches the cluster |
| An alert fires | Alertmanager posts to `:8080`, bearer-authenticated, answered `202` |

Both feed the same conversation, which is why the agent is one replica with
`strategy: Recreate`. Two pollers would each take an arbitrary half of the
messages, and a proposal made by one would be unknown to the other when the
human replies.

The playbook it runs from names the three readings that are wrong by default:
`None` is absent data rather than zero, a Kubernetes event is history rather
than current state, and an Argo CD Application reads `Synced` when Git got its
way about the spec, not when the release succeeded.

Approval identity is checked on the numeric Telegram user id, never the
username — a username can be released and re-registered by somebody else.

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
| `GITHUB_VALUES_PATH` | no | values file per environment, `{env}` substituted; default `envs/{env}/values.yaml` |
| `GITHUB_APP_REPOS_TOKEN` | no | separate read-only token for the application repos; a fine-grained PAT applies its permissions to every repo it selects |
| `MCP_BEARER_TOKEN` | when not loopback | shared secret required on every request |

The agent reads a few more:

| Variable | Purpose |
|---|---|
| `MCP_URL` | where the MCP server is, e.g. `http://gitops-oncall-mcp:8765/mcp` |
| `LLM_BASE`, `LLM_API_KEY`, `LLM_MODEL` | any OpenAI-compatible endpoint |
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | comma-separated numeric ids allowed to approve |
| `WEBHOOK_PORT` | alert listener, default 8080 |

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

### In a cluster

Two images, built from `Dockerfile` and `agent/Dockerfile`. Both run non-root on
a read-only root filesystem with every Linux capability dropped.

The server needs a ServiceAccount bound to a ClusterRole with `get`, `list` and
`watch` and nothing else — including on `argoproj.io`, because the built-in
`view` role does not know about Rollouts or Applications. The agent needs no
ServiceAccount at all.

Secrets reach the pods from a secret manager rather than Git. Note that this
does land them as Kubernetes Secrets, which are base64 and not encrypted; if
your platform can have the process fetch its own credentials at startup, that is
strictly better.

Pin image tags. `latest` makes two different images share a name, so Argo CD
reports `Synced` while running something else and rollback stops meaning
anything.

### Timeouts

Give the MCP client an explicit timeout. Most HTTP clients default to a few
seconds, a tool that reads several repositories takes longer, and the failure is
silent: the session is torn down and the agent waits on a result that will never
arrive. It looks exactly like a slow model.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Tests use `respx` to record HTTP, so the suite needs no cluster and no credentials.

## Licence

MIT. See [LICENSE](./LICENSE).
