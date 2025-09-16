#!/bin/bash

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "master")

remote_commit=$(git ls-remote origin "$branch" | awk '{print substr($1,1,7)}' 2>/dev/null || echo "")
local_commit=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "")

if [ -z "$remote_commit" ] || [ -z "$local_commit" ]; then
  echo "\"ERROR\" != \"ERROR\"" > /data/params/d/CommitCompare
  exit 1
fi

echo "\"$local_commit\" $([ "$local_commit" = "$remote_commit" ] && echo "==" || echo "!=") \"$remote_commit\"" > /data/params/d/CommitCompare

echo 0 > /data/commit_check_exit_code.txt
