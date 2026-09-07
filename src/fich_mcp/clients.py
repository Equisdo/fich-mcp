"""Register the ``fich-mcp serve`` stdio command with local MCP clients.

Each configurator is idempotent and conflict-safe: it never overwrites an
existing ``fich`` entry that points somewhere else. Three outcomes only,
never collapsed into two:

- ``"configured"``       — no entry existed; one was created.
- ``"already_configured"`` — an entry existed and matched exactly.
- raises ``FichError``   — an entry existed and did *not* match (conflict),
  or the client/executable could not be inspected.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .security import FichError, atomic_write

EXPECTED_ARGS = ["serve"]


def _executable():
    executable = shutil.which("fich-mcp")
    if not executable:
        raise FichError("executable_not_found")
    return executable


def _claude_desktop_config_path() -> Path:
    """Claude Desktop's config file for the current platform.

    macOS and Windows use their own per-app config directories; every other
    platform (Linux, BSD, ...) follows the XDG base directory spec.
    """
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Claude" / "claude_desktop_config.json"
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "Claude"
        / "claude_desktop_config.json"
    )


def configure_claude_code(run=subprocess.run):
    """Register with Claude Code via ``claude mcp add`` (user scope, stdio)."""
    executable = _executable()
    claude = shutil.which("claude")
    if not claude:
        raise FichError("executable_not_found")
    result = run([claude, "mcp", "get", "fich"], capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        lines = {
            k.strip(): v.strip()
            for line in result.stdout.splitlines()
            if ":" in line
            for k, v in [line.split(":", 1)]
        }
        if (
            lines.get("Command") == executable
            and lines.get("Args") == "serve"
            and lines.get("Type") == "stdio"
            and lines.get("Scope", "").startswith("User")
        ):
            return "already_configured"
        raise FichError("claude_configuration_conflict")
    # Wording has changed between claude CLI versions ("No MCP server found"
    # vs. "No MCP server named ..."); match the stable substring, not the
    # full sentence, so client shell one-day drift is what breaks.
    if "No MCP server" not in result.stderr + result.stdout:
        raise FichError("claude_inspection_failed")
    result = run(
        [
            claude,
            "mcp",
            "add",
            "--scope",
            "user",
            "--transport",
            "stdio",
            "fich",
            "--",
            executable,
            "serve",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise FichError("claude_configuration_failed")
    return "configured"


def configure_codex(run=subprocess.run):
    """Register with Codex CLI via ``codex mcp add``.

    This same ``~/.codex/config.toml`` is shared by ChatGPT desktop and the
    Codex IDE extension, so one registration covers all three.
    """
    executable = _executable()
    codex = shutil.which("codex")
    if not codex:
        raise FichError("executable_not_found")
    result = run([codex, "mcp", "list", "--json"], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise FichError("codex_inspection_failed")
    try:
        servers = json.loads(result.stdout)
    except ValueError as exc:
        raise FichError("codex_inspection_failed") from exc

    existing = None
    if isinstance(servers, dict):
        existing = servers.get("fich")
    elif isinstance(servers, list):
        existing = next(
            (item for item in servers if isinstance(item, dict) and item.get("name") == "fich"),
            None,
        )
    else:
        raise FichError("codex_inspection_failed")

    if existing is not None:
        transport = existing.get("transport") or {}
        if (
            transport.get("type") == "stdio"
            and transport.get("command") == executable
            and transport.get("args") == EXPECTED_ARGS
        ):
            return "already_configured"
        raise FichError("codex_configuration_conflict")

    result = run(
        [codex, "mcp", "add", "fich", "--", executable, "serve"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode:
        raise FichError("codex_configuration_failed")
    return "configured"


def configure_claude_desktop(config_path=None):
    """Merge a ``fich`` entry into Claude Desktop's ``mcpServers``.

    Preserves every other key already in the file; refuses to touch it if
    it is not a JSON object, or if ``mcpServers`` is present but malformed.
    """
    executable = _executable()
    path = config_path or _claude_desktop_config_path()

    if path.exists():
        if path.is_symlink():
            raise FichError("unsafe_storage")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FichError("claude_desktop_inspection_failed") from exc
        if not isinstance(data, dict):
            raise FichError("claude_desktop_inspection_failed")
    else:
        data = {}

    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise FichError("claude_desktop_inspection_failed")

    existing = servers.get("fich")
    if existing is not None:
        if existing.get("command") == executable and existing.get("args") == EXPECTED_ARGS:
            return "already_configured"
        raise FichError("claude_desktop_configuration_conflict")

    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())

    servers["fich"] = {"command": executable, "args": list(EXPECTED_ARGS)}
    data["mcpServers"] = servers
    atomic_write(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return "configured"


def configure_all(claude_run=subprocess.run, codex_run=subprocess.run, desktop_path=None):
    """Run all three configurators; one failing does not stop the others."""
    results = {}
    for name, action in (
        ("claude_code", lambda: configure_claude_code(claude_run)),
        ("codex", lambda: configure_codex(codex_run)),
        ("claude_desktop", lambda: configure_claude_desktop(desktop_path)),
    ):
        try:
            results[name] = action()
        except FichError as exc:
            results[name] = exc.code
    return results
