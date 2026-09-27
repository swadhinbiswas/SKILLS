---
name: shell-environment-and-tooling
description: Debug the shell environment itself when a tool is "not found", the wrong version runs, or config leaks between contexts - PATH resolution, aliases vs functions vs scripts, env-var and config-file precedence, .bashrc vs .profile vs .zshrc, .env loading, direnv, and virtualenv activation. Use when "command not found" appears, when a CLI uses the wrong AWS profile or cluster, when secrets or NODE_ENV leak between shells, or when setup differs between local, Docker, and CI. Triggers on "command not found", "PATH", "which vs whereis", "alias", "dotenv", ".env", "direnv", "venv not activated", "wrong version".
compatibility: bash 4+ and zsh; paths and filenames are Linux/macOS. Windows uses a different resolution model and is out of scope.
metadata:
  version: "1.0"
---

# Shell Environment and Tooling

Most "the tool is broken" bugs are the shell handing a different binary,
config, or variable than you think. Diagnose by asking the shell, not by
guessing.

## PATH: why an installed binary "isn't found"

`PATH` is an ordered list of directories, searched left to right, for the
**first** file matching the name. It is built up during login:

1. `/etc/profile` (system-wide, all shells).
2. The first of `~/.bash_profile`, `~/.bash_login`, `~/.profile` that exists
   (**login** shells only — SSH, `su -`, a new terminal in some setups).
3. `~/.bashrc` (interactive, non-login shells — most of your terminal).
4. `/etc/bash.bashrc` or distro equivalent.

Key facts that cause bugs:

- **`PATH` is a string, not a list.** `export PATH=$PATH:~/bin` re-exports it
  every time that line runs, so a sourced file included twice **duplicates**
  entries and slowly grows the variable. Guard with
  `case ":$PATH:" in *":$dir:"*) ;; *) PATH="$dir:$PATH";; esac; export PATH`.
- Prepend (`dir:$PATH`) to *win* the name; append to let system tools win.
- The shell caches command locations in a hash table. After installing a
  binary, `hash -r` (or open a new shell) if you still get the old one:
  `hash: /usr/bin/node: no such file or directory`.
- Interactive shells add `.` implicitly in some configs — a security hole and
  a source of "it works here, picks up a local file".

Diagnose, in order:

```bash
type -a node            # what would run, and every candidate in PATH order
command -v node         # path only, no shell functions/builtins
which -a node           # PATH scan only; misses functions/aliases/builtins
type node               # shows "node is /usr/bin/node" or "is a shell function"
whereis node            # binary + man pages + source, skips PATH order
hash -t node 2>/dev/null || echo "not hashed"
```

`type -a` is the one to use. It reveals a **shell function or alias shadowing
a real binary** — extremely common with `python`, `pip`, `node`, `npm`,
`docker`, `kubectl`, and `ll`. See `shell-scripting-robustness` in the
`terminal` domain for how to make that deterministic inside a script.

**Fix a missing PATH entry where it belongs** (user-level, in `~/.bashrc` or
`~/.profile` for a login-only tool), not by exporting it ad hoc in the current
shell. For a tool used by *scripts and CI*, put it on the system PATH or set
it explicitly in the service definition — do not depend on a developer's
`.bashrc`.

## Aliases vs functions vs scripts

- **Alias**: a textual substitution, only in **interactive** shells, expanded
  at parse time. `alias ll='ls -alF'`. An alias is not inherited by a child
  shell or a script unless `shopt -s expand_aliases` is set (and even then
  only where aliases are defined in that process). Never rely on an alias in a
  script.
- **Function**: shell code in the current shell, can be exported to children
  with `export -f` (bash) / `functions` (zsh). Available in non-interactive
  children only if exported, and `sh` (dash) will not know it. A function can
  change your shell's state (variables, cwd) — an alias cannot be *used* to
  do that reliably because expansion happens before the command parses.
- **Script**: a separate process. Predictable, testable, versionable, works
  from anywhere and from any language's `subprocess`. **Default to a script
  for anything reused or shared.**

Rule: use a function when it must affect the *current* shell (e.g. changing
directory, setting a var); use a script for everything else. Keep one obvious
implementation of a task: if there's both an alias `ll` and a script
`~/bin/ll`, you now have two behaviours depending on how you typed it.

## Environment variables and config precedence

Most tools read config from several places, and the precedence (highest
wins) is roughly: **explicit flags > environment variables > local config
file > user config file (`~/.aws/config`, `~/.kube/config`, `~/.config/x`)
> system config (`/etc/...`) > tool defaults.**

Concrete examples to check when a tool "uses the wrong account":

