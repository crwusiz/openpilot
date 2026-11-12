"""Verify optional firmware diagnostic reads and their log representation."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

PANDAD = Path(__file__).resolve().parents[1]
ROOT = PANDAD.parents[2]


class TestCanDiagnosticsLogging(unittest.TestCase):
  def test_supported_and_unsupported_firmware(self):
    compiler = shlex.split(os.environ.get("CXX", "c++"), posix=os.name != "nt")
    if not shutil.which(compiler[0]):
      self.skipTest("C++ compiler unavailable")
    source = (PANDAD / "panda.cc").read_text(encoding="utf-8")
    method = source[source.index("void Panda::log_can_diagnostics()"):source.index("void Panda::set_loopback(")]
    program = r"""
#include <cassert>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "panda/board/can_diagnostics.h"
#define USBPACKET_MAX_SIZE 64
static std::vector<std::string> logs;
static uint64_t nanos_since_boot() { return 1000000; }
static void record(const char *format, ...) __attribute__((format(printf, 1, 2)));
static void record(const char *format, ...) {
  char buffer[2048];
  va_list args;
  va_start(args, format);
  vsnprintf(buffer, sizeof(buffer), format, args);
  va_end(args);
  logs.emplace_back(buffer);
}
#define LOGD(...) record(__VA_ARGS__)
#define LOGW(...) record(__VA_ARGS__)
class Handle {
public:
  int result = 64;
  int requests = 0;
  bool healthy = false;
  int control_read(uint8_t request, uint16_t version, uint16_t zero, unsigned char *data, uint16_t len) {
    assert(request == CAN_DIAGNOSTICS_REQUEST && version == CAN_DIAGNOSTICS_VERSION && zero == 0 && len == 64);
    requests++;
    can_diagnostics_t diagnostics = {};
    diagnostics.time_us = 123;
    for (int i = 0; i < 3; i++) { diagnostics.bus[i].queued = (uint16_t)(i + 1); diagnostics.bus[i].wait_max_us = (uint32_t)(100 + i); }
    memcpy(data, &diagnostics, sizeof(diagnostics));
    return result;
  }
};
class Panda {
public:
  Handle *handle;
  bool can_diagnostics_supported = true;
  explicit Panda(Handle *h) : handle(h) {}
  void log_can_diagnostics();
};
""" + method + r"""
int main(void) {
  Handle supported;
  Panda panda(&supported);
  panda.log_can_diagnostics();
  panda.log_can_diagnostics();
  assert(supported.requests == 2 && !supported.healthy && panda.can_diagnostics_supported);
  assert(logs.size() == 2 && logs[0].find("queued=1,2,3") != std::string::npos);
  assert(logs[0].find("wait_max_us=100,101,102") != std::string::npos);
  for (int result : {0, 16, -3}) {
    Handle old;
    old.result = result;
    Panda compatibility(&old);
    compatibility.log_can_diagnostics();
    compatibility.log_can_diagnostics();
    assert(old.requests == 1 && !old.healthy && !compatibility.can_diagnostics_supported);
    assert(logs.back().find("CAN_DIAG unavailable") != std::string::npos);
  }
}
"""
    with tempfile.TemporaryDirectory() as directory:
      source_path = Path(directory) / "can_diagnostics.cc"
      executable = Path(directory) / ("can_diagnostics.exe" if os.name == "nt" else "can_diagnostics")
      source_path.write_text(program, encoding="utf-8")
      build = subprocess.run([*compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-O0", "-I", str(ROOT),
                              str(source_path), "-o", str(executable)], capture_output=True, text=True)
      self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
      result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
      self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
  unittest.main()
