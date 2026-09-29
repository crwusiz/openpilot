class ModeldTiming:
  """Rate-limit diagnostics while retaining counts and timing peaks between logs."""

  def __init__(self, frame_period: float):
    self.frame_budget_ms = frame_period * 1e3
    self.last_log_time = None
    self.previous = None
    self.frames = 0
    self.dropped_frames = 0
    self.slow_frames = 0
    self.max_ms: dict[str, float] = {}

  def update(self, sample: dict, now: float) -> dict | None:
    self.frames += 1
    # The first camera frame ID is not a count of frames dropped by this process.
    if sample['frame_id_delta'] is not None:
      self.dropped_frames += sample['vipc_dropped_frames']
    # Receiving a frame normally waits for the next camera period; don't count
    # that normal wait against the processing budget.
    slow = sample['processing_ms'] > self.frame_budget_ms or sample['camera_receive_ms'] > 2 * self.frame_budget_ms
    self.slow_frames += int(slow)
    for key, value in sample.items():
      if key.endswith('_ms') and value is not None:
        self.max_ms[key] = max(self.max_ms.get(key, value), value)

    abnormal = self.dropped_frames > 0 or self.slow_frames > 0
    interval = 1.0 if abnormal else 5.0
    event = None
    if self.last_log_time is None or now - self.last_log_time >= interval:
      event = {
        'error': abnormal,
        'current': sample,
        'previous': self.previous,
        'window': {
          'frames': self.frames,
          'dropped_frames': self.dropped_frames,
          'slow_frames': self.slow_frames,
          'max_ms': self.max_ms,
        },
      }
      self.last_log_time = now
      self.frames = self.dropped_frames = self.slow_frames = 0
      self.max_ms = {}
    self.previous = sample
    return event
