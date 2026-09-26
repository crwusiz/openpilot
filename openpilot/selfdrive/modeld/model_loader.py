import os
import sys
import threading
from collections.abc import Callable
from typing import Any, NoReturn

from openpilot.common.swaglog import cloudlog


FORCE_SMALL_MODEL_ENV = "MODELD_FORCE_SMALL_MODEL"


def restart_with_small_model(params, reason: str, demo: bool = False) -> NoReturn:
  # exec replaces every thread and tinygrad singleton, including pending USB
  # work. Clearing selected caches or re-enabling device usage is not sufficient.
  cloudlog.warning(f"{reason}; restarting modeld with the small model")
  params.put_bool("ChestnutActive", False, block=True)
  params.put_bool("ChestnutLoading", False, block=True)
  argv = [sys.executable, "-m", "openpilot.selfdrive.modeld.modeld"]
  if demo:
    argv.append("--demo")
  # This flag belongs to this process only. A new manager launch can retry eGPU.
  os.execve(sys.executable, argv, {**os.environ, FORCE_SMALL_MODEL_ENV: "1"})
  raise RuntimeError("modeld exec unexpectedly returned")


def load_big_model(load: Callable[[], Any], timeout: float, on_failure: Callable[[str], NoReturn]) -> Any:
  model = None

  def run():
    nonlocal model
    try:
      model = load()
    except Exception:
      cloudlog.exception("big model load failed")

  loader = threading.Thread(target=run, daemon=True)
  loader.start()
  loader.join(timeout)
  if loader.is_alive():
    on_failure(f"big model load timed out after {timeout}s")
  elif model is None:
    on_failure("big model load failed")
  else:
    return model
  raise RuntimeError("big model fallback must replace the process")
