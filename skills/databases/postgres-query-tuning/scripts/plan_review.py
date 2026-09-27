#!/usr/bin/env python3
"""Flag the usual suspects in a PostgreSQL EXPLAIN (ANALYZE, BUFFERS) plan.

This is an advisory first pass, not a verdict. It parses the plan text, finds
the expensive parts, and prints the finding plus what to do about it. Always
read the plan yourself as well -- see references/plan-nodes.md.

Usage:
    psql -c 'EXPLAIN (ANALYZE, BUFFERS) SELECT ...' | python3 plan_review.py
    python3 plan_review.py --file plan.txt
    python3 plan_review.py --file plan.txt --max-findings 10
    python3 plan_review.py --file plan.txt --json

Exit codes:
    0  plan parsed, no significant findings
    1  usage error or plan could not be parsed
    2  findings reported (use --quiet to treat findings as success)

Stdlib only. Tested by ../tests/test_plan_review.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict

# Actual time line: (actual time=0.088..12.340 rows=45 loops=200)
ACTUAL_RE = re.compile(
    r"\(actual time=(?P<start>[\d.]+)\.\.(?P<total>[\d.]+)\s+rows=(?P<rows>\d+)"
    r"(?:\s+loops=(?P<loops>\d+))?\)"
)
COST_RE = re.compile(r"\(cost=[\d.]+\.\.[\d.]+\s+rows=(?P<rows>\d+)")
BUFFERS_RE = re.compile(
    r"Buffers:\s+shared hit=(?P<hit>\d+) read=(?P<read>\d+)(?:\s+dirtied=(?P<dirtied>\d+))?"
)
BATCHES_RE = re.compile(r"Batches:\s+(\d+)")
EXTERNAL_SORT_RE = re.compile(r"Sort Method:\s*external merge\s+Disk:\s*(\d+)")
LOSSY_RE = re.compile(r"Heap Blocks:\s*exact=\d+\s+lossy=(\d+)")
HEAP_FETCHES_RE = re.compile(r"Heap Fetches:\s*(\d+)")
ROWS_REMOVED_RE = re.compile(r"Rows Removed by Filter:\s*(\d+)")
WORKERS_PLANNED_RE = re.compile(r"Workers Planned:\s*(\d+)")
WORKERS_LAUNCHED_RE = re.compile(r"Workers Launched:\s*(\d+)")
RECHECK_RE = re.compile(r"Recheck Cond")

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Node:
    """One plan node with its parsed costs."""
    kind: str
    index: str
    relation: str
    depth: int
    est_rows: int | None
    act_rows: int | None
    loops: int
    total_ms: float
    start_ms: float
    raw: str

    @property
    def total_cost_ms(self) -> float:
        """What this node actually cost, including repeats. This is the number."""
        return self.total_ms * self.loops


@dataclass
class Finding:
    severity: str
    node: str
    problem: str
    fix: str


def _build_node(raw: str, actual: re.Match, cost: re.Match | None) -> "Node":
    kind, index, relation = _classify(raw.strip())
    return Node(
        kind=kind,
        index=index,
        relation=relation,
        depth=_depth(raw),
        est_rows=int(cost.group("rows")) if cost else None,
        act_rows=int(actual.group("rows")),
        loops=int(actual.group("loops") or 1),
        total_ms=float(actual.group("total")),
        start_ms=float(actual.group("start")),
        raw=raw.strip(),
    )


def parse_plan(text: str) -> tuple[list[Node], list[str]]:
    """Parse EXPLAIN output into nodes. Returns (nodes, parse warnings).

    A node is any line carrying an ``(actual time=...)`` block, which is
    exactly what ``EXPLAIN ANALYZE`` emits and plain ``EXPLAIN`` does not.
    """
    nodes: list[Node] = []
    warnings: list[str] = []

    for raw in text.splitlines():
        m = ACTUAL_RE.search(raw)
        if not m:
            continue
        nodes.append(_build_node(raw, m, COST_RE.search(raw)))

    if not nodes:
        warnings.append(
            "no 'actual time=' lines found. This plan was probably produced by "
            "EXPLAIN without ANALYZE -- estimates alone cannot show where the "
            "time actually goes."
        )
    return nodes, warnings


# "Index Scan using users_pkey on users", "Bitmap Index Scan on t",
# "Subquery Scan on s", "Index Only Scan using idx"
_SCAN_RE = re.compile(r"^(?P<kind>[A-Z][A-Za-z ]*?Scan)(?: using (?P<index>\S+))? on (?P<rel>\S+)")
# Everything else: "Nested Loop", "Hash Join", "Sort", "Aggregate", "Gather Merge"
_PLAIN_RE = re.compile(r"^(?P<kind>[A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)*)")


def _classify(line: str) -> tuple[str, str, str]:
    """Return (kind, index_name, relation) from a plan line."""
    head = line.split("(cost=")[0].split("(actual time=")[0].strip()
    head = re.sub(r"^(?:->\s*)+", "", head).strip()  # EXPLAIN draws child arrows
    m = _SCAN_RE.match(head)
    if m:
        return m.group("kind").strip(), m.group("index") or "", m.group("rel")
    m = _PLAIN_RE.match(head)
    if m:
        return m.group("kind").strip(), "", ""
    return head.split()[0] if head else "Unknown", "", ""


def _depth(raw: str) -> int:
    return (len(raw) - len(raw.lstrip())) // 2


def _label(node: Node) -> str:
    if node.relation:
        return f"{node.kind} on {node.relation}"
    if node.index:
        return f"{node.kind} using {node.index}"
    return node.kind


def analyse(text: str) -> list[Finding]:
    """Return findings, most severe first."""
    nodes, warnings = parse_plan(text)
    findings: list[Finding] = []

    for w in warnings:
        findings.append(Finding("high", "plan", w, "Re-run with EXPLAIN (ANALYZE, BUFFERS)"))

    if not nodes:
        return findings

    # --- N+1 / repeat explosion: the single most valuable signal ------------
    # A node that is individually cheap but repeated many times is the N+1
    # shape, so the trigger is the TOTAL cost, never the per-loop cost.
    for n in nodes:
        if n.loops >= 1000 and n.total_cost_ms >= 100:
            findings.append(Finding(
                "high", _label(n),
                f"executed {n.loops:,} times, costing {n.total_cost_ms:,.0f} ms in total "
                f"({n.total_ms} ms per loop)",
                "This is an N+1 shape. Join or batch the inner lookup instead of "
                "looping. Multiplying total time by loops is what exposes it.",
            ))

    # --- Sort/hash spill to disk -------------------------------------------
    for line in text.splitlines():
        m = EXTERNAL_SORT_RE.search(line)
        if m:
            mb = int(m.group(1)) / (1024 * 1024)
            findings.append(Finding(
                "high", "Sort",
                f"sort spilled to disk ({mb:.0f} MB external merge)",
                "Raise work_mem for this session (SET work_mem = '64MB') rather than "
                "globally, or add an index that removes the sort entirely.",
            ))
    for m in BATCHES_RE.finditer(text):
        if int(m.group(1)) > 1:
            findings.append(Finding(
                "high", "Hash",
                f"hash join/aggregate spilled across {m.group(1)} batches",
                "Raise work_mem for the session, or reduce the build-side input size.",
            ))
            break

    # --- Bitmap index scan lossiness ----------------------------------------
    m = LOSSY_RE.search(text)
    if m and int(m.group(1)) > 0:
        findings.append(Finding(
            "medium", "Bitmap Index Scan",
            f"bitmap is lossy ({m.group(1)} lossy heap blocks) so rows must be rechecked",
            "Raise work_mem so the bitmap fits in memory.",
        ))
    if RECHECK_RE.search(text):
        findings.append(Finding(
            "medium", "Bitmap Index Scan",
            "Recheck Cond present: rows are re-fetched from the heap and re-tested",
            "Either the bitmap spilled, or a Filter is not covered by the index. "
            "Check for a Filter line that the index cannot satisfy.",
        ))

    # --- Index-only scan not actually index-only ---------------------------
    m = HEAP_FETCHES_RE.search(text)
    if m and int(m.group(1)) > 0:
        n = int(m.group(1))
        sev = "high" if n > 1000 else "medium"
        findings.append(Finding(
            sev, "Index Only Scan",
            f"performed {n:,} heap fetches, so it is not really index-only",
            "The visibility map is stale. VACUUM (ANALYZE) the table. If it recurs, "
            "a covering index will not help until autovacuum keeps up.",
        ))

    # --- Wrong statistics ---------------------------------------------------
    for n in nodes:
        if n.est_rows and n.act_rows is not None and n.loops == 1:
            est, act = n.est_rows, n.act_rows
            if act >= 1000 and est >= 1 and (act / est >= 50 or est / act >= 50):
                direction = "underestimated" if act > est else "overestimated"
                findings.append(Finding(
                    "high", _label(n),
                    f"row estimate is off by ~{max(act // max(est,1), est // max(act,1))}x "
                    f"(estimated {est:,}, actual {act:,}) -- {direction}",
                    "Every join and loop decision above this node is suspect. "
                    "ANALYZE the table; if it recurs, raise default_statistics_target "
                    "for that column or add extended statistics (CREATE STATISTICS).",
                ))

    # --- Seq scan discarding most rows --------------------------------------
    # "Rows Removed by Filter" is indented under the scan node it belongs to.
    scan_by_line: dict[int, Node] = {}
    for idx, line in enumerate(text.splitlines()):
        m = ACTUAL_RE.search(line)
        if m and "Scan" in _classify(line.strip())[0]:
            scan_by_line[idx] = _build_node(line, m, COST_RE.search(line))

    for i, line in enumerate(text.splitlines()):
        m = ROWS_REMOVED_RE.search(line)
        if not m:
            continue
        removed = int(m.group(1))
        if removed < 10000:
            continue
        # Walk upward to the nearest enclosing scan node.
        owner = None
        for j in range(i, max(i - 4, -1), -1):
            if j in scan_by_line:
                owner = scan_by_line[j]
                break
        if owner and "Seq Scan" in owner.kind:
            findings.append(Finding(
                "high", _label(owner),
                f"discarded {removed:,} rows at the Filter stage after reading them",
                "Index the filter column(s). This is a full scan doing the work of "
                "an index lookup.",
            ))
        elif owner:
            findings.append(Finding(
                "medium", _label(owner),
                f"discarded {removed:,} rows at the Filter stage",
                "A Filter is not covered by the index being used. Consider including "
                "the filter column in the index.",
            ))

    # --- Parallelism requested but unavailable -----------------------------
    planned = WORKERS_PLANNED_RE.search(text)
    launched = WORKERS_LAUNCHED_RE.search(text)
    if planned and launched and int(launched.group(1)) == 0 and int(planned.group(1)) > 0:
        findings.append(Finding(
            "medium", "Gather",
            "planner wanted parallel workers but none were launched",
            "Check max_parallel_workers_per_gather and the global "
            "max_parallel_workers pool; the pool may be exhausted.",
        ))

    # --- I/O vs CPU ---------------------------------------------------------
    m = BUFFERS_RE.search(text)
    if m:
        hit = int(m.group("hit"))
        read = int(m.group("read"))
        dirtied = int(m.group("dirtied") or 0)
        if read > 0 and read > hit:
            findings.append(Finding(
                "medium", "plan",
                f"more blocks read from disk ({read:,}) than served from cache ({hit:,})",
                "This plan is I/O bound. Reduce the number of pages touched "
                "(indexes, batching) -- a faster CPU will not help.",
            ))
        elif hit > 0 and read == 0:
            findings.append(Finding(
                "low", "plan",
                "fully served from cache (no disk reads)",
                "The remaining cost is CPU, not I/O. More RAM or faster disk will "
                "not help; reduce the work or parallelise it.",
            ))
        if dirtied > 1000:
            findings.append(Finding(
                "low", "plan",
                f"dirtied {dirtied:,} pages",
                "The query is writing substantially. If this is a read path, "
                "check for a CTE or trigger causing unexpected writes.",
            ))

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 3), f.problem))
    return findings


SEVERITY_MARK = {"high": "[HIGH]  ", "medium": "[MED]   ", "low": "[info]  "}


def render(findings: list[Finding], plan_text: str) -> str:
    """Human-readable report. Always states a count, even at zero."""
    out: list[str] = []
    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1

    if not findings:
        out.append("0 findings: no significant problems detected in this plan.")
        out.append("Check the caller too -- a fast query in a loop is still a slow request.")
        return "\n".join(out)

    out.append(
        f"{len(findings)} finding(s): "
        f"{counts['high']} high, {counts['medium']} medium, {counts['low']} info"
    )
    out.append("")
    for f in findings:
        out.append(f"{SEVERITY_MARK[f.severity]}{f.node}")
        out.append(f"    problem: {f.problem}")
        out.append(f"    fix:     {f.fix}")
        out.append("")
    out.append("This is advisory. Read the plan yourself -- see references/plan-nodes.md")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: psql -c 'EXPLAIN (ANALYZE, BUFFERS) SELECT 1' | python3 plan_review.py",
    )
    ap.add_argument("--file", "-f", help="read the plan from a file instead of stdin")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    ap.add_argument("--max-findings", type=int, default=12, help="cap output (default: 12)")
    ap.add_argument("--quiet", action="store_true", help="exit 0 even when findings exist")
    args = ap.parse_args(argv)

    if args.file:
        try:
            with open(args.file, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
            return 1
    else:
        if sys.stdin.isatty():
            print("error: no input. Pipe a plan in, or pass --file.", file=sys.stderr)
            print("hint:  psql -c 'EXPLAIN (ANALYZE, BUFFERS) SELECT ...' | python3 plan_review.py",
                  file=sys.stderr)
            return 1
        text = sys.stdin.read()

    if not text.strip():
        print("error: input was empty", file=sys.stderr)
        return 1

    findings = analyse(text)[: args.max_findings]

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        print(render(findings, text))

    if findings and not args.quiet:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
