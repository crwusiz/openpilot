#!/usr/bin/env bash
# Compare the requested package with actual installed bytes, not cached dates.
# plan prints reusable paths; hydrate fills missing staged files before apply.
set -euo pipefail
mode=${1:-}
[[ $# == 3 && ( "$mode" == plan || "$mode" == hydrate ) ]] || exit 1
stage=$(cd -- "$2" && pwd -P)
target=$(cd -- "$3" && pwd -P)
[[ "$stage" != "$target" && ! -L "$stage/SHA256SUMS" && -f "$stage/SHA256SUMS" ]] || exit 1
safe_path() {
  local root=$1 name=$2
  while :; do
    [[ ! -L "$root/$name" ]] || return 1
    [[ "$name" == */* ]] || break
    name=${name%/*}
    [[ ! -e "$root/$name" || -d "$root/$name" ]] || return 1
  done
}
seen=' '
while IFS= read -r line; do
  hash=${line:0:64}
  name=${line:66}
  [[ "$hash" =~ ^[a-f0-9]{64}$ && "${line:64:2}" == '  ' ]] || exit 1
  [[ "$name" =~ ^([a-zA-Z0-9_]+\.py|README\.md|requirements\.txt|receiver_assets\.txt|cluster-hdmi\.service|scripts/[a-zA-Z0-9_-]+\.sh|assets/fonts/[a-zA-Z0-9_-]+\.ttf|assets/icons/[a-zA-Z0-9_-]+\.png)$ ]] || exit 1
  [[ "$seen" != *" $name "* ]] || exit 1
  seen+="$name "
  safe_path "$stage" "$name" && safe_path "$target" "$name" || exit 1
  if [[ "$mode" == hydrate && -f "$stage/$name" ]]; then continue; fi
  actual=''
  if [[ -f "$target/$name" && -r "$target/$name" ]]; then
    actual=$(sha256sum -- "$target/$name")
    actual=${actual:0:64}
  fi
  if [[ "$actual" == "$hash" ]]; then
    if [[ "$mode" == plan ]]; then
      printf '%s\n' "$name"
    else
      if [[ "$name" == */* ]]; then mkdir -p -- "$stage/${name%/*}"; fi
      # Copying keeps staging independent of the live receiver and rollback.
      cp --reflink=auto -- "$target/$name" "$stage/$name"
    fi
  elif [[ "$mode" == hydrate ]]; then
    printf 'Installed file changed during staging: %s; retry the update.\n' "$name" >&2
    exit 1
  fi
done < "$stage/SHA256SUMS"
