"""Identify the receiver sources at startup without importing display libraries."""

import hashlib
from pathlib import Path
import re


BUILD_FILES = (
  "control_socket.py", "cluster_receiver.py", "display_controls.py",
  "hdmi_display.py", "receiver_build.py", "receiver_resources.py", "texture_presenter.py", "touch_status.py",
  "h264_protocol.py", "h264_receiver.py", "h264_scene.py", "scene_layers.py", "cluster_renderer.py", "cluster_render_support.py", "receiver_assets.txt",
)

RETIRED_FILES = {"cluster_protocol.py", "frame_stream.py", "tcp_diagnostics.py"}


def package_digest(directory=None):
  """Hash the same normalized package bytes as deploy.sh, including shared files."""
  directory = Path(__file__).resolve().parent if directory is None else Path(directory)
  sources = {name: directory / name for name in ("README.md", "requirements.txt", "receiver_assets.txt", "cluster-hdmi.service")}
  for pattern in ("*.py", "scripts/*.sh"):
    for path in directory.glob(pattern):
      if path.name not in RETIRED_FILES:
        sources[path.relative_to(directory).as_posix()] = path
  for name in ("cluster_renderer.py", "cluster_render_support.py"):
    if name not in sources:
      sources[name] = directory.parent.parent / name
  asset_root = directory if (directory / "assets").is_dir() else directory.parents[3]
  assets = (directory / "receiver_assets.txt").read_text(encoding="ascii").splitlines()
  if not assets or len(assets) > 128 or len(set(assets)) != len(assets):
    raise ValueError("Invalid receiver asset manifest")
  for name in assets:
    if re.fullmatch(r"assets/(?:fonts/[a-zA-Z0-9_-]+\.ttf|icons/[a-zA-Z0-9_-]+\.png)", name) is None:
      raise ValueError("Invalid receiver asset path")
    sources[name] = asset_root / name
  entries = []
  for name, path in sources.items():
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
      raise ValueError("Unsafe receiver package path")
    data = path.read_bytes()
    if not name.startswith("assets/"):
      data = re.sub(rb"\r(?=\n|$)", b"", data)
    entries.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
  return hashlib.sha256("".join(sorted(entries)).encode("ascii")).hexdigest()


class ReceiverVersion:
  def __init__(self, directory=None):
    self.directory = Path(__file__).resolve().parent if directory is None else Path(directory)
    self._fallback = None

  def sample(self):
    try:
      path = self.directory / "receiver-version"
      if path.is_symlink():
        raise ValueError("Unsafe receiver version")
      value = path.read_text(encoding="ascii").strip()
      match = re.fullmatch(r"(\d{14}) ([a-f0-9]{64})", value)
      if match:
        return {"pi_version": match[1], "pi_package_sha256": match[2]}
    except (OSError, UnicodeError, ValueError):
      pass
    if self._fallback is None:
      try:
        digest = package_digest(self.directory)
        self._fallback = {"pi_version": digest[:12], "pi_package_sha256": digest}
      except (OSError, UnicodeError, ValueError):
        self._fallback = {}
    return dict(self._fallback)


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
