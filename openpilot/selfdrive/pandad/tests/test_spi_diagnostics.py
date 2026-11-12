"""Exercise the production SPI retry loop with deterministic time and wire responses."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

PANDAD = Path(__file__).resolve().parents[1]


def _test_program(source: str, header: str, diagnostics_expected: bool = True) -> str:
  constants = source[source.index("// protocol,"):source.index("const std::string SPI_DEVICE")]
  crc = source[source.index("static uint32_t crc32("):source.index("int PandaSpiHandle::spi_transfer_retry(")]
  retry = source[source.index("int PandaSpiHandle::spi_transfer_retry("):source.index("int PandaSpiHandle::lltransfer(")]
  transfer = source[source.index("int PandaSpiHandle::spi_transfer("):]
  bulk = source[source.index("int PandaSpiHandle::bulk_write("):source.index("std::vector<std::string> PandaSpiHandle::list(")]
  return r"""
#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#define private public
""" + header.replace("#pragma once", "") + r"""
#undef private
static uint64_t now_us = 10000000;
static uint64_t queued_us;
static unsigned lock_depth, locks, unlocks, backoffs;
static uint32_t next_id;
static std::vector<uint32_t> ids;
static std::vector<std::string> logs;
static void require(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}
[[maybe_unused]] static uint64_t nanos_since_boot() { return now_us * 1000; }
static double millis_since_boot() { return now_us / 1000.0; }
static uint32_t random_id() { return ++next_id; }
[[maybe_unused]] static void diagnostic_log(const char *format, ...) {
  require(lock_depth == 0, "new diagnostic logging holds SPI lock");
  char text[4096];
  va_list args;
  va_start(args, format);
  vsnprintf(text, sizeof(text), format, args);
  va_end(args);
  logs.emplace_back(text);
}
#define LOGD(...) diagnostic_log(__VA_ARGS__)
#define CLOUDLOG_ERROR 40
#define CLOUDLOG_DEBUG 10
#define cloudlog(level, ...) diagnostic_log(__VA_ARGS__)
#define LOGW(...) do {} while (0)
#define LOGE(...) do {} while (0)
#define SPILOG(...) do {} while (0)
namespace util {
inline std::string hexdump(const uint8_t *, int) { return "ab"; }
}
class LockEx {
public:
  LockEx(int, std::recursive_mutex &mutex) : mutex(mutex) {
    now_us += queued_us;
    queued_us = 0;
    mutex.lock();
    lock_depth++;
    locks++;
  }
  ~LockEx() { lock_depth--; unlocks++; mutex.unlock(); }
private:
  std::recursive_mutex &mutex;
};
int usleep(unsigned int delay) {
  require(lock_depth == 0, "backoff holds SPI lock");
  now_us += delay;
  backoffs++;
  return 0;
}
struct spi_ioc_transfer { uint64_t tx_buf; uint64_t rx_buf; uint32_t len; };
""" + constants + crc + retry + transfer + bulk + r"""
PandaSpiHandle::PandaSpiHandle(std::string) { }
PandaSpiHandle::~PandaSpiHandle() { }
enum Mode { ACK, NACK_ACK, NACK_BAD, BAD_CRC, UNRESPONSIVE, GOES_SILENT, IO_ERROR, STALE, QUEUED };
static Mode mode;
static int nacks_left;
static unsigned polls_seen, applications;
static uint32_t last_applied;
static bool silent_once, io_error_once;

int PandaSpiHandle::lltransfer(spi_ioc_transfer &t) {
  require(lock_depth == 1, "wire transfer outside original lock");
  now_us += 50;
  memset(rx_buf, 0, sizeof(rx_buf));
  if (mode == UNRESPONSIVE || mode == QUEUED) { now_us += 1000; return (int)t.len; }
  if (mode == GOES_SILENT && !silent_once) { silent_once = true; now_us += 1001000; return (int)t.len; }
  if (mode == IO_ERROR && !io_error_once) { io_error_once = true; return -1; }
  if (t.tx_buf == (uint64_t)tx_buf) {
    require(check_crc(tx_buf, sizeof(header) + header.tx_len), "invalid request CRC");
    ids.push_back((uint32_t)header.id);
    if (nacks_left == 0 && last_applied != header.id) { applications++; last_applied = header.id; }
    rx_buf[0] = SPI_SLAVE_READY;
    return (int)t.len;
  }
  polls_seen++;
  if (mode == BAD_CRC && polls_seen == 2) { rx_buf[0] = SPI_SLAVE_READY; return (int)t.len; }
  if (mode == NACK_BAD && nacks_left == 0) return (int)t.len;
  const bool nack = nacks_left > 0;
  if (nack) { nacks_left--; now_us += 50000; }
  spi_resp_header response = {
    .status = (uint8_t)(nack ? SPI_NACK : SPI_ACK),
    .id = header.id + (mode == STALE && polls_seen == 1 ? 1U : 0U),
    .len = (uint16_t)(!nack && header.rx_len > 11 ? 3 : 0),
  };
  memcpy(rx_buf, &response, sizeof(response));
  if (response.len) memcpy(rx_buf + sizeof(response), "abc", 3);
  put_crc(rx_buf, sizeof(response) + response.len);
  if (mode == BAD_CRC && polls_seen == 1) rx_buf[sizeof(response) + response.len] ^= 1;
  return (int)t.len;
}

