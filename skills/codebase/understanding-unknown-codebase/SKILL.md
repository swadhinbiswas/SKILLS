---
name: understanding-unknown-codebase
description: Get productive in an unfamiliar repository fast - orient on entrypoints, build, and tests, map the architecture, trace one feature end to end along its spine, and read git history and blame for the why behind the code. Use when onboarding to a repo, when asked to explain how something works, when finding where a feature lives, or before your first change in unfamiliar code.
metadata:
  version: "1.0"
---

# Understanding an Unknown Codebase

You are not paid to read the whole repository. You are paid to make a change
correctly. The method below gets you to "I can make this change safely" in
hours, by following the *spine* of one real feature rather than every branch
of the tree.

Read the code in the order the runtime executes it, not in directory order.

## Phase 1 — Orient (30 minutes, no code edits)

Answer these from files, not memory. If you cannot point at the file, you are
still orienting.

- [ ] **What is this?** README, `package.json`/`pyproject.toml`/`go.mod`
      `description`, `Cargo.toml`, the first paragraph of the docs site. One
      sentence, in your own words.
- [ ] **What is the entrypoint?** For a service: the `main`, the `if
      __name__ == "__main__"`, the Dockerfile `CMD`, the Procfile, the
      `main.ts`. For a library: the package `exports` / `main` in the manifest.
- [ ] **How do I run it?** Find the real command, not the aspirational one.
      Check, in order: `Makefile` (the `dev`/`run`/`test` targets), the
      `scripts` block in the manifest, `docker-compose.yml`, the CI workflow
      (what CI runs *is* the supported invocation), then the README.
- [ ] **How do I run the tests?** Same order, plus the config file
      (`pytest.ini`, `jest.config`, `vitest.config`, `phpunit.xml`) for how to
      run a single file — you will need that constantly.
- [ ] **What is the language, version, and framework?** Exact versions from
      the lockfile/manifest, not "latest". Frameworks change behaviour between
      major versions.
- [ ] **What are the immediate neighbours?** What calls this, and what does
      this call? Service-to-service: URLs, service registry, or proto files.
- [ ] **What is the data store, and where is the schema?** Migrations
      directory, `schema.sql`, `prisma/schema.prisma`, `models/`.

The fastest orientation is often empirical: **run the thing.** `make dev` or
`docker compose up`, hit the health endpoint, and make one request you can
watch. An app that runs locally with a debugger attached teaches you more in
five minutes than an hour of reading.

## Phase 2 — Map the architecture (1–2 hours)

Build a one-page map. Not a diagram in a tool — a diagram you can redraw from
memory, because that is the one that matters.

1. **List the top-level directories/packages and give each a one-line job.**
   Read each package's `__init__`/index/`README`/`package.json` description.
   Anything you cannot summarise in a line is a module you have not opened yet.
2. **Find the layers.** Almost every codebase has some version of
   `transport → handler → service → data`. Write down where the boundaries are
   in *this* codebase, with the file that defines each. A repo that does not
   follow this pattern is the interesting finding.
3. **Map the dependencies, not the directories.** For a single service:

   ```bash
   # who imports whom (adjust the language)
   rg -l 'from app\.' --type py | head -50          # or: go list -deps ./...
   # entrypoints: main files, route registrations, CLI/command registration
   rg -n 'FastAPI\(|app\.(get|post)\(|@app\.route|addHandler' 
   # config: where settings come from
   rg -n 'os\.environ|getenv|process\.env|BaseSettings'
   ```

   A quick import-graph in three commands beats a diagram nobody maintains.
4. **Find the "seams"** — the places where the system talks to the outside:
   HTTP handlers, queue consumers, cron/scheduled jobs, DB access, third-party
   clients, file I/O. These are where the interesting behaviour and the bugs
   live, and they are a small set of files.
5. **Find the cross-cutting concerns** and where they hook in: auth
   middleware, error handling, logging/tracing, config, DI container, feature
   flags. A middleware is a high-leverage place to understand — it touches
   every request.

Write the map down in a scratch file. It will be wrong; writing it is what
finds out where it is wrong.

