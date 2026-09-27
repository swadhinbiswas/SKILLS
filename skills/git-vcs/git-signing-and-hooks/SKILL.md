---
name: git-signing-and-hooks
description: Sign commits and tags with GPG or SSH keys, set up pre-commit and commit-msg hooks that actually run (core.hooksPath, the pre-commit framework, husky for JavaScript repos), bypass them safely, and enforce signed commits in CI. Use when a user wants verified commits, hits "gpg failed to sign the data" or "failed to write commit object", when a hook is silently not running, or when a team needs hook and signature policy. Triggers on "sign commits", "GPG signing", "SSH signing", "signed-off-by", "DCO", "pre-commit hooks", "commit-msg lint", "husky", "core.hooksPath", "bypass hooks", "verify-commit", "--no-verify".
compatibility: SSH signing needs git 2.34+; gpg.format=ssh and signable git-allowed programs need 2.34+. Pre-commit framework requires Python 3; husky requires Node and a package.json.
metadata:
  version: "1.0"
---

# Git Signing and Hooks

Hooks that nobody notices are worse than no hooks, and signing that fails at
the worst moment is worse than unsigned commits. Both are configuration
problems with a known correct order.

## Signing

Three mechanisms. Pick one per repository; do not mix.

| | GPG (`gpg.format = openpgp`) | SSH (`gpg.format = ssh`) | Keyless |
|---|---|---|---|
| Needs | a GPG keypair in the secret keyring | an SSH key pair | a hosted signing service (SSH CA) |
| Config | `user.signingkey` = key id or fingerprint | `user.signingkey` = **path to the public key** or `key:user@host` | `user.signingkey` = `key:user@host` with SSH CA |
| Also configure | `gpg.program` if not `gpg` on PATH | nothing extra | `gpg.ssh.allowedSignersFile` for verification |
| Weakness | key expires, subkeys, gpg-agent on a headless box | no expiry mechanism; rotate manually | needs a hosted service |
| Best for | a workstation, an individual with a hardware token | laptops, CI, containers, teams that already use SSH keys | organisations with a shared CA |

**Default to SSH signing.** It needs no gpg-agent, no passphrase prompt in
automation, no secret-keyring setup, and `git verify-commit` works the same
way. GPG is the right answer when you already have a working GPG setup or a
YubiKey.

### SSH signing

```sh
ssh-keygen -t ed25519 -C "you@example.com"          # or reuse ~/.ssh/id_ed25519.pub
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
git config --global tag.gpgsign true
```

The signing key is a **file path to the public key**, not a `user@host` pair.
Using `user@host` requires SSH CA (keyless) support, where git asks
`ssh-keygen -Y sign` for a certificate from a CA configured in `~/.ssh/config`
— check the current key names in `git help config` rather than guessing.

Verify it works, from a clean shell:

```sh
git commit --allow-empty -m "signing test"
git log -1 --show-signature
git verify-commit HEAD
```

Verified failure modes:

```
error: Couldn't load public key SSH test@example.com: No such file or directory?
fatal: failed to write commit object
```

That is the `user.signingkey` set to a `user@host` value with no SSH CA
configured. Either give a path to the `.pub` file, or configure the CA.

Another one you will hit on a fresh machine or in CI:

```
gpg: signing failed: Inappropriate ioctl for device
gpg: signing failed: No pinentry
error: gpg failed to sign the data
```

gpg-agent cannot ask for the passphrase because there is no TTY. Fix it
properly: `export GPG_TTY=$(tty)` plus a real agent, or use a key with no
passphrase protected by filesystem permissions, or switch to SSH signing. Do
not "fix" it by disabling signing for that machine.

### GPG signing

```sh
gpg --list-secret-keys --keyid-format=long     # find the key
git config --global gpg.format openpgp         # implicit default, but be explicit
git config --global user.signingkey <KEYID>    # or the full fingerprint
git config --global commit.gpgsign true
git config --global tag.gpgsign true
```

