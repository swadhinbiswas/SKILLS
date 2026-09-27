---
name: shell-scripting-robustness
description: Write bash scripts that fail loudly and safely - set -euo pipefail and exactly where it does not fire, quoting every expansion, word splitting and glob traps, arrays vs delimited strings, traps, exit codes, and running from any directory. Use when writing or fixing a .sh file, when a script works in CI but fails in cron, when a script "skips" an error, when a path with spaces breaks it, or when someone asks why set -e did not stop the script. Triggers on "set -e", "pipefail", "word splitting", "unquoted variable", "shellcheck", "script is flaky", "works on my machine".
compatibility: Examples target GNU bash 4.4+ on Linux/macOS. POSIX sh differences are called out where they matter. Install shellcheck for static checking.
metadata:
  version: "1.0"
---

# Shell Scripting Robustness

Bash does not fail. It continues, with a slightly wrong value, and the damage
shows up three steps later in a different file. Every rule below exists because
of a specific silent failure.

Target **bash**, not `sh`. Arrays, `[[ ]]`, `local -n`, and
`mapfile` are why; `set -euo pipefail` is not.

## Skeleton

```bash
#!/usr/bin/env bash
# One line: what this script does. One more: how to invoke it.
set -euo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROG="${0##*/}"

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

usage() {
  cat <<EOF
Usage: $PROG [-v] [-o outdir] target...

  -o DIR   output directory (default: ./out)
  -v       verbose
  -h       this help
EOF
}

main() { :; }
main "$@"
```

- `set -euo pipefail` as lines 2-3, before anything else. `-E` too if you use
  an `ERR` trap.
- `IFS=$'\n\t'` stops space-splitting surprises. If you set `IFS` at all, set
  it once, globally — a function that changes `IFS` without restoring it is a
  bug factory.
- `readonly SCRIPT_DIR` uses `BASH_SOURCE[0]`, not `$0`: sourcing a script
  makes `$0` the *caller's* name, so `$0`-based path resolution breaks the
  moment someone sources your script.
- Log to **stderr**, results to **stdout**. That way `cmd | grep` works.

## `set -e` does not mean what people think

`errexit` exits when a *simple command* fails. The exceptions are the whole
story.

| Context | Does `set -e` fire? | Verified behaviour |
|---|---|---|
| `cmd` fails | yes | exits |
| `cmd1 \| cmd2` (cmd1 fails) | **no** unless `pipefail` | `pipefail` makes the pipeline return cmd1's status |
| `if cmd; then`, `cmd && next`, `cmd \|\| next`, `! cmd` | **no** | by design — you are testing it |
| `cmd` in a function called from those | **no**, and `errexit` is *off for the whole function body* | a latent bug in a "tested" helper runs |
| `cmd` before a `&&` / `\|\|` chain | **no** | only the last command in the list is checked |
| `local v=$(cmd)` | **no** | `local` returns 0, masking the failure |
| `a && b` where `a` fails and is the last in a list | yes (the list returns non-zero) | |
| `while`/`until` condition, `for` list, `[[ ]]`, `(( ))` arithmetic | **no** | these are conditions |
| Command substitution `$(cmd)` in an assignment | yes in bash (assignment's status is the substitution's) | |

Confirmed reproductions:

```bash
set -euo pipefail
g() { false; echo "g continued"; }   # never prints "g continued" in isolation
if g; then echo "g returned true"; fi
# g continued
# g returned true      <-- false did NOT abort; g's LAST command succeeded
```

That is the nastiest one: **a function used in a condition silently loses
`errexit` for its entire body**, so a real failure mid-function is ignored and
the function's exit status is whatever its last command returned. If a helper
can be called both ways, make it return explicitly and never rely on
`errexit` inside it:

```bash
fetch() {
  local url=$1
  curl -fsSL "$url" -o "$tmp"   # may fail
  [[ -s "$tmp" ]] || return 1  # check explicitly
  cat "$tmp"
}
```

Also inherited: `set -e` does **not** propagate into a child `sh`/`bash` you
launch (`bash -c 'false; echo hi'` prints `hi`). If you rely on it across a
process boundary, pass `-e` explicitly.

### Local masking the real status

