#!/usr/bin/env python3
import asyncio
import codecs
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import errno
import html as html_lib
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ADDON_PYTHONPATH = os.environ.get("ADDON_PYTHONPATH")
if ADDON_PYTHONPATH and ADDON_PYTHONPATH not in sys.path:
  sys.path.insert(0, ADDON_PYTHONPATH)

try:
  from openpilot.common.realtime import set_core_affinity
except ImportError:
  def set_core_affinity(cores): pass

from ansi2html import Ansi2HTMLConverter
from nicegui import ui

# ── 환경 설정 및 유틸리티 ───────────────────────────────────────
SCRIPTS_PATH = "/data/openpilot/scripts"
BASE_PATH = "/data/params/crwusiz"
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 7000
MAX_COMMAND_OUTPUT = 100_000
LOG_DIR = Path('/data/log')
TMUX_LOG_PATH = LOG_DIR / 'tmux_console.log'
REALDATA_PATH = Path('/data/media/0/realdata')
LOG_FILES = {
  "CAN Missing": LOG_DIR / 'can_missing.log',
  "CAN Timeout": LOG_DIR / 'can_timeout.log',
  "Tmux Error": LOG_DIR / 'tmux_error.log',
  "Tmux Console": TMUX_LOG_PATH,
  "Navi Debug": LOG_DIR / 'navi_debug.log',
  "Cruise Debug": LOG_DIR / 'cruise_debug.log',
  "Traffic Debug": LOG_DIR / 'traffic_debug.log',
  "Cluster Debug": LOG_DIR / 'cluster_debug.log',
}

def _dashboard_port_available() -> bool:
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
      sock.bind((DASHBOARD_HOST, DASHBOARD_PORT))
    except OSError as e:
      if e.errno != errno.EADDRINUSE:
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

# Params (openpilot이 없는 PC 환경 테스트용 Mock 지원)
try:
  from openpilot.common.params import Params
  params = Params()
except ImportError:
  class MockParams:
    def __init__(self): self.data = {}
    def get(self, k, encoding='utf-8'): return self.data.get(k)
    def get_bool(self, k): return self.data.get(k, False)
    def put(self, k, v, block=False): self.data[k] = v
    def put_bool(self, k, v, block=False): self.data[k] = v
    def remove(self, k): self.data.pop(k, None)
  params = MockParams()

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
    while chunk := await process.stdout.read(4096):
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
  """Prefer curl's actual error over its progress meter."""
  lines = [line.strip() for line in output.replace('\r', '\n').splitlines() if line.strip()]
  if not lines:
    return "Unknown error"
  curl_errors = [line for line in lines if line.lower().startswith('curl:')]
  return (curl_errors[-1] if curl_errors else lines[-1])[:300]


async def run_script_async(name: str, path: str, args: list[str] | None = None, show_modal: bool = False) -> int:
  command = _script_command(path, args)
  if not show_modal:
    ui.notify(f"[{name}] 진행 중...", type='info', position='top')
    try:
      return_code, output = await _run_command(command)
      if return_code == 0:
        ui.notify(f"[{name}] 완료", type='positive', position='top')
      else:
        ui.notify(f"[{name}] 에러: {_get_error_summary(output)}", type='negative', position='top')
      return return_code
    except Exception as e:
      ui.notify(f"[{name}] 실행 실패: {e}", type='negative', position='top')
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
    ui.run_javascript(f'var v=document.getElementById("{viewer_id}");if(v)v.scrollTop=v.scrollHeight;')

  output = ""
  render_output(output)
  dialog.open()
  try:
    return_code, output = await _run_command(command, render_output)
    if return_code == 0:
      # gitpull.sh starts the update in tmux and owns the restart sequence.
      message = "tmux에서 업데이트를 시작했습니다." if Path(path).name == "gitpull.sh" else "성공적으로 완료되었습니다."
      output += f"\n✅ [{datetime.now().strftime('%H:%M:%S')}] {message}\n"
    else:
      output += f"\n❌ 오류가 발생하여 중단되었습니다. (Exit Code: {return_code})\n"
    return return_code
  except Exception as e:
    output += f"\n❌ 실행 실패: {e}\n"
    return 1
  finally:
    close_btn.classes(remove='hidden')
    render_output(output + "\n[ 우측 상단의 'X' 버튼을 누르거나 창 바깥을 클릭하여 닫아주세요. ]")

