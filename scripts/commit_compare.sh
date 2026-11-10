#!/bin/sh

set -eu

PARAMS_FILE=/data/params/d/CommitCompare
EXIT_FLAG=/data/log/commit_check_exit_flag
CHECK_LOG=/data/log/commit_check.log

log() {
  printf '%s\n' "$*" >&2
  printf '%s\n' "$*" 2>/dev/null >> "$CHECK_LOG" || true
}

fail() {
  status=$1
  shift
  log "Commit check failed: $*"
  exit "$status"
}

finish() {
  status=$?
  trap - 0
  if [ "$status" -ne 0 ]; then
    rm -f -- "$PARAMS_FILE" || true
  fi
  if ! printf '%s\n' "$status" > "$EXIT_FLAG"; then
    rm -f -- "$PARAMS_FILE" || true
    log "Commit check failed: cannot write completion status to $EXIT_FLAG."
    exit 1
  fi
  printf 'Exit status: %s\n' "$status" 2>/dev/null >> "$CHECK_LOG" || true
  exit "$status"
}
trap finish 0

mkdir -p -- "$(dirname -- "$EXIT_FLAG")"
printf '' 2>/dev/null > "$CHECK_LOG" || true
log 'Commit check started.'
rm -f -- "$EXIT_FLAG" "$PARAMS_FILE"
if cd -- "$(dirname -- "$0")/.."; then
  :
else
  fail "$?" 'cannot enter the openpilot checkout.'
fi

# Keep stderr warnings out of the branch name and commit hash.
if branch=$(git symbolic-ref --quiet --short HEAD); then
  :
else
  status=$?
  [ -z "$branch" ] || log "$branch"
  if [ "$status" -eq 1 ]; then
    fail "$status" 'checkout has a detached HEAD; check out a branch before checking for updates.'
  fi
  fail "$status" "cannot determine the current branch (git exit $status)."
fi
if local_commit=$(git rev-parse --verify HEAD); then
  :
else
  status=$?
  [ -z "$local_commit" ] || log "$local_commit"
  fail "$status" "cannot read the local HEAD commit (git exit $status)."
fi
[ -n "$local_commit" ] || fail 1 'local HEAD commit is empty.'
log "Checking origin branch '$branch' against local commit $local_commit."
if remote_refs=$(GIT_TERMINAL_PROMPT=0 timeout 10s git ls-remote --exit-code --refs origin "refs/heads/$branch" 2>&1); then
  :
else
  status=$?
  [ -z "$remote_refs" ] || log "$remote_refs"
  case "$status" in
    124) fail "$status" "remote lookup timed out after 10 seconds for origin branch '$branch'.";;
    2) fail "$status" "branch '$branch' was not found on origin.";;
    *) fail "$status" "remote lookup failed for origin branch '$branch' (git exit $status); check the Git error above.";;
  esac
fi
# ls-remote patterns can also match a ref suffix; select the exact branch.
remote_commit=$(printf '%s\n' "$remote_refs" | awk -v ref="refs/heads/$branch" '$2 == ref { print $1 }')
[ -n "$remote_commit" ] || fail 1 "origin did not return an exact commit for branch '$branch'."

operator='!='
if [ "$local_commit" = "$remote_commit" ]; then
  operator='=='
fi
if printf '"%s" %s "%s"\n' "$local_commit" "$operator" "$remote_commit" > "$PARAMS_FILE"; then
  log "Commit check succeeded: \"$local_commit\" $operator \"$remote_commit\"."
else
  fail "$?" "cannot store comparison result in $PARAMS_FILE."
fi
