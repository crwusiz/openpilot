import re
from dataclasses import dataclass
from enum import Enum, IntFlag, StrEnum
from typing import Dict, List, Optional, Set, Tuple, Union

from cereal import car
from panda.python import uds
from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.car import dbc_dict
from openpilot.selfdrive.car.docs_definitions import CarFootnote, CarHarness, CarInfo, CarParts, Column
from openpilot.selfdrive.car.fw_query_definitions import FwQueryConfig, Request, p16

Ecu = car.CarParams.Ecu


class CarControllerParams:
  def __init__(self, CP):
    self.STEER_MAX = 384
    self.STEER_DELTA_UP = 3
    self.STEER_DELTA_DOWN = 7
    self.STEER_DRIVER_ALLOWANCE = 50
    self.STEER_DRIVER_MULTIPLIER = 2
    self.STEER_DRIVER_FACTOR = 1
    self.STEER_THRESHOLD = 150
    self.STEER_STEP = 1  # 100 Hz

    if CP.carFingerprint in CANFD_CAR:
      self.STEER_MAX = 270
      self.STEER_DRIVER_ALLOWANCE = 250
      self.STEER_DRIVER_MULTIPLIER = 2
      self.STEER_THRESHOLD = 250
      self.STEER_DELTA_UP = 2
      self.STEER_DELTA_DOWN = 3


class HyundaiFlags(IntFlag):
  CANFD_HDA2 = 1
  CANFD_ALT_BUTTONS = 2
  CANFD_ALT_GEARS = 4
  CANFD_CAMERA_SCC = 8

  ALT_LIMITS = 16
  ENABLE_BLINKERS = 32
  CANFD_ALT_GEARS_2 = 64
  SEND_LFA = 128
  USE_FCA = 256
  CANFD_HDA2_ALT_STEERING = 512

class CAR(StrEnum):
  # Hyundai
  ELANTRA_I30 = "HYUNDAI AVANTE,I30 (AD,PD)"
  ELANTRA_CN7 = "HYUNDAI AVANTE (CN7)"
  ELANTRA_CN7_HEV = "HYUNDAI AVANTE HEV (CN7)"
  SONATA_DN8 = "HYUNDAI SONATA (DN8)"
  SONATA_DN8_HEV = "HYUNDAI SONATA HEV (DN8)"
  SONATA_LF = "HYUNDAI SONATA (LF)"
  SONATA_LF_HEV = "HYUNDAI SONATA HEV (LF)"
  KONA = "HYUNDAI KONA (OS)"
  KONA_EV = "HYUNDAI KONA EV (OS)"
  KONA_HEV = "HYUNDAI KONA HEV (OS)"
  IONIQ = "HYUNDAI IONIQ (AE)"
  IONIQ_EV = "HYUNDAI IONIQ EV (AE)"
  IONIQ_HEV = "HYUNDAI IONIQ HEV (AE)"
  TUCSON = "HYUNDAI TUCSON (TL)"
  SANTAFE = "HYUNDAI SANTAFE (TM)"
  SANTAFE_HEV = "HYUNDAI SANTAFE HEV (TM)"
  PALISADE = "HYUNDAI PALISADE (LX2)"
  VELOSTER = "HYUNDAI VELOSTER (JS)"
  GRANDEUR_IG = "HYUNDAI GRANDEUR (IG)"
  GRANDEUR_IG_HEV = "HYUNDAI GRANDEUR HEV (IG)"
  GRANDEUR_IGFL = "HYUNDAI GRANDEUR (IG_FL)"
  GRANDEUR_IGFL_HEV = "HYUNDAI GRANDEUR HEV (IG_FL)"
  NEXO = "HYUNDAI NEXO (FE)"

  # Kia
  FORTE = "KIA K3 (BD)"
  K5 = "KIA K5 (JF)"
  K5_HEV = "KIA K5 HEV (JF)"
  K5_DL3 = "KIA K5 (DL3)"
  K5_DL3_HEV = "KIA K5 HEV (DL3)"
  K7 = "KIA K7 (YG)"
  K7_HEV = "KIA K7 HEV (YG)"
  K9 = "KIA K9 (RJ)"
  SPORTAGE = "KIA SPORTAGE (QL)"
  SORENTO = "KIA SORENTO (UM)"
  MOHAVE = "KIA MOHAVE (HM)"
  STINGER = "KIA STINGER (CK)"
  NIRO_EV = "KIA NIRO EV (DE)"
  NIRO_HEV = "KIA NIRO HEV (DE)"
  SOUL_EV = "KIA SOUL EV (SK3)"
  SELTOS = "KIA SELTOS (SP2)"

  # Genesis
  GENESIS = "GENESIS (DH)"
  GENESIS_G70 = "GENESIS G70 (IK)"
  GENESIS_G80 = "GENESIS G80 (DH)"
  GENESIS_G90 = "GENESIS EQ900, G90 (HI)"

  # CANFD
  # Hyundai
  IONIQ5 = "HYUNDAI IONIQ 5 (NE1)"
  IONIQ6 = "HYUNDAI IONIQ 6 (CE1)"
  KONA_SX2_EV = "HYUNDAI KONA EV (SX2)"
  TUCSON_NX4 = "HYUNDAI TUCSON (NX4)"
  TUCSON_NX4_HEV = "HYUNDAI TUCSON HEV (NX4)"
  STARIA = "HYUNDAI STARIA (US4)"
  SONATA_DN8_24 = "HYUNDAI SONATA_2024 (DN8)"

  # Kia
  EV6 = "KIA EV6 (CV)"
  SPORTAGE_NQ5 = "KIA SPORTAGE (NQ5)"
  SPORTAGE_NQ5_HEV = "KIA SPORTAGE HEV (NQ5)"
  SORENTO_MQ4 = "KIA SORENTO (MQ4)"
  SORENTO_MQ4_HEV = "KIA SORENTO HEV (MQ4)"
  NIRO_SG2_EV = "KIA NIRO EV (SG2)"
  NIRO_SG2_HEV = "KIA NIRO HEV (SG2)"
  CARNIVAL_KA4 = "KIA CARNIVAL (KA4)"
  K8_GL3 = "KIA K8 (GL3)"
  K8_GL3_HEV = "KIA K8 HEV (GL3)"
  EV9 = "KIA EV9 (MV)"
  K5_DL3_24 = "KIA K5_2024 (DL3)"
  K5_DL3_24_HEV = "KIA K5_2024 HEV (DL3)"

  # Genesis
  GENESIS_GV60_EV = "GENESIS GV60 (JW1)"
  GENESIS_GV70 = "GENESIS GV70 (JK1)"
  GENESIS_GV80 = "GENESIS GV80 (JX1)"

