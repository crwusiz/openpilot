from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from openpilot.selfdrive.addon.cluster.cluster_jpeg import ClusterJpegEncoder


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
