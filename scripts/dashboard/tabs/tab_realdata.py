import subprocess
from datetime import datetime
from pathlib import Path
import streamlit as st

from utils import SCRIPTS_PATH

REALDATA_PATH    = Path("/data/media/0/realdata")

def render():
  if not REALDATA_PATH.exists():
    st.markdown(
      '<div class="log-output-box log-error">❌ Path not found: /data/media/0/realdata</div>',
      unsafe_allow_html=True
    )
    return

  # ── 루트 목록 수집 ──────────────────────────────────
  route_map: dict = {}
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
    st.markdown(
      '<div class="log-output-box log-warn">⚠️ No uploadable routes found.</div>',
      unsafe_allow_html=True
    )
    return

  sorted_routes = sorted(route_map.items(), key=lambda x: x[1]['mtime'], reverse=True)
  options = [
    f"[{datetime.fromtimestamp(v['mtime']).strftime('%Y-%m-%d %H:%M')}] {k} ({len(v['paths'])} segs)"
    for k, v in sorted_routes
  ]

  sel_route = st.selectbox("Select Route to Upload", options)
  st.markdown('<div id="btn_marker_success_route"></div>', unsafe_allow_html=True)

  if st.button("Route Upload", use_container_width=True):
    idx     = options.index(sel_route)
    targets = sorted_routes[idx][1]['paths']
    cmd     = ["bash", f"{SCRIPTS_PATH}/realdata_upload.sh"] + targets
    try:
      subprocess.Popen(cmd)
      st.markdown(
        f'<div class="log-output-box log-success">✅ Upload started in background! ({len(targets)} segments)\nPlease check the NAS or Tmux logs.</div>',
        unsafe_allow_html=True
      )
    except Exception as e:
      st.markdown(
        f'<div class="log-output-box log-error">❌ Failed to start upload: {e}</div>',
        unsafe_allow_html=True
      )
