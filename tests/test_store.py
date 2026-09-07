import pytest

from fich_mcp.security import FichError
from fich_mcp.store import Store, literal_query, resolve_course


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "account")
    s.set_courses(
        [
            {"id": 1, "fullname": "Álgebra", "shortname": "ALG"},
            {"id": 2, "fullname": "Álgebra avanzada", "shortname": "ALG2"},
        ]
    )
    s.select([1, 2])
    yield s
    s.close()


def item(key="a", text="Ecuación diferencial", **kw):
    return {
        "id": key,
        "title": key,
        "text": text,
        "url": "http://e-fich.unl.edu.ar/moodle/course/view.php?id=1",
        **kw,
    }


def test_baseline_changes_and_nonconsuming(store):
    store.snapshot(1, "contents", [item()], now=100)
    assert store.changes(0) == []
    store.snapshot(1, "contents", [item(text="modified"), item("b")], now=200)
    assert len(store.changes(100)) == 2
    assert store.changes(100) == store.changes(100)
    store.snapshot(1, "contents", [], now=300, complete=False, error="network_unavailable")
    assert len(store.items()) == 2
    assert store.freshness()[0]["last_success"] == 200
    store.snapshot(1, "contents", [], now=400)
    assert store.items() == []
    assert len(store.changes(300)) == 2


def test_search_literal_and_access(store):
    store.snapshot(1, "contents", [item()], now=100)
    assert store.search("ecuacion")[0]["title"] == "a"
    for query in ['"', "OR NOT", 'x" NEAR(a)', "*", "ecuación OR"]:
        store.search(query)
    store.select([2])
    assert store.search("ecuacion") == []
    with pytest.raises(FichError):
        store.document("a")
    store.select([1, 2])
    store.revoke(1)
    assert store.items() == []
    assert store.search("ecuacion") == []


def test_pages_changed_item_hidden(store):
    store.snapshot(1, "contents", [item(kind="pdf")], now=100)
    store.page("a", 1, "important text", "native", "ok")
    assert store.search("important")[0]["page"] == 1
    store.snapshot(1, "contents", [item(text="new", kind="pdf")], now=200)
    assert store.search("important") == []


def test_alias_ambiguity(store):
    courses = store.courses()
    assert resolve_course("algebra", courses, {}) == 1
    assert resolve_course("math", courses, {"math": "ALG"}) == 1
    courses.append({"id": 3, "fullname": "ÁLGEBRA", "shortname": "OTHER"})
    with pytest.raises(FichError, match="ambiguous_course"):
        resolve_course("algebra", courses, {})


def test_writer_lock(store):
    with store.writer():
        with pytest.raises(FichError, match="sync_busy"):
            with store.writer():
                pass


def test_query_bound():
    with pytest.raises(FichError):
        literal_query("x" * 501)
