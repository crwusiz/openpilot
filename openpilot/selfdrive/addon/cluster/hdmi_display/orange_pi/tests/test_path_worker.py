from dataclasses import replace
import threading
import time
import weakref

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi.path_worker import LatestPathWorker, PathRequest


def request(sequence=1, **changes):
  value = PathRequest(path={"sequence": sequence}, hud={}, transform=None, is_metric=True,
                      key=(1920, 480, True), epoch=1, valid_until=10.15, received_at=10.0)
  return replace(value, **changes)


def poll_until(worker, current, predicate, now=10.01):
  deadline = time.monotonic() + 3
  while time.monotonic() < deadline:
    result = worker.poll(current, now)
    if predicate(result):
      return result
    time.sleep(0.001)
  raise AssertionError("Path completion did not arrive")


def test_blocked_job_does_not_block_poll_and_only_latest_input_runs_next():
  started, release, latest_started = threading.Event(), threading.Event(), threading.Event()
  calls = []
  active = maximum_active = 0
  lock = threading.Lock()

  def render(job):
    nonlocal active, maximum_active
    sequence = job.path["sequence"]
    with lock:
      calls.append(sequence)
      active += 1
      maximum_active = max(maximum_active, active)
    try:
      if sequence == 1:
        started.set()
        assert release.wait(3)
      else:
        latest_started.set()
      return sequence
    finally:
      with lock:
        active -= 1

  worker = LatestPathWorker(render)
  try:
    assert worker.poll(request(1), 10.0) is None
    assert started.wait(3)
    # These return while the callback remains blocked. No request is queued
    # behind it, so the intermediate snapshot cannot delay the latest one.
    assert worker.poll(request(2), 10.01) is None
    assert worker.poll(request(3), 10.02) is None
    assert calls == [1]
    assert worker.submitted == 1
    release.set()
    result = poll_until(worker, request(3), lambda item: item is not None and item.value == 3)
    assert latest_started.is_set()
    assert calls[:2] == [1, 3]
    assert 2 not in calls
    assert maximum_active == 1
    assert result.request.path["sequence"] == 3
  finally:
    release.set()
    worker.close()


@pytest.mark.parametrize("changes,now", [({"key": (1280, 480, True)}, 10.01), ({"epoch": 2}, 10.01),
                                        ({}, 10.151), ({"valid_until": 10.005}, 10.01)])
def test_retained_path_requires_compatible_geometry_epoch_and_original_freshness(changes, now):
  release = threading.Event()
  worker = LatestPathWorker(lambda job: release.wait(3))
  try:
    worker.seed(request(), "initial")
    assert worker.poll(request(**changes), now) is None
    if now > request(**changes).valid_until:
      assert worker.submitted == 0
  finally:
    release.set()
    worker.close()


def test_seed_is_available_immediately_and_reuse_does_not_refresh_its_expiry():
  release = threading.Event()
  worker = LatestPathWorker(lambda job: release.wait(3))
  try:
    seeded = worker.seed(request(), "initial")
    assert worker.poll(request(2, valid_until=11.0, received_at=10.1), 10.1) is seeded
    assert worker.poll(request(3, valid_until=11.0, received_at=10.15), 10.15) is seeded
    assert worker.reused == 2
    assert worker.poll(request(4, valid_until=11.0, received_at=10.2), 10.2) is None
    assert worker.submitted == 1
    assert worker.completed == 0
  finally:
    release.set()
    worker.close()


def test_invalidation_discards_late_completion_even_when_key_and_epoch_match():
  started, release = threading.Event(), threading.Event()

  def render(job):
    if job.path["sequence"] == 1:
      started.set()
      assert release.wait(3)
    return job.path["sequence"]

  worker = LatestPathWorker(render)
  try:
    worker.seed(request(), "initial")
    assert worker.poll(request(), 10.0).value == "initial"
    assert started.wait(3)
    worker.invalidate()
    assert worker.poll(request(2), 10.01) is None
    release.set()
    # The expired generation's completion cannot become visible again.
    observed = []

    def current_result(result):
      if result is not None:
        observed.append(result.value)
      return result is not None and result.value == 2

    poll_until(worker, request(2), current_result)
    assert observed == [2]
  finally:
    release.set()
    worker.close()


def test_expired_completion_is_not_reused_or_resubmitted():
  started, release = threading.Event(), threading.Event()

  def render(job):
    started.set()
    assert release.wait(3)
    return "late"

  worker = LatestPathWorker(render)
  try:
    worker.poll(request(), 10.0)
    assert started.wait(3)
    release.set()
    poll_until(worker, request(), lambda result: worker.completed == 1, now=10.2)
    assert worker.poll(request(), 10.21) is None
    assert worker.submitted == worker.completed == 1
  finally:
    release.set()
    worker.close()


