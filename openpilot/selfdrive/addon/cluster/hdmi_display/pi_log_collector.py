"""Collect bounded, read-only Orange Pi diagnostics for a C4 cluster upload."""
import argparse
from datetime import UTC, datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import NamedTuple

try:
  from .network_status import get_connected_pi_ip
except ImportError:
  from network_status import get_connected_pi_ip


COLLECTION_TIMEOUT = 25.0
MAX_OUTPUT_BYTES = 512 * 1024
MAX_STDERR_BYTES = 16 * 1024
LOCAL_WIFI_TIMEOUT = 2.0
MAX_LOCAL_WIFI_BYTES = 4 * 1024
MAX_LOCAL_COMMAND_BYTES = 16 * 1024
MAX_C4_LOG_BYTES = 32 * 1024 * 1024
C4_CRASH_LOG_DIRECTORY = Path("/data/log")
MAX_C4_CRASH_BYTES = 16 * 1024
C4_LOG_CHUNK_BYTES = 64 * 1024
C4_LOG_OVERLAP_BYTES = 256
PEER_PATTERN = re.compile(r"\[CLUSTER_NETWORK_SUCCESS\] Orange Pi connected from ([0-9.]+):[0-9]+\.")
NETWORK_EVIDENCE = re.compile(r"\[CLUSTER_NETWORK_|\bTransport:\s*network\b", re.IGNORECASE)
USB_EVIDENCE = re.compile(r"\[CLUSTER_USB(?:_|\])|\bTransport:\s*usb\b", re.IGNORECASE)
TRANSPORT_ERRORS = re.compile(
  r"^ssh: connect to host .* port 9122: (?:Connection refused|Connection timed out|Operation timed out|"
  + r"No route to host|Network is unreachable)|^Connection to .* port 9122 timed out$|"
  + r"^Connection (?:closed|reset) by .* port 9122$", re.MULTILINE,
)
ASKPASS_SCRIPT = """#!/bin/sh
case ${1:-} in
  *[Pp]assword:*) printf '%s\\n' "$CLUSTER_PI_PASSWORD";;
  *) exit 1;;
esac
"""
REMOTE_SCRIPT = r"""export LC_ALL=C SYSTEMD_COLORS=0
printf '%s\n' '--- Pi UTC wall clock ---'
date -u '+%Y-%m-%dT%H:%M:%SZ'
printf '\n--- Pi monotonic uptime ---\n'
cat /proc/uptime
printf '\n--- Pi current boot ID ---\n'
cat /proc/sys/kernel/random/boot_id
printf '\n--- Pi kernel ---\n'
uname -sr
printf '\n--- Pi fan boot configuration and active cooling levels ---\n'
if [ -r /boot/orangepiEnv.txt ]; then
  sed -n '/^user_overlays=/p' /boot/orangepiEnv.txt
fi
for levels in /sys/firmware/devicetree/base/soc*/pwm-fan/cooling-levels; do
  if [ -r "$levels" ]; then
    printf 'active_fan_cooling_levels[%s]=' "$levels"
    od -An -v --endian=big -tu4 "$levels"
  fi
done
for hwmon in /sys/class/hwmon/hwmon*; do
  [ -r "$hwmon/name" ] && [ "$(cat "$hwmon/name")" = pwmfan ] || continue
  printf 'fan_device=%s\n' "$hwmon"
  for field in pwm1 pwm1_enable fan1_input; do
    if [ -r "$hwmon/$field" ]; then printf '%s=' "$field"; cat "$hwmon/$field"; fi
  done
done
printf '\n--- Pi temperatures and thermal governors ---\n'
for zone in /sys/class/thermal/thermal_zone*; do
  [ -r "$zone/temp" ] || continue
  printf 'zone=%s ' "$zone"
  for field in type temp policy; do
    if [ -r "$zone/$field" ]; then printf '%s=' "$field"; tr '\n' ' ' < "$zone/$field"; fi
  done
  printf '\n'
done
printf '\n--- Receiver service state ---\n'
systemctl show cluster-hdmi.service \
  --property=ActiveState,SubState,MainPID,ExecMainStartTimestamp,ExecMainStartTimestampMonotonic,ExecMainStatus,NRestarts,User,Group
printf '\n--- Installed receiver source (compare with CLUSTER_RX_BUILD startup snapshot) ---\n'
if [ -r /opt/cluster-receiver/receiver_build.py ] && command -v python3 >/dev/null 2>&1; then
  python3 /opt/cluster-receiver/receiver_build.py
else
  printf '%s\n' 'Receiver build helper unavailable; Pi update/restart cannot be verified from this snapshot.'
fi
printf '\n--- Receiver asset directory permissions ---\n'
stat -c '%A %a %U:%G %n' /opt/cluster-receiver /opt/cluster-receiver/assets \
  /opt/cluster-receiver/assets/fonts /opt/cluster-receiver/assets/icons \
  /opt/cluster-receiver/assets/fonts/Inter-Bold.ttf /opt/cluster-receiver/assets/fonts/Inter-Regular.ttf
printf '\n--- Receiver assets as the service account (compare with root snapshot) ---\n'
receiver_user=$(systemctl show cluster-hdmi.service --property=User --value)
receiver_user=${receiver_user:-root}
case "$receiver_user" in
  *[!a-zA-Z0-9_-]*) printf '%s\n' 'Unsupported receiver account name; service-account snapshot skipped.';;
  *) if [ "$(id -u)" = 0 ] && command -v runuser >/dev/null 2>&1; then
       runuser -u "$receiver_user" -- /usr/bin/python3 /opt/cluster-receiver/receiver_build.py
     elif [ "$(id -un)" = "$receiver_user" ]; then
       /usr/bin/python3 /opt/cluster-receiver/receiver_build.py
     else
       printf '%s\n' 'Service-account snapshot unavailable from this SSH account.'
     fi;;
esac
printf '\n--- Receiver SDL renderer settings ---\n'
pid=$(systemctl show cluster-hdmi.service --property=MainPID --value)
case "$pid" in
  ''|*[!0-9]*) ;;
  *) if [ "$pid" -gt 0 ] && [ -r "/proc/$pid/environ" ]; then
       tr '\000' '\n' < "/proc/$pid/environ" | grep -E '^SDL_(VIDEODRIVER|RENDER_DRIVER)='
     fi;;
esac
printf '\n--- Wi-Fi state (no SSID/profile/password) ---\n'
if command -v nmcli >/dev/null 2>&1; then
  nmcli --terse --fields GENERAL.STATE,GENERAL.TYPE device show wlan0
fi
ip -4 -o address show dev wlan0
if command -v iw >/dev/null 2>&1; then
  iw dev wlan0 get power_save
  iw dev wlan0 link | sed -n -E '/^[[:space:]]*(freq:|signal:|rx bitrate:|tx bitrate:)/p'
  printf '\n--- Wi-Fi station counters (since association; no peer address) ---\n'
  iw dev wlan0 station dump | sed -n -E \
    -e '/^[[:space:]]*(inactive time:|rx bytes:|rx packets:|tx bytes:|tx packets:)/p' \
    -e '/^[[:space:]]*(tx retries:|tx failed:|beacon loss:|beacon rx:|rx drop misc:)/p' \
    -e '/^[[:space:]]*(signal:|signal avg:|rx bitrate:|tx bitrate:|expected throughput:)/p'
fi
printf '\n--- Wi-Fi interface counters (since interface creation) ---\n'
for counter in rx_bytes rx_packets rx_errors rx_dropped tx_bytes tx_packets tx_errors tx_dropped; do
  if [ -r "/sys/class/net/wlan0/statistics/$counter" ]; then
    printf '%s=' "$counter"
    cat "/sys/class/net/wlan0/statistics/$counter"
  fi
done
printf '\n--- Receiver journal (available boots, UTC, latest 2500 lines, newest first) ---\n'
journalctl -u cluster-hdmi.service --utc --no-pager --output=short-iso -n 2500 --reverse
"""
REMOTE_COMMAND = "sh -c " + shlex.quote(REMOTE_SCRIPT)


