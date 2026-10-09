"""Collect bounded, read-only Orange Pi diagnostics for a C4 cluster upload."""
import argparse
from datetime import UTC, datetime
import ipaddress
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
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
MAX_C4_LOG_BYTES = 32 * 1024 * 1024
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
printf '\n--- Receiver service state ---\n'
systemctl show cluster-hdmi.service --property=ActiveState,SubState,ExecMainStartTimestamp,ExecMainStatus
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
fi
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


def _run_ssh(command, environ, timeout):
  process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=environ, start_new_session=True)
  stdout, stderr = _Capture(MAX_OUTPUT_BYTES), _Capture(MAX_STDERR_BYTES)
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


def collect_pi_log(c4_log, output, environ=None, runner=None):
  """Always write a sidecar, including connection/permission failure details."""
  environ = dict(os.environ if environ is None else environ)
  runner = runner or _run_ssh
  started = time.monotonic()
  deadline = started + COLLECTION_TIMEOUT
  report = ["=== Orange Pi cluster diagnostics ===",
            "C4 collection UTC: " + datetime.now(UTC).isoformat(),
            "C4 monotonic uptime: " + f"{started:.3f}s",
            "C4 log: " + Path(c4_log).name]
  password = environ.get("CLUSTER_PI_PASSWORD", "orangepi")
  result = None
  success = False
  skipped = False
  failure = None
  try:
    host, source = resolve_host(c4_log, environ, deadline=deadline)
    report += ["Pi address: " + host, "Address source: " + source]
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
