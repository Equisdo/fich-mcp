"""Windows OCR uses bounded pipes instead of unbounded output files."""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, TimeoutError

MAX_OUTPUT = 24 * 1024**2


def capture(command, data=None, timeout=8):
    """Drain at most MAX_OUTPUT+1 bytes, with a deadline and no output file.

    Called only inside the PDF Job Object, which also limits memory/CPU and
    kills external tools if the parent PDF worker is killed by its deadline.
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )

    def write():
        try:
            process.stdin.write(data)
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(process.stdout.read, MAX_OUTPUT + 1)
        writer = pool.submit(write) if data is not None else None
        try:
            output = reader.result(timeout=timeout)
            if len(output) > MAX_OUTPUT:
                raise ValueError("output limit")
            if writer is not None:
                writer.result(timeout=timeout)
            if process.wait(timeout=timeout):
                raise ValueError("tool failed")
            return output
        except TimeoutError:
            raise subprocess.TimeoutExpired(command, timeout) from None
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.result()
            process.stdout.close()


def render_ocr(path, action, page):
    image = capture(
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
        ]
    )
    if not image.startswith(b"\x89PNG"):
        raise ValueError("invalid image")
    if action == "render":
        return image
    return capture(
        ["tesseract", "stdin", "stdout", "-l", "spa+eng", "--psm", "3"],
        image,
    ).decode("utf-8")[:200000]
