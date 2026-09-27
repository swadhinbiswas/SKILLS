#!/usr/bin/env bash
# install.sh — install these Agent Skills into any coding CLI or desktop app.
#
# Two jobs:
#   1. Register THIS repo as a skill source in each client (usually one symlink,
#      one opencode config entry, or nothing for clients that scan a folder).
#   2. Optionally pull skills from a GitHub repo into this collection.
#
# Design: skills live in ONE place (this repo, or a local checkout). Clients get
# a symlink or a config pointer, never a copy, so an edit here shows up
# everywhere immediately and there is nothing to drift.
#
# Quick start:
#   ./install.sh                      # detect clients, wire up all detected
#   ./install.sh --list               # show clients, what's detected, what would run
#   ./install.sh --client claude      # only Claude Code
#   ./install.sh --all                # every known client, detected or not
#   ./install.sh --client foo=/p/to/skills  # register a custom skills dir
#   ./install.sh --verify             # check the wiring, don't change anything
#   ./install.sh --uninstall          # remove the links/entries we added
#
#   # bring a skill in from the internet
#   ./install.sh --from obra/superpowers --skill brainstorming
#   ./install.sh --from owner/repo --skill skills/pdf --domain docs
#
# Nothing is ever overwritten without --force. Nothing outside $HOME and this
# repo is touched. Every action is printed before it happens, and --dry-run
# shows the whole plan without changing a thing.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$REPO_ROOT/skills"
HOME_DIR="$HOME"
IS_DRY_RUN=0
DO_UNINSTALL=0
DO_VERIFY=0
DO_LIST=0
FORCE=0
SELECTED_CLIENTS=()
CUSTOM_TARGETS=()
FROM_REPO=""
FROM_SKILL=""
FROM_DOMAIN=""
LINK_MODE="symlink"   # symlink | copy

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
YELLOW=$'\033[33m'; BLUE=$'\033[34m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
[ -t 1 ] || { BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; CYAN=""; RESET=""; }

info()  { printf '%s\n' "${DIM}$*${RESET}"; }
ok()    { printf '%s\n' "${GREEN}✓${RESET} $*"; }
warn()  { printf '%s\n' "${YELLOW}!${RESET} $*"; }
err()   { printf '%s\n' "${RED}✗${RESET} $*" >&2; }
step()  { printf '%s\n' "${CYAN}→${RESET} $*"; }
head1() { printf '\n%s\n\n' "${BOLD}$*${RESET}"; }

die() { err "$*"; exit 1; }

run() {
  if [ "$IS_DRY_RUN" -eq 1 ]; then
    printf '%s\n' "${DIM}  [dry-run] $*${RESET}"
  else
    "$@"
  fi
}

# --------------------------------------------------------------------------
# Client registry
#
# kind:
#   dir   - client scans a directory of skills; we symlink each skill in
#   path  - client scans a parent directory that must CONTAIN our skills
#           (opencode scans skills.paths for */SKILL.md)
#   config- client is configured by editing a config file (opencode.jsonc)
# --------------------------------------------------------------------------
client_def() {
  # $1=id $2=label $3=kind $4=path (supports ~) $5=detector (command or path)
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5"
}

