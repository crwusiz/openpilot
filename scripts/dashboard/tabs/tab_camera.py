import streamlit as st
import streamlit.components.v1 as components

CAMERA_OPTIONS = {
  "Road Camera":      "road",
  "Driver Camera":    "driver",
  "Wide Road Camera": "wideRoad",
}


def render():
  # ── session_state 초기화 (반드시 맨 위에서) ─────────────
  if "cam_streaming" not in st.session_state:
    st.session_state["cam_streaming"] = False

  try:
    c_col1, c_col2, c_col3 = st.columns([3, 1, 1], vertical_alignment="bottom")

    with c_col1:
      selected_cam = st.selectbox("Select Camera Source", list(CAMERA_OPTIONS.keys()), key="cam_source")
      stream_type  = CAMERA_OPTIONS[selected_cam]

    with c_col2:
      st.markdown('<div id="btn_marker_success_start"></div>', unsafe_allow_html=True)
      if st.button("Start", key="btn_cam_start", use_container_width=True):
        st.session_state["cam_streaming"] = True
        st.rerun()

    with c_col3:
      st.markdown('<div id="btn_marker_danger_stop"></div>', unsafe_allow_html=True)
      if st.button("Stop", key="btn_cam_stop", use_container_width=True):
        st.session_state["cam_streaming"] = False
        st.rerun()

    if st.session_state["cam_streaming"]:
      _render_stream(stream_type)
    else:
      _render_stopped()

  except Exception as e:
    st.markdown(
      f'<div class="log-output-box log-error">❌ Camera tab error: {e}</div>',
      unsafe_allow_html=True
    )


# ── 내부 헬퍼 ────────────────────────────────────────────

def _render_stopped():
  st.markdown("""
    <div class="log-viewer" style="
      display:flex; flex-direction:column;
      justify-content:center; align-items:center;
      gap:12px; color:#3A4A6B;
    ">
      <div style="font-size:3em; filter:grayscale(1) opacity(0.3);">📷</div>
      <div style="font-size:0.9em; font-weight:600; letter-spacing:0.08em; text-transform:uppercase;">
        Select a camera source and press Start
      </div>
    </div>
  """, unsafe_allow_html=True)
  st.markdown('<div class="log-statusbar">📷 Camera &nbsp;|&nbsp; Stream Stopped</div>', unsafe_allow_html=True)


def _render_stream(stream_type: str):
  webrtc_html = f"""
    <html>
      <body style="background-color:#0B0E14; margin:0; font-family:sans-serif;">

        <!-- 비디오 영역 -->
        <div style="
          position:relative; width:100%; height:430px;
          background:#000; border-radius:12px;
          border:1.5px solid #3A4A6B; overflow:hidden;
          box-shadow:0 4px 16px rgba(0,0,0,0.4);">
          <video id="video" autoplay playsinline muted controls
            style="width:100%; height:100%; object-fit:contain; cursor:pointer;"></video>
          <div id="status" style="
            position:absolute; top:10px; right:12px;
            color:#E8EEFF; background:rgba(0,0,0,0.65);
            padding:4px 10px; border-radius:20px;
            font-size:12px; font-weight:600; pointer-events:none;">
            Initializing...
          </div>
        </div>

        <!-- 디버그 상태바 -->
        <div id="debug" style="
          margin-top:10px;
          background:linear-gradient(90deg,#1A2235,#232E45);
          border:1.5px solid #3A4A6B;
          border-left:4px solid #3B82F6;
          border-radius:12px;
          padding:10px 16px;
          color:#93C5FD;
          font-size:12px;
          font-family:'Courier New', monospace;
          white-space:pre;
          min-height:40px;
        ">Waiting for stream stats...</div>

        <script>
          async function start() {{
            const video  = document.getElementById('video');
            const status = document.getElementById('status');
            const debug  = document.getElementById('debug');

            const iceConfig = {{
              iceServers: [],
              sdpSemantics: "unified-plan",
              iceCandidatePoolSize: 1
            }};

            const ip         = window.location.hostname || window.parent.location.hostname;
            const port       = "5001";
            const streamType = "{stream_type}";
            let lastBytes     = 0;
            let lastTimestamp = 0;

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
                status.innerText = "● Stream Active (" + streamType + ")";
                status.style.background = "rgba(16,185,129,0.75)";
                video.srcObject = event.streams[0];
                video.play().catch(e => {{
                  status.innerText = "▶ Click to Play";
                }});
              }};

              setInterval(async () => {{
                if (pc.connectionState === 'connected' || pc.iceConnectionState === 'connected') {{
                  const stats = await pc.getStats();
                  let foundVideo = false;
                  stats.forEach(report => {{
                    if (report.type === 'inbound-rtp' && report.kind === 'video') {{
                      foundVideo = true;
                      const now   = report.timestamp;
                      const bytes = report.bytesReceived;
                      let bitrate = 0;
                      if (lastTimestamp > 0) {{
                        const dur = (now - lastTimestamp) / 1000;
                        if (dur > 0) bitrate = ((bytes - lastBytes) * 8 / 1000) / dur;
                      }}
                      lastBytes     = bytes;
                      lastTimestamp = now;
                      const codec   = report.codecId ? "CodecID: " + report.codecId : "";
                      debug.innerText = `ICE: ${{pc.iceConnectionState}}  |  Conn: ${{pc.connectionState}}  |  Bytes: ${{bytes}}  |  Bitrate: ${{bitrate.toFixed(0)}} kbps  |  Frames: ${{report.framesDecoded}}  |  Lost: ${{report.packetsLost}}\\n${{codec}}`;
                    }}
                  }});
                  if (!foundVideo) debug.innerText = `ICE: ${{pc.iceConnectionState}}  |  Conn: ${{pc.connectionState}}  |  Waiting for video...`;
                }} else {{
                  debug.innerText = `ICE: ${{pc.iceConnectionState}}  |  Conn: ${{pc.connectionState}}`;
                }}
              }}, 1000);

              const offer = await pc.createOffer();
              await pc.setLocalDescription(offer);
              status.innerText = "Gathering ICE...";

              await new Promise((resolve) => {{
                if (pc.iceGatheringState === 'complete') return resolve();
                const check = () => {{
                  if (pc.iceGatheringState === 'complete') {{
                    pc.removeEventListener('icegatheringstatechange', check);
                    resolve();
                  }}
                }};
                pc.addEventListener('icegatheringstatechange', check);
                setTimeout(resolve, 8000);
              }});

              const payload = {{
                sdp: pc.localDescription.sdp,
                cameras: [streamType],
                bridge_services_in: [],
                bridge_services_out: []
              }};

              status.innerText = "Handshaking...";
              const response = await fetch(`http://${{ip}}:${{port}}/stream`, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
              }});

              if (!response.ok) {{
                throw new Error("Server Error: " + response.status + " " + await response.text());
              }}

              await pc.setRemoteDescription(await response.json());

            }} catch (e) {{
              console.error(e);
              status.innerText = "Error: " + e.message;
              status.style.background = "rgba(239,68,68,0.8)";
              debug.innerText = "Connection Error: " + e.message;
              debug.style.borderLeftColor = "#EF4444";
              debug.style.color = "#FCA5A5";
            }}
          }}

          start();
        </script>
      </body>
    </html>
  """
  components.html(webrtc_html, height=530)
