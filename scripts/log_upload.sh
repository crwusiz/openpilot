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

pi_log_upload_enabled() {
  [[ $(get_param "ClusterEnable") == 1 && $(get_param "ClusterDisplayTransport") == network ]]
}

crash_log_has_diagnostics() {
  local filename="$1" timestamp normal_records status=0
  [[ -f $filename ]] || return 1
  timestamp='\[[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\]'
  # Ignore only complete, known benign records. Do not require an exception
  # marker: native stacks can be binary, truncated, or start in a rotated file.
  normal_records="^([[:blank:]]*|$timestamp \\[CLUSTER_PROCESS_START\\] pid=[0-9]+ native_bootstrap=[01]"
  normal_records+="|$timestamp \\[CLUSTER_PROCESS_EXIT\\] pid=[0-9]+ (exit_code=0 reason=normal_exit manager_stop=[01]"
  normal_records+="|exit_code=-2 reason=SIGINT manager_stop=1|exit_code=-15 reason=SIGTERM manager_stop=1)"
  normal_records+=" logs=/data/log/cluster_debug\\.log,/data/log/cluster_crash\\.log)"$'\r?'"$"
  LC_ALL=C grep -aEvq -- "$normal_records" "$filename" || status=$?
  if (( status == 1 )); then return 1; fi
  # Inspection errors must not silently discard a potentially useful crash.
  return 0
}

has_crash_logs() {
  local directory filename
  directory=$(dirname "$1")
  for filename in cluster_crash.log cluster_crash.log.1; do
    if crash_log_has_diagnostics "$directory/$filename"; then return 0; fi
  done
  return 1
}

has_pending_crash_logs() {
  local job
  for job in "$(dirname "$1")/.cluster-pi-pending"/pending.*; do
    [[ -d $job && ! -L $job ]] || continue
    if [[ -f $job/cluster_crash.log || -f $job/cluster_crash.log.1 ]]; then return 0; fi
    # An interruption can happen after the C4 snapshot but before any crash
    # snapshot. Finish that C4 upload even if its live crash file disappeared.
    if [[ -f $job/cluster_debug.log && ( ! -f $job/c4_uploaded || ! -f $job/cluster_pi_debug.log ) ]]; then return 0; fi
  done
  return 1
}

snapshot_crash_logs() {
  local log_path="$1" directory="$2" source filename temporary status=0
  for filename in cluster_crash.log cluster_crash.log.1; do
    source="$(dirname "$log_path")/$filename"
    [[ -f $source ]] || continue
    # Publish only complete copies. A cancelled copy must never look like an
    # uploadable raw traceback on the next invocation.
    temporary="$directory/.$filename.snapshot"
    if cp -- "$source" "$temporary" && chmod 400 "$temporary"; then
      # Inspect the stable copy, never edit or truncate the live descriptor.
      if crash_log_has_diagnostics "$temporary"; then
        if mv -- "$temporary" "$directory/$filename"; then continue; fi
      else
        rm -f -- "$temporary"
        continue
      fi
      status=1
    else
      status=1
    fi
    rm -f -- "$temporary"
    printf 'CLUSTER_LOG_WARNING: %s 원본을 보관하지 못했습니다. 다른 로그 전송은 계속합니다.\n' "$filename"
  done
  return "$status"
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
  local outbox="$1" job="$2" temporary
  # Only remove known files in a directory created under this upload outbox.
  [[ $job == "$outbox"/pending.* && -d $job && ! -L $job ]] || return 1
  # SIGKILL can leave the collector's atomic-write temporary behind. Remove
  # only its known file pattern, so rmdir does not strand a completed job.
  for temporary in "$job"/.cluster_pi_debug.log.*; do
    [[ -f $temporary || -L $temporary ]] || continue
    rm -f -- "$temporary" || return 1
  done
  rm -f -- "$job/prefix" "$job/cluster_debug.log" "$job/cluster_pi_debug.log" \
    "$job/cluster_crash.log" "$job/cluster_crash.log.1" \
    "$job/.cluster_crash.log.snapshot" "$job/.cluster_crash.log.1.snapshot" \
    "$job/cluster_crash.log.uploaded" "$job/cluster_crash.log.1.uploaded" \
    "$job/c4_uploaded" "$job/pi_uploaded" "$job/collection_done" "$job/collection_status" "$job/lock"
  rmdir -- "$job"
}