| Error | Cause | Fix |
|---|---|---|
| `gpg: command not found` / `error: gpg failed to sign the data` | `gpg` not on PATH, or `gpg.program` wrong | `git config --global gpg.program /usr/local/bin/gpg2`; verify with `which gpg` |
| `gpg: signing failed: No pinentry` | headless, or no TTY | `export GPG_TTY=$(tty)` under a terminal; in CI use SSH signing or import a CI-provided key |
| `gpg: skipped "name": No secret key` | key is in a different keyring, or expired | `user.signingkey` must match the secret key you actually have |
| `error: failed to write commit object` (SSH) | bad `user.signingkey` value | use a `.pub` path, not `user@host` |
| `gpg: WARNING: unsafe permissions on homedir` | `~/.gnupg` group/world writable | `chmod 700 ~/.gnupg` |

### Checking and verifying

```sh
git log --format='%h %G? %GK %GS'          # %G? is the verification status
git verify-commit HEAD
git verify-tag v1.2.0
git verify-commit --raw HEAD               # machine-readable
```

`%G?` values (from `git log --pretty=format` documentation): `G` good, `B`
bad, `U` good but unknown key, `X` good but the signature has expired, `Y`
good but the key has expired, `R` good but the key has been revoked, `E`
cannot be checked (missing key), `N` no signature. A CI check must require
`G`; `U` and `E` mean "could not verify", not "verified". See
`references/ci-enforcement.md`.

### Lightweight vs annotated tags

```sh
git tag v1.0.0            # lightweight: no tag object, cannot be signed
git tag -a v1.0.0 -m "v1.0.0"
git tag -s v1.0.0 -m "v1.0.0"      # signed annotated tag
git tag -v v1.0.0                  # verify
```

`tag.gpgsign true` makes annotated tags signed by default. A lightweight tag
is just a ref and has no signature field at all — `git verify-tag` on one
reports `error: no signature found`.

## Hooks

### The first question: is the hook even running?

```sh
git config --get core.hooksPath        # empty means .git/hooks
ls -l .git/hooks/ | grep -v '\.sample'
git config --show-origin --get core.hooksPath
```

A hook that is not firing is almost always one of:

1. **`core.hooksPath` is set** (globally, or by a framework such as pre-commit
   or husky, which point it at `.husky` or `.git/hooks`). The *real* hooks
   are wherever this points, not `.git/hooks`.
2. **The file is not executable** (`chmod +x`).
3. **It has a CRLF line ending**, so the shebang is not the first bytes and
   the shell tries to run the whole file. Common on Windows checkouts.
4. **The file name is wrong** — `pre-commit` and `pre-commit.sample` are
   different files; git only runs the exact name.
5. **`core.hooksPath` is relative**, which resolves relative to the
   *worktree* — so a second worktree or a submodule can see a different path.

Run the hook by hand to see what it does: `bash -x .git/hooks/pre-commit`.

### What each hook does, in order

| Hook | Runs | Sees | Typical use |
|---|---|---|---|
| `pre-commit` | before the commit is created, can skip it | index staged, working tree | formatters, linters, secret scanning |
| `prepare-commit-msg` | after the default message, before the editor | the message file, source, SHA | add trailers, template the subject |
| `commit-msg` | after the message is written, can skip it | the message file | subject/Conventional Commits lint, DCO |
| `post-commit` | after a successful commit | nothing | notify, update local metadata |
| `pre-push` | before a push, can skip it | stdin list of refs to push | run the full test suite |
| `pre-rebase` | before a rebase, can skip it | the upstream | check the working tree |
| `pre-merge-commit` | for `git merge` that will make a commit | — | lint staged files |
| `post-merge` | after a merge that did not auto-commit | `$1` = squash flag | reinstall dependencies after a `package-lock.json` merge |
| `post-checkout` | after a checkout/branch switch | old HEAD, new HEAD, flag | clear caches, rebuild |

Two that are routinely misused:

- `pre-commit` runs with the **staged** content, not the working tree. That
  is what you want (only commit what passes) but it means a formatter that
  rewrites files and does not re-stage them is a no-op with a confusing
  message.
- `pre-push` receives the ref list on **stdin**, and rejecting with a non-zero
  exit blocks the push.

### The default: the pre-commit framework

