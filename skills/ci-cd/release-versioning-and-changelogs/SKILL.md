---
name: release-versioning-and-changelogs
description: Version and release software that users can reason about - semantic versioning applied honestly, Conventional Commits, automated changelog and version-bump generation with changesets, release-please or git-cliff, tag and release automation, and keeping a CHANGELOG that people actually read. Use when cutting a release, when a version number is being chosen, when writing release notes, when setting up release automation, or when a changelog is noisy or missing breaking changes. Triggers on "semver", "release notes", "CHANGELOG.md", "conventional commits", "changesets", "release-please", "git-cliff", "bump version", "breaking change".
compatibility: Examples use changesets (JS/TS monorepos), release-please, git-cliff, and conventional-commitlint. Verify CLI flags and config keys with --help and the tool's docs for your version.
metadata:
  version: "1.0"
---

# Release Versioning and Changelogs

Two jobs: pick the version honestly, and write the note someone reads before
upgrading. Everything else is tooling.

## SemVer, applied honestly

`MAJOR.MINOR.PATCH`, with **one** rule that matters: MAJOR is for a **breaking
change to a documented, consumed contract** — an API another program calls, a
config key, a CLI flag, a database schema others depend on. MAJOR is **not** for
refactors, internal reorganisations, dropping an unused function, or changing
implementation details.

| Change | Version | Examples |
|---|---|---|
| Breaking public contract | MAJOR | Removing a REST endpoint; changing a function's signature callers rely on; changing a config key's meaning; dropping a supported runtime version |
| New backwards-compatible functionality | MINOR | New endpoint; new optional parameter; new feature flag |
| Bug fix with no contract change | PATCH | Wrong result; crash on an edge case; security fix that changes no API |
| Performance improvement, same contract | PATCH | Faster query, same output |
| Dependency bump with no behaviour change | PATCH | — |

Rules of thumb:

- **0.x versions:** in `0.y.z`, the minor acts as the major. `0.3.0 → 0.2.0` is
  breaking by convention. `1.0.0` is a promise of stability; do not use it
  casually, and once you do, honour it.
- **Deprecation is a MINOR, removal is a MAJOR.** Announce deprecation, keep it
  working for at least one release (longer if the consumers are external), then
  remove in a major. `Deprecation: true` and `Sunset: <date>` headers are the
  machine-readable version of this.
- **A public API you did not declare is still public** if someone uses it.
  Semantic versioning applies to observed behaviour, not to your intent.
- **Ship the version number and the changelog together.** A version with no
  note is the one people upgrade past.

## Conventional Commits: the input

Format: `type(scope): description`. Types: `feat`, `fix`, `docs`, `style`,
`refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`. Breaking changes
get a `!` after the scope and/or a `BREAKING CHANGE:` footer.

```
feat(checkout): add idempotency keys to the payment endpoint

fix(cart): recompute totals after a discount is applied, not before

feat(api)!: remove GET /v1/orders/{id}/lines

BREAKING CHANGE: the lines sub-resource is gone. Use GET /v1/orders/{id}
with ?expand=lines. Removed in 3.0.0; deprecated since 2.4.0.
```

Enforce it with commitlint in CI (`@commitlint/cli` + `@commitlint/config-conventional`).
Enforcement matters: the changelog generator can only be as good as the commit
messages, and a generator that silently drops `chore` commits is the desired
behaviour — do not disable linting and hand-edit the changelog.

Revert convention: `revert: <the reverted subject>` so tools can detect the
pair and the generator can show a re-entry rather than deleting history.

## Automated changelog generation

Pick one. House default depends on the project.

