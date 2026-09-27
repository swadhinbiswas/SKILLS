# Secure workflow YAML

Annotated, complete examples. Replace every `<sha>` with a real 40-character
commit (resolve with `gh api repos/OWNER/REPO/git/ref/tags/vX.Y.Z --jq
'.object.sha'`). Never run a workflow with a placeholder still in it.

## Hardened PR pipeline (unprivileged)

Runs on every PR including forks. Read-only token, no secrets, no untrusted
input in a `run:` line, and it publishes only metadata — never a build artifact
that a privileged job will execute.

```yaml
name: pr
on:
  pull_request:            # NOT pull_request_target: PR code must not see secrets
  workflow_dispatch:

permissions:
  contents: read           # the floor: every job gets this, nothing more
  # No id-token, no packages, no pull-requests write.

jobs:
  static:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@<sha>          # pinned
        with: { persist-credentials: false }  # do not leave the token in .git/config
      - uses: actions/setup-node@<sha>
        with: { node-version: '22', cache: 'npm' }
      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck

  test:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@<sha>
        with: { persist-credentials: false }
      - run: npm ci
      - run: npm test
      # Attach to the PR rather than posting a comment: needs no write token.
      - uses: actions/upload-artifact@<sha>
        if: failure()
        with: { name: test-results-${{ github.event.pull_request.number || 'push' }},
                path: test-results/, retention-days: 7 }

  title-check:
    # Shows the injection fix in full: event data via env, never via ${{ }} in run.
    runs-on: ubuntu-24.04
    permissions: {}
    steps:
      - env:
          PR_TITLE: ${{ github.event.pull_request.title }}
          PR_AUTHOR: ${{ github.event.pull_request.user.login }}
        run: |
          set -euo pipefail
          printf 'title=%s\nauthor=%s\n' "$PR_TITLE" "$PR_AUTHOR"
          if ! grep -Eq '^(feat|fix|docs|refactor|perf|test|build|ci|chore)(\(.+\))?!?: .+' <<<"$PR_TITLE"; then
            echo "::error::PR title must follow Conventional Commits"
            exit 1
          fi
```

`permissions: {}` on a job that only reads event data is the tightest correct
setting. `persist-credentials: false` stops the checkout action leaving a
usable token in the local git config for later steps to exfiltrate.

## Privileged post-merge deploy (OIDC, no stored cloud secrets)

Runs only on the protected default branch, gated by an environment with
required reviewers. Builds and deploys by digest; no PR event data is involved
anywhere.

```yaml
name: deploy
on:
  push:
    branches: [main]        # protected branch: only trusted code gets here
  workflow_dispatch:

# Tight top-level floor; the deploy job re-declares what it needs.
permissions:
  contents: read

concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: false   # never cancel a deploy midway

jobs:
  deploy:
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    environment:
      name: production
      url: https://app.example.com
    permissions:
      contents: read
      id-token: write          # OIDC: no long-lived cloud secret exists
    steps:
      - uses: actions/checkout@<sha>
        with: { persist-credentials: false }

      - id: aws
        uses: aws-actions/configure-aws-credentials@<sha>
        with:
          role-to-assume: arn:aws:iam::123456789012:role/gha-deploy
          aws-region: eu-west-1
          role-session-name: deploy-${{ github.run_id }}

      - name: Build image
        run: |
          set -euo pipefail
          docker buildx build \
            --tag ghcr.io/${{ github.repository }}:${{ github.sha }} \
            --push .
          digest=$(crane digest ghcr.io/${{ github.repository }}:${{ github.sha }})
          echo "digest=$digest" >> "$GITHUB_OUTPUT"
          echo "Built $digest"

      - name: Deploy by digest, then verify
        env:
          IMAGE: ghcr.io/${{ github.repository }}@${{ steps.aws.outputs.digest }}
          # CLI flags vary by deploy tool: check `./deploy.sh --help`.
        run: |
          set -euo pipefail
          ./deploy.sh --image "$IMAGE" --wait --timeout 300s
          ./verify.sh --image "$IMAGE"    # smoke test the live service
```

