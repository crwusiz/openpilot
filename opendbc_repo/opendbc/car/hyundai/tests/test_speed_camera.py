import math
from types import SimpleNamespace

from opendbc.car.hyundai.navi_state import NAVI_POSITION_TIMEOUT_NS, NAVI_SPEED_CAMERA_PARAM_UPDATE_FRAMES, NaviState


class FakeParams:
  def __init__(self, values=None):
    self.values = {
      "VehicleNaviCanControl": False,
      "VehicleNaviSchoolZoneControl": False,
      "VehicleNaviCurveControl": False,
      "VehicleNaviCurveMppControl": False,
      "VehicleNaviCurveSpeedFactor": 100,
      "VehicleNaviCurveCtrlEnd": 3,
      "VehicleSpeedCameraDistanceTime": 60,
      "AutoCurveSpeedLowerLimit": 30,
      "AutoNaviSpeedDecelRate": 120,
    }
    self.values.update(values or {})

  def get_bool(self, key):
    return bool(self.values[key])

  def get_int(self, key):
    return int(self.values[key])


def _navi_state():
  state = NaviState()
  state.school_zone_control = False
  state.position = {"RangeAvgSpeed": 103}
  return state


def _ret(speed_limit=100.0):
  return SimpleNamespace(speedLimit=speed_limit)


def test_navi_controls_follow_params():
  params = FakeParams({
    "VehicleNaviCanControl": True,
    "VehicleNaviSchoolZoneControl": True,
    "VehicleNaviCurveControl": True,
    "VehicleNaviCurveSpeedFactor": 120,
    "VehicleNaviCurveCtrlEnd": 4,
    "VehicleSpeedCameraDistanceTime": 75,
  })
  state = NaviState(params)

  assert state.can_control
  assert state.school_zone_control
  assert state.curve_speed_factor == 1.2
  assert state.curve_control_end == 4.0

  state.speed_camera_params_counter = NAVI_SPEED_CAMERA_PARAM_UPDATE_FRAMES - 1
  assert state._update_speed_camera_params()
  assert state.speed_camera_distance_time == 7.5


def test_navi_range_average_holds_section_until_zero():
  state = _navi_state()
  cp = SimpleNamespace(ts_nanos={"Hud_Navi_V2_POS_PE": {"RangeAvgSpeed": 1_000_000_000}},
                       _last_update_nanos=1_100_000_000)
  ret = _ret()

  assert not state._update_events(cp, ret, True)
  assert state.speed_zone_active
  assert ret.naviSectionActive
  assert ret.naviSpeed == 100

  state.position["RangeAvgSpeed"] = 0
  cp.ts_nanos["Hud_Navi_V2_POS_PE"]["RangeAvgSpeed"] = 1_200_000_000
  cp._last_update_nanos = 1_200_000_000
  assert not state._update_events(cp, ret, True)
  assert not state.speed_zone_active
  assert not ret.naviSectionActive


def test_navi_stale_range_average_releases_section():
  state = _navi_state()
  cp = SimpleNamespace(ts_nanos={"Hud_Navi_V2_POS_PE": {"RangeAvgSpeed": 1_000_000_000}},
                       _last_update_nanos=1_000_000_000)
  ret = _ret()

  assert not state._update_events(cp, ret, True)
  cp._last_update_nanos += NAVI_POSITION_TIMEOUT_NS + 1
  assert not state._update_events(cp, ret, True)
  assert not state.speed_zone_active
  assert not ret.naviSectionActive


def test_navi_range_average_does_not_keep_regular_cap_in_school_zone():
  state = _navi_state()
  state.speed_zone_active = True
  state.speed_zone_speed = 100
  state.position["RangeAvgSpeed"] = 25
  cp = SimpleNamespace(ts_nanos={"Hud_Navi_V2_POS_PE": {"RangeAvgSpeed": 1_000_000_000}},
                       _last_update_nanos=1_000_000_000)
  ret = _ret(30.0)

  assert not state._update_events(cp, ret, True)
  assert not state.speed_zone_active
  assert not ret.naviSectionActive


def test_navi_availability_only_requires_receiving_0x4be():
  state = _navi_state()
  cp = SimpleNamespace(ts_nanos={"Hud_Navi_V2_PROLONG_E": {"Value": 1}})
  ret = _ret(0.0)

  assert not state._update_events(cp, ret, False)
  assert ret.naviAvailable

  cp.ts_nanos["Hud_Navi_V2_PROLONG_E"]["Value"] = 0
  assert not state._update_events(cp, ret, False)
  assert ret.naviAvailable


def test_navi_segment_decodes_functional_road_class():
  raw = (1 << 24) | (1 << 22) | 123
  segment = NaviState._decode_segment({
    f"BYTE_{i + 1}": byte for i, byte in enumerate(raw.to_bytes(8, "little"))
  })

  assert segment == {"offset": 123, "calculated_route": 1, "functional_road_class": 1}


def test_navi_school_zone_is_blocked_by_controlled_access_link_class():
  for link_class in (1, 2, 3):
    state = _navi_state()
    state.school_zone_control = True
    state.hda_info = {"LinkClass": link_class}
    state.profile = {
      "Value": 0x77,
      "Offset": 0,
      "CyclicCounter": 3,
      "Update": 1,
      "ProfileType": 16,
    }
    cp = SimpleNamespace(ts_nanos={"Hud_Navi_V2_PROLONG_E": {"Value": 1}})
    ret = _ret(30.0)

    assert not state._update_events(cp, ret, True)
    assert not ret.schoolZoneActive
    assert not state.school_zone_active