class SSHResult(NamedTuple):
  returncode: int
  stdout: str
  stderr: str
  truncated: bool = False
  timed_out: bool = False


def _ipv4(value):
  return str(ipaddress.IPv4Address(value))


class USBOnlyLog(ValueError):
  """An explicitly USB-only log needs no Orange Pi connection."""


def resolve_host(c4_log, environ=None, deadline=None):
  environ = os.environ if environ is None else environ
  if environ.get("CLUSTER_PI_HOST"):
    return _ipv4(environ["CLUSTER_PI_HOST"]), "CLUSTER_PI_HOST"
  connected = get_connected_pi_ip()
  if connected is not None:
    return _ipv4(connected), "current connection"
  saw_usb = saw_network = False
  with Path(c4_log).open("rb") as file:
    file.seek(0, os.SEEK_END)
    position = file.tell()
    lower_bound = max(0, position - MAX_C4_LOG_BYTES)
    overlap = b""
    while position > lower_bound:
      if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("Orange Pi diagnostics exceeded the 25 second collection deadline")
      start = max(lower_bound, position - C4_LOG_CHUNK_BYTES)
      file.seek(start)
      chunk = file.read(position - start)
      text = (chunk + overlap).decode("utf-8", errors="replace")
      saw_usb |= USB_EVIDENCE.search(text) is not None
      saw_network |= NETWORK_EVIDENCE.search(text) is not None
      for match in reversed(list(PEER_PATTERN.finditer(text))):
        try:
          return _ipv4(match.group(1)), "last peer in C4 log"
        except ValueError:
          continue
      overlap = chunk[:C4_LOG_OVERLAP_BYTES]
      position = start
    if position == 0 and saw_usb and not saw_network:
      raise USBOnlyLog("C4 log contains only USB transport; Orange Pi diagnostics do not apply")
  raise ValueError("No Orange Pi address in current connection or C4 log; set CLUSTER_PI_HOST")


