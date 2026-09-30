import unittest
from collections import deque
from unittest.mock import patch

from openpilot.selfdrive.modeld.camera import CameraFrameReader, MAX_CAMERA_SYNC_NS
from openpilot.selfdrive.modeld.timing import ModeldTiming


class FakeClock:
  def __init__(self):
    self.now = 0.0

  def advance(self, ms):
    self.now += ms * 1e-3


class FakeCamera:
  def __init__(self, clock, frames):
    self.clock = clock
    self.frames = deque(frames)
    self.timeouts = []
    self.frame_id = self.timestamp_sof = self.timestamp_eof = 0

  def recv(self, timeout_ms):
    self.timeouts.append(timeout_ms)
    if not self.frames:
      self.clock.advance(timeout_ms)
      return None

    frame_id, sof, wait_ms = self.frames[0]
    if wait_ms > timeout_ms:
      self.clock.advance(timeout_ms)
      self.frames[0] = (frame_id, sof, wait_ms - timeout_ms)
      return None

    self.frames.popleft()
    self.clock.advance(wait_ms)
    if frame_id is None:
      return None
    self.frame_id, self.timestamp_sof, self.timestamp_eof = frame_id, sof, sof + 10_000_000
    return object()


def frame(frame_id, sof_ms, wait_ms=0.0):
  return frame_id, round(sof_ms * 1e6), wait_ms


