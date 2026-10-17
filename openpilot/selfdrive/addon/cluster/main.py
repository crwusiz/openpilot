"""Select a display backend after the common cluster process bootstrap."""

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.addon.cluster.cluster_config import ClusterConfig
from openpilot.selfdrive.addon.cluster.cluster_logging import close_log, flog, initialize_log
from openpilot.selfdrive.addon.cluster.cluster_policy import enforce_cluster_transport


def cluster_main():
  initialize_log()
  try:
    cloudlog.info("Initializing Cluster Config...")
    config = ClusterConfig()
    codec = getattr(config, "usb_codec" if config.display_transport == "usb" else "network_codec", "jpeg")
    flog(f"[CLUSTER_START] transport={config.display_transport} codec={codec}")
    if not enforce_cluster_transport(config.params, config.display_transport):
      flog("[CLUSTER_EXIT] Selected transport is blocked by the cluster policy.")
      return
    from openpilot.selfdrive.addon.cluster.dependencies import ensure_cluster_dependencies
    if not ensure_cluster_dependencies(config):
      flog("[CLUSTER_EXIT] Cluster selection changed while preparing dependencies.")
      return

    if config.display_transport == "usb":
      from openpilot.selfdrive.addon.cluster.usb_display.main import run_usb_cluster
      run_usb_cluster(config)
    elif config.display_transport == "network":
      from openpilot.selfdrive.addon.cluster.hdmi_display.main import run_network_cluster
      run_network_cluster(config)
    else:
      raise ValueError(f"Unsupported cluster display transport: {config.display_transport}")
  finally:
    close_log()
