"""Long-running on-call agent: one conversation, fed from Telegram.

The agent object holds the conversation, so a proposal made in one turn is
still live when the human replies in the next. That is the whole reason this
is a process rather than a series of one-shot runs.
"""

from __future__ import annotations

import os
import traceback

import httpx2
from mcp.client.streamable_http import streamable_http_client
from strands import Agent
from strands.models.litellm import LiteLLMModel
from strands.tools.mcp import MCPClient

from agent.oncall import PLAYBOOK
from agent.telegram import Telegram


def main() -> None:
    tg = Telegram.from_env()

    def transport():
        return streamable_http_client(
            os.environ["MCP_URL"],
            http_client=httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {os.environ['MCP_BEARER_TOKEN']}"},
                # httpx defaults to 5s. A tool that reads three GitHub repos takes
                # longer than that, and the timeout does not surface as an error:
                # the transport tears the session down and the agent waits on a
                # future nobody will ever complete. Fast tools worked, slow ones
                # hung, and it read as a slow model for most of a day.
                timeout=httpx2.Timeout(120.0, connect=10.0),
            ),
        )

    model = LiteLLMModel(
        client_args={"api_key": os.environ["LLM_API_KEY"], "api_base": os.environ["LLM_BASE"]},
        model_id=f"litellm_proxy/{os.environ.get('LLM_MODEL', 'claude-sonnet')}",
    )

    # Single threaded on purpose. An earlier version polled Telegram on a
    # background thread and fed turns through a queue; the agent then hung
    # before issuing its first MCP request every time, while the identical code
    # as a plain script worked. Polling long-polls for 25s and the agent runs
    # between polls, so nothing is lost by doing both on one thread - and the
    # design already forbids two turns at once.
    with MCPClient(transport) as mcp:
        tools = mcp.list_tools_sync()
        print(f"[{len(tools)} tools from the MCP server]", flush=True)
        agent = Agent(model=model, tools=tools, system_prompt=PLAYBOOK)

        while True:
            try:
                for text in tg.poll():
                    print(f"[turn] {text}", flush=True)
                    tg.send("…on it")
                    try:
                        tg.send(str(agent(text)))
                    except Exception as e:
                        traceback.print_exc()
                        tg.send(f"The agent failed on that turn: {type(e).__name__}: {e}")
            except Exception:
                traceback.print_exc()


if __name__ == "__main__":
    main()