class Footnote(Enum):
  CANFD = CarFootnote(
    "Requires a <a href=\"https://comma.ai/shop/can-fd-panda-kit\" target=\"_blank\">CAN FD panda kit</a> if not using " +
    "comma 3X for this <a href=\"https://en.wikipedia.org/wiki/CAN_FD\" target=\"_blank\">CAN FD car</a>.",
    Column.MODEL, shop_footnote=False)


@dataclass
class HyundaiCarInfo(CarInfo):
  package: str = "Smart Cruise Control (SCC)"

  def init_make(self, CP: car.CarParams):
    if CP.carFingerprint in CANFD_CAR:
      self.footnotes.insert(0, Footnote.CANFD)


CAR_INFO: Dict[str, Optional[Union[HyundaiCarInfo, List[HyundaiCarInfo]]]] = {
  CAR.ELANTRA_I30: [
    HyundaiCarInfo("Hyundai Elantra 2017-19", min_enable_speed=19 * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_b])),
    HyundaiCarInfo("Hyundai Elantra GT 2017-19", car_parts=CarParts.common([CarHarness.hyundai_e])),
    HyundaiCarInfo("Hyundai i30 2017-19", car_parts=CarParts.common([CarHarness.hyundai_e])),
  ],
  CAR.ELANTRA_CN7: HyundaiCarInfo("Hyundai Elantra 2021-23", video_link="https://youtu.be/_EdYQtV52-c", car_parts=CarParts.common([CarHarness.hyundai_k])),
  CAR.ELANTRA_CN7_HEV: HyundaiCarInfo("Hyundai Elantra Hybrid 2021-23", video_link="https://youtu.be/_EdYQtV52-c", car_parts=CarParts.common([CarHarness.hyundai_k])),
  CAR.SONATA_DN8: HyundaiCarInfo("Hyundai Sonata 2020-22", "All", video_link="https://www.youtube.com/watch?v=ix63r9kE3Fw", car_parts=CarParts.common([CarHarness.hyundai_a])),
  CAR.SONATA_DN8_HEV: HyundaiCarInfo("Hyundai Sonata Hybrid 2020-22", "All", car_parts=CarParts.common([CarHarness.hyundai_a])),
  CAR.SONATA_LF: HyundaiCarInfo("Hyundai Sonata 2018-19", car_parts=CarParts.common([CarHarness.hyundai_e])),
  CAR.KONA: HyundaiCarInfo("Hyundai Kona 2020", car_parts=CarParts.common([CarHarness.hyundai_b])),
  CAR.KONA_EV: [
    HyundaiCarInfo("Hyundai Kona Electric 2018-21", car_parts=CarParts.common([CarHarness.hyundai_g])),
    HyundaiCarInfo("Hyundai Kona Electric 2022", "Smart Cruise Control (SCC)", car_parts=CarParts.common([CarHarness.hyundai_o])),
  ],
  CAR.KONA_HEV: HyundaiCarInfo("Hyundai Kona Hybrid 2020", car_parts=CarParts.common([CarHarness.hyundai_i])),
  CAR.IONIQ_EV: [
    HyundaiCarInfo("Hyundai Ioniq Electric 2019", car_parts=CarParts.common([CarHarness.hyundai_c])),
    HyundaiCarInfo("Hyundai Ioniq Electric 2020", car_parts=CarParts.common([CarHarness.hyundai_h])),
  ],
  CAR.IONIQ_HEV: [
    HyundaiCarInfo("Hyundai Ioniq Hybrid 2017-19", car_parts=CarParts.common([CarHarness.hyundai_c])),
    HyundaiCarInfo("Hyundai Ioniq Hybrid 2020-22", "SCC + LFA", car_parts=CarParts.common([CarHarness.hyundai_h])),
    HyundaiCarInfo("Hyundai Ioniq Plug-in Hybrid 2019", car_parts=CarParts.common([CarHarness.hyundai_c])),
    HyundaiCarInfo("Hyundai Ioniq Plug-in Hybrid 2020-21", car_parts=CarParts.common([CarHarness.hyundai_h])),
  ],
  CAR.SANTAFE: [
    HyundaiCarInfo("Hyundai Santa Fe 2019-20", "All", car_parts=CarParts.common([CarHarness.hyundai_d])),
    HyundaiCarInfo("Hyundai Santa Fe 2021-22", "All", car_parts=CarParts.common([CarHarness.hyundai_l])),
  ],
  CAR.SANTAFE_HEV: HyundaiCarInfo("Hyundai Santa Fe Hybrid 2022", "All", car_parts=CarParts.common([CarHarness.hyundai_l])),
  CAR.TUCSON: [
    HyundaiCarInfo("Hyundai Tucson 2021", "Smart Cruise Control (SCC)", min_enable_speed=19 * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_l])),
    HyundaiCarInfo("Hyundai Tucson Diesel 2019", "Smart Cruise Control (SCC)", car_parts=CarParts.common([CarHarness.hyundai_l])),
  ],
  CAR.PALISADE: [
    HyundaiCarInfo("Hyundai Palisade 2020-22", "All", video_link="https://youtu.be/TAnDqjF4fDY?t=456", car_parts=CarParts.common([CarHarness.hyundai_h])),
    HyundaiCarInfo("Kia Telluride 2020", "All", car_parts=CarParts.common([CarHarness.hyundai_h])),
  ],
  CAR.VELOSTER: HyundaiCarInfo("Hyundai Veloster 2019-20", "Smart Cruise Control (SCC)", min_enable_speed=5. * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_e])),

  # Kia
  CAR.FORTE: [
    HyundaiCarInfo("Kia Forte 2018", car_parts=CarParts.common([CarHarness.hyundai_b])),
    HyundaiCarInfo("Kia Forte 2019-21", "All", car_parts=CarParts.common([CarHarness.hyundai_g])),
  ],
  CAR.K5: [
    HyundaiCarInfo("Kia Optima 2017", min_steer_speed=32. * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_b])),
    HyundaiCarInfo("Kia Optima 2019", car_parts=CarParts.common([CarHarness.hyundai_g])),
  ],
  CAR.K5_HEV: [
    HyundaiCarInfo("Kia Optima Hybrid 2017", "Advanced Smart Cruise Control"),  # TODO: may support adjacent years
    HyundaiCarInfo("Kia Optima Hybrid 2019"),
  ],
  CAR.K5_DL3: HyundaiCarInfo("Kia K5 2021-22", "SCC", car_parts=CarParts.common([CarHarness.hyundai_a])),
  CAR.NIRO_EV: [
    HyundaiCarInfo("Kia Niro Electric 2019-20", "All", video_link="https://www.youtube.com/watch?v=lT7zcG6ZpGo", car_parts=CarParts.common([CarHarness.hyundai_f])),
    HyundaiCarInfo("Kia Niro Electric 2021", "All", video_link="https://www.youtube.com/watch?v=lT7zcG6ZpGo", car_parts=CarParts.common([CarHarness.hyundai_c])),
    HyundaiCarInfo("Kia Niro Electric 2022", "All", video_link="https://www.youtube.com/watch?v=lT7zcG6ZpGo", car_parts=CarParts.common([CarHarness.hyundai_h])),
  ],
  CAR.NIRO_HEV: [
    HyundaiCarInfo("Kia Niro Hybrid 2019", min_enable_speed=10. * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_c])),
    HyundaiCarInfo("Kia Niro Hybrid 2021", car_parts=CarParts.common([CarHarness.hyundai_f])),  # TODO: could be hyundai_d, verify
    HyundaiCarInfo("Kia Niro Hybrid 2022", car_parts=CarParts.common([CarHarness.hyundai_h])),
  ],
  CAR.K7: HyundaiCarInfo("Kia K7 2016-19", car_parts=CarParts.common([CarHarness.hyundai_c])),
  CAR.K7_HEV: HyundaiCarInfo("Kia K7 Hybrid 2016-19", car_parts=CarParts.common([CarHarness.hyundai_c])),
  CAR.SELTOS: HyundaiCarInfo("Kia Seltos 2021", car_parts=CarParts.common([CarHarness.hyundai_a])),
  CAR.SORENTO: [
    HyundaiCarInfo("Kia Sorento 2018", video_link="https://www.youtube.com/watch?v=Fkh3s6WHJz8", car_parts=CarParts.common([CarHarness.hyundai_c])),
    HyundaiCarInfo("Kia Sorento 2019", video_link="https://www.youtube.com/watch?v=Fkh3s6WHJz8", car_parts=CarParts.common([CarHarness.hyundai_e])),
  ],
  CAR.STINGER: HyundaiCarInfo("Kia Stinger 2018", video_link="https://www.youtube.com/watch?v=MJ94qoofYw0", car_parts=CarParts.common([CarHarness.hyundai_c])),

  # Genesis
  CAR.GENESIS: HyundaiCarInfo("Hyundai Genesis 2015-16", min_enable_speed=19 * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_j])),
  CAR.GENESIS_G70: HyundaiCarInfo("Genesis G70 2018", "All", car_parts=CarParts.common([CarHarness.hyundai_f])),
  CAR.GENESIS_G80: HyundaiCarInfo("Genesis G80 2018", "All", car_parts=CarParts.common([CarHarness.hyundai_h])),
  CAR.GENESIS_G90: HyundaiCarInfo("Genesis G90 2018", "All", car_parts=CarParts.common([CarHarness.hyundai_c])),

  # CANFD
  CAR.IONIQ5: [
    HyundaiCarInfo("Hyundai Ioniq 5 (Southeast Asia only) 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_q])),
    HyundaiCarInfo("Hyundai Ioniq 5 (without HDA II) 2022-23", "Highway Driving Assist", car_parts=CarParts.common([CarHarness.hyundai_k])),
    HyundaiCarInfo("Hyundai Ioniq 5 (with HDA II) 2022-23", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_q])),
  ],
  CAR.IONIQ6: [
    HyundaiCarInfo("Hyundai Ioniq 6 (without HDA II) 2023", "Highway Driving Assist",
                   car_parts=CarParts.common([CarHarness.hyundai_k])),
    HyundaiCarInfo("Hyundai Ioniq 6 (with HDA II) 2023", "Highway Driving Assist II",
                   car_parts=CarParts.common([CarHarness.hyundai_p])),
  ],
  CAR.TUCSON_NX4: [
    HyundaiCarInfo("Hyundai Tucson 2022", car_parts=CarParts.common([CarHarness.hyundai_n])),
    HyundaiCarInfo("Hyundai Tucson 2023", "All", car_parts=CarParts.common([CarHarness.hyundai_n])),
  ],
  CAR.TUCSON_NX4_HEV: HyundaiCarInfo("Hyundai Tucson Hybrid 2022", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_n])),
  CAR.EV6: [
    HyundaiCarInfo("Kia EV6 (Southeast Asia only) 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_p])),
    HyundaiCarInfo("Kia EV6 (without HDA II) 2022", "Highway Driving Assist", car_parts=CarParts.common([CarHarness.hyundai_l])),
    HyundaiCarInfo("Kia EV6 (with HDA II) 2022", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_p])),
  ],
  CAR.SPORTAGE_NQ5: HyundaiCarInfo("Kia Sportage Hybrid 2023", car_parts=CarParts.common([CarHarness.hyundai_n])),
  CAR.SPORTAGE_NQ5_HEV: HyundaiCarInfo("Kia Sportage Hybrid 2023", car_parts=CarParts.common([CarHarness.hyundai_n])),
  CAR.GENESIS_GV60_EV: HyundaiCarInfo("Genesis GV60 2023", "All", car_parts=CarParts.common([CarHarness.hyundai_k])),
  CAR.GENESIS_GV70: HyundaiCarInfo("Genesis GV70 2022", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_l])),
  CAR.GENESIS_GV80: HyundaiCarInfo("Genesis GV80 2023", "All", car_parts=CarParts.common([CarHarness.hyundai_m])),
  CAR.SORENTO_MQ4_HEV: HyundaiCarInfo("Kia Sorento Hybrid 2022-23", "Smart Cruise Control (SCC)", car_parts=CarParts.common([CarHarness.hyundai_a])),
  CAR.NIRO_SG2_EV: HyundaiCarInfo("Kia Niro Ev 2023", car_parts=CarParts.common([CarHarness.hyundai_a])),
  CAR.NIRO_SG2_HEV: HyundaiCarInfo("Kia Niro Hybrid 2023", car_parts=CarParts.common([CarHarness.hyundai_a])),
  CAR.CARNIVAL_KA4: [
    HyundaiCarInfo("Kia Carnival 2023", car_parts=CarParts.common([CarHarness.hyundai_a])),
    HyundaiCarInfo("Kia Carnival (China only) 2023", car_parts=CarParts.common([CarHarness.hyundai_k]))
  ],
  CAR.K8_GL3: HyundaiCarInfo("Kia K8 GL3 (with HDA II) 2023", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_k])),
  CAR.K8_GL3_HEV: HyundaiCarInfo("Kia K8 Hybrid (with HDA II) 2023", "Highway Driving Assist II",
                                         car_parts=CarParts.common([CarHarness.hyundai_q])),
}

