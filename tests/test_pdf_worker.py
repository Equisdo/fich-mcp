"""Behavioral coverage for the isolated PDF child process entry point."""

import json
import runpy
import sys

import pytest

from fich_mcp import pdf_worker


class Page:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


class Reader:
    def __init__(self, text, pages=1):
        self.pages = [Page(text) for _ in range(pages)]


def run_worker(monkeypatch, capsys, *, action, page=1, force="0", text="Native text " * 8):
    monkeypatch.setattr(pdf_worker.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["pdf_worker", "document.pdf", action, str(page), force])
    pdf_worker.main()
    return capsys.readouterr().out


def test_worker_counts_pages_and_returns_native_text(monkeypatch, capsys):
    monkeypatch.setattr("pypdf.PdfReader", lambda path: Reader("Native course material " * 4, pages=2))

    counted = json.loads(run_worker(monkeypatch, capsys, action="count"))
    assert counted == {"pages": 2}

    result = json.loads(run_worker(monkeypatch, capsys, action="page"))
    assert result["status"] == "ok"
    assert result["provenance"] == "native"


def test_worker_reports_ocr_gap_when_tools_are_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("pypdf.PdfReader", lambda path: Reader("too short"))
    monkeypatch.setattr(pdf_worker.shutil, "which", lambda name: None)

    result = json.loads(run_worker(monkeypatch, capsys, action="page"))
    assert result == {
        "text": "too short",
        "status": "gap",
        "provenance": "native",
        "error": "ocr_unavailable",
    }


def test_worker_renders_and_ocr_extracts_in_private_working_directory(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pypdf.PdfReader", lambda path: Reader(""))
    monkeypatch.setattr(pdf_worker.shutil, "which", lambda name: "/tool/" + name)

    def run(command, **kwargs):
        if command[0] == "pdftoppm":
            (tmp_path / "page.png").write_bytes(b"PNG")
        else:
            (tmp_path / "page.txt").write_text("OCR text")

    monkeypatch.setattr(pdf_worker.subprocess, "run", run)
    assert run_worker(monkeypatch, capsys, action="render") == "PNG"

    result = json.loads(run_worker(monkeypatch, capsys, action="page", force="1"))
    assert result["provenance"] == "ocr"
    assert result["text"] == "OCR text"


def test_worker_rejects_invalid_pages_and_parser_failures(monkeypatch, capsys):
    monkeypatch.setattr("pypdf.PdfReader", lambda path: Reader("text"))
    with pytest.raises(ValueError, match="page bounds"):
        run_worker(monkeypatch, capsys, action="page", page=2)

    monkeypatch.setattr("pypdf.PdfReader", lambda path: (_ for _ in ()).throw(ValueError("bad PDF")))
    with pytest.raises(ValueError, match="bad PDF"):
        run_worker(monkeypatch, capsys, action="count")


def test_worker_enforces_page_limit_and_module_entrypoint_hides_parser_errors(monkeypatch, capsys):
    monkeypatch.setattr("pypdf.PdfReader", lambda path: Reader("text", pages=2001))
    with pytest.raises(ValueError, match="page limit"):
        run_worker(monkeypatch, capsys, action="count")

    monkeypatch.setattr(pdf_worker.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["pdf_worker", "document.pdf", "count", "1", "0"])
    monkeypatch.setattr("pypdf.PdfReader", lambda path: (_ for _ in ()).throw(ValueError("bad PDF")))
    monkeypatch.delitem(sys.modules, "fich_mcp.pdf_worker")
    with pytest.raises(SystemExit) as exit_code:
        runpy.run_module("fich_mcp.pdf_worker", run_name="__main__")
    assert exit_code.value.code == 2
    assert capsys.readouterr().out == ""
