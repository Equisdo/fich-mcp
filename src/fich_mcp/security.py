"""Credential boundaries and private application-owned storage."""

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

ORIGIN = "http://e-fich.unl.edu.ar"
BASE = ORIGIN + "/moodle"
SECRET_KEYS = {"token", "wstoken", "password", "privatetoken", "sesskey", "access_token"}


class FichError(Exception):
    """Safe fixed error codes; never include untrusted remote error messages."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise FichError("unsafe_storage")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise FichError("unsafe_storage")
    path.chmod(0o700)
    return path


def atomic_write(path: Path, data: bytes) -> None:
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@dataclass
class Account:
    token: str = field(repr=False)
    userid: int
    functions: list[str]
    downloadfiles: bool
    http_accepted: bool


@dataclass
class Paths:
    config: Path
    cache: Path

    @classmethod
    def default(cls):
        return cls(
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "fich-mcp",
            Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fich-mcp",
        )

    @property
    def account_file(self):
        return self.config / "account.json"

    def save(self, account: Account):
        atomic_write(self.account_file, json.dumps(vars(account)).encode())

    def load(self) -> Account:
        try:
            if self.account_file.is_symlink() or self.account_file.stat().st_mode & 0o077:
                raise FichError("unsafe_storage")
            data = json.loads(self.account_file.read_text())
            account = Account(**data)
            if not account.http_accepted:
                raise FichError("http_consent_required")
            if not isinstance(account.userid, int) or account.userid <= 0 or not account.token:
                raise FichError("authentication_required")
            return account
        except (OSError, ValueError, TypeError):
            raise FichError("authentication_required") from None

    def user_cache(self, userid: int):
        private_dir(self.cache)
        return private_dir(self.cache / str(int(userid)))


def clean_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"}:
            return ""
        hostname = parts.hostname or ""
        netloc = hostname + (f":{parts.port}" if parts.port else "")
        query = urlencode(
            [(k, v) for k, v in parse_qsl(parts.query) if k.casefold() not in SECRET_KEYS]
        )
        return urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    except ValueError:
        return ""


def clean(value, token: str = ""):
    """Remove credential fields/URL parameters recursively, including HTML attributes."""
    if isinstance(value, dict):
        return {k: clean(v, token) for k, v in value.items() if k.casefold() not in SECRET_KEYS}
    if isinstance(value, list):
        return [clean(v, token) for v in value]
    if isinstance(value, str):
        value = re.sub(r'https?://[^\s<>"\']+', lambda m: clean_url(m[0]), value)
        return value.replace(token, "[redacted]") if token else value
    return value


def download_url(value: str, token: str) -> str:
    parts = urlsplit(value)
    decoded = unquote(parts.path)
    if (
        parts.scheme != "http"
        or parts.hostname != "e-fich.unl.edu.ar"
        or parts.port not in {None, 80}
        or parts.username
        or parts.password
        or not decoded.startswith("/moodle/webservice/pluginfile.php/")
        or any(p in {".", ".."} for p in decoded.split("/"))
        or "\\" in decoded
        or "\x00" in decoded
    ):
        raise FichError("unsafe_download_reference")
    safe = urlsplit(clean_url(value))
    query = parse_qsl(safe.query) + [("token", token)]
    return urlunsplit((safe.scheme, safe.netloc, safe.path, urlencode(query), ""))
