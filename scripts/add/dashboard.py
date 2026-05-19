import sys
import subprocess
import logging
import html as html_lib
import shutil
import asyncio
from pathlib import Path
from datetime import datetime

try:
  from nicegui import ui, app
except ImportError:
  logging.getLogger("dashboard").warning("nicegui not found. Remounting filesystem to install...")
  subprocess.check_call(["sudo", "mount", "-o", "remount,rw", "/"])
  subprocess.check_call(["sudo", sys.executable, "-m", "pip", "install", "nicegui"])
  try:
    subprocess.check_call(["sudo", "mount", "-o", "remount,ro", "/"])
  except subprocess.CalledProcessError:
    pass
  from nicegui import ui, app

try:
  from ansi2html import Ansi2HTMLConverter
except ImportError:
  logging.getLogger("terminal_tab").warning("ansi2html not found. Remounting filesystem to install...")
  subprocess.check_call(["sudo", "mount", "-o", "remount,rw", "/"])
  subprocess.check_call(["sudo", sys.executable, "-m", "pip", "install", "ansi2html"])
  try:
    subprocess.check_call(["sudo", "mount", "-o", "remount,ro", "/"])
  except subprocess.CalledProcessError:
    pass
  from ansi2html import Ansi2HTMLConverter

# ── 1. 환경 설정 및 유틸리티 ───────────────────────────────────────
SCRIPTS_PATH = "/data/openpilot/scripts"
BASE_PATH = "/data/params/crwusiz"

# Params (openpilot이 없는 PC 환경 테스트용 Mock 지원)
try:
  from openpilot.common.params import Params

  params = Params()
except ImportError:
  class MockParams:
    def __init__(self): self.data = {}
    def get(self, k, encoding='utf-8'): return self.data.get(k)
    def get_bool(self, k): return self.data.get(k, False)
    def put(self, k, v): self.data[k] = v
    def put_bool(self, k, v): self.data[k] = v
    def remove(self, k): self.data.pop(k, None)

  params = MockParams()


def get_list_from_file(path: str) -> list:
  if Path(path).exists():
    with open(path, 'r', encoding='utf-8') as f:
      return [line.strip() for line in f if line.strip()]
  return []


