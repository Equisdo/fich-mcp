# Verification evidence

This repository previously had no README or verification document. The evidence below records only commands run during the completion pass.

## RED/GREEN: requested calendar window

**Guarantee:** `get_upcoming` refreshes calendar data for its requested, validated window (maximum 366 days). A fresh short cache must not satisfy a wider query; the response exposes per-course requested interval, covering interval, last success, and freshness.

RED test added: `tests/test_sync.py::test_requested_calendar_window_refreshes_and_reports_coverage`.

Actual RED command and output before the implementation accepted a calendar window:

```text
.venv/bin/python -m pytest tests/test_sync.py::test_requested_calendar_window_refreshes_and_reports_coverage -q
E       TypeError: Service.sync() got an unexpected keyword argument 'calendar_window'
1 failed in 0.12s
```

GREEN command after adding interval-aware calendar coverage and refresh propagation:

```text
.venv/bin/python -m pytest tests/test_sync.py::test_requested_calendar_window_refreshes_and_reports_coverage -q
.                                                                        [100%]
1 passed in 0.15s
```

The regression uses `httpx.MockTransport`, captures outbound Moodle calendar options, verifies that `timestart`/`timeend` encompass an event over 30 days away, verifies that the event is returned, and proves that a prior short sync triggers a second wider calendar request.

## Test coverage added

- `tests/test_api_remote.py`: dispatcher input validation/local inventory, alias parsing, worker error envelope, application cleanup, form encoding, remote timeout/malformed response shaping, site identity rejection, and download safety.
- `tests/test_server_cli_paths.py`: real MCP server tool registration, stdio serve call, process timeout envelope, CLI courses/sync success, and safe operational error output.
- `tests/test_pdf.py`: the scanned-PDF OCR path uses the ignored test-only `.tools/ocr/runtime` only within the test process (`PATH`, `LD_LIBRARY_PATH`, and `TESSDATA_PREFIX`). The missing-OCR behavior remains a non-skipped test.
- `tests/test_pdf_worker.py`: the isolated worker counts pages, returns native text, reports an unavailable OCR dependency, renders/OCRs inside its caller-owned temporary directory, and rejects invalid pages/parser failures without a real FICH request.
- `tests/test_cli_mcp.py`: normal installed CLI help omits the private `_rpc` worker capability while the server can still invoke it through its internal subprocess boundary.

## RED/GREEN: private RPC help visibility

**Guarantee:** users do not see the internal `_rpc` subprocess capability in normal CLI help, while the internal command remains available when the server starts it directly.

RED command before the parser selected the internal command only for an internal invocation:

```text
.venv/bin/python -m pytest tests/test_cli_mcp.py::test_public_cli_help_hides_internal_rpc_command -q
1 failed in 0.87s
```

GREEN command after separating the public and internal parser command sets:

```text
.venv/bin/python -m pytest tests/test_cli_mcp.py::test_public_cli_help_hides_internal_rpc_command tests/test_pdf_worker.py tests/test_api_remote.py::test_worker_returns_public_dispatch_result_and_hides_unexpected_errors -q
6 passed in 0.90s
```

No live FICH login or configuration write was performed; Claude configuration tests mock executable discovery and subprocess calls.

## RED/GREEN: FTS5 readiness probe

**Guarantee:** `doctor` reports `"fts5": false` when the local SQLite build cannot create an FTS5 virtual table, rather than propagating the expected SQLite probe failure as a traceback.

RED command before handling the FTS5 probe error:

```text
.venv/bin/python -m pytest tests/test_cli_mcp.py::test_doctor_reports_fts5_unavailable_when_probe_fails -q
1 failed in 0.81s
```

GREEN command after constraining the `sqlite3.OperationalError` handling to that readiness probe:

```text
.venv/bin/python -m pytest tests/test_cli_mcp.py::test_doctor_reports_fts5_unavailable_when_probe_fails -q
1 passed in 0.72s
```

The regression forces `CREATE VIRTUAL TABLE ... USING fts5` to raise `sqlite3.OperationalError`, verifies that `doctor` returns a report with `fts5` set to `false`, and confirms the probe connection is closed.

## Final validation

```text
.venv/bin/python -m pytest -q
52 passed in 5.23s

.venv/bin/python -m pytest --cov=fich_mcp --cov-report=term-missing -q
52 passed in 13.70s
TOTAL                             1017    199    80%

.venv/bin/python -m ruff check .
All checks passed!
```

A separate clean virtual environment was created from `.venv/bin/python`, installed with `pip install .`, and successfully ran `fich-mcp --help`. The resulting public usage lists only `init`, `doctor`, `serve`, `courses`, `sync`, and `configure`; it does not show `_rpc`. Its runtime dependencies were resolved from package metadata; development-only PDF test generation is declared under `.[dev]`.

Installed MCP 2.1.1 was exercised through a real stdio initialize/list-tools/call-tool test. `mcp.__version__` is absent in this SDK, and the implementation does not rely on it.
