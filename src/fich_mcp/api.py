"""Public operation dispatcher. All identifiers resolve through authorized local inventory."""

import base64
import json
import tomllib
from contextlib import contextmanager

from .pdf import render
from .remote import Moodle
from .security import FichError, Paths
from .service import Service, date_range
from .store import Store, resolve_course


@contextmanager
def application(paths=None):
    paths = paths or Paths.default()
    account = paths.load()
    remote = Moodle(account)
    store = Store(paths.user_cache(account.userid))
    try:
        yield Service(store, remote)
    finally:
        store.close()
        remote.close()


def aliases(paths=None):
    path = (paths or Paths.default()).config / "aliases.toml"
    try:
        data = tomllib.loads(path.read_text()).get("aliases", {})
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise FichError("invalid_aliases")
        return data
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        raise FichError("invalid_aliases") from None


def dispatch(service, operation, args, alias_map=None):
    store = service.store
    course = args.get("course")
    if course is not None:
        course = resolve_course(str(course), store.courses(selected=True), alias_map or {})
    limit = args.get("limit", 20)
    offset = args.get("offset", 0)
    if (
        not isinstance(limit, int)
        or not 1 <= limit <= 50
        or not isinstance(offset, int)
        or not 0 <= offset <= 1000
    ):
        raise FichError("invalid_pagination")
    if operation == "sync":
        return service.sync(
            course,
            force_ocr=args.get("force_ocr", False),
            force_refresh=args.get("force_refresh", False),
        )
    calendar_window = None
    if operation == "get_upcoming":
        calendar_window = date_range(args.get("start"), args.get("end"))
    fresh = service.ensure_fresh(course, calendar_window=calendar_window)
    errors = fresh["errors"]
    if operation == "list_courses":
        data = store.courses(selected=True)
    elif operation == "get_course_contents":
        data = store.items(course, "contents")
    elif operation == "get_announcements":
        data = [i for i in store.items(course, "forums") if i.get("announcement")]
        if args.get("since"):
            start, _ = date_range(args["since"])
            data = [
                i
                for i in data
                if (i.get("remote_modified") or i.get("remote_created") or 0) >= start
            ]
    elif operation == "get_upcoming":
        start, end = calendar_window
        data = [
            i
            for i in store.items(course)
            if i.get("kind") in {"assignment", "event"} and start <= (i.get("due") or 0) <= end
        ]
        data.sort(key=lambda i: i["due"])
    elif operation == "get_changes":
        since = date_range(args["since"])[0] if args.get("since") else None
        return service.envelope(
            store.changes(since, course, limit, offset), errors, fresh["continuation"]
        )
    elif operation == "search_content":
        return service.envelope(
            store.search(args["query"], course, limit, offset), errors, fresh["continuation"]
        )
    elif operation == "read_document":
        key = args.get("document_id", "")
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            raise FichError("invalid_document_id")
        item = store.document(key)
        if course is not None and item["course"] != course:
            raise FichError("document_unavailable")
        page = args.get("page", 1)
        if not isinstance(page, int) or not 1 <= page <= 2000:
            raise FichError("invalid_page")
        if item.get("kind") != "pdf":
            if args.get("image"):
                raise FichError("render_unavailable")
            return service.envelope([item], errors)
        job = store.db.execute("SELECT * FROM jobs WHERE item=?", (key,)).fetchone()
        if not job or not item["index_current"]:
            raise FichError("document_not_indexed")
        if page > (job["pages"] or 0):
            raise FichError("invalid_page")
        citation = {
            "document_id": key,
            "course": item["course"],
            "title": item["title"],
            "page": page,
            "url": item["url"],
        }
        if args.get("image"):
            image = render(store.root / "documents" / (job["hash"] + ".pdf"), page)
            return {
                **service.envelope([citation], errors),
                "image_base64": base64.b64encode(image).decode(),
            }
        pages = [p for p in store.pages(key) if p["page"] == page]
        if not pages:
            raise FichError("page_not_indexed")
        return service.envelope([{**citation, **pages[0]}], errors)
    else:
        raise FichError("unsupported_operation")
    return service.envelope(
        data[offset : offset + limit],
        errors,
        fresh["continuation"],
        calendar_window=calendar_window,
        course=course,
    )


def worker():
    import sys

    try:
        payload = json.loads(sys.stdin.read(8192))
        with application() as service:
            result = dispatch(service, payload["operation"], payload["args"], aliases())
    except FichError as exc:
        result = {
            "data": [],
            "errors": [{"code": exc.code}],
            "freshness": [],
            "coverage": {},
            "continuation": False,
        }
    except Exception:
        # Fail closed for malformed remote schemas or storage faults without SDK traceback leakage.
        result = {
            "data": [],
            "errors": [{"code": "operation_failed"}],
            "freshness": [],
            "coverage": {},
            "continuation": False,
        }
    print(json.dumps(result))
