---
name: git-merge-conflicts
description: Resolve git merge, rebase, cherry-pick, and revert conflicts correctly - reading conflict markers, knowing what ours and theirs mean in each operation, using diff3, rerere, merge drivers, .gitattributes diff drivers, and handling rename, binary, submodule, and Git LFS conflicts. Use when git reports CONFLICT, when a merge leaves conflict markers, when git says "both modified" or "fix conflicts and then commit", or when a user asks how to resolve a conflict or what --ours and --theirs mean. Triggers on "merge conflict", "CONFLICT (content)", "<<<<<<<", "ours vs theirs", "rerere", "diff3", "binary conflict", "rename conflict", "conflict resolution".
compatibility: Requires git 2.x. rerere auto-resolution and checkout --conflict=diff3 need git 1.7+/2.x; subtree/rename detection thresholds are `diff.renames`/`merge.renameLimit` config.
metadata:
  version: "1.0"
---

# Git Merge Conflicts

A conflict is not a failure. It is git refusing to guess which of two
differing intentions wins. The job is to decide, and the whole skill is about
deciding correctly rather than picking a side and hoping.

## Workflow

- [ ] 1. Read the error — `CONFLICT (content)`, `(add/add)`, `(rename/delete)`,
      `(modify/delete)` and `(submodule)` each need a different response
- [ ] 2. Turn on `diff3` conflict style so you can see the base
- [ ] 3. For each file, understand *both* sides' intent, then write the merged
      result
- [ ] 4. `git add` the resolution, then finish with the operation's own
      continue/abort command
- [ ] 5. `git diff --check` and run the tests — a "resolved" merge that does
      not build is worse than an unresolved one
- [ ] 6. `git rerere` records the resolution so you never do it twice

## Read the conflict first

Always use `diff3` for anything non-trivial. It shows you the common ancestor,
which is the only way to tell an edit from a rewrite:

```sh
git config --global merge.conflictStyle diff3
```

Or, per file, without touching config:

```sh
git checkout --conflict=diff3 <path>
```

Result:

```
<<<<<<< ours
FEATURE
||||||| base
b
=======
MAIN
>>>>>>> theirs
```

- `<<<<<<< ours` … `||||||| base` … `=======` … `>>>>>>> theirs` — three
  versions. You can see whether one side *deleted* what the other *edited*.
- `merge.conflictStyle = zdiff3` (git 2.35+) additionally shows the base's
  *common* lines in place, which reads much closer to a normal diff. Prefer it
  if your git is new enough.
- The `ours`/`theirs` labels are **paths**, not people, and which is which
  depends on the operation. This is the single most-missed fact in conflict
  resolution:

| Operation | `--ours` / `HEAD` | `--theirs` / the other side |
|---|---|---|
| `git merge <branch>` | the current branch (usually `main`) | `<branch>` being merged in |
| `git rebase <upstream>` | `<upstream>` (the base you are replaying onto) | your commit being replayed |
| `git cherry-pick <sha>` | your current branch | `<sha>` |
| `git revert <sha>` | your current branch | the inverse of `<sha>` |

During a rebase, "ours" is the upstream you are moving onto — the opposite of
what most people assume. Verified: rebasing a feature branch that changes a
line to `FEATURE` onto a `main` that changes it to `MAIN` produces a conflict
where the `HEAD`/`ours` side is `MAIN`.

For anything where the labels are not what you expected, do not guess —
compare the blobs directly:

```sh
git show :1:<path>   # stage 1 = base (common ancestor)
git show :2:<path>   # stage 2 = ours
git show :3:<path>   # stage 3 = theirs
git ls-files -u      # all unmerged stages, with modes and blob SHAs
```

## Resolving a content conflict

Do not hand-edit blind. Start from the two sides and produce the answer:

```sh
# Three-way merge with the conflict visible
git merge-file -p --diff3 mine.txt base.txt theirs.txt > merged.txt

# Or re-run the merge with a different algorithm
git checkout --conflict=diff3 <path>
```

The algorithms, in the order worth trying:

| Strategy | Behaviour | Use when |
|---|---|---|
| `ort` (default since 2.34) | most accurate rename/rename detection | always, first try |
| `recursive` (pre-2.34 default) | older, fewer renames detected | legacy repos only |
| `-X ours` / `-X theirs` | resolve conflicting *hunks* by preferring one side | last resort, see below |
| `-s ours` | the merge resolves to our tree entirely, silently | "keep our version of this subtree" (e.g. keep our `vendor/`) |

`-X theirs` and `git checkout --theirs <path>` are **not** equivalent, and
neither is a merge tool.

