"""Select a display backend after the common cluster process bootstrap."""

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.addon.cluster.cluster_config import ClusterConfig
from openpilot.selfdrive.addon.cluster.cluster_logging import close_log, initialize_log
from openpilot.selfdrive.addon.cluster.cluster_policy import enforce_cluster_transport


def cluster_main():
  initialize_log()
  try:
    cloudlog.info("Initializing Cluster Config...")
    config = ClusterConfig()
    if not enforce_cluster_transport(config.params, config.display_transport):
      return
    from openpilot.selfdrive.addon.cluster.dependencies import ensure_cluster_dependencies
    if not ensure_cluster_dependencies(config):
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
