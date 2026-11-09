import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


PANDAD = Path(__file__).resolve().parents[1]


def _function(source: str, signature: str) -> str:
  start = source.index(signature)
  body = source.index("{", start)
  depth = 1
  end = body
  while depth:
    end += 1
    depth += (source[end] == "{") - (source[end] == "}")
  return source[start:end + 1]


def _test_program(source: str, declaration: str) -> str:
  # Compile the real transfer loops and CRC helpers; only their hardware, clock,
  # random source and lock wrapper are replaced with deterministic test doubles.
  constants = source[source.index("#define SPI_SLAVE_READY"):source.index("const std::string SPI_DEVICE")]
  crc = source[source.index("static uint32_t crc32("):source.index("int PandaSpiHandle::spi_transfer_retry(")]
  retry = _function(source, "int PandaSpiHandle::spi_transfer_retry(")
  transfer = _function(source, "int PandaSpiHandle::spi_transfer(")
  return r"""
#include <algorithm>
#include <array>
#include <atomic>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#define private public
""" + declaration.replace("#pragma once", "") + r"""
#undef private
""" + constants + r"""
#define SPILOG(...) do {} while (0)
#define LOGW(...) do {} while (0)
#define LOGE(...) do {} while (0)

static double now_ms;
static double lock_wait_ms;
static int lock_depth;
static int acquisitions;
static int releases;
static int other_thread_acquisitions;
static uint32_t next_id;
static std::vector<uint32_t> request_ids;

static void require(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}
static double millis_since_boot() { return now_ms; }
static uint32_t random_id() { return ++next_id; }

class LockEx {
public:
  LockEx(int, std::recursive_mutex &mutex) : mutex(mutex) {
    now_ms += lock_wait_ms;
    lock_wait_ms = 0;
    mutex.lock();
    ++lock_depth;
    ++acquisitions;
  }
  ~LockEx() {
    --lock_depth;
    ++releases;
    mutex.unlock();
  }
private:
  std::recursive_mutex &mutex;
};

int usleep(unsigned int microseconds) {
  require(lock_depth == 0, "SPI lock held during retry backoff");
  bool acquired = false;
  std::thread contender([&] {
    acquired = PandaSpiHandle::hw_lock.try_lock();
    if (acquired) PandaSpiHandle::hw_lock.unlock();
  });
  contender.join();
  require(acquired, "another client cannot acquire the bus between attempts");
  ++other_thread_acquisitions;
  now_ms += microseconds / 1000.0;
  return 0;
}

struct spi_ioc_transfer {
  uint64_t tx_buf;
  uint64_t rx_buf;
  uint32_t len;
};
""" + crc + retry + "\n" + transfer + r"""

enum class Reply { NOT_READY, READY, ACK, NACK, CORRUPT };
struct Step { bool request; Reply reply; double duration_ms; };
static std::vector<Step> script;
static size_t position;

PandaSpiHandle::PandaSpiHandle(std::string) {}
PandaSpiHandle::~PandaSpiHandle() {}

int PandaSpiHandle::lltransfer(spi_ioc_transfer &transfer) {
  require(position < script.size(), "unexpected hardware transfer");
  const Step step = script[position++];
  const auto *tx = reinterpret_cast<const uint8_t *>(transfer.tx_buf);
  const bool request = tx[0] != SPI_POLL;
  require(request == step.request, "unexpected request/poll sequence");
  if (request) request_ids.push_back(static_cast<uint32_t>(header.id));
  now_ms += step.duration_ms;
  std::memset(rx_buf, 0, sizeof(rx_buf));
  if (step.reply == Reply::READY) {
    rx_buf[0] = SPI_SLAVE_READY;
  } else if (step.reply == Reply::ACK || step.reply == Reply::NACK) {
    spi_resp_header response = {
      .status = static_cast<uint8_t>(step.reply == Reply::ACK ? SPI_ACK : SPI_NACK),
      .id = header.id,
      .len = static_cast<uint16_t>(step.reply == Reply::ACK ? 1 : 0),
    };
    std::memcpy(rx_buf, &response, sizeof(response));
    if (response.len) rx_buf[sizeof(response)] = 0x42;
    put_crc(rx_buf, sizeof(response) + response.len);
  }
  return transfer.len;
}

static void reset() {
  now_ms = 0;
  lock_wait_ms = 0;
  lock_depth = acquisitions = releases = other_thread_acquisitions = 0;
  next_id = 100;
  request_ids.clear();
  script.clear();
  position = 0;
}

static int run_transfer(unsigned int timeout) {
  PandaSpiHandle handle("");
  uint8_t tx = 0x11;
  uint8_t rx[4] = {};
  int result = handle.spi_transfer_retry(1, &tx, 1, rx, sizeof(rx), timeout);
  if (result >= 0) require(result == 1 && rx[0] == 0x42, "incorrect ACK payload");
  require(lock_depth == 0 && acquisitions == releases, "SPI lock leaked");
  return result;
}

static void queued_lock() {
  for (unsigned int timeout : {100U, 0U}) {
    reset();
    lock_wait_ms = timeout ? 150 : 1000000;
    script = {{true, Reply::NOT_READY, 1}, {true, Reply::READY, 1}, {false, Reply::ACK, 1}};
    require(run_transfer(timeout) == 1, "lock queue consumed the transfer deadline");
    require(position == script.size(), "ACK was not read");
    require(request_ids == std::vector<uint32_t>({101, 101}), "ambiguous request changed ID");
  }
}

static void nack_backpressure() {
  reset();
  for (int i = 0; i < 6; ++i) {
    script.push_back({true, Reply::READY, 20});
    script.push_back({false, Reply::NACK, 20});
  }
  script.insert(script.end(), {{true, Reply::READY, 1}, {false, Reply::CORRUPT, 1},
                              {false, Reply::READY, 1}, {true, Reply::READY, 1}, {false, Reply::ACK, 1}});
  require(run_transfer(100) == 1, "NACK backpressure consumed the next request deadline");
  require(now_ms > 100, "fixture did not exceed the original deadline");
  require(request_ids == std::vector<uint32_t>({101, 102, 103, 104, 105, 106, 107, 107}),
          "request ID must change only after an explicit NACK");
  require(acquisitions == 7 && other_thread_acquisitions > 0, "bus was not released between NACK attempts");
}

static void real_timeout() {
  reset();
  script.assign(5, {true, Reply::NOT_READY, 25});
  require(run_transfer(100) == SpiError::ACK_TIMEOUT, "unresponsive bus did not time out");
  require(now_ms == 125, "unresponsive transfer budget was extended");
  require(request_ids == std::vector<uint32_t>({101, 101, 101, 101, 101}), "ambiguous retries changed ID");
  reset();
  script = {{true, Reply::READY, 1}};
  script.insert(script.end(), 4, {false, Reply::CORRUPT, 25});
  require(run_transfer(100) == SpiError::ACK_TIMEOUT, "corrupt response did not time out");
  require(now_ms == 101, "corrupt response budget was extended");
}

int main(int argc, char **argv) {
  try {
    require(argc == 2, "one scenario required");
    const std::string scenario = argv[1];
    if (scenario == "queued_lock") queued_lock();
    else if (scenario == "nack_backpressure") nack_backpressure();
    else if (scenario == "real_timeout") real_timeout();
    else throw std::runtime_error("unknown scenario");
    return 0;
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
"""