class Buttons:
  NONE = 0
  RES_ACCEL = 1
  SET_DECEL = 2
  GAP_DIST = 3
  CANCEL = 4  # on newer models, this is a pause/resume button


def get_platform_codes(fw_versions: List[bytes]) -> Set[Tuple[bytes, Optional[bytes]]]:
  # Returns unique, platform-specific identification codes for a set of versions
  codes = set()  # (code-Optional[part], date)
  for fw in fw_versions:
    code_match = PLATFORM_CODE_FW_PATTERN.search(fw)
    part_match = PART_NUMBER_FW_PATTERN.search(fw)
    date_match = DATE_FW_PATTERN.search(fw)
    if code_match is not None:
      code: bytes = code_match.group()
      part = part_match.group() if part_match else None
      date = date_match.group() if date_match else None
      if part is not None:
        # part number starts with generic ECU part type, add what is specific to platform
        code += b"-" + part[-5:]

      codes.add((code, date))
  return codes


def match_fw_to_car_fuzzy(live_fw_versions, offline_fw_versions) -> Set[str]:
  # Non-electric CAN FD platforms often do not have platform code specifiers needed
  # to distinguish between hybrid and ICE. All EVs so far are either exclusively
  # electric or specify electric in the platform code.
  # TODO: whitelist platforms that we've seen hybrid and ICE versions of that have these specifiers
  fuzzy_platform_blacklist = {str(c) for c in set(CANFD_CAR - EV_CAR)}
  candidates: Set[str] = set()

  for candidate, fws in offline_fw_versions.items():
    # Keep track of ECUs which pass all checks (platform codes, within date range)
    valid_found_ecus = set()
    valid_expected_ecus = {ecu[1:] for ecu in fws if ecu[0] in PLATFORM_CODE_ECUS}
    for ecu, expected_versions in fws.items():
      addr = ecu[1:]
      # Only check ECUs expected to have platform codes
      if ecu[0] not in PLATFORM_CODE_ECUS:
        continue

      # Expected platform codes & dates
      codes = get_platform_codes(expected_versions)
      expected_platform_codes = {code for code, _ in codes}
      expected_dates = {date for _, date in codes if date is not None}

      # Found platform codes & dates
      codes = get_platform_codes(live_fw_versions.get(addr, set()))
      found_platform_codes = {code for code, _ in codes}
      found_dates = {date for _, date in codes if date is not None}

      # Check platform code + part number matches for any found versions
      if not any(found_platform_code in expected_platform_codes for found_platform_code in found_platform_codes):
        break

      if ecu[0] in DATE_FW_ECUS:
        # If ECU can have a FW date, require it to exist
        # (this excludes candidates in the database without dates)
        if not len(expected_dates) or not len(found_dates):
          break

        # Check any date within range in the database, format is %y%m%d
        if not any(min(expected_dates) <= found_date <= max(expected_dates) for found_date in found_dates):
          break

      valid_found_ecus.add(addr)

    # If all live ECUs pass all checks for candidate, add it as a match
    if valid_expected_ecus.issubset(valid_found_ecus):
      candidates.add(candidate)

  return candidates - fuzzy_platform_blacklist


