import streamlit as st
from utils import params

TOGGLE_ITEMS = [
  ("PcmCruiseEnable",           "PcmCruise",                    "Change the openpilot cruise engagement. use the PcmCruise method"),
  ("CruiseStateControl",        "Cruise State Controls",         "Openpilot controls cruise on/off, set speed"),
  ("IsHda2",                    "CANFD Car HDA2",                "Highway Drive Assist 2, turn it on"),
  ("CameraSccEnable",           "CameraSCC",                     "HDA1 CameraSCC CAR, HDA2 Connect the ADAS ECAN line to CAMERA modify, turn it on"),
  ("RadarTrackEnable",          "Enable Radar Track use",        "Enable Radar Track use (disable AEB)"),
  ("DriverCameraOnReverse",     "Driver Camera On Reverse",      "Displays the driver camera when in reverse"),
  ("DriverCameraHardwareMissing","Driver Camera Hardware Missing","If there is a problem with the driver camera hardware, drive without the driver camera"),
]


def render():
  # session_state 초기화
  for key, _, _ in TOGGLE_ITEMS:
    if f"tog_{key}" not in st.session_state:
      st.session_state[f"tog_{key}"] = params.get_bool(key)

  for key, label, desc in TOGGLE_ITEMS:
    val       = st.session_state[f"tog_{key}"]
    state_str = "on" if val else "off"

    t_col1, t_col2 = st.columns([0.1, 0.9], vertical_alignment="center")

    with t_col1:
      st.markdown(f'<div id="toggle_wrap_{state_str}_{key}"></div>', unsafe_allow_html=True)
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
        </div>""", unsafe_allow_html=True)
