import numpy as np
from typing import Any

from cereal import log


class NPQueue:
  def __init__(self, maxlen: int, rowsize: int) -> None:
    self.maxlen = maxlen
    self.arr = np.empty((0, rowsize))

  def __len__(self) -> int:
    return len(self.arr)

  def append(self, pt: list[float]) -> None:
    if len(self.arr) < self.maxlen:
      self.arr = np.append(self.arr, [pt], axis=0)
    else:
      self.arr[:-1] = self.arr[1:]
      self.arr[-1] = pt


class PointBuckets:
  def __init__(self, x_bounds: list[tuple[float, float]], min_points: list[float], min_points_total: int, points_per_bucket: int, rowsize: int) -> None:
    self.x_bounds = x_bounds
    self.buckets = {bounds: NPQueue(maxlen=points_per_bucket, rowsize=rowsize) for bounds in x_bounds}
    self.buckets_min_points = dict(zip(x_bounds, min_points, strict=True))
    self.min_points_total = min_points_total

  def __len__(self) -> int:
    return sum([len(v) for v in self.buckets.values()])

  def is_valid(self) -> bool:
    individual_buckets_valid = all(len(v) >= min_pts for v, min_pts in zip(self.buckets.values(), self.buckets_min_points.values(), strict=True))
    total_points_valid = self.__len__() >= self.min_points_total
    return individual_buckets_valid and total_points_valid

  def is_calculable(self) -> bool:
    return all(len(v) > 0 for v in self.buckets.values())

  def add_point(self, x: float, y: float) -> None:
    raise NotImplementedError

  def get_points(self, num_points: int = None) -> Any:
    points = np.vstack([x.arr for x in self.buckets.values()])
    if num_points is None:
      return points
    return points[np.random.choice(np.arange(len(points)), min(len(points), num_points), replace=False)]

  def load_points(self, points: list[list[float]]) -> None:
    for point in points:
      self.add_point(*point)


class ParameterEstimator:
  """ Base class for parameter estimators """
  def reset(self) -> None:
    raise NotImplementedError

  def handle_log(self, t: int, which: str, msg: log.Event) -> None:
    raise NotImplementedError

  def get_msg(self, valid: bool, with_points: bool) -> log.Event:
    raise NotImplementedError