class _Capture:
  def __init__(self, limit):
    self.limit = limit
    self.data = bytearray()
    self.truncated = False

  def read(self, stream):
    while chunk := stream.read(4096):
      remaining = self.limit - len(self.data)
      self.data.extend(chunk[:max(0, remaining)])
      self.truncated |= len(chunk) > remaining
    stream.close()

  def text(self):
    return self.data.decode("utf-8", errors="replace")


def _run_ssh(command, environ, timeout, *, stdout_limit=MAX_OUTPUT_BYTES, stderr_limit=MAX_STDERR_BYTES):
  process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=environ, start_new_session=True)
  stdout, stderr = _Capture(stdout_limit), _Capture(stderr_limit)
  readers = [threading.Thread(target=capture.read, args=(stream,), daemon=True)
             for capture, stream in ((stdout, process.stdout), (stderr, process.stderr))]
  for reader in readers:
    reader.start()
  timed_out = False
  try:
    process.wait(timeout=timeout)
  except subprocess.TimeoutExpired:
    timed_out = True
    if os.name == "posix":
      try:
        os.killpg(process.pid, signal.SIGKILL)
      except ProcessLookupError:
        pass
    else:
      process.kill()
    process.wait(timeout=1.0)
  for reader in readers:
    reader.join(timeout=0.5)
  return SSHResult(process.returncode, stdout.text(), stderr.text(), stdout.truncated or stderr.truncated, timed_out)


def _run_local(command, environ, timeout):
  return _run_ssh(command, environ, timeout, stdout_limit=MAX_LOCAL_COMMAND_BYTES, stderr_limit=1024)


def _wifi_values(text, prefix=""):
  """Rebuild numeric fields only; never copy raw iw lines or peer identifiers."""
  patterns = [
    ("freq_mhz", r"freq:\s*(\d+(?:\.\d+)?)\s*$"),
    ("signal_dbm", r"signal:\s*(-?\d+)\s*(?:\[[^]]*\]\s*)?dBm\b"),
    ("signal_avg_dbm", r"signal avg:\s*(-?\d+)\s*(?:\[[^]]*\]\s*)?dBm\b"),
    ("rx_bitrate_mbps", r"rx bitrate:\s*(\d+(?:\.\d+)?)\s*MBit/s\b"),
    ("tx_bitrate_mbps", r"tx bitrate:\s*(\d+(?:\.\d+)?)\s*MBit/s\b"),
    ("expected_throughput_mbps", r"expected throughput:\s*(\d+(?:\.\d+)?)\s*Mbps\b"),
    ("inactive_ms", r"inactive time:\s*(\d+)\s*ms\b"),
  ]
  patterns += [(name.replace(" ", "_"), re.escape(name) + r":\s*(\d+)\s*$") for name in (
    "rx bytes", "rx packets", "tx bytes", "tx packets", "tx retries", "tx failed", "beacon loss", "beacon rx", "rx drop misc",
  )]
  values = {}
  for line in text.splitlines():
    for key, pattern in patterns:
      match = re.match(r"\s*" + pattern, line)
      if match:
        # Restrict numeric lengths as well as total output, even with bad drivers.
        value = match.group(1)
        if len(value) <= 24:
          values[prefix + key] = value
        break
  return [key + "=" + value for key, value in values.items()]