# ── 비동기 스크립트 실행 (즉각적인 알림 피드백을 위해 변경) ──
async def run_script_async(name: str, path: str, args: list = None) -> int:
  ui.notify(f"[{name}] 진행 중...", type='info', position='top')
  await asyncio.sleep(0.1)  # UI 알림이 먼저 렌더링되도록 양보
  try:
    cmd = ["bash", path] if path.endswith('.sh') else ["python3", path]
    if args: cmd += args

    process = await asyncio.create_subprocess_exec(
      *cmd,
      stdout=asyncio.subprocess.PIPE,
      stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode == 0:
      ui.notify(f"[{name}] 완료", type='positive', position='top')
    else:
      err_text = stderr.decode('utf-8').strip() if stderr else "Unknown error"
      ui.notify(f"[{name}] 에러: {err_text}", type='negative', position='top')
    return process.returncode
  except Exception as e:
    ui.notify(f"[{name}] 실행 실패: {e}", type='negative', position='top')
    return 1


def reset_calibration():
  for p in ["CalibrationParams", "LiveTorqueParameters", "LiveParameters", "LiveParametersV2", "LiveDelay"]:
    params.remove(p)
  params.put_bool("OnroadCycleRequested", True)
  ui.notify("캘리브레이션 초기화 요청 완료!", type='positive', position='top')


def get_tmux_capture() -> str:
  try:
    res = subprocess.run(["tmux", "capture-pane", "-pe", "-t", "0"], capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else "Tmux Session not found (Wait for openpilot to start...)"
  except Exception as e:
    return f"Error capturing tmux: {e}"


# ── 2. 전역 CSS 스타일 정의 (모바일 텍스트 랩핑/비율 최적화) ───────
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
        font-size: 1.4em !important; line-height: 42px !important; text-align: center !important; display: block !important; pointer-events: none !important; z-index: 2 !important;
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
    button.btn-green, button.btn-green-route, button.btn-green-start { background: linear-gradient(90deg, #065F46 0%, #10B981 100%) !important; box-shadow: 0 5px 22px rgba(16,185,129,0.45) !important;}
    button.btn-green::before { content: '⬆' !important; }
    button.btn-green-route::before { content: '🚀' !important; }
    button.btn-green-start::before { content: '▶' !important; }
    button.btn-red-stop { background: linear-gradient(90deg, #7F1D1D 0%, #EF4444 100%) !important; box-shadow: 0 5px 22px rgba(239,68,68,0.5) !important;}
    button.btn-red-stop::before { content: '⏹' !important; }
    button.btn-default { background: linear-gradient(90deg, #2A3348 0%, #3A4A6B 100%) !important; }
    button.btn-default::before { content: '👁' !important; }

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
    .custom-toggle[aria-checked="true"] .q-toggle__inner::before, .custom-toggle:has(input:checked) .q-toggle__inner::before { content: 'ON'; left: 10px; right: auto; }
    .custom-toggle[aria-checked="true"] .q-toggle__thumb, .custom-toggle:has(input:checked) .q-toggle__thumb { left: 42px !important; }

    /* ── 로그 뷰어 ── */
    .log-viewer {
        background: #0D1117; border: 1.5px solid #3A4A6B; border-radius: 12px;
        padding: 16px 20px; font-family: 'Courier New', Courier, monospace;
        font-size: 0.8em; color: #BCC4E0; white-space: pre-wrap; word-break: break-all;
        height: 430px; overflow-y: auto; box-shadow: inset 0 2px 12px rgba(0,0,0,0.5); width: 100%;
    }
    .log-statusbar {
        background: linear-gradient(90deg, #1A2235, #232E45); border: 1.5px solid #3A4A6B; border-left: 4px solid #3B82F6;
        border-radius: 12px; padding: 10px 16px; font-family: 'Courier New', monospace;
        font-size: 0.78em; color: #93C5FD; width: 100%; margin-top: 8px;
    }
    .log-error { border-left-color: #EF4444 !important; color: #FCA5A5 !important; }

    /* ── 모바일 환경 강제 최적화 미디어 쿼리 ── */
    @media (max-width: 768px) {
        button.custom-btn { padding: 6px 8px 6px 44px !important; min-height: 48px !important; }
        button.custom-btn .q-btn__content { font-size: 0.8rem !important; white-space: normal !important; overflow: visible !important; line-height: 1.15 !important; text-overflow: clip !important;}
        button.custom-btn::before { width: 34px !important; height: 34px !important; font-size: 1.1em !important; line-height: 30px !important; left: 4px !important; }

        .pill-card { min-height: 48px !important; padding-right: 12px !important; }
        .pill-card-icon { min-width: 34px !important; height: 34px !important; font-size: 1.1em !important; margin-left: 4px !important; margin-right: 8px !important;}
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


# ── 3. 탭별 렌더링 함수들 ─────────────────────────────────────

def render_tab_functions():
  # 상태 갱신을 위해 refreshable 데코레이터 적용
  @ui.refreshable
  def functions_content():
    with ui.column().classes('w-full gap-3 mt-4'):

      # 1. 드롭다운
      with ui.row().classes('w-full grid grid-cols-1 sm:grid-cols-3 gap-3'):
        m_opts = ["[ Not Selected ]", "HYUNDAI", "KIA", "GENESIS"]
        c_m = params.get("SelectedManufacturer") or m_opts[0]

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
        c_c = params.get("SelectedCar") or c_opts[0]

        def on_c_change(e):
          if e.value != "[ Not Selected ]":
            params.put("SelectedCar", e.value)
          else:
            params.remove("SelectedCar")
          functions_content.refresh()

        ui.select(c_opts, value=c_c, label='🚗 Car Model', on_change=on_c_change).classes('w-full text-blue-200')

        b_opts = ["[ Not Selected ]"] + get_list_from_file(f"{BASE_PATH}/GitBranchList")
        c_b = params.get("SelectedBranch") or b_opts[0]

        def on_b_change(e):
          if e.value != "[ Not Selected ]":
            params.put("SelectedBranch", e.value)
          else:
            params.remove("SelectedBranch")
          functions_content.refresh()

        ui.select(b_opts, value=c_b, label='🌿 Git Branch', on_change=on_b_change).classes('w-full text-blue-200')

      # 파라미터에서 업데이트 정보 안전하게 읽어오기
      commit_raw = params.get("CommitCompare")
      commit_output = commit_raw.decode('utf-8') if isinstance(commit_raw, bytes) else str(
        commit_raw) if commit_raw else ""
      commit_info = commit_output if commit_output else "Check required"

      # 2. 업데이트 체크
      with ui.row().classes('w-full flex flex-row flex-nowrap gap-2 items-center mt-2'):
        async def do_check_updates():
          await run_script_async("Commit Check", f"{SCRIPTS_PATH}/commit_compare.sh")
          functions_content.refresh()  # 완료 후 UI 새로고침

        ui.button('CHECK UPDATES', on_click=do_check_updates, color=None).classes(
          'custom-btn btn-blue w-[40%] sm:w-1/3 shrink-0')

        card_cls, icon = ('card-success', '✅') if " == " in commit_info else ('card-danger',
                                                                              '⚠️') if " != " in commit_info else (
          'card-warning', '🔍')
        ui.html(
          f'<div class="pill-card {card_cls}"><div class="pill-card-icon">{icon}</div><div class="pill-card-text">UPDATE STATUS<div class="pill-card-value">{commit_info}</div></div></div>').classes(
          'w-[60%] sm:flex-1 shrink-0')

      # 3. [복구 완료] Git Pull 버튼 (업데이트가 있을 때만 동적으로 생성됨)
      if commit_output and " != " in commit_output:
        with ui.row().classes('w-full flex flex-row flex-nowrap gap-2 items-center mt-2'):
          async def do_git_pull():
            await run_script_async("Git Pull", f"{SCRIPTS_PATH}/gitpull.sh")
            functions_content.refresh()

          ui.button('GIT PULL NOW', on_click=do_git_pull, color=None).classes(
            'custom-btn btn-blue-pull w-[40%] sm:w-1/3 shrink-0')
          ui.html(
            '<div class="pill-card card-warning"><div class="pill-card-icon">⚠️</div><div class="pill-card-text">NEW UPDATE AVAILABLE<div class="pill-card-value">Please pull the latest changes.</div></div></div>').classes(
            'w-[60%] sm:flex-1 shrink-0')

      # 4. 캘리브레이션
      with ui.row().classes('w-full flex flex-row flex-nowrap gap-2 items-center mt-2'):
        def do_reset_cal():
          reset_calibration()
          functions_content.refresh()

        ui.button('RESET CALIBRATION', on_click=do_reset_cal, color=None).classes(
          'custom-btn btn-yellow w-[40%] sm:w-1/3 shrink-0')
        dev_pos = params.get("DevicePosition") or "--"
        ui.html(
          f'<div class="pill-card card-info"><div class="pill-card-icon">📍</div><div class="pill-card-text">DEVICE POSITION<div class="pill-card-value">{dev_pos}</div></div></div>').classes(
          'w-[60%] sm:flex-1 shrink-0')

      # 5. 재부팅
      with ui.row().classes('w-full flex flex-row flex-nowrap gap-2 items-center mt-2'):
        ui.button('REBOOT', on_click=lambda: subprocess.Popen(["sudo", "reboot"]), color=None).classes(
          'custom-btn btn-red w-[40%] sm:w-1/3 shrink-0')

  # UI 렌더링 시작
  functions_content()


def render_tab_toggles():
  TOGGLE_ITEMS = [
    ("PcmCruiseEnable", "PcmCruise", "Change the openpilot cruise engagement"),
    ("CruiseStateControl", "Cruise State Controls", "Openpilot controls cruise on/off, set speed"),
    ("IsHda2", "CANFD Car HDA2", "Highway Drive Assist 2, turn it on"),
    ("CameraSccEnable", "CameraSCC",
     "HDA1 CameraSCC CAR, HDA2 Connect the ADAS ECAN line to CAMERA modify, turn it on"),
    ("RadarTrackEnable", "Enable Radar Track use", "Enable Radar Track use (disable AEB)"),
    ("DriverCameraOnReverse", "Driver Camera On Reverse", "Displays the driver camera when in reverse"),
    ("DriverCameraHardwareMissing", "Driver Camera Hardware Missing", "Drive without the driver camera"),
    ("LoggerEnable", "Logger Enable", "Enable Logger"),
    ("LanguageSetting", "Language (en/ko)", "Switch language between English and Korean"),
  ]

  with ui.column().classes('w-full gap-4 mt-4'):
    for key, label, desc in TOGGLE_ITEMS:
      if key == "LanguageSetting":
        val = params.get(key)
        init_val = (val.decode('utf-8') if isinstance(val, bytes) else val) == "ko"
      else:
        init_val = params.get_bool(key)

      def on_change(e, k=key):
        if k == "LanguageSetting":
          params.put(k, "ko" if e.value else "en")
        else:
          params.put_bool(k, e.value)
        ui.notify(f"{k} {'ON' if e.value else 'OFF'}", position='top')

      with ui.row().classes('w-full flex-nowrap items-center border-b border-gray-800 pb-3'):
        ui.switch(value=init_val, on_change=on_change).classes('custom-toggle shrink-0')
        with ui.column().classes('gap-1 ml-3 flex-1 min-w-0'):
          ui.label(label).classes(
            'text-[0.95rem] md:text-lg font-bold text-white leading-tight break-words whitespace-normal')
          ui.label(desc).classes('text-[0.75rem] md:text-sm text-gray-400 leading-snug break-words whitespace-normal')


def render_tab_logs():
  LOG_FILES = {
    "CAN Missing": "/data/can_missing.log",
    "CAN Timeout": "/data/can_timeout.log",
    "Tmux Error": "/data/tmux_error.log",
    "Tmux Console": "TMUX_CONSOLE"
  }
  REALDATA_PATH = Path("/data/media/0/realdata")

  with ui.column().classes('w-full mt-2 gap-6'):

    # ── 1. 리얼데이터 섹션 (주행 경로 데이터 업로드) ──
    with ui.column().classes('w-full gap-2'):
      ui.html(
        '<div style="color:#6EE7B7; font-size:1.1em; font-weight:800; margin-bottom:4px;"><span style="margin-right:6px;">📂</span>Route Data Upload</div>')

      if not REALDATA_PATH.exists():
        ui.html(
          '<div class="log-output-box log-error" style="margin-top:0;">❌ Path not found: /data/media/0/realdata</div>').classes(
          'w-full')
      else:
        route_map = {}
        for item in REALDATA_PATH.iterdir():
          if item.is_dir() and "--" in item.name and item.name != "boot":
            parts = item.name.split("--")
            if len(parts) >= 2:
              route_name = f"{parts[0]}--{parts[1]}"
              if route_name not in route_map:
                route_map[route_name] = {'paths': [], 'mtime': 0}
              route_map[route_name]['paths'].append(str(item))
              route_map[route_name]['mtime'] = max(route_map[route_name]['mtime'], item.stat().st_mtime)

        for r_data in route_map.values():
          r_data['paths'].sort(key=lambda x: int(x.split("--")[-1]))

        if not route_map:
          ui.html(
            '<div class="log-output-box log-warn" style="margin-top:0;">⚠️ No uploadable routes found.</div>').classes(
            'w-full')
        else:
          sorted_routes = sorted(route_map.items(), key=lambda x: x[1]['mtime'], reverse=True)
          options = [f"[{datetime.fromtimestamp(v['mtime']).strftime('%Y-%m-%d %H:%M')}] {k} ({len(v['paths'])} segs)"
                     for k, v in sorted_routes]

          with ui.row().classes('w-full flex flex-row flex-nowrap gap-2 items-center'):
            sel_route = ui.select(options, value=options[0], label="Select Route to Upload").classes(
              'w-[70%] min-w-0 shrink-0')

            def upload_route():
              idx = options.index(sel_route.value)
              targets = sorted_routes[idx][1]['paths']
              cmd = ["bash", f"{SCRIPTS_PATH}/realdata_upload.sh"] + targets
              try:
                subprocess.Popen(cmd)
                ui.notify(f"✅ Upload started in background! ({len(targets)} segments)", type='positive', position='top')
              except Exception as e:
                ui.notify(f"❌ Failed to start upload: {e}", type='negative', position='top')

            ui.button('ROUTE UPLOAD', on_click=upload_route, color=None).classes(
              'custom-btn btn-green-route w-[30%] shrink-0')

    with ui.column().classes('w-full gap-2'):
      ui.html(
        '<div style="color:#93C5FD; font-size:1.1em; font-weight:800; margin-bottom:4px;"><span style="margin-right:6px;">📄</span>System Logs</div>')

      with ui.row().classes('w-full flex flex-row flex-nowrap gap-2 items-center'):
        sel_log = ui.select(list(LOG_FILES.keys()), value="CAN Missing", label="Select Log File").classes(
          'w-[50%] min-w-0 shrink-0')
        ui.button('VIEW', on_click=lambda: view_log(), color=None).classes('custom-btn btn-default w-[25%] shrink-0')
        ui.button('UPLOAD', on_click=lambda: upload_log(), color=None).classes('custom-btn btn-green w-[25%] shrink-0')

      viewer_container = ui.html('<div class="log-viewer">파일을 선택한 후 View 버튼을 눌러주세요.</div>').classes('w-full mt-2')
      status_container = ui.html('<div class="log-statusbar">📂 대기 중...</div>').classes('w-full')

      def view_log():
        log_path = LOG_FILES[sel_log.value]
        content = ""
        err_msg = ""

        if log_path == "TMUX_CONSOLE":
          subprocess.run("tmux capture-pane -pe -t 0 -S -500 > /data/tmux_console.log", shell=True)
          p = Path("/data/tmux_console.log")
          if p.exists():
            content = p.read_text()
          else:
            err_msg = "Failed to capture tmux console."
        else:
          p = Path(log_path)
          if p.exists():
            content = p.read_text()
          else:
            err_msg = "File not found."

        if err_msg:
          viewer_container.content = f'<div class="log-viewer log-error">❌ {err_msg}</div>'
          status_container.content = '<div class="log-statusbar log-error">⚠️ 파일을 불러오지 못했습니다.</div>'
        else:
          conv = Ansi2HTMLConverter(inline=True, dark_bg=True)
          display = conv.convert(content, full=False)
          viewer_container.content = f'<div class="log-viewer" id="logViewer">{display}</div>'
          status_container.content = f'<div class="log-statusbar">📄 {sel_log.value} | {len(content.splitlines())} lines | {len(content):,} chars</div>'
          ui.run_javascript('var v=document.getElementById("logViewer");if(v)v.scrollTop=v.scrollHeight;')

      async def upload_log():
        log_path = LOG_FILES[sel_log.value]
        if log_path == "TMUX_CONSOLE":
          if not Path("/data/tmux_console.log").exists():
            subprocess.run("tmux capture-pane -pe -t 0 -S -500 > /data/tmux_console.log", shell=True)
          await run_script_async("Console Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=["tmux_console.log"])
        else:
          await run_script_async("Log Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=[sel_log.value])


def render_tab_terminal():
  conv = Ansi2HTMLConverter(inline=True, dark_bg=True)

  with ui.column().classes('w-full mt-4'):
    viewer = ui.html('<div class="log-viewer">Loading...</div>').classes('w-full')
    statusbar = ui.html('<div class="log-statusbar">Loading...</div>').classes('w-full')

  def update_terminal():
    content = get_tmux_capture()
    lines = len(content.splitlines())
    now_str = datetime.now().strftime("%H:%M:%S")

    if content.startswith("Error") or content.startswith("Tmux Session not found"):
      viewer.content = f'<div class="log-viewer log-error">❌ {html_lib.escape(content)}</div>'
      statusbar.content = '<div class="log-statusbar log-error">⚠️ tmux 세션을 찾을 수 없습니다.</div>'
    else:
      colored_html = conv.convert(content, full=False)
      viewer.content = f'<div class="log-viewer" id="termViewer">{colored_html}</div>'
      statusbar.content = f'<div class="log-statusbar">🖥️ Tmux Session | {lines} lines | 🔄 Updated: {now_str}</div>'
      ui.run_javascript('var v=document.getElementById("termViewer");if(v)v.scrollTop=v.scrollHeight;')

  ui.timer(1.0, update_terminal)


def render_tab_camera():
  CAMERA_OPTIONS = {"Road Camera": "road", "Driver Camera": "driver", "Wide Road Camera": "wideRoad"}

  with ui.column().classes('w-full mt-4'):
    with ui.row().classes('w-full flex flex-row flex-nowrap gap-2 items-center'):
      cam_select = ui.select(list(CAMERA_OPTIONS.keys()), value="Road Camera", label="Select Camera Source").classes(
        'w-[50%] min-w-0 shrink-0')
      ui.button('START', on_click=lambda: start_stream(), color=None).classes(
        'custom-btn btn-green-start w-[25%] shrink-0')
      ui.button('STOP', on_click=lambda: stop_stream(), color=None).classes('custom-btn btn-red-stop w-[25%] shrink-0')

    stream_container = ui.html().classes('w-full mt-4')

    def start_stream():
      stream_type = CAMERA_OPTIONS[cam_select.value]
      webrtc_html = f"""
            <div style="position:relative; width:100%; height:430px; background:#000; border-radius:12px; border:1.5px solid #3A4A6B; overflow:hidden;">
                <video id="video" autoplay playsinline muted controls style="width:100%; height:100%; object-fit:contain; cursor:pointer;"></video>
                <div id="status" style="position:absolute; top:10px; right:12px; color:#E8EEFF; background:rgba(0,0,0,0.65); padding:4px 10px; border-radius:20px; font-size:12px;">Initializing...</div>
            </div>
            """
      js_code = f"""
                async function startWebRTC() {{
                    const video = document.getElementById('video');
                    const status = document.getElementById('status');
                    const ip = window.location.hostname;
                    try {{
                        const pc = new RTCPeerConnection({{iceServers: []}});
                        pc.addTransceiver('video', {{ direction: 'recvonly' }});
                        pc.ontrack = (event) => {{
                            status.innerText = "● Stream Active"; status.style.background = "rgba(16,185,129,0.75)";
                            video.srcObject = event.streams[0]; video.play();
                        }};
                        const offer = await pc.createOffer();
                        await pc.setLocalDescription(offer);

                        await new Promise((r) => setTimeout(r, 1000));

                        const payload = {{ sdp: pc.localDescription.sdp, cameras: ["{stream_type}"], bridge_services_in: [], bridge_services_out: [] }};
                        const response = await fetch(`http://${{ip}}:5001/stream`, {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(payload) }});
                        if(response.ok) await pc.setRemoteDescription(await response.json());
                    }} catch (e) {{
                        status.innerText = "Error"; status.style.background = "red";
                    }}
                }}
                startWebRTC();
            """
      stream_container.content = webrtc_html
      ui.run_javascript(js_code)

    def stop_stream():
      stream_container.content = """
            <div class="log-viewer" style="display:flex; flex-direction:column; justify-content:center; align-items:center;">
                <div style="font-size:3em; filter:grayscale(1) opacity(0.3);">📷</div>
                <div style="font-size:0.9em; margin-top:10px; text-align:center;">Select a camera source and press Start</div>
            </div>
            """

  stop_stream()


# ── 4. 메인 앱 구동 (Main) ──────────────────────────────────────
@ui.page('/')
def main_page():
  ui.dark_mode().enable()
  apply_styles()

  with ui.row().classes(
    'w-full flex-nowrap items-center justify-between px-2 pt-2 pb-1 gap-1 bg-[#0B0E14] sticky top-0 z-50 border-b border-[#1A2235]'):
    ui.html(
      '<div style="font-size: 1.0rem; font-weight: 900; line-height: 1.1; color: #E8EEFF; letter-spacing: 0.02em;">Openpilot<br><span style="color:#3B82F6;">Dashboard</span></div>').classes(
      'shrink-0 px-1')

    with ui.tabs().props('align="right" active-color="white" indicator-color="white" inline-label=false').classes(
      'flex-1 overflow-x-auto tabs-custom') as tabs:
      ui.tab('FUNCTIONS', icon='rocket')
      ui.tab('TOGGLES', icon='settings')
      ui.tab('CAMERA', icon='photo_camera')
      ui.tab('LOGS', icon='list_alt')
      ui.tab('TERMINAL', icon='terminal')

  with ui.tab_panels(tabs, value='FUNCTIONS').classes('w-full bg-transparent px-2 md:px-4'):
    with ui.tab_panel('FUNCTIONS'): render_tab_functions()
    with ui.tab_panel('TOGGLES'): render_tab_toggles()
    with ui.tab_panel('CAMERA'): render_tab_camera()
    with ui.tab_panel('LOGS'): render_tab_logs()
    with ui.tab_panel('TERMINAL'): render_tab_terminal()


if __name__ in {"__main__", "__mp_main__"}:
  ui.run(host="0.0.0.0", port=7000, title="Openpilot Dashboard", show=False)
