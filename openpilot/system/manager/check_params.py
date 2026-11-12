"""Check the native parameter registry before trusting a prebuilt installation."""
import re
import sys
from pathlib import Path

from openpilot.common.basedir import BASEDIR


def params_are_current() -> bool:
  try:
    header = (Path(BASEDIR) / "openpilot/common/params_keys.h").read_text(encoding="utf-8")
    expected = {key.encode() for key in re.findall(r'^\s*\{"([^"]+)",\s*\{', header, re.MULTILINE)}
    if not expected:
      raise ValueError("No parameter keys found in params_keys.h")

    # Import here so a missing or incompatible native library requests a build.
    from openpilot.common.params import Params
    actual = set(Params().all_keys())
    if expected != actual:
      missing = sorted(key.decode() for key in expected - actual)
      extra = sorted(key.decode() for key in actual - expected)
      print(f"Native parameter registry differs from params_keys.h: {missing=}, {extra=}", file=sys.stderr)
      return False
  except Exception as e:
    print(f"Unable to validate native parameter registry: {e}", file=sys.stderr)
    return False
  return True


if __name__ == "__main__":
  sys.exit(0 if params_are_current() else 1)
