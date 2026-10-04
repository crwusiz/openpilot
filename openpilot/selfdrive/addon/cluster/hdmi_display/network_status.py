"""Share the current Orange Pi connection with the dashboard and C4 updater."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import time


STATUS_FILE = Path(os.environ.get("CLUSTER_NETWORK_STATUS_FILE", "/dev/shm/cluster-network-status.json"))
STATUS_MAX_AGE = 10.0


def write_network_status(ip: str | None, path: Path | None = None):
  path = path or STATUS_FILE
  temporary = None
  try:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as file:
      temporary = Path(file.name)
      json.dump({"connected": ip is not None, "ip": ip, "updated_at": time.monotonic()}, file)
    os.replace(temporary, path)
  finally:
    if temporary is not None:
      temporary.unlink(missing_ok=True)


def get_connected_pi_ip(path: Path | None = None) -> str | None:
  """Ignore disconnected, corrupt or expired state, including a crashed sender."""
  try:
    status = json.loads((path or STATUS_FILE).read_text(encoding="utf-8"))
    if not isinstance(status, dict) or status.get("connected") is not True or not isinstance(status.get("ip"), str):
      return None
    updated = status.get("updated_at")
    if type(updated) not in (int, float) or not 0 <= time.monotonic() - updated <= STATUS_MAX_AGE:
      return None
    return str(ipaddress.IPv4Address(status["ip"]))
  except (OSError, UnicodeError, ValueError, TypeError, KeyError):
    return None


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--ip", action="store_true", required=True, help="print the currently connected Orange Pi IPv4 address")
  parser.parse_args()
  ip = get_connected_pi_ip()
  if ip is None:
    parser.exit(1, "No current Orange Pi connection. Specify the Pi IP explicitly.\n")
  print(ip)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
