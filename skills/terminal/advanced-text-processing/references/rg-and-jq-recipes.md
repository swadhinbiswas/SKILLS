# rg / jq / sed / awk recipe table

Concrete task → exact command. Every command here is one you'd type.

## ripgrep

| Task | Command |
|---|---|
| search cwd, ignore VCS/build | `rg 'pat'` |
| line numbers | `rg -n 'pat'` |
| literal string (no regex) | `rg -F -n 'a.b.c'` |
| several literals | `rg -F -e 'foo bar' -e 'baz'` |
| several regexes | `rg -e 'FATAL' -e 'panic' -e 'OOM'` |
| case-insensitive | `rg -i 'error'` |
| whole word | `rg -w 'id'` |
| count matches per file | `rg -c 'ERROR' logs/` |
| count matches, NUL-safe file list | `rg -c0 'ERROR'` |
| only filenames | `rg -l 'TODO'` |
| search hidden files too | `rg --hidden -g '!.git' 'pat'` |
| search ignored files (vendor, build) | `rg --no-ignore-vcs -n 'pat'` |
| everything, including binaries | `rg -uuu -n 'pat'` |
| only certain file types | `rg -n 'pat' -g '*.py' -g '*_test.go'` |
| exclude a path | `rg -n 'pat' -g '!vendor/**' -g '!*.min.js'` |
| match only, not whole line | `rg -o 'user_id=[0-9]+'` |
| unique extracted values | `rg -o '[0-9]{4}-[0-9]{2}-[0-9]{2}' f \| sort -u` |
| show 2 lines of context | `rg -n -C2 'panic'` |
| search stdin / a pipe | `rg 'pat' -` or `rg -n 'pat' /dev/stdin < f` |
| read patterns from a file | `rg -f patterns.txt` |
| read from a binary/huge file as text | `rg -a -n 'pat' file` |
| cap search depth | `rg -n 'pat' --max-depth 2` |
| follow symlinks | `rg -n 'pat' --follow` |
| print files without searching | `rg --files -g '*.ts' \| head` |
| machine-readable hits | `rg --json 'pat' f \| jq ...` |
| timing / coverage of the search | `rg --stats 'pat'` |

`rg --json` emits one object per event with `type` in
`begin`/`match`/`context`/`end`/`summary`. Useful filters:

```bash
# count matches per file from rg itself
rg --json 'ERROR' logs/ | jq -r 'select(.type=="match") | .data.path.text' | sort | uniq -c | sort -rn

# just the matched substrings
rg --json 'user_id=[0-9]+' access.log | jq -r 'select(.type=="match") | .data.lines.matches[].submatches[].match'

# first match only, stop early
rg --json -m1 'FATAL' huge.log | jq -r 'select(.type=="match") | .data.lines.text' | head -1
```

## jq — query

| Task | Command |
|---|---|
| pretty | `jq . f.json` |
| compact, one per line (stream) | `jq -c . ndjson.json` |
| raw strings, no quotes | `jq -r '.name' f.json` |
| top-level key | `jq '.data.items' f.json` |
| safe drill-down (no error if absent) | `jq '.data?.items?[]?' f.json` |
| array of strings out | `jq -r '.items[].id' f.json` |
| filter + project, TSV | `jq -r '.[] \| select(.p>100) \| [.id,.name] \| @tsv' f.json` |
| CSV output | `jq -r '[.id,.name] \| @csv' f.json` |
| combine files | `jq . a.json b.json` |
| slurp many into one array | `jq -s '.' *.json` |
| sum an array of objects | `jq '[.[].amount] \| add' f.json` |
| sum safely (missing → 0) | `jq '[.[]? \| (.amount // 0)] \| add // 0' f.json` |
| group and count | `jq -r 'group_by(.cat)[] \| "\(.[0].cat)\t\(length)"' f.json` |
| count values like uniq -c | `jq -r 'group_by(.)[] \| "\(length)\t\(.[0])"' arr.json \| sort -rn` |
| top N by field | `jq -r 'sort_by(-.amt)[:5][] \| .name' tx.json` |
| unique by key | `jq 'unique_by(.id)' f.json` |
| keys / values | `jq 'keys' f.json` / `jq 'map_values(.x)' f.json` |
| find duplicates by key | `jq -r 'group_by(.id)[] \| select(length>1) \| .[0].id' f.json` |
| delete keys | `jq 'del(.password,.token)' user.json` |
| keep only some keys | `jq 'with_entries(select(.key \| test("^(id|name)$")))' f.json` |
| defaults (null/absent → value) | `jq '.name // "anon"' f.json` |
| test a key exists | `jq 'has("id")' f.json` |
| filter by regex on a field | `jq -r '.[] \| select(.name \| test("^ab")) \| .id' f.json` |
| add a field | `jq '. + {loaded: true}' f.json` |
| reshape | `jq '{n:(.items\|length), ids:[.items[].id]}' f.json` |
| flatten nested | `jq '[.. \| objects \| .v? \| select(.!=null)]' f.json` |
| count leaves | `jq '[paths(scalars)] \| length' f.json` |
| delete nulls | `jq 'walk(if . == null then empty else . end)' f.json` |
| to TSV for a spreadsheet | `jq -r '(["id","name"] \| @tsv), (.[] \| [.id,.name] \| @tsv)' f.json` |

### jq — the null/false gotchas, side by side

