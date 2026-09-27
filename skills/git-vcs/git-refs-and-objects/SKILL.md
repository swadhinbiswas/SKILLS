---
name: git-refs-and-objects
description: Understand and manipulate git's plumbing - refs, the reflog, the object database, packed-refs, worktrees, gc and pruning - and recover branches, commits, and stashes that appear to be gone. Use when a branch or commit cannot be found, when someone asks about origin/main vs main, when a clone behaves oddly, when objects are lost or a clone is corrupt, or when a user mentions reflog, fsck, packed-refs, shallow clone, or git gc. Triggers on "recover deleted branch", "lost commit", "reflog", "fsck", "dangling commit", "shallow clone", "packed-refs", "worktree", "object not found", "origin/main vs main".
compatibility: Git 2.x. `git reflog expire`/`gc` pruning windows come from gc.reflogExpire and gc.reflogExpireUnreachable; verify current values with `git config --get`.
metadata:
  version: "1.0"
---

# Git Refs and Objects

Almost every "I lost my work in git" story has the same cause: the work was
only reachable through a ref, the ref moved, and nothing else pointed at the
old commits. The reflog is the safety net, and it works for longer than most
people think.

## The model in one paragraph

A **ref** is a file (or a line in `.git/packed-refs`) containing a SHA:
`refs/heads/main`, `refs/remotes/origin/main`, `refs/tags/v1.0`,
`refs/stash`. A **commit** is a content-addressed object containing a tree
SHA, parent SHAs, author, committer, and message. A **tree** holds blob SHAs.
A commit is *reachable* if some ref or reflog entry points at it or an
ancestor. `git gc` deletes anything unreachable, once the reflog has expired.
That is the whole system.

```sh
cat .git/HEAD                     # usually: ref: refs/heads/main
cat .git/refs/heads/main          # loose ref: a SHA
cat .git/packed-refs              # refs after `git gc` packs them
cat .git/logs/HEAD                # HEAD reflog
cat .git/logs/refs/heads/main     # per-branch reflog
```

Do not hand-edit these files; use `git update-ref` if you must set one
programmatically, and prefer the porcelain commands.

## Recovering something that looks lost

Run this first, always, before concluding anything is lost:

```sh
git reflog --all | head -40
git reflog show <branch>
git reflog show HEAD
```

The reflog is a list of where each ref has been. `git reflog show <sha>`
walks the history *of that commit's position*, which is how you find a commit
that was amended away.

Then restore it by name:

```sh
git branch recovered <sha>
git tag safe/<description> <sha>       # tag it so gc cannot take it
```

Verified on git 2.55: after `git branch -D temp`, the loose reflog file
`.git/logs/refs/heads/temp` is removed and `git reflog show temp` fails with
`fatal: ambiguous argument 'temp'`. **`HEAD`'s reflog still contains the
commits you made while that branch was checked out** — `HEAD@{1}` and friends.
`git rev-list --walk-reflogs HEAD` lists them. This is the most commonly missed
recovery, because people look for the branch ref and stop.

If the reflog has expired, the commits are dangling but usually still on disk:

```sh
git fsck --lost-found
# dangling commit 51bb12cb1c1379049020f004108492585f54ca63
ls .git/lost-found/commit/
git show 51bb12cb           # read the message, confirm it is the one
git branch recovered 51bb12cb
```