CLIENTS=()
load_clients() {
  CLIENTS=(
    # id            label              kind     path                          detector
    "$(client_def claude-code  'Claude Code'      dir    ~/.claude/skills         claude)"
    "$(client_def claude-desktop 'Claude Desktop' dir    ~/.claude/skills         claude-desktop)"
    "$(client_def codex         'OpenAI Codex'     dir    ~/.codex/skills          codex)"
    "$(client_def opencode      'opencode'         config ~/.config/opencode/opencode.jsonc opencode)"
    "$(client_def gemini-cli    'Gemini CLI'       dir    ~/.gemini/skills          gemini)"
    "$(client_def crush         'Crush'            dir    ~/.config/crush/skills    crush)"
    "$(client_def windsurf      'Windsurf'         dir    ~/.codeium/windsurf/skills windsurf)"
    "$(client_def cursor        'Cursor'           dir    ~/.cursor/skills          cursor)"
    "$(client_def amp           'Amp'              dir    ~/.config/amp/skills      amp)"
    "$(client_def goose         'Goose'            dir    ~/.config/goose/skills    goose)"
    "$(client_def aider         'Aider'            dir    ~/.aider/skills          aider)"
    "$(client_def zed           'Zed'              dir    ~/.config/zed/skills      zed)"
    "$(client_def agents-md     'Generic .agents'  dir    ~/.agents/skills          :)"
    "$(client_def project       'This project only' dir   .claude/skills            :)"
  )
}

expand_path() { echo "${1/#\~/$HOME_DIR}"; }

client_detected() {
  local detector="$1"
  case "$detector" in
    :|:*) return 0 ;;  # always available
    *) command -v "$detector" >/dev/null 2>&1 ;;
  esac
}

client_field() { printf '%s' "$1" | cut -f"$2"; }

# --------------------------------------------------------------------------
# Arg parsing
# --------------------------------------------------------------------------
usage() {
  cat <<'EOF'
install.sh — install these Agent Skills into any coding CLI or desktop app.

USAGE
  ./install.sh [options]

TARGET SELECTION
  --client <id>        Wire up one client (repeatable). --list shows ids.
  --all                Wire up every known client, detected or not.
  --client <id>=<path> Register a custom skills directory as a new client.
  --link-mode <mode>   symlink (default) or copy.
  --no-detect          Ignore which CLIs are installed; do exactly what's asked.

ACTIONS
  (default)            Detect clients and wire up the ones that are present.
  --list               Show clients, detection status, and plan. Change nothing.
  --verify             Verify existing wiring. Change nothing.
  --uninstall          Remove symlinks and config entries we added.

FROM GITHUB
  --from <owner/repo>  Clone a GitHub repo and pull skills from it.
  --skill <path>       Path to a skill inside that repo (repeatable).
                       Omit to auto-discover every */SKILL.md.
  --domain <name>      Domain subfolder to place them under (default: imported).

SAFETY
  --dry-run            Print every action, change nothing.
  --force              Overwrite existing non-symlink entries.
  -h, --help           This help.

EXAMPLES
  ./install.sh
  ./install.sh --list
  ./install.sh --client claude-code --client codex
  ./install.sh --client editors=/Users/me/.config/myeditor/skills
  ./install.sh --from obra/superpowers --skill brainstorming
  ./install.sh --from owner/repo --domain databases
  ./install.sh --verify
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --client=*)      CUSTOM_TARGETS+=("${1#--client=}"); shift ;;
    --client)
      [ $# -ge 2 ] || die "--client needs an argument"
      case "$2" in
        *=*) CUSTOM_TARGETS+=("$2") ;;
        *)   SELECTED_CLIENTS+=("$2") ;;
      esac
      shift 2 ;;
    --all)          SELECTED_CLIENTS+=("__all__"); shift ;;
    --no-detect)    SELECTED_CLIENTS+=("__nodetect__"); shift ;;
    --link-mode=*)  LINK_MODE="${1#--link-mode=}"; shift ;;
    --link-mode)    [ $# -ge 2 ] || die "--link-mode needs an argument"
                    LINK_MODE="$2"; shift 2 ;;
    --from=*)       FROM_REPO="${1#--from=}"; shift ;;
    --from)         [ $# -ge 2 ] || die "--from needs an argument"
                    FROM_REPO="$2"; shift 2 ;;
    --skill=*)      FROM_SKILL="${1#--skill=}"; shift ;;
    --skill)        [ $# -ge 2 ] || die "--skill needs an argument"
                    FROM_SKILL="$2"; shift 2 ;;
    --domain=*)     FROM_DOMAIN="${1#--domain=}"; shift ;;
    --domain)       [ $# -ge 2 ] || die "--domain needs an argument"
                    FROM_DOMAIN="$2"; shift 2 ;;
    --list)         DO_LIST=1; shift ;;
    --verify)       DO_VERIFY=1; shift ;;
    --uninstall)    DO_UNINSTALL=1; shift ;;
    --dry-run|-n)   IS_DRY_RUN=1; shift ;;
    --force)        FORCE=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *)              err "unknown option: $1"; usage >&2; exit 1 ;;
  esac
