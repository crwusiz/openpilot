#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "${SCRIPT_DIR}/ftp_upload_utils.sh"

readonly LOG_BASE_DIR="/data/log"
readonly PI_OUTBOX_MAX_KIB=65536
readonly PI_OUTBOX_MAX_JOBS=16

resolve_log_path() {
  if [[ "$1" == /* ]]; then printf '%s\n' "$1"
  else printf '%s/%s\n' "$LOG_BASE_DIR" "$(basename "$1")"; fi
}

upload_prefix() {
  printf '%s_%s_%s' "$(date +%y-%m-%d-%H:%M)" "$(get_param "CarName")" "$(get_param "DongleId")"
}

upload_file() {
  local local_file_path="$1"
  local_file_path=$(resolve_log_path "$local_file_path")
  local file_name
  file_name=$(basename "$local_file_path")

  if [ ! -f "$local_file_path" ]; then
    log "ERROR" "Log file not found: $local_file_path"
    return 1
  fi

  log "INFO" "Log file found: $local_file_path"

  local prefix=${2:-}
  if [[ -z "$prefix" ]]; then prefix=$(upload_prefix); fi
  local target_filename="${prefix}_${file_name}"
  local remote_path="/${FTP_DEFAULT_DIR}/${target_filename}"

  log "INFO" "Starting upload to ${FTP_HOST}..."
  log "INFO" "Target: $target_filename"

  local -a limits=(--max-time 30 --retry-max-time 60)
  if [[ ${3:-} == pi ]]; then
    # A single bounded attempt; a later user upload retries the saved original.
    limits=(--connect-timeout 8 --max-time 20 --retry 0)
  elif [[ ${3:-} == retry ]]; then
    limits=(--connect-timeout 5 --max-time "${4:-10}" --retry 0)
  fi
  if ftp_upload_file "$local_file_path" "$remote_path" "${limits[@]}"; then
    log "SUCCESS" "Upload completed successfully."
    return 0
  else
    log "ERROR" "Upload failed."
    return 1
  fi
}

trace_upload() {
  # Keep only summaries, never remote stderr, SSH arguments or credentials.
  printf '%s job=%s phase=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(basename "$2")" "$3" >> "$1/history.log"
  if [[ $(wc -c < "$1/history.log") -gt 65536 ]]; then
    tail -n 200 "$1/history.log" > "$1/history.log.tmp.$$" && mv -- "$1/history.log.tmp.$$" "$1/history.log"
  fi
  printf 'CLUSTER_LOG_UPLOAD: job=%s phase=%s\n' "$(basename "$2")" "$3"
}

remove_job() {
  local outbox="$1" job="$2"
  # Only remove known files in a directory created under this upload outbox.
  [[ $job == "$outbox"/pending.* && -d $job && ! -L $job ]] || return 1
  rm -f -- "$job/prefix" "$job/cluster_debug.log" "$job/cluster_pi_debug.log" \
    "$job/c4_uploaded" "$job/collection_done" "$job/collection_status" "$job/lock"
  rmdir -- "$job"
}

upload_pi_log() {
  local c4_log="$1" prefix="$2" outbox="$3" directory="$4" sidecar collector status=0
  sidecar="$directory/cluster_pi_debug.log"
  collector="${SCRIPT_DIR}/../openpilot/selfdrive/addon/cluster/hdmi_display/pi_log_collector.py"
  trace_upload "$outbox" "$directory" collecting
  if [[ -f "$collector" ]]; then
    "${CLUSTER_PYTHON:-python3}" "$collector" --c4-log "$c4_log" --output "$sidecar" || status=$?
  else
    status=1
  fi
  if (( status != 0 )); then
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 Pi 로그 수집에 실패했습니다. 실패 보고서를 함께 업로드합니다.'
  fi
  if [[ ! -s "$sidecar" ]]; then
    printf '=== Orange Pi cluster diagnostics ===\nCollection status: failed\nFailure: Pi collector missing or could not produce a log (exit %s).\n' "$status" > "$sidecar"
    status=1
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: Pi 진단 로그를 얻지 못해 실패 보고서를 준비했습니다.'
  elif (( status != 0 )) && ! grep -q '^Collection status: failed$' "$sidecar"; then
    printf '\nCollection status: failed\nFailure: Pi collector missing or interrupted before producing a complete log (exit %s).\n' "$status" >> "$sidecar"
  fi
  printf '%s\n' "$status" > "$directory/collection_status"
  touch "$directory/collection_done"
  chmod 400 "$sidecar" "$directory/collection_status"
  trace_upload "$outbox" "$directory" "collected_exit_$status"
  if upload_file "$sidecar" "$prefix" pi; then
    trace_upload "$outbox" "$directory" pi_uploaded
    if (( status == 0 )); then
      log "SUCCESS" "C4 and Orange Pi logs uploaded with the same filename prefix."
    else
      printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그와 Pi 실패 보고서는 업로드됐지만 Pi 진단 로그 수집에는 실패했습니다.'
    fi
    remove_job "$outbox" "$directory"
  else
    trace_upload "$outbox" "$directory" pi_upload_failed_saved
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 Pi 파일 전송은 실패했습니다. C4에 보관해 다음 Cluster 업로드에서 다시 전송합니다.'
  fi
  return 0
}

retry_pending() (
  local outbox="$1" job prefix remaining count=0 deadline=$((SECONDS + 20))
  shopt -s nullglob
  for job in "$outbox"/pending.*; do
    (( count < 2 && SECONDS < deadline )) || break
    [[ -d $job && ! -L $job && -s $job/prefix && -s $job/cluster_pi_debug.log ]] || continue
    # A still running collector/uploader keeps this lock across UI cancellation.
    exec 9> "$job/lock" 2>/dev/null || continue
    if command -v flock >/dev/null 2>&1; then flock -n 9 || continue; fi
    prefix=$(cat "$job/prefix")
    [[ $prefix != */* && $prefix != *$'\n'* && -n $prefix ]] || continue
    count=$((count + 1))
    trace_upload "$outbox" "$job" retry_started
    if [[ ! -f $job/c4_uploaded ]]; then
      remaining=$((deadline - SECONDS)); (( remaining > 0 )) || break
      (( remaining <= 10 )) || remaining=10
      if ! upload_file "$job/cluster_debug.log" "$prefix" retry "$remaining"; then
        trace_upload "$outbox" "$job" retry_c4_failed_saved
        printf '%s\n' 'CLUSTER_PI_LOG_WARNING: 보관된 이전 Cluster 파일 전송에 실패했습니다. 다음 업로드에서 다시 시도합니다.'
        continue
      fi
      touch "$job/c4_uploaded"
    fi
    if [[ ! -f $job/collection_done ]]; then
      # The collector atomically writes its final report before the shell marks
      # completion. Keep that report intact if cancellation falls in between.
      if grep -Eq '^Collection status: (ok|skipped)$' "$job/cluster_pi_debug.log"; then
        printf '0\n' > "$job/collection_status"
      else
        if ! grep -q '^Collection status: failed$' "$job/cluster_pi_debug.log"; then
          printf '\nCollection status: failed\nFailure: previous collection was interrupted; retained original context, not recollected.\n' >> "$job/cluster_pi_debug.log"
        fi
        printf '1\n' > "$job/collection_status"
      fi
      touch "$job/collection_done"
    fi
    remaining=$((deadline - SECONDS)); (( remaining > 0 )) || break
    (( remaining <= 10 )) || remaining=10
    if upload_file "$job/cluster_pi_debug.log" "$prefix" retry "$remaining"; then
      trace_upload "$outbox" "$job" retry_pi_uploaded
      if [[ $(cat "$job/collection_status") != 0 ]]; then
        printf '%s\n' 'CLUSTER_PI_LOG_WARNING: 이전 Pi 수집 실패 또는 중단 보고서를 기존 C4 파일명과 함께 전송했습니다.'
      fi
      remove_job "$outbox" "$job"
    else
      trace_upload "$outbox" "$job" retry_pi_failed_saved
      printf '%s\n' 'CLUSTER_PI_LOG_WARNING: 보관된 이전 Pi 파일 전송에 실패했습니다. 다음 업로드에서 다시 시도합니다.'
    fi
  done
  for job in "$outbox"/pending.*; do
    if [[ -d $job && ! -L $job ]]; then
      printf '%s\n' 'CLUSTER_PI_LOG_WARNING: 아직 전송되지 않은 이전 Cluster 파일을 C4에 보관 중입니다. 다음 Cluster 업로드에서 다시 시도합니다.'
      break
    fi
  done
  return 0
)

