"""Telegram gets plain text, and only from allowed senders."""

from __future__ import annotations

from agent.telegram import Telegram


def test_markdown_is_stripped_not_rendered(monkeypatch):
    """Telegram renders none of it, and asking it to parse risks a 400 that
    loses the message entirely."""
    sent = []
    tg = Telegram("t", {1})
    monkeypatch.setattr(tg._c, "post", lambda path, json: sent.append(json["text"]))
    tg.send("**KubeMemoryOvercommit** confirmed `firing` and __urgent__")
    assert sent == ["KubeMemoryOvercommit confirmed firing and urgent"]


def test_the_chat_id_defaults_to_the_first_allowed_user():
    """An alert can fire before anyone has messaged the bot."""
    assert Telegram("t", {4242})._chat_id == 4242


def test_a_long_message_is_split():
    sent = []
    tg = Telegram("t", {1})
    tg._c.post = lambda path, json: sent.append(json["text"])
    tg.send("x" * 9000)
    assert len(sent) == 3
    assert sum(len(s) for s in sent) == 9000
