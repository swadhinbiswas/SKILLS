# Tooling and per-ecosystem recipes

Command lines and configs for making a specific build reproducible, and the
workflow for chasing a build that is not. Verify flags with `--help` for the
version you have installed.

## Pinning inputs, per ecosystem

| Ecosystem | Lockfile | Exact install | Content verification |
|---|---|---|---|
| Node (npm) | `package-lock.json` | `npm ci` | Registry integrity hashes in the lockfile; for full control, a private registry |
| Node (pnpm) | `pnpm-lock.yaml` | `pnpm install --frozen-lockfile` | Same |
| Node (yarn berry) | `yarn.lock` | `yarn install --immutable` | Checksums in the lockfile |
| Python (pip) | `requirements.txt` with `--hash=` lines | `pip install --require-hashes -r requirements.txt` | Hashes block a re-uploaded wheel |
| Python (uv) | `uv.lock` | `uv sync --frozen` | Hashes in the lock |
| Python (poetry) | `poetry.lock` | `poetry install --sync` | Lock carries hashes |
| Rust | `Cargo.lock` | `cargo build --locked` | Checksums per crate |
| Go | `go.sum` | `go mod download` / `go build -mod=readonly` | go.sum verifies every module |
| Java (Maven) | `pom.xml` with `<dependencyManagement>` | `mvn -B -ntp dependency:go-offline verify` | `_remote.repositories` / checksum policy in `settings.xml` |
| Java (Gradle) | `gradle.lockfile` (dependency locking on) | `./gradlew --write-locks` once, then CI with locking | `gradle/verification-metadata.xml` for checksum verification |
| .NET | `packages.lock.json` | `dotnet restore --locked-mode` | NuGet lock |
| Ruby | `Gemfile.lock` | `bundle install --frozen` (`BUNDLE_FROZEN=true`) | Checksums in the lock |

Dependency **locking** (Gradle, Maven) and **verification** (Gradle
`verification-metadata.xml`, Maven `checksumPolicy=fail`) are separate
features. Locking pins versions; verification pins content. You want both.

## Determinism flags for archive and artifact formats

```bash
export SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)

# tar: the two flags that matter are entry order and mtime
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 \
    --numeric-owner --format=gnu -cf app.tar dist/

# gzip: reads SOURCE_DATE_EPOCH for the header mtime, so the tar above is
# enough; pin the gzip binary too if byte-identity matters across images.
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" -cf - dist/ | gzip -n -9 > app.tar.gz
#   -n omits the original name and timestamp from the gzip header.

# zip: write it yourself with a fixed date_time per entry
python3 - <<'PY'
import zipfile, pathlib
with zipfile.ZipFile("app.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(pathlib.Path("dist").rglob("*")):
        if p.is_file():
            zi = zipfile.ZipInfo(str(p.relative_to("dist")), date_time=(1980, 1, 1, 0, 0, 0))
            zi.external_attr = 0o644 << 16
            z.writestr(zi, p.read_bytes())
PY

# Go: strip the build id and the build path from the binary
CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -buildid=" -o app ./cmd/app

# Rust: strip paths and set a deterministic build id
#   RUSTFLAGS="-C strip=symbols" and, in .cargo/config.toml,
#   [build] rustflags = ["-C", "debuginfo=0"]
#   Set CARGO_BUILD_RUSTFLAGS / --remap-path-prefix to remove the checkout path.

# clang/gcc: remove the build directory from __FILE__ and debug info
#   -ffile-prefix-map="$PWD"=/build  -fdebug-prefix-map="$PWD"=/build  -g0

# npm: the package tarball embeds mtimes; pack from a clean tree
npm pack --json          # inspect; the tarball content must be deterministic

# Debian/RPM packages: strip build time and host from the metadata
#   (dpkg-deb / rpm support a SOURCE_DATE_EPOCH-aware build; check the docs for
#   your packaging tool rather than trusting the changelog date field)
```

## Hermeticity: container and Nix

### Dockerfile checklist for determinism

- Base image `FROM image@sha256:<digest>` (not a tag).
- Set `ARG SOURCE_DATE_EPOCH` and `ENV TZ=UTC LC_ALL=C`.
- `COPY` the lockfile and install before the source, so the install layer is
  reused.
- Compile with `-trimpath` / `-ffile-prefix-map` / `--remap-path-prefix`.
- Run as a non-root `USER` (also a security requirement, unrelated).
- A multi-stage build keeps the build toolchain out of the final image, which
  also makes the final image's contents independent of the builder's version.

