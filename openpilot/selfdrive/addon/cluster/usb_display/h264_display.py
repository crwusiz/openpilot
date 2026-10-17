"""Connect the composed-frame pipeline to hardware H.264 and TURZX USB."""

import time

from openpilot.selfdrive.addon.cluster.cluster_logging import flog
from openpilot.selfdrive.addon.cluster.usb_display.h264_encoder import HardwareH264Encoder
from openpilot.selfdrive.addon.cluster.usb_display.turing_h264_display import TuringH264Display


class H264DisplayAdapter:
  def __init__(self, config, *, encoder=None, transport=None):
    self.encoder = HardwareH264Encoder(config) if encoder is None else encoder
    self.transport = TuringH264Display(config) if transport is None else transport
    self.max_frame_age = max(0.05, float(getattr(config, "usb_max_frame_age", 0.25)))
    self.encoder_error = None
    self._perf_started = time.monotonic()
    self._encoded = 0
    self._prepare_time = self._convert_time = 0.0

  @property
  def connected(self):
    return self.transport.connected

  def open(self):
    return self.transport.open()

  def prepare_frame(self, image, created_at):
    self.check_encoder()
    try:
      frame = self.encoder.encode(image, created_at)
    except Exception as error:
      self.encoder_error = error
      raise
    if frame is not None:
      self._encoded += 1
      self._prepare_time += frame.prepare_elapsed
      self._convert_time += frame.convert_elapsed
    now = time.monotonic()
    if now - self._perf_started >= 10.0:
      flog(f"[CLUSTER_USB_H264_ENCODE_PERF] fps={self._encoded / (now - self._perf_started):.2f} | "
           + f"prepare_avg={self._prepare_time * 1000 / max(self._encoded, 1):.1f}ms | "
           + f"convert_avg={self._convert_time * 1000 / max(self._encoded, 1):.1f}ms | backend=qcom_v4l2")
      self._perf_started, self._encoded = now, 0
      self._prepare_time = self._convert_time = 0.0
    return frame

  def check_encoder(self):
    if self.encoder_error is not None:
      raise RuntimeError("USB H.264 encoder failed; inspect CLUSTER_USB_H264 logs and restart Cluster after correcting the error") from self.encoder_error

  def is_prepared_stale(self, frame):
    return time.monotonic() - frame.created_at > self.max_frame_age

  def send_prepared(self, frame):
    return self.transport.send_access_unit(frame.data, created_at=frame.created_at, keyframe=True)

  def reset_connection(self):
    # Sender recovery must not destroy a handle owned by the encoder worker.
    # GOP 1 output repeats SPS/PPS, so reopening USB needs no encoder reset.
    self.transport.close()

  def close(self):
    # The shared runtime joins encoder/sender workers before closing handles.
    try:
      self.encoder.close()
    finally:
      self.transport.close()
