# CI enforcement for signed commits and hooks

Local hooks are advisory. CI is the enforcement point, because hooks are
skippable (`--no-verify`), are not installed on every clone, and do not run at
all for PRs from forks.

## Verify commits are signed and valid

The check needs full history. A shallow clone has nothing to verify and the
check either passes vacuously or fails on everything.

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0        # required: shallow clones have no signature history

- name: Require signed commits
  run: |
    git log --format='%H %G?' --not --remotes=origin \
      | awk '$2 != "G" { print "unsigned or unverifiable: " $1; bad=1 } END { exit bad }'
```

Rules that make this real rather than decorative:

- **`%G?` must be exactly `G`.** The other values are `B` (bad signature),
  `U` (good signature, unknown key), `X` (signature expired), `Y` (key
  expired), `R` (key revoked), `E` (cannot be checked — no public key
  available), `N` (no signature). Allowing `U` and `E` makes the check
  decorative: they mean "we could not verify this", not "verified".
- **Make the public keys available in CI.** For SSH signing, write an allowed
  signers file and point git at it:

  ```sh
  printf '%s %s\n' "$COMMITTER_EMAIL" "$SIGNING_KEY" > allowed_signers
  git config --global gpg.ssh.allowedSignersFile "$PWD/allowed_signers"
  ```

  For GPG, import the public key into the CI runner's keyring. Without this,
  every commit verifies as `E` and the check fails on the whole history.
- **Pin to a set of keys, not to "any key git can verify".** Verify against
  the fingerprints you authorise; see the trusted-signer options in
  `git verify-commit --help` for the current spelling.

## Run the hooks in CI

```yaml
- name: Run pre-commit
  run: pre-commit run --all-files --show-diff-on-failure
```

- `--all-files`, not just the files in the PR: the hook config is the
  contract, and a file that happens to be untouched today is still covered.
- `--show-diff-on-failure` turns "it failed" into a reviewable diff, which
  saves a round trip.
- Pin the framework version in the runner image or via a lockfile, so CI and
  developer machines run the same hook versions.

For a husky repo, CI runs the underlying tools directly
(`npx lint-staged` is not meaningful on a full tree) — run eslint, prettier,
and the type checker as separate steps with explicit file globs.

## Enforce DCO / sign-off on pull requests

Do it with the forge's DCO check rather than a `commit-msg` hook: it works
for PRs from forks, where no hook ever runs.

```sh
# local fallback, if you must
git log --format='%H %s%n%b' "$PR_RANGE" \
  | grep -E '^(Signed-off-by:)' >/dev/null \
  || { echo "missing Signed-off-by"; exit 1; }
```

## Retry policy

- **Never retry a whole job because a test failed.** That is flake-hiding; see
  `flaky-test-triage`.
- Retry only for **infrastructure** failure (runner lost, image pull failed,
  registry timeout), count those separately, and report the count. A build
  that is green because of retries has lost its signal.
- If you do use a test-level retry for triage, the job must **report** the
  retry, not just the final green.

## Emergency bypass

A bypass must be audited, not a repository setting:

- A label on the PR (e.g. `skip-required-signatures`) that the CI job honours,
  recorded in the audit log and visible in the merge decision.
- A required approval for that label.
- Never a `git config` toggle in the repo's CI config, and never
  `core.hooksPath = /dev/null` committed anywhere.

## What to report

Publish, per run, and per test or check:

- pass/fail, and the **attempt count** for anything retried
- a flake count: failures that passed on retry (this is the signal that must
  not be hidden)
- a link from each failure to the trace, log query, or diff that shows why

A dashboard of "checks that needed a retry" over time is the honest measure
of CI health. Green is not the measure; the retry count is.
