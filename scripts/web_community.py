import streamlit as st
import subprocess
import shutil
import os
import time
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

    /* Common style for all metric cards */
    .metric-card, .metric-card-danger, .metric-card-warning, .metric-card-success {
      display: flex;
      align-items: center;
      height: 3.5em; /* Match button height */
      background-color: #161B22;
      padding-left: 15px;
      border-radius: 8px;
      border-left-width: 5px;
      border-left-style: solid;
      font-size: 1em; /* Increased font size */
      font-weight: bold;
    }

    .metric-card {
      border-left-color: #2C2CE2;
      color: white;
    }
    .metric-card-danger {
      border-left-color: rgb(201, 34, 49);
      color: rgb(201, 34, 49);
    }
    .metric-card-warning {
      border-left-color: rgb(218, 202, 37);
      color: rgb(218, 202, 37);
    }
    .metric-card-success {
      border-left-color: rgb(128, 216, 166);
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
    .stTextArea textarea {
        font-family: 'Courier New', Courier, monospace;
        font-size: 0.85em;
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

  tabs = st.tabs(["🚀 Functions", "⚙️ Toggles", "📋 Logs", "📂 Realdata", "📺 Terminal", "📷 Camera"])

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

      card_class = "metric-card-warning"

      if commit_output:
        if " == " in commit_output:
          card_class = "metric-card-success"
        elif " != " in commit_output:
          card_class = "metric-card-danger"

      st.markdown(f'<div class="{card_class}">Update Status : &nbsp;{commit_info}</div>', unsafe_allow_html=True)

    if " == " not in (params.get("CommitCompare") or "") and params.get("CommitCompare"):
      pull_col1, pull_col2 = st.columns([1, 2])
      with pull_col1:
        if st.button("⬇️ Git Pull Now", type="primary", use_container_width=True):
          run_script("Git Pull", f"{SCRIPTS_PATH}/gitpull.sh")
          st.rerun()
      with pull_col2:
        st.warning("New Update Available! Please pull the latest changes.", icon="⚠️")

    row3_col1, row3_col2 = st.columns([1, 2])
    with row3_col1:
      if st.button("✨ Reset Calibration", use_container_width=True):
        if st.checkbox("Confirm Reset"):
          reset_calibration()
    with row3_col2:
      device_position = params.get("DevicePosition") or "--"
      st.markdown(f'<div class="metric-card">Device Position : &nbsp;{device_position}</div>', unsafe_allow_html=True)

    sub_col1, sub_col2 = st.columns([1, 2])
    with sub_col1:
      if st.button("🔴 Reboot", type="secondary", use_container_width=True):
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
    st.subheader("System Logs")
    log_files = {
      "CAN Missing": "/data/can_missing.log",
      "CAN Timeout": "/data/can_timeout.log",
      "Tmux Error": "/data/tmux_error.log",
      "Tmux Console": "TMUX_CONSOLE"
    }

    l_col1, l_col2, l_col3 = st.columns([3, 1, 1])

    with l_col1:
      sel_log_name = st.selectbox("Select Log File", list(log_files.keys()), label_visibility="visible")
      sel_log_path = log_files[sel_log_name]

    with l_col2:
      st.write("")
      st.write("")
      if st.button("👁️ View", key="btn_view_file", use_container_width=True):
        if sel_log_path == "TMUX_CONSOLE":
          capture_cmd = "tmux capture-pane -p -t 0 -S -500 > /data/tmux_console.log"
          subprocess.run(capture_cmd, shell=True)

          console_log_path = Path("/data/tmux_console.log")
          if console_log_path.exists():
            st.session_state.log_out = console_log_path.read_text()
          else:
            st.error("Failed to capture tmux console.")
        else:
          if Path(sel_log_path).exists():
            st.session_state.log_out = Path(sel_log_path).read_text()
          else:
            st.error("File not found.")

    with l_col3:
      st.write("")
      st.write("")
      if st.button("⬆️ Upload", key="btn_upload_file", use_container_width=True):
        if sel_log_path == "TMUX_CONSOLE":
          console_log_path = Path("/data/tmux_console.log")
          if not console_log_path.exists():
            subprocess.run("tmux capture-pane -p -t 0 -S -500 > /data/tmux_console.log", shell=True)

          run_script("Console Upload", f"{SCRIPTS_PATH}/log_upload.sh", args=["tmux_console.log"])
        else:
          run_script("Log Upload", f"{SCRIPTS_PATH}/log_upload.sh",
                     args=[sel_log_name])

    content = st.session_state.get("log_out", "Select a log file or console to view.")
    st.text_area("Output Window", content, height=500, key="log_output_window")

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

      for r_data in route_map.values():
        r_data['paths'].sort(key=lambda x: int(x.split("--")[-1]))

      if not route_map:
        st.info("No uploadable routes found.")
      else:
        sorted_routes = sorted(route_map.items(), key=lambda x: x[1]['mtime'], reverse=True)
        options = [f"[{datetime.fromtimestamp(v['mtime']).strftime('%Y-%m-%d %H:%M')}] {k} ({len(v['paths'])} segs)" for
                   k, v in sorted_routes]
        sel_route = st.selectbox("Select Route to Upload", options)
        if st.button("🚀 Selected Route Upload", use_container_width=True):
          idx = options.index(sel_route)
          targets = sorted_routes[idx][1]['paths']

          cmd = ["bash", f"{SCRIPTS_PATH}/realdata_upload.sh"] + targets

          try:
            subprocess.Popen(cmd)
            st.success(f"Upload started in background! ({len(targets)} segments)\nPlease check the NAS or Tmux logs.")
          except Exception as e:
            st.error(f"Failed to start upload: {e}")

  with tabs[5]:
    st.subheader("📷 WebRTC Streaming")

    col_cam1, col_cam2 = st.columns([1, 3])
    with col_cam1:
      st.markdown("### Source: Road Camera")
      st.write("")
      start_btn = st.button("▶️ Start Stream", type="primary", use_container_width=True)

    with col_cam2:
      if start_btn:
        webrtc_html = f"""
            <html>
              <body style="background-color: #000; margin: 0; display: flex; justify-content: center; align-items: center; height: 500px; font-family: sans-serif; position: relative;">
                <video id="video" autoplay playsinline muted controls style="width: 100%; height: 100%; object-fit: contain; cursor: pointer;"></video>
                <div id="status" style="position: absolute; top: 10px; left: 10px; color: white; background: rgba(0,0,0,0.7); padding: 5px; border-radius: 4px; font-size: 14px; pointer-events: none;">Initializing...</div>
                <div id="debug" style="position: absolute; bottom: 10px; left: 10px; color: #00ff00; background: rgba(0,0,0,0.8); padding: 8px; border-radius: 4px; font-size: 12px; pointer-events: none; white-space: pre; display: block; text-align: left;">Waiting for stats...</div>

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
                    const streamType = "road";
                    let lastBytes = 0;
                    let lastTimestamp = 0;

                    // Click to retry play
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
                        status.innerText = "Stream Active (road)";
                        video.srcObject = event.streams[0];
                        video.play().catch(e => {{
                            console.error("Autoplay failed:", e);
                            status.innerText = "Stream Ready (Click Video to Play)";
                        }});
                      }};

                      // Stats Monitor
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
                                        const duration = (now - lastTimestamp) / 1000; // seconds
                                        if (duration > 0) {{
                                            bitrate = ((bytes - lastBytes) * 8 / 1000) / duration; // kbps
                                        }}
                                    }}

                                    lastBytes = bytes;
                                    lastTimestamp = now;

                                    let codecInfo = "";
                                    if (report.codecId) {{
                                      codecInfo = "CodecID: " + report.codecId;
                                    }}

                                    debug.innerText = `ICE: ${{pc.iceConnectionState}}\\nConn: ${{pc.connectionState}}\\nBytes: ${{bytes}}\\nBitrate: ${{bitrate.toFixed(0)}} kbps\\nFrames Decoded: ${{report.framesDecoded}}\\nPackets Lost: ${{report.packetsLost}}\\n${{codecInfo}}`;
                                }}
                            }});
                            if (!foundVideo) debug.innerText = `ICE: ${{pc.iceConnectionState}}\\nConn: ${{pc.connectionState}}\\nWaiting for video data...`;
                        }} else {{
                            debug.innerText = `ICE State: ${{pc.iceConnectionState}}\\nConn State: ${{pc.connectionState}}`;
                        }}
                      }}, 1000);

                      const offer = await pc.createOffer();
                      await pc.setLocalDescription(offer);

                      status.innerText = "Gathering ICE candidates...";
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

                      status.innerText = "Handshaking with " + ip + "...";

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
                      status.style.color = "red";
                    }}
                  }}

                  start();
                </script>
              </body>
            </html>
            """
        components.html(webrtc_html, height=500)

      elif not start_btn:
        st.info("Click 'Start Stream' to connect automatically.")

  with tabs[4]:
    st.subheader("📺 Real-time Terminal")

    terminal_placeholder = st.empty()

    while True:
      content = get_tmux_capture()
      terminal_placeholder.code(content, language="bash")
      time.sleep(1)

if __name__ == "__main__":
  if check_password():
    main()
