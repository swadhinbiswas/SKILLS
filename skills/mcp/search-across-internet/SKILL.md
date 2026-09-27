---
name: search-across-internet
description: Find Agent Skills anywhere on the internet - GitHub repositories, the skills.sh registry, awesome-lists, and personal skill collections - then rank them against a specific need and install the best one. Use when the user asks to find a skill, look for a skill, search for skills, "is there a skill that does X", "what can you do with Y", or wants to extend an agent with a capability that is not already installed. Also use before saying a task is impossible for an agent. Triggers on "find a skill", "search for skills", "skill for", "any skill that", "extend the agent", "is there a skill".
compatibility: Network access required for internet search (GitHub API). Local search works offline. Optional GITHUB_TOKEN raises the rate limit.
metadata:
  version: "1.0"
---

# Search for Skills Across the Internet

Use this before concluding that an agent cannot do something. There are 100+
public skill collections and a fast-growing ecosystem; the installed set is only
a fraction of what exists.

## The tool

```sh
python scripts/search_across_internet.py "<query>"
```

It queries several strategies against the GitHub API, deduplicates by repo, and
ranks by relevance. Every strategy catches something the others miss, which is
why a plain repo-name search is not enough:

| Strategy | Finds | Misses |
|---|---|---|
| `topic:claude-skills`, `topic:agent-skills` | repos the community tagged | anything untagged |
| Free-text (`kubernetes debugging`) | repos by description | a repo whose description omits the topic |
| `SKILL.md` keyword | repos named for the file format | everything else |
| Local catalog | skills already installed here | everything else |

**Always pass `--deep` when you intend to install the result.** Without it, the
tool infers "has skills" from the repo description. With it, the tool fetches
each repo's file tree and counts actual `SKILL.md` files. That is the difference
between "a repo about skills" and "a repo you can install a skill from".

```sh
# Explore (fast, may include repos without real skills)
python scripts/search_across_internet.py "postgres performance"

# Verify before installing (slower, accurate)
python scripts/search_across_internet.py "postgres performance" --deep

# Machine-readable
python scripts/search_across_internet.py "mcp server" --json --limit 20
```

## Workflow

- [ ] 1. Search the internet **and** check what is already installed
- [ ] 2. Re-run with `--deep` on the promising candidates
- [ ] 3. Read the candidate's `SKILL.md` before recommending it
- [ ] 4. Judge it against the checklist below
- [ ] 5. Install, or say plainly that nothing good exists

### Step 1 — search both ways, always

```sh
python scripts/search_across_internet.py "<query>" --include-local
```

`--include-local` merges hits from this repository's installed skills with the
internet results. The right answer is often "you already have it" — check
before sending someone to install something they have.

Offline, or when rate-limited, use only the local index:

```sh
python scripts/search_across_internet.py --local "<query>"
```

### Step 2 — verify the promising candidates

```sh
python scripts/search_across_internet.py "<query>" --deep --limit 5
```

Look for `contains SKILL.md` and a `skill_count` above 1 in the `why` line. A
repo with one `SKILL.md` at the root is usually a single skill; a repo with
dozens is a collection worth installing wholesale.

### Step 3 — read it before recommending it

Never recommend a skill you have not read. Fetch the `SKILL.md` from the
candidate's `raw.githubusercontent.com` and read it. You are checking that it
actually does what the user needs.

```sh
curl -sL "https://raw.githubusercontent.com/<owner>/<repo>/HEAD/<path>/SKILL.md" | head -60
```

### Step 4 — judge the skill, not the stars

A high star count measures popularity, which is not quality. Read it against:

- **Does the description say when to use it?** Without a trigger phrase the
  skill will never load, no matter how good the body is.
- **Is the body specific or generic?** "Handle errors appropriately" is filler.
  Exact commands and real error strings are worth something.
- **Is it under 500 lines?** A longer one should be splitting its detail into
  `references/`.
- **Does it have `scripts/` and do they work?** Bundled logic is usually better
  described than re-derived.
- **When was it last updated?** A skill referencing a deprecated API is worse
  than no skill.
- **Does it tell the agent what NOT to do?** A skill that only adds options
  makes the agent guess.

### Step 5 — install, or say nothing good exists

If a candidate is good, install it. The bundled installer takes a GitHub repo
and a path within it:

```sh
./install.sh --from <owner>/<repo> --skill <path-in-repo>   # into this repo
npx skills add <owner>/<repo>                              # into a local agent
```

**If nothing good exists, say so plainly.** Do not recommend the
highest-starred mediocre match. "There is nothing good for X; the closest is Y,
which is weak because Z" is a genuinely useful answer.

## Other places worth searching

GitHub is where almost everything lives, but not all of it:

- **skills.sh** — the ecosystem registry, with a `npx skills find <query>`
  command. Good for discovering well-packaged collections.
- **Awesome-lists** — `awesome-claude-skills`, `awesome-mcp-servers`, and
  similar curated lists are a good index of the ecosystem when you do not know
  what to search for.
- **Vendor collections** — `anthropics/skills`, `microsoft/skills` and similar
  first-party repos. Usually the most carefully written, and the most
  general-purpose.
- **The `awesome-*` search** — if your query returns nothing, search for
  `awesome <topic>` instead. Finding the list is usually faster than guessing
  the right keyword.

## Gotchas

- **Unauthenticated GitHub search is limited to about 10 requests/minute.** Set
  `GITHUB_TOKEN` (or `GH_TOKEN`) to raise it. Without a token, repeated searches
  will start returning 403s that look like "no results found" — they are
  rate-limit responses, not empty results. The tool surfaces this as a note
  rather than silently returning nothing, but read the notes.
- **A repo "about" skills is not a repo "of" skills.** Documentation sites,
  tutorials, and awesome-lists all rank well on keywords and contain nothing
  installable. This is what `--deep` is for.
- **Star counts are gamed and topic tags are noisy.** Score them as tie-breakers
  only, which is what the tool does.
- **`SKILL.md` in the repo root is often just an example**, not the real
  collection. Look for a `skills/` or `.claude/skills/` directory.
- **Never install a skill you have not read.** A skill is instructions that run
  with your permissions. Treat an unreviewed one as untrusted input.
- **Local search needs the catalog to be current.** If `tools/build_index.py`
  has not been run after adding skills, `--local` will miss them.
- **Search is not the same as verification.** Finding a skill that claims to do
  X does not mean it does X well. Read it, and prefer a boring well-written
  skill over a flashy incomplete one.

## When not to use this

- The task is something you can already do well. Do not add a dependency to fix
  a problem you do not have.
- The user asked for a specific tool, not a capability. Install the tool.
- Network search keeps failing. Say so, and offer the local index instead of
  burning retries.

## Files

- `scripts/search_across_internet.py` — the search tool. Stdlib only.
- `tests/test_search_across_internet.py` — 30 tests covering scoring, local
  search, rate-limit handling, dedupe, and `--deep`. Run
  `python -m unittest discover -s tests`.
