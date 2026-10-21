#!/usr/bin/env python3
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import ipaddress
import json
import logging
import math
import os
import signal
import socket
import subprocess
import threading
import time

try:
  from .control_socket import ReceiveStats, recv_exact
  from .h264_protocol import CONTROL_HEADER, HELLO, decode_payload, unpack_control_header
  from .h264_receiver import receive_h264_session
  from .receiver_build import source_fingerprint
except ImportError:
  from control_socket import ReceiveStats, recv_exact
  from h264_protocol import CONTROL_HEADER, HELLO, decode_payload, unpack_control_header
  from h264_receiver import receive_h264_session
  from receiver_build import source_fingerprint


LOG = logging.getLogger("cluster_receiver")
DEFAULT_PORT = 9200
DEFAULT_INTERFACE = "wlan0"
DEFAULT_SCAN_TIMEOUT = 0.12
DEFAULT_SCAN_WORKERS = 32
DEFAULT_RECONNECT_DELAY = 2.0
KNOWN_CONNECT_TIMEOUT = 1.0
KNOWN_CONNECT_ATTEMPTS = 2
KNOWN_CONNECT_RETRY_DELAY = 0.15
KNOWN_CONNECT_BUDGET = 2.0
FRAME_TIMEOUT_SECONDS = 2.0
MAX_SCAN_HOSTS = 512


def log_runtime_versions(pygame_module):
  # Called once by the SDL owner, never by the socket worker.
  pygame_version = getattr(getattr(pygame_module, "version", None), "ver", "unknown")

  def get_version(module, function):
    try:
      return ".".join(str(part) for part in getattr(module, function)())
    except (AttributeError, TypeError, RuntimeError):
      return "unknown"

  LOG.info("[CLUSTER_RX_RUNTIME] pygame=%s | SDL=%s | SDL_image=%s", pygame_version,
           get_version(pygame_module, "get_sdl_version"), get_version(getattr(pygame_module, "image", None), "get_sdl_image_version"))


def configure_native_runtime():
  # Decoding, RGB conversion and SDL already overlap. Eight OpenCV workers for
  # each short path/resize operation compete with those workers and add jitter.
  # Existing installations keep their custom systemd unit during pi_update;
  # apply the limits here too, before importing NumPy/OpenCV for the first time.
  for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
  import cv2
  cv2.setNumThreads(1)
  cv2.ocl.setUseOpenCL(False)
  LOG.info("[CLUSTER_RX_NATIVE] opencv_threads=%d | opencl=%d", cv2.getNumThreads(), int(cv2.ocl.useOpenCL()))


def log_socket_buffers(sock):
  def buffer_size(option):
    try:
      return sock.getsockopt(socket.SOL_SOCKET, option)
    except (AttributeError, OSError):
      return "unknown"

  # Report the effective kernel values; socket buffer sizes remain unchanged.
  LOG.info("[CLUSTER_RX_SOCKET] recv_buffer=%sB | send_buffer=%sB",
           buffer_size(socket.SO_RCVBUF), buffer_size(socket.SO_SNDBUF))


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


def connect_known_c4(address: str, port: int, poll_events=None):
  """Give a known peer a bounded retry before scanning the entire subnet."""
  deadline = time.monotonic() + KNOWN_CONNECT_BUDGET
  stop = threading.Event()
  futures = []
  winner = None
  completed = False
  try:
    with ThreadPoolExecutor(max_workers=1) as executor:
      try:
        for attempt in range(KNOWN_CONNECT_ATTEMPTS):
          if poll_events is not None:
            poll_events()
          remaining = deadline - time.monotonic()
          if remaining <= 0:
            break
          timeout = min(KNOWN_CONNECT_TIMEOUT, remaining)
          LOG.info("[CLUSTER_RX_CONNECT] Known C4 %s:%d, attempt=%d/%d, timeout=%.2fs",
                   address, port, attempt + 1, KNOWN_CONNECT_ATTEMPTS, timeout)
          future = executor.submit(_probe, address, port, timeout, stop)
          futures.append(future)
          while not future.done():
            if poll_events is not None:
              poll_events()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
              break
            wait([future], timeout=min(remaining, 0.05))
          if not future.done():
            break
          candidate = future.result()
          if time.monotonic() > deadline:
            break
          if candidate is not None:
            winner = candidate
            break
          if attempt + 1 < KNOWN_CONNECT_ATTEMPTS:
            retry_at = min(deadline, time.monotonic() + KNOWN_CONNECT_RETRY_DELAY)
            while True:
              if poll_events is not None:
                poll_events()
              remaining = retry_at - time.monotonic()
              if remaining <= 0:
                break
              time.sleep(min(remaining, 0.05))
      finally:
        stop.set()
    completed = True
  finally:
    # A probe may connect while SDL requests shutdown or its budget expires.
    for future in futures:
      if not future.cancelled() and future.exception() is None:
        sock = future.result()
        if sock is not None and (not completed or sock is not winner):
          sock.close()
  return winner


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
  """Validate the H.264 hello before starting WebRTC and local composition."""
  if max_frames == 0:
    return 0
  log_socket_buffers(sock)
  def poll_events():
    _poll_display(display)
  header_stats = ReceiveStats()
  header = recv_exact(sock, CONTROL_HEADER.size, deadline=time.monotonic() + frame_timeout,
                      poll_events=poll_events, stats=header_stats, timeout_from_first_byte=frame_timeout, phase="header")
  kind, _sequence, size = unpack_control_header(header)
  if kind != HELLO:
    raise ValueError("Expected H.264 session hello")
  payload = recv_exact(sock, size, deadline=header_stats.first_byte_at + frame_timeout,
                       poll_events=poll_events, phase="h264_hello")
  hello = decode_payload(payload)
  return receive_h264_session(sock, display, hello, frame_timeout, max_frames)


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
  parser.add_argument("--frame-timeout", type=_positive_float, default=FRAME_TIMEOUT_SECONDS,
                      help="Video freshness timeout and H.264 signaling handshake deadline in seconds")
  parser.add_argument("--width", type=_positive_int, default=1920, help="Physical HDMI output width, after frame rotation")
  parser.add_argument("--height", type=_positive_int, default=480, help="Physical HDMI output height, after frame rotation")
  parser.add_argument("--rotation", type=int, choices=(0, 90, 180, 270), default=0, help="Clockwise display rotation in degrees")
  parser.add_argument("--touch-rotation", type=int, choices=(0, 90, 180, 270),
                      help="Clockwise correction from raw touch to cluster coordinates (default: inverse of --rotation)")
  parser.add_argument("--log-touch", action="store_true", help="Log raw and corrected SDL finger coordinates")
  parser.add_argument("--display-index", type=int, default=0)
  parser.add_argument("--brightness", type=int, default=80,
                      help="Initial brightness percent from 10 to 100, rounded to the nearest 10 percent")
  parser.add_argument("--renderer", choices=("auto", "surface"), default="auto",
                      help="Try accelerated textures automatically or use the CPU surface renderer")
  parser.add_argument("--windowed", action="store_true", help="Run in a window instead of fullscreen")
  parser.add_argument("--show-cursor", action="store_true", help="Keep the pointer visible for touch debugging")
  args = parser.parse_args(argv)
  if not 1 <= args.port <= 65535:
    parser.error("--port must be between 1 and 65535")
  if args.display_index < 0:
    parser.error("--display-index must be zero or greater")
  if not 10 <= args.brightness <= 100:
    parser.error("--brightness must be between 10 and 100")
  return args


