"""Audit log writes to stderr always, and to file when configured."""

from __future__ import annotations

import json

from gitops_oncall_mcp.audit import AuditLog


def test_emit_writes_to_stderr(capsys):
    log = AuditLog(file_path=None)
    log.emit("test_event", foo="bar", num=42)
    captured = capsys.readouterr()
    line = captured.err.strip()
    record = json.loads(line)
    assert record["event"] == "test_event"
    assert record["foo"] == "bar"
    assert record["num"] == 42
    assert "ts" in record


def test_emit_writes_to_file_when_configured(tmp_path, capsys):
    log_file = tmp_path / "audit.jsonl"
    log = AuditLog(file_path=str(log_file))
    log.emit("proposal_created", proposal_id="abc", tool="rollback_deploy")
    log.emit("action_executed", proposal_id="abc")
    log.close()

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 2
    rec1 = json.loads(lines[0])
    rec2 = json.loads(lines[1])
    assert rec1["event"] == "proposal_created"
    assert rec1["tool"] == "rollback_deploy"
    assert rec2["event"] == "action_executed"

    # stderr also got both
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 2


def test_unwritable_path_falls_back_to_stderr_only(capsys, tmp_path):
    """If AUDIT_LOG_PATH points somewhere unwritable, server should still start."""
    bad = tmp_path / "missing" / "nested" / "audit.jsonl"  # parent doesn't exist
    log = AuditLog(file_path=str(bad))
    # stderr should have a "open failed" event already
    initial_err = capsys.readouterr().err
    assert "audit_sink_open_failed" in initial_err

    # Subsequent emits still work via stderr
    log.emit("hello", x=1)
    err = capsys.readouterr().err
    assert '"event": "hello"' in err
