#!/usr/bin/env python3
"""
turso.py — tiny stdlib-only Turso (libsql) HTTP client.

Used by:
  * job_search.py   — upserts normalized jobs into the `jobs` table each run.
  * site/server.py  — serves the job-board site and proxies /api/jobs queries.

No third-party packages required; talks to Turso's HTTP /v2/pipeline endpoint.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

TIMEOUT = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    company     TEXT,
    location    TEXT,
    country     TEXT,
    eligible_countries TEXT,
    remote      INTEGER NOT NULL DEFAULT 0,
    url         TEXT,
    source      TEXT,
    posted      TEXT,
    salary      TEXT,
    snippet     TEXT,
    description TEXT,
    role_fit    TEXT,
    seniority   TEXT,
    relocation  INTEGER NOT NULL DEFAULT 0,
    track       TEXT,
    company_url TEXT,
    match_score INTEGER,
    match_reasons TEXT,
    match_gaps TEXT,
    matched_skills TEXT,
    missing_skills TEXT,
    excluded     INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_track      ON jobs(track);
CREATE INDEX IF NOT EXISTS idx_jobs_country    ON jobs(country);
CREATE INDEX IF NOT EXISTS idx_jobs_source     ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_posted     ON jobs(posted);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen  ON jobs(last_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_remote     ON jobs(remote);
CREATE INDEX IF NOT EXISTS idx_jobs_match      ON jobs(match_score);

CREATE TABLE IF NOT EXISTS applications (
    job_id      TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'draft',
    method      TEXT,
    recipient   TEXT,
    subject     TEXT,
    notes       TEXT,
    resume_path TEXT,
    cover_path  TEXT,
    email_path  TEXT,
    match_score INTEGER,
    applied_at  TEXT,
    created_at  TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_updated ON applications(updated_at);
"""

# Columns written by the job search pipeline (kept in lockstep with the
# normalized dict produced by job_search.normalize()).
UPSERT_COLS = (
    "id", "title", "company", "location", "country", "eligible_countries", "remote", "url",
    "source", "posted", "salary", "snippet", "description", "role_fit",
    "seniority", "relocation", "track", "company_url",
    "match_score", "match_reasons", "match_gaps", "matched_skills", "missing_skills", "excluded",
    "first_seen", "last_seen", "active", "created_at", "updated_at",
)

UPSERT_SQL = """
INSERT INTO jobs ({cols})
VALUES ({ph})
ON CONFLICT(id) DO UPDATE SET
    title=excluded.title,
    company=excluded.company,
    location=excluded.location,
    country=excluded.country,
    eligible_countries=excluded.eligible_countries,
    remote=excluded.remote,
    url=excluded.url,
    source=excluded.source,
    posted=excluded.posted,
    salary=excluded.salary,
    snippet=excluded.snippet,
    description=excluded.description,
    role_fit=excluded.role_fit,
    seniority=excluded.seniority,
    relocation=excluded.relocation,
    track=excluded.track,
    company_url=excluded.company_url,
    match_score=excluded.match_score,
    match_reasons=excluded.match_reasons,
    match_gaps=excluded.match_gaps,
    matched_skills=excluded.matched_skills,
    missing_skills=excluded.missing_skills,
    excluded=excluded.excluded,
    last_seen=excluded.last_seen,
    active=excluded.active,
    updated_at=excluded.updated_at
""".format(cols=", ".join(UPSERT_COLS), ph=", ".join("?" * len(UPSERT_COLS)))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base_url(db_url: str) -> str:
    """Normalize a libsql:// URL into its https:// HTTP endpoint."""
    return db_url.replace("libsql://", "https://", 1).rstrip("/")