done

load_clients

# --------------------------------------------------------------------------
# Config patching (opencode, and anything else JSON-with-skills)
# --------------------------------------------------------------------------

# Strip // and /* */ comments so jsonc parses as json.
strip_jsonc() {
  python3 - "$1" <<'PY'
import sys

text = open(sys.argv[1], encoding="utf-8").read()
out = []
i, n = 0, len(text)
in_string = False
while i < n:
    ch = text[i]
    if in_string:
        out.append(ch)
        if ch == '\\' and i + 1 < n:
            out.append(text[i + 1]); i += 2; continue
        if ch == '"':
            in_string = False
        i += 1; continue
    if ch == '"':
        in_string = True; out.append(ch); i += 1; continue
    if ch == '/' and i + 1 < n:
        if text[i + 1] == '/':                      # line comment
            while i < n and text[i] != '\n':
                i += 1
            continue
        if text[i + 1] == '*':                      # block comment
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2
            continue
    out.append(ch); i += 1
sys.stdout.write(''.join(out))
PY
}

opencode_has_path() {
  local file="$1" want="$2"
  [ -f "$file" ] || return 1
  strip_jsonc "$file" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
paths = (data.get('skills') or {}).get('paths') or []
sys.exit(0 if '$want' in paths else 1)
"
}

opencode_add_path() {
  # Text-level edit, not a JSON re-dump: preserves comments, key order, and
  # the author's formatting. Falls back to a rewrite only if the anchor is
  # missing (e.g. a config with no "skills" key at all).
  local file="$1" want="$2"
  python3 - "$file" "$want" <<'PY'
import json, re, sys

path, want = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()

# Already present? Nothing to do.
if want in text:
    sys.exit(0)

# Case 1: a "paths" array already exists -> append one entry to it.
m = re.search(r'("paths"\s*:\s*\[)([^\]]*)(\])', text)
if m:
    body = m.group(2).rstrip()
    entry = json.dumps(want)
    if not body.strip():
        new_body = f"\n      {entry}\n    "
    elif body.rstrip().endswith(','):
        new_body = f"{body}\n      {entry}\n    "
    else:
        new_body = f"{body},\n      {entry}\n    "
    text = text[:m.start()] + m.group(1) + new_body + m.group(3) + text[m.end():]
    open(path, "w", encoding="utf-8").write(text)
    sys.exit(0)

# Case 2: a "skills" object exists but has no "paths" -> add the key.
# The object may be empty ({}), in which case the inserted entry must NOT be
# followed by a trailing comma.
m = re.search(r'("skills"\s*:\s*\{)(\s*)\}', text)
if m:                                          # "skills": {}  -> no comma
    insert = f'\n    "paths": [\n      {json.dumps(want)}\n    ]'
    text = text[:m.start()] + m.group(1) + insert + m.group(2) + '}' + text[m.end():]
    open(path, "w", encoding="utf-8").write(text)
    sys.exit(0)
m = re.search(r'("skills"\s*:\s*\{)', text)     # "skills": { ... } -> comma ok
if m:
    insert = f'\n    "paths": [\n      {json.dumps(want)}\n    ],'
    text = text[:m.end()] + insert + text[m.end():]
    open(path, "w", encoding="utf-8").write(text)
    sys.exit(0)

# Case 3: no "skills" key at all -> reparse and add one, accepting reformat.
data = json.loads(re.sub(r'^\s*//[^\n]*$', '', text, flags=re.M) or "{}")
data.setdefault("skills", {}).setdefault("paths", []).append(want)
open(path, "w", encoding="utf-8").write(json.dumps(data, indent=2) + "\n")
PY
}

