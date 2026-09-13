from io import BytesIO
import time
from typing import NamedTuple

import cv2
import numpy as np
from PIL import Image

from openpilot.selfdrive.addon.cluster.cluster_logging import flog


class PreparedFrame(NamedTuple):
  jpeg: memoryview
  size_kb: int
  prepare_elapsed: float


class ClusterJpegEncoder:
  def __init__(self, config, transport: str):
    self.config = config
    if transport not in ("network", "usb"):
      raise ValueError(f"Unsupported JPEG transport: {transport}")
    self.transport = transport
    self.jpeg_quality = min(max(int(getattr(config, "jpeg_quality", 68)), 1), 95)
    self._encode_param = [
      int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality,
      int(cv2.IMWRITE_JPEG_PROGRESSIVE), 0,
      int(cv2.IMWRITE_JPEG_OPTIMIZE), 0,
    ]

  def _pil_rotation(self):
    if self.transport == "usb":
      # TURZX expects portrait JPEG data even though the renderer is landscape.
      return Image.Transpose.ROTATE_90 if getattr(self.config, "rotate_180", False) \
             else Image.Transpose.ROTATE_270
    return Image.Transpose.ROTATE_180 if getattr(self.config, "rotate_180", False) else None

  def prepare_image(self, frame_image):
    """Apply transport-specific orientation and encode a rendered cluster frame."""
    try:
      prepare_started = time.monotonic()
      if isinstance(frame_image, Image.Image):
        rotation = self._pil_rotation()
        oriented = frame_image.transpose(rotation) if rotation is not None else frame_image
        encoded_buffer = BytesIO()
        oriented.save(encoded_buffer, format="JPEG", quality=self.jpeg_quality,
                      progressive=False, optimize=False, subsampling=2)
        jpg_data = encoded_buffer.getbuffer()
        success = True
      else:
        if not isinstance(frame_image, np.ndarray):
          frame_image = np.asarray(frame_image)

        if self.transport == "usb":
          rotation = cv2.ROTATE_90_COUNTERCLOCKWISE if getattr(self.config, "rotate_180", False) \
                     else cv2.ROTATE_90_CLOCKWISE
          oriented = cv2.rotate(frame_image, rotation)
        elif getattr(self.config, "rotate_180", False):
          oriented = cv2.rotate(frame_image, cv2.ROTATE_180)
        else:
          oriented = frame_image
        bgr = cv2.cvtColor(oriented, cv2.COLOR_RGB2BGR)
        success, encoded_img = cv2.imencode('.jpg', bgr, self._encode_param)
        jpg_data = memoryview(encoded_img) if success else None

      if not success or jpg_data is None:
        flog("[CLUSTER_ENCODE_ERROR] JPEG encoding failed; skipping frame.")
        return None

      return PreparedFrame(
        jpeg=jpg_data,
        size_kb=len(jpg_data) // 1024,
        prepare_elapsed=time.monotonic() - prepare_started,
      )
    except Exception as e:
      flog(f"[CLUSTER_ENCODE_ERROR] Failed to encode display frame: {e}")
      return None
