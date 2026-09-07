import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from fich_mcp.clients import (
    _claude_desktop_config_path,
    configure_all,
    configure_claude_desktop,
    configure_codex,
)
from fich_mcp.security import FichError


def test_configure_codex_add(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    run = Mock(
        side_effect=[
            Mock(returncode=0, stdout="[]", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
        ]
    )
    assert configure_codex(run) == "configured"
    assert run.call_args_list[0].args[0] == ["/bin/codex", "mcp", "list", "--json"]
    assert run.call_args_list[1].args[0] == [
        "/bin/codex",
        "mcp",
        "add",
        "fich",
        "--",
        "/bin/fich-mcp",
        "serve",
    ]


def test_configure_codex_already_configured(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    listing = json.dumps(
        [
            {
                "name": "fich",
                "transport": {"type": "stdio", "command": "/bin/fich-mcp", "args": ["serve"]},
            }
        ]
    )
    run = Mock(return_value=Mock(returncode=0, stdout=listing, stderr=""))
    assert configure_codex(run) == "already_configured"
    assert run.call_count == 1


def test_configure_codex_conflict(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    listing = json.dumps(
        [{"name": "fich", "transport": {"type": "stdio", "command": "/evil", "args": ["serve"]}}]
    )
    run = Mock(return_value=Mock(returncode=0, stdout=listing, stderr=""))
    with pytest.raises(FichError, match="codex_configuration_conflict"):
        configure_codex(run)


def test_configure_codex_missing_executable(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(FichError, match="executable_not_found"):
        configure_codex(Mock())


def test_configure_claude_desktop_creates_entry(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"preferences": {"theme": "dark"}}))

    assert configure_claude_desktop(config) == "configured"

    data = json.loads(config.read_text())
    assert data["preferences"] == {"theme": "dark"}
    assert data["mcpServers"]["fich"] == {"command": "/bin/fich-mcp", "args": ["serve"]}
    assert config.with_suffix(".json.bak").exists()


def test_configure_claude_desktop_no_existing_file(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    config = tmp_path / "nested" / "claude_desktop_config.json"

    assert configure_claude_desktop(config) == "configured"
    assert json.loads(config.read_text())["mcpServers"]["fich"]["command"] == "/bin/fich-mcp"


def test_configure_claude_desktop_already_configured(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps({"mcpServers": {"fich": {"command": "/bin/fich-mcp", "args": ["serve"]}}})
    )
    assert configure_claude_desktop(config) == "already_configured"


def test_configure_claude_desktop_conflict(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {"fich": {"command": "/evil", "args": ["serve"]}}}))
    with pytest.raises(FichError, match="claude_desktop_configuration_conflict"):
        configure_claude_desktop(config)


def test_configure_claude_desktop_rejects_symlink(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    real = tmp_path / "real.json"
    real.write_text("{}")
    link = tmp_path / "claude_desktop_config.json"
    link.symlink_to(real)
    with pytest.raises(FichError, match="unsafe_storage"):
        configure_claude_desktop(link)


def test_configure_all_collects_per_client_status(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    claude_run = Mock(
        side_effect=[
            Mock(returncode=1, stdout="", stderr="No MCP server found with name"),
            Mock(returncode=0, stdout="", stderr=""),
        ]
    )
    codex_run = Mock(return_value=Mock(returncode=1, stdout="", stderr="boom"))
    desktop_path = tmp_path / "claude_desktop_config.json"

    results = configure_all(claude_run=claude_run, codex_run=codex_run, desktop_path=desktop_path)

    assert results["claude_code"] == "configured"
    assert results["codex"] == "codex_inspection_failed"
    assert results["claude_desktop"] == "configured"


def test_claude_desktop_config_path_macos(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    path = _claude_desktop_config_path()

    assert path == tmp_path / "Library" / "Application Support" / "Claude" / (
        "claude_desktop_config.json"
    )


def test_claude_desktop_config_path_windows(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    path = _claude_desktop_config_path()

    assert path == tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"


def test_claude_desktop_config_path_windows_without_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    path = _claude_desktop_config_path()

    assert path == tmp_path / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"


def test_claude_desktop_config_path_linux_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    path = _claude_desktop_config_path()

    assert path == tmp_path / "xdg" / "Claude" / "claude_desktop_config.json"


def test_claude_desktop_config_path_linux_defaults_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    path = _claude_desktop_config_path()

    assert path == tmp_path / ".config" / "Claude" / "claude_desktop_config.json"
