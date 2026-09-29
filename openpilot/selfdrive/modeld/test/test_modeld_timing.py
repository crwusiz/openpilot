import unittest

from openpilot.selfdrive.modeld.timing import ModeldTiming


def sample(frame_id, dropped=0, model_run_ms=20.0, camera_receive_ms=25.0, first=False):
  return {
    'frame_id': frame_id,
    'frame_id_delta': None if first else dropped + 1,
    'vipc_dropped_frames': dropped,
    'camera_receive_ms': camera_receive_ms,
    'model_run_ms': model_run_ms,
    'lane_data_ms': 0.1,
    'desire_update_ms': 0.2,
    'postprocess_ms': 2.0,
    'publish_ms': 0.3,
    'processing_ms': model_run_ms + 2.6,
    'publish_interval_ms': None if first else 50.0,
  }


class TestModeldTiming(unittest.TestCase):
  def test_first_camera_id_is_not_a_drop_count(self):
    timing = ModeldTiming(0.05)
    event = timing.update(sample(6000, dropped=5999, first=True), 10.0)
    self.assertFalse(event['error'])
    self.assertEqual(event['window']['dropped_frames'], 0)
    self.assertIsNone(event['previous'])
    self.assertNotIn('publish_interval_ms', event['window']['max_ms'])

  def test_normal_period_and_previous_frame(self):
    timing = ModeldTiming(0.05)
    timing.update(sample(1, first=True), 0.0)
    previous = sample(2)
    self.assertIsNone(timing.update(previous, 4.9))
    current = sample(3)
    event = timing.update(current, 5.0)
    self.assertFalse(event['error'])
    self.assertEqual(event['window']['frames'], 2)
    self.assertEqual(event['current'], current)
    self.assertEqual(event['previous'], previous)

  def test_frame_drop_records_previous_slow_inference(self):
    timing = ModeldTiming(0.05)
    timing.update(sample(1, first=True), 0.0)
    previous = sample(2, model_run_ms=120.0)
    self.assertIsNone(timing.update(previous, 0.9))
    current = sample(5, dropped=2)
    event = timing.update(current, 1.0)
    self.assertTrue(event['error'])
    self.assertEqual(event['previous'], previous)
    self.assertEqual(event['window']['dropped_frames'], 2)
    self.assertEqual(event['window']['slow_frames'], 1)
    self.assertEqual(event['window']['max_ms']['model_run_ms'], 120.0)

  def test_throttled_drops_and_peaks_survive_recovery(self):
    timing = ModeldTiming(0.05)
    timing.update(sample(1, first=True), 0.0)
    self.assertIsNone(timing.update(sample(4, dropped=2, model_run_ms=150.0), 0.2))
    self.assertIsNone(timing.update(sample(6, dropped=1), 0.4))
    event = timing.update(sample(7), 1.0)
    self.assertTrue(event['error'])
    self.assertEqual(event['window']['dropped_frames'], 3)
    self.assertEqual(event['window']['max_ms']['model_run_ms'], 150.0)
    self.assertIsNone(timing.update(sample(8), 2.0))
    recovered = timing.update(sample(9), 6.0)
    self.assertFalse(recovered['error'])
    self.assertEqual(recovered['window']['dropped_frames'], 0)
    self.assertEqual(recovered['window']['max_ms']['model_run_ms'], 20.0)
    # Updating a later window must not mutate the already returned event.
    self.assertEqual(event['window']['max_ms']['model_run_ms'], 150.0)

  def test_sustained_drops_log_at_most_once_per_second(self):
    timing = ModeldTiming(0.05)
    timing.update(sample(1, first=True), 0.0)
    events = []
    for i in range(1, 41):
      event = timing.update(sample(1 + i * 2, dropped=1), i * 0.05)
      if event is not None:
        events.append(event)
    self.assertEqual(len(events), 2)
    self.assertEqual(sum(e['window']['dropped_frames'] for e in events), 40)

  def test_normal_camera_wait_is_not_slow_processing(self):
    timing = ModeldTiming(0.05)
    event = timing.update(sample(1, camera_receive_ms=50.0, first=True), 0.0)
    self.assertFalse(event['error'])
    event = timing.update(sample(2, camera_receive_ms=200.0), 1.0)
    self.assertTrue(event['error'])
    self.assertEqual(event['window']['slow_frames'], 1)
    self.assertEqual(event['window']['max_ms']['camera_receive_ms'], 200.0)


if __name__ == '__main__':
  unittest.main()
