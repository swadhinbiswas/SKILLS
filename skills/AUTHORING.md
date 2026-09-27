# Authoring rules for this repository

These are the house rules for every skill under `skills/`. They exist because a
100+ skill repo degrades fast without them: skills drift in tone, descriptions
stop triggering, and the whole catalog becomes noise.

Read this before writing a skill. `tools/validate_skills.py` enforces the
mechanical parts; the rest is judgement.

## 1. Conformance with the spec

Machine-checked by `python tools/validate_skills.py`:

- Every skill is `skills/<domain>/<name>/` with a `SKILL.md` at its root.
- Frontmatter is YAML between two `---` fences, first thing in the file.
- `name` is required, `[a-z0-9-]` only, ≤64 chars, no leading/trailing/double
  hyphen, and **must equal the directory name**.
- `description` is required, ≤1024 chars.
- Optional: `license`, `compatibility` (≤500 chars), `metadata` (string→string),
  `allowed-tools` (space-separated string).

Full spec: <https://agentskills.io/specification>.

## 2. The description is the product

At startup every client loads **only** `name` and `description` for every
installed skill. If the description doesn't match the user's words, the skill
never loads — a perfect skill that never activates is worth nothing.

Write descriptions as one sentence, trigger-first where it helps:

> **Good** — `Replaces a large Postgres slow query with an index, a rewritten
> plan, or a batching strategy, working from EXPLAIN output rather than guesses.
> Use when a query is slow, when a migration is about to lock a table, or when
> someone pastes `EXPLAIN ANALYZE` output and asks why it is slow.`

> **Bad** — `Helps with databases.`

Rules:

- Name the concrete artefacts a user would actually type: `EXPLAIN ANALYZE`,
  `docker-compose.yml`, `useEffect`, `git bisect`.
- Include the verbs users use: *slow, flaky, crashing, 500, memory leak, lock,
  migration, review, refactor, deploy, stuck*.
- One skill per coherent job. If a user would need two skills to do one task,
  merge them.
- Don't write "This skill..." — the client already knows it's a skill.

## 3. Progressive disclosure

The whole body loads on activation, so every line costs attention on every run.

| Content | Budget | Goes in |
|---|---|---|
| Metadata (name+description) | ~100 tokens | frontmatter |
| Core instructions | <5,000 tokens, <500 lines | `SKILL.md` |
| Deep detail | unlimited | `references/*.md` |
| Repeatable logic | — | `scripts/*` |
| Static resources | — | `assets/*` |

Rule of thumb for splitting: **if the agent only needs it in one specific
situation, it belongs in `references/`.** And always say *when* to read it:

```markdown
Read `references/anti-patterns.md` when the plan already uses indexes and is
still slow.
```

A bare "see references/" gets the file loaded every time, which defeats the
point. Keep references one level deep — no chains of reference-to-reference.

## 4. Spend context only on what the model gets wrong

Before writing a line, ask: *would the agent get this wrong without this line?*
If no, cut it. The model knows what HTTP is, what a migration is, and that
`str.split` exists. Do not explain them.

Do write:

- Exact commands, in the order to run them, with the flags that matter.
- Version-specific and product-specific behaviour.
- The project's or ecosystem's non-obvious traps.
- Concrete error strings → what they actually mean → what to do.
- The house default when several approaches are valid.

## 5. Calibrate prescriptiveness

Not every instruction should be a rule.

**Be prescriptive** when the sequence is fragile or must be identical every
time — destructive operations, data migrations, release steps, exact
invocations. Verbatim commands, no improvisation.

**Be explanatory** when several approaches are genuinely valid — code review
lenses, refactoring strategy, naming. State the goal and let the agent choose.

Most skills need both. Calibrate per section.

## 6. Defaults, not menus

Pick one way to do the common thing and name it as the default. Mention
alternatives in one clause.

> Use `httpx` for new async HTTP code. For an existing sync codebase, `requests`
> in a thread pool is fine — don't convert working sync code for this.

Never: "You can use X, Y, or Z, or D, or E..." That list gets the agent to
guess, and guessing is what wastes tokens.

## 7. Structure that works

The patterns below reliably improve activation quality. Use what fits.

**Gotchas** — the highest-value section in most skills. Facts that defy
reasonable assumption. Not "handle errors well" but the specific thing that is
true here. When a user corrects the agent, the correction goes here.

**Output templates** — agents pattern-match concrete structures far better
than prose descriptions. If the skill must emit a specific shape, show it.

**Checklists** — for multi-step workflows with dependencies, especially with
validation gates. Numbered steps the agent can track.

**Validation loops** — make the agent check its own work before proceeding:
do the thing, run the checker, fix, repeat until it passes.

**Plan-validate-execute** — for anything batch or destructive: produce an
intermediate plan, validate it against a source of truth, then execute.

## 8. Bundled scripts

Reach for a script when the agent is *reinventing the same logic* every run —
parsing a format, building a chart, validating output, computing something
fiddly. One tested script beats a paragraph of prose the agent re-implements
and gets subtly wrong.

Requirements for anything in `scripts/`:

- **Stdlib only** by default, so it runs anywhere. Document any dependency in
  the script's `--help` and in `SKILL.md`.
- **Self-contained**, with a `main()` and `if __name__ == "__main__"`.
- **`--help` that actually explains usage**, plus actionable error messages
  that tell the agent what to do next.
- **Exit non-zero on failure** so the agent can tell success from failure.
- **Tested** in `tests/test_<name>.py` using stdlib `unittest`, runnable with
  `python -m unittest discover -s tests`.
- Executable bit set, and a `#!/usr/bin/env python3` shebang.

Prefer pure functions with clear inputs and outputs so tests are trivial.

## 9. Tone

Write for a competent engineer who is *new to this specific system*, not for a
beginner and not for a peer who already knows the tool.

- Direct and concrete. Second person, active voice.
- No filler: "In today's fast-paced world", "It is important to note that".
- No hedging: say what is true. If something is genuinely contested, say so and
  name the trade-off.
- Never invent specifics. If a flag, endpoint, or version is not certain, tell
  the agent to verify it rather than guessing.
- No emoji in skill bodies. A few are fine in output templates the skill
  generates, but not in the instructions themselves.
- Honest about limits. If a tool can't do something, the skill says so instead
  of pretending.

## 10. House preferences

Unless a skill's domain says otherwise, these are the defaults in this repo:

- Shell examples target **bash** with `set -euo pipefail` discipline; quote
  expansions.
- Python targets **3.11+**, stdlib-first, type hints on public functions.
- Commits are **Conventional Commits** (`feat:`, `fix:`, `docs:`, `chore:`,
  `refactor:`, `test:`, `perf:`, `build:`, `ci:`).
- Prefer **additive, reversible changes**. Never a destructive step without an
  explicit gate and a stated rollback.
- **Never** send emails, post to third parties, force-push shared branches, or
  run destructive commands on production as a default action. Draft and
  surface; let the human execute.
- Cite evidence. "The docs say X" beats "X is best practice".

## 11. Definition of done

A skill is finished when:

1. `python tools/validate_skills.py` passes with no errors for it.
2. The description would make *you* reach for this skill, unprompted, given a
   realistic user request.
3. Every command in it has been run, or is explicitly marked unverified.
4. Every referenced file exists.
5. Scripts are tested and exit correctly on both success and failure.
6. It's registered in `skills/catalog.json`.
7. Reading it once, you could do the task yourself.
