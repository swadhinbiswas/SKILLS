# Configuration and environment precedence, per tool

Read this when a CLI is using the wrong account, region, cluster, or project,
and you need the exact chain of places it reads from.

## General shape

Highest priority first, for almost every modern CLI:

1. **Command-line flags** (`--profile prod`, `--context prod`, `-e KEY=val`)
2. **Process environment variables** (`AWS_PROFILE`, `KUBECONFIG`, `NODE_ENV`)
3. **Project-local config** (`.env`, `.envrc`, `config/local.json`,
   `docker-compose.yml`, `values.yaml` in the repo)
4. **User config** (`~/.aws/config`, `~/.kube/config`, `~/.config/<tool>/`)
5. **System config** (`/etc/<tool>/`, `/etc/default/...`)
6. **Tool defaults / hardcoded fallbacks**

Two consequences that cause most incidents:

- A variable **exported in your shell** outranks the file everyone thinks is
  controlling the tool. This is why a stale `AWS_PROFILE` in `~/.bashrc`
  silently overrides a checked-in `~/.aws/config`.
- Many tools **merge** rather than **override** across config files (kubectl,
  AWS SSO, Docker). "The file that wins" is often not a single file.

## AWS CLI

Config split across two files:

- `~/.aws/config` — profiles, region, output format, `sso_*` settings.
- `~/.aws/credentials` — `aws_access_key_id`, `aws_secret_access_key`,
  `aws_session_token`.

Resolution and diagnostics:

```bash
aws configure list                     # every setting + which file set it
aws configure get region
aws sts get-caller-identity            # the truth: account id, ARN, role
aws configure list-profiles
```

- `AWS_PROFILE` picks a profile; `AWS_DEFAULT_PROFILE` is the legacy name.
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env vars **beat** any profile —
  a common source of "I'm logged in as the wrong IAM user".
- Region precedence: `--region` > `AWS_REGION` > `AWS_DEFAULT_REGION` >
  profile's `region` > `~/.aws/config` `[default]`.
- SSO: if the profile has `sso_start_url`/`sso_session` and a cached token
  has expired, the CLI tries to open a browser — which **hangs forever in a
  non-interactive shell**. Set `AWS_SSO_...` cache or use a role/instance
  credential in CI instead. In CI, prefer instance roles / OIDC over static
  keys.
- Never run `aws configure` in a shared script — it rewrites the shared user
  files. Use `AWS_PROFILE=... aws ...` per command, or `AWS_CONFIG_FILE=`
  pointing at a temp file.

## Kubernetes (kubectl)

- `KUBECONFIG` is a `:`-separated **list** of files. They are merged; for a
  given context *name*, the first file that defines it wins; cluster and user
  names follow the same first-wins-per-name rule.
- Precedence: `--context` flag > `KUBECONFIG` (first match) > `~/.kube/config`
  > in-cluster (`/var/run/secrets/kubernetes.io/serviceaccount`, when running
  in a pod — this wins only if there is no kubeconfig).
- Diagnostics:

```bash
kubectl config current-context
kubectl config get-contexts
kubectl config view --minify                    # resolved cluster for current ctx
kubectl config view --raw                        # with secrets (do not paste)
KUBECONFIG=/tmp/other.yaml kubectl config current-context
```

- Inside a pod, `kubectl` often auto-targets the in-cluster config; that is
  why `kubectl` "works in the pod but not on your laptop" and vice versa.
  Force it with `kubectl --kubeconfig=/root/.kube/config ...`.
- `KUBECONFIG=./a.yaml:./b.yaml` is the idiomatic way to layer a local
  override (e.g. a dev cluster) on top of the base config.

## Docker / Compose

- `docker run` inherits **no** host env vars unless you pass `-e NAME` or
  `--env-file`. `docker run image` is not a mirror of your shell.
- `docker compose` reads a `.env` file in the **compose file's directory**
  for `${VAR}` substitution in the compose file (host-side). This is a
  different mechanism from the container's own env (`environment:` /
  `env_file:`).
- Image `ENV` is a base; `docker run -e` overrides it; `docker exec -e` sets
  it for that one exec only.
- Dockerfile: `ARG` = build-time only; `ENV` = baked into the image and
  visible to the container. A variable set with `ARG` is *not* present at
  runtime.

## Node / npm / dotenv

- `NODE_ENV` is set by the process (e.g. `NODE_ENV=production node app.js`);
  most bundlers/frameworks (Next, Vite, CRA) branch on it. A `NODE_ENV`
  exported in your shell leaks into test runs, dev servers, and builds —
  unset it explicitly for those (`NODE_ENV=test npm test`).
- dotenv loaders (`dotenv`, `dotenv-safe`, `next.config`, Vite) by default
  **do not override** an already-set environment variable, so a shell export
  wins over `.env`. Some frameworks (Next) additionally give
  `.env.local`/`.env.production` a different precedence than plain `.env` —
  check the framework's documented order rather than assuming.
- Precedence in `npm`/`yarn`/`pnpm`: real env vars > `.env` files (via a
  loader) > defaults in code. `process.env.FOO` is the runtime truth.
- nvm/volta/asdf put a shim directory on `PATH`; a version mismatch is
  usually the shim not being first. `node --version` and `which node` tell
  you which install is actually running.

## Git

- `git config` precedence, high to low: command-line `-c key=val`, environment
  `GIT_CONFIG_*`, local `.git/config`, global `~/.gitconfig`, system
  `/etc/gitconfig`, XDG `~/.config/git/config`.
- `GIT_CONFIG_GLOBAL` / `GIT_CONFIG_SYSTEM` override the file locations.
- `credential.helper` is resolved the same way; `git config --show-origin
  --get-all <key>` prints **every** source of a setting and its file — the
  single best debugging command in git.
- `GIT_DIR` / `GIT_WORK_TREE` env vars can silently make git operate on a
  different repository than the directory you're in.

## Python

- Interpreter and packages: `sys.executable` is the truth; `python -c 'import
  sys; print(sys.executable, sys.prefix)'`.
- `PYTHONPATH` entries are prepended to `sys.path` and can shadow installed
  packages with a source checkout. If an import resolves somewhere surprising,
  `python -c 'import foo; print(foo.__file__)'` shows it.
- `VIRTUAL_ENV` is informational; activation is purely the `PATH` edit.
- `pip` config: `PIP_INDEX_URL`, `PIP_CONSTRAINT`, `pip config list -v`.

## Generic debugging recipe

When a tool uses the wrong thing, this sequence finds it every time:

```bash
<tool> --debug 2>&1 | head -40        # most CLIs have a --debug/--verbose
env | sort | grep -i <KEYWORD>        # is a var overriding the file?
type -a <tool>                        # is a shim/alias winning?
<tool> config view / get / list        # what does it think is configured?
<tool> --profile other ...             # override the one setting, one run
```

If overriding that one variable for one command fixes it, the problem is
precedence/environment leakage, not the tool. Fix it by removing the leaked
variable or making the override explicit in the service definition.
