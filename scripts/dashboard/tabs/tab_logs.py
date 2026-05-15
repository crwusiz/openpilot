import sys
import subprocess
import logging
import html as html_lib
from pathlib import Path
import streamlit as st

try:
    from ansi2html import Ansi2HTMLConverter
except ImportError:
    logging.getLogger("tab_logs").warning("ansi2html not found. Remounting filesystem to install...")
    subprocess.check_call(["sudo", "mount", "-o", "remount,rw", "/"])
    subprocess.check_call(["sudo", sys.executable, "-m", "pip", "install", "ansi2html"])
    try:
        subprocess.check_call(["sudo", "mount", "-o", "remount,ro", "/"])
    except subprocess.CalledProcessError:
        pass
    from ansi2html import Ansi2HTMLConverter

from utils import SCRIPTS_PATH, run_script

LOG_FILES = {
  "CAN Missing":  "/data/can_missing.log",
  "CAN Timeout":  "/data/can_timeout.log",
  "Tmux Error":   "/data/tmux_error.log",
  "Tmux Console": "TMUX_CONSOLE",
}

def render():
  l_col1, l_col2, l_col3 = st.columns([3, 1, 1], vertical_alignment="bottom")

  with l_col1:
    sel_log_name = st.selectbox("Select Log File", list(LOG_FILES.keys()))
    sel_log_path = LOG_FILES[sel_log_name]

  with l_col2:
    st.markdown('<div id="btn_marker_default_view"></div>', unsafe_allow_html=True)
    if st.button("View", key="btn_view_file", use_container_width=True):
      st.session_state["log_view_error"] = None
      st.session_state["log_out"]        = ""

      if sel_log_path == "TMUX_CONSOLE":
        subprocess.run("tmux capture-pane -pe -t 0 -S -500 > /data/tmux_console.log", shell=True)
        p = Path("/data/tmux_console.log")
        st.session_state["log_out"] = p.read_text() if p.exists() else ""
        if not p.exists():
          st.session_state["log_view_error"] = "Failed to capture tmux console."
      else:
        p = Path(sel_log_path)
        st.session_state["log_out"] = p.read_text() if p.exists() else ""
        if not p.exists():
          st.session_state["log_view_error"] = "File not found."

  with l_col3:
    st.markdown('<div id="btn_marker_success_upload"></div>', unsafe_allow_html=True)
    if st.button("Upload", key="btn_upload_file", use_container_width=True):
      if sel_log_path == "TMUX_CONSOLE":
        if not Path("/data/tmux_console.log").exists():
          subprocess.run("tmux capture-pane -pe -t 0 -S -500 > /data/tmux_console.log", shell=True)
        run_script("Console Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=["tmux_console.log"])
      else:
        run_script("Log Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=[sel_log_name])

  # ── 뷰어 + 상태바 ────────────────────────────────────
  content = st.session_state.get("log_out", "")
  err_msg = st.session_state.get("log_view_error")

  if err_msg:
    st.markdown(
      f'<div class="log-viewer" style="border-left-color:#EF4444; color:#FCA5A5;">❌ {err_msg}</div>',
      unsafe_allow_html=True
    )
    st.markdown('<div class="log-statusbar log-error">⚠️ 파일을 불러오지 못했습니다.</div>', unsafe_allow_html=True)
  else:
    if content:
      conv = Ansi2HTMLConverter(inline=True, dark_bg=True)
      display = conv.convert(content, full=False)
    else:
      display = "로그 파일을 선택한 후 View 버튼을 눌러주세요."

    lines     = len(content.splitlines()) if content else 0
    chars     = len(content) if content else 0
    scroll_js = "<script>var v=document.getElementById('logViewer');if(v)v.scrollTop=v.scrollHeight;</script>" if content else ""
    st.markdown(f'<div class="log-viewer" id="logViewer">{display}</div>{scroll_js}', unsafe_allow_html=True)

    if content:
      st.markdown(
        f'<div class="log-statusbar">📄 {sel_log_name} &nbsp;|&nbsp; {lines} lines &nbsp;|&nbsp; {chars:,} chars</div>',
        unsafe_allow_html=True
      )
    else:
      st.markdown('<div class="log-statusbar">📂 파일 선택 후 View를 눌러주세요.</div>', unsafe_allow_html=True)