- `git checkout --ours <path>` / `git checkout --theirs <path>` **replace the
  entire file** with that side's version. Every non-conflicting change in the
  other side's version is discarded, including changes git merged cleanly.
  Verified: if `main` has `MAINONLY-1`, `MAINONLY-2` added cleanly plus one
  conflicting line, `git checkout --ours` throws away all of it.
  This is the most destructive command in conflict resolution and it produces
  no warning.
- `git checkout -m <path>` re-derives the conflicted file with conflict
  markers from the index stages — the way to undo a bad `checkout --ours`
  when you have not staged anything yet.
- `git checkout --conflict=merge` / `--conflict=diff3` regenerate the markers
  from the index stages at any time during the conflict, which is the correct
  undo for a file you mangled while editing.

## When to abort

Abort early and often. A merge that is taking more than a few minutes to reason
about is usually the wrong approach, not a hard problem.

```sh
git merge --abort          # merge in progress
git rebase --abort         # rebase in progress
git cherry-pick --abort    # cherry-pick in progress
git revert --abort         # revert in progress
```

`--abort` restores the pre-operation branch pointer and working tree exactly.
It is always safe.

**Do not abort** when the operation is far along and you have good resolutions
already — that throws away real work. The alternatives:

- `git rebase --quit` / `git cherry-pick --quit` — drop the sequencer state,
  keep HEAD and the working tree as they are. This is the escape hatch for
  "I want to stop being in this state but keep my resolution".
- `git merge-file` to produce a candidate merge you can inspect before
  committing anything.

Never resolve a conflict by `git checkout --theirs .` on a repository you do
not understand. It will make the merge succeed and silently delete work.

## Conflict types other than content

| Message | Meaning | Correct response |
|---|---|---|
| `CONFLICT (content): Merge conflict in f` | same lines changed on both sides | resolve as above |
| `CONFLICT (add/add): Merge conflict in f` | both sides created `f` independently | almost always needs a human decision: they are two different files with one name. Pick one, rename the other, or merge the contents. |
| `CONFLICT (modify/delete): f deleted in HEAD, modified in topic` | one side deleted, the other edited | decide: was the edit after or before the deletion? If the other side's topic branch is newer, take the edit (`git checkout --theirs f` then `git add f`); if the deletion was intentional, `git rm f`. |
| `CONFLICT (rename/rename): f renamed to a in HEAD and to b in topic` | two different renames of the same file | keep both (`git add a b`), then reconcile manually. |
| `CONFLICT (rename/delete)` | renamed on one side, deleted on the other | same reasoning as modify/delete |
| `CONFLICT (submodule): Merge conflict in sub` | the submodule's gitlink differs | the conflict is in `.gitmodules`/the gitlink, not files. Check whether the submodule's history really contains the merge; if not, resolve the gitlink: `git -C sub fetch && git -C sub checkout <sha>` then `git add sub`. |
| `warning: Cannot merge binary files: logo.png` | git will not merge binaries | no conflict markers, but both sides changed. You must supply a merged artifact. No tool can do it for you. |

## Binary and large files

`git merge-file` and the text merge machinery are text-only. For images,
databases, notebooks, and archives:

- Resolve by choosing one side, then regenerating:
  `git checkout --ours logo.png` (or `--theirs`), replace the file with the
  correct merged artifact, `git add logo.png`.
- If the file is generated, regenerate it from source rather than merging the
  artifact, and consider adding a `.gitattributes` entry so future conflicts
  say so.
- Never set `merge=binary` on a file you expect people to hand-merge; it makes
  git keep both versions with no guidance and no error.

## Git LFS conflicts

LFS conflicts behave like binary conflicts (the pointer file is text, the
object is not), with one extra trap: after resolving, the object for the file
you kept must actually exist locally.

```sh
git lfs env                      # confirm LFS is installed and configured
git lfs pull                     # fetch the objects for the current checkout
git lfs status                   # shows pending objects
git lfs fsck                     # verifies object integrity
```

`git lfs status` reporting a pending object you did not expect means the
working-tree file is not the one the commit references. Re-pull before
committing the resolution. If `git lfs` is missing entirely, git will treat
LFS pointer files as ordinary text and you can "resolve" a conflict into two
pointer files — always verify `git lfs env` output before touching an LFS
conflict.

## rerere — never resolve the same conflict twice

`rerere` ("reuse recorded resolution") records how you resolved a conflict and
replays it the next time the same conflict appears, whether in a rebase, a
cherry-pick, or a different branch.

```sh
git config --global rerere.enabled true
git config --global rerere.autoupdate true   # stages the replayed resolution
```

During a conflict:

```sh
git rerere diff          # what is unresolved right now
git rerere               # records the resolution (after git add)
git rerere forget <path> # if you resolved it wrong and want to redo it
```

