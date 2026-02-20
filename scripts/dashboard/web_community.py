import streamlit as st
import subprocess
import shutil
import os
import time
import html as html_lib
from datetime import datetime
from pathlib import Path
import streamlit.components.v1 as components

ACCESS_PASSWORD = "comma"


def check_password():
  if st.session_state.get("password_correct", False):
    return True

  # ex: http://ip:port/?pwd=comma
  try:
    if st.query_params.get("pwd") == ACCESS_PASSWORD:
      st.session_state["password_correct"] = True
      return True
  except Exception:
    pass

  # ex: http://ip:port/
  st.title("🔒 Access Restricted")
  pwd = st.text_input("Enter Dashboard Password", type="password")

  if st.button("Login"):
    if pwd == ACCESS_PASSWORD:
      st.session_state["password_correct"] = True
      st.rerun()
    else:
      st.error("Incorrect Password")
  return False


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

BASE_PATH = "/data/params/crwusiz"
CAR_LIST_PATH = f"{BASE_PATH}/CarList"
BRANCH_LIST_PATH = f"{BASE_PATH}/GitBranchList"
SCRIPTS_PATH = "/data/openpilot/scripts"
REALDATA_PATH = Path("/data/media/0/realdata")


def get_list_from_file(path: str):
  if Path(path).exists():
    with open(path, 'r', encoding='utf-8') as f:
      return [line.strip() for line in f if line.strip()]
  return []


def run_script(name, path, args=None):
  try:
    cmd = ["bash", path] if path.endswith('.sh') else ["python3", path]
    if args: cmd += args
    res = subprocess.run(cmd, capture_output=True, text=True)
    lines = []
    if res.stdout: lines.append(res.stdout.strip())
    if res.stderr: lines.append(f"[STDERR] {res.stderr.strip()}")
    st.session_state["script_log"] = f"[{name}] " + ("\n".join(lines) if lines else "Completed.")
    st.session_state["script_ok"] = (res.returncode == 0)
    return res.returncode
  except Exception as e:
    st.session_state["script_log"] = f"[{name}] Error: {e}"
    st.session_state["script_ok"] = False
    return 1


def reset_calibration():
  params.remove("CalibrationParams")
  params.remove("LiveTorqueParameters")
  params.remove("LiveParameters")
  params.remove("LiveParametersV2")
  params.remove("LiveDelay")
  params.put_bool("OnroadCycleRequested", True)
  st.toast("Calibration Reset Requested!")


def get_tmux_capture():
  try:
    res = subprocess.run(["tmux", "capture-pane", "-p", "-t", "0"], capture_output=True, text=True)
    if res.returncode == 0:
      return res.stdout
    return "Tmux Session not found (Wait for openpilot to start...)"
  except Exception as e:
    return f"Error capturing tmux: {e}"


