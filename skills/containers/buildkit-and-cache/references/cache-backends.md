# BuildKit cache backends reference

Where the build cache lives, what each backend is good for, and the failure
modes that make a cache look "empty" when it is not. Read this when a
`--cache-from` seems to be ignored, or when choosing between local, registry,
GHA, and S3.

## Backends

| Backend | Syntax | Scope | Needs | Use for |
|---|---|---|---|---|
| `local` | `type=local,src=DIR,dest=DIR` | one builder, one dir | nothing | local dev |
| `registry` | `type=registry,ref=REG/IMG:tag` | cross-machine, shareable | a writable registry | CI, self-hosted runners |
| `gha` | `type=gha` | one GitHub repo, one run | `actions: write` permission | GitHub Actions |
| `inline` | `type=inline` | in the image config of the pushed image | nothing | single-arch, small, self-contained |
| `s3` | `type=s3,region=…,bucket=…,key=…` | anything S3-compatible | credentials | MinIO, Ceph, large fleets |
| `azblob` / `gcs` | provider-specific | object storage | credentials | cloud-native fleets |

`mode=min` (default) exports only the final stage's layers. `mode=max` exports
every stage, including build stages — the expensive ones. Start with `max`.

`compression=zstd` (or `gzip`) on `cache-to` shrinks the cache; `zstd` is
usually faster. `compression-level` trades size for CPU.

`image-manifest=true` makes the cache push an image index (multi-arch aware)
rather than a single manifest. Needed for multi-arch workflows on the registry
backend.

## Which to pick

- **Local development, one machine:** `type=local` in a gitignored directory.
  Nothing else works without setup.
- **GitHub Actions, hosted runners:** `type=gha`. Fast, no registry, no
  cleanup. Bounded by the repo's cache quota and the 7-day eviction; the cache
  is keyed per branch (only the default branch's cache is readable by other
  branches) and per OS/arch.
- **Self-hosted runners, or any CI that already pushes images:** `type=registry`
  on the image repo, a separate `buildcache` tag. Survives runner
  re-provisioning, shareable across pipelines, and you can see its size with
  `docker buildx du` and the registry UI. You own the eviction policy — a
  `buildcache` tag that is never pruned is a slow disk leak.
- **Fleet / on-prem with S3:** `type=s3`. Same operational caveats as registry,
  plus credential management.
- **`type=inline`:** only when the image is tiny and single-platform and you
  want zero cache infrastructure. It writes the cache into the image config, so
  it is part of what you push — usually not what you want.

## Local cache: the two-step dance

`--cache-to type=local,dest=DIR` fails if `DIR` is also the `src` of
`--cache-from`, because the exporter writes while the importer reads. Use
distinct paths and swap:

```sh
#!/usr/bin/env bash
set -euo pipefail
CACHE=.build-cache
rm -rf "$CACHE"
docker buildx build \
  --cache-from "type=local,src=$CACHE" \
  --cache-to   "type=local,dest=$CACHE-new,mode=max" \
  -t app:tag .
rm -rf "$CACHE" && mv "$CACHE-new" "$CACHE"
```

Gitignore `$CACHE-new` too, or the intermediate write shows up in `git status`.

## Registry cache: practical notes

```sh
docker buildx build \
  --cache-from type=registry,ref=ghcr.io/org/app:buildcache \
  --cache-to   type=registry,ref=ghcr.io/org/app:buildcache,mode=max,compression=zstd \
  -t ghcr.io/org/app:tag --push .
```

- The cache ref needs push access even on a `--cache-from`-only build, because
  buildx checks the manifest to decide what to pull.
- Cached layers are stored as a normal image manifest in that repository. A
  10 GB `buildcache` repo is a real storage bill.
- There is no automatic pruning. Add a scheduled job that deletes the
  `buildcache` tag (or the whole ref) periodically, or it grows forever.
- `image-manifest=true` is required if you build multi-arch; otherwise the
  cache ref points at one platform and other-arch builds get a cold cache.
- If the registry does not support the needed manifest media types, the export
  fails with a manifest error. MinIO and some older registries need
  configuration.

## GitHub Actions cache backend

- Requires `permissions: actions: write` on the job. Without it the export
  fails with a permissions error even though the build itself succeeded.
- Cache is scoped per repository, per branch, per OS/arch, and is only shared
  across branches from the default branch. A feature branch's cache is not
  readable by another feature branch.
- Eviction: entries unused for 7 days are dropped, and the repo has a total
  quota. A cache that is written on every run and never read (because every run
  also changed a base image digest) is pure waste.
- `cache-from: type=gha,scope=NAME` gives separate caches per service in a
  monorepo. Use one scope per buildable unit; a shared scope across unrelated
  services just evicts each other.
- On self-hosted runners the cache is on the runner's disk, so it disappears
  when the runner is re-provisioned. Use the registry backend there.

## Debugging "the cache is not being used"

In order:

1. `docker buildx ls` — is the builder the `docker-container` driver? The
   default `docker` driver has a much more limited feature set and a different
   cache.
2. `docker buildx du` — what does the builder actually hold? An empty or tiny
   result means the exports are failing or going somewhere else.
3. `docker buildx build --progress=plain` — look for `CACHED` on the steps you
   expect to be cached. No `CACHED` at all means the cache import found
   nothing.
4. Did the base image digest change? A new base digest invalidates **every**
   layer below it. This is correct behaviour and the most common "cache stopped
   working" after a Dependabot bump.
5. Did an `ARG` used before a `RUN` change? It is part of the cache key for
   every layer after it.
6. Are you using `--no-cache`? It bypasses the layer cache (but not cache
   mounts).
7. Is the cache mount `id` changing? A new id is a new cache.
8. Is the builder a different machine? A `local` cache follows the directory,
   and a fresh CI runner has no directory. This is the expected behaviour of
   `local` and the reason it does not work in CI.
9. `--cache-to` failing at the *end* of an otherwise successful build — the
   image is fine, the export failed. Read the export error; the two are
   independent.

## Secrets and the cache

- The layer cache key does **not** include secret values. A layer built with
  credentials A is reused for a build with credentials B. Never make the
  build *output* depend on which secret was used.
- A layer built from `ARG SECRET` will contain the secret, and `--cache-to`
  exports it. That is a published credential. Use
  `--mount=type=secret`, which never enters a layer or the cache.
- The `gha` backend's cache is scoped to the repository; contributors with write
  access can read it. A `gha` cache is not a secret store.
