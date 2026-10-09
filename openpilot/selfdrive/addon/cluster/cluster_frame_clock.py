import time


class ClusterFrameClock:
  """A display deadline independent of the 20 Hz camera, with no catch-up bursts."""

  def __init__(self, fps, clock=time.monotonic, sleep=time.sleep):
    self.interval = 1.0 / max(float(fps), 1.0)
    self.clock = clock
    self.sleep = sleep
    self.next_frame = self.clock()

  def wait(self):
    now = self.clock()
    if now < self.next_frame:
      self.sleep(self.next_frame - now)
      now = self.clock()
    # Expensive work must not trigger a burst of obsolete frames afterwards.
    self.next_frame = max(self.next_frame + self.interval, now)
    if self.next_frame <= now:
      self.next_frame = now + self.interval
