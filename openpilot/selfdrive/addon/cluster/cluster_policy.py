from openpilot.common.hardware.usb import is_chestnut_usb_id, read_int, usb_devices
from openpilot.common.swaglog import cloudlog


def _chestnut_enumerated() -> bool:
  # modeld may set ChestnutLoading for any Type-C cable while waiting for
  # enumeration. Confirm device identity before persistently disabling USB.
  # ROM/old-firmware devices still need the bus for flashing and loading.
  return any(is_chestnut_usb_id(read_int(device / "idVendor", 16), read_int(device / "idProduct", 16),
                                include_bootloader=True) for device in usb_devices())


def enforce_cluster_transport(params, transport: str | None = None) -> bool:
  """Disable USB cluster when Chestnut needs the bus; network is unrestricted."""
  transport = transport or params.get("ClusterDisplayTransport") or "usb"
  if transport == "network":
    return True
  if params.get_bool("ChestnutActive") or (params.get_bool("ChestnutLoading") and _chestnut_enumerated()):
    if params.get_bool("ClusterEnable"):
      params.put_bool("ClusterEnable", False, block=True)
      cloudlog.warning("USB cluster disabled to prioritize Chestnut eGPU; use network cluster")
    return False
  return True
