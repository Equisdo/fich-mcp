"""Terminal-only authentication and thin CLI over the shared service."""

import argparse
import getpass
import json
import shutil
import sqlite3
import subprocess
import sys
import time

from .api import aliases, application
from .remote import ALLOWLIST, Moodle
from .security import FichError, Paths, atomic_write
from .service import Service
from .store import Store, resolve_course


def configure_claude(run=subprocess.run):
    executable = shutil.which("fich-mcp")
    claude = shutil.which("claude")
    if not executable or not claude:
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
    if "No MCP server found" not in result.stderr + result.stdout:
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


def select_courses(service, paths):
    courses = service.store.courses()
    now = time.time()
    for c in courses:
        recent = (not c.get("enddate") or c["enddate"] >= now - 180 * 86400) and c.get(
            "startdate", 0
        ) <= now
        print(
            f"{c['id']}: {c['fullname']} ({c['shortname']})"
            + (" [suggested: current/recent]" if recent else "")
        )
    answer = input(
        "Select course IDs or exact names/aliases, comma-separated (empty selects none): "
    )
    ids = [
        resolve_course(v.strip(), courses, aliases(paths)) for v in answer.split(",") if v.strip()
    ]
    service.store.select(ids)
    print(json.dumps({"selected": ids}))


def initialize(paths):
    print(
        "WARNING: FICH uses HTTP, not HTTPS. Your username, password and token can be intercepted or modified in transit. This tool cannot protect against HTTP network attackers.",
        file=sys.stderr,
    )
    if input("Accept this HTTP risk? Type yes to continue: ").strip().casefold() != "yes":
        raise FichError("http_consent_required")
    username = input("Username: ")
    password = getpass.getpass("Password: ")
    remote = Moodle()
    try:
        account = remote.login(username, password, accepted=True)
        password = None
        paths.save(account)
        if not (paths.config / "aliases.toml").exists():
            atomic_write(
                paths.config / "aliases.toml",
                b"# Map aliases to exact course names or shortnames.\n[aliases]\n",
            )
        store = Store(paths.user_cache(account.userid))
        try:
            service = Service(store, remote)
            service.refresh_courses()
            select_courses(service, paths)
        finally:
            store.close()
    finally:
        remote.close()


def doctor(paths):
    result = {
        "authentication": "authentication_required",
        "fts5": False,
        "ocr": {},
        "cache": "not_initialized",
    }
    connection = sqlite3.connect(":memory:")
    try:
        try:
            connection.execute("CREATE VIRTUAL TABLE test USING fts5(text)")
        except sqlite3.OperationalError:
            pass
        else:
            result["fts5"] = True
    finally:
        connection.close()
    result["ocr"]["pdftoppm"] = bool(shutil.which("pdftoppm"))
    result["ocr"]["tesseract"] = bool(shutil.which("tesseract"))
    if result["ocr"]["tesseract"]:
        try:
            output = subprocess.run(
                ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=5
            )
            result["ocr"]["languages"] = [
                lang for lang in ["spa", "eng"] if lang in output.stdout.splitlines()
            ]
        except (OSError, subprocess.TimeoutExpired):
            result["ocr"]["error"] = "ocr_unavailable"
    try:
        with application(paths) as service:
            service.remote.call("core_webservice_get_site_info")
            result["authentication"] = "valid"
            result["capabilities"] = {
                f: f in service.remote.account.functions for f in sorted(ALLOWLIST)
            }
            result["downloads"] = service.remote.account.downloadfiles
            result["cache"] = "available"
            result["selected_courses"] = len(service.store.courses(selected=True))
    except FichError as exc:
        result["authentication"] = exc.code
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="fich-mcp")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("doctor")
    commands.add_parser("serve")
    # The private worker command is needed by ``server.bounded_call`` but must not
    # advertise an unauthenticated implementation boundary in normal CLI help.
    if argv and argv[0] == "_rpc":
        commands.add_parser("_rpc")
    courses = commands.add_parser("courses")
    courses.add_argument("--select", action="store_true")
    sync = commands.add_parser("sync")
    sync.add_argument("--course")
    sync.add_argument("--force-ocr", action="store_true")
    sync.add_argument("--force-refresh", action="store_true")
    configure = commands.add_parser("configure")
    configure.add_argument("client", choices=["claude"])
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .server import serve

            serve()
        elif args.command == "_rpc":
            from .api import worker

            worker()
        elif args.command == "configure":
            print(configure_claude())
        elif args.command == "doctor":
            print(json.dumps(doctor(Paths.default()), indent=2))
        elif args.command == "init":
            initialize(Paths.default())
        else:
            with application() as service:
                if args.command == "courses":
                    service.refresh_courses()
                    if args.select:
                        select_courses(service, Paths.default())
                    else:
                        print(json.dumps(service.store.courses(), ensure_ascii=False, indent=2))
                else:
                    course = (
                        resolve_course(args.course, service.store.courses(selected=True), aliases())
                        if args.course
                        else None
                    )
                    for batch in range(100):
                        print(
                            f"Sync batch {batch + 1}: metadata then resumable indexing",
                            file=sys.stderr,
                        )
                        result = service.sync(
                            course,
                            force_ocr=args.force_ocr and batch == 0,
                            force_refresh=args.force_refresh and batch == 0,
                        )
                        if not result["continuation"]:
                            break
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    if result["errors"] or result["continuation"]:
                        return 1
        return 0
    except FichError as exc:
        print(
            f"Error: {exc.code}. Run fich-mcp init in a terminal if authentication is required.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("Error: operation_failed. No remote diagnostic text is displayed.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Cancelled.", file=sys.stderr)
        return 130
