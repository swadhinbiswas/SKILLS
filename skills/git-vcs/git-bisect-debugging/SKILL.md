---
name: git-bisect-debugging
description: Find the exact commit that introduced a bug, a crash, or a performance regression using git bisect run with an automated test, benchmark, or reproduction script, and drive the manual interactive workflow correctly. Use when something worked yesterday and does not now, when a regression appears after a merge, when a test started failing, or when a user mentions git bisect. Triggers on "which commit broke this", "regression", "bisect", "worked before", "reproduce on a range of commits", "find the commit that made it slow".
compatibility: Needs git 2.7+ for git bisect run; --first-parent, --term-old/--term-new and --no-checkout need 2.16-2.20+.
metadata:
  version: "1.0"
---

# Git Bisect Debugging

Binary search over commits turns "somewhere in 300 commits" into "this
commit, and here is its diff". The hard part is not the git; it is writing a
script that answers one question — good or bad — for *any* commit in the
range, including the ones that are not the one you care about.

## Workflow

- [ ] 1. Establish a **fast, reliable, scriptable** good/bad test. If you
      cannot, you are not ready to bisect.
- [ ] 2. Find a **known-good** commit and a **known-bad** commit
- [ ] 3. `git bisect start <bad> <good>` in a **clean** worktree
- [ ] 4. `git bisect run <script>` with exit 0 = good, 1-124 = bad, 125 = skip
- [ ] 5. Record the finding, then `git bisect reset` — always
- [ ] 6. Read the commit: `git show <sha>`

## Step 1 — You need a script before you need a bisect

The test must run unattended on a checkout of an arbitrary commit, repeatedly,
for hours if it takes hours. Three properties, in order of importance:

1. **Deterministic.** If it flakes at 20%, the bisect result is wrong and you
   will not notice. Re-run any surprising verdict manually.
2. **Self-contained.** It must build, install, migrate, seed, and run without
   you. Anything it needs from you it will not have.
3. **Fast.** Each step is a full checkout plus your script, so 20 steps at
   three minutes is an hour. A slow test is still worth it; an interactive
   one is useless.

Write it as a single file, exiting non-zero on failure:

```sh
#!/usr/bin/env bash
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

# Build/setup is the part that breaks across old commits. Guard it so a
# build failure is not mistaken for a test failure.
if ! ./scripts/bootstrap.sh >/tmp/bisect-build.log 2>&1; then
  echo "build failed at $(git rev-parse --short HEAD)" >&2
  exit 125          # skip, not bad
fi

if ./scripts/ci-test.sh --suite regression-4711 >/tmp/bisect-test.log 2>&1; then
  exit 0
fi
exit 1
```

**A build failure is not a bug.** If commit 40 cannot compile, exit 125
("skip") and let bisect route around it. Exiting 1 there makes bisect report a
compile error as the culprit, which is the most common way a bisect wastes an
afternoon.

`git bisect run` exit-code contract (from the git docs):

| Code | Meaning |
|---|---|
| 0 | good — the bug is fixed here |
| 1-124 (except 125) | bad — the bug is present here |
| 125 | skip — this commit cannot be tested; bisect picks another |
| 126-128 | not a valid verdict; bisect aborts |
| 129-192 | the script died from a signal; aborts |
| >=200 | aborts |
| 255 | aborts |

Note that **any non-zero exit other than 125 counts as "bad"**, including 127
("command not found") and 1 from a `set -e` in your own setup code. Test the
script on the known-good and known-bad commits by hand before trusting it.

## Step 2 — Good and bad bounds

- **Good must actually be good, and the test must pass there.** Verify.
- **Bad must actually be bad.** If HEAD is bad, that is often not enough —
  the regression may be somewhere else and you may be bisecting a range where
  every commit looks the same.
- **Good must be an ancestor of bad.** `git merge-base --is-ancestor <good> <bad>`
  checks it. If your branches diverged, there is no single linear range and
  bisect is the wrong tool — use `git log --ancestry-path` or bisect each
  side.
- **Too wide a range wastes time.** If you are bisecting across a 5,000-commit
  range, narrow it by hand first with `git log --oneline <bad>..<good>` and
  pick a good commit you can vouch for. Bisect is logarithmic; 13 steps covers
  8,192 commits, but each step is a full CI-ish run.
- `--first-parent` (git 2.16+) follows only the first-parent chain, so a merge
  that merely *brought in* a bug counts as the boundary rather than the
  hundreds of commits it pulled in.
- Paths can narrow the search space to commits touching a directory or file:
  `git bisect start <bad> <good> -- src/api`. Use it when the change is
  obviously in one area; it makes the search dramatically cheaper.

## Step 3 — Run it

```sh
git status                      # must be clean first
git stash list                  # note anything stashed; bisect moves HEAD around
git bisect start HEAD known-good-sha
git bisect run ./bisect-test.sh
```

Verified output shape:

```
Bisecting: 1 revision left to test after this (roughly 1 step)
[316da208197293d1ccef248fe07a9da8ec371072] c3
running 'bash' '-c' '...'
316da208197293d1ccef248fe07a9da8ec371072 is the first 'bad' commit
commit 316da208197293d1ccef248fe07a9da8ec371072
...
bisect found first 'bad' commit
```

The first line git prints is the number of steps it expects — sanity-check it
against your range. If it says "roughly 1 step" over a 400-commit range, your
`good` bound is wrong.

## Step 4 — `git bisect reset`. Always.

Bisect leaves you **detached HEAD on some old commit** with the bisect state
in `.git/BISECT_*`. If you leave it:

- you commit on top of a detached HEAD,
- `git checkout feature` "loses" commits that are only on the detached HEAD,
- the next person to run `git bisect` starts from wherever you left it.

