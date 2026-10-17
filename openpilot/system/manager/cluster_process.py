"""Observe auxiliary cluster exits and restart explicitly selected modes."""

from openpilot.selfdrive.addon.cluster.cluster_logging import flog, record_process_exit


def observe_cluster_exit(process):
  if process.name != "cluster" or process.proc is None:
    return
  child = process.proc
  if child.exitcode is None or getattr(process, "_cluster_reported_pid", None) == child.pid:
    return
  process._cluster_reported_pid = child.pid
  record_process_exit(child.pid, child.exitcode, process.shutting_down)


def _selection(params):
  def text(key, default):
    value = params.get(key)
    if isinstance(value, bytes):
      value = value.decode("utf-8", errors="replace")
    return str(value or default).strip().lower()

  transport = text("ClusterDisplayTransport", "usb")
  return transport, text("ClusterUsbCodec" if transport == "usb" else "ClusterNetworkCodec", "jpeg")


def prepare_cluster_start(process, params):
  if process.name != "cluster":
    return
  selection = _selection(params)
  child = process.proc
  if child is not None and child.exitcode is not None:
    observe_cluster_exit(process)
    changed = selection != getattr(process, "_cluster_selection", selection)
    # A normal codec/transport exit is an explicit restart request. A crash is
    # left visible until the user changes mode or toggles Cluster, avoiding a
    # tight loop around a failing hardware encoder.
    if child.exitcode == 0 or changed:
      reason = "selection_changed" if changed else "normal_exit"
      flog(f"[CLUSTER_RESTART] reason={reason} transport={selection[0]} codec={selection[1]}")
      process.stop()
  # start() also completes an earlier asynchronous stop before launching a new
  # child. Its configuration snapshot must describe that new child as well.
  if process.proc is None or process.shutting_down:
    process._cluster_selection = selection
  if process.proc is None:
    process._cluster_reported_pid = None