def main():
  args = parse_args()
  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
  )
  configure_native_runtime()

  try:
    from .hdmi_display import HdmiDisplay
  except ImportError:
    from hdmi_display import HdmiDisplay

  # Capture once, before the receive loop. A later deployment can change disk
  # files while this process keeps executing its already imported sources.
  LOG.info("[CLUSTER_RX_BUILD] startup_%s", source_fingerprint())

  display = HdmiDisplay(
    width=args.width,
    height=args.height,
    display_index=args.display_index,
    fullscreen=not args.windowed,
    show_cursor=args.show_cursor,
    rotation=args.rotation,
    touch_rotation=args.touch_rotation,
    log_touch=args.log_touch,
    network_interface=args.interface,
    brightness=args.brightness,
    renderer_mode=args.renderer,
  )

  def stop_receiver(_signum, _frame):
    raise KeyboardInterrupt

  signal.signal(signal.SIGTERM, stop_receiver)

  try:
    if not display.open():
      raise RuntimeError("Unable to initialize the Orange Pi HDMI display")
    if not display.show_waiting():
      raise RuntimeError("Unable to display the Orange Pi connection waiting screen")
    log_runtime_versions(getattr(display, "_pygame", None))
    LOG.info("Cluster receiver ready")
    last_c4_address = None
    while True:
      sock = None
      connected_at = None
      configuration_delay = 0.0
      try:
        _poll_display(display)
        if args.host is not None:
          sock = connect_known_c4(str(args.host), args.port, lambda: _poll_display(display))
        else:
          if last_c4_address is not None:
            sock = connect_known_c4(last_c4_address, args.port, lambda: _poll_display(display))
            if sock is not None:
              LOG.info("[CLUSTER_RX_RECONNECT] Connected to last C4 at %s:%d", last_c4_address, args.port)
          if sock is None:
            sock = discover_c4(args.interface, args.port, args.scan_timeout, args.scan_workers, lambda: _poll_display(display))
          if sock is not None:
            last_c4_address = sock.getpeername()[0]
        if sock is not None:
          sock.settimeout(args.frame_timeout)
          if hasattr(display, "set_c4_peer"):
            display.set_c4_peer(str(args.host) if args.host is not None else last_c4_address)
          connected_at = time.monotonic()
          receive_frames(sock, display, frame_timeout=args.frame_timeout)
      except (ConnectionError, OSError, RuntimeError, subprocess.SubprocessError, ValueError) as e:
        LOG.warning("Cluster connection unavailable: %s", e)
        configuration_delay = min(5.0, max(0.0, float(getattr(e, "retry_delay_s", 0.0))))
      finally:
        if hasattr(display, "set_c4_peer"):
          display.set_c4_peer(None)
        if sock is not None:
          try:
            sock.close()
          except OSError:
            pass
        display.show_waiting()
      # Keep the existing backoff for failed/short connections. A live session
      # has already spaced attempts apart and can retry the cached C4 at once.
      if configuration_delay:
        # Missing assets/dependencies need an update, not a tight reconnect
        # loop that repeats the same setup failure and fills both logs.
        wait_for_reconnect(display, max(configuration_delay, args.reconnect_delay))
      elif connected_at is None or time.monotonic() - connected_at < args.reconnect_delay:
        wait_for_reconnect(display, args.reconnect_delay)
  except KeyboardInterrupt:
    LOG.info("Stopping cluster receiver")
  finally:
    display.close()


if __name__ == "__main__":
  main()
