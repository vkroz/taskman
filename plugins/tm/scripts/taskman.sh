#!/usr/bin/env bash
# Taskman — epic, feature, and task management CLI.
#
# Single source of truth for creating, moving, listing, and closing
# epics/features/tasks under ./tasks. Skills (/tm:epic-create,
# /tm:feature-create, /tm:task-create, /tm:task-implement,
# /tm:feature-implement, /tm:epic-implement) invoke this script
# instead of touching files directly so invariants stay consistent.
#
# See rules/task-management.md for the model.
#
# Usage:
#   ./scripts/taskman.sh <command> [options]
#
# Commands:
#   new epic     --slug S --title T [--no-validate] < body
#   new feature  --slug S --title T [--epic S] [--no-validate] < body
#   new task     --title T [--feature S] [--kind task|bug]
#                [--slug S] [--no-validate] < body
#   finalize <path>            # clear the draft: true marker
#   discard  <path>            # delete a draft file (refuses non-drafts)
#   move <task-path> <backlog|active|done>
#   list epics    [--status backlog|active|done]
#   list features [--epic S]    [--status backlog|active|done]
#   list tasks    [--feature S] [--status backlog|active|done] [--kind task|bug]
#   epic show     <slug>
#   epic close    <slug>
#   feature show  <slug>
#   feature close <slug>
#   validate
#   help
#
# Draft workflow:
#   `new` writes the file with `draft: true` in the YAML header so authoring
#   skills can re-read and preview from disk. `finalize` removes the marker
#   once the user approves; `discard` deletes the file on rejection.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SCRIPT_DIR}/.."
TASKS_DIR="${TASKMAN_TASKS_DIR:-${PROJECT_DIR}/tasks}"
EPICS_DIR="${TASKS_DIR}/epics"
FEATURES_DIR="${TASKS_DIR}/features"
DOCS_SYNC_QUEUE="${TASKS_DIR}/docs-sync-queue.txt"
STATES=(backlog active done)

if [[ -t 1 ]]; then
  RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
  BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi

log_info()  { printf '%b\n' "${GREEN}[INFO]${NC}  $*" >&2; }
log_warn()  { printf '%b\n' "${YELLOW}[WARN]${NC}  $*" >&2; }
log_error() { printf '%b\n' "${RED}[ERROR]${NC} $*" >&2; }
die()       { log_error "$*"; exit 1; }

# ---------- YAML helpers (shallow frontmatter only) ----------

# Extract the value of a top-level key from the first YAML frontmatter
# block of a file. Prints empty string if missing.
yaml_field() {
  local file="$1" key="$2"
  awk -v key="$key" '
    BEGIN { count=0; in_yaml=0 }
    /^---[[:space:]]*$/ {
      count++
      if (count == 1) { in_yaml = 1; next }
      if (count == 2) { exit }
      next
    }
    in_yaml && $0 ~ "^"key":" {
      sub("^"key":[[:space:]]*", "")
      sub("[[:space:]]+$", "")
      print
      exit
    }
  ' "$file"
}

# Remove a YAML field in-place. No-op if the field is absent.
yaml_remove_field() {
  local file="$1" key="$2"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" '
    BEGIN { count=0; in_yaml=0 }
    /^---[[:space:]]*$/ {
      count++
      if (count == 1) { in_yaml = 1; print; next }
      if (count == 2) { in_yaml = 0; print; next }
    }
    in_yaml && $0 ~ "^"key":" { next }
    { print }
  ' "$file" > "$tmp"
  mv "$tmp" "$file"
}

# Rewrite a YAML field in-place. Creates the field if absent.
yaml_set_field() {
  local file="$1" key="$2" value="$3"
  local tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { count=0; in_yaml=0; replaced=0 }
    /^---[[:space:]]*$/ {
      count++
      if (count == 1) { in_yaml = 1; print; next }
      if (count == 2) {
        if (!replaced) { print key ": " value }
        in_yaml = 0
        print
        next
      }
    }
    in_yaml && $0 ~ "^"key":" {
      print key ": " value
      replaced = 1
      next
    }
    { print }
  ' "$file" > "$tmp"
  mv "$tmp" "$file"
}

# ---------- Slug and body helpers ----------

slugify() {
  local s="$1"
  s="$(printf '%s' "$s" | tr '[:upper:]' '[:lower:]')"
  s="$(printf '%s' "$s" | sed -E 's/[^a-z0-9]+/_/g; s/^_+//; s/_+$//')"
  s="${s:0:60}"
  s="${s%_}"
  printf '%s' "$s"
}

validate_slug() {
  local slug="$1"
  [[ "$slug" =~ ^[a-z][a-z0-9_]*$ ]] || die "Invalid slug: '$slug' — must match [a-z][a-z0-9_]*"
}