| Tool | Fits | Model |
|---|---|---|
| **changesets** | JS/TS monorepos, any language actually (Markdown files) | Contributors add a `.changeset/*.md` file per change; a version command consumes them and writes CHANGELOG + version + publish |
| **release-please** | Node and non-Node repos driven by Conventional Commits | A GitHub Action reads commit history since the last release, opens a release PR with the version bump + changelog, merges it on approval |
| **git-cliff** | Any language, Rust | Parses Conventional Commits into CHANGELOG.md; integrates with git-cliff-action for PR release notes |
| **conventional-changelog** (`standard-version`) | Node | Bumps `package.json`, tags, writes CHANGELOG from commits |
| **goreleaser** | Go and any language | Builds and signs artifacts, generates release notes, publishes to registries |
| **cargo-release / cargo-changelog** | Rust | — |
| **reno / towncrier** | Large projects with many small changes | One news fragment per change, assembled into release notes |

**changesets** (the default for a monorepo), how a change is proposed:

```markdown
---
"@acme/checkout": minor
"@acme/core": patch
---

Add an idempotency key to `POST /payments`. Retries with the same key return
the original result instead of charging twice.
```

```bash
npx changeset add          # interactive
npx changeset version      # consume: bump versions, update CHANGELOG, remove files
npx changeset publish      # npm publish
```

**release-please** (the default for a single-package repo), as a workflow:

