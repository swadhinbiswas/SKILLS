#!/usr/bin/env python3
"""Validate JSON files (or stdin) and pinpoint the first error in each.

Built on the stdlib json module, but adds what `python -m json.tool` and
`jq` do not give you in one pass:

  * validates MANY files at once and reports a per-file OK/FAIL summary
  * reports the line and column of the first error, with a caret marker and
    the offending line, so you can see the problem without opening the file
  * flags duplicate keys (json.loads silently keeps the last one) — a very
    common cause of "the config is being ignored"
  * flags a UTF-8 BOM, which makes a strictly-conformant parser fail on an
    otherwise fine file
  * --strict rejects NaN/Infinity (accepted by python's json but not valid
    JSON); the default rejects them too, but --no-strict allows them
  * --stats reports size, top-level type, and element counts

Usage:
    python3 jsonlint.py FILE [FILE ...]
    cat data.json | python3 jsonlint.py -
    python3 jsonlint.py --strict config/*.json
    python3 jsonlint.py --stats users.json
    python3 jsonlint.py --max-depth 2 --quiet big.json

Exit codes:
    0  every input parsed (or a parseable stream was validated)
    1  at least one input is not valid JSON, or stdin failed
    2  usage error

Stdlib only. See ../tests/test_jsonlint.py
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DuplicateKeyError(ValueError):
    """Raised when --no-dupe-keys is off (default on) and a key repeats."""

    def __init__(self, key: str, first: int, second: int) -> None:
        super().__init__(f"duplicate object key {key!r}")
        self.key = key
        self.first = first
        self.second = second


def _no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, int] = {}
    for index, (key, _value) in enumerate(pairs):
        if key in seen:
            raise DuplicateKeyError(key, seen[key], index)
        seen[key] = index
    return dict(pairs)


def parse_json(
    text: str,
    *,
    reject_duplicates: bool = True,
    allow_nan: bool = False,
) -> Any:
    """Parse JSON text, raising ValueError subclasses with position info.

    json's JSONDecodeError already carries lineno/colno/pos; the extra work
    here is duplicate-key detection and a uniform error type.
    """
    kwargs: dict[str, Any] = {}
    if reject_duplicates:
        kwargs["object_pairs_hook"] = _no_duplicate_pairs

    def _reject_constant(name: str) -> Any:
        raise ValueError(f"{name} is not valid JSON (pass --no-strict to allow)")

    # Python's json accepts NaN/Infinity by default; make the choice explicit.
    kwargs["parse_constant"] = (lambda name: name) if allow_nan else _reject_constant
    try:
        return json.loads(text, **kwargs)
    except json.JSONDecodeError:
        raise
    except DuplicateKeyError as exc:
        # Find where the second occurrence is so the caller can point at it.
        exc.lineno, exc.colno = _find_second_key(text, exc.key, exc.second)
        raise
    except RecursionError as exc:
        raise ValueError("input nests too deeply to parse (RecursionError)") from exc


def _find_second_key(text: str, key: str, ordinal: int) -> tuple[int, int]:
    """Approximate the 1-based (line, col) of the `ordinal`-th (0-based)
    occurrence of `"key"` at the start of an object. Best effort."""
    needle = f'"{key}"'
    line = 1
    col = 1
    count = 0
    for ch_index, ch in enumerate(text):
        if text.startswith(needle, ch_index):
            if count == ordinal:
                return line, col
            count += 1
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1
    return line, col


def line_col_at(text: str, pos: int) -> tuple[int, int]:
    """Convert a 0-based character offset to 1-based (line, column)."""
    line = text.count("\n", 0, pos) + 1
    last_nl = text.rfind("\n", 0, pos)
    col = pos - last_nl
    return line, col


@dataclass
class Outcome:
    name: str
    ok: bool
    error: str | None = None
    line: int | None = None
    col: int | None = None
    snippet: str | None = None
    value: Any = None


def validate_text(
    name: str,
    text: str,
    *,
    reject_duplicates: bool,
    allow_nan: bool,
) -> Outcome:
    if text.startswith("\ufeff"):
        return Outcome(
            name,
            False,
            "file starts with a UTF-8 BOM; strip it (tail -c +4) "
            "or read with encoding='utf-8-sig'",
            line=1,
            col=1,
            snippet="\ufeff...",
        )
    try:
        value = parse_json(text, reject_duplicates=reject_duplicates, allow_nan=allow_nan)
    except json.JSONDecodeError as exc:
        line, col = exc.lineno, exc.colno
        lines = text.splitlines()
        snippet = lines[line - 1] if 0 < line <= len(lines) else None
        return Outcome(name, False, exc.msg, line=line, col=col, snippet=snippet, value=None)
    except DuplicateKeyError as exc:
        lines = text.splitlines()
        snippet = lines[exc.lineno - 1] if exc.lineno and 0 < exc.lineno <= len(lines) else None
        return Outcome(name, False, str(exc), line=exc.lineno, col=exc.colno, snippet=snippet)
    except ValueError as exc:
        return Outcome(name, False, str(exc))
    return Outcome(name, True, value=value)


def read_source(name: str) -> str:
    if name == "-":
        data = sys.stdin.buffer.read()
        return data.decode("utf-8", errors="replace")
    path = Path(name)
    if not path.exists():
        raise FileNotFoundError(f"no such file: {name}")
    if path.is_dir():
        raise IsADirectoryError(f"is a directory: {name}")
    return path.read_text(encoding="utf-8", errors="replace")


def describe(value: Any) -> str:
    kind = type(value).__name__
    if isinstance(value, dict):
        return f"object with {len(value)} key(s)"
    if isinstance(value, list):
        return f"array with {len(value)} item(s)"
    if isinstance(value, str):
        return f"string of length {len(value)}"
    return kind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate JSON files/stdin and pinpoint the first error in each.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help=(
            "JSON files to validate; '-' reads stdin. With no arguments, stdin "
            "is read when it is a pipe, otherwise usage is printed."
        ),
    )
    parser.add_argument(
        "--no-dupe-keys",
        action="store_true",
        help="allow duplicate object keys (default: reject them)",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="allow NaN/Infinity tokens (default: reject, since they are not valid JSON)",
    )
    parser.add_argument("--stats", action="store_true", help="report type/size summary per file")
    parser.add_argument("--quiet", action="store_true", help="print nothing; rely on exit code")
    args = parser.parse_args(argv)

    files = list(args.files)
    if not files:
        # No arguments: read stdin when it is piped in, otherwise show usage.
        if sys.stdin.isatty():
            parser.print_usage(sys.stderr)
            print("error: no files given and stdin is a terminal", file=sys.stderr)
            return 2
        files = ["-"]

    outcomes: list[Outcome] = []
    for name in files:
        try:
            text = read_source(name)
        except (FileNotFoundError, IsADirectoryError, OSError) as exc:
            outcomes.append(Outcome(name, False, str(exc)))
            continue
        outcomes.append(
            validate_text(
                name,
                text,
                reject_duplicates=not args.no_dupe_keys,
                allow_nan=args.no_strict,
            )
        )

    if not args.quiet:
        out = sys.stdout
        for o in outcomes:
            if o.ok:
                if args.stats:
                    size = ""
                    if o.name != "-":
                        try:
                            size = f", {Path(o.name).stat().st_size} bytes"
                        except OSError:
                            size = ""
                    print(f"OK    {o.name}: {describe(o.value)}{size}", file=out)
                else:
                    print(f"OK    {o.name}", file=out)
                continue
            print(f"FAIL  {o.name}: {o.error}", file=sys.stderr)
            if o.line is not None:
                print(f"      at line {o.line}, column {o.col}", file=sys.stderr)
            if o.snippet is not None:
                caret = " " * max((o.col or 1) - 1, 0) + "^"
                print(f"      {o.snippet.rstrip()}", file=sys.stderr)
                print(f"      {caret}", file=sys.stderr)

    return 1 if any(not o.ok for o in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
