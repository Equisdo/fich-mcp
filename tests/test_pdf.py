import io
import os
import time
from pathlib import Path

import pytest
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from fich_mcp.pdf import Indexer, extract, render
from fich_mcp.security import FichError
from fich_mcp.store import Store


def pdf_bytes(text="Algebra linear mathematics and differential equations printed course material"):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(50, 700, text)
    c.save()
    return buffer.getvalue()


@pytest.fixture
def native(tmp_path):
    p = tmp_path / "text.pdf"
    p.write_bytes(pdf_bytes())
    return p


def test_native_and_render(native):
    assert extract(native, "count")["pages"] == 1
    result = extract(native, "page", 1)
    assert "Algebra" in result["text"]
    assert result["provenance"] == "native"
    assert render(native, 1).startswith(b"\x89PNG")


def test_corrupt_and_bounds(tmp_path, native):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-broken")
    with pytest.raises(FichError, match="extraction_failed"):
        extract(bad, "count")
    with pytest.raises(FichError):
        extract(native, "page", 0)
    with pytest.raises(FichError, match="extraction_timeout"):
        extract(native, "count", timeout=0.00001)


def test_ocr_missing(native, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    result = extract(native, "page", 1, force=True)
    assert result["error"] == "ocr_unavailable"
    assert result["status"] == "gap"


def test_real_scan_and_mixed(native, tmp_path, monkeypatch):
    runtime = Path(__file__).parents[1] / ".tools" / "ocr" / "runtime" / "usr"
    monkeypatch.setenv("PATH", os.pathsep.join([str(runtime / "bin"), "/usr/bin"]))
    monkeypatch.setenv("LD_LIBRARY_PATH", str(runtime / "lib" / "x86_64-linux-gnu"))
    monkeypatch.setenv(
        "TESSDATA_PREFIX", str(runtime / "share" / "tesseract-ocr" / "5" / "tessdata")
    )
    image = render(native, 1)
    output = tmp_path / "mixed.pdf"
    c = canvas.Canvas(str(output))
    c.drawImage(ImageReader(io.BytesIO(image)), 0, 0, width=595, height=842)
    c.showPage()
    c.drawString(50, 700, "Native second page course text with mathematical introduction")
    c.save()
    assert extract(output, "count")["pages"] == 2
    first = extract(output, "page", 1)
    assert first["provenance"] == "ocr"
    assert "algebra" in first["text"].lower()
    assert extract(output, "page", 2)["provenance"] == "native"


def test_index_resume_and_download_failure(tmp_path):
    store = Store(tmp_path / "cache")
    store.set_courses([{"id": 1, "fullname": "A", "shortname": "A"}])
    store.select([1])
    store.snapshot(
        1,
        "contents",
        [{"id": "doc", "kind": "pdf", "title": "PDF", "download_reference": "safe", "url": "safe"}],
    )

    class Remote:
        def download(self, ref):
            return pdf_bytes()

    indexer = Indexer(store, Remote())
    assert indexer.drain(time.monotonic() - 1)["continuation"]
    assert not indexer.drain(time.monotonic() + 20)["errors"]
    assert store.search("algebra")[0]["page"] == 1
    assert store.db.execute("select state from jobs").fetchone()[0] == "complete"

    class Failed:
        def download(self, ref):
            raise FichError("download_failed")

    result = Indexer(store, Failed()).drain(time.monotonic() + 20, force_refresh=True)
    assert result["errors"]
    assert store.search("algebra")
    store.close()


def two_page_pdf():
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(50, 700, "First page printed material about differential calculus and matrices")
    c.showPage()
    c.drawString(50, 700, "Second page printed material about eigenvalues and linear operators")
    c.save()
    return buffer.getvalue()


class Clock:
    """Deterministic budget clock; pdf.py reads wall time only through time.monotonic."""

    def __init__(self, now=0.0):
        self.now = now

    def monotonic(self):
        return self.now


def indexed_store(tmp_path, document=b""):
    store = Store(tmp_path / "cache")
    store.set_courses([{"id": 1, "fullname": "A", "shortname": "A"}])
    store.select([1])
    store.snapshot(
        1,
        "contents",
        [{"id": "doc", "kind": "pdf", "title": "PDF", "download_reference": "safe", "url": "safe"}],
    )

    class Remote:
        def download(self, ref):
            return document

    return store, Remote()


def test_batch_deadline_defers_work_instead_of_recording_gaps(tmp_path, monkeypatch):
    """A worker is never started on a remainder of the budget, and nothing is failed for it."""
    from fich_mcp import pdf

    store, remote = indexed_store(tmp_path, two_page_pdf())
    clock = Clock()
    monkeypatch.setattr(pdf.time, "monotonic", clock.monotonic)
    started = []
    real_extract = pdf.extract

    def timed(path, action, page=1, **kwargs):
        started.append((action, page))
        clock.now += 46
        return real_extract(path, action, page, **kwargs)

    monkeypatch.setattr(pdf, "extract", timed)
    indexer = Indexer(store, remote)

    # No whole worker timeout left: nothing starts, nothing is persisted as broken.
    clock.now = 95.0
    result = indexer.drain(100.0)
    assert result == {"errors": [], "continuation": True}
    assert started == []
    assert dict(store.db.execute("SELECT state,error FROM jobs").fetchone()) == {
        "state": "pending",
        "error": None,
    }
    assert store.db.execute("SELECT count(*) FROM pages").fetchone()[0] == 0

    # Counting plus one page consumes the budget; the second page is deferred, not a gap.
    clock.now = 0.0
    result = indexer.drain(100.0)
    assert result == {"errors": [], "continuation": True}
    assert started == [("count", 1), ("page", 1)]
    job = dict(store.db.execute("SELECT state,pages,next_page,error FROM jobs").fetchone())
    assert job == {"state": "indexing", "pages": 2, "next_page": 2, "error": None}
    assert store.db.execute("SELECT count(*) FROM pages WHERE status!='ok'").fetchone()[0] == 0

    clock.now = 0.0
    assert indexer.drain(100.0) == {"errors": [], "continuation": False}
    assert store.db.execute("SELECT state,error FROM jobs").fetchone()[0] == "complete"
    assert store.search("eigenvalues")[0]["page"] == 2
    store.close()


def test_exhausted_download_budget_keeps_the_job_pending(tmp_path):
    """Losing the batch deadline mid-download is not a permanent document failure."""
    store, _ = indexed_store(tmp_path)

    class Starved:
        def download(self, ref):
            raise FichError("budget_exhausted")

    result = Indexer(store, Starved()).drain(time.monotonic() + 30)
    assert result == {"errors": [], "continuation": True}
    assert dict(store.db.execute("SELECT state,error FROM jobs").fetchone()) == {
        "state": "pending",
        "error": None,
    }
    store.close()


def test_force_refresh_retries_only_failed_pages(tmp_path, monkeypatch):
    """An unchanged partial document retries its gaps and keeps its healthy pages."""
    from fich_mcp import pdf

    store, remote = indexed_store(tmp_path, two_page_pdf())
    assert Indexer(store, remote).drain(time.monotonic() + 60) == {
        "errors": [],
        "continuation": False,
    }
    # Reproduce a deadline-induced page gap left behind by an earlier release.
    store.page("doc", 2, text="", provenance="none", status="gap", error="extraction_timeout")
    with store.db:
        store.db.execute("UPDATE jobs SET state='partial',error='extraction_gaps'")

    started = []
    real_extract = pdf.extract

    def counted(path, action, page=1, **kwargs):
        started.append((action, page))
        return real_extract(path, action, page, **kwargs)

    monkeypatch.setattr(pdf, "extract", counted)
    assert Indexer(store, remote).drain(time.monotonic() + 60, force_refresh=True) == {
        "errors": [],
        "continuation": False,
    }

    assert started == [("count", 1), ("page", 2)]
    assert dict(store.db.execute("SELECT state,error FROM jobs").fetchone()) == {
        "state": "complete",
        "error": None,
    }
    assert store.db.execute("SELECT status FROM pages WHERE page=2").fetchone()[0] == "ok"
    assert store.db.execute("SELECT next_page FROM jobs").fetchone()[0] == 3
    assert store.search("eigenvalues")[0]["page"] == 2
    store.close()


def test_interrupted_retry_resumes_without_reprocessing_healthy_pages(tmp_path, monkeypatch):
    """A retry cut by the deadline keeps its remaining failed pages, not a finished cursor."""
    from fich_mcp import pdf

    store, remote = indexed_store(tmp_path, two_page_pdf())
    assert Indexer(store, remote).drain(time.monotonic() + 60) == {
        "errors": [],
        "continuation": False,
    }
    for number in (1, 2):
        store.page(
            "doc", number, text="", provenance="none", status="gap", error="extraction_timeout"
        )
    with store.db:
        store.db.execute("UPDATE jobs SET state='indexing',error='extraction_gaps'")

    clock = Clock()
    monkeypatch.setattr(pdf.time, "monotonic", clock.monotonic)
    started = []
    real_extract = pdf.extract

    def timed(path, action, page=1, **kwargs):
        started.append((action, page))
        clock.now += 91
        return real_extract(path, action, page, **kwargs)

    monkeypatch.setattr(pdf, "extract", timed)
    indexer = Indexer(store, remote)

    assert indexer.drain(100.0) == {"errors": [], "continuation": True}
    assert started == [("page", 1)]
    assert store.db.execute("SELECT next_page FROM jobs").fetchone()[0] == 3

    clock.now = 0.0
    assert indexer.drain(100.0) == {"errors": [], "continuation": False}
    assert started == [("page", 1), ("page", 2)]
    assert dict(store.db.execute("SELECT state,error FROM jobs").fetchone()) == {
        "state": "complete",
        "error": None,
    }
    store.close()


def test_force_refresh_retries_a_document_that_never_reached_a_page_count(tmp_path):
    """A document failed before its page count is pending work, not a finished job."""
    store, remote = indexed_store(tmp_path, two_page_pdf())
    with store.db:
        store.db.execute("UPDATE jobs SET state='failed',error='extraction_timeout'")

    assert Indexer(store, remote).drain(time.monotonic() + 60) == {
        "errors": [],
        "continuation": False,
    }
    assert store.db.execute("SELECT state FROM jobs").fetchone()[0] == "failed"

    assert Indexer(store, remote).drain(time.monotonic() + 60, force_refresh=True) == {
        "errors": [],
        "continuation": False,
    }
    job = dict(store.db.execute("SELECT state,pages,error FROM jobs").fetchone())
    assert job == {"state": "complete", "pages": 2, "error": None}
    store.close()


def test_verified_empty_pages_are_not_extraction_gaps(tmp_path, monkeypatch):
    """A page OCR read as blank is processed coverage, unsearchable but not a gap."""
    from fich_mcp import pdf

    store, remote = indexed_store(tmp_path, two_page_pdf())

    def blank_second_page(path, action, page=1, **kwargs):
        if action == "count":
            return {"pages": 2}
        if page == 2:
            return {"text": "", "provenance": "ocr", "status": "empty", "error": None}
        return {
            "text": "First page printed material",
            "provenance": "native",
            "status": "ok",
            "error": None,
        }

    monkeypatch.setattr(pdf, "extract", blank_second_page)
    assert Indexer(store, remote).drain(time.monotonic() + 60) == {
        "errors": [],
        "continuation": False,
    }

    assert dict(store.db.execute("SELECT state,error FROM jobs").fetchone()) == {
        "state": "complete",
        "error": None,
    }
    assert dict(store.db.execute("SELECT status,error FROM pages WHERE page=2").fetchone()) == {
        "status": "empty",
        "error": None,
    }
    assert [r["page"] for r in store.search("printed")] == [1]
    store.close()


def test_blank_scanned_page_reports_empty_not_a_gap(tmp_path, monkeypatch):
    """The isolated worker classifies a real blank scan itself, with no error code."""
    runtime = Path(__file__).parents[1] / ".tools" / "ocr" / "runtime" / "usr"
    monkeypatch.setenv("PATH", os.pathsep.join([str(runtime / "bin"), "/usr/bin"]))
    monkeypatch.setenv("LD_LIBRARY_PATH", str(runtime / "lib" / "x86_64-linux-gnu"))
    monkeypatch.setenv(
        "TESSDATA_PREFIX", str(runtime / "share" / "tesseract-ocr" / "5" / "tessdata")
    )
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.showPage()
    c.save()
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(buffer.getvalue())

    assert extract(blank, "page", 1) == {
        "text": "",
        "status": "empty",
        "provenance": "ocr",
        "error": None,
    }


def test_legacy_no_text_detected_pages_are_reclassified(tmp_path):
    """Caches written before empty pages had a status must not keep understating coverage."""
    store = Store(tmp_path / "cache")
    store.page("doc", 1, text="", provenance="ocr", status="gap", error="no_text_detected")
    store.close()

    reopened = Store(tmp_path / "cache")
    assert dict(reopened.db.execute("SELECT status,error FROM pages").fetchone()) == {
        "status": "empty",
        "error": None,
    }
    reopened.close()


def test_slow_download_defers_page_counting(tmp_path, monkeypatch):
    """Counting pages waits for a whole budget instead of failing the document."""
    from fich_mcp import pdf

    store, _ = indexed_store(tmp_path)
    clock = Clock()
    monkeypatch.setattr(pdf.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(
        pdf, "extract", lambda *args, **kwargs: pytest.fail("counted without a full budget")
    )

    class Slow:
        def download(self, ref):
            clock.now += 95
            return two_page_pdf()

    assert Indexer(store, Slow()).drain(100.0) == {"errors": [], "continuation": True}
    assert dict(store.db.execute("SELECT state,error FROM jobs").fetchone()) == {
        "state": "pending",
        "error": None,
    }
    store.close()


def test_force_refresh_keeps_a_healthy_document_complete(tmp_path, monkeypatch):
    """An unchanged, fully extracted document is restored, never re-extracted page by page."""
    from fich_mcp import pdf

    store, remote = indexed_store(tmp_path, two_page_pdf())
    assert Indexer(store, remote).drain(time.monotonic() + 60)["continuation"] is False
    # A metadata change drops FTS rows while the extracted pages stay on disk.
    with store.db:
        store.db.execute("DELETE FROM search WHERE item='doc' AND page>0")
    assert store.search("eigenvalues") == []

    started = []
    real_extract = pdf.extract

    def counted(path, action, page=1, **kwargs):
        started.append((action, page))
        return real_extract(path, action, page, **kwargs)

    monkeypatch.setattr(pdf, "extract", counted)
    assert Indexer(store, remote).drain(time.monotonic() + 60, force_refresh=True) == {
        "errors": [],
        "continuation": False,
    }

    assert started == [("count", 1)]
    assert store.db.execute("SELECT state,error FROM jobs").fetchone()[0] == "complete"
    assert store.search("eigenvalues")[0]["page"] == 2
    store.close()
