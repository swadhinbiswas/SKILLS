# Rebase / history-rewrite recovery

Maps the exact output git prints to what it means and how to get out. Verified
against git 2.55.

## First move, whatever happened

```sh
git status                 # is a rebase/merge/cherry-pick in progress?
git rebase --abort         # returns you to the original branch, nothing else
```

`git rebase --abort` is always safe to run and is the correct answer whenever
you are unsure. It restores the pre-rebase branch pointer and the working tree
as they were. The cost is only the work you have already done by hand inside
the rebase.

If a rebase already finished successfully, `--abort` does nothing. For that
case see "Recovering a finished rewrite" below.

## Mid-rebase states

### Conflict while replaying a commit

```
Auto-merging src/parser.py
CONFLICT (content): Merge conflict in src/parser.py
error: could not apply a1b2c3d... feat: add parser
hint: Resolve all conflicts manually, mark them as resolved with
hint: "git add/rm <conflicted_files>", then run "git rebase --continue".
```

Fix the files, `git add` them, then `git rebase --continue`. State lives in
`.git/rebase-merge/`; `git status` and `git rebase --show-current-patch` tell
you exactly where you are.

- `git rebase --skip` — this commit cannot apply and you have decided it
  should not be replayed. This is the right answer for a commit you just
  `drop`ped whose content another commit recreates.
- `git rebase --abort` — back to the start.
- `git rebase --quit` — stop the sequence but keep HEAD and the working tree
  exactly as they are. Use when you want to abandon the rebase *and* keep the
  conflict resolution you have staged.

### "The previous cherry-pick is now empty"

```
Rebasing (3/3)The previous cherry-pick is now empty, possibly due to conflict resolution.
If you wish to commit it anyway, use:
    git commit --allow-empty
Otherwise, please use 'git rebase --skip'
```

The commit's changes are already in the tree, so replaying them would produce
a commit with no diff. Choose deliberately:

- `git rebase --skip` — drop it (normal when you removed the commit that
  introduced the change).
- `git commit --allow-empty && git rebase --continue` — keep an empty commit
  as a marker. Rarely what you want in a product branch.
- `git rebase -i --empty=keep <upstream>` on a re-run keeps it without stopping.
- `git rebase -i --empty=drop <upstream>` on a re-run drops it silently, which
  is what a non-interactive `git rebase` does anyway.

The `Rebasing (3/3)` prefix with no newline before the message is normal; it
is the progress line, not a truncation.

### `Could not apply <sha>... # <subject>` with no conflict

The patch did not apply because its *context* disappeared — usually a
prerequisite commit was dropped or squashed. Read the diff, not the tree:

```sh
git rebase --show-current-patch
```

Fix by hand, or `git rebase --skip` if the dropped commit already introduced
the content. Reordering the todo list so the dependency comes first is
usually the real fix.

### A rebase with 500 commits and one bad patch

Do not abort. Skim forward and mark the ones you want to skip:

```
edit a1b2c3d old approach
drop d4e5f6a superseded by the above
pick 7a8b9c0 next
```

Then `git rebase --continue`. `git rebase --abort` throws away all of it.

### `fatal: invalid upstream '<sha>'`

You passed an upstream that does not exist. The most common cause is
`HEAD~N` when the repo has fewer than `N` commits, or a ref name you mistyped.
`git rev-parse --verify <sha>` to check, `git rev-list --count <upstream>` to
see how deep you actually are.

## Recovering a finished rewrite

A completed rebase leaves the old commits reachable from the reflog for
`gc.reflogExpire` (90 days reachable, 30 days unreachable by default) — unless
gc has run and expired them.

```sh
git reflog                                   # find the "rebase (start)" entry
git reflog show <branch>                     # per-branch view
git branch recovered <sha>                   # or: git reset --hard <sha>
```

If the branch ref itself was deleted, its reflog is gone too, but `HEAD`'s is
not — the commits you made while checked out are still there:

```sh
git reflog show HEAD | head -20
git rev-list --walk-reflogs HEAD
```

Confirmed on git 2.55: after `git branch -D temp`, `git reflog show temp`
fails with `fatal: ambiguous argument 'temp'`, while
`git reflog show HEAD` still lists the deleted branch's commits under
`HEAD@{n}`. The loose reflog file `.git/logs/refs/heads/temp` is unlinked at
deletion; `HEAD`'s is not.

Last resort:

```sh
git fsck --lost-found
# dangling commit <sha>
ls .git/lost-found/commit/
git branch recovered <sha>
```

`git fsck` prints `dangling commit <sha>` for unreachable tips and writes
their SHAs into `.git/lost-found/commit/`. Dangling means "no ref points
here", not "corrupt" — it is the normal output after dropping a branch.

## Detached HEAD after a botched rebase

`## HEAD (no branch)` in `git status -b` means HEAD points at a commit with no
branch ref. Your work is safe; it just has no name.

```sh
git branch rescue-me       # name the current commit, then go back to work
git checkout main
```

## After a bad push

Someone else already fetched the bad history.

1. If nothing has been built on top: they should `git fetch` and
   `git reset --hard origin/<branch>`. One person re-resetting is a
   non-event.
2. If they have local commits: they rebase their local work onto the new
   tip. Old SHAs from the bad history are unrecoverable for them unless they
   still have them in their own reflog — which is why step 1 of any rewrite is
   to tell the team.
3. Never resolve this by force-pushing *again*. Announce, then let each person
   reset or rebase.

## State directory reference

| Path | What is in it |
|---|---|
| `.git/rebase-merge/` | an interactive or merge-backed rebase is in progress; `done` and `git-rebase-todo` are the running and remaining commands |
| `.git/rebase-apply/` | a patch-based (`--apply`) rebase, `rebase-merge` is used by `-m`/`--merge` and by `git am` |
| `.git/CHERRY_PICK_HEAD` | a cherry-pick or revert is in progress; `--abort` uses it |
| `.git/MERGE_HEAD` | a merge is in progress; `git merge --abort` |
| `.git/REVERT_HEAD` | a revert is in progress |
| `.git/logs/HEAD`, `.git/logs/refs/heads/<name>` | reflogs — the reason any of this is recoverable |

`git status` names the in-progress operation in its first line. `git revert
--quit` and `git cherry-pick --quit` drop the sequencer state without touching
HEAD or the working tree, which is the escape hatch when `--abort` would
discard a resolution you want to keep.
