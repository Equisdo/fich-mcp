import time
from datetime import datetime
from urllib.parse import parse_qs

import httpx
import pytest

from fich_mcp.remote import ALLOWLIST, Moodle
from fich_mcp.security import Account, FichError
from fich_mcp.service import TZ, Service, date_range, visible_text
from fich_mcp.store import Store


@pytest.fixture
def service(tmp_path):
    responses = {
        "core_webservice_get_site_info": {"userid": 7},
        "core_enrol_get_users_courses": [{"id": 1, "fullname": "Álgebra", "shortname": "ALG"}],
        "core_course_get_contents": [
            {
                "id": 2,
                "name": "Unidad",
                "summary": "<p>Descripción</p>",
                "modules": [
                    {
                        "id": 3,
                        "name": "Guía",
                        "modname": "resource",
                        "description": "<b>Álgebra lineal</b>",
                        "contents": [],
                    }
                ],
            }
        ],
        "mod_forum_get_forums_by_courses": [],
        "mod_assign_get_assignments": {
            "courses": [
                {"id": 1, "assignments": [{"id": 4, "name": "Trabajo", "duedate": 2000000000}]}
            ],
            "warnings": [],
        },
        "mod_assign_get_submission_status": {
            "lastattempt": {"submission": {"status": "submitted"}, "extensionduedate": 2000000100}
        },
        "core_group_get_course_user_groups": {"groups": []},
        "core_calendar_get_calendar_events": {
            "events": [{"id": 8, "name": "Clase", "timestart": 2000000000, "courseid": 1}],
            "warnings": [],
        },
    }

    def handler(r):
        function = parse_qs(r.content.decode())["wsfunction"][0]
        result = responses[function]
        if isinstance(result, Exception):
            raise result
        return httpx.Response(200, json=result)

    remote = Moodle(Account("secret", 7, list(ALLOWLIST), True, True), httpx.MockTransport(handler))
    store = Store(tmp_path / "cache")
    service = Service(store, remote)
    service.refresh_courses()
    store.select([1])
    yield service, responses
    store.close()
    remote.close()


def test_first_sync_override_and_unknown(service):
    s, r = service
    result = s.sync(index=False)
    assert result["errors"] == []
    assert s.store.changes(0) == []
    assignments = s.store.items(source="assignments")
    assert assignments[0]["due"] == 2000000100
    assert assignments[0]["submission_status"] == "submitted"
    r["mod_assign_get_submission_status"] = {"lastattempt": {}}
    s.sync(index=False)
    assert s.store.items(source="assignments")[0]["submission_status"] == "unknown"
    assert s.store.items(source="calendar")[0]["kind"] == "event"


def test_partial_no_delete_and_source_freshness(service):
    s, r = service
    s.sync(index=False)
    before = next(f for f in s.store.freshness() if f["source"] == "contents")["last_success"]
    r["core_course_get_contents"] = httpx.ConnectError("secret")
    result = s.sync(index=False)
    assert result["errors"]
    assert s.store.items(source="contents")
    assert (
        next(f for f in s.store.freshness() if f["source"] == "contents")["last_success"] == before
    )
    assert (
        next(f for f in s.store.freshness() if f["source"] == "assignments")["last_success"]
        >= before
    )
    assert "secret" not in str(result)


def test_course_revocation(service):
    s, r = service
    s.sync(index=False)
    r["core_course_get_contents"] = {"exception": "x", "errorcode": "requireloginerror"}
    s.sync(index=False)
    assert s.store.items() == []
    assert s.store.search("algebra") == []


def test_token_revocation_not_refreshed(service):
    s, r = service
    s.sync(index=False)
    r["core_webservice_get_site_info"] = {"exception": "x", "errorcode": "invalidtoken"}
    with pytest.raises(FichError, match="authentication_required"):
        s.sync(index=False)


def test_bounded_resume(service):
    s, r = service
    result = s.sync(index=False, budget=0)
    assert result["continuation"]
    assert s.sync(index=False)["errors"] == []


def test_text_and_time():
    assert (
        visible_text(
            '<p>Hello</p><script>secret()</script><style>x</style><img src="http://evil">world'
        )
        == "Hello world"
    )
    start, end = date_range("2026-09-06", "2026-09-07")
    assert end - start == 86400
    assert time.gmtime(start).tm_hour == 3
    with pytest.raises(FichError):
        date_range("2026-01-01", "2028-01-01")


def test_requested_calendar_window_refreshes_and_reports_coverage(service):
    """A short fresh calendar cache cannot satisfy a wider upcoming query."""
    from fich_mcp.api import dispatch

    synced, responses = service
    now = time.time()
    short_start = datetime.fromtimestamp(now, TZ).date().isoformat()
    short_end = datetime.fromtimestamp(now + 2 * 86400, TZ).date().isoformat()
    requested_end = datetime.fromtimestamp(now + 90 * 86400, TZ).date().isoformat()
    far_event = int(now + 60 * 86400)
    calls = []
    original_call = synced.remote.call

    def capture(function, **params):
        if function == "core_calendar_get_calendar_events":
            calls.append(params["options"])
            responses[function] = {
                "events": [{"id": 90, "name": "Far event", "timestart": far_event, "courseid": 1}],
                "warnings": [],
            }
        return original_call(function, **params)

    synced.remote.call = capture
    synced.sync(index=False, calendar_window=date_range(short_start, short_end))
    result = dispatch(
        synced, "get_upcoming", {"course": 1, "start": short_start, "end": requested_end}
    )

    assert len(calls) == 2
    assert calls[-1]["timestart"] <= far_event <= calls[-1]["timeend"]
    assert [item["title"] for item in result["data"]] == ["Far event"]
    coverage = result["coverage"]["calendar_window"]["courses"]["1"]
    assert coverage["requested"] == {
        "start": int(date_range(short_start, requested_end)[0]),
        "end": int(date_range(short_start, requested_end)[1]),
    }
    assert coverage["fresh"] is True
