import subprocess
from pathlib import Path
import streamlit as st

SCRIPTS_PATH     = "/data/openpilot/scripts"

# ── Params (openpilot 없으면 MockParams) ──────────────
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


# ── 공통 유틸 ──────────────────────────────────────────
def get_list_from_file(path: str) -> list[str]:
  if Path(path).exists():
    with open(path, 'r', encoding='utf-8') as f:
      return [line.strip() for line in f if line.strip()]
  return []


def run_script(name: str, path: str, args: list | None = None) -> int:
  """스크립트 실행 후 결과를 session_state["script_log"]에 저장"""
  try:
    cmd = ["bash", path] if path.endswith('.sh') else ["python3", path]
    if args:
      cmd += args
    res = subprocess.run(cmd, capture_output=True, text=True)
    lines = []
    if res.stdout: lines.append(res.stdout.strip())
    if res.stderr: lines.append(f"[STDERR] {res.stderr.strip()}")
    st.session_state["script_log"] = f"[{name}] " + ("\n".join(lines) if lines else "Completed.")
    st.session_state["script_ok"]  = (res.returncode == 0)
    return res.returncode
  except Exception as e:
    st.session_state["script_log"] = f"[{name}] Error: {e}"
    st.session_state["script_ok"]  = False
    return 1


def reset_calibration():
  """캘리브레이션 파라미터 초기화"""
  params.remove("CalibrationParams")
  params.remove("LiveTorqueParameters")
  params.remove("LiveParameters")
  params.remove("LiveParametersV2")
  params.remove("LiveDelay")
  params.put_bool("OnroadCycleRequested", True)
  st.toast("Calibration Reset Requested!")


def get_tmux_capture() -> str:
  """tmux 세션 0의 현재 출력 캡처"""
  try:
    res = subprocess.run(
      ["tmux", "capture-pane", "-pe", "-t", "0"],
      capture_output=True, text=True
    )
    if res.returncode == 0:
      return res.stdout
    return "Tmux Session not found (Wait for openpilot to start...)"
  except Exception as e:
    return f"Error capturing tmux: {e}"
