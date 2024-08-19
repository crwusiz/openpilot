#!/usr/bin/env python3
import capnp
import os
import time
from typing import Any

import cereal.messaging as messaging

from cereal import car

from panda import ALTERNATIVE_EXPERIENCE

from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper
from openpilot.common.swaglog import cloudlog, ForwardingHandler

from opendbc.car import DT_CTRL, carlog, structs
from opendbc.car.can_definitions import CanData, CanRecvCallable, CanSendCallable
from opendbc.car.fw_versions import ObdCallback
from opendbc.car.car_helpers import get_car
from opendbc.car.interfaces import CarInterfaceBase
from openpilot.selfdrive.pandad import can_capnp_to_list, can_list_to_can_capnp
from openpilot.selfdrive.car.car_specific import CarSpecificEvents, MockCarState
from openpilot.selfdrive.controls.lib.events import Events

from openpilot.selfdrive.controls.neokii.speed_controller import SpeedController

REPLAY = "REPLAY" in os.environ
_FIELDS = '__dataclass_fields__'  # copy of dataclasses._FIELDS

EventName = car.CarEvent.EventName

# forward
carlog.addHandler(ForwardingHandler(cloudlog))


def obd_callback(params: Params) -> ObdCallback:
  def set_obd_multiplexing(obd_multiplexing: bool):
    if params.get_bool("ObdMultiplexingEnabled") != obd_multiplexing:
      cloudlog.warning(f"Setting OBD multiplexing to {obd_multiplexing}")
      params.remove("ObdMultiplexingChanged")
      params.put_bool("ObdMultiplexingEnabled", obd_multiplexing)
      params.get_bool("ObdMultiplexingChanged", block=True)
      cloudlog.warning("OBD multiplexing set successfully")
  return set_obd_multiplexing


def can_comm_callbacks(logcan: messaging.SubSocket, sendcan: messaging.PubSocket) -> tuple[CanRecvCallable, CanSendCallable]:
  def can_recv(wait_for_one: bool = False) -> list[list[CanData]]:
    """
    wait_for_one: wait the normal logcan socket timeout for a CAN packet, may return empty list if nothing comes

    Returns: CAN packets comprised of CanData objects for easy access
    """
    ret = []
    for can in messaging.drain_sock(logcan, wait_for_one=wait_for_one):
      ret.append([CanData(msg.address, msg.dat, msg.src) for msg in can.can])
    return ret

  def can_send(msgs: list[CanData]) -> None:
    sendcan.send(can_list_to_can_capnp(msgs, msgtype='sendcan'))

  return can_recv, can_send


def is_dataclass(obj):
  """Similar to dataclasses.is_dataclass without instance type check checking"""
  return hasattr(obj, _FIELDS)


def asdictref(obj) -> dict[str, Any]:
  """
  Similar to dataclasses.asdict without recursive type checking and copy.deepcopy
  Note that the resulting dict will contain references to the original struct as a result
  """
  if not is_dataclass(obj):
    raise TypeError("asdictref() should be called on dataclass instances")

  def _asdictref_inner(obj) -> dict[str, Any] | Any:
    if is_dataclass(obj):
      ret = {}
      for field in getattr(obj, _FIELDS):  # similar to dataclasses.fields()
        ret[field] = _asdictref_inner(getattr(obj, field))
      return ret
    elif isinstance(obj, (tuple, list)):
      return type(obj)(_asdictref_inner(v) for v in obj)
    else:
      return obj

  return _asdictref_inner(obj)


def convert_to_capnp(struct: structs.CarParams | structs.CarState | structs.CarControl.Actuators) -> capnp.lib.capnp._DynamicStructBuilder:
  struct_dict = asdictref(struct)

  if isinstance(struct, structs.CarParams):
    del struct_dict['lateralTuning']
    struct_capnp = car.CarParams.new_message(**struct_dict)

    # this is the only union, special handling
    which = struct.lateralTuning.which()
    struct_capnp.lateralTuning.init(which)
    lateralTuning_dict = asdictref(getattr(struct.lateralTuning, which))
    setattr(struct_capnp.lateralTuning, which, lateralTuning_dict)
  elif isinstance(struct, structs.CarState):
    struct_capnp = car.CarState.new_message(**struct_dict)
  elif isinstance(struct, structs.CarControl.Actuators):
    struct_capnp = car.CarControl.Actuators.new_message(**struct_dict)
  else:
    raise ValueError(f"Unsupported struct type: {type(struct)}")

  return struct_capnp


