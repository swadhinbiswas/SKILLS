---
name: advanced-text-processing
description: Extract, reshape, and count over real data with ripgrep, jq, sed, and awk instead of fragile grep/awk one-liners - regex search, JSON filtering and reshaping, and stream joins. Use when a log, API response, or CSV needs to be mined, when jq returns null or "no output", when counting occurrences, reformatting JSON, or joining two files. Triggers on "ripgrep", "rg", "jq", "grep -r", "sed", "awk", "parse this JSON", "count matches", "reformat JSON", "join two files".
compatibility: ripgrep (rg) 13+, jq 1.6+. Tests with python3 -m unittest discover -s tests from the skill directory. gawk-only features are labelled.
metadata:
  version: "1.0"
---

# Advanced Text Processing

The shape of the job decides the tool. Reach for the right one first; the
one-liner war stories come from using `grep` for JSON or `jq` for "which
lines contain X".

| You want to | Use | Not |
|---|---|---|
| find/filter text fast, search code | `rg` (ripgrep) | `grep -r` |
| query, reshape, aggregate JSON | `jq` | `grep` on JSON |
| substitute / delete / extract one part of a line | `sed -E` | `awk` |
| column-ish work, group and total, multi-file joins | `awk` | `cut` |
| count occurrences of a literal string | `rg -c`, `grep -c`, or `jq length` | `wc -l` after `tr` |

## ripgrep (`rg`) — fast, correct search

`rg` is the default search tool. It respects `.gitignore`, skips hidden files
by default, is parallel, and has PCRE2 available.

```bash
rg 'pattern'                     # search cwd recursively
rg -n 'pattern' file.log         # -n line numbers (do this by default)
rg -i 'error'                    # case-insensitive
rg -w 'id'                       # whole word only
rg -F 'a.b.c'                    # fixed string, NO regex at all — always -F for literals
rg -c 'ERROR'                    # count per file  (great for triage)
rg --stats 'ERROR'               # counts + timing + bytes searched
```

### The four flags that beat `grep -r`

```bash
rg --hidden -g '!.git'            # include dotfiles (excl .git) — grep -r searches .git
rg -u --no-ignore-vcs .           # search ignored + binary files too (unsafe/slow)
rg -uuu                           # everything: hidden + ignored + binary
rg -l 'TODO'                      # files with matches only
rg -c0 'ERROR' | sort ...         # counts, NUL-safe
```

`grep -r .` walks `.git`, `node_modules`, and build output, which is why it
is slow and returns noise. `rg` excludes all of it by default. **If a `rg`
search returns "nothing",** first check whether the file is ignored:
`rg --no-ignore -n 'x' path` to see if the tool or your `.gitignore` is hiding
it. The reverse gotcha: `rg` finds nothing in a file a `grep` found, when the
file has no extension and is not tracked — add `-g '*.conf'` or `--no-ignore-vcs`.

### Multiple patterns, and getting them right

```bash
rg -e 'FATAL' -e 'panic' -e 'OOM'        # -e = alternative patterns
rg 'FATAL|panic|OOM'                       # same, but | must be escaped for fixed-strings
rg -f patterns.txt                         # one pattern per line from a file
rg -F -e 'let x = 1' -e 'a | b'            # multiple fixed strings (| stays literal)
```

Use `-F/--fixed-strings` whenever the pattern is a literal — it is faster and
it eliminates the single most common bug: a literal `.`, `[`, `(`, `*`, or
`|` in your "string" being interpreted as regex.

### Glob which files to search (the `-g` flag)

```bash
rg -n 'TODO' -g '*.py'                     # only .py files
rg -n 'TODO' -g '!tests/**' -g '*.py'      # python, but not tests/
rg -n 'func' -g 'src/**'                   # only under src/
rg -n 'x' -g '*.{ts,tsx,js}'               # brace glob
rg -n 'x' -g '*_test.go'                   # suffix
```

Order matters: an include glob is a whitelist, an exclude (`!`) is a
blacklist. `rg 'x' -g '*.py' -g '!vendor/**'` = python outside vendor.

### Output: JSON, count, context, and replace

```bash
rg --json 'ERROR' log.txt | jq -r '.data.lines.text'    # machine-readable hits
rg -o 'user_id=[0-9]+' access.log                       # only the match, not the line
rg -o '[0-9a-f]{8}-[0-9a-f]{4}' | sort -u               # extract unique
rg -r '' -g '*.tmp' -l .                               # ripgrep's in-place replace (with -l)
rg -N --color=never                                      # no line numbers, no color (for | pipes)
```