`local out; out=$(cmd)` keeps the status (the assignment is the last command).
`local out=$(cmd)` returns 0 from `local`. When you need both:

```bash
local out
out=$(cmd) || die "cmd failed: $?"
```

### Where `set -e` is the wrong tool

A command's *expected* failure should be an explicit `if`/`||`, never a
tolerance for `errexit` being off:

```bash
if ! grep -q "$pattern" "$file"; then
  log "no match for $pattern in $(basename "$file")"
  continue
fi
```

## `pipefail` and the pipeline gotcha

`pipefail` makes the pipeline's status the last non-zero status of any
component. Consequences:

- `curl -s "$url" | head -1` is fine (head exits 0, curl gets SIGPIPE, but
  with `pipefail` the status is **141** — so `set -e` will kill you on success).
  Either drop `-e` around it or use `curl -fsS "$url" | { head -1; cat >/dev/null; }`.
- `grep -q` in a pipeline: `grep` exits as soon as it matches, upstream gets
  SIGPIPE (141), `pipefail` reports 141. Prefer `grep -q ... file` (no
  pipeline) or `if grep -q ... ; then` which is exempt.
- A `while read` loop at the end of a pipeline runs in a **subshell**, so
  variables it sets are lost in the parent. This is a top-three shell bug:

```bash
# BROKEN: total is still 0 in the parent
cat f | while read -r line; do total=$((total+1)); done

# FIX A: redirect into the loop, no pipeline, no subshell
total=0
while read -r line; do total=$((total+1)); done < f

# FIX B: process substitution (loop body still runs in the parent in bash)
while read -r line; do total=$((total+1)); done < <(cat f)
```

## Quoting: the single highest-value rule

**Quote every expansion.** Unquoted `$var` undergoes word splitting *and*
pathname expansion, so a filename with a space becomes two filenames, and `*`
becomes every file in the directory.

```bash
dir="my dir"
rm -rf $dir/*      # BROKEN: becomes  rm -rf my dir/*  -> error / wrong targets
rm -rf "$dir"/*    # RIGHT: the variable is quoted, the glob is still live
rm -rf "$dir/"     # with a trailing slash, the glob cannot match
```

- `"$dir"/*` is the correct idiom: quote the variable, leave the `*` outside
  the quotes so the shell still globs. Confirmed: with `dir='my dir'`,
  `ls $dir/*` reports `cannot access 'my'`, while `ls "$dir"/*` lists
  `my dir/x`.
- `rm -rf "$dir/"` is safe against `rm -rf "$dir"` when `$dir` is `/` or empty —
  it fails to match rather than deleting `/`. But it also *cannot* delete a
  hidden-file-only directory and cannot delete `$dir` itself. Use
  `rm -rf -- "$dir"/*` with a `[[ -d $dir ]]` guard.
- `--` before a filename defends against names beginning with `-`:
  `rm -rf -- "$dir"`, `cp -r -- "$src" "$dst"`.
- Quote `$(...)` too: `"$(dirname "$f")"`, never `$(dirname $f)`.
- `for f in $dir/*` is the same bug wearing a disguise. Use
  `for f in "$dir"/*` and handle the "no match" case:
  ```bash
  shopt -s nullglob
  files=("$dir"/*)          # 0 elements if nothing matches
  shopt -u nullglob
  (( ${#files[@]} )) || die "nothing to do in $dir"
  ```

## `[[ ]]` vs `[ ]`

- `[[ ]]` is a bash keyword: no word splitting, no glob expansion on the
  operands, `&&`/`||` work inside, and `=~` regex works.
