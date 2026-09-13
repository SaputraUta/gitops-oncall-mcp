"""Long-running on-call agent: one conversation, fed from Telegram.

The agent object holds the conversation, so a proposal made in one turn is
still live when the human replies in the next. That is the whole reason this
is a process rather than a series of one-shot runs.
"""

from __future__ import annotations

import os
import queue
import threading
import traceback

import httpx2
from mcp.client.streamable_http import streamable_http_client
from strands import Agent
from strands.models.litellm import LiteLLMModel
from strands.tools.mcp import MCPClient

from agent.oncall import PLAYBOOK
from agent.telegram import Telegram

_turns: queue.Queue[str] = queue.Queue()


def _poll_telegram(tg: Telegram) -> None:
    while True:
        try:
            for text in tg.poll():
                _turns.put(text)
        except Exception:
            traceback.print_exc()


def main() -> None:
    tg = Telegram.from_env()

    def transport():
        return streamable_http_client(
            os.environ["MCP_URL"],
            http_client=httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {os.environ['MCP_BEARER_TOKEN']}"}
            ),
        )

    model = LiteLLMModel(
        client_args={"api_key": os.environ["LLM_API_KEY"], "api_base": os.environ["LLM_BASE"]},
        model_id=f"litellm_proxy/{os.environ.get('LLM_MODEL', 'claude-sonnet')}",
    )

    threading.Thread(target=_poll_telegram, args=(tg,), daemon=True).start()

    with MCPClient(transport) as mcp:
        tools = mcp.list_tools_sync()
        print(f"[{len(tools)} tools from the MCP server]", flush=True)
        agent = Agent(model=model, tools=tools, system_prompt=PLAYBOOK)
        tg.send(f"On-call agent is up with {len(tools)} tools.")

        while True:
            text = _turns.get()
            print(f"[turn] {text}", flush=True)
            # A turn takes tens of seconds. Without this the chat looks dead,
            # and the human sends the question again.
            tg.send("…on it")
            try:
                tg.send(str(agent(text)))
            except Exception as e:
                traceback.print_exc()
                tg.send(f"The agent failed on that turn: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()