For **editing** files, `rg` alone is not the right tool — use the language's
own formatter or `sed -i` with a backup. `rg -r` writes in place only when
combined with `-l`/`--files-with-matches`; treat it as dangerous and preview
with `rg -n` first.

`rg --json` is genuinely useful when you need to post-process hits (count
per file, extract only matches, batch them). Full example in
`references/rg-and-jq-recipes.md`.

### When `grep` is still right

- Reading from a pipe or `/dev/stdin` where `rg` can't recurse: `rg pattern -` 
  (or plain `grep`, both fine).
- `grep -q` in a conditional (no need for `rg`'s features, and `rg -q` exits
  0/1 the same but `grep` is on every minimal image).
- Non-recursive, tiny, on a busybox/embedded system.

## `jq` — the mental model

jq is a small language. Almost every filter is one of: a **path
expression**, a **constructor** that builds a value, or a **function over
`.`** (the current value). The core operators:

| Goal | Filter |
|---|---|
| field | `.name`, `.a.b.c` |
| safe field | `.name?` (no error if missing) |
| iterate array | `.[]` |
| iterate object values | `.[]` (values only), `to_entries[]` for keys too |
| array → stream, or build | `.[]?` |
| length | `length` (array/object/string) |
| filter each element | `map(.price)` |
| filter and keep | `map(select(.active))` |
| filter and flatten | `[.[] | select(.x)] \| flatten` |
| conditionals | `if .n > 5 then "big" else "small" end` |
| fallbacks | `.a // "default"` (also handles `false`/`null`/`empty`!) |
| test | `test("re")`, `select(.name \| test("^ab"))` |
| group | `group_by(.region)`, or `group_by(.k) \| map({k: .[0].k, n: length})` |
| unique | `unique`, `unique_by(.id)`, `unique_by(.id, .v)` |
| sort | `sort`, `sort_by(.ts)`, `sort_by(-.amount)` |
| reduce | `reduce .[] as $x (0; . + $x.amount)` |
| build object | `{id: .id, name: .name}` |
| merge | `.[0] * .[1]`, or `reduce .[] as $i ({}; . * $i)` |
| pipe | `A \| B` = feed A's output into B |
| array-ify | `[...]` collects a stream into an array |
| keys | `keys`, `keys_unsorted`, `with_entries(.value = .value.x)` |
| to/from CSV | `@csv`, `@tsv` (with `-r` for raw strings) |
| read NDJSON | `jq -s` (slurp) or `jq -c` streaming |
| string ops | `split(sep)`, `join(sep)`, `ltrimstr`, `sub("re";"rep")`, `gsub`, `ascii_downcase` |
| output modes | `jq` pretty, `jq -c` compact (one line, for streaming), `jq -r` raw strings (no quotes) |
| multiple outputs | `jq '.a, .b'` (comma prints both) |
| read from files | `jq . a.json b.json`, `jq -s 'add' *.json` (slurp all, combine) |

### The `null` vs missing gotcha (the one that bites)

In jq, a missing field returns `null`, and `null == false` is a trap:

```bash
echo '{"a": null}' | jq '.a.b'      # null  (no error, but not "not found")
echo '{}'              | jq '.a.b'    # null
echo '{}'              | jq '.a'      # null
echo '{}'              | jq 'has("a")' # false
```

But `//` treats **`false` and empty as missing** too:

```bash
echo '{"ok": false}' | jq '.ok // "fallback"'   # "fallback"  <-- WRONG for booleans!
echo '{"ok": false}' | jq 'if .ok then 1 else 0 end'   # 0  <-- right
```

So: use `//` for defaults where `false`/`null`/absent should all collapse to
the default. If `false` is a meaningful value, test it explicitly
(`if has("x") and .x != null then ...`). Use `has("k")` to distinguish
"key absent" from "key present but null". `select(.x)` drops rows where `x` is
`null`/`false`/absent — surprising when you wanted "has a non-null x" (it
matches, good) vs "x is truthy" (it matches, by design).

### Practical jq recipes

Extract, one per line:
```bash
jq -r '.items[] | .id' resp.json
```

Filter then project (the common one):
```bash
jq -r '.items[] | select(.price > 100 and .in_stock) | "\(.id)\t\(.name)"' resp.json
```

