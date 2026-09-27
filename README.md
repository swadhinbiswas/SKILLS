# Agent Skills

A library of **125 hand-written skills** that give AI coding agents the kind of
hard-won, specific knowledge that models don't reliably have: the exact
`EXPLAIN` output that means you have a stats problem, the `ps` state letter
that explains a hung process, the three places a token can leak in a container
build, the reason your GitHub Actions workflow let a fork exfiltrate a secret.

The premise is simple. A general-purpose model knows a great deal about
programming in general and rather less about the specific thing you are doing
right now. Skills fill that gap — and they fill it with the details that
actually decide whether the work is good: exact commands, real error strings,
and the traps that only show up after you've lost an afternoon to them.

**Every skill here was written to be read once by an engineer, and used by an
agent many times.**

---

## Why this exists

Ask a coding model to debug a slow Postgres query and you will usually get a
plausible, confident, generic answer: add an index, check the query plan. That
answer is not wrong, and it is not useful, because it is the same answer it
would give for any slow query.

This collection is built the other way around. Each skill starts from a
specific, painful class of problem and includes the things that are actually
true about it:

- The **exact** command, in the order to run it, with the flags that matter.
- The **real error text** and what it actually means, not a paraphrase.
- The **gotchas** — environment-specific facts that defy reasonable assumption.
  These are the highest-value part of most skills here.
- The **failure modes** a general answer omits: what this approach can't do,
  when the obvious fix makes things worse, where the trap is.
- **Safety** notes where a mistake is expensive, so the agent never silently
  runs something destructive on a guess.

And explicitly not: explanations of what HTTP is, what a database migration
does, or why you should "handle errors appropriately."

---

## Install

