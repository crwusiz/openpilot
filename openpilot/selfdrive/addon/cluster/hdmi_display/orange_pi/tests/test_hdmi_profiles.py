import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_receiver, h264_scene, hdmi_display
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.hdmi_profiles import (
  DEFAULT_HDMI_PROFILE, HDMI_PROFILES, normalize_hdmi_profile,
)


@pytest.mark.parametrize("value,expected", [
  (None, "1920x480"), ("", "1920x480"), ("invalid", "1920x480"),
  (b"1920x720", "1920x720"), (" 1920X720 ", "1920x720"), ([], "1920x480"),
])
def test_saved_profile_values_have_a_compatible_default(value, expected):
  assert normalize_hdmi_profile(value) == expected
  assert DEFAULT_HDMI_PROFILE == "1920x480"


def _display():
  size = [(480, 1920)]
  calls = []
  screen = SimpleNamespace(fill=Mock(), get_size=lambda: size[0])

  def set_mode(dimensions, *_args, **_kwargs):
    size[0] = dimensions
    calls.append(dimensions)
    return screen

  pygame = SimpleNamespace(
    DOUBLEBUF=1, FULLSCREEN=2,
    display=SimpleNamespace(init=Mock(), get_driver=lambda: "dummy", set_mode=set_mode,
                            set_caption=Mock(), flip=Mock(), quit=Mock(), get_desktop_sizes=lambda: [(1920, 720)]),
    mouse=SimpleNamespace(set_visible=Mock()), event=SimpleNamespace(get=list),
  )
  display = hdmi_display.HdmiDisplay(width=480, height=1920, rotation=90,
                                     renderer_mode="surface", pygame_module=pygame)
  assert display.open()
  return display, calls


def test_profile_switch_rebuilds_output_and_touch_without_losing_local_controls_or_background_work():
  display, calls = _display()
  controls = display.controls
  controls.set_brightness(30)
  controls.screen_off = True
  controls.set_actions_available(True)
  controls._request_action("log_upload")
  pending = controls.pending_action
  network_job, panel_job = object(), object()
  display._network_future, display._panel_future = network_job, panel_job
  network_executor, panel_executor = Mock(), Mock()
  display._network_executor, display._panel_executor = network_executor, panel_executor
  try:
    assert display.apply_profile("1920x720")
    assert calls == [(480, 1920), (1920, 720)]
    assert display.controls is controls
    assert controls.logical_size == (1920, 720)
    assert controls.brightness == 30 and controls.screen_off
    assert controls.action_busy and controls.pending_action == pending
    assert display._network_future is network_job and display._panel_future is panel_job
    network_executor.shutdown.assert_not_called()
    panel_executor.shutdown.assert_not_called()
    assert (display.rotation, display.touch_rotation) == (0, 0)
    assert display.apply_profile("1920x720")
    assert calls == [(480, 1920), (1920, 720)]
    assert display.apply_profile("1920x480")
    assert calls[-1] == (480, 1920)
    assert controls.logical_size == (1920, 480)
    assert (display.rotation, display.touch_rotation) == (90, 270)
    assert controls.brightness == 30 and controls.screen_off
  finally:
    display.close()
  network_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
  panel_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


def test_profile_switch_closes_gpu_window_and_retains_explicit_touch_calibration():
  display, calls = _display()
  display._touch_rotation_override = 180
  presenter = Mock()
  display._presenter = presenter
  try:
    assert display.apply_profile("1920x720")
    presenter.close.assert_called_once()
    assert display._presenter is None
    assert display.touch_rotation == 180
    assert calls[-1] == (1920, 720)
  finally:
    display.close()


def test_startup_fallback_uses_current_mode_then_accepts_manual_profile():
  display, calls = _display()
  display.close()
  try:
    assert display.open_current_mode()
    assert calls[-1] == (1920, 720)
    assert display.rotation == display.touch_rotation == 0
    assert display.apply_profile("1920x480")
    assert calls[-1] == (480, 1920)
  finally:
    display.close()