Use `pre-commit` (https://pre-commit.com) for any language. It pins tool
*versions* in config, so every developer and CI gets the same formatter
version, and it manages its own hook installation via `core.hooksPath`.

```sh
pipx install pre-commit        # or: python3 -m pip install --user pre-commit
pre-commit init               # writes .pre-commit-config.yaml, installs the hook
pre-commit run --all-files    # the check CI should run
pre-commit autoupdate
```

`.pre-commit-config.yaml` — the shape:

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-merge-conflict
      - id: check-yaml
      - id: detect-private-key

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

Notes that decide whether it works:

- `rev` must be an exact tag or SHA. A branch name is unpinned and breaks CI
  when upstream moves.
- The first run of any hook downloads and builds it; do that once per
  developer, not in CI's inner loop. `pre-commit install --install-hooks`
  warms the cache.
- Language hooks that need a runtime must run **inside** a container hook
  (`language: docker` / `language: conda`) or on the host with the right
  version pinned. Node hooks that shell out to `npx` are the usual CI failure.
- `--all-files` in CI, not just staged files.
- `SKIP=<hook-id> git commit` skips a specific hook.

### JavaScript repos: husky

husky installs its own `core.hooksPath` (`.husky`) and gives you
lint-staged, which is what you want for per-file formatters.

```sh
npm install --save-dev husky
npx husky init
```

`npx husky init` creates `.husky/pre-commit` and sets `core.hooksPath` to
`.husky`. Since husky v9 the hook file has no shebang and no boilerplate, just
the commands. On husky v8 it must start with `#!/usr/bin/env sh` and
`. "$(dirname "$0")/_/husky.sh"`. Check with `npx husky --version` rather
than copying old snippets.

```sh
# .husky/pre-commit
npx lint-staged
```

Put the config in `.lintstagedrc.json` (or under a `"lint-staged"` key in
`package.json` — not both):

```json
{ "*.{js,ts,tsx}": ["eslint --fix", "prettier --write"] }
```

Install husky for every clone: `"prepare": "husky"` in `package.json`, so
`npm install` wires the hooks up. Forgetting that is the number one reason
husky "does not run" for a new teammate.

### Hand-written commit-msg hook

```sh
#!/usr/bin/env bash
# .git/hooks/commit-msg  (executable). Requires a subject matching Conventional
# Commits and a body explaining why, not just what.
set -euo pipefail
msg_file="$1"
subject="$(head -1 "$msg_file")"

if ! printf '%s' "$subject" | grep -qE '^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\(.+\))?: .{1,72}$'; then
  echo "commit-msg: subject must be Conventional Commits, e.g. 'fix(checkout): round total'" >&2
  echo "commit-msg: got: $subject" >&2
  exit 1
fi

[ "$(wc -l < "$msg_file")" -ge 2 ] || {
  echo "commit-msg: add a body explaining why, not just what" >&2
  exit 1
}
```

Test a hook before relying on it:

```sh
git commit --allow-empty -m "bad subject"              # must fail
git commit --allow-empty -m "fix(checkout): round total"   # must pass
```

`commit-msg` receives the path to the message file, **not** the message.
Read it, do not echo into it.

### DCO / Signed-off-by

If the project requires a DCO sign-off:

```sh
# .git/hooks/commit-msg
grep -qE '^Signed-off-by: .+ <.+>$' "$1" || {
  echo "commit-msg: add Signed-off-by: Name <email>" >&2
  exit 1
}
```

Then have git add it automatically: `git config --global commit.signoff true`
(per repo: `git config commit.signoff true`). `git commit -s` for individual
commits, `git rebase --signoff` to add it across a rebase's commits, and
`git cherry-pick -s` when picking someone else's fix.

## Bypassing hooks

```sh
git commit --no-verify          # skips pre-commit and commit-msg
git commit -n                  # alias for the above (do not: unreadable)
git push --no-verify           # skips pre-push
```

`--no-verify` skips the hooks, **not** the signature. Signing still happens and
still fails if it cannot.

When bypassing is acceptable: an emergency fix, a fix to a hook or formatter
itself, a work-in-progress commit on a private branch, and a bisect
(`git bisect run` will otherwise be stopped by a slow hook). Anything on a
shared branch needs a human decision.

Setting `core.hooksPath` to a path that does not exist disables *all* hooks
for the repo — `git config core.hooksPath /dev/null`, undone with
`git config --unset core.hooksPath`. Useful in a throwaway container or a CI
step that must not be blocked; not a thing to commit to a workstation's
`.gitconfig`.

## CI enforcement

Hooks are a convenience; CI is the enforcement point. Read
`references/ci-enforcement.md` when you are wiring this up — it has the
`%G?` verification check, the allowed-signers setup for SSH signing in CI,
retry policy, and the audited-bypass rule. The short version:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0        # required: shallow clones have no signature history
- name: Require signed commits
  run: |
    git log --format='%H %G?' --not --remotes=origin \
      | awk '$2 != "G" { print "unsigned or unverifiable: " $1; bad=1 } END { exit bad }'
- name: Run pre-commit
  run: pre-commit run --all-files --show-diff-on-failure
```

Three points that decide whether this is real: `fetch-depth: 0`, because a
shallow clone has no history to verify; `%G?` must be `G`, because `U` and
`E` mean "could not verify"; and the signing keys must be available in the
runner, or every commit fails. Enforce DCO with the forge's check, not a
`commit-msg` hook, so it works on PRs from forks.

## Gotchas

- **Signing rewrites nothing and changes nothing about content** — but
  amending, rebasing, or squashing a signed commit *replaces* the signature,
  and you must sign again. A squashed commit can only carry one signature.
- **SSH signing with an ed25519 key that is also your login key** means your
  *authentication* key is a *signing* key. Prefer a separate
  `~/.ssh/id_ed25519_signing` if your organisation's threat model cares.
- **`core.hooksPath` set by a framework overrides `.git/hooks` entirely.**
  Two frameworks fighting over it is a real and confusing failure: whichever
  ran `install` last wins.
- **Hooks are not cloned.** A hook in `.git/hooks` is local; only a framework
  that installs itself into a tracked directory (`.husky/`, via
  `core.hooksPath`) or a `prepare` script will run for other people.
- **A `pre-commit` hook that mutates files and exits 0 without re-staging
  them** lets the unformatted version through. Either `git add` the result or
  exit non-zero.
- **`pre-commit` runs on `git commit` even for `--amend`** and for `git merge`
  that creates a commit. A slow hook makes every commit slow.
- **`commit-msg` gets a file path; `prepare-commit-msg` gets file path, source
  (`message`, `template`, `merge`, `squash`, or `commit`), and the SHA.**
  Reading `$1` as the message is the classic bug.
- **`git commit --dry-run` does not run hooks.** Use it to inspect staging,
  not to test a hook.
- **A failed signature is a hard error**, not a warning: `git commit` will not
  silently create an unsigned commit. A long `git rebase` or
  `git filter-branch` fails on the first commit it cannot sign — with a
  passphrase, you get one prompt per commit unless an agent is set up.
- **`git verify-commit` needs the public key available locally.** In CI that
  means importing the key or configuring `gpg.ssh.allowedSignersFile` (one
  `email key-id` per line) for SSH signatures; without it everything verifies
  as `E` and the CI check fails on every commit.
- **A `post-checkout` hook that reinstalls dependencies** makes every
  `git bisect` step slow. Disable it locally while bisecting.

## Setup checklist

- [ ] One signing mechanism chosen repo-wide, documented in `CONTRIBUTING.md`
- [ ] `commit.gpgsign` and `tag.gpgsign` set, annotated tags only
- [ ] `git verify-commit HEAD` passes from a clean shell with no prompts
- [ ] `core.hooksPath` documented if a framework owns it
- [ ] Hooks executable, LF line endings, named exactly `pre-commit` /
      `commit-msg`
- [ ] A framework that pins versions (`pre-commit`) or a `prepare` script that
      installs hooks on clone (husky)
- [ ] CI runs the same checks with full history, independent of local hooks
- [ ] A documented, audited path for emergency bypasses (see
      `references/ci-enforcement.md`)
