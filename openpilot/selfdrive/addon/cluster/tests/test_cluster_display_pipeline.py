import threading
import time
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.cluster.cluster_display_pipeline import ClusterDisplayPipeline


class OverlapDisplay:
  def __init__(self):
    self.connected = True
    self.prepare_count = 0
    self.send_started = threading.Event()
    self.second_prepared = threading.Event()
    self.release_send = threading.Event()

  def prepare_frame(self, frame, _created_at):
    self.prepare_count += 1
    if self.prepare_count == 2:
      self.second_prepared.set()
    return frame

  def send_prepared(self, _prepared):
    self.send_started.set()
    self.release_send.wait(timeout=2.0)
    return True


class ReconnectDisplay:
  def __init__(self):
    self.connected = False
    self.open_started = threading.Event()
    self.allow_open = threading.Event()
    self.latest_prepared = threading.Event()
    self.sent = []

  def prepare_frame(self, frame, _created_at):
    if frame == "latest":
      self.latest_prepared.set()
    return frame

  def open(self):
    self.open_started.set()
    self.allow_open.wait(timeout=2.0)
    self.connected = True
    return True

  def send_prepared(self, prepared):
    self.sent.append(prepared)
    return True


class FailedSendDisplay:
  def __init__(self, raises=False):
    self.connected = True
    self.raises = raises
    self.send_attempted = threading.Event()

  def prepare_frame(self, frame, _created_at):
    return frame

  def send_prepared(self, _prepared):
    self.send_attempted.set()
    if self.raises:
      raise RuntimeError("test send failure")
    return False

  def close(self):
    self.connected = False


def _wait_for(predicate, timeout=1.0):
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if predicate():
      return True
    time.sleep(0.005)
  return predicate()


def test_async_encoder_hook_preserves_the_output_render_time_instead_of_current_submission():
  sent = []
  output = SimpleNamespace(created_at=7.0, data=b"independent hardware frame")
  prepare = []
  display = SimpleNamespace(connected=True, send_prepared=lambda frame: sent.append(frame) or True)

  def prepare_frame(image, created_at):
    prepare.append((image, created_at))
    return output

  display.prepare_frame = prepare_frame
  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()
  try:
    pipeline.push("new input", created_at=42.0)
    assert _wait_for(lambda: bool(sent))
    assert sent == [output]
    assert sent[0].created_at == 7.0
    assert prepare == [("new input", 42.0)]
  finally:
    assert pipeline.close()


def test_encoding_overlaps_blocked_send():
  display = OverlapDisplay()
  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()
  try:
    pipeline.push("first")
    assert display.send_started.wait(timeout=1.0)

    pipeline.push("second")
    assert display.second_prepared.wait(timeout=1.0)
  finally:
    display.release_send.set()
    assert pipeline.close()


def test_backpressure_bounds_preparation_while_usb_upload_is_blocked():
  display = OverlapDisplay()
  encoding, release_encode = threading.Event(), threading.Event()
  original_prepare = display.prepare_frame

  def prepare(frame, created_at):
    if frame == "second":
      encoding.set()
      release_encode.wait(timeout=2.0)
    return original_prepare(frame, created_at)

  display.prepare_frame = prepare
  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()
  try:
    pipeline.push("first")
    assert display.send_started.wait(timeout=1.0)
    assert _wait_for(pipeline.has_render_capacity)
    pipeline.push("second")
    assert encoding.wait(timeout=1.0)
    assert not pipeline.has_render_capacity()
    release_encode.set()
    assert _wait_for(lambda: pipeline.get_stats()["encoded"] == 2)

    # The main loop must stop composing once the one-frame lookahead is full,
    # even though the encoder has removed the raw frame from its input slot.
    assert not pipeline.has_render_capacity()
    assert display.prepare_count == 2
    display.release_send.set()
    assert _wait_for(lambda: pipeline.get_stats()["sent"] == 2)
    assert _wait_for(pipeline.has_render_capacity)
    pipeline.push("third")
    assert _wait_for(lambda: pipeline.get_stats()["sent"] == 3)
  finally:
    release_encode.set()
    display.release_send.set()
    assert pipeline.close()


@pytest.mark.parametrize("result", ["success", "none", "exception"])
def test_render_can_overlap_encoding_without_a_blocked_upload(result):
  entered, release = threading.Event(), threading.Event()
  display = OverlapDisplay()
  display.release_send.set()

  def prepare(frame, created_at):
    entered.set()
    release.wait(timeout=2.0)
    if result == "exception":
      raise RuntimeError("test encoding failure")
    if result == "success" and frame == "next":
      # Otherwise both encodes can win the scheduling race before the sender
      # takes the first slot; replacing that slot is intentional latest-only
      # behavior, independent of the render/encode overlap checked here.
      assert display.send_started.wait(timeout=1.0)
    return None if result == "none" else frame

  display.prepare_frame = prepare
  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()
  try:
    pipeline.push("frame")
    assert entered.wait(timeout=1.0)
    # Rendering can run ahead of the encoder when no upload is holding a frame.
    # That allowance ends as soon as the pending raw slot has been filled.
    assert pipeline.has_render_capacity()
    pipeline.push("next")
    assert not pipeline.has_render_capacity()
    release.set()
    assert _wait_for(pipeline.has_render_capacity)
    if result == "success":
      assert _wait_for(lambda: pipeline.get_stats()["sent"] == 2)
    else:
      assert pipeline.get_stats()["encoded"] == pipeline.get_stats()["sent"] == 0
  finally:
    release.set()
    assert pipeline.close()