```bash
echo '{"ok": false, "name": null}' | jq '{ok_a: .ok // "default", ok_b: (if .ok then 1 else 0 end)}'
# {
#   "ok_a": "default",     <-- // collapses false!  do NOT use // for booleans
#   "ok_b": 0              <-- correct
# }

echo '{"a": {"b": 1}}' | jq '.a.b, .a.c, .zz.y'
# 1
# null
# null            <-- absent is null; no error

echo '{"a": 1}' | jq 'has("a"), has("b")'
# true
# false
```

## sed

| Task | Command |
|---|---|
| strip leading/trailing whitespace | `sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' f` |
| delete comments and blanks | `sed -E '/^[[:space:]]*#/d; /^[[:space:]]*$/d' f` |
| date reorder (ISO → DMY) | `sed -E 's/([0-9]{4})-([0-9]{2})-([0-9]{2})/\3\/\2\/\1/g' f` |
| extract a field, comma separated | `sed -E 's/^([^,]*),.*/\1/' f` |
| extract the last field | `sed -E 's/^.*,//' f` |
| swap separator | `sed 's/,/\t/g' f.csv` |
| in-place edit with a backup | `sed -i.bak -E 's/foo/bar/g' f` |
| print a range between markers | `sed -n '/BEGIN/,/END/p' f` |
| only lines 10-20 | `sed -n '10,20p' f` |
| count lines | `sed -n '$=' f` |
| delete the last line | `sed '$d' f` |
| duplicate each line twice | `sed 'p' f` |
| prefix a number to each line | `sed 's/^/ROW /' f` |
| only replace 2nd occurrence | `sed -E 's/a/X/2' f` |
| transliterate chars | `sed 'y/abc/ABC/' f` |
| strip CR (Windows) | `sed 's/\r$//' f` |
| template substitution | `sed -E "s/\{\{name\}\}/$name/g" tpl.html` |

## awk

| Task | Command |
|---|---|
| sum a column (skip header) | `awk -F, 'NR>1{s+=$5}END{print s}' d.csv` |
| average | `awk -F, 'NR>1{s+=$5;n++}END{print s/n}' d.csv` |
| count rows matching a condition | `awk -F, 'NR>1 && $3>100{n++}END{print n+0}' d.csv` |
| group-by count | `awk -F, 'NR>1{c[$2]++}END{for(k in c)print c[k],k}' d.csv \| sort -rn` |
| group-by sum | `awk -F, 'NR>1{s[$2]+=$3}END{for(k in s)print s[k],k}' d.csv` |
| top value of a column | `cut -d, -f2 d.csv \| tail -n+2 \| sort \| uniq -c \| sort -rn \| head` |
| last field | `awk '{print $NF}' f` |
| 2nd-to-last field | `awk '{print $(NF-1)}' f` |
| split a line into fields on whitespace | `awk '{print $3}' f` |
| filter by regex on a field | `awk '$3 ~ /^sshd/ {print $1}' /etc/passwd` |
| left join on column 1 | `awk -F, 'NR==FNR{a[$1]=$2;next}{print $1,a[$1]}' l.csv r.csv` |
| add a column to every line | `awk -F, 'BEGIN{OFS=","}{$NF="new";print}' f.csv` |
| csv → tsv | `awk -F, 'BEGIN{OFS="\t"}{$1=$1;print}' f.csv` |
| change field 3 in place | `awk -F, 'BEGIN{OFS=","}{$3="X";print}' f.csv > out.csv` |
| skip malformed rows (wrong field count) | `awk -F, 'NF==6' good.csv` |
| reformat a timestamp field | `awk -F, 'BEGIN{OFS=","}{$1=$1"Z";print}' f.csv` |
| print every 10th line | `awk 'NR%10==0' f` |
| line number + content | `awk '{print NR": "$0}' f` |
| bytes per line histogram | `awk '{print length($0)}' f \| sort -n \| uniq -c` |

## Joining / comparing files

| Task | Command |
|---|---|
| common lines (both files) | `comm -12 <(sort a) <(sort b)` |
| only in A | `comm -23 <(sort a) <(sort b)` |
| only in B | `comm -13 <(sort a) <(sort b)` |
| side-by-side by line number | `paste -d, a.csv b.csv` |
| inner join on key (sorted) | `join -t, -1 1 -2 1 <(sort -t, -k1,1 a.csv) <(sort -t, -k1,1 b.csv)` |
| left join, fill missing with a value | `join -t, -1 1 -2 1 -a1 -e "(none)" -o 0,1.2,2.2 <(sort …a) <(sort …b)` |
| check every row of b has a match in a | `awk -F, 'NR==FNR{a[$1]=1;next} !($1 in a){print "orphan:",$1}' a.csv b.csv` |

## Count / dedupe / reshape CSVs

```bash
# header + column count sanity
head -1 d.csv; awk -F, 'NR==1{print NF" columns"}' d.csv

# true row count (handles missing trailing newline)
awk 'END{print NR}' d.csv

# unique values of a column, most common first
tail -n+2 d.csv | cut -d, -f3 | sort | uniq -c | sort -rn

# collapse whitespace runs
tr -s ' ' < f | sed -E 's/^ //'

# csv -> tab separated (no quoted fields assumed)
sed 's/,/\t/g' d.csv

# strip CR from every line
tr -d '\r' < d.csv > clean.csv
```
