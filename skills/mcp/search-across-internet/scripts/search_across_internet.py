#!/usr/bin/env python3
"""Search the internet for Agent Skills and rank them against a query.

Finds skills hosted on GitHub by combining several strategies that each catch
something the others miss, then scores what comes back against your query so
the most relevant skills sort first.

Strategies
----------
  topic      repositories tagged `claude-skills` / `agent-skills`
  keyword    free-text repository search (name + description)
  path       repositories whose tree contains a `SKILL.md`
  local      search the skills installed in this repository

Sources are tried in parallel-ish order and deduplicated by repo, so a repo
found two ways is returned once with both sources noted.

Auth
----
Unauthenticated GitHub search allows ~10 requests/minute. Set `GITHUB_TOKEN`
(and `GH_TOKEN` as an alias) to raise the limit to 30/minute for search and
5000/hour for the API. The token is only ever sent to api.github.com.

Usage
-----
    ./skills/search_across_internet.py "kubernetes debugging"
    ./skills/search_across_internet.py postgres --limit 20
    ./skills/search_across_internet.py react --json
    ./skills/search_across_internet.py --local "flaky test"      # offline
    ./skills/search_across_internet.py "mcp server" --include-local
    echo '<query>' | ./skills/search_across_internet.py            # stdin

Exit codes:
    0  results found (or cleanly none)
    1  usage error
    2  network/HTTP error, or rate-limited with no usable token

Stdlib only. See ../tests/test_search_across_internet.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from pathlib import Path

API = "https://api.github.com"
UA = "agent-skill-search/1.0 (+stdlib urllib)"
TIMEOUT = 20

# Topics that actually contain skill repositories.
SKILL_TOPICS = ["claude-skills", "agent-skills", "claude-code-skills", "ai-skills"]

# Query terms that strongly suggest a repo holds Agent Skills rather than
# being an unrelated project that happens to mention "skill".
SKILL_SIGNALS = (
    "skill", "skills", "claude", "agent", "agentskills", "mcp", "llm",
    "cursor", "copilot", "codex", "windsurf", "opencode", "prompt",
)


@dataclass
class Result:
    full_name: str
    url: str
    description: str = ""
    stars: int = 0
    sources: list[str] = field(default_factory=list)
    has_skill_md: bool = False
    skill_count: int = 0
    topics: list[str] = field(default_factory=list)
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    updated: str = ""


def get_token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def http_get(url: str, token: str = "") -> tuple[int, bytes, dict]:
    """GET a URL. Returns (status, body, headers). Raises nothing."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers or {})
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(f"network error contacting {url}: {exc}") from exc


def search_repos(query: str, token: str, per_page: int = 20,
                 sort: str = "stars") -> tuple[list[dict], str | None]:
    """Search repositories. Returns (items, error-or-None)."""
    params = urllib.parse.urlencode({
        "q": query, "sort": sort, "order": "desc", "per_page": per_page,
    })
    status, body, _ = http_get(f"{API}/search/repositories?{params}", token)
    if status == 403:
        return [], ("GitHub rate limit reached. Wait a minute, or set "
                    "GITHUB_TOKEN to raise the limit.")
    if status == 422:
        return [], f"GitHub rejected the query (422). Try fewer special characters."
    if status != 200:
        return [], f"GitHub returned HTTP {status}."
    try:
        return json.loads(body).get("items", []), None
    except (ValueError, UnicodeDecodeError) as exc:
        return [], f"could not parse GitHub response: {exc}"


def repo_tree_has_skill(full_name: str, token: str) -> tuple[bool, int]:
    """Does this repo contain SKILL.md files? Returns (found, count). Capped."""
    url = f"{API}/repos/{full_name}/git/trees/HEAD?recursive=1"
    status, body, _ = http_get(url, token)
    if status != 200:
        return False, 0
    try:
        tree = json.loads(body).get("tree", [])
    except (ValueError, UnicodeDecodeError):
        return False, 0
    count = sum(
        1 for node in tree
        if node.get("path", "").endswith("SKILL.md") and node.get("type") == "blob"
    )
    return count > 0, count