HYUNDAI_VERSION_REQUEST_LONG = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER]) + \
  p16(0xf100)  # Long description

HYUNDAI_VERSION_REQUEST_ALT = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER]) + \
  p16(0xf110)  # Alt long description

HYUNDAI_VERSION_REQUEST_MULTI = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER]) + \
  p16(uds.DATA_IDENTIFIER_TYPE.VEHICLE_MANUFACTURER_SPARE_PART_NUMBER) + \
  p16(uds.DATA_IDENTIFIER_TYPE.APPLICATION_SOFTWARE_IDENTIFICATION) + \
  p16(0xf100)

HYUNDAI_VERSION_RESPONSE = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER + 0x40])

# Regex patterns for parsing platform code, FW date, and part number from FW versions
PLATFORM_CODE_FW_PATTERN = re.compile(b'((?<=' + HYUNDAI_VERSION_REQUEST_LONG[1:] +
                                      b')[A-Z]{2}[A-Za-z0-9]{0,2})')
DATE_FW_PATTERN = re.compile(b'(?<=[ -])([0-9]{6}$)')
PART_NUMBER_FW_PATTERN = re.compile(b'(?<=[0-9][.,][0-9]{2} )([0-9]{5}[-/]?[A-Z][A-Z0-9]{3}[0-9])')

