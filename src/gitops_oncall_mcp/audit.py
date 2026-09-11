"""Audit log writer.

Emits structured JSON-lines events for every destructive action and the
proposal/confirmation lifecycle around it. Two sinks:

- stderr — always, so systemd/journalctl/container runtimes capture it
- file   — optional, set AUDIT_LOG_PATH; opened append-only

Events are best-effort: a sink failure logs to stderr but never blocks the
calling tool.
"""

from __future__ import annotations

import json
import sys
import time
from threading import Lock
from typing import Any, TextIO


class AuditLog:
    def __init__(self, file_path: str | None = None):
        self._file_path = file_path
        self._file: TextIO | None = None
        self._lock = Lock()
        if file_path:
            try:
                # buffering=1 = line-buffered; each event hits disk on newline
                self._file = open(file_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
            except OSError as e:
                print(
                    json.dumps(
                        {
                            "ts": _iso_now(),
                            "event": "audit_sink_open_failed",
                            "path": file_path,
                            "error": str(e),
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                self._file = None

    def emit(self, event: str, **fields: Any) -> None:
        """Write one structured event to all sinks."""
        record = {"ts": _iso_now(), "event": event, **fields}
        line = json.dumps(record, default=str)
        with self._lock:
            # stderr — always
            print(line, file=sys.stderr, flush=True)
            # file — best-effort
            if self._file is not None:
                try:
                    self._file.write(line + "\n")
                except OSError as e:
                    print(
                        json.dumps(
                            {
                                "ts": _iso_now(),
                                "event": "audit_sink_write_failed",
                                "path": self._file_path,
                                "error": str(e),
                            }
                        ),
                        file=sys.stderr,
                        flush=True,
                    )

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                finally:
                    self._file = None


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
