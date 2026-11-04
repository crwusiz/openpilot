"""Manual HDMI monitor profiles shared by C4 and the standalone Pi receiver."""

DEFAULT_HDMI_PROFILE = "1920x480"
HDMI_PROFILES = {
  "1920x480": {"width": 1920, "height": 480, "output_width": 480, "output_height": 1920, "rotation": 90},
  "1920x720": {"width": 1920, "height": 720, "output_width": 1920, "output_height": 720, "rotation": 0},
}


def normalize_hdmi_profile(value):
  if isinstance(value, bytes):
    value = value.decode("utf-8", errors="replace")
  value = value.strip().lower() if isinstance(value, str) else ""
  return value if value in HDMI_PROFILES else DEFAULT_HDMI_PROFILE