Group and count:
```bash
jq -r '.items | group_by(.category)[] | "\(.[0].category)\t\(length)"' resp.json
```

Total revenue, safely (if `items` missing or items lack `price`):
```bash
jq '[.items[]? | (.price // 0)] | add // 0' resp.json
```

Reformat / reshape:
```bash
jq '{generated: (now | todateiso8601), count: (.items | length), ids: [.items[].id]}' in.json > out.json
```

Flatten nested one level, drop nulls:
```bash
jq '[.. | objects | .value? | select(. != null)]' in.json
```

Find duplicates by key:
```bash
jq -r 'group_by(.id)[] | select(length > 1) | .[0].id' items.json
```

Count values in an array (like `uniq -c`):
```bash
jq -r 'group_by(.)[] | "\(length)\t\(.[0])"' arr.json | sort -rn
```

Top-N:
```bash
jq -r 'sort_by(-.amount) | .[:5][] | "\(.name): \(.amount)"' tx.json
```

Delete keys / keep keys:
```bash
jq 'del(.password, .token)' user.json
jq 'with_entries(select(.key | startswith("_")))' resp.json
```

Robust field access across mixed shapes (the null-safe drill-down):
```bash
jq -r '.data? // .result? // .[]? | .name? // "?"' api.json
```

Streaming NDJSON (one JSON object per line) — **do not** `jq .` without `-c`
on a big file; it buffers a stream and can be slow/memory heavy. Use:
```bash
jq -c 'select(.level=="error") | {ts, msg}' huge.ndjson
```

`jq` errors are good: `parse error: Expected ... at line 3, column 5` tells
you exactly where. If you get `null` where you expected an object, you almost
always piped an array but used `.field` instead of `.[] | .field`.

Full recipe table (including `jq --stream`, `@base64d`, `join` recipes,
and a `rg --json` pipeline) in `references/rg-and-jq-recipes.md`.

## `sed` — one substitution, one extraction

`sed` is for *editing lines in a stream*. Use `sed -E` (ERE) by default;
`\d`, `?`, lazy quantifiers and word boundaries need `-P` (GNU/BSD PCRE).

```bash
sed -E 's/^[[:space:]]+//' f                 # strip leading whitespace
sed -E 's/([0-9]{4})-([0-9]{2})-([0-9]{2})/\3\/\2\/\1/' f   # reorder with groups
sed -E '/^\s*#/d; /^$/d' f                  # delete comments and blank lines
sed -n '/START/,/END/p' f                   # print a range (inclusive)
sed 's/x/y/g' old > new                      # in place? use -i.bak (creates new.bak)
sed -E 's/([^,]*),([^,]*),([^,]*)/\3|\2|\1/'    # CSV cols -> pipe
```

- `-i` is in-place and its argument is a **suffix**: `sed -i.bak 's/a/b/' f`
  leaves `f.bak`. GNU and BSD differ historically; the suffix form works
  everywhere.
- `s///` replaces the *first* match per line; `s///g` for all; `s///2` for
  the second.
- `y/abc/ABC/` transliterates a character set (fast tr alternative).
- In the replacement, `&` is the whole match, `\1` is a group. In the
  *pattern* `\d` is a digit; in the replacement `\d` is not portable — use
  `&` and groups.
- `[[:space:]]`, `[[:digit:]]`, `[[:alpha:]]` are portable; `\s`, `\w`, `\d`
  work in GNU sed and in POSIX character classes are safer. Use `-E`.

## `awk` — columns, groups, joins

awk's real power is: a field split (`FS`), a per-line rule, a per-file
`FNR==NR` idiom, and `END`. It is the right tool the moment you need
"group by a column and sum another".

```bash
awk -F, 'NR>1 {sum+=$5; n++} END {print "avg", sum/n, "over", n, "rows"}' data.csv
awk -F, 'NR>1 {c[$2]++} END {for (k in c) print c[k], k}' data.csv | sort -rn
awk '{print $1}' file | sort | uniq -c | sort -rn | head   # top values of col 1
awk -F: '$3 ~ /^sshd/ {print $1}' /etc/passwd              # filter by regex
awk -F, 'NR==FNR {map[$1]=$2; next} {print $0, map[$1]}' a.csv b.csv   # left-join on col 1
```

Column-ish fields:
- `-F,` sets the separator; `-F'\t'` or `-F'\s+'` for whitespace. Prefer
  `FS=","` and use `$1`..`$NF`; for whitespace `awk '{print $3}'`.