def collect_c4_wifi(host, environ, deadline, runner=None, sysfs_root=Path("/sys/class/net")):
  """Take a bounded local snapshot without SSID, MAC, IP or command stderr."""
  if sys.platform != "linux":
    return "status=unsupported_platform"
  runner = runner or _run_local
  local_deadline = min(deadline, time.monotonic() + LOCAL_WIFI_TIMEOUT)
  local_env = {"LC_ALL": "C", "PATH": environ.get("PATH", os.defpath)}
  rows = ["scope=collection_time; station peers are anonymous and may not be the Pi"]

  def run(command):
    remaining = local_deadline - time.monotonic()
    if remaining <= 0:
      rows.append("command_status=local_deadline")
      return None
    try:
      result = runner(command, local_env, min(0.4, remaining))
    except (OSError, ValueError, subprocess.SubprocessError):
      rows.append("command_status=unavailable")
      return None
    if result.timed_out:
      rows.append("command_status=timeout")
    elif result.returncode != 0:
      rows.append("command_status=unavailable")
    else:
      return result.stdout[:MAX_LOCAL_COMMAND_BYTES]
    return None

  def finish():
    return "\n".join(rows).encode("utf-8")[:MAX_LOCAL_WIFI_BYTES].decode("utf-8", errors="ignore")

  ip = shutil.which("ip", path=local_env["PATH"])
  if ip is None:
    rows.append("status=route_tool_unavailable")
    return finish()
  route_text = run([ip, "-j", "route", "get", _ipv4(host)])
  try:
    routes = json.loads(route_text) if route_text else []
    interface = routes[0]["dev"]
    if not isinstance(interface, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}", interface) is None:
      raise ValueError("invalid route interface")
  except (ValueError, KeyError, IndexError, TypeError):
    rows.append("status=route_interface_unavailable")
    return finish()
  rows.append("route_interface=" + interface)
  iw = shutil.which("iw", path=local_env["PATH"])
  if iw is None:
    rows.append("iw_status=unavailable")
  else:
    power = run([iw, "dev", interface, "get", "power_save"])
    if power:
      match = re.fullmatch(r"\s*Power save:\s*(on|off)\s*", power)
      if match:
        rows.append("power_save=" + match.group(1))
    link = run([iw, "dev", interface, "link"])
    if link:
      rows += _wifi_values(link)
    info = run([iw, "dev", interface, "info"])
    if info:
      for line in info.splitlines():
        match = re.fullmatch(r"\s*type (managed|AP|P2P-client|P2P-GO|monitor|mesh point)\s*", line)
        if match:
          rows.append("interface_type=" + match.group(1))
        match = re.match(r"\s*channel \d+ \((\d{3,5}) MHz\)", line)
        if match:
          rows.append("channel_freq_mhz=" + match.group(1))
    stations = run([iw, "dev", interface, "station", "dump"])
    if stations:
      peers = re.split(r"(?m)^Station [^\n]*\n", stations)[1:]
      rows.append("anonymous_stations_observed=" + str(len(peers)))
      for index, peer in enumerate(peers[:2], 1):
        rows += _wifi_values(peer, prefix=f"station_{index}_")
      if len(peers) > 2:
        rows.append("additional_stations_omitted=1")
  # Fixed counter paths and validated interface names prevent path traversal.
  for counter in ("rx_bytes", "rx_packets", "rx_errors", "rx_dropped", "tx_bytes", "tx_packets", "tx_errors", "tx_dropped"):
    if time.monotonic() >= local_deadline:
      rows.append("counter_status=local_deadline")
      break
    try:
      with (Path(sysfs_root) / interface / "statistics" / counter).open("r", encoding="ascii") as file:
        value = file.read(32).strip()
      if re.fullmatch(r"[0-9]{1,24}", value):
        rows.append("interface_" + counter + "=" + value)
    except (OSError, UnicodeError):
      pass
  return finish()


def _write_report(output, content):
  output = Path(output)
  temporary = None
  try:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=output.parent,
                                     prefix=f".{output.name}.", delete=False) as file:
      temporary = Path(file.name)
      file.write(content)
    os.replace(temporary, output)
  finally:
    if temporary is not None:
      temporary.unlink(missing_ok=True)


def collect_c4_crashes():
  rows = []
  for name in ("cluster_crash.log", "cluster_crash.log.1"):
    try:
      with (C4_CRASH_LOG_DIRECTORY / name).open("rb") as file:
        size = os.fstat(file.fileno()).st_size
        if size > MAX_C4_CRASH_BYTES:
          head = file.read(512)
          file.seek(size - (MAX_C4_CRASH_BYTES - 512))
          data = head + b"\n[Crash history truncated; latest tail follows]\n" + file.read(MAX_C4_CRASH_BYTES - 512)
        else:
          data = file.read(MAX_C4_CRASH_BYTES)
    except OSError:
      continue
    rows += [f"--- {name} ---", data.decode("utf-8", errors="replace")]
  return "\n".join(rows) if rows else "No retained C4 cluster crash log is available."


