"""Who the bearer middleware lets through.

These cover the auth decision only. They do NOT cover the bug that made this
middleware pure ASGI: BaseHTTPMiddleware buffers through call_next and breaks
the long-lived streams MCP rides on, and reverting to it leaves every test here
green. A short StreamingResponse completes immediately and never exercises the
failure; reproducing it faithfully would mean reimplementing the MCP transport
in a fixture, which would be more fragile than the thing it guards.

The regression test for that lives in scripts/smoke.py, which drives a real MCP
client against a running server. Curl cannot stand in for it — curl does not
stream, which is exactly why the broken version returned a clean 401 and looked
correct for a whole morning.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from gitops_oncall_mcp.server import BearerTokenAuth

TOKEN = "s3cret"


async def _stream(request):
    async def chunks():
        for i in range(3):
            yield f"chunk-{i}\n".encode()

    return StreamingResponse(chunks(), media_type="text/plain")


@pytest.fixture
def client() -> TestClient:
    app = Starlette(routes=[Route("/mcp", _stream)])
    app.add_middleware(BearerTokenAuth, expected=TOKEN)
    return TestClient(app)


def test_a_response_body_arrives_whole(client):
    r = client.get("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert r.text == "chunk-0\nchunk-1\nchunk-2\n"


def test_no_token_is_rejected(client):
    assert client.get("/mcp").status_code == 401


def test_wrong_token_is_rejected(client):
    assert client.get("/mcp", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_a_token_in_the_wrong_scheme_is_rejected(client):
    """`Basic <token>` carries the right secret in the wrong envelope."""
    assert client.get("/mcp", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
