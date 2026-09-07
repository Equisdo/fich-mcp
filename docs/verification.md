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

## First real-sync corrections

Four defects observed in the first end-to-end e-FICH sync were fixed and locked with regression
tests. No credentials, tokens, or live requests were used in this pass; every case is reproduced
with `httpx.MockTransport`, generated PDFs, a deterministic budget clock, and the repository's
ignored test-only OCR runtime.

### RED/GREEN: partial task inventory

**Guarantee:** `mod_assign_get_assignments` may return readable assignments together with
`warningcode 1` entries for modules the account cannot enter. Those assignments are stored, indexed
and searchable; the source is reported as partial, and items missing from a partial response are
never inferred as removed.

`Service._call` no longer raises on `warnings`; it raises the per-source `partial` marker, which is
persisted across resumed forum/assignment cursors. `Store.snapshot` gained a `partial` mode that
upserts the visible records, skips the unavailable sweep, and keeps `error='partial_inventory'` on
the source row, so the envelope reports `partial: true` and `stale: true` for it.

RED test added: `tests/test_sync.py::test_partial_assignment_inventory_keeps_visible_tasks`.

```text
E       AssertionError: assert 'Parcial' in {'Trabajo'}
```

### RED/GREEN: batch-deadline extraction timeouts

**Guarantee:** an extraction or page-count worker is never started with less than its own
`WORKER_TIMEOUT` (10 s) left in the sync budget, and running out of batch time never persists a
gap, a failed job, or an error. The batch returns `continuation: true` and the work stays pending
or resumable. Because a worker only starts when a full timeout remains, the drain still finishes
inside the sync deadline, keeping the 25 s budget and the 27 s MCP subprocess bound intact.

`Indexer.drain` now guards before each job, after the download, and before each page; the page
worker always gets the fixed full timeout instead of the remainder. A `budget_exhausted` raised by a
download leaves the job `pending` rather than `failed`.

RED tests added: `tests/test_pdf.py::test_batch_deadline_defers_work_instead_of_recording_gaps`,
`::test_slow_download_defers_page_counting`,
`::test_exhausted_download_budget_keeps_the_job_pending`.

```text
E       AssertionError: assert [('count', 1)] == []
E       AssertionError: assert {'errors': [{...ation': False} == {'errors': []...uation': True}
E         {'errors': [{'document': 'doc', 'code': 'budget_exhausted'}]} != {'errors': []}
E   Failed: counted without a full budget
```

### RED/GREEN: partial-document retry

**Guarantee:** `--force-refresh` on an unchanged PDF retries only the pages whose status is not a
finished extraction, leaves the healthy pages untouched, and only marks the job complete once no
page is left. A document that failed before reaching a page count returns to pending work. An
interrupted retry stays retryable instead of collapsing into `extraction_gaps`.

`Indexer.incomplete()` derives the retry set from the pages table, the job cursor advances with
`max(next_page, ?)` so a retry cannot rewind it, and a cursor past the last page selects the retry
set instead of an empty range.

RED tests added: `tests/test_pdf.py::test_force_refresh_retries_only_failed_pages`,
`::test_interrupted_retry_resumes_without_reprocessing_healthy_pages`. Two further cases,
`::test_force_refresh_keeps_a_healthy_document_complete` and
`::test_force_refresh_retries_a_document_that_never_reached_a_page_count`, already held before the
change and now lock that behavior.

```text
E       AssertionError: assert [('count', 1)] == [('count', 1), ('page', 2)]
E       AssertionError: assert {'state': 'pa...raction_gaps'} == {'state': 'co...'error': None}
```

### RED/GREEN: verified empty pages

**Guarantee:** a page whose OCR pass completes and returns no text is processed coverage, recorded
as `status='empty'` with no error. It produces no FTS row, so it still returns no search results,
but it does not count as an extraction gap and does not keep the document `partial`. Caches written
before this status are migrated on open.

RED tests added: `tests/test_pdf.py::test_verified_empty_pages_are_not_extraction_gaps`,
`::test_blank_scanned_page_reports_empty_not_a_gap` (real Poppler/Tesseract on a blank page),
`::test_legacy_no_text_detected_pages_are_reclassified`.

```text
E       AssertionError: assert {'text': '', ...ext_detected'} == {'text': '', ...'error': None}
E         {'status': 'gap'} != {'status': 'empty'}
E         {'error': 'no_text_detected'} != {'error': None}
```

### RED and GREEN commands

RED, with only `src/` reverted and the new tests in place:

```text
.venv/bin/python -m pytest tests/test_sync.py::test_partial_assignment_inventory_keeps_visible_tasks tests/test_pdf.py -q
9 failed, 7 passed in 5.03s
```

GREEN, with the fixes applied:

```text
.venv/bin/python -m pytest tests/test_sync.py::test_partial_assignment_inventory_keeps_visible_tasks tests/test_pdf.py -q
16 passed in 5.52s
```

### Full validation after the corrections

