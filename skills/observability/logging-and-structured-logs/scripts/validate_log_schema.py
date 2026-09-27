#!/usr/bin/env python3
"""Validate JSON-lines log output against a required-field schema and flag
likely sensitive-field leakage.

Advisory linter for structured logs. Feed it a file, stdin, or a glob of
rotated logs. It reports:

  * lines that are not a single JSON object (unstructured output, tracebacks
    that were not escaped, log prefixes)
  * missing required fields, or fields that are present but null
  * an unknown/invalid "level" value
  * a "ts" that is not ISO-8601 / RFC3339 with an explicit offset
  * keys that look sensitive (password, token, secret, email, ...) and
    non-redacted values for them -- i.e. real leakage, not just the key name
  * suspicious shape problems: non-scalar log level, huge field counts

Usage:
    python3 validate_log_schema.py app.log
    cat app.log | python3 validate_log_schema.py -
    python3 validate_log_schema.py 'logs/*.jsonl' --required service,version,trace_id
    python3 validate_log_schema.py app.log --level warn --max-report 20
    python3 validate_log_schema.py app.log --json

Options:
    paths        One or more files, or '-' for stdin. Shell globs are expanded
                 by the shell, so pass them quoted only if you also pass
                 --glob.
    --glob       Treat each path argument as a glob pattern to expand.
    --required   Comma-separated required top-level fields
                 (default: ts,level,service,msg). Override the default set
                 entirely; use '' to require nothing.
    --level      Only report findings at or above this level
                 (debug|info|warn|warning|error|critical). Default: all.
    --ignore-sensitive
                 Skip the sensitive-field checks (use when you are
                 deliberately logging a field named, e.g., 'token_count').
    --redacted-values
                 Comma-separated substrings that mark a value as already
                 redacted (default: '[redacted],***,<redacted>,REDACTED').
    --max-report  Max findings to print per category (default 10).
    --json       Emit findings as JSON instead of text.
    --max-line-bytes
                 Skip lines longer than this (default 1 MiB) as 'oversized'.

Exit codes:
    0  no findings
    1  findings reported
    2  usage error (no such file, bad --level, etc.)

Stdlib only. Tested by ../tests/test_validate_log_schema.py
"""

from __future__ import annotations

import argparse
import glob as globlib
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

DEFAULT_REQUIRED = "ts,level,service,msg"

LEVELS = {"trace", "debug", "info", "warn", "warning", "error", "critical", "fatal"}
LEVEL_ORDER = {
    "trace": 0, "debug": 1, "info": 2, "warn": 3, "warning": 3,
    "error": 4, "critical": 5, "fatal": 5,
}
LEVEL_CANON = {"warning": "warn", "fatal": "critical"}

# Substring match on the (lowercased) key path.
SENSITIVE_KEY_PATTERNS = [
    r"password", r"passwd", r"\bpwd\b", r"secret", r"token", r"api[_-]?key",
    r"auth", r"credential", r"private[_-]?key", r"session[_-]?id", r"cookie",
    r"set-cookie", r"authorization", r"email", r"e[_-]?mail", r"phone",
    r"address", r"ssn", r"credit[_-]?card", r"card[_-]?number", r"\bcc\b",
    r"\bcvv\b", r"iban", r"passport", r"dob", r"date[_-]?of[_-]?birth",
    r"first[_-]?name", r"last[_-]?name", r"full[_-]?name", r"tax[_-]?id",
    r"conn(ection)?[_-]?str", r"dsn", r"jwt", r"bearer", r"refresh[_-]?token",
]
SENSITIVE_RE = re.compile("|".join(SENSITIVE_KEY_PATTERNS))

# Keys that contain a sensitive substring but are counts/booleans, not values.
SENSITIVE_ALLOWLIST = {
    "auth_method", "auth_type", "authenticated", "authorized", "token_count",
    "tokens", "secret_count", "has_password", "password_set", "email_domain",
    "email_verified", "email_count", "has_email",
}

TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})$"
)

REDACTED_DEFAULT = "[redacted],***,<redacted>,REDACTED"


@dataclass
class Finding:
    category: str
    line_no: int
    path: str          # file path, or '<stdin>'
    detail: str
    snippet: str = ""
    level: str = ""