def test_expired_completion_keeps_a_still_valid_retained_path_until_fresh_replacement():
  started, release = threading.Event(), threading.Event()

  def render(job):
    if job.path["sequence"] == 2:
      started.set()
      assert release.wait(3)
    return job.path["sequence"]

  worker = LatestPathWorker(render)
  try:
    retained = worker.seed(request(valid_until=10.5), "retained")
    short_lived = request(2, received_at=10.1, valid_until=10.12)
    assert worker.poll(short_lived, 10.1) is retained
    assert started.wait(3)
    release.set()
    deadline = time.monotonic() + 3
    while not worker._future.done() and time.monotonic() < deadline:
      time.sleep(0.001)
    assert worker._future.done()
    fresh = request(3, received_at=10.15, valid_until=10.65)
    assert worker.poll(fresh, 10.15) is retained
    assert worker.last_reason == "retained"
    assert retained.request.valid_until == 10.5
    assert poll_until(worker, fresh, lambda result: result is not None and result.value == 3, 10.16).value == 3
    assert worker.last_reason == "ready"
  finally:
    release.set()
    worker.close()


def test_unchanged_immutable_metadata_is_rendered_once_across_new_request_wrappers():
  calls = []
  current = request(valid_until=10.5)
  worker = LatestPathWorker(lambda job: calls.append(job) or "painted")
  try:
    first = poll_until(worker, current, lambda result: result is not None)
    assert worker.last_reason == "ready"
    for tick in range(1, 10):
      # The scene creates a new request object per camera frame, but retains
      # the same owned path/HUD snapshot until new metadata arrives.
      assert worker.poll(replace(current), 10.01 + tick * 0.01) is first
      assert worker.last_reason == "retained"
    assert worker.submitted == worker.completed == len(calls) == 1
    assert worker._future is None
    newer = request(2, received_at=10.2, valid_until=10.7)
    assert poll_until(worker, newer, lambda result: result is not None and result.request is newer, 10.2).request is newer
    assert worker.submitted == worker.completed == len(calls) == 2
  finally:
    worker.close()


def test_invalidation_repaints_even_the_same_immutable_snapshot():
  current = request()
  worker = LatestPathWorker(lambda job: "painted")
  try:
    assert poll_until(worker, current, lambda result: result is not None).value == "painted"
    assert worker.submitted == worker.completed == 1
    worker.invalidate()
    assert worker.last_reason == "incompatible"
    assert poll_until(worker, current, lambda result: result is not None).value == "painted"
    assert worker.submitted == worker.completed == 2
  finally:
    worker.close()


def test_failed_snapshot_can_be_retried_without_replacing_its_metadata():
  calls = []
  def render(job):
    calls.append(job)
    if len(calls) == 1:
      raise ValueError("transient raster failure")
    return "recovered"

  current = request()
  worker = LatestPathWorker(render)
  try:
    with pytest.raises(ValueError, match="transient raster failure"):
      poll_until(worker, current, lambda result: result is not None)
    assert poll_until(worker, current, lambda result: result is not None).value == "recovered"
    assert worker.submitted == worker.completed == len(calls) == 2
  finally:
    worker.close()


def test_callback_exception_is_reported_on_poll_and_worker_can_accept_a_new_request():
  def render(job):
    if job.path["sequence"] == 1:
      raise ValueError("path raster failed")
    return job.path["sequence"]

  worker = LatestPathWorker(render)
  try:
    worker.poll(request(), 10.0)
    with pytest.raises(ValueError, match="path raster failed"):
      poll_until(worker, request(), lambda result: result is not None)
    assert worker.completed == worker.submitted == 1
    assert poll_until(worker, request(2), lambda result: result is not None).value == 2
  finally:
    worker.close()


def test_close_joins_active_job_releases_buffers_and_is_idempotent():
  started, release, closing, closed = (threading.Event() for _ in range(4))

  class Payload:
    pass

  retained, current = Payload(), Payload()
  retained_ref, current_ref = weakref.ref(retained), weakref.ref(current)

  def render(job):
    started.set()
    assert release.wait(3)
    return job.path

  worker = LatestPathWorker(render)
  worker.seed(request(path=retained), retained)
  worker.poll(request(path=current), 10.0)
  assert started.wait(3)
  del retained, current

  def close():
    closing.set()
    worker.close()
    closed.set()

  thread = threading.Thread(target=close)
  thread.start()
  try:
    assert closing.wait(3)
    assert not closed.wait(0.05)
    release.set()
    assert closed.wait(3)
    thread.join(3)
    assert retained_ref() is None
    assert current_ref() is None
    worker.close()
    with pytest.raises(RuntimeError, match="closed"):
      worker.poll(request(), 10.01)
    with pytest.raises(RuntimeError, match="closed"):
      worker.seed(request(), "late")
  finally:
    release.set()
    thread.join(3)
    worker.close()
