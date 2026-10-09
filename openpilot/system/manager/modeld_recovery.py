from __future__ import annotations

import struct
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from openpilot.cereal.messaging import SubMaster
  from openpilot.common.params import Params
  from openpilot.system.manager.process import ManagerProcess


def read_chestnut_power() -> tuple[int, int] | None:
  import usb1

  from openpilot.common.hardware.usb import CHESTNUT_USB_PRODUCT, get_usb_state, is_chestnut_usb_id
  from openpilot.system.hardware.chestnut.status import CHESTNUT_POWERED_VOLTAGE

  devices = [d for d in get_usb_state() if is_chestnut_usb_id(d['vendorId'], d['productId'], include_bootloader=True)]
  if len(devices) != 1:
    return None

  device = devices[0]
  if not is_chestnut_usb_id(device['vendorId'], device['productId']) or device['product'] != CHESTNUT_USB_PRODUCT:
    return None
  try:
    with usb1.USBContext() as context:
      handle = context.openByVendorIDAndProductID(device['vendorId'], device['productId'], skip_on_error=True)
      if handle is None:
        return None
      try:
        voltage, _, fault = struct.unpack('<Hh?', bytes(handle.controlRead(0xC0, 0xC0, 0, 0, 5, timeout=100)))
      finally:
        handle.close()
  except (usb1.USBError, OSError, struct.error):
    return None

  # chestnutState.valid also includes modeld's GPU telemetry validity. Read the
  # supply directly so stale telemetry cannot prevent recovery after ACC returns.
  # PCIe is enabled by modeld during loading, so an inactive link is allowed here.
  return (device['busnum'], device['devnum']) if voltage >= CHESTNUT_POWERED_VOLTAGE and not fault else None


class ChestnutModeldRecovery:
  STABLE_TIME = 3.
  COOLDOWN = 30.
  MAX_RESTARTS = 2

  def __init__(self, models_available: Callable[[], bool], power_probe: Callable[[], tuple[int, int] | None] = read_chestnut_power):
    self.models_available = models_available
    self.power_probe = power_probe
    self.reset()

  def reset(self) -> None:
    self.attempts = 0
    self.last_restart = -self.COOLDOWN
    self.pid: int | None = None
    self.process_started = 0.
    self.power_device: tuple[int, int] | None = None
    self.power_since: float | None = None

  def update(self, started: bool, params: Params, sm: SubMaster, modeld: ManagerProcess) -> bool:
    if not started or not modeld.enabled:
      self.reset()
      return False

    now = time.monotonic()
    if modeld.proc is None or modeld.shutting_down or not modeld.proc.is_alive():
      self.pid = None
      self.power_device = None
      self.power_since = None
      return False

    if modeld.proc.pid != self.pid:
      self.pid = modeld.proc.pid
      self.process_started = now
      self.power_device = None
      self.power_since = None

    # ChestnutActive is cleared on ignition transitions, even when a quick key
    # cycle leaves modeld running. Use output from the current process instead.
    # modelV2.valid reflects calibration, which is unrelated to model selection.
    small_model = (sm.seen['modelV2'] and sm.alive['modelV2'] and
                   sm.logMonoTime['modelV2'] > int(self.process_started * 1e9) and not sm['modelV2'].big)
    if not small_model or params.get_bool('ChestnutLoading') or not self.models_available():
      self.power_device = None
      self.power_since = None
      return False

    if self.attempts >= self.MAX_RESTARTS:
      return False

    power_device = self.power_probe()
    if power_device is None or power_device != self.power_device:
      self.power_device = power_device
      self.power_since = now if power_device is not None else None
    if self.power_since is None or now - self.power_since < self.STABLE_TIME or now - self.last_restart < self.COOLDOWN:
      return False

    services = ['carState', 'selfdriveState']
    if not all(sm.seen[s] and sm.alive[s] and sm.valid[s] for s in services):
      return False
    cs, ss = sm['carState'], sm['selfdriveState']
    if not (cs.canValid and cs.standstill and abs(cs.vEgo) < 0.1 and not ss.enabled and not ss.active):
      return False

    self.attempts += 1
    self.last_restart = now
    self.power_device = None
    self.power_since = None
    return True