job_complete() {
  local directory="$1" filename
  [[ -f $directory/c4_uploaded ]] || return 1
  for filename in cluster_crash.log cluster_crash.log.1; do
    if [[ -f $directory/$filename && ! -f $directory/$filename.uploaded ]]; then return 1; fi
  done
  [[ ! -f $directory/cluster_pi_debug.log || -f $directory/pi_uploaded ]]
}

upload_crash_logs() {
  local directory="$1" prefix="$2" outbox="$3" mode="${4:-pi}" deadline="${5:-0}"
  local filename remaining phase status=0
  for filename in cluster_crash.log cluster_crash.log.1; do
    [[ -f $directory/$filename && ! -f $directory/$filename.uploaded ]] || continue
    if ! crash_log_has_diagnostics "$directory/$filename"; then
      touch "$directory/$filename.uploaded"
      continue
    fi
    remaining=10
    if [[ $mode == retry ]]; then
      remaining=$((deadline - SECONDS))
      if (( remaining <= 0 )); then status=1; break; fi
      (( remaining <= 10 )) || remaining=10
    fi
    phase=${filename//./_}
    if upload_file "$directory/$filename" "$prefix" "$mode" "$remaining"; then
      touch "$directory/$filename.uploaded"
      [[ -z $outbox ]] || trace_upload "$outbox" "$directory" "${mode}_${phase}_uploaded"
    else
      status=1
      [[ -z $outbox ]] || trace_upload "$outbox" "$directory" "${mode}_${phase}_failed_saved"
      if [[ -n $outbox ]]; then
        printf 'CLUSTER_LOG_WARNING: %s 전송에 실패했습니다. 보관된 원본은 다음 Cluster 업로드에서 다시 전송합니다.\n' "$filename"
      else
        printf 'CLUSTER_LOG_WARNING: %s 전송에 실패했습니다.\n' "$filename"
      fi
    fi
  done
  return "$status"
}

upload_cluster_fallback() (
  local log_path="$1" prefix="$2" warning="$3" directory filename
  directory=$(mktemp -d "${TMPDIR:-/tmp}/cluster-crash-upload.XXXXXXXX") || directory=""
  prefix="$(date +%y-%m-%d-%H:%M:%S)-${directory##*.}-${BASHPID}_${prefix#*_}"
  if [[ -n $directory ]]; then
    trap 'rm -f -- "$directory/cluster_crash.log" "$directory/cluster_crash.log.1" "$directory/.cluster_crash.log.snapshot" "$directory/.cluster_crash.log.1.snapshot" "$directory/cluster_crash.log.uploaded" "$directory/cluster_crash.log.1.uploaded"; rmdir -- "$directory"' EXIT
    snapshot_crash_logs "$log_path" "$directory" || true
  fi
  upload_file "$log_path" "$prefix" || return 1
  if [[ -n $directory ]]; then
    if ! upload_crash_logs "$directory" "$prefix" ""; then
      printf '%s\n' 'CLUSTER_LOG_WARNING: 보관 공간을 사용할 수 없어 실패한 crash 파일의 자동 재전송 사본은 남기지 못했습니다.'
    fi
  else
    for filename in cluster_crash.log cluster_crash.log.1; do
      crash_log_has_diagnostics "$(dirname "$log_path")/$filename" || continue
      upload_file "$(dirname "$log_path")/$filename" "$prefix" pi || \
        printf 'CLUSTER_LOG_WARNING: %s 전송 및 임시 사본 보관에 실패했습니다.\n' "$filename"
    done
  fi
  printf '%s\n' "$warning"
)

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
    touch "$directory/pi_uploaded"
    trace_upload "$outbox" "$directory" pi_uploaded
    if (( status == 0 )); then
      log "SUCCESS" "C4 and Orange Pi logs uploaded with the same filename prefix."
    else
      printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그와 Pi 실패 보고서는 업로드됐지만 Pi 진단 로그 수집에는 실패했습니다.'
    fi
  else
    trace_upload "$outbox" "$directory" pi_upload_failed_saved
    printf '%s\n' 'CLUSTER_PI_LOG_WARNING: C4 로그는 업로드됐지만 Pi 파일 전송은 실패했습니다. C4에 보관해 다음 Cluster 업로드에서 다시 전송합니다.'
  fi
  return 0
}

