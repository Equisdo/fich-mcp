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