## Phase 3 — Find the spine of one feature (the important one)

Pick **one** concrete thing you need to change or explain ("create an order",
"password reset", "the `/v1/search` endpoint"). Trace it **end to end, in
execution order**, from the outside in:

1. **Entry**: the route/handler/command. What is the raw input, what is the
   output? Write down the contract.
2. **Down the stack**, one call at a time: handler → service → repository/ORM →
   query. At each hop, note the file, the function, and what it adds
   (validation, auth, transformation, caching).
3. **Out to the world**: the SQL it runs (or the queue it publishes, the API it
   calls). Note the actual query text and the table it touches.
4. **Back up**: the response path — how the result is shaped, serialised, and
   returned, including any error mapping.

The deliverable is a **one-screen call chain with file:line references**:

```
POST /v1/orders
  routes/orders.py:88  create_order()        auth via require_scope("orders:write")
    services/order.py:210  OrderService.place()
      services/pricing.py:44  PriceEngine.apply_rules()
        repos/catalog.py:120  CatalogRepo.price()   -> SELECT price_minor FROM
                                                  catalog WHERE sku=$1
      repos/orders.py:88  OrderRepo.insert()       -> INSERT INTO orders ...
  serialised in schemas/order.py:30 -> 201 + Location header
```

Three things fall out of this for free: where your change goes, which tests
cover the path (search for the function names), and what the blast radius is.

**Follow the data, not the code, when there is a queue or a job.** A request
that returns `202 Accepted` is not the end of the feature; the work happens
somewhere else, triggered by an event or a poll. Find the consumer.

## Phase 4 — Read the history for the *why*

The current code tells you *what*; only history tells you *why*, and most
surprises in an unfamiliar codebase are deliberate. Git is the cheapest source
of that.

```bash
# Why does this line exist?  (blame a line range, not the whole file)
git blame -L 120,140 -- src/services/order.py
# Follow the links in the commit message (Fixes #, Part of ADR-0012)
git log -1 --format='%H %s%n%b' $(git blame -L 130,130 --porcelain src/services/order.py | head -1 | cut -d' ' -f1)
# How did this file evolve?  Follow renames, and read the whole story
git log --follow --oneline -- src/services/order.py
# What else changed in that commit?  Usually reveals the whole feature
git show --stat <sha>
# When did this break before?  Search the log
git log --oneline --grep='timeout' --grep='deadlock' -i | head -20
# What is actively changing right now?  (do not touch these without reading)
git log --oneline --since='2 weeks ago' | head -30
git log --oneline --format='%h %s' -- src/area/you/need/ | head -20
```

What to look for in history:

- **Reverts.** `git log --grep='^Revert' -i` shows what the team has already
  tried and abandoned. Do not re-attempt it without understanding why.
- **Hot spots.** `git log --format= --name-only | sort | uniq -c | sort -rn |
  head` — the files that change most are the ones that are hardest, not
  necessarily the worst-written. Frequent change plus frequent reverts is the
  real signal.
- **A revert of your exact plan.** Check before you propose anything that
  sounds clever.
- **The commit that introduced a strange line.** Blame it, read the diff, and
  the weirdness is usually a one-line fix for a specific incident, with the
  incident number in the message.
- **Recently and heavily changed files** — they are in flux; your change will
  conflict, and the maintainer may be mid-refactor. Ask.

Also read, if they exist: `CONTRIBUTING.md` (the house rules that will get
your PR rejected), `docs/adr/`, the open PRs and issues for the area, and any
`README` inside the specific directory.

## Phase 5 — Use the tools that answer for you

- **The compiler and the type checker are documentation that cannot go
  stale.** Jump-to-definition on a type, an interface, or a signature answers
  "who else calls this" and "what can I pass here" faster than grep. If the
  codebase is dynamically typed, this advantage is gone and you need grep more.
