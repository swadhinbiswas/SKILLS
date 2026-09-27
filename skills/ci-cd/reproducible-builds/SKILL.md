---
name: reproducible-builds
description: Make builds byte-for-byte reproducible and verifiable - lockfiles, hermetic and pinned toolchains, Nix flakes or digest-pinned containers, SOURCE_DATE_EPOCH and sorted archives, SBOM generation, SLSA build provenance and attestations, and reproducible-builds checkers. Use when a build works on one machine and not another, when auditing a supply chain, when a release artifact cannot be verified, or when setting up hermetic CI. Triggers on "works on my machine", "reproducible build", "hermetic", "SOURCE_DATE_EPOCH", "SBOM", "provenance", "attestation", "slsa", "supply chain", "non-deterministic build".
compatibility: Examples use Nix flakes, Docker BuildKit, GNU tar, and general attestation tooling. Verify the exact flags for your toolchain (gpg, syft, cosign, nix) with --help; attestation support varies by CI platform and is evolving.
metadata:
  version: "1.0"
---

# Reproducible Builds

Two guarantees, often conflated:

- **Reproducible:** the same source produces the same artifact, bit for bit, on
  any machine, at any time. This is a *correctness* property — it removes
  "works on my machine" and lets you verify an artifact without trusting the
  builder.
- **Verifiable:** you can prove *where* the artifact came from — which source,
  which builder, which inputs — with a signature and an attestation. This is a
  *supply-chain* property. An artifact can be verifiable without being
  reproducible, and (badly) reproducible without being verifiable.

Aim for both, in that order: reproducibility makes verification meaningful.

## Why "works on my machine" fails

Every one of these makes a build depend on something outside the source tree:

- Unpinned dependency versions (no lockfile, or `^` ranges resolved at build
  time, or a floating base image tag).
- The environment leaking in: `~/.npmrc`, `~/.gitconfig`, `HOME`, a global
  `PATH`, an installed but unlisted tool, a proxy, a credential helper.
- Ambient time: build timestamps, `__DATE__`, a generated changelog header, a
  `mtime` in an archive, a random UUID, a session id, a PID.
- Ambient locale and timezone: sort order, date formatting, collation.
- Non-deterministic ordering: directory iteration order, map iteration, a
  parallel build that concatenates output in completion order.
- The network at build time: fetching a dependency the lockfile did not pin, a
  `curl … | sh` bootstrap, a base image that moved.
- The build user: files owned by a different uid, or a umask difference, baked
  into a tar.

The first step is always the same: **make the build take no input other than
the source tree and a pinned toolchain.**

## The workflow

- [ ] 1. Pin every input: lockfile committed, base image by digest, toolchain by
      version, no network fetches at build time
- [ ] 2. Remove ambient state: fixed `TZ=UTC`, `LC_ALL=C`, `SOURCE_DATE_EPOCH`,
      empty/isolated `HOME`, no user git config
- [ ] 3. Fix nondeterminism: sorted archive entries, sorted file lists, no
      timestamps, no randomness without a fixed seed
- [ ] 4. Make it hermetic: build in a container or a Nix sandbox with only the
      declared inputs
- [ ] 5. Verify: build twice, compare digests, in CI
- [ ] 6. Then: SBOM + provenance attestation + signature, published with the
      release

## 1. Pin every input

- **Commit the lockfile, and install from it exactly**: `npm ci`,
  `pnpm install --frozen-lockfile`, `yarn install --immutable`,
  `pip install --require-hashes -r requirements.txt` (hashes in the file),
  `cargo build --locked`, `go mod download` (verified against `go.sum`),
  `bundle install --deployment` (or `BUNDLE_FROZEN=true`).
- **Pin base images by digest, not tag**:
  `FROM postgres:16.2@sha256:<digest>`. A tag is a mutable pointer; the digest
  is the content.
- **Pin tools, not just dependencies**: the compiler, the linker, the archiver,
  the code generator. Two patch versions of a compiler can produce different
  output.
- **A build that reaches the network** is not hermetic. Everything it needs must
  be fetched *before* the hermetic step, by a lockfile, and verified by hash.
- **Record the platform explicitly**: build for a fixed target
  (`GOOS=linux GOARCH=amd64`, `--platform linux/amd64`, `npm_config_target`),
  not "whatever this machine is". A build for the wrong arch that happens to
  run is the worst kind.

## 2. Remove ambient state

```bash
export SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)   # commit timestamp, in UTC
export TZ=UTC
export LC_ALL=C
export LANG=C
export HOME=/nonexistent        # catch a tool that reaches for ~/.something
export PATH=/usr/bin:/bin       # no user-installed tools
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_SYSTEM=/dev/null
```

- **`SOURCE_DATE_EPOCH`** is the standard override: gzip, tar, and many
  toolchains honour it. Without it, two builds of the same source differ in the
  gzip header's mtime alone — and the whole artifact's digest with it.
- **`git log -1 --pretty=%ct`** gives the commit time, so the output depends on
  the source, not on when you built. Fetch full history for this.
