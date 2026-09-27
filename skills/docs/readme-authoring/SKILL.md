---
name: readme-authoring
description: Write a README that gets a stranger to a working result in five minutes - what/why in two sentences, a runnable quickstart, install, usage, configuration, contributing, and license, with bad-vs-good diffs, sparing badges, and a process for keeping it current. Use when writing or fixing a README, when someone says "the docs are out of date" or "I could not get this running", or when reviewing docs in a PR.
metadata:
  version: "1.0"
---

# README Authoring

A README has one job: **get a stranger to a working result, then let them go
deeper.** Every section below earns its place by serving that. A README that
documents the architecture but does not run is a museum plaque.

Opinionated, because there is a right answer to most of these questions.

## The "works in 5 minutes" test

Before publishing, do this from a clean machine, in a clean directory, with no
prior knowledge:

1. Copy the quickstart blocks **verbatim** into a shell, top to bottom, in
   order, with no edits. It must succeed.
2. No steps that require a secret you have but a stranger does not (a personal
   token, a hardcoded URL, a local `/Users/you/...` path).
3. The output of the last step is a *visible, meaningful result* — a printed
   value, a running server, a file. "Install succeeds" is not a result.
4. It works on the platform the project claims (say which, in the README).
5. The quickstart must not require a global tool the reader may not have.
   Prefer a package-manager or `npx`/`uvx`-style one-shot over "install Go
   1.21 globally".

If any step fails, the README is broken regardless of how well-written it is.

## Section order that works

Ship this order. It is the order a reader's questions arrive in.

1. **Name + one-line what** — what it is, in under ~15 words.
2. **Why** — the problem it solves, or what it replaces. 2–3 sentences. This is
   the section that decides whether the reader keeps reading.
3. **Quickstart** — the shortest path to a working result, copy-pasteable,
   with expected output shown.
4. **Install** — the real options (package manager, container, from source),
   with a pinned or minimum version.
5. **Usage** — the 2–3 things people actually do, with real output.
6. **Configuration** — every option that a user must set, as a table, with the
   default.
7. **Contributing** — how to run tests, the commit convention, how to get a
   change merged.
8. **License** — one line, plus a link.
9. **Links**: docs site, changelog, support, security policy, ADR index.

Order matters more than completeness. "Why" before "Install" is unusual and
correct: an experienced reader decides to keep going in the first 20 seconds.

## What, before why — the most common failure

**Bad:**

> Welcome to Widgets! Widgets is a powerful, flexible, and easy-to-use solution
> that provides developers with a robust set of tools for building modern
> applications in a scalable and maintainable way. In this document we will
> explore the many features and capabilities that Widgets offers.

Every word is content-free. "Powerful, flexible, robust" are claims, not facts;
a reader cannot check them and they teach nothing. The second sentence is a
table of contents nobody asked for.

**Good:**

> **Widgets** — a 4 KB, dependency-free JavaScript date picker.
>
> Use it when you need a date input and do not want a 200 KB dependency or a
> build step. Drop in one `<script>` tag; there is nothing to install.
> Supports keyboard navigation and screen readers, and ships with TypeScript
> types.
>
> ```html
> <input type="text" id="pick" data-widgets>
> <script src="https://unpkg.com/widgets@2.1.0/dist/widgets.min.js"></script>
> ```
> ```js
> Widgets.attach('#pick', { format: 'YYYY-MM-DD' });
> // the input now opens a calendar on focus
> ```

The "good" version: specific size, names the alternative it beats, shows the
whole usage in ten lines, and the comment tells you what to look for.

## Quickstart: show output

A quickstart without visible output leaves the reader unsure whether it worked.
Show what success looks like:

```
$ npx widgets-cli init my-app
Created my-app/ with 3 files. Next: cd my-app && npm start
$ cd my-app && npm start
widgets dev server on http://localhost:5173
```

## Bad vs good, section by section

**Install — bad:** "Installation is easy, just clone the repo and run the
setup." (Which setup? Which prerequisites?)

**Install — good:** a table of prerequisites with versions, then one command
per supported method:

```bash
# from source (requires Python 3.11+)
git clone https://github.com/acme/widgets && cd widgets
make install          # or: pip install -e '.[dev]'
make test
```

**Usage — bad:** a wall of API surface with no task. "The following methods are
available: ..." A reader does not know which one to call.

**Usage — good:** three named tasks, each four lines: *Filter a stream*,
*Retry with backoff*, *Handle errors*. Show the real output, including error
output.

**Configuration — bad:** "Configuration is done via environment variables."
**Configuration — good:** a table. Every row: name, type, default, required?,
what it does, and the effect of the default.

| Variable | Type | Default | Required | Notes |
|---|---|---|---|---|
| `WIDGETS_PORT` | int | `8080` | no | Fails to bind if in use; exits with `address already in use` |
| `WIDGETS_DB_URL` | string | — | **yes** | Postgres URL. No default; startup fails fast if unset. |
| `WIDGETS_LOG_LEVEL` | `debug\|info\|warn\|error` | `info` | no | Anything else is rejected at startup: `invalid log level "verbose"`. |

**Contributing — bad:** "PRs welcome!" (Nobody knows how.)
**Contributing — good:** the exact commands, in order:

```bash
make test          # must pass; lint is part of it
make lint-fix
# Conventional Commits: feat:, fix:, docs:, refactor:, test:, chore:
# Open a PR; two approvals; CI must be green.
```

