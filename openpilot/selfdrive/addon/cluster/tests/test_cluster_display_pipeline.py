import threading
import time
from types import SimpleNamespace

from PIL import Image

from openpilot.selfdrive.addon.cluster.cluster_display_pipeline import ClusterDisplayPipeline
from openpilot.selfdrive.addon.cluster.usb_display.turing_usb_display import TuringUsbDisplay


class OverlapDisplay:
  def __init__(self):
    self.connected = True
    self.prepare_count = 0
    self.send_started = threading.Event()
    self.second_prepared = threading.Event()
    self.release_send = threading.Event()

  def prepare_image(self, frame):
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

  def prepare_image(self, frame):
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

  def prepare_image(self, frame):
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


def test_image_timeout_is_forwarded_to_usb_transport():
  config = SimpleNamespace(
    jpeg_quality=68,
    rotate_180=False,
    usb_image_timeout_ms=1000,
    fps=20,
  )
  display = TuringUsbDisplay(config)
  prepared = display.prepare_image(Image.new("RGB", (1920, 462), (10, 20, 30)))
  assert prepared is not None

  captured = {}

  def send_jpeg(_device, _jpeg, **kwargs):
    captured.update(kwargs)
    return b"\x00\xc8"

  display.device = object()
  display.connected = True
  display._send_jpeg = send_jpeg
  display._resp_ok = lambda response: response == b"\x00\xc8"

  assert display.send_prepared(prepared)
  assert captured["timeout"] == config.usb_image_timeout_ms


def test_configurable_non_ack_threshold_disconnects():
  config = SimpleNamespace(
    jpeg_quality=68,
    rotate_180=False,
    usb_image_timeout_ms=1000,
    usb_max_consecutive_failures=2,
    fps=20,
  )
  display = TuringUsbDisplay(config)
  prepared = display.prepare_image(Image.new("RGB", (1920, 462), (10, 20, 30)))
  assert prepared is not None

  display.device = object()
  display.connected = True
  display._send_jpeg = lambda *_args, **_kwargs: None
  display._resp_ok = lambda _response: False

  assert not display.send_prepared(prepared)
  assert display.connected
  assert not display.send_prepared(prepared)
  assert not display.connected


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
