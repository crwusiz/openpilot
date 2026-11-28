#!/usr/bin/env python3
import asyncio
import codecs
import errno
import html as html_lib
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from openpilot.selfdrive.addon.dependencies import activate_groups

activate_groups(("dashboard",))

import requests
from ansi2html import Ansi2HTMLConverter
from fastapi import Request
from fastapi.responses import JSONResponse
from nicegui import app, ui

from openpilot.selfdrive.addon.cluster.hdmi_display.maintenance import ACTION_PATH, ClusterActions
from openpilot.selfdrive.addon.cluster.hdmi_display.network_status import get_connected_pi_ip
from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.hdmi_profiles import (
  DEFAULT_HDMI_PROFILE, HDMI_PROFILES, normalize_hdmi_profile,
)
from openpilot.selfdrive.addon.dashboard_driving_log import read_driving_log
from openpilot.selfdrive.addon.dashboard_drives import render_tab_drives
from openpilot.selfdrive.addon.dashboard_mount import MountAdjustment
from openpilot.selfdrive.addon.dashboard_mount_stream import MountSensorStream, get_mount_sensor_stream
from openpilot.selfdrive.addon.drive_video import DriveVideoLibrary, create_drive_video_router

# ── 환경 설정 및 유틸리티 ───────────────────────────────────────
SCRIPTS_PATH = "/data/openpilot/scripts"
BASE_PATH = "/data/params/crwusiz"
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 7000
CAMERA_OPTIONS = {"ROAD": "road", "DRIVER": "driver", "WIDE": "wideRoad"}
CAMERA_STREAM_PATH = '/api/camera/stream'
WEBRTCD_URL = 'http://127.0.0.1:5001'
CAMERA_START_TIMEOUT = 10.0
MAX_CAMERA_SDP = 128 * 1024
CAMERA_STREAM_LOCK = asyncio.Lock()
MAX_COMMAND_OUTPUT = 100_000
LOG_DIR = Path('/data/log')
TMUX_LOG_PATH = LOG_DIR / 'tmux_console.log'
DRIVING_DEBUG_LOG = 'Driving Debug'
REALDATA_PATH = Path('/data/media/0/realdata')
DRIVE_VIDEOS = DriveVideoLibrary(REALDATA_PATH, Path('/data/dashboard/cache/drive-video'))
app.include_router(create_drive_video_router(DRIVE_VIDEOS))
# The angled comma four product image from https://www.comma.ai/media.
DEVICE_IMAGE_URL = app.add_static_file(
  local_file=Path(__file__).resolve().parents[1] / 'assets' / 'images' / 'comma-four-angled.webp',
  url_path='/assets/comma-four-angled.webp',
)
LOG_FILES = {
  "CAN Missing": LOG_DIR / 'can_missing.log',
  "CAN Timeout": LOG_DIR / 'can_timeout.log',
  "Tmux Error": LOG_DIR / 'tmux_error.log',
  "Tmux Console": TMUX_LOG_PATH,
  "Cluster Debug": LOG_DIR / 'cluster_debug.log',
}
CLUSTER_ACTIONS = ClusterActions()


@app.get(ACTION_PATH)
async def cluster_action_status(request: Request):
  client_address = request.client
  peer = client_address.host if client_address is not None else None
  try:
    return JSONResponse(CLUSTER_ACTIONS.status(peer, get_connected_pi_ip()))
  except PermissionError:
    return JSONResponse({'error': 'connected_pi_required'}, status_code=403)


@app.post(ACTION_PATH)
async def cluster_action_submit(request: Request):
  client_address = request.client
  peer = client_address.host if client_address is not None else None
  try:
    body = bytearray()
    async for chunk in request.stream():
      body.extend(chunk)
      if len(body) > 1024:
        return JSONResponse({'error': 'request_too_large'}, status_code=413)
    payload = json.loads(body)
    if not isinstance(payload, dict) or set(payload) != {'action', 'request_id'}:
      raise ValueError('Invalid action request')
    result = CLUSTER_ACTIONS.submit(payload['action'], peer, payload['request_id'], get_connected_pi_ip())
    return JSONResponse(result, status_code=202)
  except PermissionError:
    return JSONResponse({'error': 'connected_pi_required'}, status_code=403)
  except (ValueError, TypeError, UnicodeError):
    return JSONResponse({'error': 'invalid_action'}, status_code=400)
  except RuntimeError:
    return JSONResponse({'error': 'busy'}, status_code=409)


def _request_camera_stream(cameras: list[str], sdp: str) -> tuple[int, dict]:
  """Signal through loopback; manager owns the offroad camera processes."""
  if params.get_bool('IsOffroad'):
    params.put_bool('IsLiveStreaming', True)
  deadline = time.monotonic() + CAMERA_START_TIMEOUT
  # Loopback signaling must not be sent through an HTTP_PROXY from the environment.
  with requests.Session() as session:
    session.trust_env = False
    while True:
      try:
        if session.get(f'{WEBRTCD_URL}/schema', timeout=1).ok:
          break
      except (requests.ConnectionError, requests.Timeout):
        pass
      if time.monotonic() >= deadline:
        return 503, {'error': 'Camera streaming service did not start. Try again after checking device status.'}
      time.sleep(0.1)
    try:
      response = session.post(f'{WEBRTCD_URL}/stream', json={
        'sdp': sdp, 'cameras': cameras, 'enabled': True,
        'bridge_services_in': [], 'bridge_services_out': [],
      }, timeout=(2, 35))  # webrtcd bounds answer creation at 30 seconds.
      if not response.ok:
        return 502, {'error': f'Camera stream negotiation failed (service HTTP {response.status_code}).'}
      answer = response.json()
      if not isinstance(answer, dict) or answer.get('type') != 'answer' or not isinstance(answer.get('sdp'), str) or not answer['sdp']:
        return 502, {'error': 'Camera streaming service returned an invalid answer.'}
      return 200, {'sdp': answer['sdp'], 'type': 'answer'}
    except requests.Timeout:
      return 504, {'error': 'Camera stream negotiation timed out.'}
    except (requests.ConnectionError, ValueError):
      return 502, {'error': 'Camera streaming service could not complete negotiation.'}


@app.post(CAMERA_STREAM_PATH)
async def camera_stream(request: Request):
  """Same-origin video-only proxy; never expose webrtcd's control bridge."""
  raw = await request.body()
  if len(raw) > MAX_CAMERA_SDP + 1024:
    return JSONResponse({'error': 'Camera request is too large.'}, status_code=413)
  try:
    body = json.loads(raw)
  except (ValueError, UnicodeDecodeError):
    return JSONResponse({'error': 'Invalid camera request.'}, status_code=400)
  cameras = body.get('cameras') if isinstance(body, dict) else None
  if (not isinstance(cameras, list) or not 1 <= len(cameras) <= len(CAMERA_OPTIONS)
      or any(camera not in CAMERA_OPTIONS.values() for camera in cameras)
      or len(set(cameras)) != len(cameras)
      or not isinstance(body.get('sdp'), str) or not body['sdp'].startswith('v=0')
      or len(body['sdp']) > MAX_CAMERA_SDP):
    return JSONResponse({'error': 'Provide valid cameras and a video offer.'}, status_code=400)
  # Serialize restarts so an earlier slow startup cannot replace the new session.
  async with CAMERA_STREAM_LOCK:
    status, answer = await asyncio.to_thread(_request_camera_stream, cameras, body['sdp'])
  return JSONResponse(answer, status_code=status)

def _dashboard_port_available() -> bool:
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
      sock.bind((DASHBOARD_HOST, DASHBOARD_PORT))
    except OSError as bind_error:
      if bind_error.errno != errno.EADDRINUSE:
        raise
      return False
  return True

def _prepare_dashboard_port():
  """Reclaim a stale Linux listener before starting NiceGUI."""
  if _dashboard_port_available():
    return

  print(f"[dashboard] Port {DASHBOARD_PORT} is already in use; stopping its listener.", flush=True)
  # Resolve listening socket inodes through /proc without requiring fuser/lsof.
  inodes = set()
  for table in (Path('/proc/net/tcp'), Path('/proc/net/tcp6')):
    if not table.exists():
      continue
    for line in table.read_text().splitlines()[1:]:
      fields = line.split()
      if fields[3] == '0A' and int(fields[1].rsplit(':', 1)[1], 16) == DASHBOARD_PORT:
        inodes.add(f'socket:[{fields[9]}]')

  if inodes:
    for process in Path('/proc').iterdir():
      if not process.name.isdigit() or int(process.name) == os.getpid():
        continue
      try:
        for fd in (process / 'fd').iterdir():
          try:
            owns_port = os.readlink(fd) in inodes
          except OSError:
            continue
          if owns_port:
            print(f"[dashboard] Killing PID {process.name} on port {DASHBOARD_PORT}.", flush=True)
            os.kill(int(process.name), signal.SIGKILL)
            break
      except (FileNotFoundError, ProcessLookupError, PermissionError):
        # Processes can exit during inspection; inaccessible listeners are
        # reported by the final bind check below.
        continue

  deadline = time.monotonic() + 3.0
  while not _dashboard_port_available():
    if time.monotonic() >= deadline:
      raise RuntimeError(f"[dashboard] Cannot release port {DASHBOARD_PORT}; check the listener's process permissions.")
    time.sleep(0.1)
  print(f"[dashboard] Port {DASHBOARD_PORT} released; starting dashboard.", flush=True)

class _ParamsBackend(Protocol):
  def get(self, key: str, /) -> object: ...
  def get_bool(self, key: str, /) -> bool: ...
  def put(self, key: str, value: str, /, block: bool = False) -> None: ...
  def put_bool(self, key: str, value: bool, /, block: bool = False) -> None: ...
  def remove(self, key: str, /) -> None: ...