def test_navi_speed_bump_is_blocked_on_controlled_access_road():
  for road_class in (1, 2):
    state = _navi_state()
    state.road_class = road_class
    state.profile = {
      "Value": 6,
      "Offset": 300,
      "CyclicCounter": 3,
      "Update": 1,
      "ProfileType": 16,
    }
    cp = SimpleNamespace(ts_nanos={"Hud_Navi_V2_PROLONG_E": {"Value": 1}})
    ret = _ret()

    assert not state._update_events(cp, ret, False)
    assert ret.speedBumpDistance == 0.0
    assert state.events == []


def test_30_kph_camera_distance_is_blocked_on_controlled_access_road():
  for road_class in (1, 2):
    state = NaviState()
    state.road_class = road_class
    ret = SimpleNamespace(vEgo=20.0, speedLimit=30.0)

    state.update_speed_limit(ret, speed_limit_cam=True)

    assert ret.speedLimit == 30.0
    assert ret.speedLimitDistance == 0.0


def test_adasis_v2_curvature_decoder():
  cases = (
    (511, 0.0),
    (512, 0.00001),
    (599, 0.00112),
    (656, 0.00260),
    (819, 0.01792),
    (1023, None),
  )
  for raw_value, expected in cases:
    decoded = NaviState._decode_adasis_curvature(raw_value)
    if expected is None:
      assert decoded is None
    else:
      assert math.isclose(decoded, expected)


def test_navi_curve_profile_publishes_reference_speed_and_distance():
  state = _navi_state()
  state.curve_route_active = True
  state.profile_short = {
    "Offset": 313,
    "Distance": 5,
    "Value0": 599,
    "ProfileType": 1,
  }
  cp = SimpleNamespace(ts_nanos={"Hud_Navi_V2_PROSHORT_E_00": {"Value0": 1}})
  ret = _ret(0.0)

  assert not state._update_events(cp, ret, False)
  assert math.isclose(ret.naviCurveDistance, 313.0)
  assert math.isclose(ret.naviCurveCurvature, 0.00112)
  assert math.isclose(ret.naviCurveSpeed, math.sqrt(1.9 / 0.00112) * 3.6)
  assert ret.naviCurveRouteActive
  assert state.curves[0]["span"] == 10.0


def test_navi_curve_releases_passed_apex_and_uses_following_spot():
  state = _navi_state()
  state.curves = [
    {"target": 100.0, "span": 10.0, "curvature": 0.01, "speed": 50.0},
    {"target": 140.0, "span": 10.0, "curvature": 0.004, "speed": 80.0},
  ]
  ret = SimpleNamespace()
  cp = SimpleNamespace(ts_nanos={})

  state.total_distance = 100.0
  state._update_curve_profile(cp, ret)
  assert ret.naviCurveDistance == 0.0
  assert ret.naviCurveSpeed == 50.0

  state.total_distance = 100.1
  state._update_curve_profile(cp, ret)
  assert math.isclose(ret.naviCurveDistance, 39.9)
  assert ret.naviCurveSpeed == 80.0


def test_navi_curve_without_following_spot_releases_after_apex():
  state = _navi_state()
  state.curves = [{"target": 100.0, "span": 10.0, "curvature": 0.01, "speed": 50.0}]
  ret = SimpleNamespace()
  cp = SimpleNamespace(ts_nanos={})

  state.total_distance = 100.1
  state._update_curve_profile(cp, ret)
  assert ret.naviCurveSpeed == 0.0


def test_navi_curve_skips_only_short_hairpin_speed_spot():
  state = _navi_state()
  state._add_curve({"offset": 100.0, "span": 10.0, "curvature": 0.12288})
  assert state.curves == []

  state._add_curve({"offset": 110.0, "span": 10.0, "curvature": 0.04864})
  assert len(state.curves) == 1
  assert math.isclose(state.curves[0]["speed"], math.sqrt(1.9 / 0.04864) * 3.6)


def test_navi_route_recalculation_clears_curve_profile():
  state = _navi_state()
  state.curves = [{"target": 300.0, "curvature": 0.01, "speed": 50.0}]
  raw = bytes.fromhex("0000b8063110fdff")
  state.segment = {f"BYTE_{i + 1}": byte for i, byte in enumerate(raw)}
  cp = SimpleNamespace(ts_nanos={"Hud_Navi_V2_SEG_E": {"BYTE_1": 2}})
  ret = _ret(0.0)

  assert not state._update_events(cp, ret, False)
  assert state.curves == []
  assert ret.naviCurveSpeed == 0.0


def test_navi_curve_route_state_tracks_calculated_route():
  state = _navi_state()
  raw = (1 << 22).to_bytes(8, "little")
  state.segment = {f"BYTE_{i + 1}": byte for i, byte in enumerate(raw)}
  cp = SimpleNamespace(ts_nanos={"Hud_Navi_V2_SEG_E": {"BYTE_1": 1}})
  ret = _ret(0.0)

  assert not state._update_events(cp, ret, False)
  assert state.curve_route_active
  assert ret.naviCurveRouteActive
  assert ret.naviCurveRouteState == 1

  state.segment = {f"BYTE_{i + 1}": 0 for i in range(8)}
  cp.ts_nanos["Hud_Navi_V2_SEG_E"]["BYTE_1"] = 2
  assert not state._update_events(cp, ret, False)
  assert not state.curve_route_active
  assert ret.naviCurveRouteState == 0