class TestCameraFrameReader(unittest.TestCase):
  def setUp(self):
    self.clock = FakeClock()
    timer = patch('openpilot.selfdrive.modeld.camera.time.monotonic', side_effect=lambda: self.clock.now)
    timer.start()
    self.addCleanup(timer.stop)

  def reader(self, main_frames, extra_frames=None):
    main = FakeCamera(self.clock, main_frames)
    extra = FakeCamera(self.clock, extra_frames) if extra_frames is not None else None
    return CameraFrameReader(main, extra)

  def test_normal_pairs_advance_both_cameras_once(self):
    reader = self.reader([frame(100, 1000, 20), frame(101, 1050, 30)],
                         [frame(100, 1000.01, 3), frame(101, 1050.01, 2)])
    for frame_id, main_ms, extra_ms in ((100, 20, 3), (101, 30, 2)):
      main, extra = reader.recv()
      self.assertEqual((main.frame_id, extra.frame_id), (frame_id, frame_id))
      self.assertEqual(reader.stats['camera_main_recv_count'], 1)
      self.assertEqual(reader.stats['camera_extra_recv_count'], 1)
      self.assertAlmostEqual(reader.stats['camera_main_receive_ms'], main_ms)
      self.assertAlmostEqual(reader.stats['camera_extra_receive_ms'], extra_ms)
      self.assertAlmostEqual(reader.stats['camera_sync_max_ms'], 0.01)

  def test_single_camera_uses_same_fresh_buffer(self):
    reader = self.reader([frame(0, 0), frame(1, 50)])
    for frame_id in (0, 1):
      main, extra = reader.recv()
      self.assertIs(main, extra)
      self.assertEqual(main.frame_id, frame_id)
      self.assertEqual(reader.stats['camera_extra_recv_count'], 0)

  def test_logged_extra_ahead_recovers_on_nearest_main_frame(self):
    # 18:47: the previous code published frame 18689 out of sync, then advanced
    # main past extra + 25ms, unnecessarily skipping the nearer frame 18690.
    reader = self.reader([frame(18689, 1_032_510.83), frame(18690, 1_032_560.83), frame(18691, 1_032_610.83)],
                         [frame(18689, 1_032_566.813977), frame(18691, 1_032_610.843439)])
    main, extra = reader.recv()
    self.assertEqual((main.frame_id, extra.frame_id), (18690, 18689))
    self.assertLessEqual(abs(main.timestamp_sof - extra.timestamp_sof), MAX_CAMERA_SYNC_NS)
    self.assertEqual(reader.stats['camera_main_sync_skips'], 1)
    self.assertEqual(reader.stats['camera_main_recv_count'], 2)
    self.assertEqual(reader.stats['camera_extra_recv_count'], 1)
    self.assertAlmostEqual(reader.stats['camera_sync_max_ms'], 55.983977)

    main, extra = reader.recv()
    self.assertEqual((main.frame_id, extra.frame_id), (18691, 18691))
    self.assertEqual(reader.stats['camera_main_recv_count'], 1)
    self.assertEqual(reader.stats['camera_main_sync_skips'], 0)

  def test_queued_extra_frames_do_not_advance_main(self):
    reader = self.reader([frame(100, 1000)], [frame(i, 1000 - (100 - i) * 50) for i in range(97, 101)])
    main, extra = reader.recv()
    self.assertEqual((main.frame_id, extra.frame_id), (100, 100))
    self.assertEqual(reader.stats['camera_main_recv_count'], 1)
    self.assertEqual(reader.stats['camera_extra_recv_count'], 4)
    self.assertEqual(reader.stats['camera_extra_sync_skips'], 3)

  def test_capture_timestamps_drive_alignment_when_frame_ids_differ(self):
    reader = self.reader([frame(100, 1000)], [frame(99, 1000.01)])
    main, extra = reader.recv()
    self.assertEqual((main.frame_id, extra.frame_id), (100, 99))
    self.assertEqual(reader.stats['camera_main_sync_skips'], 0)
    self.assertEqual(reader.stats['camera_extra_sync_skips'], 0)

  def test_sync_tolerance_boundary(self):
    for offset_ns, expected_main_id in ((MAX_CAMERA_SYNC_NS, 100), (MAX_CAMERA_SYNC_NS + 1, 101)):
      with self.subTest(offset_ns=offset_ns):
        reader = self.reader([frame(100, 1000), frame(101, 1020)],
                             [(100, 1_000_000_000 + offset_ns, 0)])
        main, extra = reader.recv()
        self.assertEqual(main.frame_id, expected_main_id)
        self.assertLessEqual(abs(main.timestamp_sof - extra.timestamp_sof), MAX_CAMERA_SYNC_NS)

  def test_main_timeout_does_not_reuse_a_previous_pair(self):
    reader = self.reader([frame(100, 1000), (None, 0, 100), frame(101, 1050)],
                         [frame(100, 1000.01), frame(101, 1050.01)])
    self.assertIsNotNone(reader.recv())
    self.assertIsNone(reader.recv())
    self.assertEqual(reader.failed_stream, 'main')
    self.assertIsNone(reader.main_frame)
    self.assertIsNone(reader.extra_frame)
    self.assertEqual(reader.stats['camera_extra_recv_count'], 0)
    main, extra = reader.recv()
    self.assertEqual((main.frame_id, extra.frame_id), (101, 101))

  def test_extra_timeout_discards_unprocessed_main_frame(self):
    reader = self.reader([frame(100, 1000), frame(101, 1050)],
                         [(None, 0, 100), frame(100, 1000), frame(101, 1050)])
    self.assertIsNone(reader.recv())
    self.assertEqual(reader.failed_stream, 'extra')
    self.assertIsNone(reader.extra_frame)
    main, extra = reader.recv()
    self.assertEqual((main.frame_id, extra.frame_id), (101, 101))
    self.assertEqual(reader.stats['camera_main_recv_count'], 1)
    self.assertEqual(reader.stats['camera_extra_recv_count'], 2)

  def test_stalled_extra_shares_remaining_receive_budget(self):
    reader = self.reader([frame(100, 1000, 80)], [frame(100, 1000, 50)])
    self.assertIsNone(reader.recv())
    self.assertEqual(reader.failed_stream, 'extra')
    self.assertLessEqual(reader.extra.timeouts[0], 21)
    self.assertLessEqual(self.clock.now, 0.101)
    self.assertIsNone(reader.extra_frame)

  def test_persistent_desynchronization_is_bounded_and_does_not_return_a_pair(self):
    reader = self.reader([frame(i, i * 50, 10) for i in range(20)],
                         [frame(i, i * 50 + 25, 10) for i in range(20)])
    self.assertIsNone(reader.recv())
    self.assertIn(reader.failed_stream, ('main', 'extra'))
    self.assertLessEqual(self.clock.now, 0.101)
    self.assertGreater(reader.stats['camera_main_sync_skips'], 0)
    self.assertGreater(reader.stats['camera_extra_sync_skips'], 0)

  def test_real_frame_gap_remains_visible_to_timing(self):
    # 14:04: receive delay was followed by a real 2650 -> 2652 gap.
    reader = self.reader([frame(2650, 1000, 57.177529), frame(2652, 1100)],
                         [frame(2650, 1000.014055), frame(2651, 1050), frame(2652, 1100.015096)])
    previous, _ = reader.recv()
    main, extra = reader.recv()
    dropped = max(0, main.frame_id - previous.frame_id - 1)
    self.assertEqual(dropped, 1)
    self.assertEqual(main.frame_id, 2652)
    self.assertEqual(extra.frame_id, 2652)

    event = ModeldTiming(0.05).update({
      'frame_id': main.frame_id, 'frame_id_delta': main.frame_id - previous.frame_id,
      'vipc_dropped_frames': dropped, 'processing_ms': 43.44, 'camera_receive_ms': 0.47,
      **reader.stats,
    }, self.clock.now)
    self.assertTrue(event['error'])
    self.assertEqual(event['window']['dropped_frames'], 1)
    self.assertEqual(event['current']['camera_extra_sync_skips'], 1)


if __name__ == '__main__':
  unittest.main()