def test_closed_display_can_poll_during_profile_recovery_without_accessing_sdl_events():
  display, _calls = _display()
  display.close()
  display._pygame.event.get = Mock(side_effect=RuntimeError("video system not initialized"))
  assert display.pump_events()
  display._pygame.event.get.assert_not_called()


@pytest.mark.parametrize("profile", ["1920x480", "1920x720", None])
def test_receiver_applies_profile_on_owner_before_allocating_scene_and_starting_worker(monkeypatch, profile):
  owner = threading.get_ident()
  events = []
  height = HDMI_PROFILES[profile]["height"] if profile is not None else 480
  hello = {"codec": "h264", "transport": "webrtc", "camera": "road", "width": 1920, "height": height, "fps": 20}
  if profile is not None:
    hello["display_profile"] = profile

  def apply(selected):
    assert threading.get_ident() == owner
    events.append(("profile", selected))
    return True

  scene = SimpleNamespace(close=Mock())
  def create_scene(_assets, width, scene_height, fps):
    events.append(("scene", width, scene_height, fps))
    return scene

  receiver = SimpleNamespace(condition=threading.Condition(), start=lambda: events.append("start"), close=Mock(),
                             take_updates=Mock(side_effect=ConnectionError("finished")))
  monkeypatch.setattr(h264_scene, "H264Scene", create_scene)
  monkeypatch.setattr(h264_receiver, "H264Receiver", lambda *_args: receiver)
  display = SimpleNamespace(apply_profile=apply, pump_events=lambda: True)
  with pytest.raises(ConnectionError, match="finished"):
    h264_receiver.receive_h264_session(None, display, hello)
  expected = [("profile", profile)] if profile is not None else []
  assert events == expected + [("scene", 1920, height, 20), "start"]
  receiver.close.assert_called_once()
  scene.close.assert_called_once()


@pytest.mark.parametrize("profile,height", [("unknown", 480), (None, 480), ([], 480), ("1920x720", 480), ("1920x480", 720)])
def test_receiver_rejects_bad_profile_or_dimensions_before_reconfiguring_display(profile, height):
  hello = {"codec": "h264", "transport": "webrtc", "camera": "road", "width": 1920, "height": height,
           "fps": 20, "display_profile": profile}
  display = Mock()
  with pytest.raises(ValueError, match="HDMI display profile"):
    h264_receiver.receive_h264_session(None, display, hello)
  display.apply_profile.assert_not_called()


def test_failed_output_profile_cannot_start_scene_allocation_or_decoder(monkeypatch):
  scene_factory, receiver_factory = Mock(), Mock()
  monkeypatch.setattr(h264_scene, "H264Scene", scene_factory)
  monkeypatch.setattr(h264_receiver, "H264Receiver", receiver_factory)
  display = SimpleNamespace(apply_profile=Mock(return_value=False))
  hello = {"codec": "h264", "transport": "webrtc", "camera": "road", "width": 1920, "height": 720,
           "fps": 20, "display_profile": "1920x720"}
  with pytest.raises(RuntimeError, match="Unable to apply HDMI display profile"):
    h264_receiver.receive_h264_session(None, display, hello)
  scene_factory.assert_not_called()
  receiver_factory.assert_not_called()


def test_720_scene_renders_native_geometry_with_original_font_and_icon_proportions():
  assets = Path(__file__).resolve().parents[5] / "assets"
  scene = h264_scene.H264Scene(assets, width=1920, height=720, clock=lambda: 10.0)
  try:
    scene.update({"model_valid": False, "hud": {"v_ego": 20.0}, "path": {}, "source_size": [1344, 760]})
    scene.set_video(np.full((720, 1280, 3), 80, dtype=np.uint8))
    image = scene.render()
    layers = scene.render_layers()
    assert image.size == (1920, 720)
    assert layers.overlay.size == (1920, 720)
    assert layers.camera_rect == (476, 10, 968, 700)
    assert scene.renderer.camera_w == 968 and scene.renderer.camera_h == 700
    assert scene.renderer.font_current_speed.size == 92
    assert scene.renderer._base_icon("steer", (94, 94), True).size == (94, 94)
  finally:
    scene.close()