upload_cluster() (
  umask 077
  local log_path="$1" prefix="$2" outbox directory pending_kib snapshot_bytes
  local -a pending_jobs
  outbox="$(dirname "$log_path")/.cluster-pi-pending"
  if [[ -L $outbox ]] || ! mkdir -p -- "$outbox" || ! chmod 700 "$outbox"; then
    upload_file "$log_path" "$prefix" || return 1
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 Pi 파일 보관 경로를 만들지 못했습니다.'
    return 0
  fi
  retry_pending "$outbox"
  # Serialize only space reservation/snapshot creation, never the network work.
  exec 8> "$outbox/reservation.lock"
  if command -v flock >/dev/null 2>&1; then flock -w 2 8 || {
    upload_file "$log_path" "$prefix" || return 1
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 다른 Pi 파일 보관 작업이 진행 중이라 새 Pi 수집을 시작하지 못했습니다.'
    return 0
  }; fi
  shopt -s nullglob
  pending_jobs=("$outbox"/pending.*)
  snapshot_bytes=$(wc -c < "$log_path") || return 1
  if ! read -r pending_kib _ < <(du -sk -- "$outbox"); then pending_kib=$PI_OUTBOX_MAX_KIB; fi
  # Reserve 512 KiB for every in-flight report, plus metadata/history space.
  if (( ${#pending_jobs[@]} >= PI_OUTBOX_MAX_JOBS || pending_kib + (snapshot_bytes + 1023) / 1024 + (${#pending_jobs[@]} + 1) * 512 + 1024 >= PI_OUTBOX_MAX_KIB )); then
    exec 8>&-
    upload_file "$log_path" "$prefix" || return 1
    trace_upload "$outbox" "$outbox" outbox_limit_c4_only
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 Pi 파일 보관 한도에 도달했습니다. 이전 파일은 보존했으며 새 Pi 로그 수집은 생략했습니다.'
    return 0
  fi
  directory=$(mktemp -d "$outbox/pending.XXXXXXXX") || {
    upload_file "$log_path" "$prefix" || return 1
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 Pi 로그 수집을 준비하지 못했습니다.'
    return 0
  }
  exec 9> "$directory/lock"
  if command -v flock >/dev/null 2>&1; then flock -n 9 || return 1; fi
  # Minute-only names collide on quick retries. Preserve each old NAS pair and
  # give the new pair the same unique token as its local pending directory.
  prefix="$(date +%y-%m-%d-%H:%M:%S)-${directory##*.}_${prefix#*_}"
  printf '%s\n' "$prefix" > "$directory/prefix"
  printf '=== Orange Pi cluster diagnostics ===\nCollection status: pending\nFailure: upload or collection has not completed.\n' > "$directory/cluster_pi_debug.log"
  # Persist a stable C4 snapshot too: an interrupted FTP request may already have
  # succeeded remotely, so retry can safely finish the original pair.
  if ! cp -- "$log_path" "$directory/cluster_debug.log"; then
    remove_job "$outbox" "$directory"
    return 1
  fi
  chmod 400 "$directory/prefix" "$directory/cluster_debug.log"
  exec 8>&-
  trace_upload "$outbox" "$directory" c4_upload_started
  if ! upload_file "$directory/cluster_debug.log" "$prefix"; then
    trace_upload "$outbox" "$directory" c4_upload_failed_saved
    return 1
  fi
  touch "$directory/c4_uploaded"
  trace_upload "$outbox" "$directory" c4_uploaded
  upload_pi_log "$directory/cluster_debug.log" "$prefix" "$outbox" "$directory"
)

main() {
  if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Usage: $0 <LOG_FILENAME>${NC}"
    exit 1
  fi

  local log_path prefix
  log_path=$(resolve_log_path "$1")
  prefix=$(upload_prefix)
  if [[ "$(basename "$log_path")" == cluster_debug.log ]]; then
    upload_cluster "$log_path" "$prefix"
  else
    upload_file "$log_path" "$prefix"
  fi
}

main "$@"