# List of ECUs expected to have platform codes, camera and radar should exist on all cars
# TODO: use abs, it has the platform code and part number on many platforms
PLATFORM_CODE_ECUS = [Ecu.fwdRadar, Ecu.fwdCamera, Ecu.eps]
# So far we've only seen dates in fwdCamera
# TODO: there are date codes in the ABS firmware versions in hex
DATE_FW_ECUS = [Ecu.fwdCamera]

FW_QUERY_CONFIG = FwQueryConfig(
  requests=[
    # TODO: minimize shared whitelists for CAN and cornerRadar for CAN-FD
    # CAN queries (OBD-II port)
    Request(
      [HYUNDAI_VERSION_REQUEST_LONG],
      [HYUNDAI_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.transmission, Ecu.eps, Ecu.abs, Ecu.fwdRadar, Ecu.fwdCamera],
    ),
    Request(
      [HYUNDAI_VERSION_REQUEST_MULTI],
      [HYUNDAI_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.engine, Ecu.transmission, Ecu.eps, Ecu.abs, Ecu.fwdRadar],
    ),

    # CAN-FD queries (from camera)
    # TODO: combine shared whitelists with CAN requests
    Request(
      [HYUNDAI_VERSION_REQUEST_LONG],
      [HYUNDAI_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.fwdCamera, Ecu.fwdRadar, Ecu.cornerRadar, Ecu.hvac],
      bus=0,
      auxiliary=True,
    ),
    Request(
      [HYUNDAI_VERSION_REQUEST_LONG],
      [HYUNDAI_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.fwdCamera, Ecu.adas, Ecu.cornerRadar, Ecu.hvac],
      bus=1,
      auxiliary=True,
      obd_multiplexing=False,
    ),

    # CAN-FD debugging queries
    Request(
      [HYUNDAI_VERSION_REQUEST_ALT],
      [HYUNDAI_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.parkingAdas, Ecu.hvac],
      bus=0,
      auxiliary=True,
    ),
    Request(
      [HYUNDAI_VERSION_REQUEST_ALT],
      [HYUNDAI_VERSION_RESPONSE],
      whitelist_ecus=[Ecu.parkingAdas, Ecu.hvac],
      bus=1,
      auxiliary=True,
      obd_multiplexing=False,
    ),
  ],
  extra_ecus=[
    (Ecu.adas, 0x730, None),         # ADAS Driving ECU on HDA2 platforms
    (Ecu.parkingAdas, 0x7b1, None),  # ADAS Parking ECU (may exist on all platforms)
    (Ecu.hvac, 0x7b3, None),         # HVAC Control Assembly
    (Ecu.cornerRadar, 0x7b7, None),
  ],
  # Custom fuzzy fingerprinting function using platform codes, part numbers + FW dates:
  match_fw_to_car_fuzzy=match_fw_to_car_fuzzy,
)

