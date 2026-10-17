import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openpilot.selfdrive.addon.cluster.cluster_frame_clock import ClusterFrameClock


@pytest.fixture
def runtime(monkeypatch):
  prefix = "openpilot.selfdrive.addon.cluster."
  display = SimpleNamespace(prepare_frame=Mock(), check_encoder=Mock())
  factory, run = Mock(return_value=display), Mock()
  clock = SimpleNamespace(wait=Mock(), interval=0.05)
  clock_factory = Mock(return_value=clock)
  for name, module in {
    "cluster_frame_clock": SimpleNamespace(ClusterFrameClock=clock_factory),
    "usb_display.runtime": SimpleNamespace(run_composed_cluster=run),
    "usb_display.h264_display": SimpleNamespace(H264DisplayAdapter=factory),
  }.items():
    monkeypatch.setitem(sys.modules, prefix + name, module)
  path = Path(__file__).parents[1] / "usb_display/h264_runtime.py"
  spec = importlib.util.spec_from_file_location("usb_h264_runtime_under_test", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return SimpleNamespace(module=module, display=display, factory=factory, run=run, clock=clock, clock_factory=clock_factory)


def test_h264_composition_keeps_hardware_clock_and_checks_fatal_errors(runtime):
  config = SimpleNamespace(usb_h264_fps=20, params=object())
  runtime.module.run_h264_usb_cluster(config)
  runtime.factory.assert_called_once_with(config)
  runtime.clock_factory.assert_called_once_with(20)
  assert runtime.run.call_args.args == (config, runtime.display)
  options = runtime.run.call_args.kwargs
  camera = SimpleNamespace(wait_for_frame=Mock(return_value=8))
  assert options["wait_for_frame"](camera, 7) == 8
  runtime.clock.wait.assert_called_once()
  camera.wait_for_frame.assert_called_once_with(timeout=0.05)
  options["check_runtime"]()
  runtime.display.check_encoder.assert_called_once()
  runtime.display.check_encoder.side_effect = RuntimeError("hardware failed")
  with pytest.raises(RuntimeError, match="hardware failed"):
    options["check_runtime"]()


class PhasedCamera:
  """A 20 Hz source whose single RGB slot becomes ready 15 ms after arrival."""

  def __init__(self):
    self.now = 0.0
    self.next_source = 0.0
    self.publish_at = float("inf")
    self.prepared = self.consumed = 0

  def advance(self, target):
    while min(self.next_source, self.publish_at) <= target:
      self.now = min(self.next_source, self.publish_at)
      if self.now == self.publish_at:
        self.prepared += 1
        self.publish_at = float("inf")
      else:
        # An unconsumed RGB slot prevents another expensive conversion.
        if self.prepared == self.consumed:
          self.publish_at = self.now + 0.015
        self.next_source += 0.05
    self.now = target

  def sleep(self, duration):
    self.advance(self.now + duration)

  def wait_for_frame(self, timeout):
    deadline = self.now + timeout
    while self.prepared == self.consumed and self.now < deadline:
      self.advance(min(self.next_source, self.publish_at, deadline))
    return self.prepared

  def consume(self):
    self.consumed = self.prepared
    self.advance(self.now + 0.008)  # Composition, with no extra camera work.
    return self.consumed


def test_rgb_completion_out_of_phase_with_usb_clock_does_not_halve_camera_rate(runtime, monkeypatch):
  camera = PhasedCamera()
  monkeypatch.setattr(runtime.module, "ClusterFrameClock",
                      lambda fps: ClusterFrameClock(fps, clock=lambda: camera.now, sleep=camera.sleep))
  runtime.module.run_h264_usb_cluster(SimpleNamespace(usb_h264_fps=20))
  wait = runtime.run.call_args.kwargs["wait_for_frame"]
  previous, used, used_at = -1, [], []
  for _ in range(60):
    previous = wait(camera, previous)
    used.append(camera.consume())
    used_at.append(camera.now)
  assert len(set(used)) == 60
  assert (len(used) - 1) / (used_at[-1] - used_at[0]) == pytest.approx(20.0)


def test_missing_camera_does_not_block_hud_or_runtime_checks(runtime, monkeypatch):
  camera = PhasedCamera()
  camera.next_source = float("inf")
  monkeypatch.setattr(runtime.module, "ClusterFrameClock",
                      lambda fps: ClusterFrameClock(fps, clock=lambda: camera.now, sleep=camera.sleep))
  runtime.module.run_h264_usb_cluster(SimpleNamespace(usb_h264_fps=20))
  options = runtime.run.call_args.kwargs
  for _ in range(10):
    started = camera.now
    assert options["wait_for_frame"](camera, 0) == 0
    assert 0.0 < camera.now - started <= 0.1 + 1e-9
    options["check_runtime"]()
  assert runtime.display.check_encoder.call_count == 10