# ---------- Doc-sync queue (PostClose hook) ----------
#
# When a work unit closes (task → done, feature close, epic close), append an
# entry to docs-sync-queue.txt. The /tm:docs-sync skill reads this queue and
# proposes upstream doc updates. The queue is durable on disk — survives
# context compaction and direct CLI use.

emit_docs_sync_hint() {
  local kind="$1" id="$2"
  mkdir -p "${TASKS_DIR}"
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$kind" "$id" >> "$DOCS_SYNC_QUEUE"
  log_info "Doc-sync pending. Run /tm:docs-sync to review upstream doc drift."
}

# Print a hint to stderr if the queue has pending entries. Called from list
# commands so users see the queue whenever they look at task state.
check_docs_sync_queue() {
  [[ -s "$DOCS_SYNC_QUEUE" ]] || return 0
  local n
  n="$(grep -c . "$DOCS_SYNC_QUEUE" 2>/dev/null || printf 0)"
  (( n > 0 )) || return 0
  if (( n == 1 )); then
    log_warn "1 doc-sync entry pending in ${DOCS_SYNC_QUEUE}. Run /tm:docs-sync."
  else
    log_warn "${n} doc-sync entries pending in ${DOCS_SYNC_QUEUE}. Run /tm:docs-sync."
  fi
}

# Required body sections per entity type. Echoed one per line.
required_sections() {
  local kind="$1"
  case "$kind" in
    epic)
      printf 'Problem statement\nObjective\nScope\nAcceptance criteria\nLinked features\n' ;;
    feature)
      printf 'Problem statement\nObjective\nAcceptance criteria\n' ;;
    task)
      printf 'Problem statement\nScope\nAcceptance criteria\nQuality gates\n' ;;
    *) die "Unknown entity kind for validation: $kind" ;;
  esac
}