- **No user git config**: a `.gitconfig` with `user.name`, a `core.autocrlf`,
  or a signing key changes what gets built or breaks the build on a runner.
- **Locale**: `LC_ALL=C` fixes collation-dependent sort, number formatting, and
  case rules. `en_US.UTF-8` and `C` order files differently.

## 3. Fix nondeterminism

```bash
# Tar: sorted entries, fixed owner/mtime, no extended headers, fixed format
tar --sort=name \
    --mtime="@$SOURCE_DATE_EPOCH" \
    --owner=0 --group=0 --numeric-owner \
    --format=gnu \
    -cf dist.tar dist/
# Zip: sorted, fixed timestamps (Info-ZIP honours SOURCE_DATE_EPOCH only with
# some versions; a Python zipfile loop with a fixed date_time is the reliable way)
# Jar: needs sorted entries and a fixed manifest.
```

- **Every archive must have a fixed entry order** (sort by name), a fixed
  `mtime`, and a fixed owner. Otherwise the digest changes with the filesystem's
  readdir order.
- **Generators that embed a date or a version-from-git** must read
  `SOURCE_DATE_EPOCH` or the commit, not `date.now()`.
- **Randomness** (a generated ID, a build nonce, a temp name) must be either
  seeded deterministically or removed from the output.
- **Parallel builds** that merge output need a deterministic merge: collect
  per-unit outputs and concatenate in a sorted order, not as they finish.
- **`__DATE__`/`__TIME__` macros in C** and similar: there is no standard
  override; patch the source to use a fixed date or the commit date.
- **Map/dict iteration in a generator**: in Go, `for k := range m` is random —
  sort the keys. Same class of bug in any language with randomised hash seeds.
