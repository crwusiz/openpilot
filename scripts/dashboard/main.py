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

ACCESS_PASSWORD = "comma"

# ── 인증 ──────────────────────────────────────────────────
def check_password() -> bool:
  if st.session_state.get("password_correct", False):
    return True

  # URL 파라미터 방식: http://ip:port/?pwd=comma or http://hostname/?pwd=comma (hostname ex comma-12345678) 12345678 is comma device serial
  try:
    if st.query_params.get("pwd") == ACCESS_PASSWORD:
      st.session_state["password_correct"] = True
      return True
  except Exception:
    pass

  # 입력 폼 방식
  st.title("🔒 Access Restricted")
  pwd = st.text_input("Enter Dashboard Password", type="password")
  if st.button("Login"):
    if pwd == ACCESS_PASSWORD:
      st.session_state["password_correct"] = True
      st.rerun()
    else:
      st.error("Incorrect Password")
  return False


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
  if check_password():
    main()