retry_pending() (
  local outbox="$1" pi_enabled="$2" job prefix remaining filename eligible count=0 deadline=$((SECONDS + 20))
  shopt -s nullglob
  for job in "$outbox"/pending.*; do
    (( count < 2 && SECONDS < deadline )) || break
    [[ -d $job && ! -L $job && -s $job/prefix ]] || continue
    eligible=0
    for filename in cluster_crash.log cluster_crash.log.1; do
      if [[ -f $job/$filename && ! -f $job/$filename.uploaded ]]; then eligible=1; fi
    done
    if [[ $pi_enabled == 1 && -s $job/cluster_pi_debug.log && ! -f $job/pi_uploaded ]]; then eligible=1; fi
    if [[ -f $job/cluster_debug.log && ! -f $job/c4_uploaded ]]; then eligible=1; fi
    if job_complete "$job" && { [[ $pi_enabled == 1 || ! -f $job/cluster_pi_debug.log ]] || [[ -f $job/cluster_crash.log || -f $job/cluster_crash.log.1 ]]; }; then
      eligible=1
    fi
    (( eligible == 1 )) || continue
    # A still running collector/uploader keeps this lock across UI cancellation.
    # Never recreate a lock unlinked by a finishing uploader. If we opened its
    # old inode just before cleanup, recheck the files after acquiring the lock.
    { exec 9< "$job/lock"; } 2>/dev/null || continue
    if command -v flock >/dev/null 2>&1; then flock -n 9 || continue; fi
    [[ -d $job && -s $job/prefix ]] || continue
    # Older versions saved startup-only files too. Finish these snapshots
    # locally under their job lock; never manufacture a crash upload for them.
    for filename in cluster_crash.log cluster_crash.log.1; do
      if [[ -f $job/$filename && ! -f $job/$filename.uploaded ]] && ! crash_log_has_diagnostics "$job/$filename"; then
        touch "$job/$filename.uploaded"
      fi
    done
    # A process can stop after writing the last success marker but before
    # cleanup. Such completed jobs still need their lock before removal.
    if job_complete "$job"; then remove_job "$outbox" "$job"; continue; fi
    prefix=$(cat "$job/prefix")
    [[ $prefix != */* && $prefix != *$'\n'* && -n $prefix ]] || continue
    count=$((count + 1))
    trace_upload "$outbox" "$job" retry_started
    if [[ ! -f $job/c4_uploaded ]]; then
      remaining=$((deadline - SECONDS)); (( remaining > 0 )) || break
      (( remaining <= 10 )) || remaining=10
      if ! upload_file "$job/cluster_debug.log" "$prefix" retry "$remaining"; then
        trace_upload "$outbox" "$job" retry_c4_failed_saved
        printf '%s\n' 'CLUSTER_LOG_WARNING: 보관된 이전 Cluster 파일 전송에 실패했습니다. 다음 업로드에서 다시 시도합니다.'
        continue
      fi
      touch "$job/c4_uploaded"
    fi
    upload_crash_logs "$job" "$prefix" "$outbox" retry "$deadline" || true
    if [[ $pi_enabled == 1 && -s $job/cluster_pi_debug.log && ! -f $job/pi_uploaded ]]; then
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
        touch "$job/pi_uploaded"
        trace_upload "$outbox" "$job" retry_pi_uploaded
        if [[ $(cat "$job/collection_status") != 0 ]]; then
          printf '%s\n' 'CLUSTER_PI_LOG_WARNING: 이전 Pi 수집 실패 또는 중단 보고서를 기존 C4 파일명과 함께 전송했습니다.'
        fi
      else
        trace_upload "$outbox" "$job" retry_pi_failed_saved
        printf '%s\n' 'CLUSTER_PI_LOG_WARNING: 보관된 이전 Pi 파일 전송에 실패했습니다. 다음 업로드에서 다시 시도합니다.'
      fi
    fi
    if job_complete "$job"; then remove_job "$outbox" "$job"; fi
  done
  for job in "$outbox"/pending.*; do
    if [[ -d $job && ! -L $job ]]; then
      for filename in cluster_crash.log cluster_crash.log.1; do
        if [[ -f $job/$filename && ! -f $job/$filename.uploaded ]]; then
          printf '%s\n' 'CLUSTER_LOG_WARNING: 아직 전송되지 않은 이전 crash 원본을 C4에 보관 중입니다. 다음 Cluster 업로드에서 다시 시도합니다.'
          return 0
        fi
      done
      if [[ $pi_enabled == 1 ]]; then
        printf '%s\n' 'CLUSTER_PI_LOG_WARNING: 아직 전송되지 않은 이전 Cluster 파일을 C4에 보관 중입니다. 다음 Cluster 업로드에서 다시 시도합니다.'
        return 0
      fi
    fi
  done
  return 0
)

upload_cluster() (
  umask 077
  local log_path="$1" prefix="$2" outbox directory pending_kib snapshot_bytes filename bytes pi_enabled=0 warning_prefix
  local -a pending_jobs
  if pi_log_upload_enabled; then pi_enabled=1; fi
  warning_prefix=CLUSTER_LOG_WARNING
  if [[ $pi_enabled == 1 ]] && ! has_crash_logs "$log_path"; then warning_prefix=CLUSTER_PI_LOG_WARNING; fi
  outbox="$(dirname "$log_path")/.cluster-pi-pending"
  if [[ -L $outbox ]] || ! mkdir -p -- "$outbox" || ! chmod 700 "$outbox"; then
    upload_cluster_fallback "$log_path" "$prefix" \
      "$warning_prefix: C4 로그는 업로드됐지만 추가 로그 보관 경로를 만들지 못했습니다. Crash 파일은 별도 전송을 시도했으며 Pi 수집은 생략했습니다." || return 1
    return 0
  fi
  retry_pending "$outbox" "$pi_enabled"
  # Serialize only space reservation/snapshot creation, never the network work.
  exec 8> "$outbox/reservation.lock"
  if command -v flock >/dev/null 2>&1; then flock -w 2 8 || {
    exec 8>&-
    upload_cluster_fallback "$log_path" "$prefix" \
      "$warning_prefix: C4 로그는 업로드됐지만 다른 로그 보관 작업이 진행 중입니다. Crash 파일은 별도 전송을 시도했으며 새 Pi 수집은 생략했습니다." || return 1
    return 0
  }; fi
  shopt -s nullglob
  pending_jobs=("$outbox"/pending.*)
  snapshot_bytes=$(wc -c < "$log_path") || return 1
  for filename in cluster_crash.log cluster_crash.log.1; do
    if [[ -f $(dirname "$log_path")/$filename ]]; then
      bytes=$(wc -c < "$(dirname "$log_path")/$filename") || bytes=0
      snapshot_bytes=$((snapshot_bytes + bytes))
    fi
  done
  if ! read -r pending_kib _ < <(du -sk -- "$outbox"); then pending_kib=$PI_OUTBOX_MAX_KIB; fi
  # Reserve 512 KiB for every in-flight report, plus metadata/history space.
  if (( ${#pending_jobs[@]} >= PI_OUTBOX_MAX_JOBS || pending_kib + (snapshot_bytes + 1023) / 1024 + (${#pending_jobs[@]} + 1) * 512 + 1024 >= PI_OUTBOX_MAX_KIB )); then
    exec 8>&-
    upload_cluster_fallback "$log_path" "$prefix" \
      "$warning_prefix: C4 로그는 업로드됐지만 추가 로그 보관 한도에 도달했습니다. 이전 파일은 보존했고 Crash 파일은 별도 전송을 시도했으며 새 Pi 수집은 생략했습니다." || return 1
    trace_upload "$outbox" "$outbox" outbox_limit_c4_only
    return 0
  fi
  directory=$(mktemp -d "$outbox/pending.XXXXXXXX") || {
    exec 8>&-
    upload_cluster_fallback "$log_path" "$prefix" \
      "$warning_prefix: C4 로그는 업로드됐지만 추가 로그 보관을 준비하지 못했습니다. Crash 파일은 별도 전송을 시도했으며 Pi 수집은 생략했습니다." || return 1
    return 0
  }
  exec 9> "$directory/lock"
  if command -v flock >/dev/null 2>&1; then flock -n 9 || return 1; fi
  # Minute-only names collide on quick retries. Preserve each old NAS pair and
  # give the new pair the same unique token as its local pending directory.
  prefix="$(date +%y-%m-%d-%H:%M:%S)-${directory##*.}_${prefix#*_}"
  printf '%s\n' "$prefix" > "$directory/prefix"
  if [[ $pi_enabled == 1 ]]; then
    printf '=== Orange Pi cluster diagnostics ===\nCollection status: pending\nFailure: upload or collection has not completed.\n' > "$directory/cluster_pi_debug.log"
  fi
  # Persist a stable C4 snapshot too: an interrupted FTP request may already have
  # succeeded remotely, so retry can safely finish the original pair.
  if ! cp -- "$log_path" "$directory/cluster_debug.log"; then
    remove_job "$outbox" "$directory"
    return 1
  fi
  snapshot_crash_logs "$log_path" "$directory" || true
  chmod 400 "$directory/prefix" "$directory/cluster_debug.log"
  exec 8>&-
  trace_upload "$outbox" "$directory" c4_upload_started
  if ! upload_file "$directory/cluster_debug.log" "$prefix"; then
    trace_upload "$outbox" "$directory" c4_upload_failed_saved
    return 1
  fi
  touch "$directory/c4_uploaded"
  trace_upload "$outbox" "$directory" c4_uploaded
  upload_crash_logs "$directory" "$prefix" "$outbox" || true
  if [[ $pi_enabled == 1 ]]; then
    upload_pi_log "$directory/cluster_debug.log" "$prefix" "$outbox" "$directory"
  fi
  if job_complete "$directory"; then remove_job "$outbox" "$directory"; fi
  return 0
)

main() {
  # Shared for the entire snapshot/collection/upload lifecycle. The successful
  # Git Pull reset takes the exclusive lock before clearing pending directories.
  # Keep the lock outside /data/log so a reset never replaces its inode.
  if command -v flock >/dev/null 2>&1; then
    exec 7> "${TMPDIR:-/tmp}/openpilot_log_upload.lock"
    if ! flock -s -w 5 7; then
      log "ERROR" "Log reset is in progress. Retry log upload after restart."
      return 1
    fi
  fi
  if [ $# -eq 0 ]; then
    echo -e "${YELLOW}Usage: $0 <LOG_FILENAME>${NC}"
    exit 1
  fi

  local log_path prefix
  log_path=$(resolve_log_path "$1")
  prefix=$(upload_prefix)
  if [[ "$(basename "$log_path")" == tmux_error.log && ${LOG_UPLOAD_PREFIX:-} =~ ^[A-Za-z0-9:_-]+$ ]]; then
    # Automatic crash retries use the capture identity, not a new minute each
    # attempt. Keep the normal car/device grouping in the remote filename.
    prefix="${LOG_UPLOAD_PREFIX}_${prefix#*_}"
  fi
  if [[ "$(basename "$log_path")" == cluster_debug.log ]]; then
    # Native C4 crash files apply to every cluster transport. Only Pi work is
    # gated; its saved reports remain untouched while Network is disabled.
    if pi_log_upload_enabled || has_crash_logs "$log_path" || has_pending_crash_logs "$log_path"; then
      upload_cluster "$log_path" "$prefix"
    else
      upload_file "$log_path" "$prefix"
    fi
  elif [[ "$(basename "$log_path")" == cluster_pi_debug.log ]] && ! pi_log_upload_enabled; then
    log "INFO" "Skipping Orange Pi log upload: Network cluster is not enabled."
  elif [[ "$(basename "$log_path")" == cluster_crash.log || "$(basename "$log_path")" == cluster_crash.log.1 ]] && [[ -f $log_path ]] && ! crash_log_has_diagnostics "$log_path"; then
    log "INFO" "Skipping cluster crash upload: no crash diagnostics."
  else
    upload_file "$log_path" "$prefix"
  fi
}

main "$@"
