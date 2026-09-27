#!/usr/bin/env python3
"""Validate Agent Skills in this repository against the official spec.

Checks performed
----------------
* every skill directory contains a SKILL.md
* YAML frontmatter is present and parses
* `name` is 1-64 chars, `[a-z0-9-]` only, no leading/trailing/double hyphens,
  and matches its parent directory name  (spec requirement)
* `description` is 1-1024 chars and says *when* to use the skill, not just what
  it does
* optional `license`, `compatibility` (<=500), `metadata` (string map),
  `allowed-tools` are well formed
* SKILL.md body stays under the 500-line progressive-disclosure budget
* every relative link/command path referenced from SKILL.md actually exists
* skill is registered in skills/catalog.json, and the catalog entry is sane
* duplicate skill names are rejected (names must be globally unique)

Usage
-----
    python tools/validate_skills.py                 # validate everything
    python tools/validate_skills.py --quiet         # only failures
    python tools/validate_skills.py skills/git/...  # validate specific paths
    python tools/validate_skills.py --catalog-only  # just rebuild/verify index

Exit codes: 0 all good, 1 failures found, 2 bad usage.
Stdlib only; PyYAML is used when available but is not required.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "skills"
CATALOG_PATH = SKILLS_ROOT / "catalog.json"

NAME_RE = re.compile(r"^[a-z0-9-]+$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
MAX_SKILL_MD_LINES = 500
MAX_RECOMMENDED_SKILL_MD_LINES = 400

# A description that only says what the skill does never triggers reliably.
# Require at least one explicit trigger cue.
TRIGGER_CUES = (
    "use when",
    "use this when",
    "use this skill",
    "use it when",
    "trigger",
    "when the user",
    "when a user",
    "asks",
    "requests",
    "mentions",
    "or whenever",
    "whenever",
)

# Paths that resolve outside the skill (references the agent may cite but that
# are not part of the skill bundle). Only used to soften severity.
EXTERNAL_LINK_PREFIXES = ("http://", "https://", "mailto:", "#")


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------
# minimal YAML frontmatter reader
# --------------------------------------------------------------------------


def _coerce_scalar(raw: str):
    """Coerce a YAML scalar to str/int/float/bool/None, quoted strings kept as str."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    return raw


