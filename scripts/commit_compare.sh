#!/bin/sh

set -eu

PARAMS_FILE=/data/params/d/CommitCompare
EXIT_FLAG=/data/log/commit_check_exit_flag

finish() {
  status=$?
  trap - 0
  if [ "$status" -ne 0 ]; then
    rm -f -- "$PARAMS_FILE" || true
  fi
  if ! printf '%s\n' "$status" > "$EXIT_FLAG"; then
    rm -f -- "$PARAMS_FILE" || true
    exit 1
  fi
  exit "$status"
}
trap finish 0

mkdir -p -- "$(dirname -- "$EXIT_FLAG")"
rm -f -- "$EXIT_FLAG" "$PARAMS_FILE"
cd -- "$(dirname -- "$0")/.."

branch=$(git symbolic-ref --quiet --short HEAD)
local_commit=$(git rev-parse --verify HEAD)
remote_refs=$(timeout 10s git ls-remote --exit-code --refs origin "refs/heads/$branch")
# ls-remote patterns can also match a ref suffix; select the exact branch.
remote_commit=$(printf '%s\n' "$remote_refs" | awk -v ref="refs/heads/$branch" '$2 == ref { print $1 }')
[ -n "$local_commit" ]
[ -n "$remote_commit" ]

operator='!='
if [ "$local_commit" = "$remote_commit" ]; then
  operator='=='
fi
printf '"%s" %s "%s"\n' "$local_commit" "$operator" "$remote_commit" > "$PARAMS_FILE"
