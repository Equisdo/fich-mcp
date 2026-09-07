"""Fixed-site, read-allowlisted Moodle 3.11 REST client."""

import time
import unicodedata

import httpx

from .security import BASE, Account, FichError, clean, download_url

ALLOWLIST = frozenset(
    {
        "core_webservice_get_site_info",
        "core_enrol_get_users_courses",
        "core_course_get_contents",
        "mod_forum_get_forums_by_courses",
        "mod_forum_get_forum_discussions",
        "mod_forum_get_discussion_posts",
        "mod_assign_get_assignments",
        "mod_assign_get_submission_status",
        "core_calendar_get_calendar_events",
        "core_group_get_course_user_groups",
    }
)


def form_values(values, prefix=""):
    result = {}
    for key, value in enumerate(values) if isinstance(values, list) else values.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, (dict, list)):
            result.update(form_values(value, name))
        else:
            result[name] = str(int(value)) if isinstance(value, bool) else str(value)
    return result


class Moodle:
    def __init__(self, account: Account | None = None, transport=None):
        self.account = account
        self.client = httpx.Client(
            transport=transport,
            timeout=8,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "fich-mcp/0.1"},
        )
        self.deadline = None

    def close(self):
        self.client.close()

    def _json(self, path, data):
        remaining = 8 if self.deadline is None else min(8, self.deadline - time.monotonic())
        if remaining <= 0:
            raise FichError("budget_exhausted")
        try:
            with self.client.stream("POST", BASE + path, data=data, timeout=remaining) as response:
                if response.status_code != 200:
                    raise FichError("network_unavailable")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 8_000_000:
                        raise FichError("response_too_large")
                    if self.deadline is not None and time.monotonic() >= self.deadline:
                        raise FichError("budget_exhausted")
                    chunks.append(chunk)
                import json

                value = json.loads(b"".join(chunks))
        except (httpx.HTTPError, ValueError):
            raise FichError("network_unavailable") from None
        if isinstance(value, dict) and ("exception" in value or "error" in value):
            code = value.get("errorcode", "")
            mapped = {
                "invalidtoken": "authentication_required",
                "invalidlogin": "authentication_invalid",
                "accessexception": "unsupported_function",
                "requireloginerror": "course_denied",
                "requireloginexception": "course_denied",
                "nopermissions": "course_denied",
                "servicenotavailable": "mobile_service_unavailable",
                "webserviceaccess": "mobile_service_unavailable",
            }
            raise FichError(mapped.get(code, "remote_error"))
        return value

    def login(self, username, password, *, accepted: bool) -> Account:
        if not accepted:
            raise FichError("http_consent_required")
        result = self._json(
            "/login/token.php",
            {"username": username, "password": password, "service": "moodle_mobile_app"},
        )
        if not isinstance(result, dict) or not result.get("token"):
            raise FichError("authentication_invalid")
        self.account = Account(result["token"], 0, [], False, True)
        site = self.call("core_webservice_get_site_info")
        name = unicodedata.normalize("NFKD", site.get("sitename", "")).casefold()
        if (
            site.get("siteurl", "").rstrip("/") != BASE
            or "fich" not in name
            or "learning" not in name
        ):
            self.account = None
            raise FichError("site_identity_mismatch")
        userid = site.get("userid")
        if not isinstance(userid, int) or userid <= 0:
            raise FichError("site_identity_mismatch")
        self.account.userid = userid
        self.account.functions = [f["name"] for f in site.get("functions", [])]
        self.account.downloadfiles = bool(site.get("downloadfiles"))
        return self.account

    def call(self, function, **params):
        if not self.account:
            raise FichError("authentication_required")
        if not self.account.http_accepted:
            raise FichError("http_consent_required")
        if function not in ALLOWLIST or (
            function != "core_webservice_get_site_info" and function not in self.account.functions
        ):
            raise FichError("unsupported_function")
        result = self._json(
            "/webservice/rest/server.php",
            form_values(
                {
                    "wstoken": self.account.token,
                    "wsfunction": function,
                    "moodlewsrestformat": "json",
                    **params,
                }
            ),
        )
        return clean(result, self.account.token)

    def download(self, reference: str, *, max_bytes=30_000_000) -> bytes:
        if not self.account:
            raise FichError("authentication_required")
        if not self.account.http_accepted:
            raise FichError("http_consent_required")
        if not self.account.downloadfiles:
            raise FichError("downloads_unavailable")
        url = download_url(reference, self.account.token)
        end = min(time.monotonic() + 15, self.deadline or float("inf"))
        if end <= time.monotonic():
            raise FichError("budget_exhausted")
        try:
            with self.client.stream(
                "GET", url, timeout=max(0.1, end - time.monotonic())
            ) as response:
                if response.status_code != 200:
                    raise FichError("download_failed")
                mime = response.headers.get("content-type", "").split(";")[0]
                if mime not in {"application/pdf", "application/octet-stream"}:
                    raise FichError("not_pdf")
                size, chunks = 0, []
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise FichError("download_too_large")
                    if time.monotonic() >= end:
                        raise FichError("budget_exhausted")
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data.startswith(b"%PDF-"):
                    raise FichError("not_pdf")
                return data
        except httpx.HTTPError:
            raise FichError("download_failed") from None