- **`rg` (ripgrep) beats `grep -r`**: it respects `.gitignore` and is fast.
  Useful invocations:
  ```bash
  rg -n 'def place_order|function placeOrder'        # find a definition
  rg -n 'place_order\(' -g '!tests'                  # find callers, skipping tests
  rg -n --type ts 'interface Order\b'                # find a type's definition
  rg -l 'import.*pricing'                            # who depends on pricing
  rg -n 'TODO|FIXME|HACK|XXX' src/                   # the team's own map
  rg -n 'except:\s*pass|catch \{\}' -g '!tests'      # swallowed errors
  ```
- **The tests are a specification.** Read the test for the feature before
  reading the implementation: the test names tell you the behaviours that
  matter, and the fixtures show you the real shapes. `tests/` and
  `spec/` are the highest-value code in an unfamiliar repo.
- **A linter/type checker is a map of the invariants**: `mypy --strict` or
  `tsc --noEmit` tells you which files are checked and which are allowed to
  lie. `--strict` failing in 3 files tells you those 3 are the risky ones.
- **Coverage, if it exists** (`coverage/lcov.info`, `nyc`), tells you which
  code is untested — i.e. where to be careful.
- **The debugger beats reading** for behaviour: set a breakpoint at the entry
  point and step through one real request. You will find the actual call order,
  not the one you inferred.
- **Run the failing test with a breakpoint and a stack trace.** The stack is a
  free call chain.

## Phase 6 — Make a small change, safely

- **Write a failing test first** for anything you are changing. It proves you
  understand the path and it is your net.
- **Make the smallest change that does the job.** Do not clean up nearby code
  in the same commit; a second, separate commit, and get the first one merged
  first if you can.
- **Read the diff for the file's conventions**: naming, error handling, import
  style, test style, comment density. Match them. A correct change in the
  wrong style gets rewritten by the maintainer.
- **Run the whole relevant suite plus the linter** before you claim it works.
- If you had to learn something the code did not say, **write it down** — a
  comment at the decision point, or a short note in your PR. The next person
  will not have your afternoon.

## Gotchas

- **The README describes the aspiration, the tests describe reality, the
  history describes why.** When they disagree, believe the code and the tests,
  then check the history to find out which one is stale.
- **Directory names lie.** `utils/`, `helpers/`, `common/`, and `shared/` are
  where code goes to be depended on by everything; treat them as a smell and
  read them last, if at all.
- **A framework can hide the entrypoint.** In a Rails app, Sinatra app, or a
  DI-heavy framework, the routes are registered somewhere non-obvious
  (`config/routes.rb`, the container config, a `start()` you have to find). If
  you cannot find where the routes are, grep for the URL string you care
  about, not for `route`.
- **Generated code and vendored directories are the largest part of some
  repos.** Identify them and exclude them from every search; they will bury
  the signal. Check `.gitignore`, the build config, and a `vendor/`,
  `node_modules/`, `dist/`, `.venv/`.
- **Feature flags make behaviour conditional at runtime.** The code path you
  read may not be the path that runs. Find the flag's current value before
  concluding what the system does.
- **Multiple entrypoints share a core.** A CLI, a worker, and a web handler
  often all call into the same service layer. "Where does X happen" may have
  three answers; the shared layer is the one that matters.
- **`git blame` on a file rewritten by a formatter or a mass rename is
  useless** — every line points at the formatting commit. Use
  `git log --follow -w` to skip whitespace-only commits.
- **An unfamiliar codebase's "obvious" bug may be load-bearing.** Before
  fixing something that looks wrong, find out whether anything depends on the
  behaviour (a test pinning it, a caller relying on the edge case, a comment
  in the history). Ask if unsure.
- **Time-box the reading.** Deep reading of a whole module is a trap; follow
  the spine, make a change, and let the next change pull in the next part.
  Nobody ships a full understanding of a codebase on day one.
- **Ask a targeted question early** if the history is silent. "Was the retry
  limit here set because of a specific incident?" takes a maintainer one
  minute and saves you a day.

## Deliverable

After Phase 3, you should be able to produce, without looking anything up:

- The one-sentence description of the system.
- The exact commands to run it and its tests, including a single test file.
- The one-screen call chain for the feature, with file:line references.
- The three files you would edit to change it, and the tests that cover it.
- The two things that surprised you, and what history says about them.

That is enough to be useful. Everything after that is learned by changing
things.
