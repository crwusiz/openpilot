"""Exercise owner-side frame replacement through the actual session loop."""

import logging
import threading
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import h264_receiver, h264_scene
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.tests.test_h264_receiver import run_feedback_ticks


HELLO = {"codec": "h264", "transport": "webrtc", "camera": "road", "width": 1920, "height": 480, "fps": 20}


def test_pending_replacements_are_window_deltas_separate_from_receiver_slot_drops(monkeypatch, caplog):
  receiver = h264_receiver.H264Receiver(None, HELLO)

  def update(tick, now):
    receiver.publish_rgb("latest", received_at=now)
    if tick in (2, 11):
      for _ in range(1 if tick == 2 else 2):
        receiver.record_pending_replacement()

  with caplog.at_level(logging.INFO, logger="cluster_receiver"):
    feedbacks = run_feedback_ticks(monkeypatch, receiver, 30, update)
  assert [item["pending_replaced_frames"] for item in feedbacks] == [0, 1] + [0] * 8 + [2] + [0] * 19
  assert feedbacks[-1]["dropped_display"] == 29  # Unconsumed receiver slot, a separate counter.
  messages = [record.getMessage() for record in caplog.records if "CLUSTER_H264_RX_PERF" in record.getMessage()]
  assert len(messages) == 3
  assert all(f"pending_replaced_frames={expected} |" in message for message, expected in zip(messages, (1, 2, 0), strict=True))


def run_updates(monkeypatch, updates):
  """Updates are (time, RGB identity, metadata arrival time, screen off)."""
  clock, instances, pictures, waiting, scene_closed = [1.0], [], [], [], []

  class Scene:
    def __init__(self, *_args):
      self.picture = "metadata"
      self.video_at = None
      self.last_status = {"video_fresh": False}

    def update(self, *_args, **_kwargs):
      pass

    def set_video(self, picture, received_at, *, take_ownership):
      assert take_ownership
      self.picture, self.video_at = picture, received_at

    def render_layers(self):
      self.last_status = {"video_fresh": self.video_at is not None and clock[0] - self.video_at < 0.5}
      return self.picture

    def close(self):
      scene_closed.append(True)

  class Receiver:
    def __init__(self, *_args):
      self.condition = threading.Condition()
      self.received_frames = self.dropped_frames = self.pending_replacements = 0
      self.last_video_at = self.last_state_at = None
      self.done = self.closed = self.gpu_camera = self.screen_off = False
      self.index = 0
      instances.append(self)

    def start(self):
      pass

    def take_updates(self, timeout=0.02):
      assert 0 <= timeout <= 0.02
      if self.index == len(updates):
        return None, None  # The session rechecks its terminal error once.
      arrived_at, picture, state_at, _off = updates[self.index]
      assert clock[0] == arrived_at
      self.index += 1
      self.done = self.index == len(updates)
      if picture is not None:
        self.received_frames += 1
        self.last_video_at = arrived_at
      if state_at is not None:
        self.last_state_at = state_at
      return ((picture, arrived_at) if picture is not None else None,
              ({"state_seq": self.index}, state_at) if state_at is not None else None)

    def record_pending_replacement(self):
      self.pending_replacements += 1

    def record_display(self, *_args, **_kwargs):
      pass

    def close(self):
      self.closed = True

  def pump():
    receiver = instances[0]
    if receiver.index < len(updates):
      clock[0], _picture, _state_at, display.screen_off = updates[receiver.index]
    return True

  display = SimpleNamespace(pump_events=pump, screen_off=False, can_present_layers=True,
                            compositor_backend="pi_gpu_camera", last_frame_presented=True,
                            send_layers=lambda image: pictures.append(image) or True,
                            show_waiting=lambda: waiting.append(True) or True)
  monkeypatch.setattr(h264_receiver, "time", SimpleNamespace(monotonic=lambda: clock[0]))
  monkeypatch.setattr(h264_receiver, "H264Receiver", Receiver)
  monkeypatch.setattr(h264_scene, "H264Scene", Scene)
  received = h264_receiver.receive_h264_session(None, display, HELLO)
  assert instances[0].closed and scene_closed == [True]
  assert instances[0].dropped_frames == 0  # No receiver publish-slot replacement.
  return instances[0], received, pictures, waiting


def test_two_rgb_updates_before_gpu_deadline_count_one_owner_pending_replacement(monkeypatch):
  receiver, received, pictures, waiting = run_updates(monkeypatch, [
    (1.000, 1, 1.000, False), (1.005, 2, None, False),
    (1.009, 3, None, False), (1.017, None, None, False),
  ])
  assert received == 3 and pictures == [1, 3] and not waiting
  assert receiver.pending_replacements == 1


@pytest.mark.parametrize("updates,expected", [
  ([(1.000, 1, 1.000, False), (1.005, 2, None, False), (1.007, None, None, True),
    (1.009, 3, None, False), (1.017, None, None, False)], 0),
  ([(1.000, 1, 1.000, False), (1.005, 2, None, False), (1.007, 3, None, True),
    (1.009, 4, None, False), (1.017, None, None, False)], 0),
  ([(1.000, 1, 1.000, False), (1.005, 2, None, True), (1.007, None, None, False),
    (1.009, 3, None, False), (1.011, 4, None, False), (1.017, None, None, False)], 1),
])
def test_off_on_boundaries_exclude_hidden_candidates_and_then_resume_normal_counting(monkeypatch, updates, expected):
  receiver, _received, pictures, waiting = run_updates(monkeypatch, updates)
  assert receiver.pending_replacements == expected
  assert pictures == [1, max(event[1] or 0 for event in updates)] and not waiting


@pytest.mark.parametrize("updates,pictures,waiting", [
  ([(1.000, 1, 1.000, False), (1.050, 2, 1.050, False), (1.100, 3, 1.100, False)], [1, 2, 3], []),
  # Metadata changes freshness while no camera picture is pending.
  ([(1.000, 1, None, False), (1.005, None, 1.005, False),
    (1.009, 2, None, False), (1.017, None, None, False)], [1, 2], []),
  ([(1.000, None, 1.000, False), (1.020, None, 1.020, False),
    (1.101, None, 1.101, False)], ["metadata", "metadata"], []),
  # A waiting-screen transition discards the previously held candidate.
  ([(1.000, 1, 1.000, False), (1.005, 2, None, False),
    (3.100, None, None, False), (3.105, 3, 3.105, False)], [1, 3], [True]),
])
def test_metadata_freshness_waiting_and_completed_frames_do_not_count_as_pending_replacements(monkeypatch, updates,
                                                                                            pictures, waiting):
  receiver, _received, actual, shown = run_updates(monkeypatch, updates)
  assert actual == pictures and shown == waiting
  assert receiver.pending_replacements == 0