def test_single_push_wakes_encoder_when_sender_is_first_waiter():
  waiting = {name: threading.Event() for name in ("test-sender", "test-encoder")}

  class ObservedCondition(threading.Condition):
    def wait_for(self, predicate, timeout=None):
      waiting[threading.current_thread().name].set()
      return super().wait_for(predicate, timeout)

  display = OverlapDisplay()
  display.release_send.set()
  pipeline = ClusterDisplayPipeline(display)
  pipeline._condition = ObservedCondition()
  pipeline.running = True
  pipeline.sender_thread = threading.Thread(target=pipeline._sender_loop, name="test-sender", daemon=True)
  pipeline.encoder_thread = threading.Thread(target=pipeline._encoder_loop, name="test-encoder", daemon=True)
  pipeline.sender_thread.start()
  try:
    assert waiting["test-sender"].wait(timeout=1.0)
    pipeline.encoder_thread.start()
    assert waiting["test-encoder"].wait(timeout=1.0)
    pipeline.push("only frame")
    assert display.send_started.wait(timeout=1.0)
  finally:
    if pipeline.encoder_thread.ident is None:
      pipeline.encoder_thread = None
    assert pipeline.close()


def test_reconnect_sends_latest_prepared_frame():
  display = ReconnectDisplay()
  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()
  try:
    pipeline.push("stale")
    assert display.open_started.wait(timeout=1.0)

    pipeline.push("latest")
    assert display.latest_prepared.wait(timeout=1.0)
    display.allow_open.set()

    assert _wait_for(lambda: bool(display.sent))
    assert display.sent[0] == "latest"
    stats = pipeline.get_stats()
    assert stats["sent"] == 1
    assert stats["dropped_prepared"] == 1
    assert stats["send_failures"] == 0
  finally:
    display.allow_open.set()
    assert pipeline.close()


def test_send_failure_is_accounted_for():
  display = FailedSendDisplay()
  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()
  try:
    pipeline.push("frame")
    assert display.send_attempted.wait(timeout=1.0)
    assert _wait_for(lambda: pipeline.get_stats()["send_failures"] == 1)
    assert pipeline.get_stats()["sent"] == 0
  finally:
    assert pipeline.close()


def test_sender_exception_is_accounted_for_and_thread_survives():
  display = FailedSendDisplay(raises=True)
  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()
  try:
    pipeline.push("frame")
    assert display.send_attempted.wait(timeout=1.0)
    assert _wait_for(lambda: pipeline.get_stats()["send_failures"] == 1)
    assert pipeline.sender_thread.is_alive()
  finally:
    assert pipeline.close()


def test_sender_failure_resets_only_client_when_transport_supports_recovery(monkeypatch):
  display = FailedSendDisplay(raises=True)
  reset = threading.Event()

  def reset_connection():
    display.connected = False
    reset.set()

  def close():
    raise AssertionError("recoverable send failure must preserve the encoder")

  display.reset_connection, display.close = reset_connection, close
  pipeline = ClusterDisplayPipeline(display)
  monkeypatch.setattr(pipeline, "_wait_for_reconnect", lambda _timeout: None)
  pipeline.start()
  try:
    pipeline.push("failed")
    assert reset.wait(timeout=1.0)
    assert not display.connected
    assert pipeline.get_stats()["send_failures"] == 1
    assert pipeline.sender_thread.is_alive()
    display.raises = False
    display.send_prepared = lambda _frame: True
    display.connected = True
    pipeline.push("fresh")
    assert _wait_for(lambda: pipeline.get_stats()["sent"] == 1)
  finally:
    assert pipeline.close()


def test_transport_expiration_result_counts_as_drop_instead_of_send_failure():
  display = FailedSendDisplay()
  display.send_prepared = lambda _prepared: None
  pipeline = ClusterDisplayPipeline(display)
  pipeline.start()
  try:
    pipeline.push("frame")
    assert _wait_for(lambda: pipeline.get_stats()["dropped_stale"] == 1)
    stats = pipeline.get_stats()
    assert stats["sent"] == stats["send_failures"] == 0
    assert stats["dropped_prepared"] == 1
  finally:
    assert pipeline.close()