opencode_remove_path() {
  local file="$1" want="$2"
  [ -f "$file" ] || return 0
  python3 - "$file" "$want" <<'PY'
import json, re, sys

path, want = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
if want not in text:
    sys.exit(0)
# Drop any array element that is exactly this path, then tidy blank lines.
text = re.sub(rf'\n[ \t]*{re.escape(json.dumps(want))},?', '', text)
open(path, "w", encoding="utf-8").write(text)
PY
}

# --------------------------------------------------------------------------
# Client install: symlink every skill into a dir-based client
# --------------------------------------------------------------------------

count_skills() {
  find "$SKILLS_DIR" -name SKILL.md -not -path '*/.*' 2>/dev/null | wc -l | tr -d ' '
}

install_dir_client() {
  local label="$1" dir="$2" state_file="$3"
  local n; n="$(count_skills)"
  if [ "$n" -eq 0 ]; then
    warn "$label: no skills found under $SKILLS_DIR"
    return 0
  fi

  if [ "$IS_DRY_RUN" -eq 1 ]; then
    step "$label → $dir ($n skills, dry-run)"
    return 0
  fi

  mkdir -p "$dir"
  local linked=0 skipped=0 conflicted=0

  while IFS= read -r skill_md; do
    local src; src="$(dirname "$skill_md")"
    local name; name="$(basename "$src")"
    local dest="$dir/$name"

    if [ -e "$dest" ] || [ -L "$dest" ]; then
      if [ -L "$dest" ] && [ "$(readlink -f "$dest")" = "$(readlink -f "$src")" ]; then
        skipped=$((skipped + 1)); continue
      fi
      if [ "$FORCE" -eq 1 ]; then
        step "$label: replacing existing $name (--force)"
        rm -rf "$dest"
      else
        conflicted=$((conflicted + 1)); continue
      fi
    fi

    if [ "$LINK_MODE" = "copy" ]; then
      cp -r "$src" "$dest"
    else
      ln -s "$src" "$dest" 2>/dev/null || {
        warn "$label: symlink failed for $name, falling back to copy"
        cp -r "$src" "$dest"
      }
    fi
    linked=$((linked + 1))
  done < <(find "$SKILLS_DIR" -name SKILL.md -not -path '*/.*')

  printf '%s\n' "$dir" > "$state_file"

  if [ "$conflicted" -gt 0 ]; then
    warn "$label: $linked linked, $skipped already ok, $conflicted conflict(s) skipped (use --force to replace)"
  else
    ok "$label: $linked skills linked into $dir"
  fi
}

uninstall_dir_client() {
  local label="$1" dir="$2" state_file="$3"
  if [ ! -d "$dir" ]; then
    info "$label: nothing at $dir"
    return 0
  fi
  if [ "$IS_DRY_RUN" -eq 1 ]; then
    step "$label: would remove skill symlinks under $dir (dry-run)"
    return 0
  fi
  local removed=0
  while IFS= read -r link; do
    if [ -L "$link" ] && [[ "$(readlink -f "$link")" == "$REPO_ROOT"* ]]; then
      rm -f "$link"; removed=$((removed + 1))
    fi
  done < <(find "$dir" -maxdepth 1 -type l 2>/dev/null)
  rm -f "$state_file"
  if [ "$removed" -gt 0 ]; then
    ok "$label: removed $removed symlink(s) from $dir"
  else
    info "$label: no symlinks into this repo found in $dir"
  fi
}