# Validate body against required sections. Returns nonzero if any missing.
validate_body_sections() {
  local kind="$1" body="$2"
  local missing=()
  local section
  while IFS= read -r section; do
    [[ -z "$section" ]] && continue
    if ! grep -qiE "^##[[:space:]]+${section}[[:space:]]*$" <<<"$body"; then
      missing+=("$section")
    fi
  done < <(required_sections "$kind")
  if (( ${#missing[@]} > 0 )); then
    log_error "Body missing required sections for '${kind}':"
    local m; for m in "${missing[@]}"; do log_error "  - ## ${m}"; done
    return 1
  fi
  return 0
}

today() { date +%Y%m%d; }

# ---------- Epic helpers ----------

# Find the epic file for a given slug. Prints its path on stdout, or
# exits nonzero with no output if not found.
find_epic_file() {
  local slug="$1"
  local f
  shopt -s nullglob
  for f in "${EPICS_DIR}"/*_"${slug}".md; do
    local stem="${f##*/}"; stem="${stem%.md}"
    local file_slug="${stem#*_}"
    if [[ "$file_slug" == "$slug" ]]; then
      printf '%s' "$f"
      shopt -u nullglob
      return 0
    fi
  done
  shopt -u nullglob
  return 1
}

epic_slug_from_file() {
  local f="$1"
  local stem="${f##*/}"; stem="${stem%.md}"
  printf '%s' "${stem#*_}"
}

# ---------- Feature helpers ----------

# Find the feature file for a given slug. Prints its path on stdout, or
# exits nonzero with no output if not found.
find_feature_file() {
  local slug="$1"
  local f
  shopt -s nullglob
  for f in "${FEATURES_DIR}"/*_"${slug}".md; do
    # Confirm the slug matches the suffix exactly (date_<slug>.md).
    local stem="${f##*/}"; stem="${stem%.md}"
    local file_slug="${stem#*_}"
    if [[ "$file_slug" == "$slug" ]]; then
      printf '%s' "$f"
      shopt -u nullglob
      return 0
    fi
  done
  shopt -u nullglob
  return 1
}

feature_slug_from_file() {
  local f="$1"
  local stem="${f##*/}"; stem="${stem%.md}"
  printf '%s' "${stem#*_}"
}

# ---------- Task helpers ----------

# Return filename of all tasks under tasks/{backlog,active,done}.
list_all_task_files() {
  local s f
  shopt -s nullglob
  for s in "${STATES[@]}"; do
    for f in "${TASKS_DIR}/${s}"/*.md; do
      printf '%s\n' "$f"
    done
  done
  shopt -u nullglob
}

# State directory for a given task file (or empty if not in a state dir).
task_state_from_path() {
  local f="$1"
  local parent
  parent="$(basename "$(dirname "$f")")"
  case "$parent" in
    backlog|active|done) printf '%s' "$parent" ;;
    *) printf '' ;;
  esac
}

# Given a YYYYMMDD date and a slug, pick a non-colliding filename stem
# across all three state dirs. Adds _NN suffix if needed.
task_stem_exists() {
  local candidate="$1" st
  for st in "${STATES[@]}"; do
    if [[ -e "${TASKS_DIR}/${st}/${candidate}.md" ]]; then
      return 0
    fi
  done
  return 1
}

pick_unique_task_stem() {
  local date="$1" slug="$2"
  local stem="${date}_${slug}"
  if ! task_stem_exists "$stem"; then
    printf '%s' "$stem"
    return 0
  fi
  local nn
  for nn in 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20; do
    local cand="${date}_${nn}_${slug}"
    if ! task_stem_exists "$cand"; then
      printf '%s' "$cand"
      return 0
    fi
  done
  die "Cannot find unique filename for $date / $slug (too many same-day collisions)"
}

# ---------- Commands ----------

cmd_new_epic() {
  local slug="" title="" no_validate=0
  while (( $# > 0 )); do
    case "$1" in
      --slug) slug="${2:-}"; shift 2 ;;
      --title) title="${2:-}"; shift 2 ;;
      --no-validate) no_validate=1; shift ;;
      *) die "Unknown flag: $1" ;;
    esac
  done
  [[ -n "$slug" ]]  || die "--slug is required"
  [[ -n "$title" ]] || die "--title is required"
  validate_slug "$slug"

  # Read body from stdin.
  if [[ -t 0 ]]; then
    die "new epic requires body on stdin (redirect from a file or heredoc)"
  fi
  local body
  body="$(cat)"
  [[ -n "$body" ]] || die "Body is empty"

  if (( no_validate == 0 )); then
    validate_body_sections epic "$body" || die "Body validation failed (override with --no-validate)"
  fi

  # Uniqueness: reject if any non-done epic already uses this slug.
  local existing
  if [[ -d "$EPICS_DIR" ]] && existing="$(find_epic_file "$slug")"; then
    local existing_status
    existing_status="$(yaml_field "$existing" status)"
    if [[ "$existing_status" != "done" ]]; then
      die "Epic slug '${slug}' already in use by: ${existing} (status=${existing_status})"
    fi
    # A past done epic with same slug exists. Allow reuse; new file gets today's date.
  fi

  local date_prefix; date_prefix="$(today)"
  local path="${EPICS_DIR}/${date_prefix}_${slug}.md"
  if [[ -e "$path" ]]; then
    die "Epic file already exists: $path"
  fi

  local content
  content="$(compose_epic_file "$slug" "$title" "$body")"

  mkdir -p "$EPICS_DIR"
  printf '%s\n' "$content" > "$path"
  log_info "Created epic draft: $path"
  printf '%s\n' "$path"
}

compose_epic_file() {
  local slug="$1" title="$2" body="$3"
  cat <<EOF
---
status: backlog
slug: ${slug}
title: ${title}
draft: true
---

# ${title}

${body}
EOF
}

cmd_new_feature() {
  local slug="" title="" epic="" no_validate=0
  while (( $# > 0 )); do
    case "$1" in
      --slug) slug="${2:-}"; shift 2 ;;
      --title) title="${2:-}"; shift 2 ;;
      --epic) epic="${2:-}"; shift 2 ;;
      --no-validate) no_validate=1; shift ;;
      *) die "Unknown flag: $1" ;;
    esac
  done
  [[ -n "$slug" ]]  || die "--slug is required"
  [[ -n "$title" ]] || die "--title is required"
  validate_slug "$slug"

  # Read body from stdin.
  if [[ -t 0 ]]; then
    die "new feature requires body on stdin (redirect from a file or heredoc)"
  fi
  local body
  body="$(cat)"
  [[ -n "$body" ]] || die "Body is empty"

  if (( no_validate == 0 )); then
    validate_body_sections feature "$body" || die "Body validation failed (override with --no-validate)"
  fi

  # Validate optional --epic linkage.
  if [[ -n "$epic" ]]; then
    validate_slug "$epic"
    if ! find_epic_file "$epic" >/dev/null; then
      die "Epic not found: '${epic}'. Create it first with: taskman.sh new epic --slug ${epic} ..."
    fi
  fi

  # Uniqueness: reject if any non-done feature already uses this slug.
  local existing
  if [[ -d "$FEATURES_DIR" ]] && existing="$(find_feature_file "$slug")"; then
    local existing_status
    existing_status="$(yaml_field "$existing" status)"
    if [[ "$existing_status" != "done" ]]; then
      die "Feature slug '${slug}' already in use by: ${existing} (status=${existing_status})"
    fi
    # A past done feature with same slug exists. Allow reuse; new file gets today's date.
  fi

  local date_prefix; date_prefix="$(today)"
  local path="${FEATURES_DIR}/${date_prefix}_${slug}.md"
  if [[ -e "$path" ]]; then
    die "Feature file already exists: $path"
  fi

  local content
  content="$(compose_feature_file "$slug" "$title" "$epic" "$body")"

  mkdir -p "$FEATURES_DIR"
  printf '%s\n' "$content" > "$path"
  log_info "Created feature draft: $path"
  printf '%s\n' "$path"
}

compose_feature_file() {
  local slug="$1" title="$2" epic="$3" body="$4"
  local epic_line=""
  [[ -n "$epic" ]] && epic_line="epic: ${epic}"$'\n'
  cat <<EOF
---
status: backlog
slug: ${slug}
${epic_line}title: ${title}
draft: true
---

# ${title}

${body}
EOF
}

cmd_new_task() {
  local title="" slug_override="" feature="" kind="task" no_validate=0
  while (( $# > 0 )); do
    case "$1" in
      --title) title="${2:-}"; shift 2 ;;
      --slug) slug_override="${2:-}"; shift 2 ;;
      --feature) feature="${2:-}"; shift 2 ;;
      --kind) kind="${2:-}"; shift 2 ;;
      --no-validate) no_validate=1; shift ;;
      *) die "Unknown flag: $1" ;;
    esac
  done
  [[ -n "$title" ]] || die "--title is required"
  case "$kind" in task|bug) ;; *) die "--kind must be 'task' or 'bug'" ;; esac

  if [[ -t 0 ]]; then
    die "new task requires body on stdin (redirect from a file or heredoc)"
  fi
  local body; body="$(cat)"
  [[ -n "$body" ]] || die "Body is empty"

  if (( no_validate == 0 )); then
    validate_body_sections task "$body" || die "Body validation failed (override with --no-validate)"
  fi

  if [[ -n "$feature" ]]; then
    validate_slug "$feature"
    if ! find_feature_file "$feature" >/dev/null; then
      die "Feature not found: '${feature}'. Create it first with: taskman.sh new feature --slug ${feature} ..."
    fi
  fi

  local slug
  if [[ -n "$slug_override" ]]; then
    slug="$slug_override"
    validate_slug "$slug"
  else
    slug="$(slugify "$title")"
    [[ -n "$slug" ]] || die "Could not derive slug from title '${title}'; pass --slug explicitly"
    validate_slug "$slug"
  fi

  local date_prefix; date_prefix="$(today)"
  local stem; stem="$(pick_unique_task_stem "$date_prefix" "$slug")"
  local path="${TASKS_DIR}/backlog/${stem}.md"

  local content
  content="$(compose_task_file "$title" "$feature" "$kind" "$body")"

  mkdir -p "${TASKS_DIR}/backlog"
  printf '%s\n' "$content" > "$path"
  log_info "Created task draft: $path"
  printf '%s\n' "$path"
}

compose_task_file() {
  local title="$1" feature="$2" kind="$3" body="$4"
  local feature_line=""
  [[ -n "$feature" ]] && feature_line="feature: ${feature}"$'\n'
  cat <<EOF
---
status: backlog
kind: ${kind}
${feature_line}title: ${title}
draft: true
---

# ${title}

${body}
EOF
}

cmd_finalize() {
  (( $# == 1 )) || die "Usage: taskman.sh finalize <path>"
  local path="$1"
  [[ -f "$path" ]] || die "Not a file: $path"
  local draft; draft="$(yaml_field "$path" draft)"
  if [[ "$draft" != "true" ]]; then
    log_info "Already finalized: $path"
    printf '%s\n' "$path"
    return 0
  fi
  yaml_remove_field "$path" draft
  log_info "Finalized: $path"
  printf '%s\n' "$path"
}

cmd_discard() {
  (( $# == 1 )) || die "Usage: taskman.sh discard <path>"
  local path="$1"
  [[ -f "$path" ]] || die "Not a file: $path"
  local draft; draft="$(yaml_field "$path" draft)"
  if [[ "$draft" != "true" ]]; then
    die "Refusing to discard non-draft file (no 'draft: true' in YAML): $path"
  fi
  rm -- "$path"
  log_info "Discarded draft: $path"
}

cmd_move() {
  (( $# == 2 )) || die "Usage: taskman.sh move <task-path> <backlog|active|done>"
  local src="$1" target_state="$2"
  case "$target_state" in backlog|active|done) ;; *) die "Invalid state: $target_state" ;; esac
  [[ -f "$src" ]] || die "Not a file: $src"
  local current_state; current_state="$(task_state_from_path "$src")"
  [[ -n "$current_state" ]] || die "Not a lifecycle task file: $src"
  if [[ "$current_state" == "$target_state" ]]; then
    log_info "Already in $target_state: $src"
    printf '%s\n' "$src"
    return 0
  fi
  local base; base="$(basename "$src")"
  local dest="${TASKS_DIR}/${target_state}/${base}"
  [[ -e "$dest" ]] && die "Destination already exists: $dest"
  mkdir -p "${TASKS_DIR}/${target_state}"
  mv "$src" "$dest"
  yaml_set_field "$dest" status "$target_state"
  log_info "Moved $current_state → $target_state: $dest"
  [[ "$target_state" == "done" ]] && emit_docs_sync_hint task "$dest"
  printf '%s\n' "$dest"
}

cmd_list_epics() {
  local filter_status=""
  while (( $# > 0 )); do
    case "$1" in
      --status) filter_status="${2:-}"; shift 2 ;;
      *) die "Unknown flag: $1" ;;
    esac
  done
  printf '%-10s %-24s %s\n' STATUS SLUG TITLE
  printf '%-10s %-24s %s\n' "----" "----" "-----"
  local f
  shopt -s nullglob
  for f in "${EPICS_DIR}"/*.md; do
    local status; status="$(yaml_field "$f" status)"
    [[ -n "$filter_status" && "$status" != "$filter_status" ]] && continue
    local slug; slug="$(epic_slug_from_file "$f")"
    local title; title="$(yaml_field "$f" title)"
    printf '%-10s %-24s %s\n' "${status:-?}" "$slug" "${title:-(untitled)}"
  done
  shopt -u nullglob
}

cmd_list_features() {
  local filter_status="" filter_epic=""
  while (( $# > 0 )); do
    case "$1" in
      --status) filter_status="${2:-}"; shift 2 ;;
      --epic)   filter_epic="${2:-}";   shift 2 ;;
      *) die "Unknown flag: $1" ;;
    esac
  done
  printf '%-10s %-16s %-24s %s\n' STATUS EPIC SLUG TITLE
  printf '%-10s %-16s %-24s %s\n' "----" "----" "----" "-----"
  local f
  shopt -s nullglob
  for f in "${FEATURES_DIR}"/*.md; do
    local status; status="$(yaml_field "$f" status)"
    [[ -n "$filter_status" && "$status" != "$filter_status" ]] && continue
    local epic; epic="$(yaml_field "$f" epic)"
    [[ -n "$filter_epic" && "$epic" != "$filter_epic" ]] && continue
    local slug; slug="$(feature_slug_from_file "$f")"
    local title; title="$(yaml_field "$f" title)"
    printf '%-10s %-16s %-24s %s\n' "${status:-?}" "${epic:--}" "$slug" "${title:-(untitled)}"
  done
  shopt -u nullglob
}

cmd_list_tasks() {
  local filter_feature="" filter_status="" filter_kind=""
  while (( $# > 0 )); do
    case "$1" in
      --feature) filter_feature="${2:-}"; shift 2 ;;
      --status)  filter_status="${2:-}";  shift 2 ;;
      --kind)    filter_kind="${2:-}";    shift 2 ;;
      *) die "Unknown flag: $1" ;;
    esac
  done
  printf '%-10s %-6s %-24s %s\n' STATUS KIND FEATURE PATH
  printf '%-10s %-6s %-24s %s\n' "----" "----" "-------" "----"
  local f
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local status kind feature
    status="$(yaml_field "$f" status)"
    kind="$(yaml_field "$f" kind)"
    [[ -z "$kind" ]] && kind="task"
    feature="$(yaml_field "$f" feature)"
    [[ -n "$filter_status"  && "$status"  != "$filter_status"  ]] && continue
    [[ -n "$filter_kind"    && "$kind"    != "$filter_kind"    ]] && continue
    [[ -n "$filter_feature" && "$feature" != "$filter_feature" ]] && continue
    printf '%-10s %-6s %-24s %s\n' "${status:-?}" "$kind" "${feature:--}" "$f"
  done < <(list_all_task_files)
}

cmd_epic_show() {
  (( $# == 1 )) || die "Usage: taskman.sh epic show <slug>"
  local slug="$1"
  validate_slug "$slug"
  local ep; ep="$(find_epic_file "$slug")" || die "Epic not found: $slug"
  local status title
  status="$(yaml_field "$ep" status)"
  title="$(yaml_field "$ep" title)"
  printf '%bEpic:%b %s\n' "$BOLD" "$NC" "$slug"
  printf '  title:  %s\n' "${title:-(untitled)}"
  printf '  status: %s\n' "${status:-?}"
  printf '  file:   %s\n\n' "$ep"

  printf '%bMember features:%b\n' "$BOLD" "$NC"
  local count_backlog=0 count_active=0 count_done=0
  local f
  shopt -s nullglob
  for f in "${FEATURES_DIR}"/*.md; do
    local feat_epic; feat_epic="$(yaml_field "$f" epic)"
    [[ "$feat_epic" != "$slug" ]] && continue
    local s t fslug
    s="$(yaml_field "$f" status)"
    t="$(yaml_field "$f" title)"
    fslug="$(feature_slug_from_file "$f")"
    printf '  [%-7s] %-24s %s\n' "$s" "$fslug" "${t:-(untitled)}"
    case "$s" in
      backlog) count_backlog=$((count_backlog+1)) ;;
      active)  count_active=$((count_active+1)) ;;
      done)    count_done=$((count_done+1)) ;;
    esac
  done
  shopt -u nullglob
  printf '\n%bSummary:%b backlog=%d  active=%d  done=%d\n' \
    "$BOLD" "$NC" "$count_backlog" "$count_active" "$count_done"
}

cmd_epic_close() {
  (( $# == 1 )) || die "Usage: taskman.sh epic close <slug>"
  local slug="$1"
  validate_slug "$slug"
  local ep; ep="$(find_epic_file "$slug")" || die "Epic not found: $slug"
  local current; current="$(yaml_field "$ep" status)"
  if [[ "$current" == "done" ]]; then
    log_info "Epic already done: $slug"
    return 0
  fi

  local open_count=0 open_list=""
  local f
  shopt -s nullglob
  for f in "${FEATURES_DIR}"/*.md; do
    local feat_epic; feat_epic="$(yaml_field "$f" epic)"
    [[ "$feat_epic" != "$slug" ]] && continue
    local s; s="$(yaml_field "$f" status)"
    if [[ "$s" != "done" ]]; then
      open_count=$((open_count+1))
      open_list="${open_list}  - [${s}] ${f}"$'\n'
    fi
  done
  shopt -u nullglob

  if (( open_count > 0 )); then
    log_error "Cannot close epic '${slug}': ${open_count} member feature(s) not in done"
    printf '%s' "$open_list" >&2
    exit 1
  fi

  yaml_set_field "$ep" status done
  log_info "Closed epic: $slug"
  emit_docs_sync_hint epic "$slug"
  printf '%s\n' "$ep"
}

cmd_feature_show() {
  (( $# == 1 )) || die "Usage: taskman.sh feature show <slug>"
  local slug="$1"
  validate_slug "$slug"
  local fp; fp="$(find_feature_file "$slug")" || die "Feature not found: $slug"
  local status title epic
  status="$(yaml_field "$fp" status)"
  title="$(yaml_field "$fp" title)"
  epic="$(yaml_field "$fp" epic)"
  printf '%bFeature:%b %s\n' "$BOLD" "$NC" "$slug"
  printf '  title:  %s\n' "${title:-(untitled)}"
  printf '  status: %s\n' "${status:-?}"
  printf '  epic:   %s\n' "${epic:-(stand-alone)}"
  printf '  file:   %s\n\n' "$fp"

  printf '%bMember tasks:%b\n' "$BOLD" "$NC"
  local count_backlog=0 count_active=0 count_done=0
  local f
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local feat; feat="$(yaml_field "$f" feature)"
    [[ "$feat" != "$slug" ]] && continue
    local s k t
    s="$(yaml_field "$f" status)"
    k="$(yaml_field "$f" kind)"; [[ -z "$k" ]] && k="task"
    t="$(yaml_field "$f" title)"
    printf '  [%-7s] [%-4s] %s\n' "$s" "$k" "${t:-$(basename "$f" .md)}"
    case "$s" in
      backlog) count_backlog=$((count_backlog+1)) ;;
      active)  count_active=$((count_active+1)) ;;
      done)    count_done=$((count_done+1)) ;;
    esac
  done < <(list_all_task_files)
  printf '\n%bSummary:%b backlog=%d  active=%d  done=%d\n' \
    "$BOLD" "$NC" "$count_backlog" "$count_active" "$count_done"
}

cmd_feature_close() {
  (( $# == 1 )) || die "Usage: taskman.sh feature close <slug>"
  local slug="$1"
  validate_slug "$slug"
  local fp; fp="$(find_feature_file "$slug")" || die "Feature not found: $slug"
  local current; current="$(yaml_field "$fp" status)"
  if [[ "$current" == "done" ]]; then
    log_info "Feature already done: $slug"
    return 0
  fi

  local open_count=0 open_list=""
  local f
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local feat; feat="$(yaml_field "$f" feature)"
    [[ "$feat" != "$slug" ]] && continue
    local s; s="$(yaml_field "$f" status)"
    if [[ "$s" != "done" ]]; then
      open_count=$((open_count+1))
      open_list="${open_list}  - [${s}] ${f}"$'\n'
    fi
  done < <(list_all_task_files)

  if (( open_count > 0 )); then
    log_error "Cannot close feature '${slug}': ${open_count} member task(s) not in done"
    printf '%s' "$open_list" >&2
    exit 1
  fi

  yaml_set_field "$fp" status done
  log_info "Closed feature: $slug"
  emit_docs_sync_hint feature "$slug"
  printf '%s\n' "$fp"
}

cmd_feature() {
  (( $# >= 1 )) || die "Usage: taskman.sh feature <show|close> <slug>"
  local sub="$1"; shift
  case "$sub" in
    show)  cmd_feature_show  "$@" ;;
    close) cmd_feature_close "$@" ;;
    *) die "Unknown feature subcommand: $sub" ;;
  esac
}

cmd_epic() {
  (( $# >= 1 )) || die "Usage: taskman.sh epic <show|close> <slug>"
  local sub="$1"; shift
  case "$sub" in
    show)  cmd_epic_show  "$@" ;;
    close) cmd_epic_close "$@" ;;
    *) die "Unknown epic subcommand: $sub" ;;
  esac
}

cmd_new() {
  (( $# >= 1 )) || die "Usage: taskman.sh new <epic|feature|task> ..."
  local sub="$1"; shift
  case "$sub" in
    epic)    cmd_new_epic    "$@" ;;
    feature) cmd_new_feature "$@" ;;
    task)    cmd_new_task    "$@" ;;
    *) die "Unknown new subcommand: $sub" ;;
  esac
}

cmd_list() {
  (( $# >= 1 )) || die "Usage: taskman.sh list <epics|features|tasks> ..."
  local sub="$1"; shift
  case "$sub" in
    epics)    cmd_list_epics    "$@" ;;
    features) cmd_list_features "$@" ;;
    tasks)    cmd_list_tasks    "$@" ;;
    *) die "Unknown list subcommand: $sub" ;;
  esac
  check_docs_sync_queue
}

cmd_validate() {
  local errors=0
  local f
  # Epics.
  shopt -s nullglob
  for f in "${EPICS_DIR}"/*.md; do
    local status stem
    status="$(yaml_field "$f" status)"
    case "$status" in backlog|active|done) ;; *)
      log_error "$(basename "$f"): epic status missing or invalid: '${status}'"
      errors=$((errors+1)) ;;
    esac
    stem="$(basename "$f" .md)"
    if [[ ! "$stem" =~ ^[0-9]{8}_[a-z][a-z0-9_]*$ ]]; then
      log_error "$(basename "$f"): epic filename must be YYYYMMDD_<slug>.md"
      errors=$((errors+1))
    fi
  done
  shopt -u nullglob

  # Features.
  shopt -s nullglob
  for f in "${FEATURES_DIR}"/*.md; do
    local status stem epic
    status="$(yaml_field "$f" status)"
    case "$status" in backlog|active|done) ;; *)
      log_error "$(basename "$f"): feature status missing or invalid: '${status}'"
      errors=$((errors+1)) ;;
    esac
    stem="$(basename "$f" .md)"
    if [[ ! "$stem" =~ ^[0-9]{8}_[a-z][a-z0-9_]*$ ]]; then
      log_error "$(basename "$f"): feature filename must be YYYYMMDD_<slug>.md"
      errors=$((errors+1))
    fi
    epic="$(yaml_field "$f" epic)"
    if [[ -n "$epic" ]]; then
      if [[ "$epic" =~ ^[a-z][a-z0-9_]*$ ]]; then
        if ! find_epic_file "$epic" >/dev/null; then
          log_warn "$(basename "$f"): references unknown epic '${epic}' (legacy or stale)"
        fi
      else
        log_warn "$(basename "$f"): informal epic value '${epic}' (not a slug) — treated as stand-alone"
      fi
    fi
  done
  shopt -u nullglob

  # Tasks.
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    local status kind feat parent
    status="$(yaml_field "$f" status)"
    kind="$(yaml_field "$f" kind)"
    feat="$(yaml_field "$f" feature)"
    parent="$(task_state_from_path "$f")"
    if [[ -n "$status" && "$status" != "$parent" ]]; then
      log_error "$(basename "$f"): YAML status='${status}' does not match folder '${parent}'"
      errors=$((errors+1))
    fi
    if [[ -n "$kind" ]] && [[ "$kind" != "task" && "$kind" != "bug" ]]; then
      log_error "$(basename "$f"): invalid kind '${kind}' (expected task|bug)"
      errors=$((errors+1))
    fi
    if [[ -n "$feat" ]]; then
      if [[ "$feat" =~ ^[a-z][a-z0-9_]*$ ]]; then
        if ! find_feature_file "$feat" >/dev/null; then
          log_warn "$(basename "$f"): references unknown feature '${feat}' (legacy or stale)"
        fi
      else
        log_warn "$(basename "$f"): informal feature value '${feat}' (not a slug) — treated as ad-hoc"
      fi
    fi
  done < <(list_all_task_files)

  if (( errors > 0 )); then
    log_error "Validation failed: ${errors} issue(s)"
    exit 1
  fi
  log_info "Validation OK"
}

usage() {
  cat <<'EOF'
Taskman — epic, feature, and task management CLI.
Authoritative reference for both the CLI surface and the persistence model.

================================================================================
COMMANDS
================================================================================

USAGE
  ./scripts/taskman.sh <command> [options]

CREATION  (body on stdin; required sections enforced unless --no-validate)
  new epic    --slug <s> --title "<t>" [--no-validate]
  new feature --slug <s> --title "<t>" [--epic <s>] [--no-validate]
  new task    --title "<t>" [--feature <s>] [--kind task|bug] [--slug <s>]
              [--no-validate]

  Files created by `new` carry `draft: true` in their YAML header. The
  authoring skill re-reads the file, previews it, and then either:

    finalize <path>       # clear `draft: true` after the user approves
    discard  <path>       # delete the draft after the user rejects

LIFECYCLE  (tasks only — epics and features change state via close)
  move <task-path> <backlog|active|done>

LISTING
  list epics    [--status backlog|active|done]
  list features [--epic <s>] [--status backlog|active|done]
  list tasks    [--feature <s>] [--status backlog|active|done] [--kind task|bug]

EPICS
  epic show     <slug>
  epic close    <slug>        # fails unless all member features in done

FEATURES
  feature show  <slug>
  feature close <slug>        # fails unless all member tasks in done

INTEGRITY
  validate                    # lint all YAML headers, check orphans

ENVIRONMENT
  TASKMAN_TASKS_DIR           Override tasks dir (default: <repo>/tasks)

================================================================================
PERSISTENCE MODEL
================================================================================

STORAGE LAYOUT
  tasks/
    epics/             # epic files — flat folder; status field tracks state
      YYYYMMDD_<slug>.md
    features/          # feature files — flat folder; status field tracks state
      YYYYMMDD_<slug>.md
    backlog/           # task lifecycle subfolders
    active/
    done/

  Epic and feature files do NOT move between folders. Their lifecycle lives
  in the `status` YAML field.

  Task files move between backlog/, active/, done/ as they transition. The
  folder is the source of truth; the `status` YAML field must agree.

NAMING
  Epic file:    YYYYMMDD_<slug>.md       # creation date + slug
  Feature file: YYYYMMDD_<slug>.md       # creation date + slug
  Task file:    YYYYMMDD[_NN]_<slug>.md  # _NN added on same-day collisions

  Slug format:  [a-z][a-z0-9_]*          # lowercase, underscores only
                                         # no hyphens, no dots, no leading digit

LIFECYCLE PRECONDITIONS
  Task:    backlog → active → done   (set via `move`)
  Feature: backlog → active → done   (close enforces all member tasks done)
  Epic:    backlog → active → done   (close enforces all member features done)

  Intermediate transitions (backlog → active) have no mechanical
  precondition. Set by the initiator.

  A `## Summary` section MUST be appended to a task, feature, or epic file
  before it moves to `done`. Closure without a summary loses audit trail
  for future work in the same area.

VALIDATION BEHAVIOR
  `validate` lints all YAML headers and checks orphan references.
  Legacy / stale references (e.g., `feature: <slug>` pointing at a
  feature that no longer exists) are downgraded to WARNINGS — pre-rule
  files are implicitly ad-hoc; no retroactive rewrite required.

APPROVAL
  Human-initiated work: human approves backlog → active and active → done.
  Agent-initiated work: agent approves after validation and notification
  of the product owner.

RELATIONSHIP TO GIT
  A feature is the natural unit of a git worktree:
    one feature slug → one branch → one isolated dev stack.
  Epics span multiple worktrees. Operational convention, not enforced
  by taskman.

DOC-SYNC QUEUE (PostClose hook)
  After every successful close action — `move <path> done`, `feature close
  <slug>`, `epic close <slug>` — taskman appends an entry to:

    tasks/docs-sync-queue.txt

  Format (one line per entry):
    <ISO-8601 timestamp> <kind> <id-or-path>

  Example:
    2026-05-26T15:23:00Z task tasks/done/20260526_create_qa_subagent.md
    2026-05-26T15:35:00Z feature qa_subagent_and_validation

  `taskman.sh list` prints a one-line WARN hint when the queue is non-empty,
  so you see pending entries whenever you look at task state.

  Process the queue with `/tm:docs-sync`. The skill reads each entry,
  reviews upstream docs (vision → spec → requirements → architecture →
  linked spec) per rules/doc-maintenance.md, proposes diffs, and clears the
  entry on user approval.

  The queue is written by taskman.sh and cleared by /tm:docs-sync. Both
  are "observability records" — not lifecycle units. Direct CLI users can
  inspect or edit the queue file; the format is line-stable.

See: rules/task-composition.md   # frontmatter shapes, body sections, summary template
See: rules/doc-maintenance.md    # what to check on closure; how to propose updates
EOF
}

main() {
  if (( $# == 0 )); then usage; exit 1; fi
  local cmd="$1"; shift
  case "$cmd" in
    new)      cmd_new      "$@" ;;
    finalize) cmd_finalize "$@" ;;
    discard)  cmd_discard  "$@" ;;
    move)     cmd_move     "$@" ;;
    list)     cmd_list     "$@" ;;
    epic)     cmd_epic     "$@" ;;
    feature)  cmd_feature  "$@" ;;
    validate) cmd_validate "$@" ;;
    help|--help|-h) usage ;;
    *) die "Unknown command: $cmd (try 'taskman.sh help')" ;;
  esac
}

main "$@"
