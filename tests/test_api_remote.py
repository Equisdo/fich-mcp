"""In-process boundaries for the public dispatcher and Moodle response shaping."""

import io
import json
import sys
from contextlib import contextmanager

import httpx
import pytest

from fich_mcp import api
from fich_mcp.api import dispatch
from fich_mcp.remote import Moodle, form_values
from fich_mcp.security import Account, FichError, Paths
from fich_mcp.store import Store


class LocalService:
    """A local store-backed service used to isolate API argument handling."""

    def __init__(self, store):
        self.store = store
        self.calls = []

    def ensure_fresh(self, course=None, *, calendar_window=None):
        self.calls.append((course, calendar_window))
        return {"errors": [], "continuation": False}

    def envelope(self, data, errors=None, continuation=False, **extra):
        return {
            "data": data,
            "errors": errors or [],
            "continuation": continuation,
            "coverage": extra,
        }

    def sync(self, course, **kwargs):
        return {"data": [], "course": course, **kwargs}


@pytest.fixture
def local_service(tmp_path):
    store = Store(tmp_path / "cache")
    store.set_courses([{"id": 1, "fullname": "Algebra", "shortname": "ALG"}])
    store.select([1])
    store.snapshot(
        1,
        "contents",
        [
            {
                "id": "note",
                "course": 1,
                "kind": "description",
                "title": "Notes",
                "text": "Linear algebra",
                "url": "http://e-fich.unl.edu.ar/moodle/course/view.php?id=1",
            },
            {
                "id": "event",
                "course": 1,
                "kind": "event",
                "title": "Exam",
                "due": 1_770_000_000,
                "url": "http://e-fich.unl.edu.ar/moodle/calendar/view.php",
            },
        ],
    )
    store.snapshot(
        1,
        "forums",
        [
            {
                "id": "post",
                "course": 1,
                "kind": "forum",
                "announcement": True,
                "title": "Announcement",
                "remote_created": 1_700_000_000,
                "url": "http://e-fich.unl.edu.ar/moodle/mod/forum",
            }
        ],
    )
    service = LocalService(store)
    yield service
    store.close()


def test_dispatches_local_inventory_and_validates_inputs(local_service):
    service = local_service
    assert dispatch(service, "list_courses", {})["data"][0]["id"] == 1
    assert dispatch(service, "get_course_contents", {"course": "ALG"})["data"][0]["id"] == "note"
    assert (
        dispatch(service, "get_announcements", {"since": "2023-01-01"})["data"][0]["id"] == "post"
    )
    upcoming = dispatch(
        service,
        "get_upcoming",
        {"course": 1, "start": "2026-01-01", "end": "2026-12-31"},
    )
    assert upcoming["data"][0]["title"] == "Exam"
    assert upcoming["coverage"]["calendar_window"] == service.calls[-1][1]
    assert dispatch(service, "search_content", {"query": "linear"})["data"][0]["title"] == "Notes"
    assert dispatch(service, "sync", {"force_ocr": True})["force_ocr"] is True
    with pytest.raises(FichError, match="invalid_pagination"):
        dispatch(service, "list_courses", {"limit": 0})
    with pytest.raises(FichError, match="unsupported_operation"):
        dispatch(service, "unknown", {})


def test_aliases_worker_and_application_cleanup(monkeypatch, tmp_path, capsys):
    paths = Paths(tmp_path / "config", tmp_path / "cache")
    paths.config.mkdir(parents=True)
    (paths.config / "aliases.toml").write_text("[aliases]\nmath = 'ALG'\n")
    assert api.aliases(paths) == {"math": "ALG"}
    (paths.config / "aliases.toml").write_text("[aliases]\nmath = 1\n")
    with pytest.raises(FichError, match="invalid_aliases"):
        api.aliases(paths)

    class FakeRemote:
        def __init__(self, account):
            self.account = account
            self.closed = False

        def close(self):
            self.closed = True

    created = {}
    monkeypatch.setattr(
        api, "Moodle", lambda account: created.setdefault("remote", FakeRemote(account))
    )
    monkeypatch.setattr(api, "Store", lambda root: created.setdefault("store", Store(root)))
    account = Account("token", 3, [], False, True)
    monkeypatch.setattr(paths, "load", lambda: account)
    with api.application(paths) as service:
        assert service.store.root == paths.user_cache(3)
    assert created["remote"].closed is True
    assert created["store"].db is not None

    @contextmanager
    def failing_application():
        raise FichError("authentication_required")
        yield

    monkeypatch.setattr(api, "application", failing_application)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"operation": "list_courses", "args": {}}))
    )
    api.worker()
    assert json.loads(capsys.readouterr().out)["errors"] == [{"code": "authentication_required"}]


def test_worker_returns_public_dispatch_result_and_hides_unexpected_errors(monkeypatch, capsys):
    class Service:
        store = object()

    @contextmanager
    def working_application():
        yield Service()

    monkeypatch.setattr(api, "application", working_application)
    monkeypatch.setattr(api, "aliases", lambda: {"math": "ALG"})
    monkeypatch.setattr(
        api,
        "dispatch",
        lambda service, operation, args, aliases: {
            "data": [operation, args, aliases],
            "errors": [],
            "freshness": [],
            "coverage": {},
            "continuation": False,
        },
    )
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"operation": "list_courses", "args": {}}))
    )
    api.worker()
    assert json.loads(capsys.readouterr().out)["data"][0] == "list_courses"

    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    api.worker()
    assert json.loads(capsys.readouterr().out)["errors"] == [{"code": "operation_failed"}]


def test_remote_form_timeout_malformed_and_download():
    assert form_values({"x": [True, {"y": "z"}]}) == {"x[0]": "1", "x[1][y]": "z"}
    account = Account("token", 1, ["core_course_get_contents"], True, True)
    timeout = Moodle(
        account,
        httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.TimeoutException("x"))),
    )
    with pytest.raises(FichError, match="network_unavailable"):
        timeout.call("core_course_get_contents", courseid=1)
    timeout.close()

    malformed = Moodle(
        account, httpx.MockTransport(lambda request: httpx.Response(200, content=b"not json"))
    )
    with pytest.raises(FichError, match="network_unavailable"):
        malformed.call("core_course_get_contents", courseid=1)
    malformed.close()

    def download_handler(request):
        assert request.url.params["token"] == "token"
        return httpx.Response(
            200, content=b"%PDF-1.4\nbody", headers={"content-type": "application/pdf"}
        )

    downloader = Moodle(account, httpx.MockTransport(download_handler))
    assert (
        downloader.download("http://e-fich.unl.edu.ar/moodle/webservice/pluginfile.php/1/file.pdf")
        == b"%PDF-1.4\nbody"
    )
    downloader.close()


def test_login_rejects_bad_site_and_download_rejects_mime():
    responses = iter(
        [{"token": "safe"}, {"sitename": "Elsewhere", "siteurl": "http://bad", "userid": 1}]
    )
    remote = Moodle(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses)))
    )
    with pytest.raises(FichError, match="site_identity_mismatch"):
        remote.login("u", "p", accepted=True)
    remote.close()

    account = Account("token", 1, [], True, True)
    remote = Moodle(
        account,
        httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x", headers={"content-type": "text/html"})
        ),
    )
    with pytest.raises(FichError, match="not_pdf"):
        remote.download("http://e-fich.unl.edu.ar/moodle/webservice/pluginfile.php/1/file.pdf")
    remote.close()