```sh
git bisect reset          # back to the branch you started on
git bisect reset <sha>    # or land on a specific commit deliberately
```

Put `git bisect reset` in the same run as the bisect, or make it the first
line of your shell prompt while debugging. There is no state that "finishing"
the bisect clears for you.

## Other subcommands

```sh
git bisect log                  # the record of every good/bad/skip, replayable
git bisect replay <logfile>     # re-run the same decisions on another range
git bisect visualize <bad> <good>   # or git bisect view — marks good/bad commits in gitk
git bisect skip [<rev>...]      # this commit can't be tested; pick another
git bisect next                 # test the commit git selected
git bisect terms                # see what "good" and "bad" are called
git bisect start --term-old=fast --term-new=slow <slow> <fast>
git bisect start --no-checkout   # don't move HEAD; operate on BISECT_HEAD instead
```

`git bisect replay` is the one that saves you: it turns an interactive session
into a file you can hand to CI, re-run, or attach to a ticket.

`--term-old`/`--term-new` (git 2.20+) is for bisecting across a *regression of
a good thing* — a bisect where "good" means fast and "bad" means slow, or
"good" means no leak and "bad" means leaked.

## The manual workflow

Use it when the test needs a human, or when you want to see each commit.

```
git bisect start
git bisect bad  <sha>     # the known-bad commit
git bisect good <sha>     # a known-good one
# git checks out a midpoint; test it
git bisect good | git bisect bad
# repeat until git prints "is the first 'bad' commit"
git bisect reset
```

`git bisect start <bad> <good>` is the same thing in one command; several
`good` commits can be listed and any of them satisfying the test works:
`git bisect start HEAD good-a good-b`.

When a commit is untestable — a test that cannot run against that era of the
code — `git bisect skip` and let bisect pick a different one. Skipping too
many leads to:

```
status: waiting for 'good' commit(s), 'bad' commit known
```

which means no good commits remain; supply more with `git bisect good <sha>`.

## Bisecting a performance regression

The difference from a bug bisect is that "bad" is a *measurement*, so the
script has to be a benchmark that resists noise. Rules:

1. **Define the threshold before you start**, from a number you already
   trust. "20% slower than the baseline commit's median" — not "feels slow".
2. **Warm up, then measure repeatedly, and use a statistic that is robust to
   outliers** (median, or the 25th percentile). A single `time` reading will
   happily bisect you onto a commit that merely ran while another process was
   recompiling.
3. **Pin everything else**: CPU governor, other load, container CPU limits,
   build flags, and disable turbo/frequency scaling if you can.
4. **Compile before you measure.** Comparing an interpreted script commit
   against a compiled one measures the compiler.
5. **A benchmark that fails to compile on old commits must exit 125**, not 1.
   Framework upgrades during the range are the norm.

```sh
#!/usr/bin/env bash
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

./scripts/bootstrap.sh >/tmp/bench-build.log 2>&1 || exit 125
[ -x ./target/release/bench ] || exit 125

./target/release/bench --quick --format=json >/tmp/bench.json || exit 1

# Threshold: 250 ms p50. Tune to your project.
python3 - <<'PY' || exit 1
import json, sys
p50 = json.load(open("/tmp/bench.json"))["p50_ms"]
sys.exit(1 if p50 > 250 else 0)
PY
```

This script is stdlib-only and self-contained; adapt paths and the threshold to
the project. Put it in the repo (or the scratchpad) as a real file — an
inline one-liner passed to `git bisect run bash -c '...'` works but is
unquotable and unrepeatable.

Two extra tricks:

- **`--no-checkout`** keeps your working directory untouched and puts bisect's
  "currently testing" commit in `BISECT_HEAD`. Use it when your benchmark
  needs a persistent build directory or a database that is expensive to
  recreate. Your script must then build from `BISECT_HEAD`, not `HEAD`.
- **Build the benchmark once and bisect only the *data*.** If the regression is
  in input data or config rather than code, you can often skip git bisect
  entirely and bisect the data itself.

## Gotchas

- **A dirty worktree breaks it.** `git bisect start` refuses:
  `error: cannot bisect: You have unstaged changes.` Commit, stash, or work
  in a separate worktree.
- **Submodules and LFS are not checked out at old commits** unless they are
  present. A bisect script that assumes `vendor/` or LFS content exists will
  fail in a way that looks like a test failure. Use `git lfs pull` in the
  script, or skip those commits with 125.
- **A test that only fails intermittently gives a wrong answer.** Run any
  "first bad commit" that looks wrong manually three or four times before
  believing it. `git bisect run` cannot distinguish noise from signal.
- **Bisecting across a merge-heavy branch without `--first-parent`** attributes
  the bug to the merge commit instead of the commit that caused it, because
  the merge's second parent is part of the search space.
- **The range must be reachable from the bad commit.** In a shallow clone,
  `git bisect start` fails with something like
  `fatal: bad revision 'HEAD~50'` because the history is not there. Fix the
  clone first (`git fetch --unshallow`).
- **`git bisect` marks commits in `.git/BISECT_*`, not in the reflog.** Do not
  hand-edit those files. If the state is corrupt, `git bisect reset` clears
  it and you restart.
- **Hooks still run.** Every checkout during the bisect can trigger
  `post-checkout`, and a commit you make at the found revision triggers the
  normal hooks. Do not bisect with a repo whose hooks need a node_modules that
  an old commit does not have.
- **Do not bisect a build that needs the network.** Rate limits, flaky
  registries, and CDN failures produce false "bad" verdicts. Cache
  dependencies, or bisect with an offline build.

## Recording the result

```sh
git bisect log > bisect-log.txt
git show <first-bad-sha> --stat
```

A bisect without a written bound and a written test script is a story someone
will have to re-tell in six months. Keep both.