CHECKSUM = {
  "crc8": [CAR.SONATA_DN8, CAR.SANTAFE, CAR.PALISADE, CAR.SELTOS, CAR.ELANTRA_CN7, CAR.K5_DL3,
           CAR.SONATA_DN8_HEV, CAR.SANTAFE_HEV, CAR.SOUL_EV, CAR.ELANTRA_CN7_HEV, CAR.K5_DL3_HEV],
  "6B": [CAR.SORENTO, CAR.GENESIS],
}

CAN_GEARS = {
  "use_cluster_gears": # Use Cluster for Gear Selection, rather than Transmission [ CLU15 ]
    {CAR.ELANTRA_I30, CAR.KONA, CAR.GRANDEUR_IG, CAR.NIRO_HEV, CAR.K7},
  "use_tcu_gears": # Use TCU Message for Gear Selection [ TCU12 ]
    {CAR.SONATA_LF, CAR.TUCSON, CAR.VELOSTER, CAR.K5},
  # Use Elect GEAR Message for Gear Selection [ ELECT_GEAR ]
  # Gear not set is [ LVR12 ]
  "send_mdps12": {CAR.GENESIS_G90, CAR.K9},
}

CANFD_CAR = {
  CAR.TUCSON_NX4, CAR.STARIA,
  CAR.SPORTAGE_NQ5, CAR.SORENTO_MQ4, CAR.CARNIVAL_KA4, CAR.K8_GL3,
  CAR.IONIQ5, CAR.IONIQ6, CAR.KONA_SX2_EV, CAR.SONATA_DN8_24,
  CAR.EV6, CAR.NIRO_SG2_EV, CAR.EV9, CAR.K5_DL3_24, CAR.K5_DL3_24_HEV,
  CAR.GENESIS_GV60_EV, CAR.GENESIS_GV70, CAR.GENESIS_GV80,
  CAR.TUCSON_NX4_HEV,
  CAR.SORENTO_MQ4_HEV, CAR.SPORTAGE_NQ5_HEV, CAR.NIRO_SG2_HEV, CAR.K8_GL3_HEV,
}