class TestSpiDeadline(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    compiler = shlex.split(os.environ.get("CXX", "c++"))
    if not compiler or shutil.which(compiler[0]) is None:
      raise unittest.SkipTest("C++ compiler required; set CXX to select one")
    temporary = tempfile.TemporaryDirectory(prefix="pandad_spi_deadline_")
    cls.addClassCleanup(temporary.cleanup)
    directory = Path(temporary.name)
    source = directory / "test_spi_deadline.cc"
    source.write_text(_test_program((PANDAD / "spi.cc").read_text(), (PANDAD / "panda_comms.h").read_text()), encoding="utf-8")
    cls.executable = directory / ("test_spi_deadline.exe" if os.name == "nt" else "test_spi_deadline")
    result = subprocess.run([*compiler, "-std=c++20", "-pthread", "-Wall", "-Wextra", "-Werror", "-O0",
                             str(source), "-o", str(cls.executable)], capture_output=True, text=True, timeout=120)
    if result.returncode:
      raise RuntimeError(result.stderr)

  def _run(self, scenario):
    result = subprocess.run([str(self.executable), scenario], capture_output=True, text=True, timeout=10)
    self.assertEqual(result.returncode, 0, result.stderr)

  def test_lock_wait_preserves_finite_and_infinite_transfer_budget(self):
    self._run("queued_lock")

  def test_nack_backpressure_refreshes_budget_and_releases_bus(self):
    self._run("nack_backpressure")

  def test_unresponsive_and_corrupt_responses_keep_finite_deadline(self):
    self._run("real_timeout")


if __name__ == "__main__":
  unittest.main()