int main(int argc, char **argv) {
  try {
    require(argc == 2, "scenario required");
    const std::string scenario = argv[1];
    if (scenario == "ack" || scenario == "read") mode = ACK;
    else if (scenario == "nack_ack") { mode = NACK_ACK; nacks_left = 3; }
    else if (scenario == "nack_bad") { mode = NACK_BAD; nacks_left = 3; }
    else if (scenario == "crc_retry") mode = BAD_CRC;
    else if (scenario == "timeout" || scenario == "read_timeout") mode = UNRESPONSIVE;
    else if (scenario == "silent") mode = GOES_SILENT;
    else if (scenario == "io_error") mode = IO_ERROR;
    else if (scenario == "stale") mode = STALE;
    else if (scenario == "queued") { mode = QUEUED; queued_us = 125000; }
    else throw std::runtime_error("unknown scenario");
    PandaSpiHandle handle("");
    uint8_t payload[] = {0xAB};
    uint8_t received[3] = {};
    const bool read = scenario == "read" || scenario == "read_timeout";
    int ret = read ? handle.bulk_read(0x81, received, sizeof(received), 5) :
                    handle.bulk_write(3, payload, sizeof(payload), mode == GOES_SILENT ? 0 : 5);
    const bool fails = mode == UNRESPONSIVE || mode == QUEUED || mode == NACK_BAD;
    require(ret == (fails ? -3 : (read ? 3 : 1)), "transport result changed");
    if (read && !fails) require(memcmp(received, "abc", 3) == 0, "receive payload changed");
    require(handle.comms_healthy == !fails, "communication failure latch changed");
    require(lock_depth == 0 && locks == unlocks, "lock lifetime changed");
    if (mode == NACK_ACK || mode == NACK_BAD) {
      require(ids.size() == 4, "NACK retry count changed");
      require(ids[0] != ids[1] && ids[1] != ids[2] && ids[2] != ids[3], "NACK must use new IDs");
    }
    if (mode == BAD_CRC || mode == STALE) {
      require(ids.size() == 2 && ids[0] == ids[1], "ambiguous response changed request ID");
      require(applications == 1, "duplicate request applied twice");
    }
    if (mode == GOES_SILENT) require(backoffs == 1, "silent retry policy changed");
    if (DIAGNOSTICS_EXPECTED) {
      require(!logs.empty(), "missing diagnostic record");
      std::string combined;
      for (const auto &entry : logs) combined += entry + "\n";
      require(combined.find(read ? "SPI_DIAG ep=0x81" : "SPI_DIAG ep=0x3") != std::string::npos, "missing request context");
      if (mode == QUEUED) {
        require(combined.find("lock_max_us=125000") != std::string::npos, "queued time missing");
        require(combined.find("expired_at_lock=1") != std::string::npos, "expired deadline hidden");
      }
      if (mode == NACK_BAD) require(combined.find("nacks=3") != std::string::npos, "final NACK count missing");
      if (mode == UNRESPONSIVE || mode == NACK_BAD || mode == QUEUED)
        require(combined.find("ret=-3 pending=0") != std::string::npos, "final failure suppressed");
      if (mode == BAD_CRC) require(combined.find("invalid=1") != std::string::npos, "CRC rejection count missing");
      if (mode == STALE) require(combined.find("stale=1") != std::string::npos, "stale response count missing");
      if (mode == IO_ERROR) require(combined.find("ioctl_errors=1") != std::string::npos, "ioctl error count missing");
      std::cerr << combined;
    }
    std::cout << "ret=" << ret << " healthy=" << handle.comms_healthy << " time_us=" << now_us
              << " locks=" << locks << " backoffs=" << backoffs << " applied=" << applications << " ids=";
    for (auto id : ids) std::cout << id << ',';
    std::cout << '\n';
    return 0;
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
""".replace("DIAGNOSTICS_EXPECTED", str(int(diagnostics_expected)))


class TestSpiDiagnostics(unittest.TestCase):
  def test_transport_and_metrics(self):
    compiler = shlex.split(os.environ.get("CXX", "c++"), posix=os.name != "nt")
    if not shutil.which(compiler[0]):
      self.skipTest("C++ compiler unavailable")
    with tempfile.TemporaryDirectory() as directory:
      source = Path(directory) / "spi_diagnostics.cc"
      executable = Path(directory) / ("spi_diagnostics.exe" if os.name == "nt" else "spi_diagnostics")
      source.write_text(_test_program((PANDAD / "spi.cc").read_text(), (PANDAD / "panda_comms.h").read_text()), encoding="utf-8")
      build = subprocess.run([*compiler, "-std=c++20", "-pthread", "-Wall", "-Wextra", "-Werror", "-O0",
                              str(source), "-o", str(executable)], capture_output=True, text=True)
      self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
      for scenario in ("ack", "nack_ack", "nack_bad", "crc_retry", "timeout", "silent", "io_error", "stale", "queued", "read", "read_timeout"):
        with self.subTest(scenario=scenario):
          result = subprocess.run([str(executable), scenario], capture_output=True, text=True, timeout=15)
          self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
  unittest.main()