# The radar does SCC on these cars when HDA I, rather than the camera
CANFD_RADAR_SCC_CAR = {
  CAR.GENESIS_GV70, CAR.GENESIS_GV80, CAR.SORENTO_MQ4, CAR.SORENTO_MQ4_HEV, CAR.KONA_SX2_EV,
}

EV_CAR = {
  CAR.KONA_EV, CAR.IONIQ_EV, CAR.NIRO_EV, CAR.SOUL_EV, CAR.NEXO,
  CAR.IONIQ5, CAR.IONIQ6, CAR.KONA_SX2_EV,
  CAR.EV6, CAR.NIRO_SG2_EV, CAR.EV9,
  CAR.GENESIS_GV60_EV,
}

HEV_CAR = {
  CAR.KONA_HEV, CAR.IONIQ_HEV, CAR.NIRO_HEV, CAR.SANTAFE_HEV, CAR.ELANTRA_CN7_HEV, CAR.SONATA_DN8_HEV, CAR.SONATA_LF_HEV, CAR.GRANDEUR_IG_HEV, CAR.GRANDEUR_IGFL_HEV,
  CAR.K5_HEV, CAR.K5_DL3_HEV, CAR.K7_HEV,
  CAR.TUCSON_NX4_HEV,
  CAR.SORENTO_MQ4_HEV, CAR.SPORTAGE_NQ5_HEV, CAR.NIRO_SG2_HEV, CAR.K8_GL3_HEV, CAR.K5_DL3_24_HEV,
}

# these cars require a special panda safety mode due to missing counters and checksums in the messages
LEGACY_SAFETY_MODE_CAR = {
  CAR.ELANTRA_I30, CAR.IONIQ, CAR.IONIQ_EV, CAR.IONIQ_HEV, CAR.KONA, CAR.KONA_EV, CAR.KONA_HEV, CAR.SORENTO, CAR.SONATA_LF, CAR.SONATA_LF_HEV,
  CAR.K5, CAR.K5_HEV, CAR.K7, CAR.K7_HEV, CAR.VELOSTER, CAR.STINGER,
  CAR.GENESIS, CAR.GENESIS_G70, CAR.GENESIS_G80,
}

