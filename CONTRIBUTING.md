# Contributing

Thanks for adding to this. The bar is quality over volume: 125 skills that are
all worth loading beats 200 where half are filler.

## Before you write anything

1. **Check whether a skill already covers it.** Two skills that both explain
   "how to write tests" is worse than one, because the agent has to choose.
   ```sh
   python tools/build_index.py --stats
   grep -ri "<topic>" skills/*/*/SKILL.md -l
   ```
2. **Start from a real problem, not a topic.** "Dockerfiles" is a topic. "My
   build takes 12 minutes and the image is 1.8GB" is a problem. A skill that
   solves a problem you have actually had will be far better than one that
   surveys a field.
3. **Read [skills/AUTHORING.md](skills/AUTHORING.md).** It is short and it
   encodes every decision this library makes. It is the actual spec; this file
   is just the workflow.

## The shape of a good skill

- **Description carries the weight.** Clients load only `name` and `description`
  until a skill fires. A vague description means a perfect skill that never
  activates. Name concrete triggers people actually type.
- **Skip what the model knows.** If you're explaining what a transaction is,
  delete it. Spend the space on what it would get wrong.
- **Lead with gotchas.** Facts that defy reasonable assumption. Error string →
  what it means → what to do. This is the section that earns the skill.
- **Pick a default.** One recommended approach, alternatives in a clause. Menus
  of options make the agent guess, and guessing wastes tokens.
- **Don't invent specifics.** A confidently wrong flag is worse than a described
  intent. When unsure, say "verify with `--help`".
- **Stay under 400 lines.** Depth belongs in `references/`.

## Adding it

```
skills/<domain>/<name>/
├── SKILL.md          # required
├── references/       # optional: depth, loaded only on demand
├── scripts/          # optional: tested, stdlib-only
├── tests/            # required if scripts/ exists
└── assets/           # optional: templates
```

Pick an existing domain, or add a new one to `skills/catalog.json` under
`domains` (and to `DOMAIN_ORDER` in `tools/build_index.py` so it sorts).

## Verify before you push

```sh
# 1. spec compliance — must exit 0
python tools/validate_skills.py

# 2. regenerate the catalog and index
python tools/build_index.py

# 3. if you added a script, its tests must pass
cd skills/<domain>/<name> && python -m unittest discover -s tests
```

CI runs all three on every push, plus the installer suite. If it fails there,
it will fail for you too.

A warning about "not registered in catalog.json" means you skipped step 2. A
warning about a referenced path that doesn't exist is a real bug — you pointed
at a file you didn't create.

## Scripts

- Python 3.9+, **standard library only**. No dependencies, ever.
- Shebang, `main()`, `if __name__ == "__main__"`, argparse with a real `--help`.
- Exit non-zero on failure so the agent can tell success from failure.
- Executable bit set.
- Stdlib `unittest` tests, runnable with `python -m unittest discover -s tests`.

A script earns its place when the agent would otherwise re-derive the same
fiddly logic every run and get it subtly wrong. If prose is clearer, write
prose.

## House conventions

- Shell: bash, `set -euo pipefail`, quote every expansion.
- Python: type hints on public functions, docstrings that say why not what.
- Commits: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Additive and reversible. Never a destructive step without a gate and a
  written rollback.
- Skills never send email, post publicly, or run destructive production
  commands by default. They draft and surface; the human executes.

## Reporting a problem with an existing skill

Open an issue with the skill name and what is wrong — a command that doesn't
exist, a description that doesn't fire, an instruction that's now misleading.
Corrections are the most valuable contributions here, because skills go stale
as tools change. If you can verify the fix against the real tool, say so.
