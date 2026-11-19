"""Local readiness heartbeat for the cluster HUD."""

import contextlib
import json
import os
from pathlib import Path
import tempfile
import time


STATUS_FILE = Path(os.environ.get("CLUSTER_CONNECTION_STATUS_FILE", "/dev/shm/cluster-connection-status.json"))
STATUS_MAX_AGE = 3.0


def publish_cluster_connection(transport: str, connected: bool) -> None:
  # A missing status file must not interfere with the display transport.
  temporary = None
  with contextlib.suppress(OSError):
    try:
      with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=STATUS_FILE.parent,
                                       prefix=f".{STATUS_FILE.name}.", delete=False) as file:
        temporary = Path(file.name)
        json.dump({"transport": transport, "connected": connected, "updated_at": time.monotonic()}, file)
      os.replace(temporary, STATUS_FILE)
    finally:
      if temporary is not None:
        temporary.unlink(missing_ok=True)


def is_cluster_connected(transport: str) -> bool:
  try:
    status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    if not isinstance(status, dict) or status.get("transport") != transport or status.get("connected") is not True:
      return False
    updated_at = status.get("updated_at")
    return type(updated_at) in (int, float) and 0 <= time.monotonic() - updated_at <= STATUS_MAX_AGE
  except (OSError, ValueError, OverflowError):
    return False
