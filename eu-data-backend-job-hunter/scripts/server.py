#!/usr/bin/env python3
"""
server.py — serves the job-board site and proxies /api/jobs to Turso.

The Turso token lives on the server (read from .env / env vars) and is never
sent to the browser. The frontend only talks to /api/jobs on this server.

Usage:
    python server.py [--port 8787] [--host 0.0.0.0]
    python server.py --port 8787            # env: PORT, HOST

Endpoints:
    /                       -> site/index.html
    /api/jobs?q=...&track=...&level=...&remote=1&reloc=1&country=...&source=...
              &min_match=50&hide_applied=1&sort=new|match|old|company&page=1&per=30
    /api/stats              -> counts for the header cards
    /api/options            -> distinct countries / sources / tracks
    /api/profile            -> whether a master profile is configured (no secrets)
    /api/applications       -> GET: tracked applications; POST: update status
    /api/tailor             -> POST {job_id}: generate resume/cover-letter/email drafts
    /api/draft/<id>/<file>  -> serve a generated draft file (whitelist-guarded)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # skill root
SCRIPTS_DIR = Path(__file__).resolve().parent
SITE_DIR = ROOT / "site"

sys.path.insert(0, str(SCRIPTS_DIR))
import turso  # noqa: E402
import apply as apply_mod  # noqa: E402
import master_profile  # noqa: E402

ALLOWED_DRAFT_FILES = {
    "resume.pdf", "resume.html", "resume.md",
    "cover_letter.md", "cover_letter.txt", "email.txt", "job.json", "meta.json",
}
MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Handler(BaseHTTPRequestHandler):
    server_version = "JobBoard/1.0"

    # -- helpers ---------------------------------------------------------

    @property
    def turso_url(self) -> str:
        return os.environ.get("TURSO_URL", "")

    @property
    def turso_token(self) -> str:
        return os.environ.get("TURSO_TOKEN", "")

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, msg: str) -> None:
        self._send_json({"error": msg}, status)

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if not self.turso_url or not self.turso_token:
            self._send_error(500, "TURSO_URL / TURSO_TOKEN not configured on server")
            return

        if path in ("/", "/index.html"):
            self._serve_file(SITE_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/styles.css":
            self._serve_file(SITE_DIR / "styles.css", "text/css; charset=utf-8")
        elif path == "/app.js":
            self._serve_file(SITE_DIR / "app.js", "application/javascript; charset=utf-8")
        elif path == "/api/jobs":
            self._handle_jobs(parsed.query)
        elif path == "/api/stats":
            self._handle_stats()
        elif path == "/api/options":
            self._handle_options()
        elif path == "/api/applications":
            self._handle_applications()
        elif path == "/api/profile":
            self._handle_profile()
        elif path.startswith("/api/draft/"):
            self._serve_draft(path)
        else:
            self._send_error(404, "not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if not self.turso_url or not self.turso_token:
            self._send_error(500, "TURSO_URL / TURSO_TOKEN not configured on server")
            return
        if path == "/api/tailor":
            self._handle_tailor()
        elif path == "/api/applications":
            self._handle_application_update()
        else:
            self._send_error(404, "not found")

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _serve_file(self, path: Path, mime: str) -> None:
        if not path.exists():
            self._send_error(404, "missing site file")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- API handlers -----------------------------------------------------

    def _handle_jobs(self, query: str) -> None:
        q = urllib.parse.parse_qs(query)
        get = lambda k, d="": (q.get(k) or [d])[0]

        clauses = ["active = 1"]
        args: list = []

        search = get("q", "").strip()
        if search:
            like = f"%{search}%"
            clauses.append(
                "(title LIKE ? OR company LIKE ? OR location LIKE ? OR snippet LIKE ?)"
            )
            args += [like, like, like, like]

        track = get("track", "")
        if track and track != "all":
            clauses.append("track = ?")
            args.append(track)

        level = get("level", "")
        if level and level != "all":
            clauses.append("seniority = ?")
            args.append(level)

        if get("remote", "") == "1":
            clauses.append("remote = 1")
        if get("reloc", "") == "1":
            clauses.append("relocation = 1")
        if get("hide_applied", "") == "1":
            clauses.append(
                "id NOT IN (SELECT job_id FROM applications "
                "WHERE status IN ('applied','interview','offer'))"
            )
        try:
            min_match = int(get("min_match", "0") or 0)
        except (TypeError, ValueError):
            min_match = 0
        if min_match > 0:
            clauses.append("match_score >= ?")
            args.append(min_match)

        country = get("country", "")
        if country and country != "all":
            clauses.append(
                "(country = ? OR ',' || COALESCE(eligible_countries, '') || ',' LIKE ?)"
            )
            args.extend([country, f"%,{country},%"])

        source = get("source", "")
        if source and source != "all":
            clauses.append("source = ?")
            args.append(source)

        sort = get("sort", "new")
        order = {
            "new": "posted DESC, first_seen DESC",
            "old": "posted ASC",
            "company": "company COLLATE NOCASE ASC",
            "match": "match_score IS NULL ASC, match_score DESC, posted DESC",
        }.get(sort, "posted DESC, first_seen DESC")

        try:
            per = max(1, min(int(get("per", "30")), 100))
        except (TypeError, ValueError):
            per = 30
        try:
            page = max(int(get("page", "1")), 1)
        except (TypeError, ValueError):
            page = 1
        offset = (page - 1) * per

        where = " AND ".join(clauses)
        sql = (
            f"SELECT * FROM jobs WHERE {where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?"
        )
        count_sql = f"SELECT COUNT(*) AS n FROM jobs WHERE {where}"

        try:
            rows = turso.query_rows(self.turso_url, self.turso_token, sql,
                                    args + [per, offset])
            count_rows = turso.query_rows(self.turso_url, self.turso_token,
                                          count_sql, args)
        except Exception as e:  # noqa: BLE001
            self._send_error(500, f"query failed: {e}")
            return

        total = count_rows[0]["n"] if count_rows else 0
        self._send_json({
            "jobs": rows,
            "total": total,
            "page": page,
            "per": per,
            "pages": (total + per - 1) // per,
        })

    def _handle_stats(self) -> None:
        try:
            total = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT COUNT(*) AS n FROM jobs WHERE active = 1",
            )
            today = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT COUNT(*) AS n FROM jobs WHERE active = 1 AND last_seen >= ?",
                [f"{__import__('datetime').date.today().isoformat()}T00:00:00Z"],
            )
            remote = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT COUNT(*) AS n FROM jobs WHERE active = 1 AND remote = 1",
            )
            reloc = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT COUNT(*) AS n FROM jobs WHERE active = 1 AND relocation = 1",
            )
            by_track = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT track, COUNT(*) AS n FROM jobs WHERE active = 1 GROUP BY track",
            )
            sources = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT source, COUNT(*) AS n FROM jobs WHERE active = 1 GROUP BY source ORDER BY n DESC",
            )
        except Exception as e:  # noqa: BLE001
            self._send_error(500, f"stats failed: {e}")
            return

        def _v(rows, key="n"):
            return rows[0][key] if rows else 0

        self._send_json({
            "total": _v(total),
            "today": _v(today),
            "remote": _v(remote),
            "reloc": _v(reloc),
            "tracks": {r["track"] or "other": r["n"] for r in by_track},
            "sources": [{"name": r["source"], "count": r["n"]} for r in sources],
        })

    def _handle_options(self) -> None:
        try:
            countries = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT DISTINCT country FROM jobs WHERE active = 1 "
                "AND country != '' ORDER BY country",
            )
            sources = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT DISTINCT source FROM jobs WHERE active = 1 ORDER BY source",
            )
            eligible = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT DISTINCT eligible_countries FROM jobs WHERE active = 1 "
                "AND eligible_countries IS NOT NULL AND eligible_countries != ''",
            )
        except Exception as e:  # noqa: BLE001
            self._send_error(500, f"options failed: {e}")
            return
        country_values = {r["country"] for r in countries if r.get("country")}
        for row in eligible:
            country_values.update(
                code for code in str(row.get("eligible_countries") or "").split(",") if code
            )
        self._send_json({
            "countries": sorted(country_values),
            "sources": [r["source"] for r in sources],
        })

    # -- applications / tailoring -----------------------------------------

    def _load_profile(self) -> dict:
        try:
            return master_profile.load_profile(os.environ.get("PROFILE_PATH") or None)
        except (OSError, ValueError):
            return {}

    def _handle_profile(self) -> None:
        profile = self._load_profile()
        self._send_json({
            "configured": master_profile.profile_is_usable(profile),
            "name": profile.get("name"),
            "headline": profile.get("headline"),
            "path": profile.get("_path"),
            "tracks": profile.get("tracks", []),
            "skills": profile.get("skills", {}),
        })

    def _handle_applications(self) -> None:
        try:
            rows = turso.query_rows(
                self.turso_url, self.turso_token,
                "SELECT a.*, j.title, j.company, j.url, j.location, j.country, j.track "
                "FROM applications a LEFT JOIN jobs j ON j.id = a.job_id "
                "ORDER BY a.updated_at DESC LIMIT 500",
            )
        except Exception as e:  # noqa: BLE001
            self._send_error(500, f"applications query failed: {e}")
            return
        counts: dict[str, int] = {}
        for row in rows:
            key = row.get("status") or "draft"
            counts[key] = counts.get(key, 0) + 1
        self._send_json({"applications": rows, "counts": counts, "total": len(rows)})

    def _handle_application_update(self) -> None:
        body = self._read_json_body()
        job_id = str(body.get("job_id") or "").strip()
        status = str(body.get("status") or "").strip()
        if not job_id or status not in turso.APPLICATION_STATUSES:
            self._send_error(400, "job_id and a valid status are required")
            return
        try:
            turso.update_application_status(
                self.turso_url, self.turso_token, job_id, status, body.get("notes")
            )
        except Exception as e:  # noqa: BLE001
            self._send_error(500, f"status update failed: {e}")
            return
        self._send_json({"ok": True, "job_id": job_id, "status": status})

    def _handle_tailor(self) -> None:
        body = self._read_json_body()
        job_id = str(body.get("job_id") or "").strip()
        if not job_id:
            self._send_error(400, "job_id is required")
            return
        profile = self._load_profile()
        if not master_profile.profile_is_usable(profile):
            self._send_error(400, "no usable master profile configured on the server")
            return
        try:
            job = turso.get_job(self.turso_url, self.turso_token, job_id)
        except Exception as e:  # noqa: BLE001
            self._send_error(500, f"job lookup failed: {e}")
            return
        if not job:
            self._send_error(404, f"job {job_id} not found")
            return
        try:
            manifest = apply_mod.tailor_job(job, profile, out_root=ROOT / "applications")
            apply_mod.record_draft(self.turso_url, self.turso_token, manifest)
        except Exception as e:  # noqa: BLE001
            self._send_error(500, f"tailoring failed: {e}")
            return
        files = {name: f"/api/draft/{job_id}/{Path(path).name}"
                 for name, path in manifest.get("files", {}).items()}
        self._send_json({"ok": True, "manifest": {**manifest, "urls": files}})

    def _serve_draft(self, path: str) -> None:
        parts = path[len("/api/draft/"):].split("/")
        if len(parts) != 2:
            self._send_error(400, "expected /api/draft/<job_id>/<file>")
            return
        job_id, filename = parts
        if filename not in ALLOWED_DRAFT_FILES or ".." in job_id or "/" in job_id:
            self._send_error(400, "invalid draft path")
            return
        base = (ROOT / "applications").resolve()
        target = (base / job_id / filename).resolve()
        if base != target.parent.parent and base not in target.parents:
            self._send_error(403, "forbidden")
            return
        if not target.exists():
            self._send_error(404, "draft file not found")
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         MIME_BY_SUFFIX.get(target.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")))
    parser.add_argument("--profile", default=None,
                        help="Master profile path for tailoring (default: profile/master_profile.json|yaml)")
    args = parser.parse_args()

    _load_dotenv(ROOT / ".env")
    if args.profile:
        os.environ.setdefault("PROFILE_PATH", args.profile)

    if not os.environ.get("TURSO_URL") or not os.environ.get("TURSO_TOKEN"):
        print("  [error] TURSO_URL / TURSO_TOKEN not set (see .env)", file=sys.stderr)
        sys.exit(1)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"  [info] job board running at http://{args.host}:{args.port}",
          file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  [info] shutting down", file=sys.stderr)


if __name__ == "__main__":
    main()