- **Embedded build paths** (`__FILE__`, debug info) differ between the build
  directory and the checkout. Build in a fixed path, or strip debug info /
  compile with a stable `-trimpath` (Go) / `-ffile-prefix-map` (clang, check
  your compiler's flag).

## 4. Hermetic builds: containers and Nix

### Container (BuildKit)

```dockerfile
# syntax=docker/dockerfile:1
FROM golang:1.23.5-bookworm@sha256:<digest> AS build
ARG SOURCE_DATE_EPOCH=0
ENV TZ=UTC LC_ALL=C
WORKDIR /src
# Dependencies first: copied from the lockfile, verified by go.sum.
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
# -trimpath removes the build path from the binary. CGO off for a static build.
RUN --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -buildid=" -o /out/app ./cmd/app

FROM gcr.io/distroless/static-debian12@sha256:<digest>
COPY --from=build /out/app /app
USER 65532:65532
ENTRYPOINT ["/app"]
```

- **BuildKit is deterministic where it can be**: `COPY` of the same context
  with the same files gives the same layer, and `RUN` steps cache on the
  command plus the previous layer's digest.
- **The image digest is the identity of the artifact.** Deploy by digest, and
  the tag is just a name.
- **Pinning a base image by digest** is what makes a container build
  reproducible; `@sha256:` is the whole trick.
- Verify with `docker buildx build --provenance=true` and inspect the attached
  provenance (see below).

### Nix flake (strongest hermeticity)

A Nix flake pins every input to a content hash and builds in a sandbox with
only the declared dependencies — no ambient `PATH`, no network, no home
directory.

```nix
# flake.nix
{
  description = "myapp";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";   # pin the rev for strictness
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      packages = forAll (pkgs: {
        myapp = pkgs.stdenv.mkDerivation {
          pname = "myapp";
          version = "1.4.0";
          src = ./.;
          nativeBuildInputs = [ pkgs.go pkgs.gnumake ];
          buildPhase = "make build";
          installPhase = "make install PREFIX=$out";
          # Determinism: fixed timestamps, no network, no user config.
          SOURCE_DATE_EPOCH = 0;
          enableParallelBuilding = true;
        };
      });
      devShells = forAll (pkgs: { default = pkgs.mkShell { packages = [ pkgs.go pkgs.python3 ]; } });
    };
}
```

```bash
nix build .#myapp                       # hermetic build
nix build .#myapp --rebuild             # force a second build
nix path-info .#myapp                   # prints the output store path (a hash)
nix develop                             # a dev shell with the exact toolchain
```

Nix is the most complete answer to reproducibility and hermeticity and the
steepest to adopt. Use it for the build of a release artifact you must be able
to rebuild in two years; do not convert an existing CI to it as a side project.

## 5. Verify reproducibility in CI

Build the same commit twice, in two clean environments, and compare digests.
A reproducibility check that never fails is a check that is not running.

```bash
# Two builds into different directories, same inputs.
export SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)
rm -rf out1 out2
make build OUT=out1
make clean
make build OUT=out2
sha256sum out1/app.tar out2/app.tar
# Identical digests = reproducible. Different = find the leak (compare with
# `diffoscope out1 out2` or `tar --diff` / `unzip -l` to see which file differs).
```

- **`diffoscope`** (`pip install diffoscope`, or the packaged binaries) explains
  *why* two artifacts differ — it will point at a timestamp, a path, a
  compression level, or a single differing byte. This is the tool that turns
  "not reproducible" into a fixable bug.
- **Build in two different environments** (two container images, two machines,
  two runner OS versions) — building twice on the same machine can hide
  host-dependence. Building the same commit on `x86_64` and `arm64` and
  getting the same digest (for a target-independent artifact) is a strong
  signal.
- **Compare across time**: the same commit built a month later should still
  match. A registry mirror that changed, or a base image that moved, shows up
  here.

## 6. SBOM, provenance, and attestation

Once the build is reproducible, publish the evidence.

- **SBOM** — a machine-readable inventory of every component in the artifact.
  Use `syft` (`syft <image> -o spdx-json` or `-o cyclonedx-json`) or
  `anchore/sbom-action` in CI; attach it to the release. An SBOM is what you
  query when a CVE lands in one of your 900 transitive dependencies.
- **Provenance** — where the artifact came from: source repo and commit, builder
  identity, build platform, parameters. Generate it in the build:
  ```bash
  # Docker BuildKit provenance, built into the push:
  docker buildx build --provenance=mode=max --sbom=true --push -t repo/app:sha .
  # Inspect:
  docker buildx imagetools inspect repo/app@sha256:<digest> --format '{{ json .Provenance }}'
  ```
  For non-container builds, use an in-toto/SLSA provenance generator and sign
  it (`cosign attest --predicate provenance.json --type slsaprovenance`).
- **SLSA** — a framework for build provenance levels. L1 is provenance recorded;
  L2 is provenance from a hosted, isolated build service; L3 is provenance that
  cannot be forged by the build host. Most CI + OIDC + attestation setups reach
  L2–L3 for free because the build runs on infrastructure you do not control
  (GitHub-hosted runners) and the identity is federated, not a stored key.
- **Sign the artifact and the attestation**: `cosign sign` / `cosign attest`,
  or `sigstore` in CI with OIDC (no long-lived signing key — see
  `github-actions-hardening`). Verify at deploy time: refuse to deploy an image
  whose signature or provenance does not match the expected repository,
  workflow, and commit.

```bash
# Verify before you deploy:
cosign verify \
  --certificate-identity-regexp 'https://github.com/myorg/myrepo/\.github/workflows/release\.yml@refs/heads/main' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  repo/app@sha256:<digest>
```

The identity check is the security control: it asserts *which workflow in which
repo* signed it. Without `--certificate-identity*`, you have only checked that
someone with a valid cert signed it.

## Gotchas

- **`SOURCE_DATE_EPOCH` is honoured by gzip and many tools, not by all.** A
  `.tar.gz` is deterministic if tar is given `--mtime` and `--sort=name`; a
  `.zip` or a `.jar` often is not without extra work. Check the specific
  format; do not assume the env var is enough.
- **`pip install -r requirements.txt` without hashes** is not reproducible even
  with a lockfile of pinned versions — the wheels can be re-uploaded. Use
  `--require-hashes` with hashes in the file, and a private index if the
  package could be typosquatted.
- **A floating base image tag makes the build irreproducible** even if
  everything in the Dockerfile is pinned. This is the most common single cause
  of "it built differently yesterday".
- **The build directory path leaks into binaries** (debug info, `__FILE__`,
  panic messages). Build in a fixed path or strip/rewrite it.
- **Nix store paths change with the nixpkgs pin.** Pin `nixpkgs` to a
  `github:NixOS/nixpkgs/<rev>` for byte-identical output across time; a branch
  name is a moving target.
- **`npm ci` still lets the *content* of a registry tag change** for a
  non-locked dependency, and `npm` can resolve an optional dependency
  differently on different OSes. The lockfile pins the graph; a private,
  immutable registry pins the content.
- **Compression level and library version change the bytes.** Two gzip
  implementations at the same level produce different output; pin the
  compressor (a container with one `gzip`) if byte-identity of a compressed
  artifact matters.
- **"Reproducible" for a *container image* is harder than for a tarball**: the
  image includes the base image's layers, its config (creation timestamp, env
  ordering), and the runtime's file layout. BuildKit gets close (its
  `SOURCE_DATE_EPOCH` support and `--provenance` help), but expect to compare
  layer-by-layer with `docker save` and `diffoscope`, and be satisfied with a
  pinned base digest plus a reproducible app layer.
- **Rebuilding for verification is slow.** Do it on a schedule or on release,
  not on every PR — but do it at all, and fail the build when it fails.
- **Signing does not make an artifact safe.** A reproducible build is what
  makes a signature meaningful: without it, the signed bytes could have been
  produced from different source than they claim.
- **A private dependency proxy that silently resolves a missing package** breaks
  the "only declared inputs" property. Make it fail on anything not in the
  lockfile.

## Files

- `references/tooling.md` — per-ecosystem pinning recipes, the exact
  determinism flags for common archive formats, attestation/SBOM command lines,
  and the diffoscope workflow for chasing a non-reproducible build. Read it
  when you are making a specific build reproducible or wiring attestation.
