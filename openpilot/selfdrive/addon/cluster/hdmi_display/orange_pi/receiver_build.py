"""Identify the receiver sources at startup without importing display libraries."""

import hashlib
from pathlib import Path
import re


BUILD_FILES = (
  "cluster_protocol.py", "cluster_receiver.py", "display_controls.py", "frame_stream.py",
  "hdmi_display.py", "receiver_build.py", "tcp_diagnostics.py", "texture_presenter.py", "touch_status.py",
  "h264_protocol.py", "h264_receiver.py", "h264_scene.py", "cluster_renderer.py", "cluster_render_support.py", "receiver_assets.txt",
)


def _asset_snapshot(directory):
  aggregate = hashlib.sha256()
  missing = []
  try:
    names = (directory / "receiver_assets.txt").read_text(encoding="utf-8").splitlines()
  except (OSError, UnicodeError):
    return "assets_sha256=unavailable | missing_assets=manifest"
  if not names or len(names) > 128 or len(set(names)) != len(names) or any(
    re.fullmatch(r"assets/(?:fonts/[a-zA-Z0-9_-]+\.ttf|icons/[a-zA-Z0-9_-]+\.png)", name) is None for name in names
  ):
    return "assets_sha256=unavailable | missing_assets=invalid_manifest"
  for name in names:
    path = directory / name
    try:
      if any((directory / parent).is_symlink() for parent in (name, str(Path(name).parent), "assets")):
        raise OSError("unsafe asset path")
      digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
      digest = "missing_or_unreadable"
      missing.append(name)
    aggregate.update(f"{name}\0{digest}\n".encode())
  return f"assets_sha256={aggregate.hexdigest()} | asset_files={len(names)} | missing_assets={','.join(missing) or 'none'}"


def source_fingerprint(directory=None):
  directory = Path(__file__).resolve().parent if directory is None else Path(directory)
  aggregate = hashlib.sha256()
  files = []
  missing = []
  unreadable = []
  for name in BUILD_FILES:
    try:
      digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    except FileNotFoundError:
      digest = "missing"
      missing.append(name)
    except OSError:
      digest = "unreadable"
      unreadable.append(name)
    aggregate.update(f"{name}\0{digest}\n".encode())
    files.append(f"{name}:{digest[:12]}")
  return (f"source_sha256={aggregate.hexdigest()} | files={','.join(files)}"
          + f" | missing={','.join(missing) or 'none'} | unreadable={','.join(unreadable) or 'none'} | {_asset_snapshot(directory)}")


if __name__ == "__main__":
  print(source_fingerprint())