```bash
# AWS: which profile/config is in effect, and which file/setting set it
aws configure list                       # shows profile, source of each setting
aws configure get region                 # resolved value
echo "$AWS_PROFILE" "$AWS_DEFAULT_REGION"
aws sts get-caller-identity              # the ground truth: which account/role
AWS_PROFILE=prod aws sts get-caller-identity   # override for one command
```

```bash
# Kubernetes: current context and where it came from
kubectl config current-context
kubectl config get-contexts
kubectl config view --minify             # resolved server for current context
echo "$KUBECONFIG"                       # colon-separated list; first match wins
```

- **`KUBECONFIG` is a list.** `:`-separated; the *first file containing a
  named context* wins for that name. Merging behaves differently from
  override — this surprises people when two kubeconfigs define a context with
  the same name.
- **`NODE_ENV`**, `DEBUG`, `CI`: environment beats `.env` files. Most loaders
  (`dotenv`, `python-dotenv`, Vite, Next) by default do **not** override an
  already-set variable. That is why a value "sticks" from your shell.

**The debugging move, always:** print the resolved environment *and* the
config files in play, then override the one variable explicitly for a single
command (`VAR=x cmd`) to test whether precedence is the problem.

Precedence trap: a variable exported in your interactive shell is inherited
by every child, including build tools, test runners, and `docker compose`
(though `docker run` does not inherit it unless passed `-e` or the container
declares it). If `NODE_ENV=production` leaks into a test run, that is the
cause.

## Which startup file goes where

| File | Read when | Put here |
|---|---|---|
| `/etc/profile`, `/etc/profile.d/*` | any login shell, system-wide | system env, `PATH` defaults |
| `~/.bash_profile` / `~/.bash_login` / `~/.profile` | **login** shells (first that exists) | `PATH` additions, `umask`, one-time setup |
| `~/.bashrc` | **interactive non-login** shells | aliases, functions, prompt, editor |
| `~/.zshrc` | interactive zsh | same as bashrc |
| `~/.zprofile` | zsh **login** | like bash_profile |
| `~/.bash_logout` | zsh/bash on **logout** | cleanup, `tmux` detach, history save |
| `/etc/bash.bashrc` | interactive bash, system-wide | system-wide aliases |

Consequences:

- **Non-interactive shells (cron, `ssh host cmd`, CI) read neither
  `.profile` (not a login) nor `.bashrc` (not interactive) by default.** So
  your `PATH` tweaks and aliases do not apply. That is exactly why cron says
  `git: command not found` while you can run `git` in your terminal. Put
  absolute paths or an explicit `PATH=` in the unit file; `crontab -l` is worth
  reading once before debugging anything else.
- `~/.bash_profile` often *overwrites* `PATH` and forgets to re-add
  `~/.cargo/bin`, `go/bin`, etc. Prefer sourcing `~/.profile` or a shared
  `~/.envrc`-style file rather than duplicating the same `export` lines in
  both `.profile` and `.bashrc`.
- Guard every conditional in these files: `[ -f ~/.my_aliases ] && . ~/.my_aliases`.
- `shopt -s histappend` (bash) and `setopt HIST_IGNORE_DUPS` (zsh) keep shared
  history clean. An unsaved history file means "up-arrow" shows nothing after a
  crash.

Keep a sane skeleton:

```bash
# ~/.profile  (login: PATH + umask, nothing interactive)
[ -f ~/.profile_env ] && . ~/.profile_env
umask 022

# ~/.bashrc  (interactive: aliases + functions)
case $- in *i*) ;; *) return;; esac   # guard for non-interactive safety
[ -f ~/.config/bash/aliases.sh ] && . ~/.config/bash/aliases.sh
[ -f ~/.config/bash/functions.sh ] && . ~/.config/bash/functions.sh
```

## The loader pattern: `.env` files and direnv

**`.env` files** are read by the *application*, not the shell. The important
distinctions:

- Syntax: `KEY=value`, **no** `export` needed, **no** command substitution,
  **no** spaces around `=`. Quoting rules differ per loader — dotenv strips
  matching quotes, shell does not. `FOO="a b"` is safer.
- **Precedence**: shell env > `.env` by default in most loaders (they don't
  override existing). So `FOO=1 .env-based-app` may or may not win depending
  on the loader — check the framework's docs for its exact rule.
- **Never commit `.env` with real secrets**; commit `.env.example`. Add
  `.env` to `.gitignore` from day one.
- **Never use `.env` for anything the shell itself needs** (`PATH`, `IFS`,
  `PS1`) — the shell has already read its config by the time a program
  sources `.env`.

**direnv** makes this automatic per-directory: put `.envrc` in a repo, run
`direnv allow` once, and every `cd` into it loads/unloads the env. It is the
right tool for a project that needs its own `PATH`, `PYTHONPATH`, or tool
versions.

