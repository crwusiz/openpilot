import pytest

from openpilot.selfdrive.addon.cluster.cluster_frame_clock import ClusterFrameClock


class Clock:
  def __init__(self):
    self.now = 0.0
    self.sleeps = []

  def __call__(self):
    return self.now

  def sleep(self, duration):
    self.sleeps.append(duration)
    self.now += duration


def test_display_deadlines_include_render_time_in_the_frame_period():
  clock = Clock()
  frames = ClusterFrameClock(60, clock=clock, sleep=clock.sleep)
  frames.wait()
  clock.now += 0.008  # Rendering must not add another complete frame wait.
  frames.wait()
  assert clock.now == pytest.approx(1 / 60)
  clock.now += 0.006
  frames.wait()
  assert clock.now == pytest.approx(2 / 60)


def test_slow_work_does_not_cause_a_catch_up_burst():
  clock = Clock()
  frames = ClusterFrameClock(60, clock=clock, sleep=clock.sleep)
  frames.wait()
  clock.now = 0.5
  frames.wait()
  assert clock.sleeps == []
  frames.wait()
  assert clock.sleeps == [pytest.approx(1 / 60)]
