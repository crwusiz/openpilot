#!/usr/bin/env python3
import os
import argparse
import threading
from inputs import UnpluggedError, get_gamepad
import math

from cereal import messaging, car
from openpilot.common.realtime import DT_CTRL, Ratekeeper
from openpilot.common.numpy_fast import interp, clip
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.vehicle_model import VehicleModel
from openpilot.system.hardware import HARDWARE
from openpilot.tools.lib.kbhit import KBHit

EXPO = 0.4
MAX_LAT_ACCEL = 2.5


class Keyboard:
  def __init__(self):
    self.kb = KBHit()
    self.axis_increment = 0.05  # 5% of full actuation each key press
    self.axes_map = {'w': 'gb', 's': 'gb',
                     'a': 'steer', 'd': 'steer'}
    self.axes_values = {'gb': 0., 'steer': 0.}
    self.axes_order = ['gb', 'steer']
    self.cancel = False

  def update(self):
    key = self.kb.getch().lower()
    self.cancel = False
    if key == 'r':
      self.axes_values = {ax: 0. for ax in self.axes_values}
    elif key == 'c':
      self.cancel = True
    elif key in self.axes_map:
      axis = self.axes_map[key]
      incr = self.axis_increment if key in ['w', 'a'] else -self.axis_increment
      self.axes_values[axis] = clip(self.axes_values[axis] + incr, -1, 1)
    else:
      return False
    return True


class Joystick:
  def __init__(self):
    # This class supports a PlayStation 5 DualSense controller on the comma 3X
    # TODO: find a way to get this from API or detect gamepad/PC, perhaps "inputs" doesn't support it
    self.cancel_button = 'BTN_NORTH'  # BTN_NORTH=X/triangle
    if HARDWARE.get_device_type() == 'pc':
      accel_axis = 'ABS_Z'
      steer_axis = 'ABS_RX'
      # TODO: once the longcontrol API is finalized, we can replace this with outputting gas/brake and steering
      self.flip_map = {'ABS_RZ': accel_axis}
    else:
      accel_axis = 'ABS_RX'
      steer_axis = 'ABS_Z'
      self.flip_map = {'ABS_RY': accel_axis}

    self.min_axis_value = {accel_axis: 0., steer_axis: 0.}
    self.max_axis_value = {accel_axis: 255., steer_axis: 255.}
    self.axes_values = {accel_axis: 0., steer_axis: 0.}
    self.axes_order = [accel_axis, steer_axis]
    self.cancel = False

  def update(self):
    try:
      joystick_event = get_gamepad()[0]
    except (OSError, UnpluggedError):
      self.axes_values = {ax: 0. for ax in self.axes_values}
      return False

    event = (joystick_event.code, joystick_event.state)

    # flip left trigger to negative accel
    if event[0] in self.flip_map:
      event = (self.flip_map[event[0]], -event[1])

    if event[0] == self.cancel_button:
      if event[1] == 1:
        self.cancel = True
      elif event[1] == 0:   # state 0 is falling edge
        self.cancel = False
    elif event[0] in self.axes_values:
      self.max_axis_value[event[0]] = max(event[1], self.max_axis_value[event[0]])
      self.min_axis_value[event[0]] = min(event[1], self.min_axis_value[event[0]])

      norm = -interp(event[1], [self.min_axis_value[event[0]], self.max_axis_value[event[0]]], [-1., 1.])
      norm = norm if abs(norm) > 0.03 else 0.  # center can be noisy, deadzone of 3%
      self.axes_values[event[0]] = EXPO * norm ** 3 + (1 - EXPO) * norm  # less action near center for fine control
    else:
      return False
    return True


def send_thread(joystick):
  params = Params()
  cloudlog.info("joystickd is waiting for CarParams")
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  VM = VehicleModel(CP)

  sm = messaging.SubMaster(['carState', 'onroadEvents', 'liveParameters', 'selfdriveState'], frequency=1. / DT_CTRL)
  pm = messaging.PubMaster(['carControl', 'controlsState'])

  rk = Ratekeeper(100, print_delay_threshold=None)
  while 1:
    sm.update(0)

    joystick_axes = [joystick.axes_values[a] for a in joystick.axes_order]
    if rk.frame % 20 == 0:
      print('\n' + ', '.join(f'{name}: {round(v, 3)}' for name, v in joystick.axes_values.items()))

    cc_msg = messaging.new_message('carControl')
    cc_msg.valid = True
    CC = cc_msg.carControl
    CC.enabled = sm['selfdriveState'].enabled
    CC.latActive = sm['selfdriveState'].active and not sm['carState'].steerFaultTemporary and not sm['carState'].steerFaultPermanent
    CC.longActive = CC.enabled and not any(e.overrideLongitudinal for e in sm['onroadEvents']) and CP.openpilotLongitudinalControl

    actuators = CC.actuators

    if CC.longActive:
      actuators.accel = 4.0 * clip(joystick_axes[0], -1, 1)

    if CC.latActive:
      max_curvature = MAX_LAT_ACCEL / max(sm['carState'].vEgo ** 2, 5)
      max_angle = math.degrees(VM.get_steer_from_curvature(max_curvature, sm['carState'].vEgo, sm['liveParameters'].roll))

      actuators.steer = clip(joystick_axes[1], -1, 1)
      actuators.steeringAngleDeg, actuators.curvature = actuators.steer * max_angle, actuators.steer * -max_curvature

    pm.send('carControl', cc_msg)

    cs_msg = messaging.new_message('controlsState')
    cs_msg.valid = True
    controlsState = cs_msg.controlsState
    controlsState.lateralControlState.init('debugState')
    pm.send('controlsState', cs_msg)

    rk.keep_time()


def joystick_thread(joystick):
  Params().put_bool('JoystickDebugMode', True)
  threading.Thread(target=send_thread, args=(joystick,), daemon=True).start()
  while True:
    joystick.update()


def main():
  joystick_thread(Joystick())


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description='Publishes events from your joystick to control your car.\n' +
                                               'openpilot must be offroad before starting joystickd. This tool supports ' +
                                               'a PlayStation 5 DualSense controller on the comma 3X.',
                                   formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser.add_argument('--keyboard', action='store_true', help='Use your keyboard instead of a joystick')
  args = parser.parse_args()

  if not Params().get_bool("IsOffroad") and "ZMQ" not in os.environ:
    print("The car must be off before running joystickd.")
    exit()

  print()
  if args.keyboard:
    print('Gas/brake control: `W` and `S` keys')
    print('Steering control: `A` and `D` keys')
    print('Buttons')
    print('- `R`: Resets axes')
    print('- `C`: Cancel cruise control')
  else:
    print('Using joystick, make sure to run cereal/messaging/bridge on your device if running over the network!')
    print('If not running on a comma device, the mapping may need to be adjusted.')

  joystick = Keyboard() if args.keyboard else Joystick()
  joystick_thread(joystick)