Cloud-side trust policy (the half people skip — without this, any repo in the
org can assume the role):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": {
        "token.actions.githubusercontent.com:sub":
          "repo:myorg/myrepo:ref:refs/heads/main"
      }
    }
  }]
}
```

`sub` must pin repo *and* ref (or the environment's
`environment:production` subject). `"repo:myorg/*"` is not a restriction.

## Unprivileged build → privileged deploy across two workflows

The correct way to deploy something built from a fork's PR without giving that
PR access to production. The trusted workflow verifies the artifact before
running it.

### Workflow 1 (runs on the PR, no secrets)

```yaml
name: build-pr
on: pull_request
permissions:
  contents: read
  id-token: write          # only to push to a *quarantine* registry namespace
jobs:
  build:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@<sha>
      - uses: aws-actions/configure-aws-credentials@<sha>
        with:
          role-to-assume: arn:aws:iam::123456789012:role/gha-pr-build   # push only to pr-quarantine/*
          aws-region: eu-west-1
      - run: |
          set -euo pipefail
          # Tag into a namespace that no deploy job ever pulls from.
          docker buildx build --push \
            --tag 123456789012.dkr.ecr.eu-west-1.amazonaws.com/pr-quarantine/pr-${{ github.event.pull_request.number }}:${{ github.sha }} .
      - uses: actions/upload-artifact@<sha>
        with: { name: image-digest, path: digest.txt, retention-days: 3 }
        # The only file crossing the trust boundary is a digest string.
        run: echo "$IMAGE_DIGEST" > digest.txt
```

### Workflow 2 (privileged; triggers on the first workflow's completion)

```yaml
name: deploy-pr
on:
  workflow_run:
    workflows: [build-pr]
    types: [completed]
permissions:
  contents: read
jobs:
  verify-and-deploy:
    # Only successful, non-PR-triggered-of-a-fork? No: PR builds are expected.
    # The trust comes from rebuilding in THIS context, not from the artifact.
    if: >-
      github.event.workflow_run.event == 'pull_request' &&
      github.event.workflow_run.conclusion == 'success'
    runs-on: ubuntu-24.04
    environment: { name: staging }
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@<sha>
        with:
          # BASE branch, never the PR head: the code you run is trusted code.
          ref: ${{ github.event.workflow_run.head_branch }}
      - uses: aws-actions/configure-aws-credentials@<sha>
        with:
          role-to-assume: arn:aws:iam::123456789012:role/gha-deploy
          aws-region: eu-west-1
      - name: Deploy the exact digest the PR build produced (no rebuild)
        run: |
          set -euo pipefail
          digest=$(cat digest.txt)   # artifact from the untrusted run: treat as data
          case "$digest" in
            sha256:*) ;;
            *) echo "::error::malformed digest"; exit 1 ;;
          esac
          ./deploy.sh --staging --image "123456789012.dkr.ecr.eu-west-1.amazonaws.com/pr-quarantine/pr-X@$digest"
```

The distinction that matters: the **artifact is data, not code**. A digest is
opaque and cannot execute. Do not download the PR's tarball and run it here; if
you need the image itself, either rebuild in the trusted context or verify a
signature produced by a trusted builder.

## Anti-patterns to grep for

| Pattern | Why it is wrong |
|---|---|
| `on: pull_request_target` + `ref: ${{ ...head.sha }}` + `run:` | PR code executes with repo secrets |
| `run: ... ${{ github.event.*.title\|body\|head.ref }}` | Shell injection from attacker-controlled text |
| `uses: some/action@v1` (tag, not SHA) | Mutable third-party code with your token |
| `permissions: write-all` | Every job gets write to everything |
| `actions: write`, `pull-requests: write` on a job that only tests | Unnecessary escalation surface |
| `secrets: inherit` on a job running external code | Leaks every repo secret to third-party actions |
| Self-hosted runner + `pull_request` from a fork | RCE on your network |
| `docker build` then push to the deploy registry on a PR | Publishes attacker-controlled image where prod pulls |
| `if: always()` step running `rm -rf ${{ github.event.* }}` | Injection again, in a step that runs on failure too |
| A composite action (`./.github/actions/x`) interpolating event data into `run:` | Same injection, inside a "trusted" local action |
| `ghcr.io/...:latest` in a deploy step | Rollback is not expressible; deploy by digest |

A quick local sweep for the two most common findings:

```bash
grep -rn 'run:.*\${{' .github/workflows/ | grep -v 'env:'   # candidates for injection
grep -rn 'uses:.*@\(v\|main\|master\)' .github/workflows/     # unpinned actions
```
