"""Run on real Windows/POSIX: no pretend os.name for kernel guarantees."""

import json
import os
import subprocess
import sys

import pytest

from fich_mcp.platforms import exclusive_writer
from fich_mcp.security import Account, FichError, Paths, private_dir


def test_writer_cross_process_and_crash_release(tmp_path):
    root = private_dir(tmp_path / "cuenta con espacios á")
    path = root / "writer.lock"
    code = """
import sys
from fich_mcp.platforms import exclusive_writer
with exclusive_writer(Path(sys.argv[1])):
    print('ready', flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(path)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(FichError, match="sync_busy"):
            with exclusive_writer(path):
                pytest.fail("concurrent writer admitted")
        with exclusive_writer(root / "other-account.lock"):
            pass
    finally:
        process.kill()
        process.communicate(timeout=10)
    with exclusive_writer(path):
        pass
    assert path.exists()  # Never unlink: that can create competing lock inodes.


def test_release_on_exception(tmp_path):
    path = tmp_path / "writer.lock"
    with pytest.raises(RuntimeError):
        with exclusive_writer(path):
            raise RuntimeError()
    with exclusive_writer(path):
        pass


def test_timezone_without_system_database():
    code = """
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, reset_tzpath
reset_tzpath([])
ZoneInfo.clear_cache()
from fich_mcp.service import TZ
assert datetime(2026, 9, 7, tzinfo=TZ).utcoffset() == timedelta(hours=-3)
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=20)


def test_doctor_and_native_menu_subprocess(tmp_path):
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"),
           "XDG_CACHE_HOME": str(tmp_path / "cache"), "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "fich_mcp", "doctor"], env=env,
        capture_output=True, text=True, check=True, timeout=20,
    )
    report = json.loads(result.stdout)
    assert report["authentication"] == "authentication_required"
    assert report["fts5"]
    result = subprocess.run(
        [sys.executable, "-c", "from fich_mcp.tui import run; run()"],
        input="2\n0\n", env=env, capture_output=True, text=True,
        encoding="utf-8", check=True, timeout=20,
    )
    assert "e-FICH" in result.stdout
    assert '"authentication"' in result.stdout
    if os.name == "nt":
        result = subprocess.run(
            [sys.executable, "-m", "fich_mcp", "tui"], input="0\n", env=env,
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
        )
        assert "by @juanmabdu" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows ACLs")
def test_windows_secret_acl_and_unsafe_acl_rejected(tmp_path):
    import win32security as security

    from fich_mcp.platforms import windows_private

    paths = Paths(tmp_path / "config", tmp_path / "cache")
    account = Account("test-secret", 1, [], True, True)
    paths.save(account)
    windows_private(paths.config)
    windows_private(paths.account_file)
    assert paths.load() == account
    paths.save(Account("replacement", 1, [], True, True))
    windows_private(paths.account_file)
    acl = security.ACL()
    acl.AddAccessAllowedAce(security.ACL_REVISION, 0x1F01FF,
                            security.CreateWellKnownSid(security.WinWorldSid))
    security.SetNamedSecurityInfo(
        str(paths.account_file), security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None,
    )
    with pytest.raises(FichError, match="unsafe_storage"):
        paths.load()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects")
def test_windows_job_kills_descendants(tmp_path):
    import win32api
    import win32event

    from fich_mcp.platforms import windows_job

    code = """
import subprocess, sys
sys.stdin.readline()
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
print(child.pid, flush=True)
sys.stdin.read()
"""
    process = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    job = windows_job(process.pid)
    child = None
    try:
        process.stdin.write("start\n")
        process.stdin.flush()
        pid = int(process.stdout.readline())
        child = win32api.OpenProcess(0x00100000, False, pid)
        job.Close()
        process.wait(timeout=10)
        assert win32event.WaitForSingleObject(child, 5000) == 0
    finally:
        job.Close()
        if child is not None:
            child.Close()
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_bounded_output_and_timeout():
    from fich_mcp.windows_pdf import MAX_OUTPUT, capture

    assert capture([sys.executable, "-c", "print('hello')"]).strip() == b"hello"
    with pytest.raises(ValueError, match="output limit"):
        capture([sys.executable, "-c", f"import sys; sys.stdout.buffer.write(b'x'*{MAX_OUTPUT + 1})"])
    with pytest.raises(subprocess.TimeoutExpired):
        capture([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1)
