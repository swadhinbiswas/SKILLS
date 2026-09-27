---
name: git-history-surgery
description: Rewrite existing git history safely with interactive rebase, fixup/squash, edit/reword, split commits, --onto, and reflog recovery of dropped commits, while avoiding the rewrites that destroy other people's work. Use when a commit message is wrong, a change is in the wrong commit, a branch needs re-parenting, a secret must be purged, or someone says "force-push with lease" or "rebase -i". Triggers on "rewrite history", "squash", "reword", "interactive rebase", "fixup", "recover dropped commit", "reflog", "purge secret from history".
compatibility: Needs git 2.20+ for the todo-list features; --update-refs and --empty=drop need git 2.26+ / 2.38+ respectively.
metadata:
  version: "1.0"
---

# Git History Surgery

Rewriting history is fine when the commits are yours and nobody has pulled
them. It is a way to destroy other people's work when they are not. Decide
which case you are in before typing anything.

## Step 0 — Is this history yours alone?

| Situation | Rewrite? |
|---|---|
| Commits exist only on your branch, never pushed | Yes, freely |
| Pushed to a personal fork / nobody else has the branch | Yes, with `--force-with-lease` |
| Merged into a shared `main`/`trunk`/`release/*` | No. Add a new commit instead. |
| Someone else has pulled or based work on the branch | No, unless you coordinate and they rebase |
| Published tags, release branches, package-registry-published refs | No |

`git log --oneline origin/main..HEAD` shows exactly which commits you would be
rewriting. If that list is empty you are safe. If it is not empty and the
branch is shared, stop and propose a normal commit instead.

**Never run `git push --force` on a shared branch.** If you must, use
`git push --force-with-lease` (refuses if someone else pushed since your last
fetch) and announce it. `--force-with-lease=<refname>:<expect>` pins the
expected old value when plain `--force-with-lease` is too loose.

Before any surgery on a branch you might have to undo, make a backup ref. It
costs nothing and it is the only way back:

```sh
git branch backup/pre-rewrite-$(date +%Y%m%d-%H%M%S)
```

## Workflow

- [ ] 1. Classify the rewrite: message only, content only, or re-parenting
- [ ] 2. `git status` must be clean — stash or commit first
- [ ] 3. Create a backup ref
- [ ] 4. Do the smallest rewrite that achieves the goal
- [ ] 5. Verify with `git log`, then force-push with `--force-with-lease` only
      if the branch is unpublished or personal

`git status` clean means no unstaged, staged, *or* untracked changes in the
way. Rebase refuses otherwise:

```
error: cannot rebase: You have unstaged changes.
error: cannot rebase: Your index contains uncommitted changes.
```

If you genuinely cannot stash (you need the working tree as it is),
`git rebase --autostash` stashes and re-applies around the rebase and prints
`Created autostash:` / `Applied autostash.`. If the re-applied stash
conflicts, your changes are in `git stash list` — do not drop it.

## The operations, in the order you will usually need them

### 1. Fix the most recent commit's message

```sh
git commit --amend          # new message
git commit --amend --no-edit  # keep message, fold in staged changes
```

**`--amend` rewrites the commit, so the SHA changes.** Never amend a commit
that has been pushed to a shared branch.

### 2. Fold a fix into an older commit

```sh
git commit --fixup=<sha>            # message: "fixup! <original subject>"
git rebase -i --autosquash <upstream>
```

`--autosquash` moves `fixup!` lines under their target and rewrites `fixup` to
`fixup`, so no editor is needed. Use `git commit --squash=<sha>` instead when
you want to *edit* the combined message.

Without `--fixup`, mark the lines by hand in the todo list:

```
pick  a1b2c3d feat: add parser
fixup  d4e5f6a typo in parser doc
squash  7a8b9c0 add another test
```

`fixup` discards the second message; `squash` concatenates both into one
message and opens the editor. The rest of the todo vocabulary:

