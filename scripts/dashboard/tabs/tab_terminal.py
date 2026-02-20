import html as html_lib
from datetime import datetime
import streamlit as st

from utils import get_tmux_capture


def _render_content():
  """터미널 내용 1회 렌더"""
  content = get_tmux_capture()
  escaped = html_lib.escape(content)
  lines   = len(content.splitlines())
  is_err  = (
    content.startswith("Error capturing tmux") or
    content.startswith("Tmux Session not found")
  )

  if is_err:
    st.markdown(
      f'<div class="log-viewer" style="border-left-color:#EF4444; color:#FCA5A5;">❌ {escaped}</div>',
      unsafe_allow_html=True
    )
    st.markdown(
      '<div class="log-statusbar log-error">⚠️ tmux 세션을 찾을 수 없습니다. openpilot이 실행 중인지 확인하세요.</div>',
      unsafe_allow_html=True
    )
  else:
    now_str = datetime.now().strftime("%H:%M:%S")
    st.markdown(
      f'<div class="log-viewer">{escaped}</div>',
      unsafe_allow_html=True
    )
    st.markdown(
      f'<div class="log-statusbar">🖥️ Tmux Session &nbsp;|&nbsp; {lines} lines &nbsp;|&nbsp; 🔄 Last updated: {now_str}</div>',
      unsafe_allow_html=True
    )


# st.fragment 지원 여부에 따라 자동 갱신 or 수동 갱신
try:
  @st.fragment(run_every=1)
  def _terminal_fragment():
    _render_content()

  def render():
    _terminal_fragment()

except AttributeError:
  # Streamlit < 1.33: st.fragment 미지원 → 수동 Refresh 버튼
  def render():
    _render_content()
    if st.button("🔄 Refresh", key="btn_terminal_refresh"):
      st.rerun()
