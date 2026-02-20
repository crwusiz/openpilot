import streamlit as st
import streamlit.components.v1 as components


def apply():
  """전체 대시보드 CSS 주입 + 태블릿 키보드 차단 JS"""
  st.markdown("""
    <style>
    .stApp { background-color: #0B0E14; }

    /* ══ PILL BUTTON 기본 ══ */
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

    /* ══ 버튼 색상별 마커 ══ */
    div:has([id^="btn_marker_blue_check"]) ~ div > div > button,
    div:has([id^="btn_marker_blue_check"]) + div > div > button {
      background: linear-gradient(90deg, #1E3A8A 0%, #3B82F6 100%) !important;
      box-shadow: 0 5px 22px rgba(59,130,246,0.5) !important;
    }
    div:has([id^="btn_marker_blue_check"]) ~ div > div > button::before,
    div:has([id^="btn_marker_blue_check"]) + div > div > button::before { content: '🔍' !important; }

    div:has([id^="btn_marker_blue_pull"]) ~ div > div > button,
    div:has([id^="btn_marker_blue_pull"]) + div > div > button {
      background: linear-gradient(90deg, #1E3A8A 0%, #3B82F6 100%) !important;
      box-shadow: 0 5px 22px rgba(59,130,246,0.5) !important;
    }
    div:has([id^="btn_marker_blue_pull"]) ~ div > div > button::before,
    div:has([id^="btn_marker_blue_pull"]) + div > div > button::before { content: '⬇' !important; }

    div:has([id^="btn_marker_success_upload"]) ~ div > div > button,
    div:has([id^="btn_marker_success_upload"]) + div > div > button {
      background: linear-gradient(90deg, #065F46 0%, #10B981 100%) !important;
      box-shadow: 0 5px 22px rgba(16,185,129,0.45) !important;
    }
    div:has([id^="btn_marker_success_upload"]) ~ div > div > button::before,
    div:has([id^="btn_marker_success_upload"]) + div > div > button::before { content: '⬆' !important; }

    div:has([id^="btn_marker_success_route"]) ~ div > div > button,
    div:has([id^="btn_marker_success_route"]) + div > div > button {
      background: linear-gradient(90deg, #065F46 0%, #10B981 100%) !important;
      box-shadow: 0 5px 22px rgba(16,185,129,0.45) !important;
    }
    div:has([id^="btn_marker_success_route"]) ~ div > div > button::before,
    div:has([id^="btn_marker_success_route"]) + div > div > button::before { content: '🚀' !important; }

    div:has([id^="btn_marker_success_start"]) ~ div > div > button,
    div:has([id^="btn_marker_success_start"]) + div > div > button {
      background: linear-gradient(90deg, #065F46 0%, #10B981 100%) !important;
      box-shadow: 0 5px 22px rgba(16,185,129,0.45) !important;
    }
    div:has([id^="btn_marker_success_start"]) ~ div > div > button::before,
    div:has([id^="btn_marker_success_start"]) + div > div > button::before { content: '▶' !important; }

    div:has([id^="btn_marker_danger_reboot"]) ~ div > div > button,
    div:has([id^="btn_marker_danger_reboot"]) + div > div > button {
      background: linear-gradient(90deg, #7F1D1D 0%, #EF4444 100%) !important;
      box-shadow: 0 5px 22px rgba(239,68,68,0.5) !important;
    }
    div:has([id^="btn_marker_danger_reboot"]) ~ div > div > button::before,
    div:has([id^="btn_marker_danger_reboot"]) + div > div > button::before { content: '⏻' !important; }

    div:has([id^="btn_marker_danger_stop"]) ~ div > div > button,
    div:has([id^="btn_marker_danger_stop"]) + div > div > button {
      background: linear-gradient(90deg, #7F1D1D 0%, #EF4444 100%) !important;
      box-shadow: 0 5px 22px rgba(239,68,68,0.5) !important;
    }
    div:has([id^="btn_marker_danger_stop"]) ~ div > div > button::before,
    div:has([id^="btn_marker_danger_stop"]) + div > div > button::before { content: '⏹' !important; }

    div:has([id^="btn_marker_warning_cal"]) ~ div > div > button,
    div:has([id^="btn_marker_warning_cal"]) + div > div > button {
      background: linear-gradient(90deg, #78350F 0%, #F59E0B 100%) !important;
      box-shadow: 0 5px 22px rgba(245,158,11,0.45) !important;
    }
    div:has([id^="btn_marker_warning_cal"]) ~ div > div > button::before,
    div:has([id^="btn_marker_warning_cal"]) + div > div > button::before { content: '✦' !important; }

    div:has([id^="btn_marker_default_view"]) ~ div > div > button::before,
    div:has([id^="btn_marker_default_view"]) + div > div > button::before { content: '👁' !important; }

    /* ══ TOGGLE 버튼 ══ */
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
    div:has([id^="toggle_wrap_"]) + div > div > button:hover { transform: none !important; filter: none !important; }
    div:has([id^="toggle_wrap_"]) ~ div > div > button::before,
    div:has([id^="toggle_wrap_"]) + div > div > button::before {
      content: '' !important;
      position: absolute !important;
      width: 24px !important; height: 24px !important;
      background: white !important;
      border-radius: 50% !important;
      border: none !important;
      top: 4px !important;
      transform: none !important;
      font-size: 0 !important; line-height: 0 !important;
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
    div:has([id^="toggle_wrap_off_"]) ~ div > div > button,
    div:has([id^="toggle_wrap_off_"]) + div > div > button { background: #E03535 !important; }
    div:has([id^="toggle_wrap_off_"]) ~ div > div > button:hover,
    div:has([id^="toggle_wrap_off_"]) + div > div > button:hover { background: #E03535 !important; transform: none !important; }
    div:has([id^="toggle_wrap_off_"]) ~ div > div > button::before,
    div:has([id^="toggle_wrap_off_"]) + div > div > button::before { left: 4px !important; }
    div:has([id^="toggle_wrap_off_"]) ~ div > div > button::after,
    div:has([id^="toggle_wrap_off_"]) + div > div > button::after {
      content: 'OFF' !important; right: 7px !important; left: auto !important;
    }
    div:has([id^="toggle_wrap_on_"]) ~ div > div > button,
    div:has([id^="toggle_wrap_on_"]) + div > div > button { background: #10B981 !important; }
    div:has([id^="toggle_wrap_on_"]) ~ div > div > button:hover,
    div:has([id^="toggle_wrap_on_"]) + div > div > button:hover { background: #10B981 !important; transform: none !important; }
    div:has([id^="toggle_wrap_on_"]) ~ div > div > button::before,
    div:has([id^="toggle_wrap_on_"]) + div > div > button::before { left: 42px !important; }
    div:has([id^="toggle_wrap_on_"]) ~ div > div > button::after,
    div:has([id^="toggle_wrap_on_"]) + div > div > button::after {
      content: 'ON' !important; left: 9px !important; right: auto !important;
    }

    /* ══ Selectbox ══ */
    div[data-baseweb="select"] input {
      caret-color: transparent !important;
      user-select: none !important;
    }
    div[data-baseweb="select"] input:focus { outline: none !important; box-shadow: none !important; }
    [data-testid="stSelectbox"] label {
      color: #7B8EC8 !important;
      font-size: 0.78em !important;
      font-weight: 700 !important;
      letter-spacing: 0.06em !important;
      text-transform: uppercase !important;
      margin-bottom: 4px !important;
    }
    div[data-baseweb="select"] > div {
      min-height: 56px !important; height: 56px !important;
      background: linear-gradient(90deg, #1A2235 0%, #232E45 100%) !important;
      border: 1.5px solid #3A4A6B !important;
      border-radius: 50px !important;
      color: #E8EEFF !important;
      display: flex !important; align-items: center !important;
      padding: 0 20px !important;
      font-weight: 600 !important; font-size: 0.95em !important;
      box-shadow: 0 4px 16px rgba(0,0,0,0.4) !important;
      transition: all 0.2s ease !important;
    }
    div[data-baseweb="select"] > div:hover {
      border-color: #5B6EAE !important;
      background: linear-gradient(90deg, #1E2A40 0%, #2A3652 100%) !important;
      box-shadow: 0 4px 20px rgba(59,130,246,0.2) !important;
    }
    div[data-baseweb="select"] svg { fill: #7B8EC8 !important; }
    [data-baseweb="popover"] ul {
      background: #1A2235 !important;
      border: 1px solid #3A4A6B !important;
      border-radius: 16px !important;
      overflow: hidden !important;
    }
    [data-baseweb="popover"] li { color: #E8EEFF !important; font-weight: 600 !important; }
    [data-baseweb="popover"] li:hover { background: #2A3652 !important; }

    /* ══ PILL CARD (상태 카드) ══ */
    .pill-card {
      display: flex; align-items: center;
      height: 56px; border-radius: 50px;
      box-shadow: 0 5px 22px rgba(0,0,0,0.4);
      font-weight: 700; font-size: 0.82em;
      letter-spacing: 0.05em; text-transform: uppercase;
      padding-right: 20px;
      background: linear-gradient(90deg, #1A2235 0%, #232E45 100%);
      border: 1.5px solid #3A4A6B;
    }
    .pill-card-icon {
      display: flex; align-items: center; justify-content: center;
      min-width: 46px; height: 46px;
      margin: 5px 0 5px 5px;
      background: rgba(255,255,255,0.08);
      border-radius: 50%; font-size: 1.25em;
      border: 1.5px solid rgba(255,255,255,0.15);
      flex-shrink: 0;
    }
    .pill-card-text {
      padding-left: 14px; color: #7B8EC8;
      white-space: nowrap; overflow: hidden;
      text-overflow: ellipsis; line-height: 1.15;
    }
    .pill-card-value {
      font-size: 0.95em; font-weight: 600;
      letter-spacing: 0; text-transform: none;
      color: #E8EEFF; margin-top: 2px;
    }
    .pill-card-warning { border-left: 4px solid #D97706 !important; }
    .pill-card-success { border-left: 4px solid #10B981 !important; }
    .pill-card-danger  { border-left: 4px solid #EF4444 !important; }
    .pill-card-info    { border-left: 4px solid #3B82F6 !important; }
    .pill-card-warning .pill-card-text { color: #FCD34D; }
    .pill-card-success .pill-card-text { color: #6EE7B7; }
    .pill-card-danger  .pill-card-text { color: #FCA5A5; }
    .pill-card-info    .pill-card-text { color: #93C5FD; }

    /* ══ 로그 출력 영역 ══ */
    .log-output-box {
      background: linear-gradient(90deg, #0D1117 0%, #161B22 100%);
      border: 1.5px solid #3A4A6B;
      border-left: 4px solid #3B82F6;
      border-radius: 16px;
      padding: 16px 20px;
      font-family: 'Courier New', Courier, monospace;
      font-size: 0.82em; color: #BCC4E0;
      white-space: pre-wrap; word-break: break-all;
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
      font-size: 0.8em; color: #BCC4E0;
      white-space: pre-wrap; word-break: break-all;
      line-height: 1.6;
      height: 430px; overflow-y: auto;
      box-shadow: inset 0 2px 12px rgba(0,0,0,0.5);
      margin-top: 8px; scroll-behavior: smooth;
    }
    .log-statusbar {
      background: linear-gradient(90deg, #1A2235, #232E45);
      border: 1.5px solid #3A4A6B;
      border-left: 4px solid #3B82F6;
      border-radius: 12px;
      padding: 10px 16px;
      font-family: 'Courier New', Courier, monospace;
      font-size: 0.78em; color: #93C5FD;
      margin-top: 10px; min-height: 40px;
    }
    .log-output-box.log-error,  .log-statusbar.log-error  { border-left-color: #EF4444; color: #FCA5A5; }
    .log-output-box.log-success,.log-statusbar.log-success { border-left-color: #10B981; color: #6EE7B7; }
    .log-output-box.log-warn,   .log-statusbar.log-warn    { border-left-color: #D97706; color: #FCD34D; }

    /* ══ 기타 ══ */
    [data-testid="stHeader"] { display: none; }
    .block-container { padding-top: 1rem; }
    .stTextArea textarea { font-family: 'Courier New', Courier, monospace; font-size: 0.85em; }
    .toggle-title { font-size: 1.1em; font-weight: bold; color: white; margin-bottom: 2px; }
    .toggle-description { font-size: 0.85em; color: #aaa; line-height: 1.2; }
    .toggle-container { padding: 10px 0; border-bottom: 1px solid #222; }
    </style>
  """, unsafe_allow_html=True)

  # 태블릿 가상 키보드 차단
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