def reset_calibration():
  for p in ["CalibrationParams", "LiveTorqueParameters", "LiveParametersV2", "LiveDelay"]:
    params.remove(p)
  params.put_bool("OnroadCycleRequested", True)
  ui.notify("캘리브레이션 초기화 요청 완료!", type='positive', position='top')

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
  return sorted(routes.values(), key=lambda route: route.modified, reverse=True)


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

    button.btn-yellow { background: linear-gradient(90deg, #78350F 0%, #F59E0B 100%) !important; box-shadow: 0 5px 22px rgba(245,158,11,0.45) !important;}
    button.btn-yellow::before { content: '✦' !important; }
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
        text-transform: none; white-space: normal !important; overflow: visible !important;
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

    /* ── 모바일 환경 강제 최적화 미디어 쿼리 ── */
    @media (max-width: 768px) {
        button.custom-btn { padding: 6px 8px 6px 44px !important; min-height: 48px !important; }
        button.custom-btn .q-btn__content {
            font-size: 0.8rem !important; white-space: normal !important; overflow: visible !important;
            line-height: 1.15 !important; text-overflow: clip !important;
        }
        button.custom-btn::before {
            width: 34px !important; height: 34px !important; font-size: 1.1em !important; line-height: 30px !important; left: 4px !important;
        }

        .pill-card { min-height: 48px !important; padding-right: 12px !important; }
        .pill-card-icon {
            min-width: 34px !important; height: 34px !important; font-size: 1.1em !important; margin-left: 4px !important; margin-right: 8px !important;
        }
        .pill-card-text { font-size: 0.65rem !important; white-space: normal !important; overflow: visible !important; text-overflow: clip !important; }
        .pill-card-value { font-size: 0.85rem !important; white-space: normal !important; overflow: visible !important; text-overflow: clip !important;}

        .q-field__control { height: 48px !important; min-height: 48px !important; padding: 0 14px !important;}
        .q-field__native { font-size: 0.85em !important; }

        .tabs-custom .q-tab { padding: 0 4px !important; min-width: 48px !important; min-height: 48px !important; }
        .tabs-custom .q-tab__icon { font-size: 1.25em !important; }
        .tabs-custom .q-tab__label { font-size: 0.6rem !important; }
    }
    </style>
  """)

# ── 탭별 렌더링 함수들 ─────────────────────────────────────

def render_status_card(title: str, value: str, icon: str, card_class: str):
  ui.html(
    f'<div class="pill-card {card_class}"><div class="pill-card-icon">{icon}</div>'
    + f'<div class="pill-card-text">{title}<div class="pill-card-value">{html_lib.escape(value)}</div></div></div>'
  ).classes('w-full')

def render_tab_functions():
  @ui.refreshable
  def functions_content():
    with ui.column().classes('w-full gap-3 mt-4'):

      with ui.element('div').classes('w-full grid grid-cols-1 sm:grid-cols-3 gap-3'):
        m_opts = ["[ Not Selected ]", "HYUNDAI", "KIA", "GENESIS"]
        c_m = get_param_text("SelectedManufacturer", m_opts[0])
        if c_m not in m_opts:
          c_m = m_opts[0]

        def on_m_change(e):
          if e.value != "[ Not Selected ]":
            params.put("SelectedManufacturer", e.value)
            mapping = {"HYUNDAI": "CarList_Hyundai", "KIA": "CarList_Kia", "GENESIS": "CarList_Genesis"}
            src = f"{BASE_PATH}/{mapping.get(e.value)}"
            if Path(src).exists():
              shutil.copy2(src, f"{BASE_PATH}/CarList")
          else:
            params.remove("SelectedManufacturer")
            params.remove("SelectedCar")
          functions_content.refresh()

        ui.select(m_opts, value=c_m, label='🌐 Manufacturer', on_change=on_m_change).classes('w-full text-blue-200')

        c_opts = ["[ Not Selected ]"] + get_list_from_file(f"{BASE_PATH}/CarList")
        c_c = get_param_text("SelectedCar", c_opts[0])
        if c_c not in c_opts:
          c_c = c_opts[0]
        def on_c_change(e):
          if e.value != "[ Not Selected ]":
            params.put("SelectedCar", e.value)
          else:
            params.remove("SelectedCar")
          functions_content.refresh()
        ui.select(c_opts, value=c_c, label='🚗 Car Model', on_change=on_c_change).classes('w-full text-blue-200')

        b_opts = ["[ Not Selected ]"] + get_list_from_file(f"{BASE_PATH}/GitBranchList")
        c_b = get_param_text("SelectedBranch", b_opts[0])
        if c_b not in b_opts:
          c_b = b_opts[0]
        def on_b_change(e):
          if e.value != "[ Not Selected ]":
            params.put("SelectedBranch", e.value)
          else:
            params.remove("SelectedBranch")
          functions_content.refresh()
        ui.select(b_opts, value=c_b, label='🌿 Git Branch', on_change=on_b_change).classes('w-full text-blue-200')

      commit_output = get_param_text("CommitCompare")
      commit_info = commit_output or "Check required"

      with ui.element('div').classes('w-full grid grid-cols-1 sm:grid-cols-3 gap-3 items-center mt-2'):
        async def do_check_updates():
          await run_script_async("Commit Check", f"{SCRIPTS_PATH}/commit_compare.sh")
          functions_content.refresh()

        ui.button('CHECK UPDATES', on_click=do_check_updates, color=None).classes('custom-btn btn-blue w-full')

        card_cls, icon = ('card-success', '✅') if " == " in commit_info else ('card-danger', '⚠️') if " != " in commit_info else ('card-warning', '🔍')
        render_status_card("UPDATE STATUS", commit_info, icon, card_cls)

      if commit_output and " != " in commit_output:
        with ui.element('div').classes('w-full grid grid-cols-1 sm:grid-cols-3 gap-3 items-center mt-2'):
          async def do_git_pull():
            await run_script_async("Git Pull", f"{SCRIPTS_PATH}/gitpull.sh", show_modal=True)
            functions_content.refresh()

          ui.button('GIT PULL NOW', on_click=do_git_pull, color=None).classes('custom-btn btn-blue-pull w-full')
          render_status_card("NEW UPDATE AVAILABLE", "Please pull the latest changes.", "⚠️", "card-warning")

      with ui.element('div').classes('w-full grid grid-cols-1 sm:grid-cols-3 gap-3 items-center mt-2'):
        def do_reset_cal():
          reset_calibration()
          functions_content.refresh()

        ui.button('RESET CALIBRATION', on_click=do_reset_cal, color=None).classes('custom-btn btn-yellow w-full')
        render_status_card("DEVICE POSITION", get_param_text("DevicePosition", "--"), "📍", "card-info")

      with ui.element('div').classes('w-full grid grid-cols-1 sm:grid-cols-3 gap-3 items-center mt-2'):
        ui.button('REBOOT', on_click=lambda: subprocess.Popen(["sudo", "reboot"], start_new_session=True), color=None).classes('custom-btn btn-red w-full')

  functions_content()

def render_tab_toggles():
  TOGGLE_ITEMS = [
    ("PcmCruiseEnable", "PcmCruise", "Change the openpilot cruise engagement"),
    ("CruiseStateControl", "Cruise State Controls", "Openpilot controls cruise on/off, set speed"),
    ("IsHda2", "CANFD Car HDA2", "Highway Drive Assist 2, turn it on"),
    ("CameraSccEnable", "CameraSCC", "HDA1 CameraSCC CAR, HDA2 type ADAS harness cable, turn it on"),
    ("RadarTrackEnable", "Enable Radar Track use", "Enable Radar Track use (disable AEB)"),
    ("CabinCameraOnReverse", "Cabin Camera On Reverse", "Displays the Cabin camera when in reverse"),
    ("CabinCameraHardwareMissing", "Cabin Camera Hardware Missing", "Drive without the Cabin camera"),
    ("ClusterEnable", "Cluster Enable", "Enable Addon Cluster"),
    ("LanguageSetting", "Language (en/ko)", "Switch language between English and Korean"),
  ]

  @ui.refreshable
  def toggles_content():
    with ui.column().classes('w-full gap-4 mt-4'):
      for key, label, desc in TOGGLE_ITEMS:
        if key == "LanguageSetting":
          init_val = get_param_text(key) == "ko"
        else:
          init_val = params.get_bool(key)

        def on_change(e, k=key):
          if k == "LanguageSetting":
            params.put(k, "ko" if e.value else "en")
          else:
            params.put_bool(k, e.value, block=k == "ClusterEnable")
          ui.notify(f"{k} {'ON' if e.value else 'OFF'}", position='top')
          if k == "ClusterEnable":
            toggles_content.refresh()

        with ui.row().classes('w-full flex-nowrap items-center border-b border-gray-800 pb-3'):
          ui.switch(value=init_val, on_change=on_change).classes('custom-toggle shrink-0')
          with ui.column().classes('gap-1 ml-3 flex-1 min-w-0'):
            ui.label(label).classes('text-[0.95rem] md:text-lg font-bold text-white leading-tight break-words whitespace-normal')
            ui.label(desc).classes('text-[0.75rem] md:text-sm text-gray-400 leading-snug break-words whitespace-normal')

        if key == "ClusterEnable" and init_val:
          transport = get_param_text("ClusterDisplayTransport", "usb")
          if transport not in ("network", "usb"):
            transport = "usb"

          def on_transport_change(e):
            params.put("ClusterDisplayTransport", e.value, block=True)
            ui.notify(f"Cluster display transport: {e.value} (Cluster restarting)", position='top')

          ui.select(
            {"network": "Network (Orange Pi HDMI)", "usb": "USB (TURZX Display)"},
            value=transport,
            label="CLUSTER_DISPLAY_TRANSPORT",
            on_change=on_transport_change,
          ).classes('w-full text-blue-200')
          ui.label('The Cluster process restarts automatically when the transport changes.').classes(
            'text-[0.75rem] md:text-sm text-gray-400 -mt-3 ml-1'
          )

  toggles_content()

def render_tab_logs():
  converter = Ansi2HTMLConverter(inline=True, dark_bg=True)
  with ui.column().classes('w-full mt-2 gap-6'):
    with ui.column().classes('w-full gap-2'):
      ui.label('📂 Route Data Upload').classes('text-green-200 text-lg font-bold')
      try:
        routes = get_routes(REALDATA_PATH)
        route_error = "" if REALDATA_PATH.exists() else f"Path not found: {REALDATA_PATH}"
      except OSError as e:
        routes = []
        route_error = f"Cannot read routes: {e}"

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
            except OSError as e:
              ui.notify(f"❌ Failed to start upload: {e}", type='negative', position='top')

          ui.button('ROUTE UPLOAD', on_click=upload_route, color=None).classes('custom-btn btn-green-route col-span-1')

    with ui.column().classes('w-full gap-2'):
      ui.label('📄 System Logs').classes('text-blue-200 text-lg font-bold')
      with ui.element('div').classes('w-full grid grid-cols-4 gap-2 items-center'):
        sel_log = ui.select(list(LOG_FILES), value="CAN Missing", label="Select Log File").classes('col-span-2 min-w-0')

        async def view_log():
          name = sel_log.value
          try:
            path = LOG_FILES[name]
            if path == TMUX_LOG_PATH:
              path = await save_tmux_log()
            content = await asyncio.to_thread(path.read_text, encoding='utf-8', errors='replace')
          except (OSError, RuntimeError, TimeoutError) as e:
            viewer_container.content = f'<div style="color:#FCA5A5; font-weight:bold;">❌ {html_lib.escape(str(e))}</div>'
            status_container.content = '<div class="log-statusbar log-error">⚠️ 파일을 불러오지 못했습니다.</div>'
            return
          viewer_container.content = converter.convert(content, full=False)
          status_container.content = f'<div class="log-statusbar">📄 {name} | {len(content.splitlines())} lines | {len(content):,} chars</div>'
          ui.run_javascript('var v=document.getElementById("logContainer");if(v)v.scrollTop=v.scrollHeight;')

        async def upload_log():
          path = LOG_FILES[sel_log.value]
          try:
            if path == TMUX_LOG_PATH:
              path = await save_tmux_log()
          except (OSError, RuntimeError, TimeoutError) as e:
            ui.notify(f"❌ Failed to capture tmux: {e}", type='negative', position='top')
            return
          await run_script_async("Log Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=[str(path)])

        ui.button('VIEW', on_click=view_log, color=None).classes('custom-btn btn-default col-span-1')
        ui.button('UPLOAD', on_click=upload_log, color=None).classes('custom-btn btn-green col-span-1')

      with ui.element('div').classes('log-viewer w-full mt-2').props('id="logContainer"'):
        viewer_container = ui.html('파일을 선택한 후 View 버튼을 눌러주세요.')
      status_container = ui.html('<div class="log-statusbar">📂 대기 중...</div>').classes('w-full')

def render_tab_terminal(tabs):
  conv = Ansi2HTMLConverter(inline=True, dark_bg=True)
  updating = False

  with ui.column().classes('w-full mt-4'):
    with ui.element('div').classes('log-viewer').props('id="termContainer"'):
        viewer = ui.html('Loading...')
    statusbar = ui.html('<div class="log-statusbar">Loading...</div>').classes('w-full')

  async def update_terminal():
    nonlocal updating
    if updating or tabs.value != 'TERMINAL':
      return
    updating = True
    try:
      if getattr(viewer, 'is_deleted', False) or viewer.parent_slot is None:
        return

      error = False
      try:
        content = await get_tmux_capture()
      except (OSError, RuntimeError, TimeoutError) as e:
        error = True
        content = str(e) or 'Tmux capture timed out'
      if tabs.value != 'TERMINAL' or getattr(viewer, 'is_deleted', False) or viewer.parent_slot is None:
        return
      lines = len(content.splitlines())
      now_str = datetime.now().strftime("%H:%M:%S")

      if error:
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
      ui.run_javascript(js_code)

    finally:
      updating = False

  # 1초마다 터미널 업데이트 실행
  ui.timer(1.0, update_terminal)

def render_tab_camera():
  CAMERA_OPTIONS = {"Road Camera": "road", "Driver Camera": "driver", "Wide Road Camera": "wideRoad"}

  with ui.column().classes('w-full mt-4'):
    with ui.row().classes('w-full flex flex-row flex-nowrap gap-2 items-center'):
      cam_select = ui.select(list(CAMERA_OPTIONS.keys()), value="Road Camera", label="Select Camera Source").classes('w-[50%] min-w-0 shrink-0')
      ui.button('START', on_click=lambda: start_stream(), color=None).classes('custom-btn btn-green-start w-[25%] shrink-0')
      ui.button('STOP', on_click=lambda: stop_stream(), color=None).classes('custom-btn btn-red-stop w-[25%] shrink-0')

    stream_container = ui.html().classes('w-full mt-4')

    def start_stream():
      stream_type = CAMERA_OPTIONS[cam_select.value]
      webrtc_html = """
        <div style="position:relative; width:100%; height:430px; background:#000; border-radius:12px; border:1.5px solid #3A4A6B; overflow:hidden;">
            <video id="video" autoplay playsinline muted controls style="width:100%; height:100%; object-fit:contain; cursor:pointer;"></video>
            <div id="status" style="position:absolute; top:10px; right:12px; color:#E8EEFF; background:rgba(0,0,0,0.65);
                padding:4px 10px; border-radius:20px; font-size:12px;">Initializing...</div>
        </div>
      """
      js_code = f"""
            async function startWebRTC() {{
                const video = document.getElementById('video');
                const status = document.getElementById('status');
                const ip = window.location.hostname;
                if (window.dashboardPeerConnection) window.dashboardPeerConnection.close();
                const pc = new RTCPeerConnection({{iceServers: []}});
                window.dashboardPeerConnection = pc;
                try {{
                    pc.addTransceiver('video', {{ direction: 'recvonly' }});
                    pc.ontrack = (event) => {{
                        status.innerText = "● Stream Active"; status.style.background = "rgba(16,185,129,0.75)";
                        video.srcObject = event.streams[0]; video.play();
                    }};
                    const offer = await pc.createOffer();
                    await pc.setLocalDescription(offer);

                    await new Promise((resolve) => {{
                        if (pc.iceGatheringState === 'complete') {{
                            resolve();
                        }} else {{
                            const checkState = () => {{
                                if (pc.iceGatheringState === 'complete') {{
                                    pc.removeEventListener('icegatheringstatechange', checkState);
                                    resolve();
                                }}
                            }};
                            pc.addEventListener('icegatheringstatechange', checkState);
                            setTimeout(() => {{
                                pc.removeEventListener('icegatheringstatechange', checkState);
                                resolve();
                            }}, 2000); // 2초 초과시 강제 진행
                        }}
                    }});

                    if (window.dashboardPeerConnection !== pc) return;
                    const payload = {{
                        sdp: pc.localDescription.sdp, cameras: ["{stream_type}"], enabled: true,
                        bridge_services_in: [], bridge_services_out: []
                    }};
                    const response = await fetch(`http://${{ip}}:5001/stream`, {{
                        method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(payload)
                    }});
                    if (window.dashboardPeerConnection !== pc) return;
                    if (!response.ok) throw new Error(`Stream request failed: ${{response.status}}`);
                    await pc.setRemoteDescription(await response.json());
                }} catch (e) {{
                    pc.close();
                    if (window.dashboardPeerConnection !== pc) return;
                    window.dashboardPeerConnection = null;
                    status.innerText = "Error"; status.style.background = "red";
                }}
            }}
            startWebRTC();
      """
      stream_container.content = webrtc_html
      ui.run_javascript(js_code)

    def stop_stream():
      ui.run_javascript('''
        if (window.dashboardPeerConnection) {
          window.dashboardPeerConnection.close();
          window.dashboardPeerConnection = null;
        }
        const video = document.getElementById('video');
        if (video && video.srcObject) {
          video.srcObject.getTracks().forEach(track => track.stop());
          video.srcObject = null;
        }
      ''')
      stream_container.content = """
        <div class="log-viewer" style="display:flex; flex-direction:column; justify-content:center; align-items:center;">
            <div style="font-size:3em; filter:grayscale(1) opacity(0.3);">📷</div>
            <div style="font-size:0.9em; margin-top:10px; text-align:center;">Select a camera source and press Start</div>
        </div>
      """

  stop_stream()

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

    with ui.tabs().props('align="right" active-color="white" indicator-color="white" inline-label=false').classes(
      'flex-1 overflow-x-auto tabs-custom') as tabs:
      ui.tab('FUNCTIONS', icon='rocket')
      ui.tab('TOGGLES', icon='settings')
      ui.tab('CAMERA', icon='photo_camera')
      ui.tab('LOGS', icon='list_alt')
      ui.tab('TERMINAL', icon='terminal')

  with ui.tab_panels(tabs, value='FUNCTIONS').classes('w-full bg-transparent px-2 md:px-4'):
    with ui.tab_panel('FUNCTIONS'):
      render_tab_functions()
    with ui.tab_panel('TOGGLES'):
      render_tab_toggles()
    with ui.tab_panel('CAMERA'):
      render_tab_camera()
    with ui.tab_panel('LOGS'):
      render_tab_logs()
    with ui.tab_panel('TERMINAL'):
      render_tab_terminal(tabs)

if __name__ in {"__main__", "__mp_main__"}:
  set_core_affinity([0, 1, 2])
  _prepare_dashboard_port()
  ui.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, title="Openpilot Dashboard", show=False, reload=False)
