#!/usr/bin/env python3
"""Rebuild skills/catalog.json and the human index from the skills on disk.

The catalog is derived state: this script reads every skill directory, pulls
the real `description` out of its frontmatter, and writes the registry back.
Run it after adding or removing skills so `catalog.json`, `skills/INDEX.md`,
and the README stay in sync.

Usage:
    python tools/build_index.py            # rewrite catalog.json + skills/INDEX.md
    python tools/build_index.py --check    # fail if out of date, write nothing
    python tools/build_index.py --stats    # print counts, write nothing
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "skills"
CATALOG_PATH = SKILLS_ROOT / "catalog.json"
INDEX_PATH = SKILLS_ROOT / "INDEX.md"

# Used only when the checkout has no git remote to read a URL from. It is
# clearly a placeholder so nobody mistakes it for a working link.
FALLBACK_REPO_URL = "https://github.com/your-org/skills"


def detect_repo_url() -> str:
    """The GitHub URL of this checkout, so generated links never go stale."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return FALLBACK_REPO_URL
    url = out.stdout.strip()
    if not url:
        return FALLBACK_REPO_URL
    url = re.sub(r"^git@([^:]+):", r"https://\1/", url)   # scp-style -> https
    url = re.sub(r"\.git$", "", url)
    return url if url.startswith("http") else FALLBACK_REPO_URL


REPO_URL = detect_repo_url()

sys.path.insert(0, str(REPO_ROOT / "tools"))
from validate_skills import parse_frontmatter  # noqa: E402

# What each domain is for in one line, shown in the index.
DOMAIN_BLURB = {
    "ai": "Prompting, RAG, agents, evals, cost control, and model integration that survives production.",
    "api-design": "REST and GraphQL contracts, versioning, pagination, webhooks, and third-party integrations.",
    "architecture": "Boundaries, scaling, caching, queues, resilience, and making trade-offs explicit.",
    "backend": "Language-specific performance, concurrency, and idiomatic patterns.",
    "ci-cd": "Pipelines that are fast, reproducible, cache-aware, and safe to run unattended.",
    "cloud": "AWS, GCP, and Azure: IAM, networking, cost, and serverless service design.",
    "codebase": "Refactoring, design review, code review, and working in an unfamiliar repository.",
    "containers": "Dockerfiles, Compose, image size, build caching, and reproducible local environments.",
    "databases": "Query tuning, indexing, schema evolution, transactions, replication, and safe migrations.",
    "data-eng": "Pipelines, warehouses, streaming, orchestration, and data quality.",
    "debugging": "Systematic root-cause hunting for anything slow, leaking, crashing, or misleading.",
    "docs": "READMEs, ADRs, API docs, changelogs, and writing that people actually read.",
    "frontend": "Architecture, state, rendering performance, accessibility, and CSS.",
    "git-vcs": "History surgery, bisecting, refs, merges, and safe version-control workflows.",
    "iac": "Terraform, Ansible, drift, state, and modules that survive contact with reality.",
    "kubernetes": "Manifests, rollouts, networking, resources, and debugging workloads in production.",
    "mcp": "Building and consuming Model Context Protocol tools, plus authoring skills.",
    "mobile": "Native and React Native app lifecycles, Expo, and shipping to app stores.",
    "observability": "Metrics, logs, traces, alerting, SLOs, and incident response.",
    "product": "Specs, estimation, iteration practice, and shipping the right thing.",
    "terminal": "Shell fluency, search, text processing, and automation that does not surprise.",
    "testing": "Test design, flaky-test elimination, contract and load testing, and real coverage.",
}

DOMAIN_ORDER = list(DOMAIN_BLURB)


def read_skill(skill_dir: Path) -> dict | None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        fm, _ = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - skip malformed, validator will report it
        return None

    rel = skill_dir.relative_to(SKILLS_ROOT).as_posix()
    domain, _, name = rel.partition("/")
    description = str(fm.get("description", "")).strip()
    # The index reads better with the trigger clause trimmed off.
    summary = description.split(" Use when ")[0].split(" Use this when ")[0]
    summary = summary.split(". Triggers on ")[0].split(". Trigger on ")[0]
    summary = summary.strip().rstrip(".")
    if len(summary) > 160:
        summary = summary[:157].rsplit(" ", 1)[0] + "..."

    extras = [
        kind
        for kind in ("references", "scripts", "tests", "assets")
        if (skill_dir / kind).is_dir()
    ]
    return {
        "name": fm.get("name") or name,
        "domain": domain,
        "path": rel,
        "description": summary,
        "extras": extras,
    }


def collect() -> list[dict]:
    entries = []
    for skill_md in sorted(SKILLS_ROOT.rglob("SKILL.md")):
        skill_dir = skill_md.parent
        if skill_dir == SKILLS_ROOT:
            continue
        entry = read_skill(skill_dir)
        if entry:
            entries.append(entry)
    entries.sort(key=lambda e: (DOMAIN_ORDER.index(e["domain"])
                                if e["domain"] in DOMAIN_ORDER else 99, e["name"]))
    return entries


