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
    self._last_submitted = None
    self._generation = self._running_generation = 0
    self._closed = False
    self.submitted = self.completed = self.reused = 0
    self.last_reason = "pending"

  def _check_open(self):
    if self._closed:
      raise RuntimeError("Path worker is closed")

  def seed(self, request, value):
    """Publish an already computed first frame, superseding earlier jobs."""
    self._check_open()
    self.invalidate()
    self._result = PathResult(request, value)
    self.last_reason = "ready"
    return self._result

  def invalidate(self):
    """Hide the retained path immediately; an active CPU job may finish."""
    self._generation += 1
    self._result = None
    self._last_submitted = None
    self.last_reason = "incompatible"

  @staticmethod
  def _matches(candidate, current):
    return candidate.epoch == current.epoch and candidate.key == current.key

  @classmethod
  def _usable(cls, candidate, current, now):
    return cls._matches(candidate, current) and now <= candidate.valid_until and now <= current.valid_until

  def _already_submitted(self, request):
    previous = self._last_submitted
    # Keep actual references, not only ids: a later object's recycled id must
    # never suppress a new immutable metadata snapshot. Transform changes
    # are represented by the compatibility key supplied by the owner.
    return previous is not None and self._matches(previous, request) and (
      previous.received_at == request.received_at and previous.is_metric == request.is_metric
      and previous.path is request.path and previous.hud is request.hud
    )

  def poll(self, request, now):
    self._check_open()
    collected = False
    rejected = None
    if self._future is not None and self._future.done():
      future, finished_request = self._future, self._request
      generation = self._running_generation
      self._future = self._request = None
      self.completed += 1
      try:
        value = future.result()  # Propagate worker errors on the owner thread.
      except BaseException:
        # The failed input can be offered again; it was not successfully
        # rendered. Callers still decide whether to retry or use a fallback.
        self._last_submitted = None
        raise
      if generation != self._generation or not self._matches(finished_request, request):
        rejected = "incompatible"
      elif not self._usable(finished_request, request, now):
        rejected = "expired"
      else:
        # A late completion must not erase an older, still-valid path. Check
        # admission before replacing the retained result, not afterwards.
        self._result = PathResult(finished_request, value)
        collected = True

    result = self._result
    if result is not None and not self._usable(result.request, request, now):
      rejected = "incompatible" if not self._matches(result.request, request) else "expired"
      self._result = result = None

    # An expired snapshot must not consume CPU or extend an earlier result's
    # lifetime. In particular, never replace valid_until with the poll time.
    if self._future is None and now <= request.valid_until and not self._already_submitted(request):
      self._request = request
      self._running_generation = self._generation
      self._future = self._executor.submit(self._render, request)
      self._last_submitted = request
      self.submitted += 1

    if result is not None and not collected:
      self.reused += 1
    self.last_reason = ("ready" if collected else "retained") if result is not None else (
      "expired" if now > request.valid_until else rejected or "pending"
    )
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
      self._future = self._request = self._result = self._render = self._last_submitted = None
      self.last_reason = "closed"