```text
.venv/bin/python -m pytest -q
63 passed in 8.01s

.venv/bin/python -m ruff check .
All checks passed!

.venv/bin/python -m pytest --cov=fich_mcp --cov-report=term-missing -q
src/fich_mcp/pdf.py            114      5    96%
src/fich_mcp/store.py          170     14    92%
src/fich_mcp/service.py        208     49    76%
TOTAL                         1049    188    82%
63 passed in 16.91s
```

### Known limitation

A source that stays partial keeps `errors` non-empty on every sync, so `runs.complete` never
becomes 1 and `get_changes` without an explicit `since` returns no baseline interval. That is the
pre-existing partial-sync semantics and was not changed here; pass `since` to `get_changes` while a
course keeps restricted modules.

## Live verification against the real e-FICH site

Run on 2026-09-06 with the token already stored by a previous `init`; no credentials were entered and
no login was performed in this pass. The site is HTTP-only, so the stored token travelled in clear —
that risk is inherent to e-FICH and is documented in the README. The cache was copied before any
command so the pre-fix state stayed available for comparison.

### Starting state (the defective first sync)

```text
sources    : course 2079 'assignments' -> error='partial_inventory', last_success=NULL
items      : 0 rows with source='assignments'   (the visible task was never stored)
jobs       : 70 complete | 11 partial 'extraction_gaps' | 1 failed 'extraction_timeout' (pages=NULL)
pages      : 1880 ok | 34 gap 'extraction_timeout' | 15 gap 'no_text_detected'
```

### A latent crash the first fix uncovered

Keeping the previously discarded assignments made the code reach data it had never processed:

```text
File "src/fich_mcp/service.py", line 241, in _assignments
    "due": max(assignment.get("duedate", 0), extension),
TypeError: '>' not supported between instances of 'NoneType' and 'int'
```

Moodle sends an explicit `null` for an unset due date, and `dict.get(key, default)` returns that `null`,
not the default. The CLI's broad handler turned it into `operation_failed` and the whole sync died. Fixed
by reading a missing key and an explicit null as the same absent value across `duedate`,
`extensionduedate`, `submission`/`teamsubmission` and `cmid`. Regression test:
`tests/test_sync.py::test_assignment_nulls_are_read_as_absent_values`.

### `sync` (no force)

```text
errors     : [{"course": 2079, "source": "assignments", "code": "partial_inventory"}]
continuation: false
items      : assignment:12696 "Condición Final Laboratorio" (course 2079), due=0, status=new
sources    : course 2079 'assignments' -> error='partial_inventory', last_success set
search     : one FTS row for assignment:12696
pages      : 15 empty (error NULL) | 34 gap 'extraction_timeout' | 1880 ok
```

The task that the account can read is stored, indexed and searchable while the source is still reported
partial. The 15 verified-blank pages were reclassified on cache open. The 11 `partial` and 1 `failed`
jobs were correctly left alone: an unforced sync only drains `pending`/`indexing`.

### `sync --force-refresh`

Six batches, 15.3 / 15.1 / 15.1 / 16.2 / 15.1 / 11.5 s — 88.3 s of wall time for 82 downloads, 82 page
counts, the retries and one full document.

```text
jobs  : 82 complete, 0 partial, 0 failed          (was 70 / 11 / 1)
pages : 1915 ok | 15 empty                        (was 1880 ok | 49 gap)
the document that had failed before its page count: failed/pages=NULL -> complete/pages=1
the 34 'extraction_timeout' pages: all ok — 32 native, 2 OCR
```

Every one of the 34 pages and the failed document extracted normally once the worker got a whole
budget, which is what the read-only diagnosis had predicted.

### Proof that healthy pages were not reprocessed

`store.page()` writes with `INSERT OR REPLACE`, and SQLite assigns a **new rowid** on replace. Comparing
rowids between the backup and the live cache therefore shows exactly which page rows were rewritten:

```text
paginas totales antes/ahora   : 1929 / 1930
rowid intacto (NO reprocesadas): 1895
rowid nuevo  (reprocesadas)    : 34
paginas nuevas                 : 1  (the previously failed document)
reprocesadas que estaban sanas : 0
```

The 34 rewritten rows are exactly the 34 that carried `extraction_timeout`. The 15 blank pages were
migrated in place with an `UPDATE` (rowid preserved) and deliberately **not** retried: a verified-absent
page is finished work, not pending work.

### Final coverage check

```text
paginas ex-timeout con fila FTS : 34 / 34
paginas vacias con fila FTS     : 0 / 15
documentos sin ninguna pagina no extraida: 82 / 82
```

The retried pages are searchable, the blank pages stay out of the index without degrading coverage, and
no document is left with an unextracted page.

### Full suite after the live pass

```text
.venv/bin/python -m pytest -q
64 passed in 8.22s

.venv/bin/python -m ruff check .
All checks passed!
```

**Environment trap:** the `.venv` carries a stale installed copy of `fich_mcp` in `site-packages`. The
test suite only uses `src/` because `pyproject.toml` sets `pythonpath = ["src"]`, and the live commands
above were run as `PYTHONPATH=src .venv/bin/python -m fich_mcp …`. The installed `fich-mcp` entry point
still runs the old code until the package is reinstalled.
