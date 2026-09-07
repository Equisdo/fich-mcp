"""Account-isolated snapshots, resumable indexing, literal FTS, and change history."""

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from contextlib import contextmanager

from .security import FichError, private_dir


def normalize(text):
    return "".join(
        c for c in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(c)
    ).strip()


def resolve_course(value, courses, aliases):
    value = normalize(value)
    aliases = {normalize(k): v for k, v in aliases.items()}
    target = normalize(aliases.get(value, value))
    matches = [
        c
        for c in courses
        if target in {str(c["id"]), normalize(c["fullname"]), normalize(c["shortname"])}
    ]
    if len(matches) != 1:
        raise FichError("ambiguous_course" if matches else "course_not_found")
    return matches[0]["id"]


def literal_query(query):
    if not isinstance(query, str) or not 1 <= len(query) <= 500:
        raise FichError("invalid_query")
    terms = re.findall(r"\w+", query, re.UNICODE)[:30]
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)


class Store:
    def __init__(self, root):
        self.root = private_dir(root)
        path = root / "cache.sqlite3"
        if path.is_symlink():
            raise FichError("unsafe_storage")
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        path.chmod(0o600)
        self.db = sqlite3.connect(path, timeout=0.2)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=DELETE;
        CREATE TABLE IF NOT EXISTS courses(id INTEGER PRIMARY KEY, data TEXT NOT NULL,
          selected INTEGER NOT NULL DEFAULT 0, accessible INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sources(course INTEGER, source TEXT, last_success REAL,
          last_attempt REAL, error TEXT, PRIMARY KEY(course,source));
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, course INTEGER, source TEXT,
          data TEXT, fingerprint TEXT, first_seen REAL, available INTEGER DEFAULT 1,
          index_current INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS pages(item TEXT, page INTEGER, text TEXT, provenance TEXT,
          status TEXT, error TEXT, PRIMARY KEY(item,page));
        CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(item UNINDEXED, page UNINDEXED,
          text, tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY, course INTEGER, item TEXT,
          kind TEXT, time REAL, data TEXT);
        CREATE TABLE IF NOT EXISTS jobs(item TEXT PRIMARY KEY, state TEXT DEFAULT 'pending',
          hash TEXT, version TEXT, pages INTEGER, next_page INTEGER DEFAULT 1, error TEXT);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started REAL, ended REAL, complete INTEGER);
        CREATE TABLE IF NOT EXISTS cursors(key TEXT PRIMARY KEY, data TEXT);
        CREATE TABLE IF NOT EXISTS calendar_coverage(course INTEGER, start REAL, end REAL,
          last_success REAL, PRIMARY KEY(course,start,end));
        """)
        with self.db:
            # Caches written before empty pages had their own status recorded a verified blank
            # page as an extraction gap; reclassify it so coverage is not understated.
            self.db.execute(
                "UPDATE pages SET status='empty',error=NULL "
                "WHERE status='gap' AND error='no_text_detected'"
            )

    def close(self):
        self.db.close()

    @contextmanager
    def writer(self):
        path = self.root / "writer.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise FichError("sync_busy") from None
            yield
        finally:
            os.close(fd)

    def set_courses(self, courses):
        with self.db:
            self.db.execute("UPDATE courses SET accessible=0")
            for c in courses:
                self.db.execute(
                    "INSERT INTO courses(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,accessible=1",
                    (c["id"], json.dumps(c)),
                )

    def courses(self, selected=False):
        rows = self.db.execute(
            "SELECT * FROM courses WHERE accessible=1" + (" AND selected=1" if selected else "")
        )
        return [{**json.loads(r["data"]), "selected": bool(r["selected"])} for r in rows]

    def select(self, ids):
        accessible = {c["id"] for c in self.courses()}
        if not set(ids) <= accessible:
            raise FichError("course_denied")
        with self.db:
            self.db.execute("UPDATE courses SET selected=0")
            self.db.executemany("UPDATE courses SET selected=1 WHERE id=?", [(i,) for i in ids])

    def revoke(self, course):
        with self.db:
            self.db.execute("UPDATE courses SET accessible=0 WHERE id=?", (course,))

    def snapshot(
        self, course, source, items, *, now=None, complete=True, partial=False, error=None
    ):
        now = time.time() if now is None else now
        prior = self.db.execute(
            "SELECT last_success FROM sources WHERE course=? AND source=?", (course, source)
        ).fetchone()
        baseline = bool(prior and prior[0] is not None)
        with self.db:
            self.db.execute(
                "INSERT INTO sources VALUES(?,?,?,?,?) ON CONFLICT(course,source) DO UPDATE SET last_attempt=excluded.last_attempt,error=excluded.error",
                (course, source, None, now, error),
            )
            # Incomplete inventory never changes last-good inventory/index visibility.
            if not complete:
                return
            seen = set()
            for item in items:
                key = str(item["id"])
                seen.add(key)
                data = json.dumps(item, sort_keys=True, ensure_ascii=False)
                fingerprint = hashlib.sha256(data.encode()).hexdigest()
                old = self.db.execute("SELECT * FROM items WHERE id=?", (key,)).fetchone()
                changed = not old or old["fingerprint"] != fingerprint or not old["available"]
                if changed:
                    if baseline:
                        self.db.execute(
                            "INSERT INTO changes(course,item,kind,time,data) VALUES(?,?,?,?,?)",
                            (
                                course,
                                key,
                                "modified" if old and old["available"] else "added",
                                now,
                                data,
                            ),
                        )
                    self.db.execute(
                        "INSERT INTO items VALUES(?,?,?,?,?,?,1,0) ON CONFLICT(id) DO UPDATE SET data=excluded.data,fingerprint=excluded.fingerprint,available=1,index_current=0",
                        (key, course, source, data, fingerprint, now),
                    )
                    self.db.execute("DELETE FROM search WHERE item=?", (key,))
                    self.db.execute(
                        "INSERT INTO search VALUES(?,0,?)",
                        (key, item.get("title", "") + "\n" + item.get("text", "")),
                    )
                    if item.get("kind") == "pdf":
                        self.db.execute(
                            "INSERT INTO jobs(item) VALUES(?) ON CONFLICT(item) DO UPDATE SET state='pending',error=NULL",
                            (key,),
                        )
            # A partial inventory legitimately omits restricted items; never infer removal.
            if not partial:
                for row in self.db.execute(
                    "SELECT id,data FROM items WHERE course=? AND source=? AND available=1",
                    (course, source),
                ).fetchall():
                    if row["id"] not in seen:
                        self.db.execute("UPDATE items SET available=0 WHERE id=?", (row["id"],))
                        if baseline:
                            self.db.execute(
                                "INSERT INTO changes(course,item,kind,time,data) VALUES(?,?,?,?,?)",
                                (course, row["id"], "unavailable", now, row["data"]),
                            )
            self.db.execute(
                "UPDATE sources SET last_success=?,error=? WHERE course=? AND source=?",
                (now, error if partial else None, course, source),
            )

    def record_calendar_coverage(self, course, start, end, *, now=None):
        """Record a successful, inclusive calendar fetch interval for one course."""
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO calendar_coverage VALUES(?,?,?,?)",
                (course, start, end, time.time() if now is None else now),
            )

    def calendar_coverage(self, course, start, end, *, now=None, max_age=300):
        """Return freshness for a requested interval, not merely any calendar sync."""
        now = time.time() if now is None else now
        row = self.db.execute(
            "SELECT start,end,last_success FROM calendar_coverage "
            "WHERE course=? AND start<=? AND end>=? "
            "ORDER BY (end-start) ASC LIMIT 1",
            (course, start, end),
        ).fetchone()
        return {
            "requested": {"start": int(start), "end": int(end)},
            "covered_by": ({"start": int(row["start"]), "end": int(row["end"])} if row else None),
            "last_success": row["last_success"] if row else None,
            "fresh": bool(row and now - row["last_success"] <= max_age),
        }

    def freshness(self, course=None):
        sql = "SELECT s.* FROM sources s JOIN courses c ON c.id=s.course WHERE c.selected=1 AND c.accessible=1"
        rows = self.db.execute(
            sql + (" AND course=?" if course else ""), (course,) if course else ()
        )
        return [dict(r) for r in rows]

    def items(self, course=None, source=None):
        sql = "SELECT i.data,i.first_seen FROM items i JOIN courses c ON c.id=i.course WHERE c.selected=1 AND c.accessible=1 AND i.available=1"
        args = []
        for field, val in [("i.course", course), ("i.source", source)]:
            if val is not None:
                sql += f" AND {field}=?"
                args.append(val)
        return [{**json.loads(r[0]), "first_seen": r[1]} for r in self.db.execute(sql, args)]

    def document(self, key):
        row = self.db.execute(
            "SELECT i.* FROM items i JOIN courses c ON c.id=i.course WHERE i.id=? AND i.available=1 AND c.selected=1 AND c.accessible=1",
            (key,),
        ).fetchone()
        if not row:
            raise FichError("document_unavailable")
        return {
            **json.loads(row["data"]),
            "course": row["course"],
            "index_current": bool(row["index_current"]),
        }

    def page(self, key, page, text, provenance, status, error=None):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?)",
                (key, page, text, provenance, status, error),
            )
            self.db.execute("DELETE FROM search WHERE item=? AND page=?", (key, page))
            if text:
                self.db.execute("INSERT INTO search VALUES(?,?,?)", (key, page, text))
            self.db.execute("UPDATE items SET index_current=1 WHERE id=?", (key,))

    def pages(self, key):
        self.document(key)
        return [
            dict(r)
            for r in self.db.execute("SELECT * FROM pages WHERE item=? ORDER BY page", (key,))
        ]

    def search(self, query, course=None, limit=20, offset=0):
        if not 1 <= limit <= 50 or not 0 <= offset <= 1000:
            raise FichError("invalid_pagination")
        query = literal_query(query)
        if not query:
            return []
        sql = """SELECT i.data,i.course,search.page,bm25(search) rank,
          snippet(search,2,'[',']',' … ',24) excerpt FROM search JOIN items i ON i.id=search.item
          JOIN courses c ON c.id=i.course WHERE search MATCH ? AND c.selected=1 AND c.accessible=1
          AND i.available=1 AND (search.page=0 OR i.index_current=1)"""
        args = [query]
        if course:
            sql += " AND i.course=?"
            args.append(course)
        rows = self.db.execute(sql + " ORDER BY rank LIMIT ? OFFSET ?", [*args, limit, offset])
        return [
            {
                "document_id": (d := json.loads(r["data"]))["id"],
                "title": d.get("title"),
                "url": d.get("url"),
                "course": r["course"],
                "page": r["page"] or None,
                "excerpt": r["excerpt"],
            }
            for r in rows
        ]

    def changes(self, since=None, course=None, limit=50, offset=0):
        if since is None:
            row = self.db.execute(
                "SELECT started FROM runs WHERE complete=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return []
            since = row[0]
        sql = "SELECT h.* FROM changes h JOIN courses c ON c.id=h.course WHERE c.selected=1 AND c.accessible=1 AND h.time>=?"
        args = [since]
        if course:
            sql += " AND h.course=?"
            args.append(course)
        return [
            {**dict(r), "data": json.loads(r["data"])}
            for r in self.db.execute(
                sql + " ORDER BY h.id DESC LIMIT ? OFFSET ?", [*args, limit, offset]
            )
        ]

    def cursor(self, key, value=None):
        if value is not None:
            with self.db:
                self.db.execute(
                    "INSERT OR REPLACE INTO cursors VALUES(?,?)", (key, json.dumps(value))
                )
            return value
        row = self.db.execute("SELECT data FROM cursors WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def clear_cursor(self, key):
        with self.db:
            self.db.execute("DELETE FROM cursors WHERE key=?", (key,))
