"""Retain the latest completed CPU path without stalling camera presentation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PathRequest:
  """An owned snapshot; callers must also keep its nested objects immutable."""

  path: Any
  hud: Any
  transform: Any
  is_metric: bool
  key: Any
  epoch: int
  valid_until: float
  received_at: float


@dataclass(frozen=True)
class PathResult:
  request: PathRequest
  value: Any


class LatestPathWorker:
  """One running job and one retained result, operated by its owner thread.

  poll() never waits for an unfinished job and never queues an intermediate
  request. After a completion, it submits the request offered by that poll.
  The callback owns its CPU painter; it must not touch the owner's renderer
  or SDL objects. Result buffers must remain immutable after callback return.
  """

  def __init__(self, render):
    self._render = render
    self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cluster-path")
    self._future = self._request = self._result = None
    self._generation = self._running_generation = 0
    self._closed = False
    self.submitted = self.completed = self.reused = 0

  def _check_open(self):
    if self._closed:
      raise RuntimeError("Path worker is closed")

  def seed(self, request, value):
    """Publish an already computed first frame, superseding earlier jobs."""
    self._check_open()
    self.invalidate()
    self._result = PathResult(request, value)
    return self._result

  def invalidate(self):
    """Hide the retained path immediately; an active CPU job may finish."""
    self._generation += 1
    self._result = None

  def poll(self, request, now):
    self._check_open()
    collected = False
    if self._future is not None and self._future.done():
      future, finished_request = self._future, self._request
      generation = self._running_generation
      self._future = self._request = None
      self.completed += 1
      value = future.result()  # Propagate worker errors on the owner thread.
      if generation == self._generation:
        self._result = PathResult(finished_request, value)
        collected = True

    result = self._result
    if result is not None and not (
      result.request.epoch == request.epoch and result.request.key == request.key
      and now <= result.request.valid_until and now <= request.valid_until
    ):
      self._result = result = None

    # An expired snapshot must not consume CPU or extend an earlier result's
    # lifetime. In particular, never replace valid_until with the poll time.
    if self._future is None and now <= request.valid_until:
      self._request = request
      self._running_generation = self._generation
      self._future = self._executor.submit(self._render, request)
      self.submitted += 1

    if result is not None and not collected:
      self.reused += 1
    return result

  def close(self):
    if self._closed:
      return
    self._closed = True
    executor, self._executor = self._executor, None
    try:
      executor.shutdown(wait=True, cancel_futures=True)
    finally:
      if self._future is not None:
        self.completed += 1
      self._future = self._request = self._result = self._render = None
