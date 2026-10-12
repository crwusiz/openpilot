"""Identify the receiver sources at startup without importing display libraries."""

import hashlib
from pathlib import Path


BUILD_FILES = (
  "cluster_protocol.py", "cluster_receiver.py", "display_controls.py", "frame_stream.py",
  "hdmi_display.py", "receiver_build.py", "tcp_diagnostics.py", "texture_presenter.py",
)


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
          + f" | missing={','.join(missing) or 'none'} | unreadable={','.join(unreadable) or 'none'}")


if __name__ == "__main__":
  print(source_fingerprint())