def build_domains(entries: list[dict]) -> list[dict]:
    seen = {}
    for e in entries:
        seen.setdefault(e["domain"], 0)
        seen[e["domain"]] += 1
    domains = []
    for did in DOMAIN_ORDER:
        if did not in seen:
            continue
        existing = {}
        if CATALOG_PATH.is_file():
            for d in json.loads(CATALOG_PATH.read_text(encoding="utf-8")).get("domains", []):
                if d.get("id") == did:
                    existing = d
                    break
        domains.append({
            "id": did,
            "title": existing.get("title") or did.replace("-", " ").title(),
            "summary": existing.get("summary") or DOMAIN_BLURB.get(did, ""),
            "count": seen[did],
        })
    # Any domain not in DOMAIN_ORDER but present on disk.
    for did, count in seen.items():
        if did not in DOMAIN_ORDER:
            domains.append({"id": did, "title": did.replace("-", " ").title(),
                            "summary": "", "count": count})
    return domains


def github_anchor(heading: str) -> str:
    """The anchor GitHub generates for a heading.

    GitHub lowercases, drops punctuation, and turns each run of non-word
    characters into a single hyphen. "Git & Version Control" therefore becomes
    "git--version-control" (the "&" collapses to a hyphen, and the surrounding
    spaces add two more). Guessing this wrong produces dead links, so compute
    it the same way GitHub does.
    """
    anchor = heading.lower()
    anchor = re.sub(r"[^\w\s-]", "", anchor)   # drop punctuation (incl. &)
    anchor = re.sub(r"\s", "-", anchor)        # each space -> one hyphen
    return anchor


def render_index(catalog: dict) -> str:
    entries = catalog["skills"]
    domains = catalog["domains"]
    out: list[str] = []
    out.append("# Skill index")
    out.append("")
    out.append(f"**{len(entries)} skills** across **{len(domains)} domains**.")
    out.append("")
    out.append("> Generated by `python tools/build_index.py` — do not edit by hand.")
    out.append("")
    out.append("[Back to the README](../README.md) · "
               "[Authoring rules](AUTHORING.md) · "
               f"[Report a problem]({REPO_URL}/issues)")
    out.append("")

    total = len(entries)
    out.append("| Domain | Skills | What it covers |")
    out.append("|---|---:|---|")
    for d in domains:
        anchor = github_anchor(d["title"])
        out.append(f"| [{d['title']}](#{anchor}) | {d['count']} | {d['summary']} |")
    out.append(f"| **Total** | **{total}** | |")
    out.append("")

    for d in domains:
        out.append(f"## {d['title']}")
        out.append("")
        out.append(f"*{d['summary']}*")
        out.append("")
        out.append("| Skill | What it does | |")
        out.append("|---|---|---|")
        for e in entries:
            if e["domain"] != d["id"]:
                continue
            badges = " ".join(f"`{x}/`" for x in e["extras"])
            name_cell = f"[`{e['name']}`]({e['path']}/SKILL.md)"
            out.append(f"| {name_cell} | {e['description']} | {badges} |")
        out.append("")

    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="verify sync, write nothing")
    ap.add_argument("--stats", action="store_true", help="print counts, write nothing")
    args = ap.parse_args()

    entries = collect()
    if not entries:
        print("error: no skills found", file=sys.stderr)
        return 2

    catalog = {
        "schema_version": 1,
        "description": (
            "Master registry of every skill in this repository. Generated by "
            "tools/build_index.py -- edit that, or the skills themselves, not this file."
        ),
        "domains": build_domains(entries),
        "skills": entries,
    }
    new_json = json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"
    new_index = render_index(catalog)

    if args.stats:
        print(f"{len(entries)} skills across {len(catalog['domains'])} domains")
        for d in catalog["domains"]:
            print(f"  {d['id']:<16} {d['count']:>3}")
        return 0

    old_json = CATALOG_PATH.read_text(encoding="utf-8") if CATALOG_PATH.is_file() else ""
    old_index = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.is_file() else ""

    if args.check:
        problems = []
        if old_json != new_json:
            problems.append("skills/catalog.json is out of date")
        if old_index != new_index:
            problems.append("skills/INDEX.md is out of date")
        if problems:
            for p in problems:
                print(f"error: {p}", file=sys.stderr)
            print("fix: run python tools/build_index.py", file=sys.stderr)
            return 1
        print(f"catalog and index are in sync ({len(entries)} skills)")
        return 0

    CATALOG_PATH.write_text(new_json, encoding="utf-8")
    INDEX_PATH.write_text(new_index, encoding="utf-8")
    print(f"wrote {CATALOG_PATH.relative_to(REPO_ROOT)} and "
          f"{INDEX_PATH.relative_to(REPO_ROOT)} ({len(entries)} skills)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