verify_dir_client() {
  local id="$1" label="$2" dir="$3"
  if [ ! -d "$dir" ]; then
    printf '%s\n' "${YELLOW}not installed${RESET}"
    return 0
  fi
  local total broken
  total=$(find "$dir" -maxdepth 1 -type l 2>/dev/null | wc -l | tr -d ' ')
  broken=$(find "$dir" -maxdepth 1 -type l ! -exec test -e {} \; -print 2>/dev/null | wc -l | tr -d ' ')
  if [ "$broken" -gt 0 ]; then
    printf '%s\n' "${RED}$broken broken symlink(s) of $total — rerun ./install.sh --force${RESET}"
  elif [ "$total" -gt 0 ]; then
    printf '%s\n' "${GREEN}ok — $total linked${RESET}"
  else
    printf '%s\n' "${YELLOW}dir exists, nothing linked${RESET}"
  fi
}

install_config_client() {
  local label="$1" file="$2" want="$3"
  if [ "$IS_DRY_RUN" -eq 1 ]; then
    if opencode_has_path "$file" "$want" 2>/dev/null; then
      step "$label: already configured → $want (dry-run)"
    else
      step "$label: would add \"$want\" to skills.paths in $file (dry-run)"
    fi
    return 0
  fi
  mkdir -p "$(dirname "$file")"
  [ -f "$file" ] || printf '{}\n' > "$file"
  if opencode_has_path "$file" "$want" 2>/dev/null; then
    ok "$label: already configured"
  else
    step "$label: adding $want to skills.paths in $file"
    opencode_add_path "$file" "$want"
    ok "$label: configured — restart opencode to pick it up"
  fi
}

uninstall_config_client() {
  local label="$1" file="$2" want="$3"
  [ -f "$file" ] || { info "$label: no config at $file"; return 0; }
  if [ "$IS_DRY_RUN" -eq 1 ]; then
    step "$label: would remove $want from $file (dry-run)"
    return 0
  fi
  if opencode_has_path "$file" "$want" 2>/dev/null; then
    opencode_remove_path "$file" "$want"
    ok "$label: removed $want from skills.paths"
  else
    info "$label: $want not present in config"
  fi
}

# --------------------------------------------------------------------------
# Pull skills from GitHub
# --------------------------------------------------------------------------
import_from_github() {
  local repo="$1"
  [ -n "$repo" ] || die "--from needs a GitHub repo (owner/name)"
  [ -d "$SKILLS_DIR" ] || die "skills/ directory not found at $REPO_ROOT"

  local tmp; tmp="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$tmp'" RETURN

  step "cloning $repo…"
  if [ "$IS_DRY_RUN" -eq 1 ]; then
    step "would clone https://github.com/$repo and import ${FROM_SKILL:-all skills}"
    return 0
  fi
  if ! git clone --depth 1 --quiet "https://github.com/$repo.git" "$tmp/repo" 2>/dev/null; then
    die "could not clone $repo (check the name and network access)"
  fi

  local domain="${FROM_DOMAIN:-imported}"
  local dest="$SKILLS_DIR/$domain"
  mkdir -p "$dest"

  local found=0
  if [ -n "$FROM_SKILL" ]; then
    local src="$tmp/repo/$FROM_SKILL"
    [ -f "$src/SKILL.md" ] || die "'$FROM_SKILL' in $repo has no SKILL.md"
    cp -r "$src" "$dest/$(basename "$src")"
    ok "imported $FROM_SKILL → $dest/$(basename "$src")"
    found=1
  else
    # Auto-discover every */SKILL.md and import each parent dir.
    while IFS= read -r md; do
      local src; src="$(dirname "$md")"
      local name; name="$(basename "$src")"
      [ -e "$dest/$name" ] && { warn "skipped $name (already exists)"; continue; }
      cp -r "$src" "$dest/$name"
      ok "imported $name → $dest/$name"
      found=$((found + 1))
    done < <(find "$tmp/repo" -name SKILL.md -not -path '*/.git/*')
  fi

  [ "$found" -gt 0 ] || warn "no skills found in $repo"
  step "run 'python tools/build_index.py' to register the new skills"
}

