"""Resource-limited PDF child. No credentials or network access are needed here."""

import json
import os
import resource
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    resource.setrlimit(resource.RLIMIT_FSIZE, (24 * 1024**2, 24 * 1024**2))
    from pypdf import PdfReader

    path, action, number, force = sys.argv[1:]
    page = int(number)
    reader = PdfReader(path)
    count = len(reader.pages)
    if count > 2000:
        raise ValueError("page limit")
    if action == "count":
        print(json.dumps({"pages": count}))
        return
    if not 1 <= page <= count:
        raise ValueError("page bounds")
    text = reader.pages[page - 1].extract_text()[:200000]
    if action == "page" and len(text.strip()) >= 40 and force != "1":
        print(json.dumps({"text": text, "status": "ok", "provenance": "native", "error": None}))
        return
    if not shutil.which("pdftoppm") or (action != "render" and not shutil.which("tesseract")):
        print(
            json.dumps(
                {"text": text, "status": "gap", "provenance": "native", "error": "ocr_unavailable"}
            )
        )
        return
    # Caller owns this private temporary working directory.
    subprocess.run(
        [
            "pdftoppm",
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-scale-to",
            "1800",
            "-png",
            path,
            "page",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=8,
    )
    if action == "render":
        sys.stdout.buffer.write(Path("page.png").read_bytes())
        return
    subprocess.run(
        ["tesseract", "page.png", "page", "-l", "spa+eng", "--psm", "3"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=8,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    text = Path("page.txt").read_text()[:200000]
    # An OCR pass that returns nothing read the page successfully: it is blank, not a gap.
    print(
        json.dumps(
            {
                "text": text,
                "status": "ok" if text.strip() else "empty",
                "provenance": "ocr",
                "error": None,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Native parser errors can contain document material; never print them.
        sys.exit(2)