```yaml
name: release-please
on:
  push:
    branches: [main]
permissions:
  contents: write
  pull-requests: write
jobs:
  release-please:
    runs-on: ubuntu-24.04
    steps:
      - uses: googleapis/release-please-action@<sha>   # pin; verify major version
        with:
          config-file: release-please-config.json
          token: ${{ secrets.GITHUB_TOKEN }}
# It opens a release PR (version bump + CHANGELOG). Merging it tags, publishes,
# and creates the GitHub Release. Nothing is published by this workflow directly.
```

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "packages": { ".": { "release-type": "node", "changelog-path": "CHANGELOG.md" } },
  "changelog-sections": [
    { "type": "feat", "section": "Features" },
    { "type": "fix", "section": "Bug fixes" },
    { "type": "perf", "section": "Performance" },
    { "type": "revert", "section": "Reverts" },
    { "type": "docs", "section": "Documentation", "hidden": true }
  ]
}
```

**git-cliff**, for a language-agnostic single-package repo:

```bash
git-cliff --tag v1.4.0 --output CHANGELOG.md          # regenerate for a tag
git-cliff --unreleased --strip header                # notes for a PR
```

```toml
# cliff.toml
[changelog]
header = "# Changelog\n\nAll notable changes to this project are documented here."
body = """
## [{{version}}] - {{date}}
{% for group, commits in commits | group_by(attribute="group") %}
### {{group | upper_first}}
{% for commit in commits %}- {{commit.message | upper_first}} ({{commit.id | truncate(length=7, end="")}})
{% endfor %}{% endfor %}
"""
trim = true
```

House rules for the config: **hide `chore`, `ci`, `build`, `test`, `style`** from
the user-facing changelog (they are real, just not news to users); **always
show** `feat`, `fix`, `perf`, and anything with `BREAKING CHANGE`.

## Keeping the changelog useful

The failure mode of generated changelogs is not missing entries — it is a wall
of 200 lines of `fix: fix typo` that nobody reads, so they do not read the one
line that says the database schema changed.

Make it useful by:

- **Grouping by type** (Features / Bug fixes / Breaking changes / Performance).
- **Putting breaking changes first**, always, in their own section, with the
  migration step spelled out. This is the only part of a changelog most readers
  will act on.
- **Filtering the noise** (`chore`, `ci`, `test`, `docs`, `style`, `refactor`
  with no user-visible effect) out of the user-facing file. Keep a full
  generated log in the release page if you want the completeness.
- **Writing the migration, not the diff.** "Renamed `oldName` to `newName`;
  `oldName` is ignored from 3.0.0" beats "refactor: rename field".
- **Highlighting security fixes** in their own section, with severity and CVE.
  Do not bury them in bug fixes; a patch release with a CVE must be findable.
- **Linking the PR/issue** per entry so a reader can check whether it affects
  them. Each entry is one line; the link is the detail.
- **Never regenerate history destructively.** Append a new section per release.
  If a tool rewrites the whole file, diff the result before committing.

## Tag and release automation

- **Sign tags.** `git tag -s v1.4.0 -m "v1.4.0"` (GPG) or an SSH signature
  (`git tag -s` with `gpg.format=ssh`). Verify with `git tag -v v1.4.0`. An
  unsigned tag on a public repo is forgeable.
- **Tag format: `v1.4.0`**, no build metadata in the tag name
  (`+build.5` breaks some tooling); put the build in the artifact name or an
  annotation.
- **The tag must point at a commit whose artifact is published and verified.**
  The house rule: the release pipeline builds the artifact *by digest*, and the
  tag is placed on that exact commit, not on a rebuild.
- **A release is: tag → GitHub Release (with notes from the changelog
  section) → publish artifacts → verify the install works.** Verify before
  announcing.
- **Do not delete or move a published tag.** If a release is bad, publish a new
  one. A yanked release gets a `YANKED` note in its release page; a rewritten
  tag destroys everyone's ability to verify what they installed.
- **Automate the mechanics, gate the decision.** release-please or a manual
  "prepare release PR" step; the human approves the version and the notes.

## Safety notes

- **Never publish to a public registry from an unreviewed commit.** A release
  is permanent: npm unpublish is heavily restricted, PyPI deletion is
  exceptional, container tags can be overwritten. The release PR is the gate.
- **Never put a secret, internal URL, or customer name in a changelog** — it is
  the most-read file in the repo and is often published to a public registry
  page and an RSS feed.
- **Never auto-bump a major version.** A `BREAKING CHANGE` footer should force a
  human to choose MAJOR, not have the tool decide.
- **Verify the artifact before announcing**: install the published package into
  a clean environment and run its entrypoint. A release that is published but
  broken is worse than a failed release.

## Gotchas

- **Conventional Commits are not enforced by most `git commit` setups** — commit
  templates and `commit-msg` hooks are bypassed with `--no-verify`, and merge
  commits ("Merge pull request #42") poison the log. Enforce in CI on the PR
  range (`commitlint --from origin/main --to HEAD`), and use squash merges with
  the PR title as the commit message.
- **`BREAKING CHANGE:` must be a footer**, on its own line, after a blank line
  following the body. A `BREAKING CHANGE:` inside a paragraph is not parsed.
- **The `!` marker and the footer are independent.** `feat(api)!: …` marks it;
  a generator that only reads footers will miss the `!` form. Use the footer
  for machine parsing and `!` for humans.
- **Type-only commits are not semantic.** `fix: stuff` produces a changelog line
  that means nothing. Insist on a scope and an imperative subject
  (`fix(cart): …`). Configure commitlint to enforce both.
- **Squash merges lose the individual commits**, so the changelog is one line
  per PR. That is usually what you want — but it means the PR title must be a
  good changelog line, which is another reason to enforce it.
- **`revert:` commits appear as new entries.** Keep the original entry and add
  the revert below it; do not delete history from the changelog.
- **Monorepo versioning:** independent (`@acme/checkout@1.2.0` bumps alone) vs
  fixed (everything bumps together). Independent is less painful; use
  `fixed` only when a change to one package always requires all others to move.
- **Prereleases** (`1.5.0-rc.1`) are semver-valid; a `prerelease` mode in the
  generator is separate from a `pre` branch. Decide whether prereleases publish
  to the public registry (usually they should not) and configure it explicitly.
- **A changelog that is generated but never reviewed** will ship a misleading
  entry. The release PR review is where the wording gets fixed — treat the
  changelog diff as part of the release, not as an artifact.
- **Changelog date format**: ISO 8601 (`2026-01-15`) sorts correctly and is
  unambiguous. "January 15th" does not.
