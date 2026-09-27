---
name: agent-skills-authoring
description: Write Agent Skills that trigger reliably and load efficient context - the SKILL.md frontmatter contract, trigger-rich descriptions, progressive disclosure across references/ scripts/ assets/, the under-500-line budget, choosing prose vs a bundled script, and validating a skill before it ships. Use when creating a new skill, when a skill never activates, when SKILL.md is too long, when a skill needs to be split or restructured, or when someone says "write a skill", "add a skill", "SKILL.md", "frontmatter", "progressive disclosure", "the skill does not trigger", "skills/catalog.json".
compatibility: Follows the Agent Skills spec at https://agentskills.io/specification. Validated by tools/validate_skills.py in this repo; verify the current spec before shipping.
metadata:
  version: "1.0"
---

# Authoring Agent Skills

A skill is a folder an agent reads on demand. It has one job: **load the right
instructions at the right moment, and load them cheaply.** Everything below is
in service of those two goals.

Read `skills/AUTHORING.md` first — it is the house rules this repo enforces, and
this skill is the general version of it. The normative spec is
<https://agentskills.io/specification>; when the two disagree, the spec wins and
this repo's validator is the local gate.

## The contract

```
skills/<domain>/<name>/
├── SKILL.md            # required, < 500 lines
├── references/*.md     # optional deep detail, read on demand
├── scripts/*           # optional repeatable logic, stdlib-first
├── assets/*            # optional templates/fixtures
└── tests/              # optional, for scripts
```

`SKILL.md` starts with YAML frontmatter, then the body:

```markdown
---
name: my-skill                 # required; [a-z0-9-], <=64 chars, == folder name
description: One sentence, trigger-rich. Required, <=1024 chars.
license: MIT                   # optional
compatibility: Needs Python 3.11+ and psql.   # optional, <=500 chars
metadata:                      # optional, string -> string only
  version: "1.0"
allowed-tools: read bash       # optional, space-separated
---

# Title
Body. No "This skill..." — the client already knows.
```

Mechanics enforced by `python tools/validate_skills.py`: frontmatter parses,
`name` matches the directory exactly, `description` present and under the limit,
`SKILL.md` under the line budget, and **every relative path the body references
must exist**. Run it before you claim a skill is done.

## The description is the product

At startup the client loads only `name` and `description` for every installed
skill. If the description does not match the user's words, the skill never
loads — a perfect skill that never activates is worth nothing.

The description has one job: **be retrieved by a realistic user request.** Shape:

> [What it does, concretely] — [the artefacts and verbs users type]. Use when
> [the triggering situations]. Triggers on "[literal words]".

- Name the artefacts a user would actually type: `EXPLAIN ANALYZE`,
  `docker-compose.yml`, `useEffect`, `git bisect`, `SKILL.md`, `401`.
- Include the verbs users use: *slow, flaky, crashing, 500, memory leak, lock,
  review, refactor, deploy, stuck, why is X wrong*.
- Say *when to use it*, in the user's terms — a description that only says what
  the skill does is a warning in this repo's validator, because it does not
  trigger reliably.
- **Don't write "This skill…"**; the client already knows.
- One skill per coherent job. If a user would need two skills to do one task,
  they are one skill.

Worked example of the difference:

> **Bad** — `Helps with databases.`
>
> **Good** — `Replaces a large Postgres slow query with an index, a rewritten
> plan, or a batching strategy, working from EXPLAIN output rather than guesses.
> Use when a query is slow, when a migration is about to lock a table, or when
> someone pastes EXPLAIN (ANALYZE) output and asks why it is slow. Triggers on
> "seq scan", "index not used", "N+1".`

More description craft (trigger-word mining, anti-triggering, a 6-line
description you can draft from a user request) is in
`references/description-and-triggering.md` — read it when you are writing a new
skill's description or when a skill is not activating.

## Progressive disclosure: split by *when*

The whole body loads on activation, so every line costs attention on every run.

| Content | Budget | Goes in |
|---|---|---|
| Metadata (name + description) | ~100 tokens | frontmatter |
| Core instructions | <5,000 tokens, <500 lines | `SKILL.md` |
| Deep detail | unlimited | `references/*.md` |
| Repeatable logic | — | `scripts/*` |
| Static resources | — | `assets/*` |

**The split rule: if the agent only needs it in one specific situation, it
belongs in `references/`.** And always say *when* to read it:

```markdown
Read `references/description-and-triggering.md` when you are writing this
skill's description, or when the skill is not activating.
```

A bare "see references/" gets the file loaded every time, which defeats the
point. Keep references **one level deep** — no reference pointing at another
reference.

**Line budget.** The validator errors above 500 lines and warns above 400.
Target **150-280** lines for a normal skill. A skill that needs more usually
needs a split, not a longer file. If you are over, ask for each section: is
this needed *always*, or *only in situation X*?

## Write only what the model gets wrong

Before writing a line, ask: *would the agent get this wrong without it?* If no,
cut it. The model knows what HTTP is, that a migration exists, and that
`str.split` exists. Do not explain them.

**Do write:**

- Exact commands in the order to run, with the flags that matter.
- Version- and product-specific behaviour.
- Non-obvious traps of *this* ecosystem.
- Concrete error strings → what they mean → what to do.
- The house default when several approaches are valid.
- "Don't invent specifics" — tell the agent to verify uncertain APIs against
  current docs rather than guessing.

**Do not write:** background, "why LLMs matter", motivational framing, or
anything a competent engineer already knows.

