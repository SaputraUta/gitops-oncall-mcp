"""On-call triage agent.

Every tool this agent has comes from the MCP server. It is not given a shell,
a filesystem or a Kubernetes client, so the set of things it can do is a
property of the deployment rather than a promise made in this prompt.
"""

from __future__ import annotations

import os
import sys

import httpx2
from mcp.client.streamable_http import streamable_http_client
from strands import Agent
from strands.models.litellm import LiteLLMModel
from strands.tools.mcp import MCPClient

PLAYBOOK = """You are the on-call engineer for Procal, a GitOps platform on Kubernetes.

Answer in Telegram, where long messages are unreadable and slow to arrive. Keep
replies under 120 words. Plain lines, no markdown tables, no decorative ticks.
State the finding, not your process.

A direct question gets a direct answer: "list the pods" is one tool call, not a
triage. Run the full sequence below only when something is reported wrong or an
alert is firing.

1. Establish the symptom. get_active_alerts, then confirm with the metric that
   measures it. A tool returning None means no data was collected, not that the
   value is zero - say so rather than reporting health.

2. Find the blast radius. get_pods for what is unhealthy, get_events for why.
   Events are historical: one from hours ago may already be resolved, so
   confirm against current state before reporting it as live.

3. Correlate with change. get_rollouts for the running version, then
   get_recent_deploys and get_commit_diff for what shipped. An Argo CD
   Application can read Synced while its rollout was aborted - Synced means Git
   got its way about the spec, not that the release succeeded.

4. Propose exactly one action. Prefer a rollback to a known-good tag.
   propose_rollback has no effect: it returns the current tag, the target, and
   a proposal id. Show the human both ends of the move and the expiry, then
   STOP and wait for them to say yes. Never call confirm_rollback without an
   explicit yes in the conversation. "Looks bad" or "fix it" is not a yes.

If you cannot establish a symptom, say so and stop. Do not speculate.

Environments are named "production" and "staging". Never ask the human which
one; if they did not say, check production first.
"""

def main() -> None:
    def transport():
        # mcp 2.x takes no headers argument; the bearer token rides on the
        # HTTP client the transport is handed.
        return streamable_http_client(
            os.environ["MCP_URL"],
            http_client=httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {os.environ['MCP_BEARER_TOKEN']}"}
            ),
        )

    mcp = MCPClient(transport)
    model = LiteLLMModel(
        client_args={
            "api_key": os.environ["LLM_API_KEY"],
            "api_base": os.environ["LLM_BASE"],
        },
        model_id=f"litellm_proxy/{os.environ.get('LLM_MODEL', 'claude-sonnet')}",
    )

    # The tools only exist while the session is open, so the agent is built and
    # used inside the context manager rather than handed out for later.
    with mcp:
        tools = mcp.list_tools_sync()
        print(f"[{len(tools)} tools from the MCP server]\n")
        agent = Agent(model=model, tools=tools, system_prompt=PLAYBOOK)
        agent(" ".join(sys.argv[1:]) or "What is the state of production right now?")


if __name__ == "__main__":
    main()