`rerere.autoupdate true` puts the replayed resolution in the index
automatically — convenient, but it means a replayed resolution can be staged
without you reading it. If you want to read every one, leave autoupdate off.
Storage is in `.git/rr-cache/`; commit that directory or `.git/rr-cache` to
`.gitignore` deliberately — it is per-clone and normally local.

Enable this repo-wide. It is the single highest-value git config for anyone
who rebases daily.

## Merge drivers and `.gitattributes`

`git merge-file` accepts a driver name; the driver determines the low-level
diff tool used to find changed regions. A custom driver is how a large
generated file (OpenAPI specs, protobuf descriptors, lockfiles) gets a
semantically-aware merge.

`.gitattributes`:

```
*.pb.go        diff=pb
*.svg  -diff    merge=binary
package-lock.json  linguist-generated=true
docs/**         -merge    # never auto-merge; always conflict
```

Configure the driver in `.git/config` or `~/.gitconfig`:

```ini
[merge "pb"]
	name = "protobuf merge driver"
	driver = protoc-merge %O %A %B %P %L
```

The driver receives `%O` (base), `%A` (ours, the file it must edit), `%B`
(theirs), `%P` (path) and `%L` (conflict-marker size). It must exit non-zero
on conflict. Git has built-in `binary`, `text`, `union`, `diff3` and `zdiff3`;
`union` concatenates both sides and is occasionally right for append-only
files.

`union` is worth knowing: for a file both sides appended to, it produces
both additions with no conflict — correct for log files and version files,
catastrophic for code. Set it per-path, never globally.

Diff drivers (`diff=<driver>`) affect `git diff` display and conflict region
detection, not the merge algorithm itself.

## Finishing the operation

```sh
git add <resolved paths>       # or `git add -A` only if you're sure
git diff --check              # whitespace errors and leftover markers
git status                    # "all conflicts fixed but you are still merging"
git merge --continue          # or commit; there is no --continue for merge
```

Verified: `git merge --continue` is **not a thing** —
`fatal: --continue expects no arguments` comes from
`git cherry-pick --continue` passed an argument. For a merge it is just
`git commit`, which opens the default merge message listing the conflicted
files. For rebase, cherry-pick, and revert, it is `--continue`.

Before you commit, check that no conflict markers survived into files you did
not stage:

```sh
git grep -n -e '^<<<<<<<' -e '^=======$' -e '^>>>>>>>' -- ':!*.md'
```

Note that `=======` alone is not proof — it appears in Markdown, in `rebase`
output, and in SQL. Look at the surrounding lines.

## Gotchas

- **`git checkout --ours/--theirs` replaces the whole file**, not just the
  conflicting hunk. This is the most common way a "resolved" merge silently
  deletes a hundred lines of unrelated work. Prefer editing by hand; if you
  must use it, diff the result against the merged version afterwards.
- **During a rebase, ours is the upstream.** `--ours` gives you the base you
  are moving onto, not your work. See the table above.
- **`git add` is what marks a conflict resolved**, not editing the file and
  not removing the markers. A file that is fixed but unstaged still shows as
  unmerged and `git commit` fails with
  `error: Committing is not possible because you have unmerged files.`
- **A resolved file that both sides deleted differently** will re-appear as
  a conflict on the next rebase unless `rerere` is on.
- **Rerere replays a resolution that may be wrong for the new context.** The
  same textual conflict can have a different correct answer on a different
  branch. Always read the replayed file.
- **Conflict marker size defaults to 7 lines.** Two edits within 7 lines of
  each other conflict even when they are logically independent. Raise
  `merge.conflictStyle` awareness first; if you genuinely need a smaller
  context, a custom merge driver is the supported mechanism, not a global
  marker-size change.
- **Submodule conflicts are gitlink conflicts.** "Fixing" them by
  `git add submodule` without checking out the intended submodule SHA just
  stages whatever happens to be checked out.
- **Resolving during a rebase and then running `git merge --abort`** does
  nothing — the operation is the rebase. Every `--abort` is
  operation-specific; read the hint git printed after the conflict.
- **`git checkout --conflict=diff3` regenerates from the index stages, so any
  hand-editing you have done is lost.** Copy your work elsewhere first if you
  have partially resolved.
- **A merge that "succeeds" with no markers can still be wrong** — git merged
  cleanly into something neither side wrote. Build and test.

## When a conflict means the change is wrong

Stop and reconsider when:

- The same file conflicts in nearly every commit of a rebase → the branch is
  too old; rebase onto `main` and redo the change on current code.
- Both sides changed a schema, a public API, or a migration in incompatible
  ways → one must change. Decide which, do it in a follow-up commit with a
  clear message, and do not paper over it in the conflict.
- You are about to run `-X ours` to get past more than a couple of files →
  you are choosing one branch's version of a file the other branch edited.
  Understand the edits first.