**License — bad:** omitting it, or "MIT" with no file.
**License — good:** `MIT. See the LICENSE file at the repo root.` plus a
one-line note if there are third-party licence obligations.

## Badges, sparingly

A wall of eight badges is decoration that hides the one that matters
(license, or CI status). Rules:

- **Maximum three**, and only for: build/CI status, licence, and one link to
  the docs or package registry.
- The alt text and the target must both be true. A badge that says `build:
  passing` because it hit a cached green from a fork is worse than no badge.
- Skip coverage percentage (gaming target, not a user signal), skip download
  counts (vanity), skip sponsor badges in the README (they belong lower the
  page or in the org profile).
- Badge images come from an external host; a corporate firewall or a China-based
  user may see broken images. The text link must stand alone.
- **Never a badge for something the project does not do** (e.g. "OpenSSF
  Badge" that has never run).

```markdown
[![build](https://github.com/acme/widgets/actions/workflows/ci.yml/badge.svg)](https://github.com/acme/widgets/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![docs](https://img.shields.io/badge/docs-typed-blue.svg)](docs/)
```

## Keeping it current

Stale docs are worse than no docs: they send people down a dead end and cost
trust. Mechanisms, in order of how well they work:

1. **The quickstart is a test.** Extract the quickstart into a script the CI
   runs (e.g. `make readme-check` that runs the commands in a container). The
   README cannot rot if the commands still run.
2. **Config table is generated** from the code or a single schema file, and CI
   fails on a diff. Hand-maintained tables of env vars always rot.
3. **CI greps for the obvious drift:** links (with a link checker), version
   numbers that do not match the manifest, command names that no longer exist
   in the CLI.
4. **Every PR that changes a public interface touches the README** — make it a
   review checklist item, or a CI check that fails when
   `src/public/**` changed and `README.md` did not.
5. **A "last verified" date** at the top of long READMEs, plus a link to file
   an issue when it's wrong. A visible staleness marker beats a confident lie.

## Editing for a busy expert

- **Inverted pyramid**: the answer first, the background later (and often not
  at all).
- **Concrete over general**: `pip install widgets` beats "install the package
  using your preferred package manager".
- **Show, don't describe**: a command and its output beats a paragraph about
  what the command does.
- **One task per code block.** If a block does three things, split it.
- **Headings a reader can scan**: "How do I…" style questions beat "Advanced
  Usage" and "Miscellaneous".
- **No hedging in instructions**: "You may want to set X" is an instruction
  that will be ignored. Either set it or say why not.
- **Say the platform and version** once, near the top: "Tested on macOS 14 and
  Ubuntu 22.04 with Python 3.11+".
- **Length**: a README that needs scrolling to find install instructions has
  them in the wrong place. Move depth into `docs/`.

## Structure of a real README

```markdown
# Widgets

<!-- one-line what, then 2-3 sentences of why. links/docs once, no more. -->

[badges: build, license, docs]

## Quickstart
## Install
## Usage
   ### Filter a stream
   ### Retry with backoff
## Configuration
## Contributing
## License
## Links (docs, changelog, security, ADRs)
```

## Gotchas

- **A README that was right once goes wrong quietly.** Nothing errors when a
  command in the quickstart stops working; the file just becomes a lie. The
  usual cause is that nobody runs it after a release. If the quickstart is not
  covered by CI, it will drift.
- **`pip install widgets` fails on the reader's machine** because the PyPI name
  differs from the repo name, or the package was never published. Say which
  one you mean, and prefer the `git clone && pip install -e .` form for an
  unreleased project.
- **Copy-pasted commands lose their environment.** A block that assumes
  `cd myproject` or an activated virtualenv fails on paste. Either show the
  `cd` or state the assumption above the block.
- **Badges rot.** A green build badge pointing at a deleted repo is worse than
  no badge. Fewer badges, all live.
- **Configuration documented from the source, not from usage.** Tables drift
  from the code and nobody notices. Generate it, or add a test that fails when
  the table and the code disagree.
- **"Contributing" that says "please contribute"** is the most common dead
  section. If it exists it needs runnable commands: how to set up, run tests,
  and submit a change.
- **A long README is not a bad README; a badly ordered one is.** Nobody is
  scrolled out — they just never reach the install step. Depth belongs in
  `docs/`, and the README links to it.
- **Writing for the wrong reader.** Contributors, users, and operators need
  different things. If the README tries to be all three, it serves none. Pick
  the primary audience and link the rest.
- **"Coming soon" and `TODO` in a README** signal the project isn't finished.
  Delete the section until it is true; a promise in a README is a bug report
  waiting to happen.
- **Screenshots of code go stale** silently and teach the old API. Prefer text.
  If an image is genuinely needed (a UI), it needs the same review as code.

## Review checklist

- [ ] Passes the 5-minute test from a clean checkout, with the blocks copied
      verbatim.
- [ ] "What" and "why" fit in three sentences with no content-free adjectives.
- [ ] Every command has been run; every block shows its output.
- [ ] Prerequisites with versions are stated once, up front.
- [ ] Config table complete and matching the code; no "and other options".
- [ ] Badges: three or fewer, all true, with working targets.
- [ ] Contributing gives runnable commands.
- [ ] Licence stated, file present.
- [ ] No `TODO`, no "coming soon", no dead links, no placeholder text.
- [ ] Under ~400 lines; depth moved to `docs/`.