| Command | Meaning |
|---|---|
| `pick` | use the commit as-is |
| `reword` | use it, but edit the message only |
| `edit` | stop after applying, so you can amend |
| `fixup` / `fixup -C` | squash into the previous commit, discard message (`-C` reuses the fixup commit's own message) |
| `squash` | squash into the previous commit, combine messages |
| `drop` | do not apply this commit at all |
| `exec <cmd>` | run a shell command between commits |
| `label` / `reset` / `merge` | `-r`/`--rebase-merges` plumbing for preserving merge structure |

Read `references/recovery.md` for what each failure state looks like and how to
get out of it, including the abort paths and the "commit became empty" stops.

### 3. Split one commit into several

```sh
git rebase -i <upstream>     # mark the commit `edit`
# rebase stops on it:
git reset HEAD^              # uncommit, keep the work in the working tree
git add -p                   # stage the first logical chunk
git commit -m "feat: add X"
git add -p                   # stage the second chunk
git commit -m "test: cover X"
git rebase --continue
```

`git reset HEAD^` (mixed, no `--hard`) is the safe form — it moves the branch
back and leaves every change on disk. `git reset --hard` here would throw the
work away.

### 4. Re-parent a branch onto a different base

`rebase --onto` moves commits without replaying the ones you don't want:

```sh
# Take the 3 commits after the one being dropped and replay them on new-base
git rebase --onto new-base dropped-commit mybranch
# Rebase mybranch onto new-base, keeping the merge base it already had
git rebase --keep-base new-base mybranch
```

A concrete case: `main` moved, your branch has a commit that should never have
been on it.

```sh
git checkout mybranch
git rebase --onto main~1 <sha-of-unwanted-commit> mybranch
```

Related, and often what you actually want:

```sh
git rebase --onto main upstream mybranch   # update a stale feature branch
```

`--update-refs` (git 2.38+) also moves *other* local branches that point into
the range being rebased, so a second branch built on top of the first follows
it. Without it those branches are silently left pointing at the old commits —
the single most common reason a `--update-refs` rebase produces "impossible"
conflicts later. Git prints what it moved:

```
Updated the following refs with --update-refs:
	refs/heads/side
```

`--rebase-merges` (`-r`) replays merge commits instead of flattening them.
Without it, merge commits are dropped and the branch becomes linear — which
loses the "released in X, then Y" structure. If your project relies on merge
commits, always pass `-r`.

### 5. Move commits between branches

```sh
# One commit, elsewhere
git cherry-pick -x <sha>          # -x records "cherry picked from commit <sha>"

# A contiguous range
git cherry-pick <old>^..<new>

# Everything on a branch that upstream does not have
git cherry-pick <old>..<new>

# Search across all refs for a commit with a known message
git log --all --grep='panic in parser'
git log --all -S 'handleRequest' --oneline   # commits touching a string

# Delete the moved commits from the source branch
git rebase --onto <old>^ <new> <source-branch>
```

`git cherry -v <upstream> <branch>` lists what *would* be picked, `-` for
already-present commits and `+` for new ones. Check that before replaying a
large range.

### 6. Purge a secret

Rewriting removes the commit from the tip, but the object stays in every clone
that already fetched it, in `git reflog`, and in the remote's reflogs and
forks. Treat any pushed secret as compromised: rotate it first, then rewrite.

```sh
git filter-repo --path secrets/ --invert-paths   # separate tool, git-filter-repo
git filter-repo --replace-text exprs.txt        # replace specific strings
```

Then force-push every ref and have everyone reclone rather than pull. Plain
`git filter-branch` is deprecated and slow; if you must use it, set
`FILTER_BRANCH_SQUELCH_WARNING=1`.

## Gotchas

- **`git rebase` and `git rebase -i` disagree about "empty".** Verified on
  git 2.55: a non-interactive `git rebase` **drops** commits that become empty
  silently, while `git rebase -i` **stops** and asks. Use `--empty=drop`
  (git 2.26+) to make the interactive form match the non-interactive one, or
  `--empty=keep` to force the stop behaviour. The stop looks like:
  ```
  The previous cherry-pick is now empty, possibly due to conflict resolution.
  Otherwise, please use 'git rebase --skip'
  ```
- **A "dropped" commit often reappears as a conflict, not as a clean drop.**
  If commit B modified lines that commit A introduced, dropping A leaves B's
  patch with nowhere to apply: `Could not apply <sha>... # <subject>`. That
  usually means the commits are not independently applicable and need
  squashing rather than dropping.
- **Squashing loses the intermediate commit objects** and therefore any GPG
  signature on them, and any `Co-authored-by` trailer not carried in the final
  message.
- **Author date vs committer date.** Rebase preserves the *author* date of each
  replayed commit and sets a *new committer* date. `git commit --amend` keeps
  the author date unless you pass `--date`. Result: `git log --date=iso` shows
  the original author date but a commit date of "now". That is why rebased
  history looks scrambled when sorted by commit date. Use
  `git rebase --committer-date-is-author-date` to make them agree, or
  `--reset-author-date` for the opposite.
- **Rebase does not preserve reflog entries for the old commits** under the
  new SHAs. Before rewriting a shared branch, write down the old tip:
  `git rev-parse HEAD > /tmp/old-tip.txt`.
- **Hooks run during rebase.** `pre-rebase` runs once; `post-rewrite` runs at
  the end. `git rebase --no-verify` skips `pre-rebase` — the only rebase flag
  that does, and it does not skip `pre-commit` (rebase does not run
  `pre-commit` at all).
- **`git rebase` on a dirty tree fails, it does not auto-stash.** This is a
  feature. Use `--autostash` deliberately, not reflexively.
- **An interactive rebase that you started and abandoned leaves you in a
  detached HEAD state.** `git rebase --abort` returns you to the original
  branch. `git rebase --quit` stops the rebase but leaves HEAD where it is.
- **`rebase.missingCommitsCheck = error`** makes rebase fail if a `drop` line
  removes a commit you did not intend to drop. Set it to `warn` (or `ignore`)
  for interactive use; it protects against a miscounted todo list.
- **Do not rebase a branch that is currently checked out in another
  worktree.** Git refuses, but the error is unhelpful. `git worktree list`
  shows which worktrees hold which branches.
- **Interactive rebase list order is oldest first**, and the file you edit
  (`git rebase --edit-todo` to re-open it mid-rebase) shows `noop` as the first
  command when the range is already contained. Verify the upstream you passed
  before editing.

## Edge cases

- **Rebase says `fatal: invalid upstream '<sha>'`** — the upstream you gave
  resolves to nothing, usually because you meant `HEAD~N` and there are fewer
  than `N` commits. Check `git rev-list --count <upstream>`.
- **A commit you want is gone and you know roughly when.** `git reflog` on the
  branch, then `git reflog show <sha>` to read that entry's history, then
  `git branch recovered <sha>`.
- **A commit you want is gone and you know nothing.** `git fsck --lost-found`
  writes dangling commits under `.git/lost-found/commit/`; then restore the
  one whose message matches.
- **You rewrote a merge-heavy branch and want the old structure back.**
  `git reflog` the pre-rebase tip and rebase *from* it, or reset to it and
  re-apply forward.

## Safety checklist

- [ ] `git status` clean, backup ref created
- [ ] `git log --oneline origin/main..HEAD` — every SHA in it is one you are
      about to invalidate
- [ ] Nobody else has the branch, or you have told them and they will rebase
- [ ] No published tag or release branch points into the rewritten range
- [ ] `git push --force-with-lease`, never bare `--force`
- [ ] For a secret purge: the credential is already rotated

## Files

- `references/recovery.md` — read this when a rebase, amend, or reset leaves
  you in a state you do not recognise: conflict mid-rebase, empty commit,
  lost branch, detached HEAD, wrong `--onto` base. It maps the exact git
  output to the exit and the recovery.