def main():
  st.set_page_config(page_title="Openpilot Dashboard", layout="wide", initial_sidebar_state="collapsed")

  st.markdown("""
    <style>
    .stApp { background-color: #0B0E14; }

    .stButton > button {
      border-radius: 50px !important;
      height: 56px !important;
      padding: 0 24px 0 70px !important;
      text-align: left !important;
      font-weight: 900 !important;
      font-size: 0.88em !important;
      letter-spacing: 0.08em !important;
      text-transform: uppercase !important;
      position: relative !important;
      overflow: visible !important;
      background: linear-gradient(90deg, #2A3348 0%, #3A4A6B 100%) !important;
      border: none !important;
      color: #E8EEFF !important;
      box-shadow: 0 5px 22px rgba(0,0,0,0.45), 0 1px 4px rgba(0,0,0,0.3) !important;
      transition: all 0.22s ease !important;
      z-index: 1 !important;
    }
    .stButton > button:hover {
      transform: translateY(-2px) !important;
      filter: brightness(1.18) !important;
      box-shadow: 0 8px 30px rgba(0,0,0,0.5) !important;
    }
    .stButton > button:active {
      transform: translateY(0) !important;
      filter: brightness(0.95) !important;
    }

    .stButton > button::before {
      content: '' !important;
      position: absolute !important;
      left: 5px !important;
      top: 50% !important;
      transform: translateY(-50%) !important;
      width: 46px !important;
      height: 46px !important;
      background: rgba(255,255,255,0.18) !important;
      border-radius: 50% !important;
      border: 2px solid rgba(255,255,255,0.35) !important;
      box-shadow: 0 2px 8px rgba(0,0,0,0.25) !important;
      font-size: 1.4em !important;
      line-height: 42px !important;
      text-align: center !important;
      display: block !important;
      pointer-events: none !important;
      z-index: 2 !important;
    }

    div:has([id^="btn_marker_blue_check"]) ~ div > div > button,
    div:has([id^="btn_marker_blue_check"]) + div > div > button {
      background: linear-gradient(90deg, #1E3A8A 0%, #3B82F6 100%) !important;
      box-shadow: 0 5px 22px rgba(59,130,246,0.5) !important;
    }
    div:has([id^="btn_marker_blue_check"]) ~ div > div > button::before,
    div:has([id^="btn_marker_blue_check"]) + div > div > button::before {
      content: '🔍' !important;
    }

    div:has([id^="btn_marker_blue_pull"]) ~ div > div > button,
    div:has([id^="btn_marker_blue_pull"]) + div > div > button {
      background: linear-gradient(90deg, #1E3A8A 0%, #3B82F6 100%) !important;
      box-shadow: 0 5px 22px rgba(59,130,246,0.5) !important;
    }
    div:has([id^="btn_marker_blue_pull"]) ~ div > div > button::before,
    div:has([id^="btn_marker_blue_pull"]) + div > div > button::before {
      content: '⬇' !important;
    }

    div:has([id^="btn_marker_success_upload"]) ~ div > div > button,
    div:has([id^="btn_marker_success_upload"]) + div > div > button {
      background: linear-gradient(90deg, #065F46 0%, #10B981 100%) !important;
      box-shadow: 0 5px 22px rgba(16,185,129,0.45) !important;
    }
    div:has([id^="btn_marker_success_upload"]) ~ div > div > button::before,
    div:has([id^="btn_marker_success_upload"]) + div > div > button::before {
      content: '⬆' !important;
    }
    div:has([id^="btn_marker_success_route"]) ~ div > div > button,
    div:has([id^="btn_marker_success_route"]) + div > div > button {
      background: linear-gradient(90deg, #065F46 0%, #10B981 100%) !important;
      box-shadow: 0 5px 22px rgba(16,185,129,0.45) !important;
    }
    div:has([id^="btn_marker_success_route"]) ~ div > div > button::before,
    div:has([id^="btn_marker_success_route"]) + div > div > button::before {
      content: '🚀' !important;
    }

    div:has([id^="btn_marker_success_start"]) ~ div > div > button,
    div:has([id^="btn_marker_success_start"]) + div > div > button {
      background: linear-gradient(90deg, #065F46 0%, #10B981 100%) !important;
      box-shadow: 0 5px 22px rgba(16,185,129,0.45) !important;
    }
    div:has([id^="btn_marker_success_start"]) ~ div > div > button::before,
    div:has([id^="btn_marker_success_start"]) + div > div > button::before {
      content: '▶' !important;
    }

    div:has([id^="btn_marker_danger_reboot"]) ~ div > div > button,
    div:has([id^="btn_marker_danger_reboot"]) + div > div > button {
      background: linear-gradient(90deg, #7F1D1D 0%, #EF4444 100%) !important;
      box-shadow: 0 5px 22px rgba(239,68,68,0.5) !important;
    }
    div:has([id^="btn_marker_danger_reboot"]) ~ div > div > button::before,
    div:has([id^="btn_marker_danger_reboot"]) + div > div > button::before {
      content: '⏻' !important;
    }

    div:has([id^="btn_marker_dlg_yes"]) ~ div > div > button,
    div:has([id^="btn_marker_dlg_yes"]) + div > div > button {
      background: linear-gradient(90deg, #065F46 0%, #10B981 100%) !important;
      box-shadow: 0 5px 22px rgba(16,185,129,0.45) !important;
    }
    div:has([id^="btn_marker_dlg_yes"]) ~ div > div > button::before,
    div:has([id^="btn_marker_dlg_yes"]) + div > div > button::before {
      content: '✓' !important;
      pointer-events: none !important;
    }

    div:has([id^="btn_marker_dlg_no"]) ~ div > div > button,
    div:has([id^="btn_marker_dlg_no"]) + div > div > button {
      background: linear-gradient(90deg, #7F1D1D 0%, #EF4444 100%) !important;
      box-shadow: 0 5px 22px rgba(239,68,68,0.5) !important;
    }
    div:has([id^="btn_marker_dlg_no"]) ~ div > div > button::before,
    div:has([id^="btn_marker_dlg_no"]) + div > div > button::before {
      content: '✕' !important;
      pointer-events: none !important;
    }

    div:has([id^="btn_marker_danger_stop"]) ~ div > div > button,
    div:has([id^="btn_marker_danger_stop"]) + div > div > button {
      background: linear-gradient(90deg, #7F1D1D 0%, #EF4444 100%) !important;
      box-shadow: 0 5px 22px rgba(239,68,68,0.5) !important;
    }
    div:has([id^="btn_marker_danger_stop"]) ~ div > div > button::before,
    div:has([id^="btn_marker_danger_stop"]) + div > div > button::before {
      content: '⏹' !important;
    }

    div:has([id^="btn_marker_warning_cal"]) ~ div > div > button,
    div:has([id^="btn_marker_warning_cal"]) + div > div > button {
      background: linear-gradient(90deg, #78350F 0%, #F59E0B 100%) !important;
      box-shadow: 0 5px 22px rgba(245,158,11,0.45) !important;
    }
    div:has([id^="btn_marker_warning_cal"]) ~ div > div > button::before,
    div:has([id^="btn_marker_warning_cal"]) + div > div > button::before {
      content: '✦' !important;
    }

    div:has([id^="btn_marker_default_view"]) ~ div > div > button::before,
    div:has([id^="btn_marker_default_view"]) + div > div > button::before {
      content: '👁' !important;
    }

    div:has([id^="toggle_wrap_"]) ~ div > div > button,
    div:has([id^="toggle_wrap_"]) + div > div > button {
      width: 70px !important;
      border-radius: 16px !important;
      height: 32px !important;
      min-height: 32px !important;
      max-height: 32px !important;
      padding: 0 !important;
      text-align: center !important;
      text-transform: none !important;
      font-size: 0 !important;
      letter-spacing: 0 !important;
      transform: none !important;
      filter: none !important;
      box-shadow: inset 0 2px 5px rgba(0,0,0,0.35) !important;
    }
    div:has([id^="toggle_wrap_"]) ~ div > div > button:hover,
    div:has([id^="toggle_wrap_"]) + div > div > button:hover {
      transform: none !important;
      filter: none !important;
    }
    div:has([id^="toggle_wrap_"]) ~ div > div > button::before,
    div:has([id^="toggle_wrap_"]) + div > div > button::before {
      content: '' !important;
      position: absolute !important;
      width: 24px !important;
      height: 24px !important;
      background: white !important;
      border-radius: 50% !important;
      border: none !important;
      top: 4px !important;
      transform: none !important;
      font-size: 0 !important;
      line-height: 0 !important;
      box-shadow: 0 2px 4px rgba(0,0,0,0.35) !important;
      transition: left 0.2s ease !important;
      pointer-events: none !important;
    }
    div:has([id^="toggle_wrap_"]) ~ div > div > button::after,
    div:has([id^="toggle_wrap_"]) + div > div > button::after {
      display: block !important;
      position: absolute !important;
      top: 50% !important;
      transform: translateY(-50%) !important;
      color: white !important;
      font-size: 11px !important;
      font-weight: 800 !important;
      font-family: sans-serif !important;
      line-height: 1 !important;
      pointer-events: none !important;
    }

    div:has([id^="toggle_wrap_off_"]) ~ div > div > button { background: #E03535 !important; }
    div:has([id^="toggle_wrap_off_"]) + div > div > button { background: #E03535 !important; }
    div:has([id^="toggle_wrap_off_"]) ~ div > div > button:hover { background: #E03535 !important; transform: none !important; }
    div:has([id^="toggle_wrap_off_"]) + div > div > button:hover { background: #E03535 !important; transform: none !important; }
    div:has([id^="toggle_wrap_off_"]) ~ div > div > button::before,
    div:has([id^="toggle_wrap_off_"]) + div > div > button::before { left: 4px !important; }
    div:has([id^="toggle_wrap_off_"]) ~ div > div > button::after,
    div:has([id^="toggle_wrap_off_"]) + div > div > button::after {
      content: 'OFF' !important; right: 7px !important; left: auto !important;
    }


    div:has([id^="toggle_wrap_on_"]) ~ div > div > button { background: #10B981 !important; }
    div:has([id^="toggle_wrap_on_"]) + div > div > button { background: #10B981 !important; }
    div:has([id^="toggle_wrap_on_"]) ~ div > div > button:hover { background: #10B981 !important; transform: none !important; }
    div:has([id^="toggle_wrap_on_"]) + div > div > button:hover { background: #10B981 !important; transform: none !important; }
    div:has([id^="toggle_wrap_on_"]) ~ div > div > button::before,
    div:has([id^="toggle_wrap_on_"]) + div > div > button::before { left: 42px !important; }
    div:has([id^="toggle_wrap_on_"]) ~ div > div > button::after,
    div:has([id^="toggle_wrap_on_"]) + div > div > button::after {
      content: 'ON' !important; left: 9px !important; right: auto !important;
    }

    div[data-baseweb="select"] input {
      caret-color: transparent !important;
      user-select: none !important;
    }
    div[data-baseweb="select"] input:focus {
      outline: none !important;
      box-shadow: none !important;
    }

    .log-output-box {
      background: linear-gradient(90deg, #0D1117 0%, #161B22 100%);
      border: 1.5px solid #3A4A6B;
      border-left: 4px solid #3B82F6;
      border-radius: 16px;
      padding: 16px 20px;
      font-family: 'Courier New', Courier, monospace;
      font-size: 0.82em;
      color: #BCC4E0;
      white-space: pre-wrap;
      word-break: break-all;
      line-height: 1.6;
      box-shadow: 0 4px 16px rgba(0,0,0,0.4);
      margin-top: 8px;
    }
    .log-viewer {
      background: #0D1117;
      border: 1.5px solid #3A4A6B;
      border-radius: 12px;
      padding: 16px 20px;
      font-family: 'Courier New', Courier, monospace;
      font-size: 0.8em;
      color: #BCC4E0;
      white-space: pre-wrap;
      word-break: break-all;
      line-height: 1.6;
      height: 430px;
      overflow-y: auto;
      box-shadow: inset 0 2px 12px rgba(0,0,0,0.5);
      margin-top: 8px;
      scroll-behavior: smooth;
    }
    .log-statusbar {
      background: linear-gradient(90deg, #1A2235, #232E45);
      border: 1.5px solid #3A4A6B;
      border-left: 4px solid #3B82F6;
      border-radius: 12px;
      padding: 10px 16px;
      font-family: 'Courier New', Courier, monospace;
      font-size: 0.78em;
      color: #93C5FD;
      margin-top: 10px;
      min-height: 40px;
    }
    .log-output-box.log-error, .log-statusbar.log-error  { border-left-color: #EF4444; color: #FCA5A5; }
    .log-output-box.log-success,.log-statusbar.log-success{ border-left-color: #10B981; color: #6EE7B7; }
    .log-output-box.log-warn,  .log-statusbar.log-warn   { border-left-color: #D97706; color: #FCD34D; }

    [data-testid="stSelectbox"] label {
      color: #7B8EC8 !important;
      font-size: 0.78em !important;
      font-weight: 700 !important;
      letter-spacing: 0.06em !important;
      text-transform: uppercase !important;
      margin-bottom: 4px !important;
    }
    div[data-baseweb="select"] > div {
      min-height: 56px !important;
      height: 56px !important;
      background: linear-gradient(90deg, #1A2235 0%, #232E45 100%) !important;
      border: 1.5px solid #3A4A6B !important;
      border-radius: 50px !important;
      color: #E8EEFF !important;
      display: flex !important;
      align-items: center !important;
      padding: 0 20px !important;
      font-weight: 600 !important;
      font-size: 0.95em !important;
      box-shadow: 0 4px 16px rgba(0,0,0,0.4) !important;
      transition: all 0.2s ease !important;
    }
    div[data-baseweb="select"] > div:hover {
      border-color: #5B6EAE !important;
      background: linear-gradient(90deg, #1E2A40 0%, #2A3652 100%) !important;
      box-shadow: 0 4px 20px rgba(59,130,246,0.2) !important;
    }
    div[data-baseweb="select"] svg {
      fill: #7B8EC8 !important;
    }
    [data-baseweb="popover"] ul {
      background: #1A2235 !important;
      border: 1px solid #3A4A6B !important;
      border-radius: 16px !important;
      overflow: hidden !important;
    }
    [data-baseweb="popover"] li {
      color: #E8EEFF !important;
      font-weight: 600 !important;
    }
    [data-baseweb="popover"] li:hover {
      background: #2A3652 !important;
    }

    .pill-card {
      display: flex;
      align-items: center;
      height: 56px;
      border-radius: 50px;
      box-shadow: 0 5px 22px rgba(0,0,0,0.4);
      font-weight: 700;
      font-size: 0.82em;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      padding-right: 20px;
      background: linear-gradient(90deg, #1A2235 0%, #232E45 100%);
      border: 1.5px solid #3A4A6B;
    }
    .pill-card-icon {
      display: flex;
      align-items: center;
      justify-content: center;
      min-width: 46px;
      height: 46px;
      margin: 5px 0 5px 5px;
      background: rgba(255,255,255,0.08);
      border-radius: 50%;
      font-size: 1.25em;
      border: 1.5px solid rgba(255,255,255,0.15);
      flex-shrink: 0;
    }
    .pill-card-text {
      padding-left: 14px;
      color: #7B8EC8;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      line-height: 1.15;
    }
    .pill-card-value {
      font-size: 0.95em;
      font-weight: 600;
      letter-spacing: 0;
      text-transform: none;
      color: #E8EEFF;
      margin-top: 2px;
    }
    .pill-card-warning { border-left: 4px solid #D97706 !important; }
    .pill-card-success { border-left: 4px solid #10B981 !important; }
    .pill-card-danger  { border-left: 4px solid #EF4444 !important; }
    .pill-card-info    { border-left: 4px solid #3B82F6 !important; }
    .pill-card-warning .pill-card-text { color: #FCD34D; }
    .pill-card-success .pill-card-text { color: #6EE7B7; }
    .pill-card-danger  .pill-card-text { color: #FCA5A5; }
    .pill-card-info    .pill-card-text { color: #93C5FD; }

    [data-testid="stHeader"] {
      display: none;
    }
    .block-container {
      padding-top: 1rem;
    }

    .stTextArea textarea {
        font-family: 'Courier New', Courier, monospace;
        font-size: 0.85em;
    }

    .toggle-title {
        font-size: 1.1em;
        font-weight: bold;
        color: white;
        margin-bottom: 2px;
    }
    .toggle-description {
        font-size: 0.85em;
        color: #aaa;
        line-height: 1.2;
    }
    .toggle-container {
        padding: 10px 0;
        border-bottom: 1px solid #222;
    }
    </style>
  """, unsafe_allow_html=True)

  st.title("Openpilot Dashboard")

  components.html("""
    <script>
    (function() {
      var doc = window.parent.document;
      function patchSelectInputs() {
        doc.querySelectorAll('[data-baseweb="select"] input').forEach(function(el) {
          if (el.getAttribute('inputmode') === 'none') return;
          el.setAttribute('inputmode', 'none');
        });
      }
      patchSelectInputs();
      new MutationObserver(patchSelectInputs).observe(doc.body, { childList: true, subtree: true });
    })();
    </script>
  """, height=0)

  tabs = st.tabs(["🚀 Functions", "⚙️ Toggles", "📋 Logs", "📂 Realdata", "📺 Terminal", "📷 Camera"])

  with tabs[0]:
    col_m, col_c, col_b = st.columns(3)

    with col_m:
      manufacturers = ["[ Not Selected ]", "HYUNDAI", "KIA", "GENESIS"]
      current_m = params.get("SelectedManufacturer") or manufacturers[0]
      m_idx = manufacturers.index(current_m) if current_m in manufacturers else 0
      selected_m = st.selectbox("🌐 Manufacturer", manufacturers, index=m_idx)

      if selected_m != current_m:
        if selected_m == "[ Not Selected ]":
          params.remove("SelectedManufacturer")
          params.remove("SelectedCar")
        else:
          params.put("SelectedManufacturer", selected_m)
          mapping = {"HYUNDAI": "CarList_Hyundai", "KIA": "CarList_Kia", "GENESIS": "CarList_Genesis"}
          src = f"{BASE_PATH}/{mapping.get(selected_m)}"
          if Path(src).exists():
            shutil.copy2(src, CAR_LIST_PATH)
        st.rerun()

    with col_c:
      car_list = ["[ Not Selected ]"] + get_list_from_file(CAR_LIST_PATH)
      current_c = params.get("SelectedCar") or car_list[0]
      c_idx = car_list.index(current_c) if current_c in car_list else 0
      selected_c = st.selectbox("🚗 Car Model", car_list, index=c_idx)

      if selected_c != current_c:
        if selected_c == "[ Not Selected ]":
          params.remove("SelectedCar")
        else:
          params.put("SelectedCar", selected_c)
        st.rerun()

    with col_b:
      branch_list = ["[ Not Selected ]"] + get_list_from_file(BRANCH_LIST_PATH)
      current_b = params.get("SelectedBranch") or branch_list[0]
      b_idx = branch_list.index(current_b) if current_b in branch_list else 0
      selected_b = st.selectbox("🌿 Git Branch", branch_list, index=b_idx)

      if selected_b != current_b:
        if selected_b == "[ Not Selected ]":
          params.remove("SelectedBranch")
        else:
          params.put("SelectedBranch", selected_b)
        st.rerun()

    row1_col1, row1_col2 = st.columns([1, 2], vertical_alignment="center")
    with row1_col1:
      st.markdown('<div id="btn_marker_blue_check"></div>', unsafe_allow_html=True)
      if st.button("Check Updates", use_container_width=True):
        run_script("Commit Check", f"{SCRIPTS_PATH}/commit_compare.sh")
        st.rerun()
    with row1_col2:
      commit_output = params.get("CommitCompare")
      commit_info = commit_output or "Check required"
      if commit_output and " == " in commit_output:
        card_type, icon = "pill-card-success", "✅"
      elif commit_output and " != " in commit_output:
        card_type, icon = "pill-card-danger", "⚠️"
      else:
        card_type, icon = "pill-card-warning", "🔍"
      st.markdown(f"""
        <div class="pill-card {card_type}">
          <div class="pill-card-icon">{icon}</div>
          <div class="pill-card-text">UPDATE STATUS
            <div class="pill-card-value">{commit_info}</div>
          </div>
        </div>""", unsafe_allow_html=True)

    if " == " not in (params.get("CommitCompare") or "") and params.get("CommitCompare"):
      pull_col1, pull_col2 = st.columns([1, 2], vertical_alignment="center")
      with pull_col1:
        st.markdown('<div id="btn_marker_blue_pull"></div>', unsafe_allow_html=True)
        if st.button("Git Pull Now", use_container_width=True):
          run_script("Git Pull", f"{SCRIPTS_PATH}/gitpull.sh")
          st.rerun()
      with pull_col2:
        st.markdown(f"""
          <div class="pill-card pill-card-warning">
            <div class="pill-card-icon">⚠️</div>
            <div class="pill-card-text">NEW UPDATE AVAILABLE
              <div class="pill-card-value">Please pull the latest changes.</div>
            </div>
          </div>""", unsafe_allow_html=True)

    row3_col1, row3_col2 = st.columns([1, 2], vertical_alignment="center")
    with row3_col1:
      st.markdown('<div id="btn_marker_warning_cal"></div>', unsafe_allow_html=True)
      if st.button("Reset Calibration", use_container_width=True):
        reset_calibration()
        st.session_state["script_log"] = "[Reset Calibration] Completed."
        st.session_state["script_ok"] = True
        st.rerun()
    with row3_col2:
      device_position = params.get("DevicePosition") or "--"
      st.markdown(f"""
        <div class="pill-card pill-card-info">
          <div class="pill-card-icon">📍</div>
          <div class="pill-card-text">DEVICE POSITION
            <div class="pill-card-value">{device_position}</div>
          </div>
        </div>""", unsafe_allow_html=True)

    reboot_col, _ = st.columns([1, 2])
    with reboot_col:
      st.markdown('<div id="btn_marker_danger_reboot"></div>', unsafe_allow_html=True)
      if st.button("Reboot", use_container_width=True):
        st.session_state["script_log"] = "[Reboot] Rebooting device..."
        st.session_state["script_ok"] = True
        subprocess.Popen(["sudo", "reboot"])

    if "script_log" in st.session_state:
      ok = st.session_state.get("script_ok", True)
      icon = "✅" if ok else "❌"
      extra_class = "log-success" if ok else "log-error"
      st.markdown(
        f'<div class="log-output-box {extra_class}">{icon} {st.session_state["script_log"]}</div>',
        unsafe_allow_html=True
      )

  with tabs[1]:
    toggle_items = [
      ("PcmCruiseEnable", "PcmCruise", "Change the openpilot cruise engagement. use the PcmCruise method"),
      ("CruiseStateControl", "Cruise State Controls", "Openpilot controls cruise on/off, set speed"),
      ("IsHda2", "CANFD Car HDA2", "Highway Drive Assist 2, turn it on"),
      ("CameraSccEnable", "CameraSCC",
       "HDA1 CameraSCC CAR, HDA2 Connect the ADAS ECAN line to CAMERA modify, turn it on"),
      ("RadarTrackEnable", "Enable Radar Track use", "Enable Radar Track use (disable AEB)"),
      ("DriverCameraOnReverse", "Driver Camera On Reverse", "Displays the driver camera when in reverse"),
      ("DriverCameraHardwareMissing", "Driver Camera Hardware Missing",
       "If there is a problem with the driver camera hardware, drive without the driver camera"),
    ]

    for key, _, _ in toggle_items:
      if f"tog_{key}" not in st.session_state:
        st.session_state[f"tog_{key}"] = params.get_bool(key)

    for key, label, desc in toggle_items:
      val = st.session_state[f"tog_{key}"]
      state_str = "on" if val else "off"

      t_col1, t_col2 = st.columns([0.1, 0.9], vertical_alignment="center")

      with t_col1:
        st.markdown(
          f'<div id="toggle_wrap_{state_str}_{key}"></div>',
          unsafe_allow_html=True
        )
        if st.button(" ", key=f"btn_tog_{key}"):
          new_val = not val
          st.session_state[f"tog_{key}"] = new_val
          params.put_bool(key, new_val)
          st.toast(f"{label} {'ON' if new_val else 'OFF'}")
          st.rerun()

      with t_col2:
        st.markdown(f"""
        <div class="toggle-container">
            <div class="toggle-title">{label}</div>
            <div class="toggle-description">{desc}</div>
        </div>
        """, unsafe_allow_html=True)

  with tabs[2]:
    log_files = {
      "CAN Missing": "/data/can_missing.log",
      "CAN Timeout": "/data/can_timeout.log",
      "Tmux Error": "/data/tmux_error.log",
      "Tmux Console": "TMUX_CONSOLE"
    }

    l_col1, l_col2, l_col3 = st.columns([3, 1, 1], vertical_alignment="bottom")

    with l_col1:
      sel_log_name = st.selectbox("Select Log File", list(log_files.keys()), label_visibility="visible")
      sel_log_path = log_files[sel_log_name]

    with l_col2:
      st.markdown('<div id="btn_marker_default_view"></div>', unsafe_allow_html=True)
      if st.button("View", key="btn_view_file", use_container_width=True):
        st.session_state["log_view_error"] = None
        st.session_state.log_out = ""

        if sel_log_path == "TMUX_CONSOLE":
          capture_cmd = "tmux capture-pane -p -t 0 -S -500 > /data/tmux_console.log"
          subprocess.run(capture_cmd, shell=True)

          console_log_path = Path("/data/tmux_console.log")
          if console_log_path.exists():
            st.session_state.log_out = console_log_path.read_text()
          else:
            st.session_state["log_view_error"] = "Failed to capture tmux console."
        else:
          if Path(sel_log_path).exists():
            st.session_state.log_out = Path(sel_log_path).read_text()
          else:
            st.session_state["log_view_error"] = "File not found."

    with l_col3:
      st.markdown('<div id="btn_marker_success_upload"></div>', unsafe_allow_html=True)
      if st.button("Upload", key="btn_upload_file", use_container_width=True):
        if sel_log_path == "TMUX_CONSOLE":
          console_log_path = Path("/data/tmux_console.log")
          if not console_log_path.exists():
            subprocess.run("tmux capture-pane -p -t 0 -S -500 > /data/tmux_console.log", shell=True)

          run_script("Console Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=["tmux_console.log"])
        else:
          run_script("Log Upload", f"{SCRIPTS_PATH}/log_upload.sh",
                     args=[sel_log_name])

    content = st.session_state.get("log_out", "")
    err_msg = st.session_state.get("log_view_error")

    if err_msg:
      st.markdown(f'<div class="log-viewer" style="border-left-color:#EF4444; color:#FCA5A5;">❌ {err_msg}</div>', unsafe_allow_html=True)
      st.markdown('<div class="log-statusbar log-error">⚠️ 파일을 불러오지 못했습니다.</div>', unsafe_allow_html=True)
    else:
      display  = html_lib.escape(content) if content else "로그 파일을 선택한 후 View 버튼을 눌러주세요."
      lines    = len(content.splitlines()) if content else 0
      chars    = len(content) if content else 0
      scroll_js = "<script>var v=document.getElementById('logViewer');if(v)v.scrollTop=v.scrollHeight;</script>" if content else ""
      st.markdown(
        f'<div class="log-viewer" id="logViewer">{display}</div>{scroll_js}',
        unsafe_allow_html=True
      )
      if content:
        st.markdown(
          f'<div class="log-statusbar">📄 {sel_log_name} &nbsp;|&nbsp; {lines} lines &nbsp;|&nbsp; {chars:,} chars</div>',
          unsafe_allow_html=True
        )
      else:
        st.markdown('<div class="log-statusbar">📂 파일 선택 후 View를 눌러주세요.</div>', unsafe_allow_html=True)

  with tabs[3]:
    if not REALDATA_PATH.exists():
      st.markdown('<div class="log-output-box log-error">❌ Path not found: /data/media/0/realdata</div>', unsafe_allow_html=True)
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
        st.markdown('<div class="log-output-box log-warn">⚠️ No uploadable routes found.</div>', unsafe_allow_html=True)
      else:
        sorted_routes = sorted(route_map.items(), key=lambda x: x[1]['mtime'], reverse=True)
        options = [f"[{datetime.fromtimestamp(v['mtime']).strftime('%Y-%m-%d %H:%M')}] {k} ({len(v['paths'])} segs)" for
                   k, v in sorted_routes]
        sel_route = st.selectbox("Select Route to Upload", options)
        st.markdown('<div id="btn_marker_success_route"></div>', unsafe_allow_html=True)
        if st.button("Route Upload", use_container_width=True):
          idx = options.index(sel_route)
          targets = sorted_routes[idx][1]['paths']
          cmd = ["bash", f"{SCRIPTS_PATH}/realdata_upload.sh"] + targets
          try:
            subprocess.Popen(cmd)
            st.markdown(f'<div class="log-output-box log-success">✅ Upload started in background! ({len(targets)} segments)\nPlease check the NAS or Tmux logs.</div>', unsafe_allow_html=True)
          except Exception as e:
            st.markdown(f'<div class="log-output-box log-error">❌ Failed to start upload: {e}</div>', unsafe_allow_html=True)

  with tabs[5]:
    camera_options = {
      "Road Camera": "road",
      "Driver Camera": "driver",
      "Wide Road Camera": "wideRoad"
    }

    c_col1, c_col2, c_col3 = st.columns([3, 1, 1], vertical_alignment="center")

    with c_col1:
      selected_cam = st.selectbox("Select Camera Source", list(camera_options.keys()), key="cam_source")
      stream_type = camera_options[selected_cam]

    with c_col2:
      st.markdown('<div id="btn_marker_success_start"></div>', unsafe_allow_html=True)
      if st.button("Start", key="btn_cam_start", use_container_width=True):
        st.session_state["cam_streaming"] = True

    with c_col3:
      st.markdown('<div id="btn_marker_danger_stop"></div>', unsafe_allow_html=True)
      if st.button("Stop", key="btn_cam_stop", use_container_width=True):
        st.session_state["cam_streaming"] = False

    # Default state
    if "cam_streaming" not in st.session_state:
      st.session_state["cam_streaming"] = False

    if st.session_state["cam_streaming"]:
      webrtc_html = f"""
            <html>
              <body style="background-color: #0B0E14; margin: 0; font-family: sans-serif;">

                <div style="position:relative; width:100%; height:430px;
                  background:#000;
                  border-radius:12px;
                  border: 1.5px solid #3A4A6B;
                  overflow:hidden;
                  box-shadow: 0 4px 16px rgba(0,0,0,0.4);">
                  <video id="video" autoplay playsinline muted controls
                    style="width:100%; height:100%; object-fit:contain; cursor:pointer;"></video>
                  <div id="status" style="
                    position:absolute; top:10px; right:12px;
                    color:#E8EEFF; background:rgba(0,0,0,0.65);
                    padding:4px 10px; border-radius:20px;
                    font-size:12px; font-weight:600; pointer-events:none;">
                    Initializing...
                  </div>
                </div>

                <div id="debug" style="
                  margin-top:10px;
                  background: linear-gradient(90deg, #1A2235, #232E45);
                  border: 1.5px solid #3A4A6B;
                  border-left: 4px solid #3B82F6;
                  border-radius:12px;
                  padding:10px 16px;
                  color:#93C5FD;
                  font-size:12px;
                  font-family:'Courier New', monospace;
                  white-space:pre;
                  min-height:40px;
                ">Waiting for stream stats...</div>

                <script>
                  async function start() {{
                    const video = document.getElementById('video');
                    const status = document.getElementById('status');
                    const debug = document.getElementById('debug');

                    const iceConfig = {{
                        iceServers: [],
                        sdpSemantics: "unified-plan",
                        iceCandidatePoolSize: 1
                    }};

                    const ip = window.location.hostname || window.parent.location.hostname;
                    const port = "5001";
                    const streamType = "{stream_type}";
                    let lastBytes = 0;
                    let lastTimestamp = 0;

                    video.addEventListener('click', () => {{
                        if (video.paused) {{
                            video.play().catch(console.error);
                            status.innerText = "Attempting to play...";
                        }}
                    }});

                    try {{
                      const pc = new RTCPeerConnection(iceConfig);

                      pc.addTransceiver('video', {{ direction: 'recvonly' }});

                      pc.ontrack = (event) => {{
                        console.log("Track received:", event.track.kind);
                        status.innerText = "● Stream Active (" + streamType + ")";
                        status.style.background = "rgba(16,185,129,0.75)";
                        video.srcObject = event.streams[0];
                        video.play().catch(e => {{
                            console.error("Autoplay failed:", e);
                            status.innerText = "▶ Click to Play";
                        }});
                      }};

                      setInterval(async () => {{
                        if(pc.connectionState === 'connected' || pc.iceConnectionState === 'connected') {{
                            const stats = await pc.getStats();
                            let foundVideo = false;
                            stats.forEach(report => {{
                                if(report.type === 'inbound-rtp' && report.kind === 'video') {{
                                    foundVideo = true;
                                    const now = report.timestamp;
                                    const bytes = report.bytesReceived;
                                    let bitrate = 0;
                                    if (lastTimestamp > 0) {{
                                        const duration = (now - lastTimestamp) / 1000;
                                        if (duration > 0) bitrate = ((bytes - lastBytes) * 8 / 1000) / duration;
                                    }}
                                    lastBytes = bytes;
                                    lastTimestamp = now;
                                    let codecInfo = report.codecId ? "CodecID: " + report.codecId : "";
                                    debug.innerText = `ICE: ${{pc.iceConnectionState}}  |  Connection: ${{pc.connectionState}}  |  Bytes: ${{bytes}}  |  Bitrate: ${{bitrate.toFixed(0)}} kbps  |  Frames: ${{report.framesDecoded}}  |  Lost: ${{report.packetsLost}}\\n${{codecInfo}}`;
                                }}
                            }});
                            if (!foundVideo) debug.innerText = `ICE: ${{pc.iceConnectionState}}  |  Connection: ${{pc.connectionState}}  |  Waiting for video...`;
                        }} else {{
                            debug.innerText = `ICE: ${{pc.iceConnectionState}}  |  Connection: ${{pc.connectionState}}`;
                        }}
                      }}, 1000);

                      const offer = await pc.createOffer();
                      await pc.setLocalDescription(offer);
                      status.innerText = "Gathering ICE...";
                      await new Promise((resolve) => {{
                          if (pc.iceGatheringState === 'complete') return resolve();
                          const checkState = () => {{
                              if (pc.iceGatheringState === 'complete') {{
                                  pc.removeEventListener('icegatheringstatechange', checkState);
                                  resolve();
                              }}
                          }};
                          pc.addEventListener('icegatheringstatechange', checkState);
                          setTimeout(resolve, 8000);
                      }});

                      const payload = {{
                        sdp: pc.localDescription.sdp,
                        cameras: [streamType],
                        bridge_services_in: [],
                        bridge_services_out: []
                      }};

                      status.innerText = "Handshaking...";
                      const response = await fetch(`http://${{ip}}:${{port}}/stream`, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(payload)
                      }});

                      if (!response.ok) {{
                        const errMsg = await response.text();
                        throw new Error("Server Error: " + response.status + " " + errMsg);
                      }}

                      const answer = await response.json();
                      await pc.setRemoteDescription(answer);

                    }} catch (e) {{
                      console.error(e);
                      status.innerText = "Error: " + e.message;
                      status.style.background = "rgba(239,68,68,0.8)";
                      debug.innerText = "Connection Error: " + e.message;
                      debug.style.borderLeftColor = "#EF4444";
                      debug.style.color = "#FCA5A5";
                    }}
                  }}

                  start();
                </script>
              </body>
            </html>
            """
      components.html(webrtc_html, height=530)
    else:
      st.markdown("""
        <div class="log-viewer" style="
          display:flex; flex-direction:column;
          justify-content:center; align-items:center;
          gap:12px; color:#3A4A6B;
        ">
          <div style="font-size:3em; filter:grayscale(1) opacity(0.3);">📷</div>
          <div style="font-size:0.9em; font-weight:600; letter-spacing:0.08em; text-transform:uppercase;">
            Select a camera source and press Start
          </div>
        </div>
      """, unsafe_allow_html=True)
      st.markdown('<div class="log-statusbar">📷 Camera &nbsp;|&nbsp; Stream Stopped</div>', unsafe_allow_html=True)

  with tabs[4]:
    term_view   = st.empty()
    term_status = st.empty()

    while True:
      content = get_tmux_capture()
      escaped = html_lib.escape(content)
      lines   = len(content.splitlines())
      is_err  = content.startswith("Error capturing tmux") or content.startswith("Tmux Session not found")

      if is_err:
        term_view.markdown(
          f'<div class="log-viewer" style="border-left-color:#EF4444; color:#FCA5A5;">❌ {escaped}</div>',
          unsafe_allow_html=True
        )
        term_status.markdown(
          '<div class="log-statusbar log-error">⚠️ tmux 세션을 찾을 수 없습니다. openpilot이 실행 중인지 확인하세요.</div>',
          unsafe_allow_html=True
        )
      else:
        now_str = datetime.now().strftime("%H:%M:%S")
        term_view.markdown(
          f'<div class="log-viewer">{escaped}</div>',
          unsafe_allow_html=True
        )
        term_status.markdown(
          f'<div class="log-statusbar">🖥️ Tmux Session &nbsp;|&nbsp; {lines} lines &nbsp;|&nbsp; 🔄 Last updated: {now_str}</div>',
          unsafe_allow_html=True
        )
      time.sleep(1)


if __name__ == "__main__":
  if check_password():
    main()
