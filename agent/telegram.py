"""Telegram as the approval channel.

Long polling, not a webhook: the agent calls out to Telegram, so nothing has to
reach the cluster from the internet and no port is exposed. The cluster keeps
zero inbound.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator

import httpx

# Telegram rejects anything longer, and a triage summary routinely exceeds it.
_MAX_CHARS = 4000
_MARKDOWN = re.compile(r"\*\*|__|`")


class Telegram:
    def __init__(self, token: str, allowed_ids: set[int]) -> None:
        self._c = httpx.Client(base_url=f"https://api.telegram.org/bot{token}", timeout=40)
        self._allowed = allowed_ids
        self._offset: int | None = None
        # In a private chat the chat id equals the user id, so the bot can reach
        # the first allowed user before anyone has spoken to it. An alert can
        # fire on a fresh pod, and it would otherwise have nowhere to report.
        self._chat_id: int | None = min(allowed_ids) if allowed_ids else None

    @classmethod
    def from_env(cls) -> Telegram:
        ids = {
            int(x) for x in os.environ["TELEGRAM_ALLOWED_USER_IDS"].split(",") if x.strip()
        }
        if not ids:
            raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS is empty; nobody could approve anything")
        return cls(os.environ["TELEGRAM_BOT_TOKEN"], ids)

    def send(self, text: str) -> None:
        """Send to the last chat that talked to us, or to the first allowed user.

        Markdown is stripped rather than rendered. Asking Telegram to parse it
        means one unbalanced asterisk returns 400 and the message is lost, and
        the text here is written by a model.
        """
        if self._chat_id is None:
            return
        text = _MARKDOWN.sub("", text)
        for i in range(0, len(text), _MAX_CHARS):
            self._c.post("/sendMessage", json={"chat_id": self._chat_id, "text": text[i : i + _MAX_CHARS]})

    def poll(self) -> Iterator[str]:
        """Yield message text from allowed senders, one long-poll at a time.

        Identity is checked on the numeric user id, never the username: a
        username can be changed and reused, so an allowlist of names is an
        allowlist of whoever claims them today.
        """
        r = self._c.get("/getUpdates", params={"timeout": 25, "offset": self._offset})
        r.raise_for_status()
        for update in r.json().get("result", []):
            self._offset = update["update_id"] + 1
            msg = update.get("message") or {}
            text, frm = msg.get("text"), msg.get("from", {})
            if not text:
                continue
            if frm.get("id") not in self._allowed:
                print(f"[telegram] ignored message from unlisted user {frm.get('id')}")
                continue
            self._chat_id = msg["chat"]["id"]
            yield text