class MockParams(_ParamsBackend):
  """Synchronous in-memory settings for PCs without openpilot's native Params."""

  def __init__(self):
    self.data: dict[str, object] = {}

  def get(self, key: str) -> object:
    return self.data.get(key)

  def get_bool(self, key: str) -> bool:
    return bool(self.data.get(key, False))

  def put(self, key: str, value: str, block: bool = False) -> None:
    _ = block  # Memory writes complete immediately.
    self.data[key] = value

  def put_bool(self, key: str, value: bool, block: bool = False) -> None:
    _ = block
    self.data[key] = value

  def remove(self, key: str) -> None:
    self.data.pop(key, None)


def _create_params() -> _ParamsBackend:
  try:
    from openpilot.common.params import Params
    return Params()
  except ImportError:
    return MockParams()


params: _ParamsBackend = _create_params()

def get_list_from_file(path: str) -> list[str]:
  try:
    return [line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
  except FileNotFoundError:
    return []


def get_param_text(key: str, default: str = "") -> str:
  value = params.get(key)
  if isinstance(value, bytes):
    value = value.decode('utf-8', errors='replace')
  return str(value) if value else default


def _parse_commit_compare(output: str) -> tuple[str, str, str] | None:
  match = re.fullmatch(r'"([0-9a-f]{7,40})"\s+(==|!=)\s+"([0-9a-f]{7,40})"', output.strip())
  if match is None:
    return None
  local_commit, operator, remote_commit = match.groups()
  if (local_commit == remote_commit) != (operator == "=="):
    return None
  return local_commit, operator, remote_commit


def get_orange_pi_status_text() -> str:
  if not params.get_bool("ClusterEnable") or get_param_text("ClusterDisplayTransport", "usb") != "network":
    return "Orange Pi: Cluster를 켜고 Network 모드를 선택하면 IP를 확인할 수 있습니다."
  ip = get_connected_pi_ip()
  return f"Orange Pi 연결됨 · IP: {ip}" if ip else "Orange Pi 연결 대기 중"


def _script_command(path: str, args: list[str] | None = None) -> list[str]:
  interpreter = "bash" if Path(path).suffix == '.sh' else sys.executable
  return [interpreter, path, *(args or [])]


def _subprocess_env() -> dict[str, str]:
  env = os.environ.copy()
  env.pop('TMUX', None)
  env.pop('TMUX_PANE', None)
  return env


async def _run_command(command: list[str], on_output: Callable[[str], None] | None = None) -> tuple[int, str]:
  """Read both output streams without blocking the UI or sharing temporary files."""
  process = await asyncio.create_subprocess_exec(
    *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    start_new_session=True, env=_subprocess_env(),
  )
  output = ""
  decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
  try:
    stdout = process.stdout
    if stdout is None:
      raise RuntimeError('Command output pipe was not created')
    while chunk := await stdout.read(4096):
      output = (output + decoder.decode(chunk))[-MAX_COMMAND_OUTPUT:]
      if on_output is not None:
        on_output(output)
    output = (output + decoder.decode(b'', final=True))[-MAX_COMMAND_OUTPUT:]
    return await process.wait(), output
  finally:
    if process.returncode is None:
      try:
        process.terminate()
      except ProcessLookupError:
        pass
      try:
        await asyncio.wait_for(process.wait(), timeout=3)
      except TimeoutError:
        try:
          process.kill()
        except ProcessLookupError:
          pass
        await process.wait()


def _get_error_summary(output: str) -> str:
  """Prefer command errors over progress meters and wrapper messages."""
  lines = [line.strip() for line in output.replace('\r', '\n').splitlines() if line.strip()]
  if not lines:
    return "Unknown error"
  command_errors = [line for line in lines if line.lower().startswith(('curl:', 'fatal:', 'error:', 'ssh:'))]
  return (command_errors[-1] if command_errors else lines[-1])[:300]


def _get_log_upload_warning(path: str, output: str) -> str | None:
  if Path(path).name != 'log_upload.sh':
    return None
  prefixes = ('CLUSTER_PI_LOG_WARNING:', 'CLUSTER_LOG_WARNING:')
  warnings = [line.strip().split(':', 1)[1].strip() for line in output.splitlines() if line.strip().startswith(prefixes)]
  return '; '.join(dict.fromkeys(warnings))[:600] if warnings else None


async def run_script_async(name: str, path: str, args: list[str] | None = None, show_modal: bool = False,
                           on_error: Callable[[str], None] | None = None) -> int:
  command = _script_command(path, args)
  if not show_modal:
    ui.notify(f"[{name}] 진행 중...", type='info', position='top')
    try:
      return_code, output = await _run_command(command)
      if return_code == 0:
        warning = _get_log_upload_warning(path, output)
        if warning:
          ui.notify(f"[{name}] {warning}", type='warning', position='top')
        else:
          ui.notify(f"[{name}] 완료", type='positive', position='top')
      else:
        error = _get_error_summary(output)
        if on_error is not None:
          on_error(error)
        ui.notify(f"[{name}] 에러: {error}", type='negative', position='top')
      return return_code
    except Exception as script_error:
      if on_error is not None:
        on_error(str(script_error) or type(script_error).__name__)
      ui.notify(f"[{name}] 실행 실패: {script_error}", type='negative', position='top')
      return 1

  with ui.dialog().classes('backdrop-blur-sm') as dialog, ui.card().classes(
    'w-[95vw] max-w-4xl bg-[#0D1117] border border-[#3A4A6B] p-0 shadow-2xl'
  ):
    with ui.row().classes('w-full px-4 py-3 border-b border-[#3A4A6B] bg-[#1A2235] justify-between items-center'):
      ui.label(f'실행 중: {name}').classes('text-white font-bold text-[1.1rem]')
      close_btn = ui.button(icon='close', on_click=dialog.close).props('flat round dense color=white').classes('hidden')
    log_container = ui.html().classes('w-full bg-[#0D1117]')

  converter = Ansi2HTMLConverter(inline=True, dark_bg=True)
  viewer_id = f'modalLogViewer-{dialog.id}'
  header = f"🚀 [{datetime.now().strftime('%H:%M:%S')}] {name} 작업을 시작합니다...\n\n"

  def render_output(text: str):
    nonlocal output
    output = text
    log_container.content = (
      f'<div class="log-viewer" id="{viewer_id}" style="border:none; box-shadow:none; height:60vh;">'
      + f'{converter.convert(header + output, full=False)}</div>'
    )
    _ = ui.run_javascript(f'var v=document.getElementById("{viewer_id}");if(v)v.scrollTop=v.scrollHeight;')

  output = ""
  render_output(output)
  dialog.value = True
  try:
    return_code, output = await _run_command(command, render_output)
    if return_code == 0:
      warning = _get_log_upload_warning(path, output)
      if warning:
        output += f"\n⚠️ [{datetime.now().strftime('%H:%M:%S')}] {warning}\n"
      else:
        # gitpull.sh starts the update in tmux and owns the restart sequence.
        message = "tmux에서 업데이트를 시작했습니다." if Path(path).name == "gitpull.sh" else "성공적으로 완료되었습니다."
        output += f"\n✅ [{datetime.now().strftime('%H:%M:%S')}] {message}\n"
    else:
      if on_error is not None:
        on_error(_get_error_summary(output))
      output += f"\n❌ 오류가 발생하여 중단되었습니다. (Exit Code: {return_code})\n"
    return return_code
  except Exception as modal_error:
    if on_error is not None:
      on_error(str(modal_error) or type(modal_error).__name__)
    output += f"\n❌ 실행 실패: {modal_error}\n"
    return 1
  finally:
    close_btn.classes(remove='hidden')
    render_output(output + "\n[ 우측 상단의 'X' 버튼을 누르거나 창 바깥을 클릭하여 닫아주세요. ]")

def reset_calibration():
  for p in ["CalibrationParams", "LiveTorqueParameters", "LiveParametersV2", "LiveDelay"]:
    params.remove(p)
  params.put_bool("OnroadCycleRequested", True)
  ui.notify("캘리브레이션 초기화 요청 완료!", type='positive', position='top')


def _read_device_position() -> tuple[float, float] | None:
  raw = params.get('CalibrationParams')
  if not isinstance(raw, bytes) or not raw:
    return None

  try:
    from capnp import KjException
    from openpilot.cereal import log
  except ImportError:
    return None

  try:
    with log.Event.from_bytes(raw) as event:
      if event.which() != 'extrinsicsCalibration':
        return None
      calibration = event.extrinsicsCalibration
      if calibration.calStatus != log.ExtrinsicsCalibration.Status.calibrated or len(calibration.rpyCalib) != 3:
        return None
      roll, pitch, yaw = calibration.rpyCalib
  except (KjException, ValueError):
    return None

  if not all(math.isfinite(value) for value in (roll, pitch, yaw)):
    return None
  return round(math.degrees(pitch), 1), round(math.degrees(yaw), 1)


async def get_tmux_capture(lines: int = 100) -> str:
  resize = ["tmux", "resize-window", "-t", "0", "-x", "250", "-y", "100"]
  capture = ["tmux", "capture-pane", "-pe", "-t", "0", "-S", f"-{lines}"]
  for command in (resize, capture):
    return_code, output = await asyncio.wait_for(_run_command(command), timeout=5)
    if return_code:
      raise RuntimeError(_get_error_summary(output))
  return output


async def save_tmux_log() -> Path:
  content = await get_tmux_capture(lines=500)
  await asyncio.to_thread(TMUX_LOG_PATH.write_text, content, encoding='utf-8')
  return TMUX_LOG_PATH


@dataclass
class Route:
  name: str
  paths: list[Path] = field(default_factory=list)
  modified: float = 0.0


def get_routes(root: Path) -> list[Route]:
  routes: dict[str, Route] = {}
  try:
    entries = list(root.iterdir())
  except FileNotFoundError:
    return []
  for entry in entries:
    route_name, _, segment = entry.name.rpartition('--')
    if '--' not in route_name or not segment.isdecimal():
      continue
    try:
      if not entry.is_dir():
        continue
      modified = entry.stat().st_mtime
    except FileNotFoundError:
      continue  # loggerd/deleter may remove a segment while the page is loading.
    route = routes.setdefault(route_name, Route(route_name))
    route.paths.append(entry)
    route.modified = max(route.modified, modified)
  for route in routes.values():
    route.paths.sort(key=lambda path: int(path.name.rsplit('--', 1)[1]))
  return sorted(routes.values(), key=lambda candidate: candidate.modified, reverse=True)


# ── 전역 CSS 스타일 정의 (모바일 텍스트 랩핑/비율 최적화) ───────
def apply_styles():
  ui.add_head_html("""
    <style>
    body { background-color: #0B0E14; color: #E8EEFF; }

    /* ── 커스텀 그라데이션 버튼 ── */
    button.custom-btn {
        border-radius: 50px !important;
        height: auto !important; min-height: 56px !important;
        padding: 6px 16px 6px 60px !important;
        text-align: left !important;
        font-weight: 900 !important;
        font-size: 0.88em !important;
        letter-spacing: 0.05em !important;
        text-transform: uppercase !important;
        color: #E8EEFF !important;
        box-shadow: 0 5px 22px rgba(0,0,0,0.45), 0 1px 4px rgba(0,0,0,0.3) !important;
        transition: all 0.22s ease !important;
        justify-content: flex-start !important;
        position: relative !important;
        overflow: hidden !important;
        border: none !important;
    }
    button.custom-btn .q-btn__content {
        justify-content: flex-start !important;
        width: 100%; white-space: normal !important; line-height: 1.2 !important;
    }
    button.custom-btn:hover {
        transform: translateY(-2px) !important; filter: brightness(1.18) !important; box-shadow: 0 8px 30px rgba(0,0,0,0.5) !important;
    }
    button.custom-btn:active {
        transform: translateY(0) !important; filter: brightness(0.95) !important;
    }

    button.custom-btn::before {
        content: '' !important; position: absolute !important; left: 5px !important; top: 50% !important; transform: translateY(-50%) !important;
        width: 46px !important; height: 46px !important; background: rgba(255,255,255,0.18) !important; border-radius: 50% !important;
        border: 2px solid rgba(255,255,255,0.35) !important; box-shadow: 0 2px 8px rgba(0,0,0,0.25) !important;
        font-size: 1.4em !important; line-height: 42px !important; text-align: center !important;
        display: block !important; pointer-events: none !important; z-index: 2 !important;
    }
    button.custom-btn .q-focus-helper { display: none !important; }

    button.btn-blue { background: linear-gradient(90deg, #1E3A8A 0%, #3B82F6 100%) !important; box-shadow: 0 5px 22px rgba(59,130,246,0.5) !important; }
    button.btn-blue::before { content: '🔍' !important; }

    button.btn-blue-pull { background: linear-gradient(90deg, #1E3A8A 0%, #3B82F6 100%) !important; box-shadow: 0 5px 22px rgba(59,130,246,0.5) !important; }
    button.btn-blue-pull::before { content: '⬇' !important; }

    button.btn-yellow, button.btn-restart {
        background: linear-gradient(90deg, #78350F 0%, #F59E0B 100%) !important;
        box-shadow: 0 5px 22px rgba(245,158,11,0.45) !important;
    }
    button.btn-yellow::before { content: '✦' !important; }
    button.btn-restart::before { content: '↻' !important; }
    button.btn-red { background: linear-gradient(90deg, #7F1D1D 0%, #EF4444 100%) !important; box-shadow: 0 5px 22px rgba(239,68,68,0.5) !important;}
    button.btn-red::before { content: '⏻' !important; }
    button.btn-green, button.btn-green-route, button.btn-green-start {
        background: linear-gradient(90deg, #065F46 0%, #10B981 100%) !important;
        box-shadow: 0 5px 22px rgba(16,185,129,0.45) !important;
    }
    button.btn-green::before { content: '⬆' !important; }
    button.btn-green-route::before { content: '🚀' !important; }
    button.btn-green-start::before { content: '▶' !important; }
    button.btn-red-stop { background: linear-gradient(90deg, #7F1D1D 0%, #EF4444 100%) !important; box-shadow: 0 5px 22px rgba(239,68,68,0.5) !important;}
    button.btn-red-stop::before { content: '⏹' !important; }
    button.btn-default { background: linear-gradient(90deg, #2A3348 0%, #3A4A6B 100%) !important; }
    button.btn-default::before { content: '👁' !important; }
    button.btn-camera { background: linear-gradient(90deg, #1E3A8A 0%, #3B82F6 100%) !important; box-shadow: 0 5px 22px rgba(59,130,246,0.5) !important; }
    button.btn-camera::before { content: '📷' !important; }

    .camera-grid {
        display: grid; grid-template-columns: minmax(0, 62fr) minmax(0, 38fr);
        grid-template-rows: repeat(2, minmax(0, 1fr)); gap: 12px;
        width: 100%; height: clamp(360px, 60vh, 640px);
    }
    .camera-card {
        display: flex; flex-direction: column; min-width: 0; min-height: 0;
        padding: 12px; background: #111827; border: 1.5px solid #3A4A6B; border-radius: 12px;
    }
    .camera-card-road { grid-row: span 2; }
    .camera-card-title { color: #E8EEFF; font-weight: 700; margin-bottom: 8px; }
    .camera-card-index { color: #60A5FA; margin-right: 12px; }
    .camera-video-wrap { position: relative; flex: 1; min-height: 0; background: #000; border-radius: 8px; overflow: hidden; }
    .camera-video-wrap video { display: block; width: 100%; height: 100%; object-fit: contain; }
    .camera-status {
        position: absolute; top: 8px; right: 8px; max-width: calc(100% - 16px);
        padding: 4px 8px; border-radius: 12px; color: #E8EEFF; background: rgba(0,0,0,0.65);
        font-size: 12px; pointer-events: none;
    }

    /* 새로고침 버튼 전용 스타일 */
    button.refresh-btn {
        min-height: 40px !important; padding: 0 12px !important;
        background: #232E45 !important; color: #60A5FA !important; border: 1px solid #3A4A6B !important;
    }

    /* ── 드롭다운(Selectbox) ── */
    .q-field__control {
        background: linear-gradient(90deg, #1A2235 0%, #232E45 100%) !important;
        border-radius: 50px !important; border: 1.5px solid #3A4A6B !important;
        box-shadow: 0 4px 16px rgba(0,0,0,0.4) !important; padding: 0 20px !important;
        min-height: 56px !important; height: 56px !important;
    }
    .q-field__control:before { display: none !important; }
    .q-field__native { color: #E8EEFF !important; font-weight: 600 !important; font-size: 0.95em !important; }
    .q-field__append i { color: #7B8EC8 !important; }

    /* ── 상태 카드(Pill Card) ── */
    .pill-card {
        display: flex; align-items: center; height: auto; min-height: 56px; border-radius: 50px;
        box-shadow: 0 5px 22px rgba(0,0,0,0.4); font-weight: 700; font-size: 0.82em;
        letter-spacing: 0.05em; text-transform: uppercase; padding: 6px 16px 6px 0;
        background: linear-gradient(90deg, #1A2235 0%, #232E45 100%);
        border: 1.5px solid #3A4A6B; width: 100%; overflow: hidden;
    }
    .pill-card-icon {
        display: flex; align-items: center; justify-content: center;
        min-width: 46px; height: 46px; margin: 0 10px 0 5px;
        background: rgba(255,255,255,0.08); border-radius: 50%; font-size: 1.25em;
        border: 1.5px solid rgba(255,255,255,0.15); flex-shrink: 0;
    }
    .pill-card-text {
        color: #7B8EC8; line-height: 1.2;
        flex: 1; min-width: 0; white-space: normal !important; overflow: visible !important;
    }
    .pill-card-value {
        font-size: 0.95em; font-weight: 600; color: #E8EEFF; margin-top: 2px;
        text-transform: none; white-space: normal !important; overflow: visible !important; overflow-wrap: anywhere;
    }

    .card-success { border-left: 4px solid #10B981; }
    .card-danger { border-left: 4px solid #EF4444; }
    .card-warning { border-left: 4px solid #D97706; }
    .card-info { border-left: 4px solid #3B82F6; }

    /* ── 탭 메뉴 커스텀 (모바일 두줄 메뉴명 최적화) ── */
    .tabs-custom .q-tab { padding: 0 10px !important; min-height: 54px !important; }
    .tabs-custom .q-tab__icon { font-size: 1.4em !important; margin-bottom: 2px !important; }
    .tabs-custom .q-tab__label { font-size: 0.7em !important; font-weight: 800 !important; letter-spacing: 0.05em; }

    /* ── 토글 스위치 ── */
    .custom-toggle { font-size: 1.2em; flex-shrink: 0; }
    .custom-toggle .q-toggle__inner {
        width: 70px !important; height: 32px !important; padding: 0 !important;
        border-radius: 16px !important; background: #E03535 !important;
        box-shadow: inset 0 2px 5px rgba(0,0,0,0.35) !important;
        transition: background 0.2s ease !important; position: relative !important;
    }
    .custom-toggle .q-toggle__track { display: none !important; }
    .custom-toggle .q-toggle__thumb {
        position: absolute !important; width: 24px !important; height: 24px !important;
        background: white !important; border-radius: 50% !important;
        top: 4px !important; left: 4px !important; transform: none !important;
        transition: left 0.2s ease !important; box-shadow: 0 2px 4px rgba(0,0,0,0.35) !important;
    }
    .custom-toggle .q-toggle__thumb .q-icon, .custom-toggle .q-toggle__thumb::after, .custom-toggle .q-focus-helper { display: none !important; }
    .custom-toggle .q-toggle__inner::before {
        content: 'OFF'; position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
        color: white; font-size: 11px; font-weight: 800; font-family: sans-serif; pointer-events: none; line-height: 1;
    }
    .custom-toggle[aria-checked="true"] .q-toggle__inner, .custom-toggle:has(input:checked) .q-toggle__inner { background: #10B981 !important; }
    .custom-toggle[aria-checked="true"] .q-toggle__inner::before, .custom-toggle:has(input:checked) .q-toggle__inner::before {
        content: 'ON'; left: 10px; right: auto;
    }
    .custom-toggle[aria-checked="true"] .q-toggle__thumb, .custom-toggle:has(input:checked) .q-toggle__thumb { left: 42px !important; }

    /* ── 로그 뷰어 ── */
    .log-viewer {
        background: #0D1117;
        border: 1.5px solid #3A4A6B;
        border-radius: 12px;
        padding: 16px 20px;

        /* 폰트 렌더링 최적화 */
        font-family: 'Roboto Mono', 'Consolas', 'Menlo', 'Courier New', monospace;
        font-size: 1.15em;
        font-weight: 500;
        line-height: 1.5;
        letter-spacing: 0.02em;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
        color: #E8EEFF;

        white-space: pre-wrap;
        word-break: keep-all;
        overflow-wrap: break-word;

        height: 60vh;
        min-height: 430px;
        overflow-y: auto;
        box-shadow: inset 0 2px 12px rgba(0,0,0,0.5);
        width: 100%;
    }

    .log-viewer span {
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }

    .log-statusbar {
        background: linear-gradient(90deg, #1A2235, #232E45);
        border: 1.5px solid #3A4A6B;
        border-left: 4px solid #3B82F6;
        border-radius: 12px;
        padding: 10px 16px;
        font-family: 'Roboto Mono', 'Consolas', 'Menlo', monospace;
        font-size: 1.15em;
        font-weight: 600;
        -webkit-font-smoothing: antialiased;
        color: #93C5FD;
        width: 100%;
        margin-top: 8px;
    }
    .log-error { border-left-color: #EF4444 !important; color: #FCA5A5 !important; }

    .device-position-panel {
        background: #111827; border: 1.5px solid #3A4A6B; border-radius: 12px;
        padding: 16px; width: 100%;
    }
    .device-position-grid {
        display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
        align-items: stretch; gap: 12px;
        --pitch-color: #7DC7FF; --yaw-color: #69E2B5;
    }
    .device-orientation-scene {
        position: relative; min-width: 0; height: clamp(205px, 42vh, 320px); isolation: isolate;
        overflow: hidden; border: 1px solid #354662; border-radius: 10px;
        background: radial-gradient(ellipse at 50% 44%, #243650 0%, #151F31 52%, #0B111B 100%);
    }
    .device-orientation-scene::before {
        content: ''; position: absolute; inset: 0; opacity: 0.35;
        background-image: linear-gradient(#344B68 1px, transparent 1px), linear-gradient(90deg, #344B68 1px, transparent 1px);
        background-size: 18px 18px; mask-image: radial-gradient(ellipse at center, black 5%, transparent 74%);
    }
    .device-camera-guide, .device-camera-posed {
        position: absolute; left: 50%; top: 54%; width: min(78%, 360px); height: 84%; object-fit: contain;
        transform: translate(-50%, -50%) perspective(320px) rotateX(var(--pitch, 0deg)) rotateY(var(--yaw, 0deg)) scale(var(--outline-scale, 1));
    }
    .device-camera-guide {
        --outline-scale: 1.03; filter: brightness(0) invert(85%); opacity: 0.45;
    }
    .device-camera-posed {
        filter: brightness(1.38) drop-shadow(0 6px 8px #0009);
    }
    .device-axis-line { position: absolute; opacity: 0.55; }
    .device-axis-line-yaw { left: 50%; top: 8%; bottom: 16%; border-left: 1px dashed var(--yaw-color); }
    .device-axis-line-pitch { left: 9%; right: 12%; top: 61%; border-top: 1px dashed var(--pitch-color); transform: rotate(-12deg); }
    .device-orbit { position: absolute; border: 2px solid currentColor; border-radius: 50%; z-index: 1; }
    .device-orbit-yaw {
        left: 20%; width: 60%; top: 18%; height: 22%; color: var(--yaw-color); border-top-color: transparent;
    }
    .device-orbit-pitch {
        left: 15%; width: 22%; top: 36%; height: 49%; color: var(--pitch-color); border-right-color: transparent; transform: rotate(-18deg);
    }
    .device-orbit[data-turn]::after {
        content: ''; position: absolute; width: 14px; height: 14px; background: currentColor;
        clip-path: polygon(0 0, 100% 50%, 0 100%);
    }
    .device-orbit[data-turn="left"]::after { left: 3%; bottom: 5%; transform: rotate(198deg); }
    .device-orbit[data-turn="right"]::after { right: 3%; bottom: 5%; transform: rotate(-18deg); }
    .device-orbit[data-turn="up"]::after { left: 7%; top: 12%; transform: rotate(-37deg); }
    .device-orbit[data-turn="down"]::after { left: 41%; bottom: -6%; transform: rotate(13deg); }
    .device-axis-tag { position: absolute; z-index: 2; font-size: 0.75rem; font-weight: 800; }
    .device-axis-tag-yaw { right: 12%; top: 10%; color: var(--yaw-color); }
    .device-axis-tag-pitch { left: 4%; top: 48%; color: var(--pitch-color); }
    .device-orientation-legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 6px 0 0; color: #C0CDE3; font-size: 0.75rem; }
    .device-orientation-legend i { display: inline-block; width: 14px; margin-right: 5px; vertical-align: middle; border-top: 2px solid #A6B4CB; }
    .device-orientation-legend .measured { border-color: #7DC7FF; }
    .device-position-readings { display: grid; gap: 7px; margin: 0; }
    .device-position-readings > div {
        background: #1A2235; border-left: 3px solid var(--axis-color); border-radius: 7px;
        padding: 7px 10px; min-width: 0;
    }
    .device-reading-pitch { --axis-color: var(--pitch-color); }
    .device-reading-yaw { --axis-color: var(--yaw-color); }
    .device-position-readings dt {
        display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; column-gap: 8px;
        color: var(--axis-color); font-size: 0.78rem; font-weight: 700;
    }
    .device-position-range { color: #C0CDE3; font-size: 0.75rem; font-weight: 600; }
    .device-position-readings dd { margin: 0; }
    .device-position-value { font-size: 1.08rem; font-weight: 800; color: #E8EEFF; font-variant-numeric: tabular-nums; }
    .device-position-adjustment { color: #D4DFF3; font-size: 0.82rem; }
    .device-position-adjustment span { color: var(--axis-color); font-weight: 800; }
    .device-position-status { color: #B2C0D8; font-size: 0.75rem; margin: 6px 0 0; }
    .device-position-aligned .device-position-status { color: var(--yaw-color); font-weight: 700; }
    .device-position-aligned .device-camera-posed { filter: brightness(1.38) drop-shadow(0 0 5px #69E2B5B0); }
    .device-position-panel button { min-height: 36px; }
    .device-mount-controls { border-top: 1px solid #354662; padding-top: 8px; }
    .device-mount-status { color: #C0CDE3; font-size: 0.82rem; }
    .device-saved-position {
        background: #1A2235; border-radius: 7px; color: #D4DFF3; padding: 6px 10px; font-size: 0.82rem;
    }

    @media (min-width: 769px) and (max-height: 540px) {
        .device-position-panel { padding: 10px; }
        .device-orientation-scene { height: clamp(100px, calc(100vh - 320px), 228px); }
    }
    @media (min-width: 769px) and (max-height: 335px) {
        .device-position-readings > div { padding: 3px 8px; }
        .device-position-readings div { line-height: 1.15; }
    }

    /* ── 모바일 환경 강제 최적화 미디어 쿼리 ── */
    @media (max-width: 768px) {
        .camera-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: minmax(0, 3fr) minmax(0, 2fr); }
        .camera-card-road { grid-column: span 2; grid-row: auto; }
        .camera-card { padding: 8px; }
        button.custom-btn { padding: 6px 8px 6px 44px !important; min-height: 48px !important; }
        button.custom-btn .q-btn__content {
            font-size: 0.8rem !important; white-space: normal !important; overflow: visible !important;
            line-height: 1.15 !important; text-overflow: clip !important;
        }
        button.custom-btn::before {
            width: 34px !important; height: 34px !important; font-size: 1.1em !important; line-height: 30px !important; left: 4px !important;
        }
        .camera-controls button.custom-btn { padding: 38px 8px 8px !important; }
        .camera-controls button.custom-btn::before { top: 4px !important; left: 50% !important; transform: translateX(-50%) !important; }
        .camera-controls button.custom-btn .q-btn__content { justify-content: center !important; text-align: center !important; font-size: 0.75rem !important; }

        .pill-card { min-height: 48px !important; padding-right: 12px !important; }
        .pill-card-icon {
            min-width: 34px !important; height: 34px !important; font-size: 1.1em !important; margin-left: 4px !important; margin-right: 8px !important;
        }
        .pill-card-text { font-size: 0.65rem !important; white-space: normal !important; overflow: visible !important; text-overflow: clip !important; }
        .pill-card-value { font-size: 0.85rem !important; white-space: normal !important; overflow: visible !important; text-overflow: clip !important;}

        .q-field__control { height: 48px !important; min-height: 48px !important; padding: 0 14px !important;}
        .q-field__native { font-size: 0.85em !important; }

        .tabs-custom .q-tab { flex: 0 0 auto !important; padding: 0 6px !important; min-width: 48px !important; min-height: 48px !important; }
        .tabs-custom .q-tab__icon { font-size: 1.25em !important; }
        .tabs-custom .q-tab__label { font-size: 0.6rem !important; }
        .device-position-panel { padding: 14px; }
        .device-position-grid { grid-template-columns: minmax(0, 1fr); }
        .device-orientation-scene { height: 226px; }
        .device-position-readings { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 440px) {
        .device-orientation-scene { height: 176px; }
        .device-position-readings { grid-template-columns: minmax(0, 1fr); }
    }
    </style>
  """)

# ── 탭별 렌더링 함수들 ─────────────────────────────────────

def render_status_card(title: str, value: str, icon: str, card_class: str):
  ui.html(
    f'<div class="pill-card {card_class}"><div class="pill-card-icon">{icon}</div>'
    + f'<div class="pill-card-text">{title}<div class="pill-card-value">{html_lib.escape(value)}</div></div></div>'
  ).classes('w-full')

@dataclass
class _CommitCheckState:
  running: bool = False
  failed: bool = False
  error: str = ""


def render_tab_functions():
  client = ui.context.client
  commit_state = _CommitCheckState()

  @ui.refreshable
  def functions_content():
    with ui.column().classes('w-full gap-3 mt-4'):

      with ui.element('div').classes('w-full grid grid-cols-1 sm:grid-cols-3 gap-3'):
        m_opts = ["[ Not Selected ]", "HYUNDAI", "KIA", "GENESIS"]
        c_m = get_param_text("SelectedManufacturer", m_opts[0])
        if c_m not in m_opts:
          c_m = m_opts[0]

        def on_m_change(event):
          if event.value != "[ Not Selected ]":
            params.put("SelectedManufacturer", event.value)
            mapping = {"HYUNDAI": "CarList_Hyundai", "KIA": "CarList_Kia", "GENESIS": "CarList_Genesis"}
            src = f"{BASE_PATH}/{mapping.get(event.value)}"
            if Path(src).exists():
              shutil.copy2(src, f"{BASE_PATH}/CarList")
          else:
            params.remove("SelectedManufacturer")
            params.remove("SelectedCar")
          _ = functions_content.refresh()

        ui.select(m_opts, value=c_m, label='🌐 Manufacturer', on_change=on_m_change).classes('w-full text-blue-200')

        c_opts = ["[ Not Selected ]"] + get_list_from_file(f"{BASE_PATH}/CarList")
        c_c = get_param_text("SelectedCar", c_opts[0])
        if c_c not in c_opts:
          c_c = c_opts[0]
        def on_c_change(event):
          if event.value != "[ Not Selected ]":
            params.put("SelectedCar", event.value)
          else:
            params.remove("SelectedCar")
          _ = functions_content.refresh()
        ui.select(c_opts, value=c_c, label='🚗 Car Model', on_change=on_c_change).classes('w-full text-blue-200')

        b_opts = ["[ Not Selected ]"] + get_list_from_file(f"{BASE_PATH}/GitBranchList")
        c_b = get_param_text("SelectedBranch", b_opts[0])
        if c_b not in b_opts:
          c_b = b_opts[0]
        def on_b_change(event):
          if event.value != "[ Not Selected ]":
            params.put("SelectedBranch", event.value)
          else:
            params.remove("SelectedBranch")
          _ = functions_content.refresh()
        ui.select(b_opts, value=c_b, label='🌿 Git Branch', on_change=on_b_change).classes('w-full text-blue-200')

      commit_output = get_param_text("CommitCompare")
      comparison = _parse_commit_compare(commit_output)
      if commit_state.running:
        commit_info, card_cls, icon = "Checking...", "card-warning", "🔍"
      elif commit_state.failed:
        commit_info = f"Check failed. {commit_state.error or 'Try again.'}"
        card_cls, icon = "card-danger", "❌"
      elif comparison is not None:
        local_commit, operator, remote_commit = comparison
        commit_info = f'"{local_commit[:7]}" {operator} "{remote_commit[:7]}"'
        card_cls, icon = ("card-success", "✅") if operator == "==" else ("card-danger", "⚠️")
      elif commit_output:
        commit_info, card_cls, icon = "Invalid comparison. Check again.", "card-danger", "❌"
      else:
        commit_info, card_cls, icon = "Check required", "card-warning", "🔍"

      with ui.element('div').classes('w-full grid grid-cols-1 sm:grid-cols-3 gap-3 items-center mt-2'):
        async def do_check_updates():
          if commit_state.running:
            return
          commit_state.running = True
          commit_state.failed = True
          commit_state.error = ""
          _ = functions_content.refresh()

          def record_error(message: str):
            commit_state.error = message

          try:
            # Refresh deletes the clicked button's slot; notifications need a stable parent.
            with client:
              return_code = await run_script_async("Commit Check", f"{SCRIPTS_PATH}/commit_compare.sh", on_error=record_error)
              comparison_result = _parse_commit_compare(get_param_text("CommitCompare"))
              commit_state.failed = return_code != 0 or comparison_result is None
              if return_code != 0 and not commit_state.error:
                commit_state.error = f"Update check exited with code {return_code}."
              if return_code == 0 and comparison_result is None:
                commit_state.error = "비교 결과를 읽을 수 없습니다. 다시 확인해주세요."
                ui.notify("[Commit Check] 비교 결과가 올바르지 않습니다. 다시 확인해주세요.", type='negative', position='top')
          finally:
            commit_state.running = False
            _ = functions_content.refresh()

        check_button = ui.button('CHECK UPDATES', on_click=do_check_updates, color=None).classes('custom-btn btn-blue w-full')
        if commit_state.running:
          check_button.disable()

        render_status_card("UPDATE STATUS", commit_info, icon, card_cls)

      if comparison is not None and comparison[1] == "!=" and not commit_state.running and not commit_state.failed:
        with ui.element('div').classes('w-full grid grid-cols-1 sm:grid-cols-3 gap-3 items-center mt-2'):
          async def do_git_pull():
            current_comparison = _parse_commit_compare(get_param_text("CommitCompare"))
            if commit_state.running or commit_state.failed or current_comparison is None or current_comparison[1] != "!=":
              ui.notify("커밋 상태를 다시 확인한 뒤 업데이트해주세요.", type='warning', position='top')
              _ = functions_content.refresh()
              return
            await run_script_async("Git Pull", f"{SCRIPTS_PATH}/gitpull.sh", show_modal=True)
            _ = functions_content.refresh()

          ui.button('GIT PULL NOW', on_click=do_git_pull, color=None).classes('custom-btn btn-blue-pull w-full')
          render_status_card("NEW UPDATE AVAILABLE", "Please pull the latest changes.", "⚠️", "card-warning")

      with ui.element('div').classes('w-full grid grid-cols-2 sm:grid-cols-3 gap-3 items-center mt-2'):
        async def do_restart():
          await run_script_async("Restart", f"{SCRIPTS_PATH}/restart.sh")

        ui.button('REBOOT', on_click=lambda: subprocess.Popen(["sudo", "reboot"], start_new_session=True), color=None).classes('custom-btn btn-red w-full')
        ui.button('RESTART', on_click=do_restart, color=None).classes('custom-btn btn-restart w-full')

  functions_content()

def _device_angle_labels(position: tuple[float, float] | None) -> tuple[str, str]:
  if position is None:
    return '—', '—'
  pitch, yaw = position
  pitch_direction = ' 아래' if pitch > 0 else ' 위' if pitch < 0 else ''
  yaw_direction = ' 왼쪽' if yaw > 0 else ' 오른쪽' if yaw < 0 else ''
  return f'{abs(pitch):.1f}°{pitch_direction}', f'{abs(yaw):.1f}°{yaw_direction}'


def _device_position_html(position: tuple[float, float] | None, *, live: bool = False, relative: bool = False) -> str:
  camera = ''
  pitch_text, yaw_text = _device_angle_labels(position)
  pitch_turn = yaw_turn = ''
  pitch_adjustment = yaw_adjustment = ''
  reference_label = '정면 기준'
  measured_label = '저장된 자세'
  status = '표시할 보정 각도가 없습니다.'
  aligned = live and not relative and position == (0.0, 0.0)
  if relative:
    reference_label = '시작 자세'
    measured_label = '현재 변화량'
    status = '시작 자세 대비 변화량 · 저장된 보정값이 있어야 목표 방향을 안내할 수 있습니다.'
  elif live:
    reference_label = '목표 자세 (0° / 0°)'
    measured_label = '현재 장착 추정'
    status = '목표 자세에 근접했습니다.' if aligned else '보정값 + 센서 변화량 · 모형 기울기 확대'

  if position is not None:
    pitch, yaw = position
    # CSS uses screen coordinates. Amplify the displayed pose while keeping the readouts in real degrees.
    visual_pitch = max(-28.0, min(28.0, -4.0 * pitch))
    visual_yaw = max(-28.0, min(28.0, -4.0 * yaw))
    camera = f'''
      <img class="device-camera-posed" src="{DEVICE_IMAGE_URL}" alt="" draggable="false"
           style="--pitch: {visual_pitch:.1f}deg; --yaw: {visual_yaw:.1f}deg">
    '''
    if not relative:
      pitch_adjustment = yaw_adjustment = '목표에 근접' if live else '기준 방향'
      if pitch:
        direction, arrow, label = ('up', '↑', '위로') if pitch > 0 else ('down', '↓', '아래로')
        pitch_turn = f' data-turn="{direction}"'
        pitch_move = f'{abs(pitch):.1f}° 기울이세요.' if live else '기울이기'
        pitch_adjustment = f'<span>{arrow}</span> 기기를 {label} {pitch_move}'
      if yaw:
        direction, arrow, label = ('right', '→', '오른쪽으로') if yaw > 0 else ('left', '←', '왼쪽으로')
        yaw_turn = f' data-turn="{direction}"'
        yaw_move = f'{abs(yaw):.1f}° 돌리세요.' if live else '돌리기'
        yaw_adjustment = f'<span>{arrow}</span> 기기를 {label} {yaw_move}'
    if not live:
      status = '저장 보정값 기준 · 모형 기울기 확대 표시'

  return f'''
    <div class="device-position-grid{' device-position-aligned' if aligned else ''}">
      <div aria-hidden="true">
        <div class="device-orientation-scene">
          <div class="device-axis-line device-axis-line-yaw"></div>
          <div class="device-axis-line device-axis-line-pitch"></div>
          <img class="device-camera-guide" src="{DEVICE_IMAGE_URL}" alt="" draggable="false">
          {camera}
          <div class="device-orbit device-orbit-yaw"{yaw_turn}></div>
          <div class="device-orbit device-orbit-pitch"{pitch_turn}></div>
          <span class="device-axis-tag device-axis-tag-yaw">YAW</span>
          <span class="device-axis-tag device-axis-tag-pitch">PITCH</span>
        </div>
        <div class="device-orientation-legend">
          <span><i></i>{reference_label}</span>
          <span><i class="measured"></i>{measured_label}</span>
        </div>
      </div>
      <div>
        <dl class="device-position-readings">
          <div class="device-reading-pitch">
            <dt>Pitch · 상하 회전 <span class="device-position-range" title="openpilot 장착 범위">위 5° / 아래 9° 이내</span></dt>
            <dd class="device-position-value">{pitch_text}</dd>
            <dd class="device-position-adjustment">{pitch_adjustment}</dd>
          </div>
          <div class="device-reading-yaw">
            <dt>Yaw · 좌우 회전 <span class="device-position-range" title="openpilot 장착 범위">좌우 4° 이내</span></dt>
            <dd class="device-position-value">{yaw_text}</dd>
            <dd class="device-position-adjustment">{yaw_adjustment}</dd>
          </div>
        </dl>
        <p class="device-position-status">{status}</p>
      </div>
    </div>
  '''


MOUNT_READY_TIMEOUT_NS = 5_000_000_000
MOUNT_IDLE_STATUS = '시동이 켜진 정차 상태에서 기준을 잡으세요.'


@dataclass
class DeviceMountSession:
  stream: MountSensorStream
  estimator: MountAdjustment
  calibration: tuple[float, float] | None
  started_ns: int = field(default_factory=time.monotonic_ns)
  after_accel_ns: int = field(init=False)
  after_gyro_ns: int = field(init=False)
  has_reference: bool = False

  def __post_init__(self):
    self.after_accel_ns = self.after_gyro_ns = self.started_ns


def render_tab_device(tabs):
  lock = asyncio.Lock()
  saved_position = None
  displayed_position = (None, False, False)
  mount = None

  def update_position(position, *, live=False, relative=False):
    nonlocal displayed_position
    if (position, live, relative) != displayed_position and not position_panel.is_deleted:
      position_panel.content = _device_position_html(position, live=live, relative=relative)
      displayed_position = position, live, relative

  def update_saved_position(position):
    nonlocal saved_position
    if position_panel.is_deleted:
      return
    saved_position = position
    if mount is None:
      update_position(position)

  async def refresh_position():
    if tabs.value != 'DEVICE' or lock.locked():
      return
    async with lock:
      position = await asyncio.to_thread(_read_device_position)
      if tabs.value == 'DEVICE':
        update_saved_position(position)

  def finish_mount(status=MOUNT_IDLE_STATUS):
    nonlocal mount
    if mount is not None:
      mount = None
      live_timer.deactivate()
    if not position_panel.is_deleted:
      start_button.enable()
      stop_button.set_visibility(False)
      saved_row.set_visibility(False)
      mount_status.set_text(status)
      update_position(saved_position)

  async def start_mount():
    nonlocal mount
    async with lock:
      if tabs.value != 'DEVICE' or mount is not None:
        return
      calibration = await asyncio.to_thread(_read_device_position)
      if tabs.value != 'DEVICE' or position_panel.is_deleted:
        return
      update_saved_position(calibration)
      try:
        stream = get_mount_sensor_stream()
      except (ImportError, OSError, RuntimeError):
        finish_mount('장착 조정용 센서를 열 수 없습니다.')
        return
      mount = DeviceMountSession(stream, MountAdjustment(), calibration)
      pitch, yaw = _device_angle_labels(calibration)
      saved_angles.set_text(f'Pitch {pitch} · Yaw {yaw}' if calibration is not None else '보정값 없음')
      start_button.disable()
      stop_button.set_visibility(True)
      saved_row.set_visibility(True)
      mount_status.set_text('기준 잡는 중 · 기기를 잠시 움직이지 마세요.')
      live_timer.activate()

  def refresh_mount():
    if mount is None or tabs.value != 'DEVICE':
      return
    try:
      samples = mount.stream.read(mount.after_accel_ns, mount.after_gyro_ns)
      position = None
      if samples is not None:
        accel, gyro = samples
        if accel:
          mount.after_accel_ns = accel[-1][0]
        if gyro:
          mount.after_gyro_ns = gyro[-1][0]
        position = mount.estimator.update(accel, gyro)
    except RuntimeError as error:
      finish_mount(str(error))
      return
    except (ValueError, OSError):
      finish_mount('센서 입력이 끊겼거나 불안정합니다. 기준을 다시 잡으세요.')
      return

    if position is None:
      if mount.has_reference:
        finish_mount('차량 상태 또는 센서 입력이 끊겼습니다. 기준을 다시 잡으세요.')
      elif time.monotonic_ns() - mount.started_ns >= MOUNT_READY_TIMEOUT_NS:
        finish_mount('기준 자세를 잡지 못했습니다. 시동과 정차 상태를 확인하고 기기를 잠시 고정하세요.')
      return

    if not mount.has_reference:
      mount.has_reference = True
      mount_status.set_text('조정 중 · Yaw는 시작 자세를 기준으로 추정하며 시간이 지나면 오차가 쌓일 수 있습니다.')
    pitch, yaw = position
    if mount.calibration is not None:
      start_pitch, start_yaw = mount.calibration
      pitch += start_pitch
      yaw = math.remainder(yaw + start_yaw, 360.0)
    update_position((round(pitch, 1), round(yaw, 1)), live=True, relative=mount.calibration is None)

  async def reset_position():
    async with lock:
      finish_mount()
      reset_calibration()
      update_saved_position(None)

  with ui.column().classes('w-full max-w-4xl mt-2 mx-auto'):
    with ui.column().classes('device-position-panel gap-2'):
      with ui.row().classes('w-full items-center justify-between gap-2'):
        ui.label('Device Position').classes('text-lg font-bold')
        ui.button('RESET CALIBRATION', icon='restart_alt', on_click=reset_position).props('outline dense color=amber-5').classes('px-2')
      with ui.row().classes('device-mount-controls w-full items-center gap-x-3 gap-y-1'):
        ui.label('장착 조정 모드').classes('font-bold')
        start_button = ui.button('기준 잡기', icon='adjust', on_click=start_mount).props('dense color=blue-8').classes('px-2')
        stop_button = ui.button('조정 종료', on_click=lambda: finish_mount()).props('dense outline color=blue-3').classes('px-2')
        stop_button.set_visibility(False)
        mount_status = ui.label(MOUNT_IDLE_STATUS).classes('device-mount-status').props('role=status')
      position_panel = ui.html(_device_position_html(None)).classes('w-full').props('id="device-position"')
      with ui.row().classes('device-saved-position w-full items-center justify-between gap-1') as saved_row:
        ui.label('저장된 보정값').classes('font-bold')
        saved_angles = ui.label('Pitch — · Yaw —')
      saved_row.set_visibility(False)

  timer = ui.timer(1.0, refresh_position, active=tabs.value == 'DEVICE')
  live_timer = ui.timer(0.1, refresh_mount, active=False)

  async def change_tab(event):
    if event.value == 'DEVICE':
      timer.activate()
      await refresh_position()
    else:
      timer.deactivate()
      finish_mount()

  tabs.on_value_change(change_tab)
  ui.context.client.on_disconnect(lambda: finish_mount())


def render_tab_toggles():
  toggle_items = [
    ("PcmCruiseEnable", "PcmCruise", "Change the openpilot cruise engagement"),
    ("CruiseStateControl", "Cruise State Controls", "Openpilot controls cruise on/off, set speed"),
    ("IsHda2", "CANFD Car HDA2", "Highway Drive Assist 2, turn it on"),
    ("CameraSccEnable", "CameraSCC", "HDA1 CameraSCC CAR, HDA2 type ADAS harness cable, turn it on"),
    ("RadarTrackEnable", "Enable Radar Track use", "Enable Radar Track use (disable AEB)"),
    ("AlphaLongitudinalEnabled", "AlphaLongitudinalEnabled use", "AlphaLongitudinalEnabled"),
    ("CabinCameraHardwareMissing", "Cabin Camera Hardware Missing", "Drive without the Cabin camera"),
    ("ClusterEnable", "Cluster Enable", "Enable Addon Cluster"),
    ("LanguageSetting", "Language (en/ko)", "Switch language between English and Korean"),
  ]

  @ui.refreshable
  def toggles_content():
    with ui.column().classes('w-full gap-4 mt-4'):
      for key, toggle_label, description in toggle_items:
        if key == "LanguageSetting":
          init_val = get_param_text(key) == "ko"
        else:
          init_val = params.get_bool(key)

        def on_change(event, k=key):
          if k == "LanguageSetting":
            params.put(k, "ko" if event.value else "en")
          else:
            params.put_bool(k, event.value, block=k == "ClusterEnable")
          ui.notify(f"{k} {'ON' if event.value else 'OFF'}", position='top')
          if k == "ClusterEnable":
            _ = toggles_content.refresh()

        with ui.row().classes('w-full flex-nowrap items-center border-b border-gray-800 pb-3'):
          ui.switch(value=init_val, on_change=on_change).classes('custom-toggle shrink-0')
          with ui.column().classes('gap-1 ml-3 flex-1 min-w-0'):
            ui.label(toggle_label).classes('text-[0.95rem] md:text-lg font-bold text-white leading-tight break-words whitespace-normal')
            ui.label(description).classes('text-[0.75rem] md:text-sm text-gray-400 leading-snug break-words whitespace-normal')

        if key == "ClusterEnable" and init_val:
          transport = get_param_text("ClusterDisplayTransport", "usb")
          if transport not in ("network", "usb"):
            transport = "usb"

          def on_transport_change(event):
            if event.value not in ("network", "usb"):
              return
            params.put("ClusterDisplayTransport", event.value, block=True)
            ui.notify(f"Cluster display transport: {event.value} (Cluster restarting)", position='top')
            _ = toggles_content.refresh()

          ui.select(
            {"network": "Network (Orange Pi HDMI)", "usb": "USB (TURZX Display)"},
            value=transport,
            label="Cluster display transport",
            on_change=on_transport_change,
          ).classes('w-full text-blue-200')
          ui.label('H.264 video. Changing the display transport restarts Cluster; new dependencies are prepared while offroad.').classes(
            'text-[0.75rem] md:text-sm text-gray-400 -mt-3 ml-1'
          )

          if transport == "network":
            profile = normalize_hdmi_profile(get_param_text("ClusterHdmiProfile", DEFAULT_HDMI_PROFILE))

            def on_hdmi_profile_change(event):
              if not isinstance(event.value, str) or event.value not in HDMI_PROFILES:
                return
              if event.value == normalize_hdmi_profile(get_param_text("ClusterHdmiProfile", DEFAULT_HDMI_PROFILE)):
                return
              params.put("ClusterHdmiProfile", event.value, block=True)
              ui.notify(f"HDMI monitor: {event.value} (Cluster restarting)", position='top')
              _ = toggles_content.refresh()

            ui.select(
              {"1920x480": "8.8인치 · 1920×480", "1920x720": "10.3인치 · 1920×720"},
              value=profile,
              label="HDMI monitor",
              on_change=on_hdmi_profile_change,
            ).classes('w-full text-blue-200')
            ui.label('모니터를 변경하면 Cluster가 재시작되고 새 해상도가 적용됩니다.').classes(
              'text-[0.75rem] md:text-sm text-gray-400 -mt-3 ml-1'
            )

        if key == "ClusterEnable":
          pi_status = ui.label(get_orange_pi_status_text()).classes('text-sm text-blue-200 break-words w-full')
          ui.timer(1.0, lambda status_label=pi_status: status_label.set_text(get_orange_pi_status_text()))

  toggles_content()

def render_tab_logs():
  converter = Ansi2HTMLConverter(inline=True, dark_bg=True)
  log_read_lock = asyncio.Lock()
  driving_log_content: str | None = None
  with ui.column().classes('w-full mt-2 gap-6'):
    with ui.column().classes('w-full gap-2'):
      ui.label('📂 Route Data Upload').classes('text-green-200 text-lg font-bold')
      try:
        routes = get_routes(REALDATA_PATH)
        route_error = "" if REALDATA_PATH.exists() else f"Path not found: {REALDATA_PATH}"
      except OSError as route_read_error:
        routes = []
        route_error = f"Cannot read routes: {route_read_error}"

      if route_error:
        ui.label(route_error).classes('text-red-300')
      elif not routes:
        ui.label('⚠️ No uploadable routes found.').classes('text-yellow-200')
      else:
        route_map = {route.name: route for route in routes}
        options = {
          route.name: f"[{datetime.fromtimestamp(route.modified).strftime('%Y-%m-%d %H:%M')}] {route.name} ({len(route.paths)} segs)"
          for route in routes
        }
        with ui.element('div').classes('w-full grid grid-cols-4 gap-2 items-center'):
          sel_route = ui.select(options, value=routes[0].name, label="Select Route to Upload").classes('col-span-3 min-w-0')

          def upload_route():
            targets = [str(path) for path in route_map[sel_route.value].paths]
            try:
              subprocess.Popen(
                _script_command(f"{SCRIPTS_PATH}/realdata_upload.sh", targets),
                start_new_session=True, env=_subprocess_env(),
              )
              ui.notify(f"✅ Upload started in background! ({len(targets)} segments)", type='positive', position='top')
            except OSError as upload_error:
              ui.notify(f"❌ Failed to start upload: {upload_error}", type='negative', position='top')

          ui.button('ROUTE UPLOAD', on_click=upload_route, color=None).classes('custom-btn btn-green-route col-span-1')

    with ui.column().classes('w-full gap-2'):
      ui.label('📄 System Logs').classes('text-blue-200 text-lg font-bold')
      with ui.element('div').classes('w-full grid grid-cols-4 gap-2 items-center'):
        sel_log = ui.select([DRIVING_DEBUG_LOG, *LOG_FILES], value="CAN Missing", label="Select Log File").classes('col-span-2 min-w-0')

        async def view_log():
          nonlocal driving_log_content
          async with log_read_lock:
            name = sel_log.value
            try:
              if name == DRIVING_DEBUG_LOG:
                snapshot = await asyncio.to_thread(read_driving_log, LOG_DIR, LOG_DIR)
                content = snapshot.text
                count = f'{snapshot.entry_count:,} entries'
              else:
                path = LOG_FILES[name]
                if path == TMUX_LOG_PATH:
                  path = await save_tmux_log()
                content = await asyncio.to_thread(path.read_text, encoding='utf-8', errors='replace')
                count = f'{len(content.splitlines())} lines'
            except (OSError, RuntimeError, TimeoutError) as log_read_error:
              if name == sel_log.value:
                driving_log_content = None
                viewer_container.content = f'<div style="color:#FCA5A5; font-weight:bold;">❌ {html_lib.escape(str(log_read_error))}</div>'
                status_container.content = '<div class="log-statusbar log-error">⚠️ 파일을 불러오지 못했습니다.</div>'
              return
            if name != sel_log.value:
              return
            if name == DRIVING_DEBUG_LOG:
              driving_log_content = content
              viewer_container.content = html_lib.escape(content)
            else:
              viewer_container.content = converter.convert(content, full=False)
            status_container.content = f'<div class="log-statusbar">📄 {name} | {count} | {len(content):,} chars</div>'
            _ = ui.run_javascript('var v=document.getElementById("logContainer");if(v)v.scrollTop=v.scrollHeight;')

        async def upload_log():
          if sel_log.value == DRIVING_DEBUG_LOG:
            if driving_log_content is None:
              await view_log()
            if sel_log.value == DRIVING_DEBUG_LOG and driving_log_content is not None:
              try:
                with TemporaryDirectory(prefix='driving-log-') as directory:
                  path = Path(directory) / 'driving_debug.log'
                  await asyncio.to_thread(path.write_text, driving_log_content, encoding='utf-8', newline='\n')
                  await run_script_async("Log Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=[str(path)])
              except OSError as snapshot_error:
                ui.notify(f"❌ Failed to prepare Driving Debug: {snapshot_error}", type='negative', position='top')
            return

          path = LOG_FILES[sel_log.value]
          try:
            if path == TMUX_LOG_PATH:
              path = await save_tmux_log()
          except (OSError, RuntimeError, TimeoutError) as log_capture_error:
            ui.notify(f"❌ Failed to capture tmux: {log_capture_error}", type='negative', position='top')
            return
          await run_script_async("Log Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=[str(path)])

        ui.button('VIEW', on_click=view_log, color=None).classes('custom-btn btn-default col-span-1')
        ui.button('UPLOAD', on_click=upload_log, color=None).classes('custom-btn btn-green col-span-1')

        def change_log():
          nonlocal driving_log_content
          driving_log_content = None

        sel_log.on_value_change(change_log)

      with ui.element('div').classes('log-viewer w-full mt-2').props('id="logContainer"'):
        viewer_container = ui.html('파일을 선택한 후 View 버튼을 눌러주세요.')
      status_container = ui.html('<div class="log-statusbar">📂 대기 중...</div>').classes('w-full')

def render_tab_terminal(tabs):
  conv = Ansi2HTMLConverter(inline=True, dark_bg=True)
  update_lock = asyncio.Lock()

  with ui.column().classes('w-full mt-4'):
    with ui.element('div').classes('log-viewer').props('id="termContainer"'):
        viewer = ui.html('Loading...')
    statusbar = ui.html('<div class="log-statusbar">Loading...</div>').classes('w-full')

  async def update_terminal():
    if update_lock.locked() or tabs.value != 'TERMINAL':
      return
    async with update_lock:
      if getattr(viewer, 'is_deleted', False) or viewer.parent_slot is None:
        return

      capture_failed = False
      try:
        content = await get_tmux_capture()
      except (OSError, RuntimeError, TimeoutError) as capture_error:
        capture_failed = True
        content = str(capture_error) or 'Tmux capture timed out'
      if tabs.value != 'TERMINAL' or getattr(viewer, 'is_deleted', False) or viewer.parent_slot is None:
        return
      lines = len(content.splitlines())
      now_str = datetime.now().strftime("%H:%M:%S")

      if capture_failed:
        viewer.content = f'<div style="color:#FCA5A5; font-weight:bold;">❌ {html_lib.escape(content)}</div>'
        statusbar.content = '<div class="log-statusbar log-error">⚠️ tmux 세션을 찾을 수 없습니다.</div>'
      else:
        colored_html = conv.convert(content, full=False)
        viewer.content = colored_html
        statusbar.content = f'<div class="log-statusbar">🖥️ Tmux Session | {lines} lines | 🔄 Updated: {now_str}</div>'

      js_code = """
      var container = document.getElementById("termContainer");
      if (container) {
          // 사용자가 화면 맨 밑에서 50px 이내 영역에 스크롤을 위치시켰는지 판별
          var isAtBottom = (container.scrollHeight - container.clientHeight - container.scrollTop) <= 50;
          var currentScroll = container.scrollTop;

          // 약간의 딜레이(DOM 변경 적용시간) 후 스크롤을 보정
          setTimeout(function() {
              if (isAtBottom) {
                  container.scrollTop = container.scrollHeight;
              } else if (container.scrollTop !== currentScroll) {
                  container.scrollTop = currentScroll; // 기존 위치 고정 (튀는 현상 방지)
              }
          }, 50);
      }
      """
      _ = ui.run_javascript(js_code)

  # 1초마다 터미널 업데이트 실행
  ui.timer(1.0, update_terminal)

def _camera_start_javascript() -> str:
  return r'''
    (async () => {
      if (window.dashboardCameraSession) window.dashboardCameraSession.stop();
      const cameras = __CAMERAS__;
      const session = {pc: null, stopped: false, controller: new AbortController(), panels: new Map(), tracks: new Set()};
      window.dashboardCameraSession = session;
      const current = () => window.dashboardCameraSession === session && !session.stopped;
      const update = (camera, message, color = 'rgba(0,0,0,0.65)') => {
        const panel = session.panels.get(camera);
        if (!current() || !panel) return;
        panel.status.innerText = message;
        panel.status.style.background = color;
      };
      const updateAll = (message, color) => cameras.forEach(camera => update(camera, message, color));
      session.stop = (message = 'Stopped') => {
        if (session.stopped) return;
        session.stopped = true;
        session.controller.abort();
        if (session.pc) session.pc.close();
        session.tracks.forEach(track => track.stop());
        session.tracks.clear();
        for (const panel of session.panels.values()) {
          if (panel.video.srcObject === panel.stream) panel.video.srcObject = null;
          if (panel.video.onplaying === panel.onplaying) panel.video.onplaying = null;
          if (panel.video.onwaiting === panel.onwaiting) panel.video.onwaiting = null;
          if (window.dashboardCameraSession === session && message !== null) {
            panel.status.innerText = message;
            panel.status.style.background = 'rgba(0,0,0,0.65)';
          }
        }
      };
      try {
        // NiceGUI updates the HTML and executes this code in the same batch.
        await new Promise(resolve => requestAnimationFrame(resolve));
        if (!current()) return;
        for (const camera of cameras) {
          const video = document.getElementById(`dashboard-camera-${camera}-video`);
          const status = document.getElementById(`dashboard-camera-${camera}-status`);
          if (!video || !status) throw new Error('Camera panels did not initialize.');
          const panel = {video, status, stream: null};
          panel.onplaying = () => update(camera, '● Stream Active', 'rgba(16,185,129,0.75)');
          panel.onwaiting = () => update(camera, 'Waiting for video...');
          video.onplaying = panel.onplaying;
          video.onwaiting = panel.onwaiting;
          session.panels.set(camera, panel);
        }
        updateAll('Initializing...');
        if (typeof RTCPeerConnection === 'undefined') throw new Error('This browser does not support WebRTC.');
        const pc = new RTCPeerConnection({iceServers: []});
        session.pc = pc;
        const transceiverCameras = new Map();
        cameras.forEach(camera => transceiverCameras.set(pc.addTransceiver('video', {direction: 'recvonly'}), camera));
        pc.ontrack = event => {
          if (!current()) { event.track.stop(); return; }
          const cameraPrefix = event.track.id.split(':')[0];
          const camera = cameras.includes(cameraPrefix) ? cameraPrefix : transceiverCameras.get(event.transceiver);
          const panel = session.panels.get(camera);
          if (!panel || event.track.kind !== 'video') { event.track.stop(); return; }
          if (panel.stream) panel.stream.getTracks().forEach(track => track.stop());
          session.tracks.add(event.track);
          // Each panel must contain only its own track even if the remote stream groups cameras.
          panel.stream = new MediaStream([event.track]);
          panel.video.srcObject = panel.stream;
          update(camera, 'Waiting for video...');
          panel.video.play().catch(() => update(camera, 'Tap Play to view the camera.'));
        };
        pc.onconnectionstatechange = () => {
          if (!current()) return;
          if (pc.connectionState === 'failed') {
            updateAll('Connection failed. Check the network and press CAMERA VIEW (WEB) again.', 'red');
            session.stop(null);
          } else if (pc.connectionState === 'disconnected') {
            updateAll('Connection interrupted. Waiting to reconnect...', 'orange');
          } else if (pc.connectionState === 'closed') {
            updateAll('Stream ended. Press CAMERA VIEW (WEB) to reconnect.');
          }
        };
        const offer = await pc.createOffer();
        if (!current()) return;
        if (!/a=rtpmap:\d+ H264\/90000/i.test(offer.sdp)) throw new Error('This browser does not offer H.264 video.');
        await pc.setLocalDescription(offer);
        if (!current()) return;
        await new Promise((resolve, reject) => {
          let timer;
          const cleanup = () => {
            clearTimeout(timer);
            pc.removeEventListener('icegatheringstatechange', changed);
            session.controller.signal.removeEventListener('abort', aborted);
          };
          const changed = () => {
            if (pc.iceGatheringState === 'complete') { cleanup(); resolve(); }
          };
          const aborted = () => { cleanup(); reject(new Error('Stream stopped.')); };
          pc.addEventListener('icegatheringstatechange', changed);
          session.controller.signal.addEventListener('abort', aborted);
          timer = setTimeout(() => { cleanup(); reject(new Error('Network candidate discovery timed out. Try again.')); }, 10000);
          if (session.controller.signal.aborted) aborted();
          else changed();
        });
        if (!current()) return;
        updateAll('Starting cameras...');
        const timer = setTimeout(() => session.controller.abort(), 50000);
        let response;
        try {
          response = await fetch('__STREAM_PATH__', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({sdp: pc.localDescription.sdp, cameras}),
            signal: session.controller.signal,
          });
        } finally {
          clearTimeout(timer);
        }
        if (!current()) return;
        const answer = await response.json();
        if (!current()) return;
        if (!response.ok) throw new Error(answer.error || `Stream request failed: ${response.status}`);
        updateAll('Connecting...');
        await pc.setRemoteDescription(answer);
      } catch (error) {
        if (!current()) return;
        updateAll(error.name === 'AbortError' ? 'Camera request timed out. Press CAMERA VIEW (WEB) to retry.' : error.message, 'red');
        session.stop(null);
      }
    })();
  '''.replace('__CAMERAS__', json.dumps(list(CAMERA_OPTIONS.values()))).replace('__STREAM_PATH__', CAMERA_STREAM_PATH)


CAMERA_STOP_JAVASCRIPT = '''
  if (window.dashboardCameraSession) {
    window.dashboardCameraSession.stop();
    window.dashboardCameraSession = null;
  }
  document.querySelectorAll('[id^="dashboard-camera-"][id$="-status"]').forEach(status => {
    status.innerText = 'Stopped';
    status.style.background = 'rgba(0,0,0,0.65)';
  });
'''


def render_tab_camera():

  with ui.column().classes('w-full mt-4'):
    def start_stream():
      _ = ui.run_javascript(_camera_start_javascript())

    def show_device():
      params.put_bool("CameraPreview", True, block=True)
      ui.notify("장치 화면에 카메라 보기(watch3)를 요청했습니다. STOP 또는 장치의 Close 버튼으로 종료할 수 있습니다.", type='info', position='top')

    def stop_stream():
      _ = ui.run_javascript(CAMERA_STOP_JAVASCRIPT)
      params.put_bool("CameraPreview", False, block=True)

    with ui.element('div').classes('camera-controls w-full grid grid-cols-3 gap-2 items-stretch'):
      ui.button('CAMERA VIEW (WEB)', on_click=start_stream, color=None).classes('custom-btn btn-camera w-full')
      ui.button('CAMERA VIEW (DEVICE)', on_click=show_device, color=None).classes('custom-btn btn-camera w-full')
      ui.button('STOP', on_click=stop_stream, color=None).classes('custom-btn btn-red-stop w-full')

    cards = ''.join(
      f'<div class="camera-card camera-card-{camera}">'
      + f'<div class="camera-card-title"><span class="camera-card-index">{index:02d}</span>{title}</div>'
      + '<div class="camera-video-wrap">'
      + f'<video id="dashboard-camera-{camera}-video" autoplay playsinline muted controls></video>'
      + f'<div id="dashboard-camera-{camera}-status" class="camera-status">Ready</div>'
      + '</div></div>'
      for index, (title, camera) in enumerate(CAMERA_OPTIONS.items(), start=1)
    )
    ui.html(f'<div class="camera-grid">{cards}</div>').classes('w-full mt-4')

# ── 메인 앱 구동 (Main) ──────────────────────────────────────
@ui.page('/')
def main_page():
  ui.dark_mode().enable()
  apply_styles()

  with ui.row().classes(
    'w-full flex-nowrap items-center justify-between px-2 pt-2 pb-1 gap-1 bg-[#0B0E14] sticky top-0 z-50 border-b border-[#1A2235]'):
    with ui.row().classes('flex-nowrap items-center shrink-0 px-1 gap-2'):
      ui.html(
        '<div style="font-size: 1.0rem; font-weight: 900; line-height: 1.1; color: #E8EEFF; letter-spacing: 0.02em;">'
        + 'Openpilot<br><span style="color:#3B82F6;">Dashboard</span></div>')
      ui.button(icon='refresh', on_click=lambda: ui.run_javascript('window.location.reload()')).classes(
        'refresh-btn rounded-full')

    with ui.tabs().props('align="right" active-color="white" indicator-color="white"').classes(
      'flex-1 overflow-x-auto tabs-custom') as tabs:
      ui.tab('FUNCTIONS', icon='rocket')
      ui.tab('DEVICE', icon='devices')
      ui.tab('TOGGLES', icon='settings')
      ui.tab('CAMERA', icon='photo_camera')
      ui.tab('DRIVES', icon='video_library')
      ui.tab('LOGS', icon='list_alt')
      ui.tab('TERMINAL', icon='terminal')

  with ui.tab_panels(tabs, value='FUNCTIONS').classes('w-full bg-transparent px-2 md:px-4'):
    with ui.tab_panel('FUNCTIONS'):
      render_tab_functions()
    with ui.tab_panel('DEVICE'):
      render_tab_device(tabs)
    with ui.tab_panel('TOGGLES'):
      render_tab_toggles()
    with ui.tab_panel('CAMERA'):
      render_tab_camera()
    with ui.tab_panel('DRIVES'):
      render_tab_drives(tabs)
    with ui.tab_panel('LOGS'):
      render_tab_logs()
    with ui.tab_panel('TERMINAL'):
      render_tab_terminal(tabs)

if __name__ in {"__main__", "__mp_main__"}:
  from openpilot.common.realtime import set_core_affinity

  set_core_affinity([0, 1, 2])
  _prepare_dashboard_port()
  ui.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, title="Openpilot Dashboard", show=False, reload=False)
