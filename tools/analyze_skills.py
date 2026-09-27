#!/usr/bin/env python3
"""Per-skill quality analysis. Reports outliers worth a human's attention.

This does not decide whether a skill is *correct* — only a domain expert can do
that. It finds the structural signals that correlate with an unfinished or
unmaintained skill, so review effort goes to the right files.

Usage:
    python tools/analyze_skills.py                 # full report
    python tools/analyze_skills.py --worst 20     # the ones to look at first
    python tools/analyze_skills.py --json         # machine-readable
    python tools/analyze_skills.py --skill <path> # one skill, all signals

Stdlib only. Exit 0 always (it is a report, not a gate).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "skills"

sys.path.insert(0, str(REPO_ROOT / "tools"))
from validate_skills import parse_frontmatter  # noqa: E402

# A skill with a Gotchas section is making a specific claim about its domain.
# Without one, it is more likely to be generic advice an LLM could produce.
# Key is the report field; value is the heading pattern that sets it.
# Headings may be numbered ("## 8. Edge cases and failure modes") and use
# synonyms, so the patterns are deliberately generous. A false negative here
# sends a reviewer to a skill that was already fine.
SECTION_PATTERNS = {
    "has_gotchas": r"^#{2,4}\s*(?:\d+\.\s*)?("
                   r"gotchas?|traps?|pitfalls?|failure modes?|anti-?patterns?|"
                   r"anti-?goals?|common mistakes|things that go wrong|"
                   r"when .* (?:goes wrong|breaks|fails)|don'?t do this|never\b|"
                   r"caution|warnings?"
                   r")\b",
    "has_workflow": r"^#{2,4}\s*(?:\d+\.\s*)?("
                    r"workflow|process|steps?|how to|the loop|method|"
                    r"before you (start|run|ship)"
                    r")\b",
    "has_safety": r"^#{2,4}\s*(?:\d+\.\s*)?("
                  r"safety|when not to|before you (run|do|deploy)|danger|approval|"
                  r"never\b|do not\b|rollback|production"
                  r")\b",
    "has_checklist": r"^#{2,4}\s*(?:\d+\.\s*)?("
                    r"checklist|before you ship|definition of done|first-run|"
                    r"review checklist|acceptance criteria"
                    r")\b",
}

# Phrases that signal the skill is restating what a model already knows.
GENERIC_PHRASES = [
    "best practices",
    "it is important",
    "in today's",
    "make sure to",
    "as needed",
    "appropriately",
    "leverage",
    "utilize",
    "in order to",
    "it is recommended to",
]

# Concrete, checkable content: a flag, an env var, a path, an error string.
SPECIFICITY_MARKERS = re.compile(
    r"(--[a-z][a-z0-9-]{2,}|`[A-Z][A-Z0-9_]{3,}`|\$?[A-Z_][A-Z0-9_]{3,}|"
    r"[A-Z][a-z]+Error|error:|warn:|Traceback|\.py|\.go|\.rs|\.ts|\.tsx|"
    r"kubectl |docker |psql |terraform |git |curl |jq |rg )"
)

WEAK_TRIGGER = re.compile(r"^.{0,40}$")


@dataclass
class SkillReport:
    name: str
    path: str
    lines: int
    words: int
    has_gotchas: bool = False
    has_workflow: bool = False
    has_safety: bool = False
    has_checklist: bool = False
    specificity: float = 0.0        # concrete markers per 100 lines
    code_blocks: int = 0
    tables: int = 0
    generic_hits: int = 0
    refs: int = 0
    scripts: int = 0
    tests: int = 0
    desc_len: int = 0
    trigger_cues: int = 0
    has_compat: bool = False
    issues: list[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def path_str(self) -> str:
        return self.path


def analyse(skill_md: Path) -> SkillReport:
    text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    lines = text.splitlines()
    rep = SkillReport(
        name=str(fm.get("name", skill_md.parent.name)),
        path=Path(skill_md.parent).resolve().relative_to(REPO_ROOT).as_posix(),
        lines=len(lines),
        words=len(body.split()),
        code_blocks=body.count("```") // 2,
        tables=len(re.findall(r"^\|.*\|$", body, re.M)),
        desc_len=len(str(fm.get("description", ""))),
        has_compat=bool(fm.get("compatibility")),
    )

    for key, pattern in SECTION_PATTERNS.items():
        if re.search(pattern, body, re.M | re.I):
            setattr(rep, key, True)

    markers = len(SPECIFICITY_MARKERS.findall(body))
    rep.specificity = round(markers / max(rep.lines, 1) * 100, 1)

    low = body.lower()
    rep.generic_hits = sum(low.count(p) for p in GENERIC_PHRASES)

    rep.refs = sum(1 for _ in (skill_md.parent / "references").glob("*.md")) \
        if (skill_md.parent / "references").is_dir() else 0
    rep.scripts = sum(1 for _ in (skill_md.parent / "scripts").iterdir()) \
        if (skill_md.parent / "scripts").is_dir() else 0
    rep.tests = sum(1 for _ in (skill_md.parent / "tests").glob("test_*.py")) \
        if (skill_md.parent / "tests").is_dir() else 0

    desc = str(fm.get("description", ""))
    rep.trigger_cues = sum(1 for cue in
                           ("use when", "use this when", "trigger", "when the user",
                            "when a user", "asks", "requests", "mentions", "whenever")
                           if cue in desc.lower())

    # ---------------------------------------------------------------- scoring
    # A prose skill (a spec template, a writing guide) has no commands to
    # offer, so scoring it against a command-density target is misleading.
    # Judge each against what is appropriate for its kind.
    is_prose = rep.scripts == 0 and rep.code_blocks == 0
    score = 50.0
    score += 12 if rep.has_gotchas else -10
    score += 8 if rep.has_workflow else 0
    score += 6 if rep.has_safety else 0
    score += 5 if rep.has_checklist else 0
    if is_prose:
        # Reward structure instead: templates, tables, concrete examples.
        score += 6 if rep.tables >= 2 else 0
        score += 4 if rep.words >= 900 else 0
    else:
        score += 5 if rep.specificity >= 15 else -6
    score += 4 if rep.desc_len >= 150 else 0
    score += 4 if rep.trigger_cues >= 2 else -4
    score += min(6, rep.refs * 2)
    score += 4 if rep.scripts and rep.tests else 0
    score += 2 if rep.has_compat else 0
    score -= min(12, rep.generic_hits * 3)
    score -= 8 if rep.lines < 120 else 0
    rep.score = round(max(0.0, min(100.0, score)), 1)

    # --------------------------------------------------------------- issues
    if not rep.has_gotchas:
        rep.issues.append("no gotchas/failure-modes section — check for generic advice")
    if not is_prose and rep.specificity < 15:
        rep.issues.append(f"low specificity ({rep.specificity} markers/100 lines)")
    if rep.desc_len < 150:
        rep.issues.append(f"description is short ({rep.desc_len} chars) — may not fire")
    if rep.trigger_cues < 2:
        rep.issues.append("description lacks explicit trigger cues")
    if rep.generic_hits >= 2:
        rep.issues.append(f"{rep.generic_hits} generic filler phrase(s)")
    if rep.lines < 120:
        rep.issues.append(f"only {rep.lines} lines — may be thin")
    if rep.scripts and not rep.tests:
        rep.issues.append("ships scripts with no tests")
    if rep.tests and not rep.scripts:
        rep.issues.append("has tests but no scripts")

    return rep


def collect() -> list[SkillReport]:
    return [analyse(p) for p in sorted(SKILLS_ROOT.rglob("SKILL.md"))]


def bar(value: float, lo: float, hi: float, width: int = 20) -> str:
    filled = int(round((value - lo) / (hi - lo) * width))
    return "#" * max(0, min(width, filled)).ljust(width)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--worst", type=int, default=0,
                    help="show only the N lowest-scoring skills")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--skill", help="analyse one skill directory or name")
    args = ap.parse_args()

    reports = collect()
    if args.skill:
        target = args.skill.rstrip("/")
        reports = [r for r in reports
                   if r.name == Path(target).name or r.path == target]
        if not reports:
            print(f"no skill matching {args.skill!r}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps([asdict(r) for r in reports], indent=2))
        return 0

    if args.skill:
        for r in reports:
            print(f"\n{r.name}  ({r.path})")
            print(f"  score {r.score}  |  {r.lines} lines  |  {r.words} words")
            print(f"  specificity {r.specificity}/100 lines   "
                  f"code blocks {r.code_blocks}   tables {r.tables}")
            print(f"  gotchas={r.has_gotchas} workflow={r.has_workflow} "
                  f"safety={r.has_safety} checklist={r.has_checklist}")
            print(f"  refs={r.refs} scripts={r.scripts} tests={r.tests} "
                  f"compat={r.has_compat}")
            print(f"  description {r.desc_len} chars, {r.trigger_cues} trigger cues")
            for issue in r.issues:
                print(f"    - {issue}")
        return 0

    scores = [r.score for r in reports]
    mean = sum(scores) / len(scores)
    with_issues = [r for r in reports if r.issues]

    if args.worst:
        show = sorted(reports, key=lambda r: r.score)[: args.worst]
    else:
        show = sorted(reports, key=lambda r: -r.score)

    print(f"{len(reports)} skills analysed")
    print(f"  mean score      {mean:.1f}   (min {min(scores)}, max {max(scores)})")
    print(f"  with gotchas    {sum(r.has_gotchas for r in reports)}/{len(reports)}")
    print(f"  with workflow   {sum(r.has_workflow for r in reports)}/{len(reports)}")
    print(f"  with safety     {sum(r.has_safety for r in reports)}/{len(reports)}")
    print(f"  mean specificity {sum(r.specificity for r in reports) / len(reports):.1f}"
          " markers/100 lines")
    print(f"  with issues     {len(with_issues)}")
    print()

    print(f"{'score':<6} {'name':<42} {'spec':<6} {'got':<4} lines")
    print("-" * 78)
    for r in show:
        print(f"{r.score:<6} {r.name:<42} {r.specificity:<6} "
              f"{'yes' if r.has_gotchas else 'NO':<4} {r.lines}")

    if args.worst:
        print()
        for r in show:
            if r.issues:
                print(f"{r.name}:")
                for i in r.issues:
                    print(f"    - {i}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