Everything installs through one script. It works with any coding CLI or
desktop app that follows the [Agent Skills](https://agentskills.io)
specification.

```sh
git clone https://github.com/swadhinbiswas/SKILLS.git
cd SKILLS
./install.sh
```

That's it. The script detects which agents you have installed, and wires this
repository into each one as a **symlink** — not a copy — so an edit you make
here is live in every agent immediately, and there's nothing to drift.

```
~/SKILLS/skills/*        <- the source of truth
        |
        +-> ~/.claude/skills/<name>          (symlink)
        +-> ~/.codex/skills/<name>           (symlink)
        +-> ~/.config/crush/skills/<name>    (symlink)
        +-> ~/.config/opencode/opencode.jsonc  (skills.paths entry)
```

### Common commands

```sh
./install.sh --list              # what would happen, changes nothing
./install.sh                     # wire up every detected agent
./install.sh --client claude-code
./install.sh --client claude-code --client codex
./install.sh --all               # every known client, detected or not
./install.sh --verify            # check the wiring, change nothing
./install.sh --uninstall         # remove what we added
./install.sh --dry-run           # show every action, do none of them
```

### Clients it knows about

Claude Code · Claude Desktop · OpenAI Codex · opencode · Gemini CLI · Crush ·
Windsurf · Cursor · Amp · Goose · Aider · Zed · generic `~/.agents/skills` ·
per-project `.claude/skills`

**Any other app?** If it reads a directory of skills, point the installer at it:

```sh
./install.sh --client myeditor=$HOME/.config/myeditor/skills
```

If it's configured by a file rather than a directory, the `config` client type
in `install.sh` is the pattern to copy — it's a small, well-tested function.

### Bring skills in from the internet

```sh
./install.sh --from obra/superpowers --skill brainstorming
./install.sh --from owner/repo --domain databases    # import the whole repo
```

Or, to *find* skills rather than import one you already know about:

```sh
python skills/mcp/search-across-internet/scripts/search-across-internet.py "postgres performance" --deep
```

That searches GitHub several different ways, verifies each result really
contains `SKILL.md` files, and ranks them. Set `GITHUB_TOKEN` to avoid the
unauthenticated rate limit.

---

## What's in it

125 skills across 22 domains. The [full index with one-line descriptions for
every skill](skills/INDEX.md) is the right place to browse.

| Domain | Skills | What it covers |
|---|---:|---|
| [AI & LLM](skills/INDEX.md#ai--llm-engineering) | 9 | Prompting, RAG, agents, evals, cost control, model integration |
| [Backend](skills/INDEX.md#backend-languages--runtimes) | 12 | Python, Go, Rust, Java, Node, TypeScript — performance and concurrency |
| [Databases](skills/INDEX.md#databases--data-stores) | 7 | Query tuning, indexing, transactions, replication, safe migrations |
| [Kubernetes](skills/INDEX.md#kubernetes--orchestration) | 6 | Manifests, rollouts, networking, resource tuning, production safety |
| [Cloud](skills/INDEX.md#cloud-platforms) | 6 | AWS, GCP, Azure — IAM, networking, cost, serverless |
| [Containers](skills/INDEX.md#containers--local-dev) | 6 | Dockerfiles, Compose, image size, build caching, runtime security |
| [CI/CD](skills/INDEX.md#cicd--releases) | 6 | Pipeline design, caching, releases, deploy strategies, Actions hardening |
| [IaC](skills/INDEX.md#infrastructure-as-code) | 6 | Terraform modules, state, plans, drift, Ansible, IaC testing |
| [Git](skills/INDEX.md#git--version-control) | 6 | History surgery, bisecting, merges, refs, signing, workflows |
| [Testing](skills/INDEX.md#testing) | 6 | Test design, patterns, integration harnesses, contracts, load, coverage |
| [Debugging](skills/INDEX.md#debugging--diagnostics) | 6 | Root-cause method, incidents, profiling, leaks, flakes, tracing |
| [Data Eng](skills/INDEX.md#data-engineering) | 6 | Pipeline reliability, streaming vs batch, warehouse modelling, observability |
| [Frontend](skills/INDEX.md#frontend--ui) | 6 | React hooks, state, RSC, performance, CSS, accessibility |
| [Mobile](skills/INDEX.md#mobile--desktop) | 6 | React Native, Expo, offline sync, app stores, crash diagnostics |
| [Terminal](skills/INDEX.md#terminal--shell) | 4 | Shell robustness, text processing, environment, process tooling |
| [API Design](skills/INDEX.md#api-design--integration) | 4 | REST, GraphQL, pagination, webhooks |
| [Architecture](skills/INDEX.md#architecture--systems) | 4 | Boundaries, caching, resilience, system design |
| [Observability](skills/INDEX.md#observability--operations) | 4 | Metrics, structured logs, SLOs, OpenTelemetry |
| [Docs](skills/INDEX.md#docs--technical-writing) | 4 | READMEs, ADRs, API docs, technical writing |
| [Codebase](skills/INDEX.md#codebase-craft) | 4 | Code review, refactoring, design review, onboarding |
| [MCP & Tooling](skills/INDEX.md#mcp--agent-tooling) | 4 | MCP servers and clients, skill search, skill authoring |
| [Product](skills/INDEX.md#product--delivery) | 3 | Specs, task breakdown, iteration practice |

---

## How a skill works

You never invoke these directly. The agent loads them by itself, based on the
`description` at the top of each file. It sees every skill's name and
description at startup; the full body loads only when the description matches
what you're doing.

That has one consequence worth understanding: **the description is the
product.** A skill with excellent content and a vague description will never
fire. Every skill here names concrete triggers — `EXPLAIN ANALYZE`, `CrashLoopBackOff`,
`Maximum update depth exceeded` — because that's the vocabulary you'll actually
use.

A skill directory looks like this:

```
skills/databases/postgres-query-tuning/
├── SKILL.md                 # the always-loaded core, under 400 lines
├── references/
│   ├── plan-nodes.md        # read only when you need the deep detail
│   └── indexing.md
├── scripts/
│   └── plan_review.py       # runs, rather than being re-derived by the agent
└── tests/
    └── test_plan_review.py
```

The split matters. `SKILL.md` is the part that costs context on every run, so it
holds only what you need every time. Detail that matters only sometimes lives
in `references/`, and logic the agent would otherwise re-improve imperfectly
each time is a tested script.

### Try one

```sh
psql -c 'EXPLAIN (ANALYZE, BUFFERS) SELECT ...' \
  | python skills/databases/postgres-query-tuning/scripts/plan_review.py
```

```
2 finding(s): 1 high, 1 medium, 0 info

[HIGH]  Index Scan on items
    problem: executed 100,000 times, costing 3,000 ms in total (0.03 ms per loop)
    fix:     This is an N+1 shape. Join or batch the inner lookup instead of
             looping. Multiplying total time by loops is what exposes it.

[MED]   plan
    problem: more blocks read from disk (9,400) than served from cache (120)
    fix:     This plan is I/O bound. Reduce the number of pages touched
             (indexes, batching) -- a faster CPU will not help.
```

The N+1 was invisible in the raw plan: the node's own cost is `0.03ms`, which
looks free. It's executed 100,000 times. Multiplying total time by loop count
is the thing that makes it obvious, and that's the kind of detail a skill
should exist to carry.

---

## Contributing a skill

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow, and
[skills/AUTHORING.md](skills/AUTHORING.md) for the actual rules — the latter is
short and encodes every decision this library makes. The short version:

1. **Start from a real problem**, not a topic. "Dockerfiles" is a topic;
   "my Docker build takes 12 minutes and the image is 1.8GB" is a problem.
2. **Write only what the agent would get wrong.** If you catch yourself
   explaining something well-known, delete it.
3. **Description first.** It's the only thing loaded until the skill fires.
4. **Lead with the gotchas.** That's the section that earns its place.
5. **Give a default, not a menu.** Pick one approach; mention alternatives in a
   clause.
6. **Under 400 lines.** Move depth into `references/`.
7. **Don't invent specifics.** A confidently wrong command is worse than a
   described intent. When in doubt, tell the agent to check the docs.

Then:

```sh
python tools/validate_skills.py          # spec compliance; must pass
python tools/build_index.py              # register it in the catalog + index
```

`validate_skills.py` enforces the spec mechanically — frontmatter shape, name
matching its directory, description quality, the line budget, and that every
file you reference actually exists. It fails the build on errors.

If you add a script, it needs a `tests/` directory and the tests need to pass:

```sh
cd skills/<domain>/<name> && python -m unittest discover -s tests
```

Everything is stdlib-only and every script has a real `--help`. There are no
dependencies to install and nothing to break in six months.

---

## Layout

```
.
├── install.sh                    # the installer (36 tests)
├── Makefile                      # `make check` runs everything
├── skills/
│   ├── INDEX.md                  # generated: every skill, one line each
│   ├── AUTHORING.md              # the rules for writing a skill
│   ├── catalog.json              # generated: machine-readable registry
│   └── <domain>/<name>/          # the 125 skills themselves
├── tools/
│   ├── validate_skills.py        # spec compliance checker
│   └── build_index.py            # regenerates catalog.json + INDEX.md
├── tests/
│   └── test_install.py           # installer test suite (36 tests)
├── .github/workflows/            # CI: runs `make check` equivalent
├── job-application-matcher/      # an earlier standalone skill
├── eu-data-backend-job-hunter/   # an earlier standalone skill + job board
├── CONTRIBUTING.md
├── LICENSE
└── README.md
```

The two standalone skills at the root predate the `skills/<domain>/<name>/`
layout. They're valid Agent Skills and `validate_skills.py` checks them, but
they sit outside `skills/`, so they're **not** in `catalog.json`, `INDEX.md`, or
the 125 count. They work the same way — point the installer at them, or move
them into a domain folder when you next touch them.

## Verifying the whole repo

```sh
make check        # everything: spec, index, installer, all test suites
```

Or individually:

```sh
python tools/validate_skills.py        # spec compliance: 125 skills, 0 failures
python tools/build_index.py --check    # catalog + index are in sync
bash -n install.sh                     # installer parses
python -m unittest discover -s tests   # installer suite (36 tests)

# each skill that bundles a script has its own suite
cd skills/<domain>/<name> && python -m unittest discover -s tests
```

CI runs the same checks on every push, across Python 3.11, 3.12, and 3.13.

## Troubleshooting

**A skill doesn't show up in my agent.** Most clients read skills at startup.
Restart the agent after installing. If it still doesn't appear, run
`./install.sh --verify` — it reports broken symlinks and missing config entries
explicitly.

**`./install.sh` says nothing happened.** It only wires up clients it detects.
Use `--all` to force every known client, or `--list` to see what's detected.

**opencode didn't pick up the change.** `skills.paths` is read once at startup.
Restart it. The installer edits your `opencode.jsonc` in place, preserving
comments and formatting.

**A skill exists but never activates.** The agent only reads `name` and
`description` until it decides a skill applies. If yours has a vague description
it will never fire — see [AUTHORING.md](skills/AUTHORING.md) §2.

**`--force` is refusing to replace something.** It only replaces entries that
are *not* symlinks pointing into this repo. If you have a real directory with
the same name, move it aside first.

## Compatibility

Skills are plain markdown. Any client that implements the
[Agent Skills spec](https://agentskills.io/specification) can read them — there
is no runtime, no plugin, no dependency.

- **Installer**: bash + Python 3 (standard library only). Tested on Linux.
- **Scripts**: Python 3.9+, standard library only, executable bit set.
- **Known good clients**: Claude Code, Codex, opencode, Crush, Zed, and the
  generic `~/.agents/skills` convention.
- **Unverified**: the skill directory paths for Gemini CLI, Windsurf, Cursor,
  Amp, Goose, Aider, and Claude Desktop are taken from each project's
  documentation. They're implemented but unconfirmed on a real install — if
  yours differs, use `--client <name>=<correct/path>` and consider opening an
  issue.

## Licence

MIT — see [LICENSE](LICENSE). Use them, change them, take them apart, put them
back together.

Skills describe techniques and practices from publicly available tooling and
documentation. Flag anything that's wrong or outdated and it should be fixed.
