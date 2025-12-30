import streamlit as st
import subprocess
import shutil
import os
import time
from datetime import datetime
from pathlib import Path

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
  with st.status(f"Executing {name}...") as status:
    try:
      cmd = ["bash", path] if path.endswith('.sh') else ["python3", path]
      if args: cmd += args
      res = subprocess.run(cmd, capture_output=True, text=True)
      if res.stdout: st.code(res.stdout)
      if res.stderr: st.error(res.stderr)
      status.update(label=f"{name} Completed", state="complete")
      return res.returncode
    except Exception as e:
      st.error(f"Error: {e}")
      status.update(label="Failed", state="error")
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
  st.set_page_config(page_title="Openpilot Dashboard by crwusiz", layout="wide", initial_sidebar_state="collapsed")

  st.markdown("""
    <style>
    .stApp { background-color: #0B0E14; }
    .stButton>button {
      border: 1px solid #2C2CE2;
      background-color: #161B22;
      color: white;
      font-weight: bold;
      height: 3.5em;
      transition: 0.3s;
    }
    .stButton>button:hover { background-color: #2C2CE2; border-color: #ffffff; }
    .metric-card {
      background-color: #161B22;
      padding: 12px;
      border-radius: 8px;
      border-left: 4px solid #2C2CE2;
      margin-bottom: 10px;
      font-size: 0.9em;
    }
    .metric-card-danger {
      background-color: #161B22;
      padding: 12px;
      border-radius: 8px;
      border-left: 4px solid rgb(201, 34, 49); /* DANGER Color */
      margin-bottom: 10px;
      font-size: 0.9em;
      color: rgb(201, 34, 49);
    }
    .metric-card-success {
      background-color: #161B22;
      padding: 12px;
      border-radius: 8px;
      border-left: 4px solid rgb(128, 216, 166); /* UP_TO_DATE Color */
      margin-bottom: 10px;
      font-size: 0.9em;
      color: rgb(128, 216, 166);
    }
    [data-testid="stHeader"] { background: rgba(0,0,0,0); }
    .toggle-description {
      font-size: 0.8em;
      color: #888;
      margin-top: -10px;
      margin-bottom: 10px;
      margin-left: 0px;
    }
    </style>
  """, unsafe_allow_html=True)

  st.title("Openpilot Dashboard by crwusiz")

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

  st.divider()

  tabs = st.tabs(["🚀 Functions", "⚙️ Toggles", "📋 Logs", "📂 Realdata", "📺 Terminal"])

  with tabs[0]:
    st.subheader("System Maintenance")

    row1_col1, row1_col2 = st.columns([1, 2])
    with row1_col1:
      if st.button("🔍 Check Updates", use_container_width=True):
        run_script("Commit Check", f"{SCRIPTS_PATH}/commit_compare.sh")
        st.rerun()
    with row1_col2:
      commit_output = params.get("CommitCompare")
      commit_info = commit_output or "Check required"

      card_class = "metric-card"

      if commit_output:
        if " == " in commit_output:
           card_class = "metric-card-success"
        elif " != " in commit_output:
           card_class = "metric-card-danger"

      st.markdown(f'<div class="{card_class}"><b>Update Status</b><br>{commit_info}</div>', unsafe_allow_html=True)

    row2_col1, row2_col2 = st.columns([1, 2])
    with row2_col1:
      if st.button("✨ Reset Calibration", use_container_width=True):
        if st.checkbox("Confirm Reset"):
          reset_calibration()
    with row2_col2:
      device_position = params.get("DevicePosition") or "--"
      st.markdown(f'<div class="metric-card"><b>Device Position</b><br>{device_position}</div>', unsafe_allow_html=True)

    if " == " not in (params.get("CommitCompare") or "") and params.get("CommitCompare"):
      st.warning("New Update Available!")
      if st.button("⬇️ Git Pull Now", type="primary", use_container_width=True):
        run_script("Git Pull", f"{SCRIPTS_PATH}/gitpull.sh")
        st.rerun()

    sub_col1, sub_col2 = st.columns([1, 2])
    with sub_col1:
      if st.button("📷 Camera View", use_container_width=True):
        run_script("Camera View", "/data/openpilot/selfdrive/ui/watch3.py")

      if st.button("🔴 REBOOT", type="secondary", use_container_width=True):
        if st.checkbox("Confirm Reboot"):
          st.error("Rebooting Device...")
          subprocess.run(["sudo", "reboot"])

  with tabs[1]:
    st.subheader("Parameter Configuration")
    toggle_items = [
      ("PcmCruiseEnable", "PcmCruise", "Change the openpilot cruise engagement. use the PcmCruise method"),
      ("CruiseStateControl", "Cruise State Controls", "Openpilot controls cruise on/off, set speed"),
      ("IsHda2", "CANFD Car HDA2", "Highway Drive Assist 2, turn it on"),
      ("CameraSccEnable", "CameraSCC", "HDA1 CameraSCC CAR, HDA2 Connect the ADAS ECAN line to CAMERA modify, turn it on"),
      ("RadarTrackEnable", "Enable Radar Track use", "Enable Radar Track use (disable AEB)"),
      ("DriverCameraOnReverse", "Driver Camera On Reverse", "Displays the driver camera when in reverse"),
      ("DriverCameraHardwareMissing", "Driver Camera Hardware Missing", "If there is a problem with the driver camera hardware, drive without the driver camera"),
    ]
    for key, label, desc in toggle_items:
      curr = params.get_bool(key)
      new = st.toggle(label, value=curr)
      if new != curr:
        params.put_bool(key, new)
        st.toast(f"{label} Changed")
      st.markdown(f'<div class="toggle-description">{desc}</div>', unsafe_allow_html=True)

  with tabs[2]:
    st.subheader("System Logs & Diagnostics")
    log_files = {
      "CAN Missing": "/data/can_missing.log",
      "CAN Timeout": "/data/can_timeout.log",
      "Tmux Error": "/data/tmux_error.log",
    }
    l_col1, l_col2 = st.columns([1, 2])
    with l_col1:
      sel_log = st.selectbox("Select Log File", list(log_files.keys()))
      if st.button("👁️ View Log File", use_container_width=True):
        path = log_files[sel_log]
        if Path(path).exists():
          st.session_state.log_out = Path(path).read_text()
        else: st.error("File not found.")
      if st.button("⬆️ Upload This Log", use_container_width=True):
        run_script("Log Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=[sel_log])
    with l_col2:
      content = st.session_state.get("log_out", "Select a log file to view.")
      st.text_area("Output Window", content, height=450)

  with tabs[3]:
    st.subheader("Realdata Route Upload")
    if not REALDATA_PATH.exists():
      st.warning("Path not found: /data/media/0/realdata")
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

      if not route_map:
        st.info("No uploadable routes found.")
      else:
        sorted_routes = sorted(route_map.items(), key=lambda x: x[1]['mtime'], reverse=True)[:10]
        options = [f"[{datetime.fromtimestamp(v['mtime']).strftime('%Y-%m-%d %H:%M')}] {k} ({len(v['paths'])} segs)" for k, v in sorted_routes]
        sel_route = st.selectbox("Select Route to Upload", options)
        if st.button("🚀 Selected Route Upload", use_container_width=True):
          idx = options.index(sel_route)
          targets = sorted_routes[idx][1]['paths']
          run_script("Realdata Upload", f"{SCRIPTS_PATH}/upload_realdata.sh", args=targets)

  with tabs[4]:
    st.subheader("📺 Real-time Terminal (tmux)")
    col_t1, col_t2 = st.columns([1, 4])
    with col_t1:
      live_view = st.toggle("Live Stream", value=False)
      if st.button("Manual Refresh", use_container_width=True): st.rerun()
    with col_t2:
      terminal_placeholder = st.empty()
      if live_view:
        while live_view:
          content = get_tmux_capture()
          terminal_placeholder.code(content, language="bash")
          time.sleep(1)
      else:
        content = get_tmux_capture()
        terminal_placeholder.code(content, language="bash")

if __name__ == "__main__":
  if check_password():
    main()
