"""Prepare a newly selected cluster mode without installing while onroad."""

import time

from openpilot.selfdrive.addon.dependencies import (
  DependencyError, DependencyInstallCancelled, activate_groups, groups_for_cluster, groups_ready, prepare_groups,
)
from openpilot.selfdrive.addon.cluster.cluster_logging import flog


def _still_selected(config):
  params = config.params
  if not params.get_bool("ClusterEnable"):
    return False
  transport = params.get("ClusterDisplayTransport") or config.display_transport
  return transport == config.display_transport


def ensure_cluster_dependencies(config):
  groups = groups_for_cluster(config.display_transport)
  try:
    if groups_ready(groups):
      activate_groups(groups)
      return True
  except DependencyError as error:
    flog(f"[CLUSTER_DEPS_ERROR] {error}")
    raise

  flog(f"[CLUSTER_DEPS_WAIT] Required groups: {','.join(groups)}; preparation waits for offroad.")
  retry_at = 0.0
  next_wait_log = time.monotonic() + 30.0
  while _still_selected(config):
    now = time.monotonic()
    if now >= next_wait_log:
      flog(f"[CLUSTER_DEPS_WAIT] Still waiting; offroad={config.params.get_bool('IsOffroad')} groups={','.join(groups)}")
      next_wait_log = now + 30.0
    if config.params.get_bool("IsOffroad") and time.monotonic() >= retry_at:
      try:
        prepare_groups(groups, cancel_check=lambda: not config.params.get_bool("IsOffroad") or not _still_selected(config))
        if not _still_selected(config):
          return False
        activate_groups(groups)
        flog(f"[CLUSTER_DEPS_READY] Prepared groups: {','.join(groups)}")
        return True
      except DependencyInstallCancelled:
        flog("[CLUSTER_DEPS_WAIT] Preparation cancelled because driving state or cluster selection changed.")
      except DependencyError as error:
        flog(f"[CLUSTER_DEPS_ERROR] {error}; retry after 30 seconds while offroad.")
        retry_at = time.monotonic() + 30.0
    time.sleep(1.0)
  return False