def convert_carControl(struct: capnp.lib.capnp._DynamicStructReader) -> structs.CarControl:
  # TODO: recursively handle any car struct as needed
  def remove_deprecated(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.endswith('DEPRECATED')}

  struct_dict = struct.to_dict()
  struct_dataclass = structs.CarControl(**remove_deprecated({k: v for k, v in struct_dict.items() if not isinstance(k, dict)}))

  struct_dataclass.actuators = structs.CarControl.Actuators(**remove_deprecated(struct_dict.get('actuators', {})))
  struct_dataclass.cruiseControl = structs.CarControl.CruiseControl(**remove_deprecated(struct_dict.get('cruiseControl', {})))
  struct_dataclass.hudControl = structs.CarControl.HUDControl(**remove_deprecated(struct_dict.get('hudControl', {})))

  return struct_dataclass


class Car:
  CI: CarInterfaceBase
  CP: structs.CarParams
  CP_capnp: car.CarParams

  def __init__(self, CI=None) -> None:
    self.can_sock = messaging.sub_sock('can', timeout=20)
    self.sm = messaging.SubMaster(['pandaStates', 'carControl', 'onroadEvents', 'modelV2', 'radarState'])
    self.pm = messaging.PubMaster(['sendcan', 'carState', 'carParams', 'carOutput'])

    self.can_rcv_cum_timeout_counter = 0

    self.CC_prev = car.CarControl.new_message()
    self.CS_prev = car.CarState.new_message()
    self.initialized_prev = False

    self.last_actuators_output = structs.CarControl.Actuators()

    self.params = Params()

    self.can_callbacks = can_comm_callbacks(self.can_sock, self.pm.sock['sendcan'])

    if CI is None:
      # wait for one pandaState and one CAN packet
      print("Waiting for CAN messages...")
      while True:
        can = messaging.recv_one_retry(self.can_sock)
        if len(can.can) > 0:
          break

      experimental_long_allowed = self.params.get_bool("ExperimentalLongitudinalEnabled")
      num_pandas = len(messaging.recv_one_retry(self.sm.sock['pandaStates']).pandaStates)

      cached_params = None
      cached_params_raw = self.params.get("CarParamsCache")
      if cached_params_raw is not None:
        with car.CarParams.from_bytes(cached_params_raw) as _cached_params:
          cached_params = structs.CarParams(carName=_cached_params.carName, carFw=_cached_params.carFw, carVin=_cached_params.carVin)

      self.CI = get_car(*self.can_callbacks, obd_callback(self.params), experimental_long_allowed, num_pandas, cached_params)
      self.CP = self.CI.CP

      # continue onto next fingerprinting step in pandad
      self.params.put_bool("FirmwareQueryDone", True)
    else:
      self.CI, self.CP = CI, CI.CP

    # set alternative experiences from parameters
    self.disengage_on_accelerator = self.params.get_bool("DisengageOnAccelerator")
    self.disengage_on_brake = self.params.get_bool("DisengageOnBrake")

    self.CP.alternativeExperience = 0
    if not self.disengage_on_accelerator:
      self.CP.alternativeExperience |= ALTERNATIVE_EXPERIENCE.DISABLE_DISENGAGE_ON_GAS

    if not self.disengage_on_brake:
      self.CP.alternativeExperience |= ALTERNATIVE_EXPERIENCE.ALLOW_AEB

    openpilot_enabled_toggle = self.params.get_bool("OpenpilotEnabledToggle")

    controller_available = self.CI.CC is not None and openpilot_enabled_toggle and not self.CP.dashcamOnly

    self.CP.passive = not controller_available or self.CP.dashcamOnly
    if self.CP.passive:
      safety_config = structs.CarParams.SafetyConfig()
      safety_config.safetyModel = structs.CarParams.SafetyModel.noOutput
      self.CP.safetyConfigs = [safety_config]

    # Write previous route's CarParams
    prev_cp = self.params.get("CarParamsPersistent")
    if prev_cp is not None:
      self.params.put("CarParamsPrevRoute", prev_cp)

    # Write CarParams for controls and radard
    # convert to pycapnp representation for caching and logging
    self.CP_capnp = convert_to_capnp(self.CP)
    cp_bytes = self.CP_capnp.to_bytes()
    self.params.put("CarParams", cp_bytes)
    self.params.put_nonblocking("CarParamsCache", cp_bytes)
    self.params.put_nonblocking("CarParamsPersistent", cp_bytes)

    self.events = Events()

    self.car_events = CarSpecificEvents(self.CP)
    self.mock_carstate = MockCarState()

    # card is driven by can recv, expected at 100Hz
    self.rk = Ratekeeper(100, print_delay_threshold=None)

    self.speed_controller = SpeedController(self.CP, self.CI)

  def state_update(self) -> car.CarState:
    """carState update loop, driven by can"""

    # Update carState from CAN
    can_strs = messaging.drain_sock_raw(self.can_sock, wait_for_one=True)
    CS = convert_to_capnp(self.CI.update(can_capnp_to_list(can_strs)))

    if self.CP.carName == 'mock':
      CS = self.mock_carstate.update(CS)

    self.sm.update(0)

    can_rcv_valid = len(can_strs) > 0

    # Check for CAN timeout
    if not can_rcv_valid:
      self.can_rcv_cum_timeout_counter += 1

    if can_rcv_valid and REPLAY:
      self.can_log_mono_time = messaging.log_from_bytes(can_strs[0]).logMonoTime

    self.speed_controller.update_v_cruise(CS, self.sm, self.sm['carControl'].enabled)

    return CS

  def update_events(self, CS: car.CarState):
    self.events.clear()

    CS.events = self.car_events.update(self.CI.CS, self.CS_prev, self.CI.CC, self.CC_prev).to_msg()

    self.events.add_from_msg(CS.events)

    if self.CP.notCar:
      # wait for everything to init first
      if self.sm.frame > int(5. / DT_CTRL) and self.initialized_prev:
        # body always wants to enable
        self.events.add(EventName.pcmEnable)

    # Disable on rising edge of accelerator or brake. Also disable on brake when speed > 0
    #if (CS.gasPressed and not self.CS_prev.gasPressed and self.disengage_on_accelerator) or \
    #  (CS.brakePressed and (not self.CS_prev.brakePressed or not CS.standstill)) or \
    #  (CS.regenBraking and (not self.CS_prev.regenBraking or not CS.standstill)):
    #  self.events.add(EventName.pedalPressed)

    self.speed_controller.inject_events(CS, self.events)
    CS.events = self.events.to_msg()

  def state_publish(self, CS: car.CarState):
    """carState and carParams publish loop"""

    # carParams - logged every 50 seconds (> 1 per segment)
    if self.sm.frame % int(50. / DT_CTRL) == 0:
      cp_send = messaging.new_message('carParams')
      cp_send.valid = True
      cp_send.carParams = self.CP_capnp
      self.pm.send('carParams', cp_send)

    # publish new carOutput
    co_send = messaging.new_message('carOutput')
    co_send.valid = self.sm.all_checks(['carControl'])
    co_send.carOutput.actuatorsOutput = convert_to_capnp(self.last_actuators_output)
    self.pm.send('carOutput', co_send)

    # kick off controlsd step while we actuate the latest carControl packet
    cs_send = messaging.new_message('carState')
    cs_send.valid = CS.canValid
    cs_send.carState = CS
    cs_send.carState.canErrorCounter = self.can_rcv_cum_timeout_counter
    cs_send.carState.cumLagMs = -self.rk.remaining * 1000.
    self.pm.send('carState', cs_send)

  def controls_update(self, CS: car.CarState, CC: car.CarControl):
    """control update loop, driven by carControl"""

    if not self.initialized_prev:
      # Initialize CarInterface, once controls are ready
      # TODO: this can make us miss at least a few cycles when doing an ECU knockout
      self.CI.init(self.CP, *self.can_callbacks)
      # signal pandad to switch to car safety mode
      self.params.put_bool_nonblocking("ControlsReady", True)

    if self.sm.all_alive(['carControl']):
      # send car controls over can
      now_nanos = self.can_log_mono_time if REPLAY else int(time.monotonic() * 1e9)
      self.last_actuators_output, can_sends = self.CI.apply(convert_carControl(CC), now_nanos)
      self.speed_controller.spam_message(CS, can_sends)
      self.pm.send('sendcan', can_list_to_can_capnp(can_sends, msgtype='sendcan', valid=CS.canValid))

      self.CC_prev = CC

  def step(self):
    CS = self.state_update()

    self.update_events(CS)

    self.state_publish(CS)

    initialized = (not any(e.name == EventName.controlsInitializing for e in self.sm['onroadEvents']) and
                   self.sm.seen['onroadEvents'])
    if not self.CP.passive and initialized:
      self.controls_update(CS, self.sm['carControl'])

    self.initialized_prev = initialized
    self.CS_prev = CS.as_reader()

  def card_thread(self):
    while True:
      self.step()
      self.rk.monitor_time()


def main():
  config_realtime_process(4, Priority.CTRL_HIGH)
  car = Car()
  car.card_thread()


if __name__ == "__main__":
  main()