- `[ ]` is the external `/usr/bin/[`: it is a plain command, so
  `[ $x == a* ]` word-splits `x` and does **not** glob `a*` (it is a string
  compare). Use `=` not `==` with `[` (dash's `[` treats `==` oddly).
- `[[ a* == a* ]]` is a **pattern** match, so the right side globs. Use
  quoted RHS for a literal: `[[ $x == "a*" ]]`.
- `=~` is ERE, unquoted. `[[ $v =~ ^[0-9]{4}$ ]]`. Do not quote the pattern —
  quoting makes it a literal string.

```bash
if [[ -z ${var:-} ]]; then die "var unset"; fi
if [[ -f $f && -r $f ]]; then ...; fi
if [[ ${#arr[@]} -gt 0 ]]; then ...; fi      # array is non-empty
```

## Arrays vs delimited strings

Default to an array. `"${arr[@]}"` preserves element boundaries exactly;
a space-delimited string loses them the moment a value contains a space, and
re-splitting it is where "one extra argument" bugs come from.

```bash
declare -a files=()
while IFS= read -r line; do files+=("$line"); done < input.txt
printf '%s\n' "${files[@]}"
printf '%q ' "${files[@]}"    # shell-escaped, for logs / xtrace

# associative array (bash 4+)
declare -A counts=()
counts[$user]=$(( ${counts[$user]:-0} + 1 ))
echo "${counts[$missing]:-default}"   # always use :- for maybe-missing keys
```

- `mapfile -t arr < file` is the fast, safe way to slurp lines (strips the
  newline for you; `-t` prevents backslash mangling).
- `${arr[*]}` joins with the first char of `IFS`; `${arr[@]}` is for
  expanding to separate arguments. Only use `[*]` for printing.
- `set -u` and empty arrays: bash 4.4+ treats `"${arr[@]}"` on an empty array
  as no-op (no unbound error). Bash 4.2/4.3 error. If you must support those,
  use `for x in "${arr[@]:-}"`.

## `read` and the backslash trap

`read` without `-r` eats backslashes and keeps leading/trailing IFS whitespace:

```bash
printf 'a  b\\c\n' | { read x;   echo "[$x]"; }   # [a  bc]  <-- wrong
printf 'a  b\\c\n' | { read -r x; echo "[$x]"; }  # [a  b\c] <-- right
```

**Always `read -r`.** `IFS= read -r` to get the whole line verbatim. With
multiple names, extra words land in the last variable (newlines and trailing
IFS stripped): `IFS=: read -r host port rest`.

## Traps, exit codes, and signals

- `trap ... EXIT` runs on normal exit, on `set -e` exit, and on `exit`. It
  does **not** run on SIGKILL (9) or on an un-trapped SIGINT if the child
  ignores it. Trap INT/TERM too if you have long children.
- An EXIT trap changes `$?`. **Capture it first** or the script exits 0 while
  "failing" — verified:
  ```bash
  cleanup() { local rc=$?; rm -rf "$work"; exit $rc; }   # without $?=..., rc is 0
  ```
  If you do not want to re-`exit`, `return` from the trap instead.
- `trap` is **not inherited by subshells** unless the shell was started with
  `-E` (`set -E` / `set -o errtrace`); `ERR` traps need `-E` too to fire
  inside functions and subshells.
- A child that ignores SIGINT (`trap "" INT`) makes Ctrl-C invisible to the
  parent. If a script is hard to kill, that is usually why.

```bash
work=$(mktemp -d)
trap 'rc=$?; rm -rf -- "$work"; exit $rc' EXIT
trap 'log "interrupted"; exit 130' INT TERM
```

- Exit codes: 0 success, 1 generic failure, 2 usage error, 126 not
  executable, 127 not found, 128+N killed by signal N. Return 130 from INT
  handling so callers know it was not a normal failure.
- `set -e` + a `trap ERR` handler: the handler must re-`exit`, or the script
  continues.

## Running from anywhere: `$0` vs `BASH_SOURCE`, and the PATH trap

A script that does `cd "$(dirname "$0")"` then calls `python` is broken: it
runs in the wrong directory *and* now has a project `bin/` shadowing your
tooling.

```bash
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
```
- `BASH_SOURCE[0]` is correct when **sourced**; `$0` is the caller's name then.
- `cd ... && pwd -P` resolves symlinks and *fails* if the `cd` fails — the
  `&&` is the point. `cd` alone in a `$( )` that then runs `pwd` prints the
  old directory on failure.
- Never `cd` in a script unless the whole job is directory-relative. Use
  absolute paths: `"$SCRIPT_DIR/config.toml"`.
- When you do need cwd, use `(cd "$dir" && cmd)` in a subshell rather than
  `cd`/`cd -` — no state leaks back, and `set -e` still applies.

## `getopts` for arguments

Hand-roll it; don't use a full CLI framework for a script.

```bash
verbose=0
outdir=./out
while getopts ":vho:" opt; do
  case $opt in
    v) verbose=1 ;;
    o) outdir=$OPTARG ;;
    h) usage; exit 0 ;;
    \?) printf '%s: unknown option -%s\n' "$PROG" "$OPTARG" >&2; exit 2 ;;
    :)  printf '%s: option -%s needs an argument\n' "$PROG" "$OPTARG" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
(( $# )) || { usage >&2; exit 2; }
```

Leading `:` in the optstring enables `?` and `:` cases instead of a silent
error message. `OPTIND` must be used with arithmetic expansion, and
`shift $((OPTIND-1))` before touching `"$@"`.

## Concurrent work: `xargs -P` and `wait`

```bash
printf '%s\0' "${files[@]}" | xargs -0 -P 8 -n 1 -I{} ./process-one {}
```
- `-0` (NUL-separated) is what makes spaces and newlines in filenames safe.
  `xargs -n1` runs the command once per argument — safe with weird filenames
  but slow (one exec each); batching with `-n 50` is much faster when each
  argument is small and safe.
- `xargs` exits **123** if any invocation exits 1-125, and **124** on timeout,
  **255** if the command itself is 255. With `set -e` a non-zero xargs is
  fatal — check `$?` if partial failure is acceptable.
- `cmd &` returns immediately; `$!` is the PID. **`wait` for all children
  before exiting** or an `EXIT` trap can run while children still write files.
  `wait -n` (bash 4.3+) returns on the first exit. Kill by PID: `kill "$!"`.

## `find` gotchas that matter

- `find` prints paths **newline-separated**, so a filename with a newline (or
  a leading `-`) breaks `-exec`. Use `-print0` + `xargs -0` for anything
  non-trivial.
- `-exec ... \;` forks **once per file**; `-exec ... +` batches many files
  into one fork. `-delete` avoids exec entirely and is far faster — but it
  only deletes in depth-first order and **implies `-depth`**, so it will
  happily delete the directory it is given. `find dir -name '*.tmp' -print
  -delete` is the safe form; pair it with `-print` so you can see what is
  about to go (and add `-print -quit` to test first).
- `find` is not affected by shell globs, so `find . -name '*.log'` is not
  equivalent to `./**/*.log` — the latter depends on your `globstar` setting
  and is a bashism.
- `-maxdepth 1` before `-name` on a delete, or you will descend into
  subdirectories and delete more than you meant.

## The checklist before you ship a script

Run `shellcheck` and read every message. Then:

- [ ] `set -euo pipefail` on line 2; `-E` if there is an `ERR` trap.
- [ ] Shebang is `#!/usr/bin/env bash` and the file is executable (`chmod +x`).
- [ ] Every `$var` and `$(...)` is quoted. Globs stay *outside* the quotes.
- [ ] `set -e` is not the only error handling in any function that can be
      called from an `if` — those return explicit statuses.
- [ ] Pipelines that assign in a loop use `< file` or `< <(...)`, not a pipe.
- [ ] `read` always has `-r`.
- [ ] Any temporary path has a `trap ... EXIT` that preserves `$?`.
- [ ] It works when invoked by absolute path from a different cwd, and via
      `cron` (minimal `PATH`, no TTY, `HOME` may differ).
- [ ] `--help` exists and exits 0.
- [ ] `bash -n script.sh` is clean, and one run against a fixture directory
      with a **space in its name** passes.

## Cron / CI / local are three different environments

- cron has a tiny `PATH` (`/usr/bin:/bin`). Use absolute paths, or set
  `PATH=/usr/local/bin:/usr/bin:/bin` at the top of the script.
- No TTY: anything that prompts (gpg passphrase, `git` credentials) will hang
  forever. Set `GIT_TERMINAL_PROMPT=0`.
- Exit code > 0 is the only signal cron/CI gives you. If cleanup or alerting
  matters, do it in the script's `EXIT` trap, not in the scheduler.
- Read `references/debugging-scripts.md` when a script works by hand and
  fails under cron/CI, or when a `set -e` script is silently doing the wrong
  thing.