```bash
# .envrc
export PATH="$PWD/bin:$PATH"          # project-local tools first
export PYTHONPATH="$PWD/src"
use python3.11                         # direnv stdlib: select a version
watch_file .envrc                      # reload when this file changes
dotenv .env.local                      # layer a gitignored file on top
```

`direnv allow` trusts the file; `direnv deny`/`export` control it. Without
`allow`, the env is not loaded (a safety feature). direnv must be in your
`.bashrc`; it hooks `PROMPT_COMMAND`/`precmd`.

**If you don't want a dependency**, the manual loader is a few lines — load
`KEY=value` pairs into the current shell:

```bash
# usage: source .env (only KEY=VALUE, no export needed)
set -a          # subsequent assignments are auto-exported
# shellcheck disable=SC1091
. ./.env
set +a
```

`set -a` (allexport) is what makes the sourced assignments become
environment variables. This is the correct minimal `.env` loader; use it in
Docker `ENTRYPOINT` scripts and one-off shells, not as a general config
system.

## Virtualenvs and the activation gotchas

- A **venv** is a directory with its own `bin/python`, `bin/pip`, and
  `pyvenv.cfg`. `activate` prepends its `bin` to `PATH` and sets
  `VIRTUAL_ENV`; `deactivate` undoes it. Nothing is "installed into" the venv
  magically — the `PATH` entry is the entire mechanism.
- **Gotcha 1 — `which python` is lying to you.** If you `alias`/`function`
  python, or your `PATH` puts a system `bin` first, you can run a venv's
  `pip` with the system `python`. Sanity check: `python -c 'import sys;
  print(sys.executable)'` and `python -m pip` (never bare `pip`).
- **Gotcha 2 — `pip` vs `python -m pip`.** `pip` on PATH may belong to a
  different interpreter. `python -m pip` always uses the same Python as
  `python`. House default: `python -m pip install ...`.
- **Gotcha 3 — shebangs are absolute.** A console script installed in
  `venvA/bin/foo` starts with `#!/…/venvA/bin/python`. Copy that script (or a
  cron job calling it) into `venvB` and it still runs under venvA. This is
  why a "moved" script behaves wrongly. Reinstall rather than copy.
- **Gotcha 4 — activation does not persist.** A new terminal, a `tmux` pane
  created before activation, a cron job, and a `sudo` command all start from
  the *base* environment. `sudo python ...` runs the **root** (system)
  python, not your venv. Use absolute paths, or `sudo -E` carefully, or drop
  `sudo` and manage permissions differently.
- **Gotcha 5 — `tmux`/`screen` panes and `ENV` are static.** Environment
  variables (`export FOO`) are captured when the pane is created; later
  changes to your interactive shell's `export` do not update running panes or
  already-running processes.
- **Gotcha 6 — nested/leftover activation.** Activating B inside A prepends
  B, and `deactivate` only pops one layer. A stray `deactivate` from the
  wrong shell, or `VIRTUAL_ENV` pointing at a deleted dir, leaves you with a
  half-on venv. Reset with `deactivate 2>/dev/null; hash -r` or open a clean
  shell.
- Detection: if `echo $VIRTUAL_ENV` is set but `which python` is not
  under it, your `PATH` is being overridden *after* activation — look for a
  later `PATH=` line (e.g. in `~/.bashrc` after the venv block, or a
  `direnv`/conda shim) that reorders `PATH`.

## Gotchas

- **A venv is only a `PATH` entry, and nothing else carries it.** `deactivate`
  is a shell function in *your* shell; the next terminal, a cron line, and a
  script's shebang all start from the base environment. `sudo`, `su -`,
  `systemd`, `at`, and `nohup` drop the environment too. Invoke the absolute
  path — `~/.venv/bin/python script.py` — or set `PATH` in the unit file.
- **`tmux` and `screen` panes keep the environment they were born with.**
  Pane env is a copy: an `export FOO=bar` after the pane started is invisible
  to it, and `tmux kill-server` then recreating your workflow loses every
  activation. A venv activated in the shell before `tmux` is not active in the
  pane. Check `env | grep VIRTUAL_ENV` inside the pane, not outside.
- **Non-interactive shells read neither `.bashrc` nor `.profile`, and cron
  reads a third thing again.** A non-interactive, non-login shell is exactly
  what `sh script.sh` and `docker exec` give you, and neither file is read. In
  cron the variable is even smaller — `HOME` can differ, `SHELL` is unset, and
  the `PATH` is often just `/usr/bin:/bin`, which is why the same job succeeds
  when you paste it into a terminal and fails at 03:00.
