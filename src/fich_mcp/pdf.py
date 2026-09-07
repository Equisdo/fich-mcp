"""Bounded subprocess extraction; versioned, content-addressed original PDFs."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .security import FichError, atomic_write, private_dir

VERSION = "pypdf6-tesseract5-v1"


def _worker(path, action, page, force, timeout):
    if page < 1 or page > 2000:
        raise FichError("invalid_page")
    with tempfile.TemporaryDirectory(prefix=".extract-", dir=path.parent) as temporary:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("pdf_worker.py")),
                    str(path.resolve()),
                    action,
                    str(page),
                    str(int(force)),
                ],
                cwd=temporary,
                capture_output=True,
                timeout=max(0.00001, timeout),
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
        except subprocess.TimeoutExpired:
            raise FichError("extraction_timeout") from None
        if result.returncode or len(result.stdout) > 24_000_000:
            raise FichError("extraction_failed")
        return result.stdout


def extract(path, action, page=1, force=False, timeout=10):
    try:
        return json.loads(_worker(path, action, page, force, timeout))
    except (ValueError, OSError):
        raise FichError("extraction_failed") from None


def render(path, page, timeout=10):
    data = _worker(path, "render", page, False, timeout)
    if not data.startswith(b"\x89PNG"):
        raise FichError("render_unavailable")
    return data


class Indexer:
    def __init__(self, store, remote):
        self.store, self.remote = store, remote
        self.files = private_dir(store.root / "documents")

    def drain(self, end, course=None, force_ocr=False, force_refresh=False):
        db = self.store.db
        if force_ocr or force_refresh:
            with db:
                db.execute(
                    "UPDATE jobs SET state='pending',error=NULL"
                    + (" WHERE item IN (SELECT id FROM items WHERE course=?)" if course else ""),
                    (course,) if course else (),
                )
        jobs = db.execute(
            "SELECT j.*,i.data,i.course FROM jobs j JOIN items i ON i.id=j.item JOIN courses c ON c.id=i.course WHERE j.state IN ('pending','indexing') AND i.available=1 AND c.selected=1 AND c.accessible=1"
            + (" AND i.course=?" if course else ""),
            (course,) if course else (),
        ).fetchall()
        errors = []
        continuation = False
        for job in jobs:
            if time.monotonic() >= end:
                continuation = True
                break
            key = job["item"]
            try:
                digest = job["hash"]
                count = job["pages"]
                next_page = job["next_page"]
                if job["state"] == "pending":
                    data = self.remote.download(json.loads(job["data"])["download_reference"])
                    digest = hashlib.sha256(data).hexdigest()
                    path = self.files / (digest + ".pdf")
                    if not path.exists():
                        atomic_write(path, data)
                    count = extract(path, "count", timeout=min(10, end - time.monotonic()))["pages"]
                    if (
                        digest == job["hash"]
                        and job["version"] == VERSION
                        and not force_ocr
                        and next_page > count
                    ):
                        with db:
                            db.execute(
                                "UPDATE jobs SET state='complete',error=NULL WHERE item=?", (key,)
                            )
                            db.execute("UPDATE items SET index_current=1 WHERE id=?", (key,))
                            # A metadata change removed FTS entries, so restore last-good pages.
                            db.execute(
                                "INSERT INTO search SELECT item,page,text FROM pages WHERE item=? AND text!='' AND NOT EXISTS (SELECT 1 FROM search WHERE item=pages.item AND page=pages.page)",
                                (key,),
                            )
                        continue
                    if digest != job["hash"] or job["version"] != VERSION or force_ocr:
                        next_page = 1
                        with db:
                            db.execute("DELETE FROM pages WHERE item=?", (key,))
                            db.execute("DELETE FROM search WHERE item=? AND page>0", (key,))
                    with db:
                        db.execute(
                            "UPDATE jobs SET state='indexing',hash=?,version=?,pages=?,next_page=?,error=NULL WHERE item=?",
                            (digest, VERSION, count, next_page, key),
                        )
                path = self.files / (digest + ".pdf")
                for number in range(next_page, count + 1):
                    if time.monotonic() >= end:
                        continuation = True
                        break
                    try:
                        result = extract(
                            path,
                            "page",
                            number,
                            force=force_ocr,
                            timeout=min(10, end - time.monotonic()),
                        )
                    except FichError as exc:
                        result = {
                            "text": "",
                            "provenance": "none",
                            "status": "gap",
                            "error": exc.code,
                        }
                    self.store.page(key, number, **result)
                    with db:
                        db.execute("UPDATE jobs SET next_page=? WHERE item=?", (number + 1, key))
                    if result["error"]:
                        errors.append({"document": key, "page": number, "code": result["error"]})
                else:
                    gaps = db.execute(
                        "SELECT count(*) FROM pages WHERE item=? AND status!='ok'", (key,)
                    ).fetchone()[0]
                    with db:
                        db.execute(
                            "UPDATE jobs SET state=?,error=? WHERE item=?",
                            (
                                "partial" if gaps else "complete",
                                "extraction_gaps" if gaps else None,
                                key,
                            ),
                        )
            except FichError as exc:
                errors.append({"document": key, "code": exc.code})
                with db:
                    db.execute(
                        "UPDATE jobs SET state='failed',error=? WHERE item=?", (exc.code, key)
                    )
        return {"errors": errors, "continuation": continuation}