- `NF` = number of fields; `$NF` = last field. `$(NF-1)` = second to last.
- `NR` = total record number, `FNR` = record number within the current file.
  `NR==FNR` is the "first file" idiom; it breaks when the first file is empty
  (then it reads the second) — guard with `FILENAME==ARGV[1]`.

Built-ins that come up: `length`, `substr(s,start,len)` (1-indexed!), `index`,
`split`, `toupper/tolower`, `gsub`, `match`/`RSTART`/`RLENGTH`, `system`,
`printf`, `int`, `exit`, `BEGIN{}`/`END{}`. `gawk` adds `length(array)`,
`asort`, `strftime`, `gensub` — label them or stay portable.

**awk gotchas:**
- `substr` is 1-indexed; `substr("hello",0,3)` is `hel`, and awk is not
  forgiving like Python.
- `print` adds `\n`; use `printf "%s\t%s\n", a, b` for control.
- Uninitialised field is `""` and `"" + 1 == 1` — a missing column silently
  becomes 0 in a sum. Filter with `NF==expected` first if it matters.
- `getline` from a command inside awk (`"cmd" | getline`) runs the command
  once per input line by accident if not cached; prefer `cmd | getline
  arr[NR]` in `BEGIN`.

## Joining files

`join` needs sorted input; `paste` puts them side by side; awk is general:

```bash
join -t, -1 1 -2 1 -a1 -a2 -e '(none)' -o 0,1.2,2.2 <(sort -t, -k1,1 left.csv) <(sort -t, -k1,1 right.csv)
# left-join, left file, col 2 of each side, missing = "(none)"
paste -d, a.csv b.csv                       # line i of each, side by side
awk 'NR==FNR{a[$1]=$2;next}{print $1,a[$1]}' left.csv right.csv   # simple left join
comm -12 <(sort a) <(sort b)                # lines in both files
comm -23 <(sort a) <(sort b)                # in A only
```

## When to reach for a real language

awk/jq/sed are for *terminal-length* work: logs, one-off analysis, quick
transformation of a file you can eyeball. The moment you need multi-pass
logic, joins across many files, a schema, or something that will be re-run,
write a 20-line Python/pandas/duckdb script. Do not build a 200-character awk
one-liner you cannot read.

## Gotchas

- **`wc -l` counts newlines, not records.** A file with no trailing newline
  undercounts by one: `awk 'END{print NR}' file` is the record count.
- **Locale changes sort order.** `sort` under `LC_ALL=C` is byte order (fast,
  stable, correct for `LC_ALL=C`-style dedupe); under `en_US.UTF-8` it ignores
  punctuation. Pick one and export `LC_ALL=C` for reproducibility in scripts.
- **`grep -c` counts matching *lines*, not matches.** `grep -o pattern | wc -l`
  or `rg -o -c` counts occurrences.
- **Binary files.** `grep` prints `Binary file matches` and exits 0. `rg`
  skips them; add `-a`/`--text` (grep) or `-a` (rg) to force.
- **Windows line endings** break `awk -F,` on the last column and leave `\r`
  in joins. Pipe through `tr -d '\r'` first.
- **`IFS` in read loops** and quoting — see `shell-scripting-robustness` in
  the `terminal` domain. A `while read` fed by a pipeline loses variables set
  in the loop body (subshell).

## Checklist

- [ ] Used `rg` (not `grep -r`) for recursive search; `-F` for literal
      patterns; `-n` for line numbers.
- [ ] Chose `jq` for anything JSON, `sed` for single-line edits, `awk` for
      column/group/join work.
- [ ] Handled the `null`-vs-missing and `false`-as-`//`-fallback cases in jq.
- [ ] Counted occurrences with the right tool (occurrences, not lines).
- [ ] Pinned `LC_ALL` and stripped `\r` where the input is machine-generated.
- [ ] Checked that the output has a trailing newline and the expected column
      count before piping it onward.

## Files

- `references/rg-and-jq-recipes.md` — a task → exact command table covering
  `rg` flags, `jq` one-liners, `rg --json` post-processing, and stream joins.
- `scripts/jsonlint.py` — validates one or many JSON files (or stdin) and
  points at the first byte/line/column of each error; stricter than `jq` for
  things jq accepts (e.g. non-standard NaN handling, trailing commas, BOM).
  Stdlib only; `python3 scripts/jsonlint.py --help`. Tested with
  `python3 -m unittest discover -s tests` from the skill directory.
