---
name: git-workflow-and-branches
description: Choose and run a branching strategy that fits the project - trunk-based, GitHub flow, or gitflow - with an explicit merge-vs-rebase policy per context, short-lived branches, clean PRs, and a real backport process. Use when setting up a team's git conventions, deciding whether to rebase or merge a PR, setting up release branches, or when a user asks about branch naming, PR review, or cherry-picking a fix to a maintenance branch. Triggers on "branching strategy", "trunk-based", "gitflow", "github flow", "rebase or merge", "release branch", "backport", "PR review", "long-running branch", "stale branches".
compatibility: Applies to any git 2.x remote (GitHub, GitLab, Bitbucket, Gitea). PR/merge-queue wording varies by host; verify host-specific menu names.
metadata:
  version: "1.0"
---

# Git Workflow and Branches

Two decisions decide how painful a repo is: how long a branch lives, and
whether merges or rebases go into shared history. Everything else is
convention.

## Pick one strategy

| Strategy | Shape | Fits |
|---|---|---|
| **Trunk-based** (default) | Short branches off `main`, merged or rebased in within a day or two | Continuous deployment, strong test suite, most web services |
| **GitHub flow** (the default here) | `main` is always releasable; every change is a PR; deploy from `main` | Team-shipped SaaS, one production environment |
| **Gitflow** | `develop` + `feature/*` + `release/*` + `hotfix/*`, long-lived | Versioned software with hand-managed releases, multiple supported versions |
| **Release branch model** | `main` plus a `release/1.2` cut from it, cherry-picked fixes forward | You ship versions people upgrade between, and need to patch old ones |

Default: **trunk-based with GitHub flow**. Branch from `main`, land the change
within a day, keep `main` always deployable. Move to release branches only
when you genuinely support more than one version in production at a time.

Gitflow's cost is real and it is not a style preference: `develop` and
`main` drift, merges come back as conflicts weeks later, and hotfixes get
cherry-picked into a branch nobody tested. Most projects that adopt it end up
running a slow trunk-based process while carrying all of gitflow's ceremony.
Do not adopt it because a blog post said to.

## Branch naming

Make names machine-readable. The branch name should say what the change *is*,
so release tooling, changelog generators, and `git log --oneline` all work
without a lookup table.

```
feat/user-token-refresh
fix/checkout-total-rounding
chore/bump-deps
hotfix/1.4.2-null-deref
release/1.5
spike/otel-sampling
```

Rules: lowercase, hyphens not underscores (slashes are fine and create a
directory-like grouping in `git branch` output), no personal names, no ticket
numbers in the branch name — put the ticket in the PR body, where it is
searchable from the commit too. If your forge requires a ticket prefix
(`PROJ-123-...`), put it first and make the rest readable.

## The merge-or-rebase policy

This is the one thing to write down in `CONTRIBUTING.md`, because the default
is currently wrong in most repos.

| Context | Do this | Why |
|---|---|---|
| Your feature branch, before opening the PR | `git rebase origin/main` | Removes merge noise, keeps the PR a straight-line diff of intent |
| Merging a PR into `main` | **Merge commit** (or squash) | Preserves that the change happened as a unit, and the branch's existence |
| Merging a PR into a long-lived `develop` | Merge commit | Keeps the branch's commits reviewable as a unit |
| Merging *into your own long-lived private branch* | Rebase | Nothing else references those SHAs |
| Backport to a release branch | Cherry-pick | Records provenance via `-x` |
| Any branch anyone else has pulled | Never force-push; merge instead | A force-push invalidates their local SHAs |

**Squash-merge is the other defensible default**, and it is better when the
team makes messy commits and the codebase's history readability is not a
priority. Pick squash or merge once, write it down, and stop relitigating per
PR. Do not mix: a repo that sometimes squash-merges and sometimes
merge-commits produces a history nobody can read.

