"""Long-running on-call agent, fed from Telegram and from Grafana alerts."""

from __future__ import annotations

import os
import traceback

import httpx2
from mcp.client.streamable_http import streamable_http_client
from strands import Agent
from strands.models.litellm import LiteLLMModel
from strands.tools.mcp import MCPClient

from agent import webhook
from agent.oncall import PLAYBOOK
from agent.telegram import Telegram


def main() -> None:
    tg = Telegram.from_env()
    webhook.serve(int(os.environ.get("WEBHOOK_PORT", "8080")))

    def transport():
        return streamable_http_client(
            os.environ["MCP_URL"],
            http_client=httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {os.environ['MCP_BEARER_TOKEN']}"},
                # httpx defaults to 5s, which silently kills any tool slower than
                # that: the session is torn down and the agent waits forever.
                timeout=httpx2.Timeout(120.0, connect=10.0),
            ),
        )

    model = LiteLLMModel(
        client_args={"api_key": os.environ["LLM_API_KEY"], "api_base": os.environ["LLM_BASE"]},
        model_id=f"litellm_proxy/{os.environ.get('LLM_MODEL', 'claude-sonnet')}",
    )

    with MCPClient(transport) as mcp:
        tools = mcp.list_tools_sync()
        print(f"[{len(tools)} tools from the MCP server]", flush=True)
        agent = Agent(model=model, tools=tools, system_prompt=PLAYBOOK)

        def run(text: str, label: str) -> None:
            print(f"[{label}] {text}", flush=True)
            tg.send("…on it")
            try:
                tg.send(str(agent(text)))
            except Exception as e:
                traceback.print_exc()
                tg.send(f"The agent failed on that turn: {type(e).__name__}: {e}")

        while True:
            try:
                while not webhook.alerts.empty():
                    run(webhook.alerts.get(), "alert")
                for text in tg.poll():
                    run(text, "turn")
            except Exception:
                traceback.print_exc()


if __name__ == "__main__":
    main()
