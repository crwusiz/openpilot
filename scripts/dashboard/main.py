import streamlit as st
import styles

from tabs import (
  tab_functions,
  tab_toggles,
  tab_logs,
  tab_realdata,
  tab_terminal,
  tab_camera,
)

# ── 메인 ──────────────────────────────────────────────────
def main():
  st.set_page_config(
    page_title="Openpilot Dashboard",
    layout="wide",
    initial_sidebar_state="collapsed",
  )

  styles.apply()          # CSS + 태블릿 키보드 차단 JS

  st.title("Openpilot Dashboard")

  tabs = st.tabs([
    "🚀 Functions",
    "⚙️ Toggles",
    "📋 Logs",
    "📂 Realdata",
    "📷 Camera",
    "📺 Terminal",
  ])

  with tabs[0]: tab_functions.render()
  with tabs[1]: tab_toggles.render()
  with tabs[2]: tab_logs.render()
  with tabs[3]: tab_realdata.render()
  with tabs[4]: tab_camera.render()
  with tabs[5]: tab_terminal.render()


if __name__ == "__main__":
  main()