- **`which` reports the wrong thing often enough to be the bug, not the
  diagnosis.** It is a PATH scan: it does not see shell functions or aliases, so
  it happily prints `/usr/bin/python3` while your `function python` is what
  actually runs, and it is a separate implementation with its own PATH lookup.
  Use `type -a`; use `command -v` to deliberately bypass functions and aliases.
- **Aliases are a text substitution that only exists in interactive shells, so
  they vanish in a script even when the same file defines them.** Worse, the
  text the alias expands to is *baked in at definition time*, so an alias
  wrapping a variable keeps that value forever. Anything a script or another
  person depends on must be a script in `~/bin`; `export -f` only covers
  children of a *bash* parent, and `sh`/dash drops it entirely.
- **A CLI can read config from a completely different place than the one you
  set — the env beats the file for most tools.** With `AWS_PROFILE=prod` exported
  in the shell, the profile in `~/.aws/config` is ignored, and setting
  `AWS_PROFILE` inside a script you ran by hand does nothing to a job that
  inherited the old one. `aws configure list` and `git config --show-origin
  --get-all <key>` print the resolved value and the file that set it; pin
  `AWS_PROFILE=prod cmd` per command, or `AWS_CONFIG_FILE=` at a temp file.
- **`PATH` order, not the version manager, decides which version runs.**
  nvm, asdf, volta, rbenv, pyenv and mise are PATH edits: the shim directory
  that comes first in `PATH` wins, so a stale export in `~/.profile` silently
  reinstates the system `node` after a correct `nvm use`. `type -a node` lists
  every candidate in the order the shell will try them.
- **The shell's command hash goes stale and returns a path that no longer
  exists** — `hash: /usr/bin/node: no such file or directory` after an upgrade
  or a venv deactivation. The shell is reporting a cached location, not a real
  one. `hash -r` in the current shell, or a fresh shell, clears it.
- **A `PATH` line that is a plain `export PATH=$PATH:$dir` runs again on every
  source and every nested interactive shell, duplicating entries** until the
  variable is thousands of characters and the lookup is measurably slower.
  Guard it with a `case ":$PATH:" in *":$dir:"*)` test, and prefer
  `~/.local/bin` over `~/bin` — nothing puts it on `PATH` for you, and
  `/usr/local/bin` beats both on macOS.
- **A venv's console scripts have absolute shebangs.** `venvA/bin/foo` starts
  with `#!/…/venvA/bin/python`; copy that script, or a cron line calling it,
  into another venv and it keeps running under the first. Reinstall rather
  than copy, and prefer `python -m` entry points where they exist.

## Docker / CI notes (env does not cross the boundary)

- **`docker run` passes nothing from your shell unless you name it.** `-e FOO=bar`
  passes one variable; `--env-file .env` passes a file; `docker run image` with no
  flags passes nothing at all. A container that works locally and 500s in CI is
  usually missing the variable the CI file never forwarded.
- **CI runners are not your terminal.** `$PATH`, `$HOME`, and the available
  binaries all differ, so a green local run proves nothing. Set `PATH` explicitly
  in the job and never assume a login shell — most runners run one non-interactive
  `sh -c`.

## The debugging checklist

When "the tool is wrong", run these before changing config:

```bash
type -a <tool>                 # what actually runs, in what order
echo "$PATH" | tr ':' '\n'     # PATH, one entry per line, in order
hash -t <tool> 2>/dev/null     # is a stale hash entry cached?
declare -p <VAR>               # is a var set, and with what value/type?
env | sort                     # full inherited environment
alias <name> 2>/dev/null       # is an alias shadowing it?
set -o | grep -i posix          # is this an interactive/login shell?
shopt -s expand_aliases 2>/dev/null && echo "aliases expand here"
```

Then answer: which directory in `PATH` wins, and is a function/alias
intercepting it. Nine times out of ten that is the whole bug.

## Checklist

- [ ] `type -a <tool>` confirms *which* binary runs, not `which`.
- [ ] `PATH` additions are idempotent (guarded against re-append) and live in
      `.profile` (login) or `.bashrc` (interactive) as appropriate.
- [ ] Non-interactive contexts (cron, CI, `ssh host cmd`) get an explicit
      `PATH`, not the developer's `.bashrc`.
- [ ] Reused behaviour is a **script** in `~/bin` (on `PATH`), not an alias.
- [ ] `.env` is gitignored; `.env.example` is committed; secrets never land
      in `.env` in a shared repo.
- [ ] Any venv-dependent tool is invoked by absolute path (or the service
      definition sets `PATH`), so it works outside an interactive shell.
- [ ] Python installs go through `python3 -m pip`, never bare `pip`.
- [ ] Read `references/env-precedence.md` when a tool is picking up the wrong
      profile/region/cluster and you need the exact precedence chain for AWS,
      Kubernetes, Docker, or Node.