def score_result(r: Result, terms: list[str]) -> None:
    """Score 0-100 for relevance to the query terms, recording why."""
    haystack = f"{r.full_name} {r.description} {' '.join(r.topics)}".lower()
    reasons: list[str] = []
    score = 0.0

    if r.has_skill_md:
        score += 30
        reasons.append("contains SKILL.md")
        if r.skill_count > 1:
            score += min(15, r.skill_count)
            reasons.append(f"{r.skill_count} skills")

    name_lower = r.full_name.lower()
    desc_lower = r.description.lower()

    for term in terms:
        if term in name_lower:
            score += 22
            reasons.append(f"'{term}' in repo name")
        elif term in desc_lower:
            score += 12
            reasons.append(f"'{term}' in description")
        elif term in haystack:
            score += 5
            reasons.append(f"'{term}' in topics/metadata")

    # Popularity is a weak signal: it breaks ties, it does not rank.
    score += min(20, (r.stars ** 0.5) / 4)
    if r.stars >= 1000:
        reasons.append("widely used")
    elif r.stars >= 100:
        reasons.append("some usage")

    if r.updated:
        reasons.append(f"updated {r.updated}")

    r.score = round(min(100.0, score), 1)
    r.reasons = reasons[:4]


def collect_from_github(query: str, token: str, limit: int,
                        deep: bool) -> tuple[list[Result], list[str]]:
    """Run the GitHub strategies and merge the results."""
    terms = [t for t in re.split(r"[\s,+]+", query.lower()) if t]
    found: dict[str, Result] = {}
    notes: list[str] = []

    strategies: list[tuple[str, str]] = [
        ("topic", "topic:claude-skills"),
        ("topic", "topic:agent-skills"),
        ("keyword", query),
    ]
    if any(s in query.lower() for s in ("skill", "claude", "agent", "mcp")):
        strategies.insert(0, ("keyword", "SKILL.md"))

    for source, q in strategies:
        items, err = search_repos(q, token, per_page=max(limit, 10))
        if err:
            notes.append(f"{source} search ({q}): {err}")
        for item in items:
            name = item.get("full_name", "")
            if not name:
                continue
            r = found.get(name)
            if r is None:
                r = Result(
                    full_name=name,
                    url=item.get("html_url", f"https://github.com/{name}"),
                    description=(item.get("description") or "").strip(),
                    stars=item.get("stargazers_count", 0),
                    topics=[t for t in (item.get("topics") or [])][:8],
                    updated=(item.get("pushed_at") or "")[:10],
                )
                found[name] = r
            if source not in r.sources:
                r.sources.append(source)

    results = list(found.values())

    # The tree check is what separates "a repo about skills" from "a repo that
    # actually contains skills". It costs an API call each, so it is opt-in via
    # --deep and capped.
    if deep and results:
        for r in results[: min(limit, 15)]:
            r.has_skill_md, r.skill_count = repo_tree_has_skill(r.full_name, token)
            time.sleep(0.2)
    else:
        for r in results:
            if "skill" in f"{r.full_name} {r.description}".lower():
                r.has_skill_md = True
                r.skill_count = 1

    for r in results:
        score_result(r, terms)
    results.sort(key=lambda x: (-x.score, -x.stars))
    return results[:limit], notes


# --------------------------------------------------------------------------
# local search
# --------------------------------------------------------------------------