def collect_pi_log(c4_log, output, environ=None, runner=None):
  """Always write a sidecar, including connection/permission failure details."""
  environ = dict(os.environ if environ is None else environ)
  runner = runner or _run_ssh
  started = time.monotonic()
  deadline = started + COLLECTION_TIMEOUT
  report = ["=== Orange Pi cluster diagnostics ===",
            "C4 collection UTC: " + datetime.now(UTC).isoformat(),
            "C4 monotonic uptime: " + f"{started:.3f}s",
            "C4 log: " + Path(c4_log).name,
            "\n--- Retained C4 cluster failures (bounded history, may predate this session) ---", collect_c4_crashes()]
  password = environ.get("CLUSTER_PI_PASSWORD", "orangepi")
  def save_progress():
    content = "\n".join(report + ["Collection status: collecting",
                                     "Failure: collection did not complete if this status remains."]) + "\n"
    if password:
      content = content.replace(password, "<redacted>")
    _write_report(output, content)

  # Atomic progress survives UI/process termination; final output replaces it.
  save_progress()
  result = None
  success = False
  skipped = False
  failure = None
  try:
    host, source = resolve_host(c4_log, environ, deadline=deadline)
    report += ["Pi address: " + host, "Address source: " + source]
    report += ["\n--- C4 Wi-Fi snapshot (no SSID/profile/MAC) ---", collect_c4_wifi(host, environ, deadline)]
    save_progress()
    user = environ.get("CLUSTER_PI_USER", "root")
    if re.fullmatch(r"[a-z_][a-z0-9_-]*\$?", user) is None:
      raise ValueError("Invalid CLUSTER_PI_USER")
    ssh = shutil.which("ssh")
    if ssh is None:
      raise OSError("OpenSSH client is unavailable on C4")
    identity = environ.get("CLUSTER_PI_IDENTITY")
    if identity and not Path(identity).is_file():
      raise ValueError("CLUSTER_PI_IDENTITY is not an existing key file")
    with tempfile.TemporaryDirectory(prefix="cluster-pi-log.") as directory:
      askpass = Path(directory) / "askpass"
      askpass.write_text(ASKPASS_SCRIPT, encoding="utf-8", newline="\n")
      askpass.chmod(0o700)
      ssh_env = {**environ, "CLUSTER_PI_PASSWORD": password, "SSH_ASKPASS": str(askpass),
                 "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": environ.get("DISPLAY") or ":0", "LC_ALL": "C"}
      for port in (9122, 22):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise TimeoutError("Orange Pi diagnostics exceeded the 25 second collection deadline")
        command = [ssh, "-T", "-p", str(port), "-l", user,
                   "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8",
                   "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=1",
                   "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1", "-o", "LogLevel=ERROR"]
        if identity:
          command += ["-i", str(Path(identity).resolve())]
        command += [host, REMOTE_COMMAND]
        result = runner(command, ssh_env, remaining)
        report.append(f"SSH attempt: port={port} exit={result.returncode}")
        if port == 9122 and result.returncode == 255 and not result.timed_out and TRANSPORT_ERRORS.search(result.stderr):
          report.append("Port 9122 unavailable; retrying port 22")
          continue
        success = result.returncode == 0 and not result.timed_out
        if not success:
          failure = ("SSH/remote command exceeded collection deadline" if result.timed_out
                     else f"SSH/remote diagnostics failed (exit {result.returncode})")
        break
  except USBOnlyLog as error:
    skipped = success = True
    report.append("Skip reason: " + str(error))
  except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
    failure = str(error)
  status = "skipped" if skipped else "ok" if success else "failed"
  report += [f"Collection status: {status}",
             f"Collection elapsed: {time.monotonic() - started:.2f}s"]
  if failure:
    report.append("Failure: " + failure)
  if result is not None:
    if result.stderr:
      report += ["\n--- SSH/remote diagnostics stderr ---", result.stderr]
    if result.stdout:
      report += ["\n--- Orange Pi diagnostics ---", result.stdout]
    if result.truncated:
      report.append("[OUTPUT TRUNCATED: collection output exceeded its byte limit]")
  content = "\n".join(report) + "\n"
  if password:
    content = content.replace(password, "<redacted>")
  encoded = content.encode("utf-8")
  if len(encoded) > MAX_OUTPUT_BYTES:
    marker = b"\n[OUTPUT TRUNCATED: report exceeded its byte limit]\n"
    content = encoded[:MAX_OUTPUT_BYTES - len(marker)].decode("utf-8", errors="ignore") + marker.decode()
  _write_report(output, content)
  return success


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--c4-log", type=Path, required=True)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()
  return 0 if collect_pi_log(args.c4_log, args.output) else 1


if __name__ == "__main__":
  raise SystemExit(main())
