"""CLI and MCP process-boundary behavior without touching user configuration."""

import json
from contextlib import contextmanager
from unittest.mock import Mock

from fich_mcp import cli, server


def test_server_registration_serve_and_timeout(monkeypatch):
    created = server.create_server()
    assert set(created._tool_manager._tools) == {
        "list_courses",
        "get_course_contents",
        "get_announcements",
        "get_upcoming",
        "get_changes",
        "search_content",
        "read_document",
        "sync",
    }
    fake_server = Mock()
    monkeypatch.setattr(server, "create_server", lambda: fake_server)
    server.serve()
    fake_server.run.assert_called_once_with(transport="stdio")

    process = Mock()
    process.pid = 123
    process.communicate.side_effect = [__import__("subprocess").TimeoutExpired("rpc", 1), ("", "")]
    process.poll.return_value = 0
    monkeypatch.setattr(server.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(server.os, "killpg", Mock(), raising=False)
    monkeypatch.setattr(server, "windows_job", lambda pid: Mock())
    result = server.bounded_call("list_courses", {}, timeout=0.01)
    assert result["errors"] == [{"code": "budget_exhausted"}]
    assert result["continuation"] is True


def test_cli_courses_sync_and_operational_error(monkeypatch, capsys):
    class FakeStore:
        def courses(self, selected=False):
            return [{"id": 1, "fullname": "A", "shortname": "A"}]

    class FakeService:
        store = FakeStore()

        def refresh_courses(self):
            return self.store.courses()

        def sync(self, course=None, **kwargs):
            return {"errors": [], "continuation": False, "course": course, "kwargs": kwargs}

    @contextmanager
    def application():
        yield FakeService()

    monkeypatch.setattr(cli, "application", application)
    monkeypatch.setattr(cli, "aliases", lambda: {})
    assert cli.main(["courses"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == 1
    assert cli.main(["sync", "--course", "1", "--force-ocr"]) == 0
    assert json.loads(capsys.readouterr().out)["kwargs"]["force_ocr"] is True

    @contextmanager
    def broken_application():
        raise OSError("private detail")
        yield

    monkeypatch.setattr(cli, "application", broken_application)
    assert cli.main(["courses"]) == 1
    assert "operation_failed" in capsys.readouterr().err
