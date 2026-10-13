from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openpilot.common.constants import UnitConverter


REAR_CAMERA_TYPES = (75, 76, 86, 87, 88, 89)


def load_navi_controller():
  cereal = ModuleType("openpilot.cereal")
  cereal.messaging = SimpleNamespace(sub_sock=lambda service: None)
  realtime = ModuleType("openpilot.common.realtime")
  realtime.Ratekeeper = type("Ratekeeper", (), {})
  stubs = {
    "openpilot.cereal": cereal,
    "openpilot.common.realtime": realtime,
    "fcntl": ModuleType("fcntl"),
  }
  with patch.dict(sys.modules, stubs):
    path = Path(__file__).resolve().parents[1] / "navi_controller.py"
    spec = spec_from_file_location("_navi_controller_under_test", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


navi_controller = load_navi_controller()


@pytest.fixture
def limiter(monkeypatch):
  conv = UnitConverter.__new__(UnitConverter)
  conv.is_metric = True
  monkeypatch.setattr(navi_controller, "UnitConverter", lambda: conv)
  return navi_controller.SpeedLimiter()


def make_navi_data(cam_type, *, distance=600, is_highway=False, rear=False):
  data = SimpleNamespace(
    active=1,
    roadLimitSpeed=70,
    isHighway=is_highway,
    camType=cam_type,
    camLimitSpeedLeftDist=distance,
    camLimitSpeed=70,
    sectionLimitSpeed=0,
    sectionLeftDist=0,
    sectionAvgSpeed=0,
    sectionAdjustSpeed=False,
    camSpeedFactor=1.0,
    currentRoadName="금벽로",
  )
  if rear:
    data.camRearHoldDist = 87.5
    data.camDistanceAge = 0.25
    data.camDistanceId = 1234567890123456
    data.camLimitSpeedLeftDistPrecise = float(distance)
  return data


@pytest.mark.parametrize("cam_type", REAR_CAMERA_TYPES)
def test_udp_accepts_rear_camera_payload(monkeypatch, cam_type):
  road_limit = {
    "road_limit_speed": 70,
    "cam_type": cam_type,
    "cam_limit_speed": 70,
    "cam_limit_speed_left_dist": 600,
  }
  payload = json.dumps({"active": 1, "road_limit": road_limit}).encode()
  remote_addr = ("192.0.2.1", 12345)
  sock = SimpleNamespace(recvfrom=lambda size: (payload, remote_addr))
  monkeypatch.setattr(navi_controller.select, "select", lambda *args: ([sock], [], []))
  server = navi_controller.NaviServer(start_broadcast=False)

  assert server.udp_recv(sock)
  assert server.active == 1
  assert server.get_limit_val("cam_type", 0) == cam_type
  assert server.get_limit_val("cam_limit_speed", 0) == 70
  assert server.get_limit_val("cam_limit_speed_left_dist", 0) == 600


@pytest.mark.parametrize("cam_type", REAR_CAMERA_TYPES)
@pytest.mark.parametrize("is_highway", [False, True])
def test_rear_camera_decelerates_and_releases(limiter, cam_type, is_highway, caplog):
  limiter.naviData = make_navi_data(cam_type, is_highway=is_highway)

  with caplog.at_level("DEBUG", logger="navi"):
    assert limiter.get_camera_limit_active()
    assert limiter.get_max_speed(80) == (0, False)
    assert not limiter.cam_decel

    limiter.naviData.camLimitSpeedLeftDist = 399
    target, is_limit_zone = limiter.get_max_speed(80)
    assert 70 < target <= 80
    assert is_limit_zone
    assert limiter.cam_decel

    previous_target = target
    for distance in (350, 300, 250, 200, 150, 100, 80):
      limiter.naviData.camLimitSpeedLeftDist = distance
      target, is_limit_zone = limiter.get_max_speed(80)
      assert 70 <= target <= previous_target
      assert not is_limit_zone
      assert limiter.cam_decel
      previous_target = target
    assert target == 70

    limiter.naviData.camLimitSpeedLeftDist = 0
    assert not limiter.get_camera_limit_active()
    assert limiter.get_max_speed(80) == (0, False)
    assert not limiter.cam_decel

  assert f"rear camera event: cam_type={cam_type}" in caplog.text


@pytest.mark.parametrize("cam_type", REAR_CAMERA_TYPES)
@pytest.mark.parametrize("speed,distance", [(0, 100), (70, 0)])
def test_rear_camera_requires_speed_and_distance(limiter, cam_type, speed, distance):
  limiter.naviData = make_navi_data(cam_type, distance=distance)
  limiter.naviData.camLimitSpeed = speed

  assert not limiter.get_camera_limit_active()
  assert limiter.get_max_speed(80) == (0, False)
  assert not limiter.cam_decel


@pytest.mark.parametrize("cam_type", REAR_CAMERA_TYPES)
def test_rear_camera_holds_through_exact_boundary(limiter, cam_type):
  limiter.naviData = make_navi_data(cam_type, distance=399, rear=True)
  target, first_event = limiter.get_max_speed(80)
  assert 70 < target <= 80
  assert first_event
  started_distance = limiter.started_dist

  for distance in (350.0, 300.5, 250.0, 200.0, 170.25, 100.0, 1.5, 0.0, -0.9, -10.0, -87.5):
    limiter.naviData.camLimitSpeedLeftDist = int(distance)
    limiter.naviData.camLimitSpeedLeftDistPrecise = distance
    limiter.naviData.camDistanceId += 1
    assert limiter.get_camera_limit_active()
    target, first_event = limiter.get_max_speed(80)
    assert 70 <= target <= 80
    assert not first_event
    assert limiter.cam_decel
    assert limiter.started_dist == started_distance
    if distance <= 170.25:
      assert target == 70

  # The precise value must release the camera even though Int16 truncation
  # still places -87.5001 inside the 87.5 m hold range.
  limiter.naviData.camLimitSpeedLeftDist = -87
  limiter.naviData.camLimitSpeedLeftDistPrecise = -87.5001
  assert not limiter.get_camera_limit_active()
  assert limiter.get_max_speed(80) == (0, False)
  assert not limiter.cam_decel


@pytest.mark.parametrize("cam_type", REAR_CAMERA_TYPES)
@pytest.mark.parametrize("distance", [0.0, -10.0])
def test_rear_camera_can_enter_hold_after_first_received_message(limiter, cam_type, distance):
  limiter.naviData = make_navi_data(cam_type, distance=distance, rear=True)
  assert limiter.get_camera_limit_active()
  assert limiter.get_max_speed(80) == (70, True)
  assert limiter.cam_decel


@pytest.mark.parametrize("age", [3.0, 3.01])
@pytest.mark.parametrize("distance", [100.0, 0.0, -10.0])
def test_expired_rear_camera_releases_even_with_new_udp_messages(limiter, age, distance):
  limiter.naviData = make_navi_data(75, distance=100, rear=True)
  assert limiter.get_max_speed(80) == (70, True)
  limiter.naviData.camLimitSpeedLeftDist = int(distance)
  limiter.naviData.camLimitSpeedLeftDistPrecise = distance
  limiter.naviData.camDistanceAge = age
  assert not limiter.get_camera_limit_active()
  assert limiter.get_max_speed(80) == (0, False)
  assert not limiter.cam_decel


def test_rear_hold_releases_to_ongoing_section_limit(limiter):
  limiter.naviData = make_navi_data(87, distance=-10, rear=True)
  limiter.naviData.sectionLimitSpeed = 60
  limiter.naviData.sectionLeftDist = 1000
  assert limiter.get_max_speed(80) == (70, True)
  limiter.naviData.camLimitSpeedLeftDist = -87
  limiter.naviData.camLimitSpeedLeftDistPrecise = -87.5001
  assert limiter.get_camera_limit_active()  # The independent section remains.
  assert limiter.get_max_speed(80) == (60, True)
  assert not limiter.cam_decel
  assert limiter.sec_decel


def test_new_camera_far_ahead_releases_previous_rear_hold_deceleration(limiter):
  limiter.naviData = make_navi_data(75, distance=-20.0, rear=True)
  assert limiter.get_max_speed(80) == (70, True)
  assert limiter.cam_decel
  limiter.naviData.camDistanceId += 1
  limiter.naviData.camLimitSpeedLeftDist = 600
  limiter.naviData.camLimitSpeedLeftDistPrecise = 600.0
  assert limiter.get_camera_limit_active()
  assert limiter.get_max_speed(80) == (0, False)
  assert not limiter.cam_decel


@pytest.mark.parametrize("cam_type", [2, 86, 88])
def test_section_start_camera_keeps_approach_deceleration(limiter, cam_type):
  limiter.naviData = make_navi_data(cam_type, distance=399, rear=cam_type != 2)
  limiter.naviData.sectionLimitSpeed = 70
  limiter.naviData.sectionLeftDist = 2000
  target, first_event = limiter.get_max_speed(80)
  assert 70 < target <= 80
  assert first_event
  assert limiter.cam_decel


def test_rear_udp_preserves_fractional_distance_and_large_sample_id(monkeypatch):
  road_limit = {
    "road_limit_speed": 70,
    "cam_type": 75,
    "cam_limit_speed": 70,
    "cam_limit_speed_left_dist": -0.9,
    "cam_rear_hold_dist": 87.5,
    "cam_distance_age": 0.25,
    "cam_distance_id": 9223372036854775807,
  }
  payload = json.dumps({"active": 1, "road_limit": road_limit}).encode()
  sock = SimpleNamespace(recvfrom=lambda size: (payload, ("192.0.2.1", 12345)))
  monkeypatch.setattr(navi_controller.select, "select", lambda *args: ([sock], [], []))
  server = navi_controller.NaviServer(start_broadcast=False)
  assert server.udp_recv(sock)
  assert server.get_json_val(server.json_road_limit, "cam_limit_speed_left_dist", 0) == -0.9
  assert server.get_limit_val("cam_rear_hold_dist", 0) == 87.5
  assert server.get_limit_val("cam_distance_age", 0) == 0.25
  assert server.get_limit_val("cam_distance_id", 0) == 9223372036854775807


@pytest.mark.parametrize("key", ["cam_rear_hold_dist", "cam_distance_age"])
@pytest.mark.parametrize("value", [-0.1, float("nan"), float("inf"), -float("inf"), True, "0.25", None])
def test_rear_metadata_rejects_invalid_float_values(key, value):
  assert not navi_controller.NaviServer._valid_road_limit({key: value})


@pytest.mark.parametrize("key", ["cam_rear_hold_dist", "cam_distance_age"])
@pytest.mark.parametrize("value", [0, 0.0, 0.25, 87.5])
def test_rear_metadata_accepts_finite_nonnegative_values(key, value):
  assert navi_controller.NaviServer._valid_road_limit({key: value})


@pytest.mark.parametrize("value", [-1, 2 ** 63, 1.0, 1.5, True, "123", None, float("nan"), float("inf")])
def test_source_sample_id_rejects_non_int64_values(value):
  assert not navi_controller.NaviServer._valid_road_limit({"cam_distance_id": value})


@pytest.mark.parametrize("value", [0, 1, 32768, 2 ** 53 + 1, 2 ** 63 - 1])
def test_source_sample_id_accepts_exact_nonnegative_int64(value):
  assert navi_controller.NaviServer._valid_road_limit({"cam_distance_id": value})


def test_invalid_rear_update_leaves_last_accepted_data_unchanged(monkeypatch):
  server = navi_controller.NaviServer(start_broadcast=False)
  previous = {"cam_type": 75, "cam_limit_speed": 70, "cam_limit_speed_left_dist": 100}
  server.json_road_limit = previous
  server.active = 1
  server.last_updated = 100.0
  payload = json.dumps({"active": 0, "road_limit": {"cam_distance_id": 2 ** 63}}).encode()
  sock = SimpleNamespace(recvfrom=lambda size: (payload, ("192.0.2.1", 12345)))
  monkeypatch.setattr(navi_controller.select, "select", lambda *args: ([sock], [], []))
  server.udp_recv(sock)
  assert server.json_road_limit is previous
  assert server.active == 1
  assert server.last_updated == 100.0


def test_sdp_advertises_rear_capable_protocol():
  server = navi_controller.NaviServer(start_broadcast=False)
  server.remote_addr = ("192.0.2.1", 12345)
  sock = MagicMock()
  server.send_sdp(sock)
  sock.sendto.assert_called_once_with(b"EON:ROAD_LIMIT_SERVICE:v2", ("192.0.2.1", navi_controller.Port.BROADCAST_PORT))


def test_broadcast_advertises_rear_capable_protocol(monkeypatch):
  sock = MagicMock()
  sock.__enter__.return_value = sock
  monkeypatch.setattr(navi_controller.socket, "socket", lambda *args: sock)
  monkeypatch.setattr(navi_controller, "terminate_flag", SimpleNamespace(is_set=MagicMock(side_effect=[False, True])))
  monkeypatch.setattr(navi_controller.time, "sleep", lambda seconds: None)
  server = navi_controller.NaviServer(start_broadcast=False)
  monkeypatch.setattr(server, "get_broadcast_address", lambda: "192.0.2.100")
  server.broadcast_thread()
  assert sock.sendto.call_count == 254
  assert all(call.args[0] == b"EON:ROAD_LIMIT_SERVICE:v2" for call in sock.sendto.call_args_list)
  assert sock.sendto.call_args_list[0].args[1] == ("192.0.2.1", navi_controller.Port.BROADCAST_PORT)
  assert sock.sendto.call_args_list[-1].args[1] == ("192.0.2.254", navi_controller.Port.BROADCAST_PORT)


def run_publisher(monkeypatch, road_limit, *, frames=6, after_frame=None):
  """Run the production publishing loop against deterministic message/socket fakes."""
  now = [100.0]
  stop = [False]
  published = []

  class FakeSubMaster:
    updated = {"carState": True}

    def __init__(self, services):
      assert services == ["carState"]

    def update(self, timeout):
      pass

    def __getitem__(self, service):
      assert service == "carState"
      return SimpleNamespace(vEgo=20.0)

  class FakeMessage:
    def __init__(self):
      self.naviData = SimpleNamespace()

    def to_bytes(self):
      return self.naviData

  class FakeRatekeeper:
    def __init__(self, rate, **kwargs):
      assert rate == 10

    def keep_time(self):
      now[0] += 0.5
      if after_frame is not None:
        after_frame(server, len(published), now[0])
      stop[0] = len(published) >= frames

  monkeypatch.setattr(navi_controller, "terminate_flag", SimpleNamespace(is_set=lambda: stop[0]))
  monkeypatch.setattr(navi_controller.time, "monotonic", lambda: now[0])
  monkeypatch.setattr(navi_controller, "Ratekeeper", FakeRatekeeper)
  monkeypatch.setattr(navi_controller.socket, "socket", lambda *args: MagicMock())
  monkeypatch.setattr(navi_controller, "messaging", SimpleNamespace(
    SubMaster=FakeSubMaster,
    pub_sock=lambda service: SimpleNamespace(send=published.append),
    new_message=lambda service, **kwargs: FakeMessage(),
  ))
  server = navi_controller.NaviServer(start_broadcast=False)
  server.active = 1
  server.remote_addr = ("192.0.2.1", 12345)
  server.last_updated = now[0]
  server.last_updated_active = now[0]
  server.json_road_limit = dict(road_limit)
  monkeypatch.setattr(server, "check", lambda: None)
  navi_controller.publish_thread(server)
  return published


def rear_road_limit(distance):
  return {
    "cam_type": 75,
    "cam_limit_speed": 70,
    "cam_limit_speed_left_dist": distance,
    "cam_rear_hold_dist": 87.5,
    "cam_distance_age": 0.5,
    "cam_distance_id": 1234567890123456,
  }


def test_publisher_compensates_source_age_and_keeps_negative_distance(monkeypatch):
  from openpilot.selfdrive.addon.navi_helpers import camera_limit_active

  published = run_publisher(monkeypatch, rear_road_limit(10.0))
  assert [n.camLimitSpeedLeftDistPrecise for n in published[:-1]] == pytest.approx([0.0, -10.0, -20.0, -30.0, -40.0])
  assert [n.camLimitSpeedLeftDist for n in published[:-1]] == [0, -10, -20, -30, -40]
  assert [n.camDistanceAge for n in published] == pytest.approx([0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
  assert all(camera_limit_active(n) for n in published[:-1])
  assert published[-1].camLimitSpeed == 0
  assert not camera_limit_active(published[-1])


def test_publisher_preserves_fractional_rear_distance(monkeypatch):
  published = run_publisher(monkeypatch, rear_road_limit(-0.9), frames=1)
  assert published[0].camLimitSpeedLeftDistPrecise == pytest.approx(-10.9)
  assert published[0].camLimitSpeedLeftDist == -10
  assert published[0].camDistanceId == 1234567890123456


def test_publisher_duplicate_udp_packets_cannot_restart_age_or_distance(monkeypatch):
  def duplicate(server, frame, now):
    server.last_updated = now
    server.json_road_limit["cam_distance_age"] = 0.5
    server.json_road_limit["cam_limit_speed_left_dist"] = 100.0

  published = run_publisher(monkeypatch, rear_road_limit(100.0), after_frame=duplicate)
  assert [n.camLimitSpeedLeftDistPrecise for n in published[:-1]] == pytest.approx([90.0, 80.0, 70.0, 60.0, 50.0])
  assert published[-1].camDistanceAge >= 3.0
  assert published[-1].camLimitSpeed == 0


def test_publisher_releases_when_rear_camera_payload_disappears(monkeypatch):
  from openpilot.selfdrive.addon.navi_helpers import camera_limit_active

  def remove(server, frame, now):
    server.json_road_limit = None

  published = run_publisher(monkeypatch, rear_road_limit(-10.0), frames=2, after_frame=remove)
  assert camera_limit_active(published[0])
  assert published[1].camLimitSpeed == 0
  assert not camera_limit_active(published[1])


def rear_section_road_limit(distance):
  values = rear_road_limit(distance)
  values.update({
    "cam_type": 86,
    "road_limit_speed": 70,
    "section_limit_speed": 60,
    "section_left_dist": 1000,
    "section_avg_speed": 65,
    "section_adjust_speed": True,
    "current_road_name": "금벽로",
  })
  return values


def test_publisher_rear_source_expiry_releases_all_guidance_and_section_limit(monkeypatch, limiter):
  published = run_publisher(monkeypatch, rear_section_road_limit(100.0))
  assert published[0].active == 1
  assert published[0].sectionLimitSpeed == 60
  assert published[0].sectionLeftDist == 1000
  expired = published[-1]
  assert expired.camDistanceAge >= 3.0
  assert expired.active == 0
  assert expired.roadLimitSpeed == 0
  assert expired.camLimitSpeed == 0
  assert expired.camRearHoldDist == 0.0
  assert expired.sectionLimitSpeed == 0
  assert expired.sectionLeftDist == 0
  assert expired.sectionAvgSpeed == 0
  assert expired.sectionAdjustSpeed is False
  assert expired.currentRoadName == ""
  limiter.naviData = expired
  assert not limiter.get_camera_limit_active()
  assert limiter.get_max_speed(80) == (0, False)


def test_publisher_rear_hold_distance_expiry_keeps_fresh_section_guidance(monkeypatch, limiter):
  published = run_publisher(monkeypatch, rear_section_road_limit(-80.0), frames=1)
  current = published[0]
  assert current.camDistanceAge == 0.5
  assert current.camLimitSpeed == 0
  assert current.camRearHoldDist == 0.0
  assert current.active == 1
  assert current.roadLimitSpeed == 70
  assert current.sectionLimitSpeed == 60
  assert current.sectionLeftDist == 1000
  assert current.sectionAvgSpeed == 65
  assert current.sectionAdjustSpeed is True
  assert current.currentRoadName == "금벽로"
  limiter.naviData = current
  assert limiter.get_camera_limit_active()
  assert limiter.get_max_speed(80) == (57.5, True)
  assert limiter.sec_decel
  assert not limiter.cam_decel


@pytest.mark.parametrize("reconnect_reason", ["peer_change", "inactive_gap"])
def test_publisher_accepts_lower_source_id_after_session_change_without_disconnected_frame(monkeypatch, reconnect_reason):
  def reconnect(server, frame, now):
    if frame != 1:
      return
    new_data = rear_road_limit(200.0)
    new_data["cam_distance_id"] = 42
    remote_addr = ("192.0.2.2" if reconnect_reason == "peer_change" else "192.0.2.1", 12345)
    if reconnect_reason == "inactive_gap":
      server.last_updated_active = now - 7.0
    payload = json.dumps({"active": 1, "road_limit": new_data}).encode()
    sock = SimpleNamespace(recvfrom=lambda size: (payload, remote_addr))
    monkeypatch.setattr(navi_controller.select, "select", lambda *args: ([sock], [], []))
    assert server.udp_recv(sock)

  published = run_publisher(monkeypatch, rear_road_limit(100.0), frames=2, after_frame=reconnect)
  assert published[0].camDistanceId == 1234567890123456
  assert published[1].camDistanceId == 42
  assert published[1].camLimitSpeed == 70
  assert published[1].camLimitSpeedLeftDistPrecise == pytest.approx(190.0)


def test_udp_reordered_source_id_does_not_replace_current_camera(monkeypatch):
  now = [100.0]
  monkeypatch.setattr(navi_controller.time, "monotonic", lambda: now[0])
  server = navi_controller.NaviServer(start_broadcast=False)
  sock = MagicMock()
  monkeypatch.setattr(navi_controller.select, "select", lambda *args: ([sock], [], []))
  new_data = rear_road_limit(-10.0)
  old_data = rear_road_limit(200.0)
  old_data["cam_distance_id"] -= 1
  remote_addr = ("192.0.2.1", 12345)
  sock.recvfrom.side_effect = [
    (json.dumps({"active": 1, "road_limit": new_data}).encode(), remote_addr),
    (json.dumps({"active": 1, "road_limit": old_data}).encode(), remote_addr),
  ]
  assert server.udp_recv(sock)
  now[0] = 100.5
  assert not server.udp_recv(sock)
  assert server.json_road_limit == new_data
  assert server.last_updated == 100.0
