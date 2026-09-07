import asyncio
import json
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from fich_mcp.cli import configure_claude, doctor, launch_tui, main
from fich_mcp.security import FichError, Paths
from fich_mcp.server import bounded_call


def test_init_decline_no_password(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    password = Mock(side_effect=AssertionError("must not prompt"))
    monkeypatch.setattr("getpass.getpass", password)
    assert main(["init"]) == 1
    assert not password.called
    assert "HTTP" in capsys.readouterr().err


def test_doctor_without_auth(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert main(["doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["authentication"] == "authentication_required"


def test_doctor_reports_fts5_unavailable_when_probe_fails(monkeypatch, tmp_path):
    connection = Mock()
    connection.execute.side_effect = sqlite3.OperationalError("no such module: fts5")
    monkeypatch.setattr("fich_mcp.cli.sqlite3.connect", Mock(return_value=connection))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    report = doctor(Paths.default())

    assert report["fts5"] is False
    connection.close.assert_called_once_with()


def test_configure_matching_and_conflict(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    run = Mock(
        return_value=Mock(
            returncode=0,
            stdout="Scope: User config\nType: stdio\nCommand: /bin/fich-mcp\nArgs: serve\n",
            stderr="",
        )
    )
    assert configure_claude(run) == "already_configured"
    assert run.call_count == 1
    run.return_value.stdout = "Command: /evil\nArgs: serve\n"
    with pytest.raises(FichError, match="claude_configuration_conflict"):
        configure_claude(run)


def test_configure_add(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    run = Mock(
        side_effect=[
            Mock(returncode=1, stdout="", stderr="No MCP server found with name"),
            Mock(returncode=0, stdout="", stderr=""),
        ]
    )
    assert configure_claude(run) == "configured"
    assert run.call_args.args[0] == [
        "/bin/claude",
        "mcp",
        "add",
        "--scope",
        "user",
        "--transport",
        "stdio",
        "fich",
        "--",
        "/bin/fich-mcp",
        "serve",
    ]


def test_public_cli_help_hides_internal_rpc_command():
    result = subprocess.run(
        [sys.executable, "-m", "fich_mcp", "--help"], capture_output=True, text=True, check=True
    )
    assert "_rpc" not in result.stdout
    assert "==SUPPRESS==" not in result.stdout


def test_launch_tui_execs_menu_script():
    calls = []
    launch_tui(execv=lambda program, args: calls.append((program, args)))
    assert calls[0][0] == "bash"
    assert calls[0][1][0] == "bash"
    assert calls[0][1][1].endswith("scripts/fich-menu.sh")


def test_launch_tui_missing_script(monkeypatch):
    monkeypatch.setattr("fich_mcp.cli.Path.is_file", lambda self: False)
    with pytest.raises(FichError, match="tui_not_found"):
        launch_tui(execv=Mock(side_effect=AssertionError("must not exec")))


def test_bounded_auth_error(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert bounded_call("list_courses", {})["errors"][0]["code"] == "authentication_required"


def test_actual_stdio_initialize_tools_and_call(tmp_path):
    async def run():
        import os
        import sys

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "fich_mcp", "serve"],
            env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "PYTHONPATH": "src"},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                result = await client.list_tools()
                assert {t.name for t in result.tools} == {
                    "list_courses",
                    "get_course_contents",
                    "get_announcements",
                    "get_upcoming",
                    "get_changes",
                    "search_content",
                    "read_document",
                    "sync",
                }
                result = await client.call_tool("list_courses", {})
                assert "authentication_required" in str(result)

    asyncio.run(run())
