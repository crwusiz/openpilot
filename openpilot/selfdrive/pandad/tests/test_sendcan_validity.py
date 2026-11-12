import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


PANDAD = Path(__file__).resolve().parents[1]


def _test_program(source: str) -> str:
  start = source.index("void can_send_thread(")
  end = source.index("\nvoid can_recv(", start)
  function = source[start:end]
  return r"""
#include <cinttypes>
#include <cassert>
#include <cstdint>
#include <deque>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

struct TestEvent {
  bool valid;
  uint64_t time;
  std::vector<int> frames;
  bool getValid() const { return valid; }
  uint64_t getLogMonoTime() const { return time; }
  const std::vector<int> &getSendcan() const { return frames; }
};
static bool do_exit = false;
static std::deque<TestEvent> queued;
static uint64_t now = 2000000000ULL;
struct Message {
  TestEvent event;
};
struct AlignedBuffer {
  Message *align(Message *message) { return message; }
};
struct Service { int queue_size; };
static std::map<std::string, Service> services = {{"sendcan", {100}}};
class SubSocket {
public:
  static SubSocket *create(const char *, bool, int) { return new SubSocket(); }
  void setTimeout(int) {}
  Message *receive() {
    if (queued.empty()) {
      do_exit = true;
      return nullptr;
    }
    auto event = queued.front();
    queued.pop_front();
    return new Message{event};
  }
};
namespace cereal { struct Event { using Reader = TestEvent; }; }
namespace capnp {
class FlatArrayMessageReader {
public:
  explicit FlatArrayMessageReader(Message *message) : event(message->event) {}
  template <typename T> TestEvent getRoot() { return event; }
private:
  TestEvent event;
};
}
namespace util { static void set_thread_name(const char *) {} }
static uint64_t nanos_since_boot() { return now; }
#define LOGE(...) do {} while (0)
class Panda {
public:
  std::vector<int> sent;
  bool healthy = true;
  bool fail_after_send = false;
  bool comms_healthy() const { return healthy; }
  void can_send(const std::vector<int> &frames) {
    sent.insert(sent.end(), frames.begin(), frames.end());
    if (fail_after_send) healthy = false;
  }
};
static bool check_connected(Panda *) { return true; }
""" + function + r"""

int main(int argc, char **argv) {
  try {
    if (argc != 2) throw std::runtime_error("one scenario required");
    const std::string scenario = argv[1];
    bool fake_send = false;
    std::vector<int> expected;
    Panda panda;
    if (scenario == "invalid") {
      // An invalid snapshot can contain an entire batch of stale vehicle data.
      queued = {{false, now, {1, 2, 3}}, {false, now - 10000000, {4}}, {true, now, {5}}};
      expected = {5};
    } else if (scenario == "age") {
      queued = {{true, now - 1000000001ULL, {1}}, {true, now - 1000000000ULL, {2}},
                {true, now - 999999999ULL, {3}}, {true, now, {4}}};
      expected = {3, 4};
    } else if (scenario == "fake_send") {
      fake_send = true;
      queued = {{true, now, {1}}, {false, now, {2}}};
    } else if (scenario == "transport_fault") {
      // carState's last snapshot can still be marked valid when SPI fails.
      panda.healthy = false;
      queued = {{true, now, {1}}, {true, now, {2}}};
    } else if (scenario == "failure_midstream") {
      panda.fail_after_send = true;
      queued = {{true, now, {1}}, {true, now, {2}}, {true, now, {3}}};
      expected = {1};
    } else throw std::runtime_error("unknown scenario");
    can_send_thread(&panda, fake_send);
    if (panda.sent != expected) throw std::runtime_error("unexpected CAN frames reached Panda");
    return 0;
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
"""


class TestSendcanValidity(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    compiler = shlex.split(os.environ.get("CXX", "c++"))
    if not compiler or shutil.which(compiler[0]) is None:
      raise unittest.SkipTest("C++ compiler required; set CXX to select one")
    temporary = tempfile.TemporaryDirectory(prefix="pandad_sendcan_validity_")
    cls.addClassCleanup(temporary.cleanup)
    directory = Path(temporary.name)
    source = directory / "sendcan_validity.cc"
    source.write_text(_test_program((PANDAD / "pandad.cc").read_text()), encoding="utf-8")
    cls.executable = directory / ("sendcan_validity.exe" if os.name == "nt" else "sendcan_validity")
    result = subprocess.run([*compiler, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-O0",
                             str(source), "-o", str(cls.executable)], capture_output=True, text=True, timeout=120)
    if result.returncode:
      raise RuntimeError(result.stderr)

  def _run(self, scenario):
    result = subprocess.run([str(self.executable), scenario], capture_output=True, text=True, timeout=10)
    self.assertEqual(result.returncode, 0, result.stderr)

  def test_invalid_batches_never_reach_panda(self):
    self._run("invalid")

  def test_valid_batch_age_limit_is_preserved(self):
    self._run("age")

  def test_fake_send_never_reaches_panda(self):
    self._run("fake_send")

  def test_transport_fault_blocks_even_before_carstate_timeout(self):
    self._run("transport_fault")

  def test_transfer_failure_stops_subsequent_batches(self):
    self._run("failure_midstream")


if __name__ == "__main__":
  unittest.main()
