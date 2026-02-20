import subprocess
import shutil
from pathlib import Path
import streamlit as st

from utils import params, get_list_from_file, run_script, reset_calibration, SCRIPTS_PATH

BASE_PATH        = "/data/params/crwusiz"
CAR_LIST_PATH    = f"{BASE_PATH}/CarList"
BRANCH_LIST_PATH = f"{BASE_PATH}/GitBranchList"

def render():
  # ── 드롭다운 행 ──────────────────────────────────────
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
      params.remove("SelectedCar") if selected_c == "[ Not Selected ]" else params.put("SelectedCar", selected_c)
      st.rerun()

  with col_b:
    branch_list = ["[ Not Selected ]"] + get_list_from_file(BRANCH_LIST_PATH)
    current_b = params.get("SelectedBranch") or branch_list[0]
    b_idx = branch_list.index(current_b) if current_b in branch_list else 0
    selected_b = st.selectbox("🌿 Git Branch", branch_list, index=b_idx)
    if selected_b != current_b:
      params.remove("SelectedBranch") if selected_b == "[ Not Selected ]" else params.put("SelectedBranch", selected_b)
      st.rerun()

  # ── Check Updates ────────────────────────────────────
  row1_col1, row1_col2 = st.columns([1, 2], vertical_alignment="center")
  with row1_col1:
    st.markdown('<div id="btn_marker_blue_check"></div>', unsafe_allow_html=True)
    if st.button("Check Updates", use_container_width=True):
      run_script("Commit Check", f"{SCRIPTS_PATH}/commit_compare.sh")
      st.rerun()
  with row1_col2:
    commit_output = params.get("CommitCompare")
    commit_info   = commit_output or "Check required"
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

  # ── Git Pull (업데이트 있을 때만) ─────────────────────
  if " == " not in (params.get("CommitCompare") or "") and params.get("CommitCompare"):
    pull_col1, pull_col2 = st.columns([1, 2], vertical_alignment="center")
    with pull_col1:
      st.markdown('<div id="btn_marker_blue_pull"></div>', unsafe_allow_html=True)
      if st.button("Git Pull Now", use_container_width=True):
        run_script("Git Pull", f"{SCRIPTS_PATH}/gitpull.sh")
        st.rerun()
    with pull_col2:
      st.markdown("""
        <div class="pill-card pill-card-warning">
          <div class="pill-card-icon">⚠️</div>
          <div class="pill-card-text">NEW UPDATE AVAILABLE
            <div class="pill-card-value">Please pull the latest changes.</div>
          </div>
        </div>""", unsafe_allow_html=True)

  # ── Reset Calibration ────────────────────────────────
  row3_col1, row3_col2 = st.columns([1, 2], vertical_alignment="center")
  with row3_col1:
    st.markdown('<div id="btn_marker_warning_cal"></div>', unsafe_allow_html=True)
    if st.button("Reset Calibration", use_container_width=True):
      reset_calibration()
      st.session_state["script_log"] = "[Reset Calibration] Completed."
      st.session_state["script_ok"]  = True
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

  # ── Reboot ───────────────────────────────────────────
  reboot_col, _ = st.columns([1, 2])
  with reboot_col:
    st.markdown('<div id="btn_marker_danger_reboot"></div>', unsafe_allow_html=True)
    if st.button("Reboot", use_container_width=True):
      st.session_state["script_log"] = "[Reboot] Rebooting device..."
      st.session_state["script_ok"]  = True
      subprocess.Popen(["sudo", "reboot"])

  # ── 스크립트 로그 출력 ───────────────────────────────
  if "script_log" in st.session_state:
    ok    = st.session_state.get("script_ok", True)
    icon  = "✅" if ok else "❌"
    cls   = "log-success" if ok else "log-error"
    st.markdown(
      f'<div class="log-output-box {cls}">{icon} {st.session_state["script_log"]}</div>',
      unsafe_allow_html=True
    )