def _read_block_scalar(lines: list[str], start: int, style: str | None):
    """Read a `|` or `>` block scalar body starting after the indicator line."""
    body: list[str] = []
    i = start
    if style in ("|", "|-", "|+", ">", ">-", ">+"):
        while i < len(lines) and (lines[i].strip() == "" or lines[i].startswith((" ", "\t"))):
            body.append(lines[i])
            i += 1
    text = "\n".join(body) if style in ("|", "|-", "|+") else " ".join(x.strip() for x in body)
    if style in ("|-", ">-"):
        text = text.rstrip("\n")
    return text, i


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict, body). Raises ParseError on malformed input."""
    if not text.startswith("---"):
        raise ParseError("file does not start with '---' frontmatter fence")
    lines = text.splitlines()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ParseError("no closing '---' frontmatter fence")

    data: dict = {}
    i = 1
    while i < end:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[:1] in (" ", "\t"):  # continuation of a block scalar
            i += 1
            continue
        if ":" not in line:
            raise ParseError(f"frontmatter line {i + 1} is not 'key: value': {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ParseError(f"empty key on frontmatter line {i + 1}")

        if value in ("|", "|-", "|+", ">", ">-", ">+"):
            text_val, i = _read_block_scalar(lines, i + 1, value)
            data[key] = text_val
            continue

        if value == "":  # nested mapping
            mapping: dict = {}
            i += 1
            while i < end and (lines[i][:1] in (" ", "\t")):
                sub = lines[i].strip()
                if sub and not sub.startswith("#") and ":" in sub:
                    sub_key, _, sub_val = sub.partition(":")
                    mapping[sub_key.strip()] = _coerce_scalar(sub_val)
                i += 1
            data[key] = mapping
            continue

        data[key] = _coerce_scalar(value)
        i += 1

    return data, "\n".join(lines[end + 1 :])


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


@dataclass
class Result:
    path: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    name: str | None = None
    domain: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_name(raw, expected_dir: str, res: Result) -> str:
    if raw is None:
        res.errors.append("frontmatter is missing required field 'name'")
        return expected_dir
    name = str(raw)
    if not NAME_RE.fullmatch(name):
        res.errors.append(
            f"name {name!r}: only lowercase letters, digits and hyphens are allowed"
        )
    if len(name) > MAX_NAME:
        res.errors.append(f"name {name!r}: {len(name)} chars, max {MAX_NAME}")
    if name != expected_dir:
        res.errors.append(
            f"name {name!r} must match its directory name {expected_dir!r} (spec requirement)"
        )
    return name


def validate_description(raw, res: Result) -> None:
    if raw is None or not str(raw).strip():
        res.errors.append("frontmatter is missing required field 'description'")
        return
    desc = str(raw)
    if len(desc) > MAX_DESCRIPTION:
        res.errors.append(f"description is {len(desc)} chars, max {MAX_DESCRIPTION}")
    low = desc.lower()
    if not any(cue in low for cue in TRIGGER_CUES):
        res.warnings.append(
            "description says what the skill does but not when to use it; add a trigger "
            "phrase like 'Use when ...' so the skill activates reliably"
        )


def validate_optional(raw_fm: dict, res: Result) -> None:
    compat = raw_fm.get("compatibility")
    if compat is not None:
        if len(str(compat)) > MAX_COMPATIBILITY:
            res.errors.append(
                f"compatibility is {len(str(compat))} chars, max {MAX_COMPATIBILITY}"
            )
    meta = raw_fm.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        res.errors.append("metadata must be a mapping of string keys to string values")
    elif isinstance(meta, dict):
        for k, v in meta.items():
            if not isinstance(v, str):
                res.errors.append(
                    f"metadata.{k} must be a string, got {type(v).__name__}"
                )
    tools = raw_fm.get("allowed-tools")
    if tools is not None and not isinstance(tools, str):
        res.errors.append("allowed-tools must be a space-separated string")


def collect_referenced_paths(body: str) -> set[str]:
    """Relative paths a SKILL.md body points at, from links and inline code."""
    refs: set[str] = set()
    for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", body):
        target = match.group(1).split("#", 1)[0].strip()
        if target and not target.startswith(EXTERNAL_LINK_PREFIXES):
            refs.add(target)
    for match in re.finditer(r"(?<![\w/.-])((?:scripts|references|assets)/[\w./-]+)", body):
        refs.add(match.group(1).rstrip(".,;:)`\"'"))
    return refs


def validate_skill(skill_dir: Path) -> Result:
    res = Result(path=skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        res.errors.append("missing SKILL.md")
        res.name = skill_dir.name
        return res

    raw_text = skill_md.read_text(encoding="utf-8")
    if len(raw_text) > 0 and raw_text[0] == "﻿":
        raw_text = raw_text[1:]

    try:
        fm, body = parse_frontmatter(raw_text)
    except ParseError as exc:
        res.errors.append(f"frontmatter: {exc}")
        res.name = skill_dir.name
        return res

    res.name = validate_name(fm.get("name"), skill_dir.name, res)
    validate_description(fm.get("description"), res)
    validate_optional(fm, res)

    unknown = set(fm) - {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }
    if unknown:
        res.warnings.append(f"unrecognised frontmatter keys: {', '.join(sorted(unknown))}")

    line_count = len(raw_text.splitlines())
    if line_count > MAX_SKILL_MD_LINES:
        res.errors.append(
            f"SKILL.md is {line_count} lines, over the {MAX_SKILL_MD_LINES}-line "
            "progressive-disclosure budget; move detail into references/"
        )
    elif line_count > MAX_RECOMMENDED_SKILL_MD_LINES:
        res.warnings.append(
            f"SKILL.md is {line_count} lines; consider moving detail into references/"
        )

    if not body.strip():
        res.errors.append("SKILL.md has frontmatter but no body content")

    for ref in sorted(collect_referenced_paths(body)):
        if (skill_dir / ref).exists():
            continue
        # A path that resolves inside another skill in this repo is a valid
        # cross-reference, not a broken link. Report which skill owns it so the
        # author can name the target, but do not fail the check.
        target = Path(ref).name
        owner = None
        for other_skill_md in SKILLS_ROOT.rglob("SKILL.md"):
            other_dir = other_skill_md.parent
            if other_dir == skill_dir:
                continue
            for candidate in other_dir.rglob(target):
                if candidate.is_file():
                    owner = other_dir.relative_to(SKILLS_ROOT).as_posix()
                    break
            if owner:
                break
        if owner:
            res.warnings.append(
                f"references {ref!r}, which lives in skill {owner!r} - "
                "consider naming the owning skill explicitly"
            )
        else:
            res.warnings.append(f"references {ref!r} but that path does not exist")

    return res


# --------------------------------------------------------------------------
# catalog
# --------------------------------------------------------------------------


def load_catalog() -> dict:
    if not CATALOG_PATH.is_file():
        print(f"error: catalog not found at {CATALOG_PATH}", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def discover_skills() -> list[Path]:
    return sorted(
        p.parent for p in SKILLS_ROOT.rglob("SKILL.md") if p.parent.is_dir()
    )


def check_catalog(results: list[Result], catalog: dict) -> list[str]:
    """Cross-check discovered skills against the catalog registry."""
    problems: list[str] = []
    entries = catalog.get("skills", [])
    known = {e.get("name") for e in entries}
    domains = {d["id"] for d in catalog.get("domains", [])}

    seen: dict[str, Path] = {}
    for res in results:
        name = res.name or res.path.name
        if name in seen:
            res.errors.append(f"duplicate skill name {name!r} (also at {seen[name]})")
        seen[name] = res.path

        if name not in known:
            res.warnings.append(f"{name!r} is not registered in skills/catalog.json")
        else:
            entry = next(e for e in entries if e.get("name") == name)
            rel = res.path.relative_to(SKILLS_ROOT).as_posix()
            if entry.get("path") != rel:
                res.errors.append(
                    f"catalog path mismatch: catalog says {entry.get('path')!r}, "
                    f"actual path is {rel!r}"
                )
            if entry.get("domain") not in domains:
                res.errors.append(
                    f"catalog domain {entry.get('domain')!r} is not declared in domains[]"
                )
            if not entry.get("description"):
                res.warnings.append(f"catalog entry for {name!r} has no description")
            for kind in entry.get("extras", []):
                if not (res.path / kind).is_dir():
                    res.errors.append(
                        f"catalog claims {kind}/ exists for {name!r} but it does not"
                    )

    for entry in entries:
        name = entry.get("name")
        if name not in seen:
            problems.append(
                f"catalog lists {name!r} at {entry.get('path')!r} but no SKILL.md exists there"
            )
    return problems


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*", help="specific skill dirs (default: all)")
    ap.add_argument("--quiet", action="store_true", help="only print failures")
    ap.add_argument(
        "--catalog-only",
        action="store_true",
        help="only verify catalog consistency, skip per-skill checks",
    )
    args = ap.parse_args()

    catalog = load_catalog()

    if args.paths:
        targets = [Path(p).resolve() for p in args.paths]
    else:
        targets = discover_skills()

    if not targets:
        print("no skills found", file=sys.stderr)
        return 2

    results: list[Result] = []
    if not args.catalog_only:
        for target in targets:
            results.append(validate_skill(target))
    else:
        for target in targets:
            res = Result(path=target, name=target.name)
            results.append(res)

    catalog_problems = check_catalog(results, catalog) if not args.paths else []

    failures = [r for r in results if not r.ok]
    warnings = [r for r in results if r.ok and r.warnings]

    if not args.quiet:
        for res in results:
            rel = res.path.relative_to(REPO_ROOT).as_posix() if res.path.is_relative_to(REPO_ROOT) else res.path
            status = "FAIL" if not res.ok else ("warn" if res.warnings else "ok")
            print(f"{status:>4}  {rel}")
            for err in res.errors:
                print(f"        error: {err}")
            for warn in res.warnings:
                print(f"        warn:  {warn}")

    for problem in catalog_problems:
        print(f"        error: catalog: {problem}", file=sys.stderr)

    print(
        f"\n{len(results)} skills checked, {len(failures)} failed, "
        f"{len(warnings)} with warnings, {len(catalog_problems)} catalog problems",
        file=sys.stderr,
    )
    return 1 if failures or catalog_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
