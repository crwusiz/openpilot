from io import BytesIO
from types import SimpleNamespace

import numpy as np
from PIL import Image

from openpilot.selfdrive.addon.cluster import cluster_jpeg
from openpilot.selfdrive.addon.cluster.cluster_jpeg import ClusterJpegEncoder, PreparedFrame


def _encoded_size(transport, size, rotate_180=False):
  config = SimpleNamespace(jpeg_quality=68, rotate_180=rotate_180)
  prepared = ClusterJpegEncoder(config, transport=transport).prepare_image(
    Image.new("RGB", size, (10, 20, 30)),
  )
  assert prepared is not None
  with Image.open(BytesIO(prepared.jpeg)) as encoded:
    return encoded.size


def test_network_frames_keep_hdmi_landscape_orientation():
  assert _encoded_size("network", (1920, 720)) == (1920, 720)
  assert _encoded_size("network", (1920, 720), rotate_180=True) == (1920, 720)


def test_usb_frames_keep_turzx_portrait_protocol_orientation():
  assert _encoded_size("usb", (1920, 462)) == (462, 1920)
  assert _encoded_size("usb", (1920, 462), rotate_180=True) == (462, 1920)


def test_network_quality_improves_image_detail_without_changing_usb_quality():
  config = SimpleNamespace(jpeg_quality=68, network_jpeg_quality=82, rotate_180=False)
  image = np.random.default_rng(7).integers(0, 256, (120, 240, 3), dtype=np.uint8)
  # Grayscale detail isolates JPEG quality from unavoidable chroma subsampling.
  image[:] = image[:, :, :1]
  frame = Image.fromarray(image)
  errors = {}
  for transport in ("network", "usb"):
    encoder = ClusterJpegEncoder(config, transport)
    prepared = encoder.prepare_image(frame)
    assert encoder.jpeg_quality == (82 if transport == "network" else 68)
    with Image.open(BytesIO(prepared.jpeg)) as decoded:
      if transport == "usb":
        decoded = decoded.transpose(Image.Transpose.ROTATE_90)
      errors[transport] = np.mean((np.asarray(decoded, dtype=np.float32) - image) ** 2)
  assert errors["network"] < errors["usb"]


def test_encoder_records_monotonic_creation_and_completion_times(monkeypatch):
  ticks = iter((10.0, 10.015))
  monkeypatch.setattr(cluster_jpeg, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
  config = SimpleNamespace(jpeg_quality=68, network_jpeg_quality=82, rotate_180=False)
  prepared = ClusterJpegEncoder(config, "network").prepare_image(Image.new("RGB", (32, 32)))
  assert prepared.created_at == 10.0 and prepared.encoded_at == 10.015
  assert abs(prepared.prepare_elapsed - 0.015) < 1e-6


def test_prepared_frame_keeps_existing_three_argument_construction():
  prepared = PreparedFrame(memoryview(b"jpeg"), 1, 0.005)
  assert prepared.created_at is prepared.encoded_at is None
