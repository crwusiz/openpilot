#!/usr/bin/env python3
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import ipaddress
import json
import logging
import math
import signal
import socket
import subprocess
import threading
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
FRAME_TIMEOUT_SECONDS = 2.0
MAX_SCAN_HOSTS = 512


def _run_ip_json(*args):
  result = subprocess.run(
    ["ip", "-j", *args], check=True, capture_output=True, text=True, timeout=2.0,
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


def discover_c4(interface: str, port: int, timeout: float, workers: int, poll_events=None):
  local_ip, network = get_interface_network(interface)
  candidates = [str(address) for address in network.hosts() if address != local_ip]
  LOG.info("Scanning %s on TCP port %d via %s", network, port, interface)
  stop = threading.Event()
  winner = None
  futures = []
  completed = False

  try:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
      futures = [executor.submit(_probe, address, port, timeout, stop) for address in candidates]
      pending = set(futures)
      try:
        while pending:
          if poll_events is not None:
            poll_events()
          done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
          for future in done:
            sock = future.result()
            if sock is not None and winner is None:
              winner = sock
              stop.set()
      finally:
        stop.set()
    completed = True
  finally:
    # Workers may finish connecting while another worker wins or SDL requests
    # shutdown. Close every unused socket after the executor has stopped.
    for future in futures:
      if not future.cancelled() and future.exception() is None:
        sock = future.result()
        if sock is not None and (not completed or sock is not winner):
          sock.close()

  if winner is not None:
    LOG.info("Connected to C4 at %s:%d", winner.getpeername()[0], port)
  return winner


def _poll_display(display):
  if not display.pump_events():
    raise KeyboardInterrupt


def wait_for_reconnect(display, delay):
  deadline = time.monotonic() + delay
  while True:
    _poll_display(display)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      return
    time.sleep(min(remaining, 0.05))


def receive_frames(sock, display, max_frames: int | None = None, frame_timeout=FRAME_TIMEOUT_SECONDS) -> int:
  def poll_events():
    _poll_display(display)

  received_frames = 0
  while max_frames is None or received_frames < max_frames:
    # One deadline for the entire frame also bounds slow, partial deliveries.
    deadline = time.monotonic() + frame_timeout
    sequence, frame_size = unpack_frame_header(recv_exact(sock, FRAME_HEADER.size, deadline=deadline, poll_events=poll_events))
    jpeg = recv_exact(sock, frame_size, deadline=deadline, poll_events=poll_events)
    display_ok = display.send_jpeg(jpeg)
    status = ACK_OK if display_ok else ACK_DISPLAY_ERROR
    sock.sendall(pack_ack(sequence, status))
    if not display_ok:
      _poll_display(display)
      raise RuntimeError("Unable to display cluster frame")
    received_frames += 1
  return received_frames


def _positive_float(value):
  number = float(value)
  if not math.isfinite(number) or number <= 0:
    raise argparse.ArgumentTypeError("must be a finite number greater than zero")
  return number


def _positive_int(value):
  number = int(value)
  if number <= 0:
    raise argparse.ArgumentTypeError("must be greater than zero")
  return number


def parse_args(argv=None):
  parser = argparse.ArgumentParser(description="Receive C4 cluster frames and show them on an Orange Pi HDMI display")
  parser.add_argument("--interface", default=DEFAULT_INTERFACE, help="Wi-Fi interface connected to the phone hotspot")
  parser.add_argument("--host", type=ipaddress.IPv4Address, help="Connect directly to this C4 IPv4 address instead of scanning")
  parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="C4 TCP port to discover")
  parser.add_argument("--scan-timeout", type=_positive_float, default=DEFAULT_SCAN_TIMEOUT, help="Per-address TCP timeout in seconds")
  parser.add_argument("--scan-workers", type=_positive_int, default=DEFAULT_SCAN_WORKERS, help="Parallel subnet scan workers")
  parser.add_argument("--reconnect-delay", type=_positive_float, default=DEFAULT_RECONNECT_DELAY)
  parser.add_argument("--frame-timeout", type=_positive_float, default=FRAME_TIMEOUT_SECONDS, help="Seconds without a complete frame before reconnecting")
  parser.add_argument("--width", type=_positive_int, default=1920)
  parser.add_argument("--height", type=_positive_int, default=480)
  parser.add_argument("--display-index", type=int, default=0)
  parser.add_argument("--windowed", action="store_true", help="Run in a window instead of fullscreen")
  parser.add_argument("--show-cursor", action="store_true", help="Keep the pointer visible for touch debugging")
  args = parser.parse_args(argv)
  if not 1 <= args.port <= 65535:
    parser.error("--port must be between 1 and 65535")
  if args.display_index < 0:
    parser.error("--display-index must be zero or greater")
  return args


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

  def stop_receiver(_signum, _frame):
    raise KeyboardInterrupt

  signal.signal(signal.SIGTERM, stop_receiver)

  try:
    if not display.open():
      raise RuntimeError("Unable to initialize the Orange Pi HDMI display")
    while True:
      sock = None
      try:
        _poll_display(display)
        if args.host is not None:
          sock = _probe(str(args.host), args.port, args.scan_timeout, threading.Event())
        else:
          sock = discover_c4(args.interface, args.port, args.scan_timeout, args.scan_workers, lambda: _poll_display(display))
        if sock is not None:
          sock.settimeout(args.frame_timeout)
          receive_frames(sock, display, frame_timeout=args.frame_timeout)
      except (ConnectionError, OSError, RuntimeError, subprocess.SubprocessError, ValueError) as e:
        LOG.warning("Cluster connection unavailable: %s", e)
      finally:
        if sock is not None:
          try:
            sock.close()
          except OSError:
            pass
        display.clear()
      wait_for_reconnect(display, args.reconnect_delay)
  except KeyboardInterrupt:
    LOG.info("Stopping cluster receiver")
  finally:
    display.close()


if __name__ == "__main__":
  main()
