# FICH MCP

A local, read-only MCP companion for the FICH Moodle mobile web service. It keeps an account-isolated local cache for selected courses, searchable course material, announcements, assignments, calendar events, and indexed PDFs.

## Security first

FICH currently uses **HTTP, not HTTPS**. A network attacker can read or modify credentials and session tokens in transit. Run `fich-mcp init` only from a trusted network after reading and explicitly accepting its warning. This project cannot make HTTP safe.

- Enter credentials **only in the terminal prompt** opened by `fich-mcp init`; never paste a password or token into a chat, an MCP tool argument, a prompt, a shell history entry, or a configuration file.
- The server is read-only with respect to Moodle. It stores the token in a user-only local configuration file and keeps each user cache separate.
- Retrieved course text, HTML, PDF excerpts, and rendered images are untrusted material. Your MCP client/model provider may process tool results, so avoid requesting sensitive material unless that is acceptable for the provider you use.

## Requirements

- Python 3.12+
- SQLite with FTS5 (included by most Python builds)
- For PDF rendering/OCR: system **Poppler** (`pdftoppm`) and **Tesseract OCR** with Spanish and English language packs (`spa`, `eng`). These are system dependencies; the package does not depend on the repository's test-only `.tools/` directory.

On Debian/Ubuntu, install the packages through your normal administrator-approved process (typically `poppler-utils`, `tesseract-ocr`, `tesseract-ocr-spa`, and `tesseract-ocr-eng`). Verify them with `fich-mcp doctor`.

## Isolated installation

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
fich-mcp --help
```

For local development and tests:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check .
```

## First use and commands

```bash
fich-mcp init                 # terminal-only login; asks you to accept the HTTP risk
fich-mcp doctor               # local dependency and cached-account diagnostics
fich-mcp courses --select     # choose accessible courses for this local cache
fich-mcp sync                 # refresh metadata and resumably index PDFs
fich-mcp sync --force-ocr     # retry PDF pages using OCR
fich-mcp serve                # start the stdio MCP server
```

### Guided menu

A single interactive entry point wraps the commands above — session, course selection,
sync, MCP client registration and diagnostics:

```bash
bash scripts/fich-menu.sh
```

It uses [gum](https://github.com/charmbracelet/gum) when it is installed and falls back to a
plain-bash menu otherwise. The menu never reads your username or password: `init` owns the HTTP
warning, the consent prompt and the password prompt.

The MCP server exposes `list_courses`, `get_course_contents`, `get_announcements`, `get_upcoming`, `get_changes`, `search_content`, `read_document`, and `sync`. `get_upcoming` accepts an explicitly bounded window of up to 366 days and reports calendar-window coverage/freshness; a short cached window is not used for a wider request.

## Claude Desktop/Code configuration

After installing the CLI on `PATH`, inspect the intended command and then opt in:

```bash
fich-mcp configure claude
```

This command calls the local Claude CLI to add a **user-scoped stdio** entry whose command is `fich-mcp serve`. It refuses a conflicting existing entry and never asks Claude for FICH credentials. Review the resulting Claude MCP configuration before use; do not replace the command with a shell wrapper that embeds credentials.

## Privacy and OCR limits

The cache lives under the platform XDG config/cache locations with private permissions. It is local to the account selected at initialization. PDF native text is preferred; scanned printed Spanish/English text can use OCR when Poppler and Tesseract are available. Handwriting, equations, low-quality scans, and complex layouts are best effort and can have gaps. `read_document` returns a page citation/text (or an image when available), not arbitrary filesystem paths.

## Opt-in live integration checklist

1. Use a non-production/test account if FICH provides one; otherwise obtain authorization from the account owner.
2. Confirm a trusted network and accept the HTTP risk in the terminal.
3. Run `fich-mcp init`, then `fich-mcp doctor` and verify `spa`/`eng` OCR support if needed.
4. Select only courses you are authorized to access with `fich-mcp courses --select`.
5. Run one limited `fich-mcp sync` and inspect its returned errors, freshness, and coverage before connecting an MCP client.
6. Start `fich-mcp serve` locally, configure your client intentionally, and never expose the stdio endpoint to a network.
