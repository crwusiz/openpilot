import sys
import subprocess
import logging
import html as html_lib
from datetime import datetime
import streamlit as st

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

from utils import get_tmux_capture


def _render_content():
  """터미널 내용 1회 렌더"""
  content = get_tmux_capture()
  lines   = len(content.splitlines())
  is_err  = (
    content.startswith("Error capturing tmux") or
    content.startswith("Tmux Session not found")
  )

  if is_err:
    escaped = html_lib.escape(content) # 에러 메시지는 단순 이스케이프
    st.markdown(
      f'<div class="log-viewer" style="border-left-color:#EF4444; color:#FCA5A5;">❌ {escaped}</div>',
      unsafe_allow_html=True
    )
    st.markdown(
      '<div class="log-statusbar log-error">⚠️ tmux 세션을 찾을 수 없습니다. openpilot이 실행 중인지 확인하세요.</div>',
      unsafe_allow_html=True
    )
  else:
    # ANSI 코드를 HTML로 변환하는 로직
    conv = Ansi2HTMLConverter(inline=True, dark_bg=True)
    # full=False로 설정하여 HTML 뼈대(body, head 등) 없이 내용물만 변환합니다.
    colored_html = conv.convert(content, full=False)

    now_str = datetime.now().strftime("%H:%M:%S")
    st.markdown(
      f'<div class="log-viewer">{colored_html}</div>',
      unsafe_allow_html=True
    )
    st.markdown(
      f'<div class="log-statusbar">🖥️ Tmux Session &nbsp;|&nbsp; {lines} lines &nbsp;|&nbsp; 🔄 Last updated: {now_str}</div>',
      unsafe_allow_html=True
    )

# 하단 파편화/수동 갱신 로직은 동일하게 유지...
try:
  @st.fragment(run_every=1)
  def _terminal_fragment():
    _render_content()

  def render():
    _terminal_fragment()

except AttributeError:
  def render():
    _render_content()
    if st.button("🔄 Refresh", key="btn_terminal_refresh"):
      st.rerun()