# If 0x500 is present on bus 1 it probably has a Mando radar outputting radar points.
# If no points are outputted by default it might be possible to turn it on using  selfdrive/debug/hyundai_enable_radar_points.py
DBC = {
  # Hyundai
  CAR.ELANTRA_I30: dbc_dict('hyundai_kia_generic', None),
  CAR.ELANTRA_CN7: dbc_dict('hyundai_kia_generic', None),
  CAR.ELANTRA_CN7_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.SONATA_DN8: dbc_dict('hyundai_kia_generic', 'hyundai_kia_mando_front_radar_generated'),
  CAR.SONATA_DN8_HEV: dbc_dict('hyundai_kia_generic', 'hyundai_kia_mando_front_radar_generated'),
  CAR.SONATA_LF: dbc_dict('hyundai_kia_generic', None),
  CAR.SONATA_LF_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.KONA: dbc_dict('hyundai_kia_generic', None),
  CAR.KONA_EV: dbc_dict('hyundai_kia_generic', None),
  CAR.KONA_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.IONIQ: dbc_dict('hyundai_kia_generic', None),
  CAR.IONIQ_EV: dbc_dict('hyundai_kia_generic', 'hyundai_kia_mando_front_radar_generated'),
  CAR.IONIQ_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.TUCSON: dbc_dict('hyundai_kia_generic', None),
  CAR.SANTAFE: dbc_dict('hyundai_kia_generic', 'hyundai_kia_mando_front_radar_generated'),
  CAR.SANTAFE_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.PALISADE: dbc_dict('hyundai_kia_generic', 'hyundai_kia_mando_front_radar_generated'),
  CAR.VELOSTER: dbc_dict('hyundai_kia_generic', None),
  CAR.GRANDEUR_IG: dbc_dict('hyundai_kia_generic', None),
  CAR.GRANDEUR_IG_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.GRANDEUR_IGFL: dbc_dict('hyundai_kia_generic', None),
  CAR.GRANDEUR_IGFL_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.NEXO: dbc_dict('hyundai_kia_generic', None),

  # Kia
  CAR.FORTE: dbc_dict('hyundai_kia_generic', None),
  CAR.K5: dbc_dict('hyundai_kia_generic', None),
  CAR.K5_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.K5_DL3: dbc_dict('hyundai_kia_generic', None),
  CAR.K5_DL3_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.K7: dbc_dict('hyundai_kia_generic', None),
  CAR.K7_HEV: dbc_dict('hyundai_kia_generic', None),
  CAR.K9: dbc_dict('hyundai_kia_generic', None),
  CAR.SPORTAGE: dbc_dict('hyundai_kia_generic', None),
  CAR.SORENTO: dbc_dict('hyundai_kia_generic', None),
  CAR.MOHAVE: dbc_dict('hyundai_kia_generic', None),
  CAR.STINGER: dbc_dict('hyundai_kia_generic', None),
  CAR.NIRO_EV: dbc_dict('hyundai_kia_generic', 'hyundai_kia_mando_front_radar_generated'),
  CAR.NIRO_HEV: dbc_dict('hyundai_kia_generic', 'hyundai_kia_mando_front_radar_generated'),
  CAR.SOUL_EV: dbc_dict('hyundai_kia_generic', None),
  CAR.SELTOS: dbc_dict('hyundai_kia_generic', None),

  # Genesis
  CAR.GENESIS: dbc_dict('hyundai_kia_generic', None),
  CAR.GENESIS_G70: dbc_dict('hyundai_kia_generic', 'hyundai_kia_mando_front_radar_generated'),
  CAR.GENESIS_G80: dbc_dict('hyundai_kia_generic', None),
  CAR.GENESIS_G90: dbc_dict('hyundai_kia_generic', None),

  # CANFD
  CAR.IONIQ5: dbc_dict('hyundai_canfd', None),
  CAR.IONIQ6: dbc_dict('hyundai_canfd', None),
  CAR.KONA_SX2_EV: dbc_dict('hyundai_canfd', None),
  CAR.TUCSON_NX4: dbc_dict('hyundai_canfd', None),
  CAR.TUCSON_NX4_HEV: dbc_dict('hyundai_canfd', None),
  CAR.STARIA: dbc_dict('hyundai_canfd', None),
  CAR.SONATA_DN8_24: dbc_dict('hyundai_canfd', None),
  CAR.EV6: dbc_dict('hyundai_canfd', None),
  CAR.SPORTAGE_NQ5: dbc_dict('hyundai_canfd', None),
  CAR.SPORTAGE_NQ5_HEV: dbc_dict('hyundai_canfd', None),
  CAR.SORENTO_MQ4: dbc_dict('hyundai_canfd', None),
  CAR.SORENTO_MQ4_HEV: dbc_dict('hyundai_canfd', None),
  CAR.NIRO_SG2_EV: dbc_dict('hyundai_canfd', None),
  CAR.NIRO_SG2_HEV: dbc_dict('hyundai_canfd', None),
  CAR.CARNIVAL_KA4: dbc_dict('hyundai_canfd', None),
  CAR.K8_GL3: dbc_dict('hyundai_canfd', None),
  CAR.K8_GL3_HEV: dbc_dict('hyundai_canfd', None),
  CAR.EV9: dbc_dict('hyundai_canfd', None),
  CAR.K5_DL3_24: dbc_dict('hyundai_canfd', None),
  CAR.K5_DL3_24_HEV: dbc_dict('hyundai_canfd', None),
  CAR.GENESIS_GV60_EV: dbc_dict('hyundai_canfd', None),
  CAR.GENESIS_GV70: dbc_dict('hyundai_canfd', None),
  CAR.GENESIS_GV80: dbc_dict('hyundai_canfd', None),
}