```bash
# Build twice and compare. Note --no-cache the second time, and use two
# different base tags (both pinned to the same digest) to prove host independence.
docker buildx build --no-cache -t app:a --provenance=false .
docker buildx build --no-cache -t app:b --provenance=false .
docker save app:a -o a.tar && docker save app:b -o b.tar
diffoscope a.tar b.tar          # the tool that tells you which layer/file differs
```

### Nix: pin the inputs, not the branch

```nix
# flake.lock is generated and committed; it pins nixpkgs to an exact revision.
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";   # a branch: moves
    nixpkgs.flake = true;
  };
}
# For a strict pin, replace the branch with the 40-char rev from flake.lock,
# or commit flake.lock and use `nix build --no-update-lock-file`.
```

```bash
nix build .#myapp --no-update-lock-file   # never re-resolve inputs in CI
nix path-info --json .#myapp | jq '.[].hash'   # the output store path = a content hash
nix develop --command bash               # a dev shell with the exact toolchain
```

## SBOM and provenance

```bash
# SBOM from an image
syft ghcr.io/myorg/myapp@sha256:<digest> -o spdx-json > sbom.spdx.json
syft ghcr.io/myorg/myapp@sha256:<digest> -o cyclonedx-json > sbom.cdx.json

# SBOM in CI without installing anything (verify the action's current tag)
- uses: anchore/sbom-action@<sha>
  with: { image: ghcr.io/myorg/myapp@${{ steps.build.outputs.digest }},
          format: spdx-json, output-file: sbom.spdx.json }
- uses: actions/upload-artifact@<sha>
  with: { name: sbom, path: sbom.spdx.json, retention-days: 90 }

# Container provenance, built into the push
docker buildx build --provenance=mode=max --sbom=true --push -t repo/app:tag .
docker buildx imagetools inspect repo/app@sha256:<digest> --format '{{ json .Provenance }}'

# Sign and attest without a stored key (OIDC in CI)
cosign sign ghcr.io/myorg/myapp@sha256:<digest>
cosign attest --predicate provenance.json --type slsaprovenance \
  ghcr.io/myorg/myapp@sha256:<digest>

# Verify identity, not just validity
cosign verify \
  --certificate-identity-regexp '^https://github.com/myorg/myrepo/\.github/workflows/release\.yml@refs/tags/v.*$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  ghcr.io/myorg/myapp@sha256:<digest>
```

Publish the SBOM alongside the release (GitHub Release asset, or a package
registry's attestation store). An SBOM that lives only in a build log is an
SBOM nobody can query during an incident.

## Chasing a non-reproducible build

1. **Build twice into different output directories on the same machine.** If
   they match, the leak is environmental (a tool, a path, a service); if they
   differ, it is in the build itself (a timestamp, ordering, randomness).
2. **`diffoscope a b`** — it decomposes both artifacts and reports the first
   difference, down to a byte offset, a differing timestamp, a differing
   path, or a compression difference. Read its first line; that is almost always
   the cause.
3. **Narrow it**: if the difference is in one file inside a tar, extract both
   and `diff -r`; if a file differs, look at its first differing line
   (`cmp -l` shows the offsets). The usual suspects in order: a timestamp, an
   absolute path, a random id, a hostname/user, a locale-dependent sort, a
   compressed blob built by a different library version.
4. **Bisect the toolchain**: build with the previous version of the compiler /
   archiver / generator. If the output changes with a tool version, pin that
   tool (in a container, or via Nix).
5. **Bisect the environment**: build in the Nix sandbox. If it becomes
   reproducible in the sandbox, the leak is ambient state (a config file, an
   env var, a home directory, a network fetch) — move that input into the
   declared inputs.
6. **Make it a CI check once fixed**, so it cannot regress. Compare digests
   between the current run and a stored "known good" digest for the commit, or
   simply build twice and compare.

## Where the platform helps

- **GitHub Actions**: `docker/build-push-action` with `provenance: true` and
  `sbom: true` attaches both to the image by default in recent versions —
  confirm with `docker buildx imagetools inspect`.
- **GitLab**: dependency scanning and container scanning emit SBOMs; the
  "protected container" feature signs images with a keyless flow.
- **npm / PyPI / crates.io**: publishing with trusted publishing (OIDC) removes
  the long-lived API token entirely; the registry then records the workflow
  identity as the publisher.