## Calibrate prescriptiveness per section

- **Prescriptive** where the sequence is fragile and must be identical every
  time: destructive operations, migrations, release steps, exact invocations.
  Verbatim commands, no improvisation.
- **Explanatory** where several approaches are genuinely valid: review lenses,
  refactoring strategy, naming, trade-offs. State the goal; let the agent
  choose.
- Most skills need both. A skill that is uniformly prescriptive reads as a
  script and is annoying; uniformly explanatory produces improvisation.

## Defaults, not menus

Pick one way to do the common thing and name it the default. Mention
alternatives in a clause. Never "you can use X, Y, or Z" — that list makes the
agent guess, and guessing is what wastes tokens.

> Use `httpx` for new async HTTP code. For an existing sync codebase,
> `requests` in a thread pool is fine — don't convert working sync code.

## Structure that works

- **Gotchas** — the highest-value section in most skills: facts that defy
  reasonable assumption. When a user corrects the agent, the correction belongs
  here.
- **Output templates** — agents pattern-match concrete shapes far better than
  prose. If the skill must emit something, show it.
- **Checklists** — for multi-step workflows with dependencies or validation
  gates.
- **Validation loops** — do the thing, run the checker, fix, repeat until green.
- **Plan → validate → execute** — for anything batch or destructive: produce an
  intermediate plan, check it against a source of truth, then execute.
- **Diagnosis tables** — symptom → cause → fix beats a paragraph of advice.

## Prose or a bundled script?

Reach for a script when the agent is **reinventing the same logic every run** —
parsing a format, building a chart, validating output, computing something
fiddly. One tested script beats a paragraph of prose the agent re-implements
and gets subtly wrong.

Rules for `scripts/*`:

- **Stdlib only** by default, so it runs anywhere. Document any dependency in
  the script's `--help` and in `SKILL.md`.
- **Self-contained**, with a `main()` and `if __name__ == "__main__"`.
- **`--help` that actually explains usage**, plus actionable errors that say
  what to do next.
- **Exit non-zero on failure** so the agent can tell success from failure.
- **Tested** in `tests/test_<name>.py` with stdlib `unittest`, runnable with
  `python -m unittest discover -s tests`.
- Executable bit set, `#!/usr/bin/env python3` shebang.
- Prefer pure functions with clear inputs and outputs so tests are trivial.

Keep it prose when the logic is a judgement the agent must make, is used once,
or is short enough to do correctly inline.

## Authoring workflow

1. **Pick the name and domain.** Verb-or-noun kebab-case, `skills/<domain>/<name>/`, unique across the repo. The name must equal the folder.
2. **Draft the description first**, from a realistic user request. If you cannot write a description that would make *you* reach for it, the skill's scope is not clear yet.
3. **Sketch the sections** — workflow, tables, gotchas, checklist. Count the lines; over budget means split.
4. **Write the body.** Cut every line the model would get right anyway.
5. **Move one-specific-situation detail into `references/`**, with a "read this when" line.
6. **Register it in `skills/catalog.json`** (name, domain, path, one-line description, and `extras` listing any `references`/`scripts`/`tests` dirs). Check with the validator.
7. **Validate**: `python3 tools/validate_skills.py skills/<domain>/<name>` — no errors; only "not registered in catalog" (or a line-count hint) should remain.
8. **Trigger test**: read the description alone and ask "would I reach for this for a real request?" If not, the description is the bug.

## Gotchas

- **A skill that never triggers is invisible, not bad.** Fix the description
  before you touch the body.
- **Frontmatter keys outside the spec are ignored or flagged** — stick to
  `name`, `description`, `license`, `compatibility`, `metadata`,
  `allowed-tools`. `metadata` values must be strings.
- **`name` must equal the directory name** exactly. A mismatch is a hard error
  in the validator and in most clients.
- **Referencing a file that does not exist is a hard error.** Every
  `references/…`, `scripts/…`, `assets/…` path you mention must exist. Create
  the file or drop the mention.
- **Cross-skill references should name the owning skill** — write
  `skills/ai/llm-evaluation/SKILL.md`, not a bare relative path, so a reader (and
  the validator) can tell where it lives.
- **Markdown links are checked too**, and so is any `references/`, `scripts/`, or
  `assets/` path in inline code — the validator resolves them relative to the
  skill. Every one must exist. A bare URL is left alone.
- **Over-long descriptions get truncated by some clients** and lose the trigger
  cue. Put the "Use when…" part where it survives truncation — front, not buried.
- **Do not duplicate content across skills.** Two skills that both explain
  caching will drift; have one and link to it.
- **Prompts inside skills are just text** — if the skill needs a *deterministic*
  transformation, that is a script, not a paragraph.
- **The spec evolves.** Check <https://agentskills.io/specification> before
  relying on a field or behaviour; the local validator is a floor, not the
  ceiling.
- **"Verified" means verified.** Every command in a skill should have been run
  or be explicitly marked unverified.

## Definition of done

- [ ] `python tools/validate_skills.py skills/<domain>/<name>` passes with no errors
- [ ] Description would make you reach for the skill unprompted on a real request
- [ ] `SKILL.md` under the line budget (target 150-280, hard cap 500)
- [ ] Every referenced `references/`/`scripts/`/`assets/` file exists
- [ ] Reference files are one level deep and each has a "read this when" line
- [ ] Scripts are stdlib-first, tested, `--help`-documented, non-zero on failure
- [ ] Registered in `skills/catalog.json`
- [ ] Reading it once, you could do the task yourself