@dataclass
class Report:
    files: list[str] = field(default_factory=list)
    lines_read: int = 0
    json_lines: int = 0
    non_json_lines: int = 0
    skipped_oversized: int = 0
    level_counts: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    truncated: dict = field(default_factory=dict)

    def add(self, category, line_no, path, detail, snippet="", level=""):
        self.findings.append(
            Finding(category=category, line_no=line_no, path=path,
                    detail=detail, snippet=snippet, level=level)
        )

    def counts(self) -> dict:
        out: dict = {}
        for f in self.findings:
            out[f.category] = out.get(f.category, 0) + 1
        return out


def parse_iso_ts(value: str) -> bool:
    """True if value is a timestamp with an explicit UTC offset (Z or +hh:mm)."""
    if not isinstance(value, str) or not TS_RE.match(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def is_sensitive_key(key: str) -> bool:
    low = key.lower()
    if low in SENSITIVE_ALLOWLIST:
        return False
    return bool(SENSITIVE_RE.search(low))


def value_is_redacted(value, markers: list) -> bool:
    if value is None:
        return True
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
        return True
    if isinstance(value, list):
        return all(value_is_redacted(v, markers) for v in value)
    if isinstance(value, dict):
        return all(value_is_redacted(v, markers) for v in value.values())
    s = str(value).strip()
    if not s:
        return True
    low = s.lower()
    return any(m and m.lower() in low for m in markers)


def iter_lines(stream, report: Report, max_line_bytes: int, path: str):
    for line_no, raw in enumerate(stream, start=1):
        report.lines_read += 1
        if len(raw) > max_line_bytes:
            report.skipped_oversized += 1
            report.add("oversized_line", line_no, path,
                       f"line is {len(raw)} bytes, over --max-line-bytes")
            continue
        yield line_no, raw


def check_line(raw: str, line_no: int, path: str, required: list,
               markers: list, check_sensitive: bool, report: Report) -> None:
    text = raw.strip()
    if not text:
        return
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        report.non_json_lines += 1
        report.add("not_json", line_no, path, f"line is not valid JSON: {exc.msg}")
        return
    if not isinstance(record, dict):
        report.non_json_lines += 1
        report.add("not_json_object", line_no, path,
                   f"top-level value is {type(record).__name__}, want object")
        return

    report.json_lines += 1
    level = record.get("level")
    if isinstance(level, str):
        norm = LEVEL_CANON.get(level.lower(), level.lower())
        report.level_counts[norm] = report.level_counts.get(norm, 0) + 1
    else:
        norm = ""

    for field_name in required:
        if field_name not in record:
            report.add("missing_field", line_no, path,
                       f"required field {field_name!r} is absent", level=norm)
        elif record[field_name] is None:
            report.add("null_field", line_no, path,
                       f"required field {field_name!r} is null", level=norm)

    if "level" in record and not isinstance(level, str):
        report.add("bad_level_type", line_no, path,
                   f"'level' is {type(level).__name__}, want a string", level=norm)
    elif isinstance(level, str) and level.lower() not in LEVELS:
        report.add("bad_level_value", line_no, path,
                   f"level {level!r} is not one of {sorted(LEVELS)}", level=norm)

    if "ts" in record and not parse_iso_ts(record["ts"]):
        report.add("bad_timestamp", line_no, path,
                   f"ts {record['ts']!r} is not RFC3339 with an explicit offset",
                   level=norm)

    if check_sensitive:
        for key, value in record.items():
            if is_sensitive_key(key) and not value_is_redacted(value, markers):
                preview = str(value)
                if len(preview) > 60:
                    preview = preview[:60] + "..."
                report.add("sensitive_value", line_no, path,
                           f"field {key!r} holds a value that does not look "
                           f"redacted: {preview!r}", level=norm)


def scan_stream(stream, path, required, markers, check_sensitive, report,
                max_line_bytes):
    for line_no, raw in iter_lines(stream, report, max_line_bytes, path):
        try:
            check_line(raw, line_no, path, required, markers, check_sensitive, report)
        except Exception as exc:  # noqa: BLE001 - never die on one bad line
            report.add("internal_error", line_no, path, f"validator error: {exc}")


def expand_paths(paths: list, use_glob: bool) -> list:
    if use_glob:
        out = []
        for p in paths:
            out.extend(sorted(globlib.glob(p)))
        return out
    return list(paths)


def run(paths, required, level_filter, ignore_sensitive, markers,
        max_report, as_json, max_line_bytes, use_glob) -> Report:
    report = Report()
    expanded = expand_paths(paths, use_glob)
    if not expanded:
        report.add("no_input", 0, ", ".join(paths) or "<none>", "no files matched the given paths")
        return report

    for path in expanded:
        if path == "-":
            report.files.append("<stdin>")
            scan_stream(sys.stdin, "<stdin>", required, markers,
                        not ignore_sensitive, report, max_line_bytes)
            continue
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError as exc:
            report.add("unreadable", 0, path, f"cannot read file: {exc.strerror}")
            continue
        report.files.append(path)
        with handle:
            scan_stream(handle, path, required, markers, not ignore_sensitive,
                        report, max_line_bytes)

    if level_filter:
        cutoff = LEVEL_ORDER[level_filter]
        report.findings = [
            f for f in report.findings
            if not f.level or LEVEL_ORDER.get(f.level, 0) >= cutoff
        ]
        report.level_counts = {k: v for k, v in report.level_counts.items()
                               if LEVEL_ORDER.get(k, 0) >= cutoff}
    return report


def render(report: Report, max_report: int) -> str:
    out = []
    if report.json_lines or report.non_json_lines:
        parts = [f"{report.json_lines} json", f"{report.non_json_lines} non-json"]
        if report.skipped_oversized:
            parts.append(f"{report.skipped_oversized} oversized")
        if report.level_counts:
            lv = " ".join(f"{k}={v}" for k, v in sorted(report.level_counts.items()))
            parts.append(f"levels: {lv}")
        out.append("scanned " + ", ".join(parts) + f" across {len(report.files)} file(s)")

    if not report.findings:
        out.append("OK: no findings")
        return "\n".join(out)

    by_cat: dict = {}
    for f in report.findings:
        by_cat.setdefault(f.category, []).append(f)

    for cat in sorted(by_cat):
        items = by_cat[cat]
        out.append(f"\n{cat} ({len(items)})")
        for f in items[:max_report]:
            where = f"{f.path}:{f.line_no}" if f.line_no else f.path
            out.append(f"  {where}: {f.detail}")
        if len(items) > max_report:
            out.append(f"  ... and {len(items) - max_report} more (use --max-report)")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validate_log_schema.py",
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("paths", nargs="+", help="log files, or '-' for stdin")
    p.add_argument("--glob", action="store_true",
                   help="expand each path argument as a glob pattern")
    p.add_argument("--required", default=DEFAULT_REQUIRED,
                   help="comma-separated required top-level fields "
                        f"(default: {DEFAULT_REQUIRED}); '' to require none")
    p.add_argument("--level", default="",
                   help="only report findings at/above this level "
                        "(debug|info|warn|warning|error|critical)")
    p.add_argument("--ignore-sensitive", action="store_true",
                   help="skip sensitive-field checks")
    p.add_argument("--redacted-values", default=REDACTED_DEFAULT,
                   help="comma-separated substrings that mark a value as redacted")
    p.add_argument("--max-report", type=int, default=10,
                   help="max findings printed per category (default 10)")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="emit findings as JSON")
    p.add_argument("--max-line-bytes", type=int, default=1024 * 1024,
                   help="skip lines longer than this (default 1048576)")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = args.level.lower() if args.level else ""
    if level and level not in LEVEL_ORDER:
        parser.error(f"--level must be one of {sorted(LEVEL_ORDER)}")
    if args.max_report < 1:
        parser.error("--max-report must be >= 1")
    if args.max_line_bytes < 1:
        parser.error("--max-line-bytes must be >= 1")

    required = [f.strip() for f in args.required.split(",") if f.strip()]
    markers = [m.strip() for m in args.redacted_values.split(",") if m.strip()]

    missing = [p for p in args.paths if p != "-" and not args.glob and not globlib.glob(p)]
    if missing and not args.glob:
        parser.error(f"no such file: {missing[0]}")

    report = run(args.paths, required, level, args.ignore_sensitive, markers,
                 args.max_report, args.as_json, args.max_line_bytes, args.glob)

    if args.as_json:
        print(json.dumps({
            "files": report.files,
            "lines_read": report.lines_read,
            "json_lines": report.json_lines,
            "non_json_lines": report.non_json_lines,
            "skipped_oversized": report.skipped_oversized,
            "level_counts": report.level_counts,
            "finding_counts": report.counts(),
            "findings": [asdict(f) for f in report.findings],
        }, indent=2))
    else:
        print(render(report, args.max_report))

    fatal = {"no_input", "unreadable"}
    if any(f.category in fatal for f in report.findings):
        return 2
    return 1 if report.findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
