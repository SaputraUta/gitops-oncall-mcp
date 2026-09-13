#!/usr/bin/env bash
# One triage run, in the cluster. The pod is deleted when it exits.
set -euo pipefail
kubectl -n procal run oncall-$RANDOM --rm -it --restart=Never --quiet \
  --image=134604498185.dkr.ecr.ap-southeast-3.amazonaws.com/ch4-group4-procal-uta/gitops-oncall-agent:v0.1.0 \
  --overrides="$(cat <<JSON
{"spec":{"containers":[{"name":"oncall",
  "image":"134604498185.dkr.ecr.ap-southeast-3.amazonaws.com/ch4-group4-procal-uta/gitops-oncall-agent:v0.1.0",
  "args":["$*"],
  "stdin":true,"tty":true,
  "envFrom":[{"configMapRef":{"name":"gitops-oncall-agent"}},{"secretRef":{"name":"gitops-oncall-mcp"}}]}]}}
JSON
)"