def pipeline(db_url: str, token: str, requests: list) -> dict:
    """Run a batch of SQL statements via Turso's /v2/pipeline endpoint."""
    body = json.dumps({"requests": requests}).encode("utf-8")
    req = urllib.request.Request(
        _base_url(db_url) + "/v2/pipeline",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Turso HTTP {e.code}: {detail[:400]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Turso connection error: {e.reason}") from e


def _typed(v):
    """Wrap a Python value into Turso's typed Value envelope. The HTTP API
    expects an internally-tagged enum, e.g. {'type': 'text', 'value': 'x'},
    and numeric values are transported as strings."""
    if v is None:
        return {"type": "null", "value": None}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, (int, float)):
        return {"type": "integer" if isinstance(v, int) else "real", "value": str(v)}
    return {"type": "text", "value": str(v)}


def _result_error(result: dict) -> str | None:
    if not isinstance(result, dict):
        return "malformed Turso result"
    if result.get("type") == "error":
        return result.get("error", {}).get("message", "unknown Turso error")
    return None


def _check_results(response: dict, context: str) -> list[dict]:
    results = response.get("results") if isinstance(response, dict) else None
    if not isinstance(results, list):
        raise RuntimeError(f"Turso {context}: malformed pipeline response")
    errors = [_result_error(result) for result in results if _result_error(result)]
    if errors:
        raise RuntimeError(f"Turso {context}: {errors[0]}")
    return results


def execute(db_url: str, token: str, sql: str, args: list | None = None) -> dict:
    """Run a single statement; returns the raw result row."""
    response = pipeline(db_url, token, [{
        "type": "execute",
        "stmt": {"sql": sql, "args": [_typed(a) for a in (args or [])]},
    }])
    results = _check_results(response, "execute")
    if not results:
        raise RuntimeError("Turso execute returned no result")
    return results[0]


def _unwrap(value):
    """Turso HTTP API returns typed cells like {'type': 'text', 'value': 'x'}
    (or the raw scalar for null/empty). Collapse them to plain Python values,
    converting numeric cells back to numbers."""
    if isinstance(value, dict) and "type" in value:
        if value.get("type") == "null" or "value" not in value:
            return None
        v = value["value"]
        t = value["type"]
        if t in ("integer", "real") and v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return v
        return v
    return value


def query_rows(db_url: str, token: str, sql: str, args: list | None = None) -> list[dict]:
    """Run a query and return a list of row dicts keyed by column name."""
    res = execute(db_url, token, sql, args)
    if res.get("type") == "error":
        raise RuntimeError(res["error"].get("message", "unknown error"))
    resp = res.get("response", {})
    if resp.get("type") != "execute":
        return []
    cols = [c["name"] for c in resp["result"].get("cols", [])]
    return [dict(zip(cols, (_unwrap(v) for v in row)))
            for row in resp["result"].get("rows", [])]


COLUMN_MIGRATIONS = {
    "company": "TEXT",
    "location": "TEXT",
    "country": "TEXT",
    "eligible_countries": "TEXT",
    "remote": "INTEGER NOT NULL DEFAULT 0",
    "url": "TEXT",
    "source": "TEXT",
    "posted": "TEXT",
    "salary": "TEXT",
    "snippet": "TEXT",
    "description": "TEXT",
    "role_fit": "TEXT",
    "seniority": "TEXT",
    "relocation": "INTEGER NOT NULL DEFAULT 0",
    "track": "TEXT",
    "company_url": "TEXT",
    "match_score": "INTEGER",
    "match_reasons": "TEXT",
    "match_gaps": "TEXT",
    "matched_skills": "TEXT",
    "missing_skills": "TEXT",
    "excluded": "INTEGER NOT NULL DEFAULT 0",
    "first_seen": "TEXT",
    "last_seen": "TEXT",
    "active": "INTEGER NOT NULL DEFAULT 1",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}


def _ensure_columns(db_url: str, token: str) -> None:
    """Migrate databases created by older versions of this skill."""
    rows = query_rows(db_url, token, "PRAGMA table_info(jobs)")
    existing = {str(row.get("name")) for row in rows}
    if not {"id", "title"}.issubset(existing):
        raise RuntimeError("Turso jobs table is missing the id/title primary schema")
    for name, definition in COLUMN_MIGRATIONS.items():
        if name not in existing:
            execute(db_url, token, f"ALTER TABLE jobs ADD COLUMN {name} {definition}")


def init_schema(db_url: str, token: str) -> None:
    """Create/migrate the jobs table and indexes (idempotent)."""
    statements = [stmt.strip() for stmt in SCHEMA.split(";") if stmt.strip()]
    table_request = [{
        "type": "execute",
        "stmt": {"sql": stmt},
    } for stmt in statements if stmt.upper().startswith("CREATE TABLE")]
    _check_results(pipeline(db_url, token, table_request), "schema create")
    _ensure_columns(db_url, token)
    index_request = [{
        "type": "execute",
        "stmt": {"sql": stmt},
    } for stmt in statements if stmt.upper().startswith("CREATE INDEX")]
    if index_request:
        _check_results(pipeline(db_url, token, index_request), "schema indexes")


def deactivate_missing_jobs(db_url: str, token: str, active_ids: list[str],
                            batch_size: int = 400, grace_days: int = 7) -> int:
    """Hide missing rows only after a grace period.

    Search sources are paginated/bounded, so absence from one run is not proof
    that a job closed. Keeping recently seen rows for a grace window prevents a
    bounded snapshot from hiding valid jobs.
    """
    ids = {str(job_id) for job_id in active_ids if job_id}
    existing = query_rows(db_url, token, "SELECT id, last_seen FROM jobs WHERE active = 1")
    cutoff = datetime.now(timezone.utc).timestamp() - max(0, grace_days) * 86400

    def old_enough(value) -> bool:
        if not value:
            return True
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp() < cutoff
        except (TypeError, ValueError, OverflowError):
            return True

    missing = [
        str(row.get("id")) for row in existing
        if str(row.get("id")) not in ids and old_enough(row.get("last_seen"))
    ]
    changed = 0
    for start in range(0, len(missing), batch_size):
        chunk = missing[start:start + batch_size]
        placeholders = ",".join("?" for _ in chunk)
        sql = f"UPDATE jobs SET active = 0, updated_at = ? WHERE id IN ({placeholders})"
        result = execute(db_url, token, sql, [now_iso(), *chunk])
        response = result.get("response", {}) if isinstance(result, dict) else {}
        result_body = response.get("result", {}) if isinstance(response, dict) else {}
        changed += int(
            result_body.get("rows_affected", 0)
            or result_body.get("affected_row_count", 0)
            or 0
        )
    return changed


def upsert_jobs(db_url: str, token: str, items: list[dict], batch_size: int = 100) -> int:
    """Upsert a batch of normalized job dicts; returns the number of rows."""
    today = now_iso()
    rows = []
    for it in items:
        rows.append([
            it.get("id"), it.get("title"), it.get("company"),
            it.get("location"), it.get("country"), it.get("eligible_countries"),
            1 if it.get("remote") else 0,
            it.get("url"), it.get("source"), it.get("posted"),
            it.get("salary"), it.get("snippet"), it.get("description"),
            it.get("role_fit"), it.get("seniority"),
            1 if it.get("relocation") else 0,
            it.get("_track") or it.get("track"), it.get("company_url"),
            it.get("match_score"), it.get("match_reasons"), it.get("match_gaps"),
            it.get("matched_skills"), it.get("missing_skills"),
            1 if it.get("excluded") else 0,
            today, today, 1, today, today,
        ])
    upserted = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        requests = [{
            "type": "execute",
            "stmt": {"sql": UPSERT_SQL, "args": [_typed(v) for v in row]},
        } for row in chunk]
        response = pipeline(db_url, token, requests)
        results = _check_results(response, f"upsert batch {start // batch_size + 1}")
        if len(results) != len(requests) or any(result.get("type") != "ok" for result in results):
            raise RuntimeError("Turso upsert returned an incomplete batch")
        upserted += len(results)
    return upserted


# ---------------------------------------------------------------------------
# Applications (draft → applied → interview → …) tracking
# ---------------------------------------------------------------------------

APPLICATION_STATUSES = (
    "draft", "ready", "applied", "interview", "rejected", "offer", "withdrawn",
)

APPLICATION_COLS = (
    "job_id", "status", "method", "recipient", "subject", "notes",
    "resume_path", "cover_path", "email_path", "match_score",
    "applied_at", "created_at", "updated_at",
)


def upsert_application(db_url: str, token: str, app: dict) -> None:
    """Insert or update one application row (keyed on job_id)."""
    now = now_iso()
    values = [
        app.get("job_id"), app.get("status") or "draft", app.get("method"),
        app.get("recipient"), app.get("subject"), app.get("notes"),
        app.get("resume_path"), app.get("cover_path"), app.get("email_path"),
        app.get("match_score"),
        app.get("applied_at"), app.get("created_at") or now, now,
    ]
    if app.get("status") == "applied" and not app.get("applied_at"):
        values[10] = now
    update_clause = ", ".join(
        f"{col}=excluded.{col}" for col in APPLICATION_COLS if col not in ("job_id", "created_at")
    )
    # Preserve an existing applied_at rather than clearing it on re-draft.
    update_clause = update_clause.replace(
        "applied_at=excluded.applied_at",
        "applied_at=COALESCE(excluded.applied_at, applications.applied_at)",
    )
    # created_at is immutable once set.
    update_clause += ", created_at=applications.created_at"
    sql = (
        f"INSERT INTO applications ({', '.join(APPLICATION_COLS)}) "
        f"VALUES ({', '.join('?' * len(APPLICATION_COLS))}) "
        f"ON CONFLICT(job_id) DO UPDATE SET {update_clause}"
    )
    execute(db_url, token, sql, values)


def update_application_status(db_url: str, token: str, job_id: str,
                              status: str, notes: str | None = None,
                              method: str | None = None,
                              recipient: str | None = None) -> None:
    """Update the status (and optional fields) of an existing application."""
    if status not in APPLICATION_STATUSES:
        raise ValueError(f"unknown application status: {status}")
    now = now_iso()
    sets = ["status = ?", "updated_at = ?"]
    args: list = [status, now]
    if status == "applied":
        sets.append("applied_at = COALESCE(applied_at, ?)")
        args.append(now)
    for col, value in (("notes", notes), ("method", method), ("recipient", recipient)):
        if value is not None:
            sets.append(f"{col} = ?")
            args.append(value)
    args.append(job_id)
    execute(db_url, token, f"UPDATE applications SET {', '.join(sets)} WHERE job_id = ?", args)


def get_application(db_url: str, token: str, job_id: str) -> dict | None:
    rows = query_rows(db_url, token, "SELECT * FROM applications WHERE job_id = ?", [job_id])
    return rows[0] if rows else None


def get_job(db_url: str, token: str, job_id: str) -> dict | None:
    """Fetch one job by id (used by the tailoring pipeline)."""
    rows = query_rows(db_url, token, "SELECT * FROM jobs WHERE id = ?", [job_id])
    return rows[0] if rows else None


def list_applications(db_url: str, token: str, status: str | None = None,
                      limit: int = 500) -> list[dict]:
    if status:
        return query_rows(
            db_url, token,
            "SELECT * FROM applications WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
            [status, max(1, int(limit))],
        )
    return query_rows(
        db_url, token,
        "SELECT * FROM applications ORDER BY updated_at DESC LIMIT ?",
        [max(1, int(limit))],
    )