`dangling` means "reachable from nothing" — it is normal output, not damage.
`missing` or `broken link from` is damage (see "When the object store is
broken" below).

Dangling blobs are often exactly what you want when a commit was lost:
`git fsck --lost-found` lists them, and `git cat-file -p <blob-sha>` prints
the content.

Stashes live in `refs/stash` with their own reflog:

```sh
git stash list
git reflog show refs/stash
git stash show -p stash@{2}
```

## Why `origin/main` vs `main` matters

| Ref | Lives where | Moves when | Exists after `git clone` |
|---|---|---|---|
| `main` | `.git/refs/heads/main` | you commit, merge, rebase, reset | yes |
| `origin/main` | `.git/remotes/origin/main` | `git fetch` / `git pull` only | yes, as of the last fetch |
| `upstream/main` | `.git/remotes/upstream/main` | you fetch `upstream` | only if you added that remote |
| `refs/pull/123/head` | remote side | the PR updates | no (GitHub-specific) |

`origin/main` is a **cache of the last fetch**, not a live view. It is stale
the moment anyone else pushes, and it is stale forever if you never fetch.
`git log origin/main..HEAD` answers "what do I have that the remote had when
I last looked", which is the right question only right after a `git fetch`.

Consequences people get burned by:

- `git push` with no arguments pushes according to `push.default` (modern
  default `simple`), which refuses if the upstream branch name differs from
  the local one. Set `push.default = current` or always name the remote.
- `git status` reporting "Your branch is up to date with 'origin/main'" means
  up to date *with your last fetch*, which may be days old.
- `git fetch --prune` deletes remote-tracking refs for branches deleted on the
  remote. Without `--prune`, `origin/feature` lingers and `git branch -r`
  lies to you. Set `fetch.prune = true`.

`git ls-remote --heads origin` shows the truth on the remote without changing
anything.

## Tags vs branches

|  | Branches | Tags |
|---|---|---|
| Moves | yes, on merge/rebase/reset | only if forced (`git tag -f`) |
| Fetched by default | `refs/heads/*` maps to `refs/remotes/*` | only tags reachable from fetched branches |
| Meaning | a line of work | a point in history |
| Created | `git branch`, `git switch -c` | `git tag -a <name> -m "msg"` |

`git tag` without `-a` makes a **lightweight** tag: just a ref, no tag object,
no message, no tagger, no signature. If you want a release, an audit trail, or
a signature, you need `-a` (annotated). Signed tags are a separate flag; see
`git-signing-and-hooks`.

Annotated tags are what package managers and CI look for, and what
`git describe` reports. A lightweight tag cannot be signed at all.

Force-moving a published tag breaks everyone who pinned it. If you must,
delete and re-tag only when you know nobody has consumed it, and say so.

## Packed refs and object packing

After `git gc` or `git pack-refs`, loose ref files are removed and refs live in
`.git/packed-refs`:

```sh
cat .git/packed-refs
git for-each-ref --format='%(refname) %(objecttype) %(objectname:short)'
git show-ref                  # all refs including packed ones
git count-objects -vH         # loose/pack counts and size
```

Objects are stored either loose (`.git/objects/xx/yyyy…`, one file per
object) or packed into `.git/objects/pack/*.pack` with an index. Packs are
delta-compressed. `git cat-file -t <sha>`, `git cat-file -p <sha>`, and
`git count-objects -vH` are how you inspect the store.

## gc and pruning — what actually deletes your data

```sh
git gc                     # pack, prune unreachable, expire old reflogs
git gc --prune=now         # prune immediately, ignoring the default grace
git reflog expire --expire-unreachable=now --all
git prune                  # delete unreachable loose objects
git fsck --unreachable     # list them without deleting
```

Defaults: reachable reflog entries are kept ~90 days
(`gc.reflogExpire`), unreachable ones ~30 days
(`gc.reflogExpireUnreachable`); unreachable objects get a ~2 week grace
(`gc.pruneExpire`). `git gc` runs automatically in the background and is why
"it was there yesterday and not today" is sometimes true.

The practical consequences:

- The recovery window is bounded but generous. Do not assume "gc will never
  get it" — but also do not assume 2 weeks is left.
- Once a commit is unreachable and past the grace period, it is gone. Only a
  remote, another clone, or `refs/stash` can still have it.
- `git gc --prune=now` in a script that runs while a long operation is
  in progress can delete objects a detached HEAD is about to need. Avoid it.

Config worth knowing:

```sh
git config --get gc.auto            # default true
git config --get gc.reflogExpire
git config --get gc.reflogExpireUnreachable
git config --get gc.pruneExpire
git config --get core.logAllRefUpdates  # false disables reflogs: never do this
```

`core.logAllRefUpdates = false` in a non-bare repo turns off the reflog for
`HEAD` and refs under `refs/heads/`, and then the reflog safety net does not
exist. Some CI mirrors and bare repos do this deliberately.

## Worktrees

A worktree is a second working directory sharing one object database. Each has
its own `HEAD`, index, and per-worktree reflogs under
`.git/worktrees/<name>/`.

```sh
git worktree add ../hotfix -b hotfix/urgent main
git worktree add ../spike --detach        # detached, no branch
git worktree list
git worktree lock  --reason "on-call" ../hotfix   # refuse to prune it
git worktree move ../hotfix /tmp/hotfix
git worktree remove ../hotfix
git worktree prune -v                       # clean up dirs whose dir is gone
```

Worktree gotchas:

- **A branch can only be checked out in one worktree.** Git refuses with
  `fatal: '<branch>' is already checked out at ...` and offers
  `--force`. Do not force it; use a new branch or `--detach`.
- **`git branch -D` refuses to delete a branch checked out in a worktree**:
  `error: cannot delete branch 'x' used by worktree at '...'`. Verified on
  2.55. This is a feature.
- **`git worktree prune` will not remove a worktree whose directory still
  exists**, even if the administrative data is stale. `rm -rf` the directory
  by hand, then `git worktree prune -v`.
- **A worktree left on a deleted branch keeps its commits reachable** through
  that worktree's `HEAD` reflog — which is why a machine with three abandoned
  worktrees never gc's.
- All worktrees share `refs/`, so a `git gc` in any of them packs for all of
  them.

## Shallow and partial clones

```sh
git clone --depth 50 <url>            # shallow: truncated history
git fetch --unshallow                 # complete it
git fetch --depth 500                 # deepen
git clone --filter=blob:none <url>    # partial: blobs fetched on demand
git clone --single-branch <url>       # one branch, with --depth implies it
```

Shallow clones have a real `.git/shallow` file listing the boundary commits.
Everything past it does not exist, so:

- `git log`, `git bisect`, `git blame`, and `git describe` all stop at the
  boundary or fail with
  `fatal: bad revision 'HEAD~50'` / `fatal: no such object`.
- You **cannot bisect** a shallow clone without first unshallowing, or at
  least deepening past your `good` bound.
- `git fetch --unshallow` on a repository without a shallow boundary fails
  with `fatal: --unshallow on a complete repository does not make sense`.
- Shallow clones and `git push` are fine; pushing from a shallow clone to a
  server that lacks the history is rejected by most forges.

Partial (blobless/treeless) clones look complete to `git log` but fetch blobs
on demand, so `git show` of an old file may hit the network. Good for
monorepos, confusing while debugging. `git config --get remote.origin.promisor`
is non-empty when you have one.

Most CI systems use a shallow or partial clone by default, and that is the
usual reason a CI-only failure cannot be reproduced locally.

## When the object store is broken

```sh
git fsck                 # full check; `git fsck --full` skips defaults
git fsck --strict        # also checks reflogs and index
git count-objects -vH
```

| Output | Meaning |
|---|---|
| `dangling commit <sha>` | unreachable but intact — recoverable |
| `dangling blob <sha>` | unreachable file content — often the answer |
| `missing blob <sha>` | referenced but not present — the repo is damaged |
| `broken link from tree <sha>` | a tree points at a missing object |
| `error: inflate: data stream error` | packfile corruption |

Repair:

```sh
git fsck --lost-found       # rebuild lost-found links where possible
git gc --prune=now          # only after you have read --lost-found
git remote -v               # the fastest fix: re-clone and re-fetch
```

If objects are genuinely missing locally, the authoritative copy is the
remote. Re-clone (`git clone <url> newdir`) and copy over any local-only refs
by SHA before discarding the broken repo. Do not run `git gc` on a repo with
`missing` objects — it can delete the surviving parts of the structure.

## Practical diagnostics

```sh
git rev-parse --show-toplevel        # repo root
git rev-parse --git-dir             # .git, or .git/worktrees/<n> in a worktree
git rev-parse --git-common-dir      # the shared .git, even from a worktree
git rev-parse --is-shallow-repository
git rev-parse --verify <sha>        # exit 1 if the object does not exist
git cat-file -t <sha>               # commit/tree/blob/tag
git branch -vv                      # each branch, its upstream, ahead/behind
git remote show origin              # which refs the remote has
git for-each-ref --sort=-committerdate refs/heads/
```

`git branch -vv` is the fastest way to answer "why does my log not match
GitHub": it shows the upstream and the ahead/behind counts, and `[gone]` for
an upstream deleted on the remote.

## Gotchas

- **A ref is a SHA; a SHA is a name.** `HEAD~3`, `main@{yesterday}`,
  `:/message` are all reflog- and history-relative notations. `main@{1}` is
  the *previous* position of `main`, and it disappears once the reflog for
  that ref expires or the ref is deleted.
- **Reflog entries record ref movement, not file changes.** `git add` and
  `git commit` before a bad rebase leave uncommitted work recoverable only
  from the stash or the fsck dangling blobs — not from the reflog.
- **A detached HEAD is still reflog-protected** through `.git/logs/HEAD`, so
  a "lost" commit from a checkout of an old SHA is almost always
  recoverable. `git reflog --all` is the command you keep not running.
- **`git fsck` on a repository with an in-progress rebase reports spurious
  problems.** Finish or abort first.
- **Amending or rebasing a signed commit drops the signature.** See
  `git-history-surgery` and `git-signing-and-hooks`.
- **`git gc` is not a repair tool.** It is a garbage collector that will
  happily delete the dangling commit you were about to recover.
- **Force-pushing a deleted-then-recreated branch can resurrect a tag
  conflict**: `! [rejected] ... (would clobber existing tag)`. Tags are not
  overwritten by a force-push. Delete or move the tag explicitly.