Whichever you pick, PRs must be up to date before merge
(`git rebase origin/main` in your branch), because a rebase-based review means
the reviewer reads a diff that will actually merge.

## Keeping branches short-lived

Long branches are the cause of nearly all merge pain. Enforce these:

- **One branch, one concern.** A branch that fixes a bug *and* reformats the
  file cannot be reviewed and cannot be reverted.
- **Delete on merge.** Automate it (GitHub's "Automatically delete head
  branches", GitLab's equivalent). Local stale branches: `git fetch --prune`
  removes the remote-tracking refs; `git branch -d <name>` removes locals that
  are merged into the current branch (`-d` refuses if unmerged — that refusal
  is the feature).
- **A branch older than a few days is a design problem, not a scheduling
  problem.** Either land pieces of it, or throw it away and start over.
- **Do not let a branch accumulate a stash of unrelated WIP.** Use
  `git stash create` / `git worktree` / throwaway commits on a `wip/` branch
  instead of a long-lived `wip` branch that everyone merges from.

Automated reminders work better than nagging:

```sh
# Branches with no commits relative to main, older than 21 days
for b in $(git for-each-ref --format='%(refname:short) %(committerdate:unix)' refs/heads/); do
  set -- $b
  age=$(( ( $(date +%s) - $2 ) / 86400 ))
  if [ "$age" -gt 21 ] && git log --oneline "main..$1" | grep -q .; then
    printf '%s\t%s days\n' "$1" "$age"
  fi
done
```

## PR hygiene

A PR is a document, not a diff dump.

- **Title: Conventional Commits** (`feat:`, `fix:`, `chore:`, `refactor:`,
  `test:`, `perf:`, `build:`, `ci:`, `docs:`). If your release tooling derives
  the changelog from titles, the title is the release note.
- **Description answers three questions**: what changed, why, and how it was
  verified. A reviewer should be able to approve from the description plus the
  diff without asking you anything.
- **Size.** Under ~400 changed lines gets a real review. Above that, the
  review is a rubber stamp, and the defects concentrate in the unreviewed
  parts. Split by commit, not by file.
- **Self-review the diff before requesting review.** `git diff origin/main...HEAD`
  — the three-dot form. `git diff main` on a branch that has merged main in
  shows you nothing useful; `...` diffs from the merge base.
- **Screenshots or traces for anything user-visible.** Reviewers cannot run
  your branch.
- **Never approve your own PR** if the platform shows it, and never drive
  review through a second account.

### Keeping a branch current

```sh
git fetch origin
git rebase origin/main          # while the PR is open, force-push is safe:
git push --force-with-lease     # the branch is yours until merge
```

After the PR is merged, your branch is nobody's business — delete it.

## Merge conflicts in a PR

Reach for `git-merge-conflicts` when there is real conflict work. The policy
rule: **a conflict that takes more than a few minutes to resolve is a signal
to change the approach** — usually rebase onto `main` and redo the change on
top of current code, rather than reconciling two divergent versions of the
same file.

Record conflict resolutions with `git rerere` (see `git-merge-conflicts`) so
the same resolution is reused across rebases and across everyone who has it
enabled.

## Release branches and backports

Adopt a release branch when you must patch a version already in production.

```
main            # next release, always deployable
release/1.4     # 1.4.x line; receives only fixes
hotfix/1.4.2-null-deref
```

Policy:

1. `release/1.4` is **cut from `main`** at the moment 1.4.0 shipped:
   `git branch release/1.4 <1.4.0-tag>`. It never receives new features.
2. Fixes go on `main` first. Then backport:
   ```sh
   git checkout release/1.4
   git cherry-pick -x <sha>          # -x records where it came from
   git push origin release/1.4
   ```
3. `git cherry -v release/1.4 <sha>` tells you whether the fix is already
   there (`-`) or needs picking (`+`).
4. Then merge `release/1.4` forward into `main` if the fix has a version
   bump, so the next release includes it:
   `git checkout main && git merge --no-ff release/1.4`.
5. Tag releases with annotated, signed tags: `git tag -a v1.4.1 -m "v1.4.1"`
   (see `git-signing-and-hooks`). Never move a published tag — delete and
   re-tag only if nobody has consumed it.

Rules that keep backports from rotting:

- **Never cherry-pick a `release/*` commit back into `main`.** Merge forward
  instead. Backward merges re-introduce old release commits.
- **A backport that does not apply cleanly means the fix was structural, not
  a one-line bug.** Do it by hand on `release/1.4` and merge the whole thing
  forward, rather than forcing a `git cherry-pick --strategy=recursive -X theirs`
  (see `git-merge-conflicts` for what `-X theirs` actually does to a file).
- **Re-test on the release branch.** A fix that assumes the current version's
  code can break 1.4.

## Fork and upstream

- `origin` is your fork, `upstream` is the original. `git remote -v` to check.
- Never commit directly to `upstream`.
- `git fetch upstream && git rebase upstream/main` to sync.

## Multiple work in parallel

Use worktrees, not stashes, for genuine parallelism:

```sh
git worktree add ../hotfix -b hotfix/urgent main
git worktree add ../spike --detach
git worktree list
```

Each worktree has its own HEAD and index, sharing one object database. You
cannot check out the same branch in two worktrees — git refuses with
`fatal: '<branch>' is already checked out at ...`, which is the point.

## Gotchas

- **`git pull` is ambiguous and its configuration differs per machine.** The
  same command can merge or rebase depending on `pull.rebase`. Replace it
  with two explicit commands: `git fetch origin` then either
  `git rebase origin/main` or `git merge origin/main`. Say which one in
  `CONTRIBUTING.md`.
- **`git push` after a rebase to your own open PR branch needs
  `--force-with-lease`.** Plain `--force` on a branch someone might have
  pushed to is how you delete a colleague's commits.
- **`origin/main` is a cache.** It is a remote-tracking ref updated by
  `fetch`, and it is what you last fetched, not what is on the remote. It goes
  stale silently. `git log --oneline origin/main..HEAD` is only meaningful
  after a fetch.
- **A branch that is merged with `--no-ff` and then rebased produces duplicate
  commits** in the log. Look for identical messages with different SHAs.
- **`git merge --squash` then commit yourself** produces a commit that is not
  on any branch until you commit; if you `git checkout` before committing, the
  staged changes come with you and you may commit them onto the wrong branch.
  Commit immediately or abort.
- **GitHub squash-merge of a PR whose branch is also pushed elsewhere** leaves
  the original commits alive on the branch; if the branch is then rebased and
  re-merged you get the same change twice in the tree's history.
- **Deleting a branch on the remote does not delete the objects.** Recovery is
  via the remote's reflog and your local `origin/<branch>` until you fetch
  with `--prune`. See `git-refs-and-objects`.
- **`git switch` and `git checkout` are not identical.** `git switch` (2.23+)
  refuses to guess, `git checkout` will happily carry an uncommitted change
  across branches. Use `git switch` for branch changes; the refusal is
  helpful.
- **`git config --global` is invisible to new teammates.** Put
  `pull.rebase = false`, `fetch.prune = true`, `push.default = current`, and
  `rerere.enabled = true` in a `.gitconfig` in the repo root and have
  everyone copy it, or document the commands in `CONTRIBUTING.md` instead.

## When to move to a different strategy

- PRs routinely sit for more than a week → long branches, not gitflow. Fix the
  review queue; gitflow makes it worse.
- You ship a versioned product with customers on old versions → add release
  branches, keep trunk-based development.
- You need to support five simultaneous majors and hand-manage upgrade paths →
  gitflow's release discipline is worth its cost, but still branch from a
  short-lived `main`, not from a permanent `develop` that accumulates a week
  of unreviewed work.
