from openpilot.common.swaglog import cloudlog


def enforce_cluster_transport(params, transport: str | None = None) -> bool:
  """Disable USB cluster when Chestnut needs the bus; network is unrestricted."""
  transport = transport or params.get("ClusterDisplayTransport") or "usb"
  if transport == "network":
    return True
  if params.get_bool("ChestnutLoading") or params.get_bool("ChestnutActive"):
    if params.get_bool("ClusterEnable"):
      params.put_bool("ClusterEnable", False, block=True)
      cloudlog.warning("USB cluster disabled to prioritize Chestnut eGPU; use network cluster")
    return False
  return True