# --------------------------------------------------------------------------
# --list
# --------------------------------------------------------------------------
do_list() {
  local n; n="$(count_skills)"
  head1 "Agent Skills — $(pwd)"
  printf '%s\n' "${DIM}$n skills in $SKILLS_DIR${RESET}"
  printf '\n%s\n\n' "${BOLD}Clients${RESET}"
  printf '%-16s %-12s %-8s %s\n' "ID" "DETECTED" "KIND" "TARGET"
  printf '%s\n' "${DIM}------------------------------------------------------------------------${RESET}"

  local id label kind path detector target
  while IFS=$'\t' read -r id label kind path detector; do
    [ -n "$id" ] || continue
    target="$(expand_path "$path")"
    local det="no"
    client_detected "$detector" && det="yes"
    local marker="  "
    [ "$det" = "yes" ] && marker="* "
    printf '%s%-16s %-12s %-8s %s\n' "$marker" "$id" "$det" "$kind" "$target"
  done < <(printf '%s\n' "${CLIENTS[@]}")

  printf '\n%s\n' "${DIM}* = CLI detected on this machine${RESET}"
  printf '\n%s\n' "${BOLD}What the default run would do${RESET}"
  printf '%s\n' "  Symlink every skill into each detected client that reads a"
  printf '%s\n' "  skills directory, and add this path to opencode's skills.paths."
  printf '%s\n' "  Clients not installed are skipped. Nothing is copied or overwritten."
  printf '\n%s\n' "${BOLD}Add a client this repo does not know about${RESET}"
  printf '%s\n' "  ./install.sh --client myeditor=$HOME/.config/myeditor/skills"
  printf '\n'
}

# --------------------------------------------------------------------------
# --verify
# --------------------------------------------------------------------------
do_verify() {
  head1 "Verify installation"
  printf '%s\n' "${DIM}skills source: $SKILLS_DIR${RESET}"
  printf '\n'
  local id label kind path detector target
  while IFS=$'\t' read -r id label kind path detector; do
    [ -n "$id" ] || continue
    target="$(expand_path "$path")"
    local line
    if [ "$kind" = "config" ]; then
      if opencode_has_path "$target" "$SKILLS_DIR" 2>/dev/null; then
        line="${GREEN}ok — configured${RESET}"
      else
        line="${YELLOW}not configured${RESET}"
      fi
    else
      line="$(verify_dir_client "$id" "$label" "$target")"
    fi
    printf '%-16s %-24s %s\n' "$id" "$label" "$line"
  done < <(printf '%s\n' "${CLIENTS[@]}")
  printf '\n'
}

# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
select_clients() {
  # Populates SELECTED rows. Returns count.
  local -a rows=()
  local id label kind path detector

  # custom targets: always id=path, so an id without a path is a typo and
  # should be rejected rather than silently creating a bogus target.
  for ct in "${CUSTOM_TARGETS[@]:-}"; do
    [ -n "$ct" ] || continue
    case "$ct" in
      *=*) ;;
      *) die "custom target '$ct' must be <id>=<path> (e.g. myeditor=$HOME/.myeditor/skills)" ;;
    esac
    local cid="${ct%%=*}" cpath="${ct#*=}"
    [ -n "$cpath" ] || die "custom target '$ct' has an empty path"
    rows+=("$(printf '%s\t%s\tdir\t%s\t:\n' "$cid" "$cid" "$(expand_path "$cpath")")")
  done

  if [ ${#SELECTED_CLIENTS[@]} -gt 0 ] && [[ " ${SELECTED_CLIENTS[*]} " == *" __all__ "* ]]; then
    while IFS=$'\t' read -r id label kind path detector; do
      [ -n "$id" ] || continue
      rows+=("$(printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$label" "$kind" "$path" "$detector")")
    done < <(printf '%s\n' "${CLIENTS[@]}")
  elif [ ${#SELECTED_CLIENTS[@]} -gt 0 ]; then
    for want in "${SELECTED_CLIENTS[@]}"; do
      [ "$want" = "__nodetect__" ] && continue
      local found=0
      while IFS=$'\t' read -r id label kind path detector; do
        [ "$id" = "$want" ] || continue
        found=1
        rows+=("$(printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$label" "$kind" "$path" "$detector")")
      done < <(printf '%s\n' "${CLIENTS[@]}")
      [ "$found" -eq 1 ] || die "unknown client '$want' (try --list)"
    done
  elif [ ${#CUSTOM_TARGETS[@]} -gt 0 ]; then
    # Only custom targets were given: do exactly those, nothing else. Falling
    # through to auto-detect here would quietly wire up every CLI on the box.
    :
  else
    local want_detect=1
    for w in "${SELECTED_CLIENTS[@]:-}"; do [ "$w" = "__nodetect__" ] && want_detect=0; done
    while IFS=$'\t' read -r id label kind path detector; do
      [ -n "$id" ] || continue
      # claude-desktop shares claude's dir; skip the duplicate
      [ "$id" = "claude-desktop" ] && continue
      if [ "$want_detect" -eq 1 ] && ! client_detected "$detector"; then continue; fi
      rows+=("$(printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$label" "$kind" "$path" "$detector")")
    done < <(printf '%s\n' "${CLIENTS[@]}")
  fi

  SELECTED_ROWS=("${rows[@]:-}")
  printf '%s' "${#SELECTED_ROWS[@]}"
}

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
main() {
  [ -d "$SKILLS_DIR" ] || die "no skills/ directory at $REPO_ROOT"

  if [ "$DO_LIST" -eq 1 ]; then do_list; exit 0; fi
  if [ "$DO_VERIFY" -eq 1 ]; then do_verify; exit 0; fi

  if [ -n "$FROM_REPO" ]; then
    import_from_github "$FROM_REPO"
    exit $?
  fi

  SELECTED_ROWS=()
  select_clients >/dev/null
  local count=${#SELECTED_ROWS[@]}

  head1 "${BOLD}Install Agent Skills${RESET} — $(count_skills) available"
  if [ "$IS_DRY_RUN" -eq 1 ]; then
    printf '%s\n' "${YELLOW}DRY RUN — nothing will be changed${RESET}"
  fi
  [ "$LINK_MODE" = "copy" ] && printf '%s\n' "${YELLOW}link mode: copy (files are duplicated, may drift)${RESET}"
  printf '\n'

  if [ "$count" -eq 0 ]; then
    warn "no clients selected or detected. Use --list to see options, or --all."
    return 0
  fi

  local state_dir="$HOME_DIR/.local/state/agent-skills-installer"
  [ "$IS_DRY_RUN" -eq 1 ] || mkdir -p "$state_dir"

  local row id label kind path detector target
  for row in "${SELECTED_ROWS[@]}"; do
    IFS=$'\t' read -r id label kind path detector <<< "$row"
    target="$(expand_path "$path")"
    local state="$state_dir/$id"

    if [ "$DO_UNINSTALL" -eq 1 ]; then
      if [ "$kind" = "config" ]; then
        uninstall_config_client "$label" "$target" "$SKILLS_DIR"
      else
        uninstall_dir_client "$label" "$target" "$state"
      fi
      continue
    fi

    if [ "$kind" = "config" ]; then
      install_config_client "$label" "$target" "$SKILLS_DIR"
    else
      install_dir_client "$label" "$target" "$state"
    fi
  done

  printf '\n'
  if [ "$IS_DRY_RUN" -eq 0 ]; then
    ok "done. Start (or restart) your agent to load the skills."
    printf '%s\n' "${DIM}Verify any time with: ./install.sh --verify${RESET}"
  fi
}

main "$@"
