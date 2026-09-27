# Debugging shell scripts

Read this when a script behaves differently under cron/CI than in your
terminal, or when `set -e` is not firing where you expect.

## Make bash tell you what it is actually running

Put this near the top (after `set -euo pipefail`):

```bash
set -x                       # or: bash -x script.sh arg1
PS4='+ ${BASH_SOURCE##*/}:${LINENO}:${FUNCNAME[0]:-main}: '
```

A `$LINENO`/`$FUNCNAME`-aware `PS4` is worth it — without line numbers a
traceback into a function is guesswork:

```bash
+ build.sh:31:main: cp /tmp/x.csv /data/
+ build.sh:9:copy_one: cp -f -- "$1" "$dst"
```

Toggle sections with `{ set -x; cmd; set +x; }` around the region you care
about, so the interesting part is not buried.

Trick: inside a loop or function, run `declare -p var` to see exactly what a
variable holds — including trailing newlines and unexpanded whitespace, which
are the usual cause of "the value looks right in the trace but is wrong in
the file".

## The only three things to check first

1. **Is it even the same bash?** `head -1 script.sh` vs
   `type -a bash`. cron runs `/bin/sh`. A `#!/usr/bin/env bash` shebang with
   CRLF line endings (`file script.sh` shows `with CRLF line terminators`)
   gives `env: 'bash\r': No such file or directory` — the single most common
   "works locally, fails in CI" cause.
2. **Is it the same environment?** `env | sort` in both places. Missing
   `HOME`, `PATH`, `LANG` (breaks UTF-8 sorting), `TZ` (breaks date maths).
3. **Is it the same cwd?** `pwd` in both. Relative paths are the default
   cause. Cron starts in `$HOME`; systemd `ExecStart` starts in `/`.

## Reconstruct what cron actually ran

```bash
crontab -l
ls -l /var/log/cron* /var/log/syslog 2>/dev/null | head
sudo grep CRON /var/log/syslog | tail -30
```

If there is no log, the job never ran — a syntax error in the crontab entry
(including a `%`, which cron treats as a newline and which needs `\%`) stops
it from being installed. Quick sanity check: have the job's first line be
`date >> /tmp/cron-probe.log; whoami >> /tmp/cron-probe.log`.

## Force the script through cron exactly as cron will

```bash
env -i /bin/bash -lc '/full/path/to/script.sh arg'   # near-empty env
sudo -u nobody /full/path/to/script.sh               # as the right user
```

`env -i` is the single most useful command here: it reproduces "works on my
machine" in one step.

## `set -e` is not firing — check these in order

- The failing command is in an `if`/`&&`/`||`/`!` position, or in a function
  **called from** one of those. `errexit` is disabled for the whole function
  body in that case.
- It is a pipeline component and `pipefail` is off.
- It is `local v=$(cmd)` — `local` succeeds, so the assignment "succeeds".
- It is the last command of a `while` body that ends via `break`/`continue`?
  Check `$?` right after the loop.
- It is in a `$( )` that is itself part of a larger command whose status is
  decided by something else.

To prove where the guard applies, instrument:

```bash
set -Eeuo pipefail
trap 'printf "FAILED at %s:%s (rc=%s in %s)\n" "${BASH_SOURCE[0]}" "$LINENO" "$?" "${FUNCNAME[0]}" >&2' ERR
```

`set -E` is what makes the `ERR` trap fire inside functions and subshells.

## Argument list too long

```
Argument list too long   (E2BIG)
```

You expanded a huge array into a single command. Switch to streaming:

```bash
printf '%s\0' "${files[@]}" | xargs -0 -P 4 ./process-one
# or, for reading:
while IFS= read -r line; do ...; done < <(produce)
```

## Hanging

- A command is waiting on stdin: `< /dev/null` for anything that might prompt,
  or `set -o noclobber`? No — add `</dev/null` to the call.
- A child ignored SIGINT: `trap '' INT` in the child, or the parent is in a
  pipeline with a subshell holding the terminal. Use `setsid` or run under
  `timeout`.
- The script is waiting on a lock: `lsof` the lock file, or
  `flock -n /var/lock/x.lock -c '...'` to fail fast instead of blocking.
- Check what it is: `ps -o pid,ppid,stat,wchan:20,args -p <pid>` and the
  process's `wchan`. See `process-and-perf-cli-tooling` in the `terminal`
  domain for the full workflow.

## Things that differ between your terminal and the runner

| Terminal | cron / CI / systemd |
|---|---|
| interactive `PATH` from `.bashrc` | `/usr/bin:/bin` only |
| TTY present | none; any prompt hangs or fails |
| `umask 022` inherited from your session | `umask 022` but often `027` or `077` |
| alias/function for `gs` | aliases are not expanded in non-interactive shells, and neither are most functions unless exported |
| locale set | `POSIX` locale → byte sort order, non-UTF-8 filenames break |
| `ulimit -n` high | often 1024, causing "too many open files" on a big `xargs` fan-out |

## Byte-level inspection

When a file "looks fine" but the tools disagree, it usually has invisible
characters:

```bash
file -i data.csv
sed -n 'l' data.csv            # shows $ for newline, M-BM- for a BOM, \r, \t
od -c data.csv | head          # raw bytes
xxd data.csv | head
```

- A UTF-8 BOM (EF BB BF) at the start of a CSV makes the first column header
  `\ufeffid`, and every join on `id` silently finds nothing.
- CRLF line endings make the last field of every line contain a trailing `\r`,
  which is why `awk '{print $NF}'` looks fine in a terminal and breaks in a
  container built from different tooling.
- Non-breaking spaces and smart quotes from a copy-paste out of a doc.

## Recovering from a partial run

A script that died halfway should leave the system in a state you can reason
about. Do:

```bash
set -euo pipefail
work=$(mktemp -d "${TMPDIR:-/tmp}/job.XXXXXXXX")
trap 'rc=$?; [[ $rc -eq 0 ]] && rm -rf -- "$work" || { printf "left %s for inspection\n" "$work" >&2; }; exit $rc' EXIT
```

Build into `$work`, then a final `mv` into place. `mv` within one filesystem
is atomic; across filesystems it is a copy, so make the staging dir on the
same filesystem as the destination.
