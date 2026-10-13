from pathlib import Path

import capnp
import pytest


# The original wire layout, before the rear-camera fields were appended.
LEGACY_SCHEMA = """
@0xb526ba661d550a59;
struct NaviData @0x81c2f05a394cf4af {
  active @0 :Int16;
  roadLimitSpeed @1 :Int16;
  isHighway @2 :Bool;
  camType @3 :Int16;
  camLimitSpeedLeftDist @4 :Int16;
  camLimitSpeed @5 :Int16;
  sectionLimitSpeed @6 :Int16;
  sectionLeftDist @7 :Int16;
  sectionAvgSpeed @8 :Int16;
  sectionLeftTimeDEPRECATED @9 :Int16;
  sectionAdjustSpeed @10 :Bool;
  camSpeedFactor @11 :Float32;
  currentRoadName @12 :Text;
  ts @13 :TrafficSignal;
  struct TrafficSignal {
    isGreenLightOn @0 :Bool;
    isLeftLightOn @1 :Bool;
    isRedLightOn @2 :Bool;
    greenLightRemainTime @3 :Int16;
    leftLightRemainTime @4 :Int16;
    redLightRemainTime @5 :Int16;
    distance @6 :Int16;
  }
}
"""


@pytest.fixture(scope="module")
def schemas(tmp_path_factory):
  cereal = Path(__file__).resolve().parents[3] / "cereal"
  legacy_path = tmp_path_factory.mktemp("navi_schema") / "legacy_custom.capnp"
  legacy_path.write_text(LEGACY_SCHEMA, encoding="utf-8")
  new_parser = capnp.SchemaParser()
  old_parser = capnp.SchemaParser()
  new = new_parser.load(str(cereal / "custom.capnp"), imports=[str(cereal)])
  old = old_parser.load(str(legacy_path))
  yield new.NaviData, old.NaviData


def test_rear_metadata_roundtrips_and_legacy_reader_keeps_original_fields(schemas):
  new, old = schemas
  message = new.new_message(
    active=1, roadLimitSpeed=70, camType=75, camLimitSpeed=70, camLimitSpeedLeftDist=-10,
    camRearHoldDist=87.5, camDistanceAge=0.25, camDistanceId=2**63 - 1, camLimitSpeedLeftDistPrecise=-10.5,
    currentRoadName="금벽로", ts={"isRedLightOn": True, "distance": 50},
  )
  encoded = message.to_bytes()
  with new.from_bytes(encoded) as reader:
    assert reader.camRearHoldDist == 87.5
    assert reader.camDistanceAge == 0.25
    assert reader.camDistanceId == 2**63 - 1
    assert reader.camLimitSpeedLeftDistPrecise == -10.5
  with old.from_bytes(encoded) as reader:
    assert (reader.active, reader.roadLimitSpeed, reader.camType, reader.camLimitSpeed) == (1, 70, 75, 70)
    assert reader.camLimitSpeedLeftDist == -10
    assert reader.currentRoadName == "금벽로"
    assert reader.ts.isRedLightOn
    assert reader.ts.distance == 50


def test_legacy_message_reads_with_zero_defaults_for_new_fields(schemas):
  new, old = schemas
  message = old.new_message(active=1, camType=3, camLimitSpeed=70, camLimitSpeedLeftDist=100)
  with new.from_bytes(message.to_bytes()) as reader:
    assert (reader.camType, reader.camLimitSpeed, reader.camLimitSpeedLeftDist) == (3, 70, 100)
    assert reader.camRearHoldDist == 0
    assert reader.camDistanceAge == 0
    assert reader.camDistanceId == 0
    assert reader.camLimitSpeedLeftDistPrecise == 0