def find_catalog() -> Path | None:
    """Locate this repository's skills/catalog.json by walking up from the script."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "skills" / "catalog.json"
        if candidate.is_file():
            return candidate
    # Fall back to a sibling layout: <root>/catalog.json next to the skill.
    local = Path(__file__).resolve().parent.parent.parent / "catalog.json"
    return local if local.is_file() else None


def collect_local(query: str, limit: int) -> list[Result]:
    """Search the skills installed in this repository."""
    catalog = find_catalog()
    if catalog is None:
        return []
    try:
        data = json.loads(catalog.read_text(encoding="utf-8"))
    except ValueError:
        return []

    repo_root = catalog.parent.parent
    terms = [t for t in re.split(r"[\s,+]+", query.lower()) if t]
    results: list[Result] = []
    for entry in data.get("skills", []):
        rel = entry.get("path", "")
        name = entry.get("name", "")
        hay = f"{name} {rel} {entry.get('description','')}".lower()
        hits = [t for t in terms if t in hay]
        if not hits:
            continue
        r = Result(
            full_name=f"local/{entry.get('domain','?')}/{name}",
            url=str(repo_root / "skills" / rel / "SKILL.md"),
            description=entry.get("description", ""),
            stars=0,
            sources=["local"],
            has_skill_md=True,
        )
        score_result(r, terms)
        r.score = min(100.0, r.score + 20)  # an exact local hit is a strong signal
        r.reasons.insert(0, f"installed locally, matches {'/'.join(hits)}")
        results.append(r)
    results.sort(key=lambda x: -x.score)
    return results[:limit]


def render(results: list[Result], query: str, notes: list[str]) -> str:
    out: list[str] = []
    if not results:
        out.append(f'No skills found for "{query}".')
        out.append("")
        out.append("Try a broader term, or --include-local to search the "
                   "skills already installed in this repo.")
    else:
        out.append(f'{len(results)} result(s) for "{query}"')
        out.append("")
        for i, r in enumerate(results, 1):
            star = f"{r.stars:,} stars" if r.stars else "local"
            out.append(f"{i}. {r.full_name}  ({star}, score {r.score})")
            out.append(f"   {r.url}")
            if r.description:
                out.append(f"   {r.description}")
            if r.reasons:
                out.append(f"   why: {'; '.join(r.reasons)}")
            out.append("")
        out.append("Install one with:")
        out.append("  npx skills add <owner>/<repo>          # from the ecosystem")
        out.append("  ./install.sh --from <owner>/<repo> --skill <path>   # into this repo")
    if notes:
        out.append("")
        out.append("Notes:")
        for n in notes:
            out.append(f"  - {n}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Auth:\n"
            "  Unauthenticated GitHub search allows ~10 requests/minute.\n"
            "  Set GITHUB_TOKEN (or GH_TOKEN) to raise the limit. It is only\n"
            "  ever sent to api.github.com.\n"
            "\n"
            "Examples:\n"
            "  ./skills/search_across_internet.py 'kubernetes debugging'\n"
            "  ./skills/search_across_internet.py postgres --deep\n"
            "  ./skills/search_across_internet.py mcp --json\n"
            "  ./skills/search_across_internet.py --local 'flaky test'\n"
        ),
    )
    ap.add_argument("query", nargs="*", help="search terms (or pipe via stdin)")
    ap.add_argument("--limit", "-n", type=int, default=10, help="max results (default: 10)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--local", "-l", action="store_true",
                    help="only search skills installed in this repository (offline)")
    ap.add_argument("--include-local", action="store_true",
                    help="also search locally installed skills")
    ap.add_argument("--deep", action="store_true",
                    help="verify each repo actually contains SKILL.md (slower, more accurate)")
    args = ap.parse_args(argv)

    query = " ".join(args.query).strip()
    if not query and not sys.stdin.isatty():
        query = sys.stdin.read().strip()
    if not query:
        ap.print_usage(sys.stderr)
        print("error: no query given", file=sys.stderr)
        return 1

    if args.limit < 1 or args.limit > 100:
        print("error: --limit must be between 1 and 100", file=sys.stderr)
        return 1

    token = get_token()

    if args.local:
        results = collect_local(query, args.limit)
        notes: list[str] = []
        print(json.dumps([asdict(r) for r in results], indent=2) if args.json
              else render(results, query, notes))
        return 0

    try:
        results, notes = collect_from_github(query, token, args.limit, args.deep)
    except ConnectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("hint: check network access, or use --local for offline search.",
              file=sys.stderr)
        return 2

    if args.include_local:
        results.extend(collect_local(query, args.limit))
        results.sort(key=lambda x: -x.score)
        results = results[: args.limit]

    print(json.dumps([asdict(r) for r in results], indent=2) if args.json
          else render(results, query, notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
