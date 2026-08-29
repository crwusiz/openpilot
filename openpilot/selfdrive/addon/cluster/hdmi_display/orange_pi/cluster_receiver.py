#!/usr/bin/env python3
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import logging
import socket
import subprocess
import time

try:
  from .cluster_protocol import ACK_DISPLAY_ERROR, ACK_OK, FRAME_HEADER, pack_ack, recv_exact, unpack_frame_header
except ImportError:
  from cluster_protocol import ACK_DISPLAY_ERROR, ACK_OK, FRAME_HEADER, pack_ack, recv_exact, unpack_frame_header


LOG = logging.getLogger("cluster_receiver")
DEFAULT_PORT = 9200
DEFAULT_INTERFACE = "wlan0"
DEFAULT_SCAN_TIMEOUT = 0.12
DEFAULT_SCAN_WORKERS = 32
DEFAULT_RECONNECT_DELAY = 2.0
FRAME_TIMEOUT_SECONDS = 5.0
MAX_SCAN_HOSTS = 512


def _run_ip_json(*args):
  result = subprocess.run(
    ["ip", "-j", *args], check=True, capture_output=True, text=True,
  )
  return json.loads(result.stdout)


def get_interface_network(interface: str) -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Network]:
  entries = _run_ip_json("-4", "addr", "show", "dev", interface)
  if not entries:
    raise RuntimeError(f"Wi-Fi interface not found: {interface}")

  addresses = [entry for entry in entries[0].get("addr_info", [])
               if entry.get("family") == "inet" and entry.get("scope") == "global"]
  if not addresses:
    raise RuntimeError(f"No IPv4 address assigned to {interface}")

  address = addresses[0]
  local_ip = ipaddress.ip_address(address["local"])
  network = ipaddress.ip_network(f"{local_ip}/{address['prefixlen']}", strict=False)
  if network.num_addresses - 2 > MAX_SCAN_HOSTS:
    limited = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    LOG.warning("Wi-Fi subnet %s is too large; limiting discovery to %s", network, limited)
    network = limited
  return local_ip, network


def _probe(address: str, port: int, timeout: float, stop) -> socket.socket | None:
  if stop.is_set():
    return None
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  sock.settimeout(timeout)
  try:
    sock.connect((address, port))
    if stop.is_set():
      sock.close()
      return None
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.settimeout(FRAME_TIMEOUT_SECONDS)
    return sock
  except OSError:
    sock.close()
    return None


def discover_c4(interface: str, port: int, timeout: float, workers: int):
  import threading

  local_ip, network = get_interface_network(interface)
  candidates = [str(address) for address in network.hosts() if address != local_ip]
  LOG.info("Scanning %s on TCP port %d via %s", network, port, interface)
  stop = threading.Event()
  winner = None

  with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
    futures = [executor.submit(_probe, address, port, timeout, stop) for address in candidates]
    for future in as_completed(futures):
      sock = future.result()
      if sock is None:
        continue
      if winner is None:
        winner = sock
        stop.set()
      else:
        sock.close()

  if winner is not None:
    LOG.info("Connected to C4 at %s:%d", winner.getpeername()[0], port)
  return winner


def receive_frames(sock, display, max_frames: int | None = None) -> int:
  received_frames = 0
  while max_frames is None or received_frames < max_frames:
    sequence, frame_size = unpack_frame_header(recv_exact(sock, FRAME_HEADER.size))
    jpeg = recv_exact(sock, frame_size)
    display_ok = display.send_jpeg(jpeg)
    status = ACK_OK if display_ok else ACK_DISPLAY_ERROR
    sock.sendall(pack_ack(sequence, status))
    received_frames += 1
  return received_frames


def parse_args():
  parser = argparse.ArgumentParser(description="Receive C4 cluster frames and show them on an Orange Pi HDMI display")
  parser.add_argument("--interface", default=DEFAULT_INTERFACE, help="Wi-Fi interface connected to the phone hotspot")
  parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="C4 TCP port to discover")
  parser.add_argument("--scan-timeout", type=float, default=DEFAULT_SCAN_TIMEOUT, help="Per-address TCP timeout in seconds")
  parser.add_argument("--scan-workers", type=int, default=DEFAULT_SCAN_WORKERS, help="Parallel subnet scan workers")
  parser.add_argument("--reconnect-delay", type=float, default=DEFAULT_RECONNECT_DELAY)
  parser.add_argument("--width", type=int, default=1920)
  parser.add_argument("--height", type=int, default=720)
  parser.add_argument("--display-index", type=int, default=0)
  parser.add_argument("--windowed", action="store_true", help="Run in a window instead of fullscreen")
  parser.add_argument("--show-cursor", action="store_true", help="Keep the pointer visible for touch debugging")
  return parser.parse_args()


def main():
  args = parse_args()
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
  )

  try:
    from .hdmi_display import HdmiDisplay
  except ImportError:
    from hdmi_display import HdmiDisplay

  display = HdmiDisplay(
    width=args.width,
    height=args.height,
    display_index=args.display_index,
    fullscreen=not args.windowed,
    show_cursor=args.show_cursor,
  )

  try:
    if not display.open():
      raise RuntimeError("Unable to initialize the Orange Pi HDMI display")
    while True:
      sock = None
      try:
        sock = discover_c4(args.interface, args.port, args.scan_timeout, args.scan_workers)
        if sock is None:
          time.sleep(args.reconnect_delay)
          continue
        receive_frames(sock, display)
      except (ConnectionError, OSError, RuntimeError, subprocess.SubprocessError, ValueError) as e:
        LOG.warning("Cluster connection unavailable: %s", e)
        time.sleep(args.reconnect_delay)
      finally:
        if sock is not None:
          try:
            sock.close()
          except OSError:
            pass
  except KeyboardInterrupt:
    LOG.info("Stopping cluster receiver")
  finally:
    display.close()


if __name__ == "__main__":
  main()
