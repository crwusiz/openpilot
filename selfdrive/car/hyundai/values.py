import re
from dataclasses import dataclass, field
from enum import Enum, IntFlag

from cereal import car
from panda.python import uds
from openpilot.common.conversions import Conversions as CV
from openpilot.selfdrive.car import CarSpecs, DbcDict, PlatformConfig, Platforms, dbc_dict, AngleRateLimit
from openpilot.selfdrive.car.docs_definitions import CarFootnote, CarHarness, CarDocs, CarParts, Column
from openpilot.selfdrive.car.fw_query_definitions import FwQueryConfig, Request, p16

Ecu = car.CarParams.Ecu


class CarControllerParams:

  # seen changing at 0.2 deg/frame down, 0.1 deg/frame up at 100Hz
  ANGLE_RATE_LIMIT_UP = AngleRateLimit(speed_bp=[0., 5., 15.], angle_v=[5., .8, .15])
  ANGLE_RATE_LIMIT_DOWN = AngleRateLimit(speed_bp=[0., 5., 15.], angle_v=[5., 3.5, 0.4])

  def __init__(self, CP):
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

    # To determine the limit for your car, find the maximum value that the stock LKAS will request.
    # If the max stock LKAS request is <384, add your car to this list.
    #elif CP.carFingerprint in (CAR.HYUNDAI_AVANTE, CAR.HYUNDAI_I30, CAR.HYUNDAI_IONIQ, CAR.HYUNDAI_IONIQ_EV, CAR.HYUNDAI_SONATA_LF,
    #                           CAR.KIA_K3, CAR.KIA_K5, CAR.KIA_K5_HEV, CAR.KIA_SORENTO,
    #                           CAR.GENESIS_G80, CAR.GENESIS_G90):
    #  self.STEER_MAX = 255

    # these cars have significantly more torque than most HKG; limit to 70% of max
    elif CP.flags & HyundaiFlags.ALT_LIMITS:
      self.STEER_MAX = 270
      self.STEER_DELTA_UP = 2
      self.STEER_DELTA_DOWN = 3

    # Default for most HKG
    else:
      self.STEER_MAX = 384


class HyundaiFlags(IntFlag):
  # Dynamic Flags
  CANFD_HDA2 = 1
  CANFD_ALT_BUTTONS = 2
  CANFD_ALT_GEARS = 2 ** 2
  CANFD_CAMERA_SCC = 2 ** 3

  ALT_LIMITS = 2 ** 4
  ENABLE_BLINKERS = 2 ** 5
  CANFD_ALT_GEARS_2 = 2 ** 6
  SEND_LFA = 2 ** 7
  USE_FCA = 2 ** 8
  CANFD_HDA2_ALT_STEERING = 2 ** 9

  # these cars use a different gas signal
  HYBRID = 2 ** 10
  EV = 2 ** 11

  # Static flags

  # If 0x500 is present on bus 1 it probably has a Mando radar outputting radar points.
  # If no points are outputted by default it might be possible to turn it on using  selfdrive/debug/hyundai_enable_radar_points.py
  MANDO_RADAR = 2 ** 12
  CANFD = 2 ** 13

  # The radar does SCC on these cars when HDA I, rather than the camera
  RADAR_SCC = 2 ** 14
  CAMERA_SCC = 2 ** 15
  CHECKSUM_CRC8 = 2 ** 16
  CHECKSUM_6B = 2 ** 17

  # these cars require a special panda safety mode due to missing counters and checksums in the messages
  LEGACY = 2 ** 18

  # these cars have not been verified to work with longitudinal yet - radar disable, sending correct messages, etc.
  UNSUPPORTED_LONGITUDINAL = 2 ** 19

  CANFD_NO_RADAR_DISABLE = 2 ** 20

  CLUSTER_GEARS = 2 ** 21
  TCU_GEARS = 2 ** 22

  MIN_STEER_32_MPH = 2 ** 23

  # LFA2 car angle steer type
  ANGLE_CONTROL = 2 ** 24

class HyundaiExFlags(IntFlag):
  AUTOHOLD = 1
  SCC13 = 2
  SCC14 = 2 ** 2
  NAVI = 2 ** 3
  TPMS = 2 ** 4
  LFA = 2 ** 5

class Footnote(Enum):
  CANFD = CarFootnote(
    "Requires a <a href=\"https://comma.ai/shop/can-fd-panda-kit\" target=\"_blank\">CAN FD panda kit</a> if not using " +
    "comma 3X for this <a href=\"https://en.wikipedia.org/wiki/CAN_FD\" target=\"_blank\">CAN FD car</a>.",
    Column.MODEL, shop_footnote=False)


@dataclass
class HyundaiCarDocs(CarDocs):
  package: str = "Smart Cruise Control (SCC)"

  def init_make(self, CP: car.CarParams):
    if CP.flags & HyundaiFlags.CANFD:
      self.footnotes.insert(0, Footnote.CANFD)


@dataclass
class HyundaiPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: dbc_dict("hyundai_kia_generic", None))

  def init(self):
    if self.flags & HyundaiFlags.MANDO_RADAR:
      self.dbc_dict = dbc_dict('hyundai_kia_generic', 'hyundai_kia_mando_front_radar_generated')

    if self.flags & HyundaiFlags.MIN_STEER_32_MPH:
      self.specs = self.specs.override(minSteerSpeed=32 * CV.MPH_TO_MS)


@dataclass
class HyundaiCanFDPlatformConfig(PlatformConfig):
  dbc_dict: DbcDict = field(default_factory=lambda: dbc_dict("hyundai_canfd", None))

  def init(self):
    self.flags |= HyundaiFlags.CANFD


class CAR(Platforms):
  # Hyundai
  HYUNDAI_AVANTE = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI AVANTE (AD)", car_parts=CarParts.common([CarHarness.hyundai_b])),
      HyundaiCarDocs("Hyundai Elantra 2017-18", min_enable_speed=19 * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_b])),
      HyundaiCarDocs("Hyundai Elantra 2019", min_enable_speed=19 * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_g])),
      HyundaiCarDocs("Hyundai Elantra GT 2017-19", car_parts=CarParts.common([CarHarness.hyundai_e])),
    ],
    CarSpecs(mass=1340, wheelbase=2.72, steerRatio=15.4, tireStiffnessFactor=0.385),
    flags=HyundaiFlags.LEGACY | HyundaiFlags.CLUSTER_GEARS,
  )
  HYUNDAI_I30 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI I30 (PD)", car_parts=CarParts.common([CarHarness.hyundai_e])),
      HyundaiCarDocs("Hyundai i30 2017-19", car_parts=CarParts.common([CarHarness.hyundai_e])),
    ],
    HYUNDAI_AVANTE.specs,
    flags=HyundaiFlags.LEGACY | HyundaiFlags.CLUSTER_GEARS,
  )
  HYUNDAI_AVANTE_CN7 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI AVANTE (CN7)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Hyundai Elantra 2021-23", video_link="https://youtu.be/_EdYQtV52-c", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    CarSpecs(mass=1270, wheelbase=2.72, steerRatio=12.9, tireStiffnessFactor=0.65),
    flags=HyundaiFlags.CHECKSUM_CRC8,
  )
  HYUNDAI_AVANTE_CN7_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI AVANTE HYBRID (CN7)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Hyundai Elantra Hybrid 2021-23", video_link="https://youtu.be/_EdYQtV52-c", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    CarSpecs(mass=1368, wheelbase=2.72, steerRatio=12.9, tireStiffnessFactor=0.65),
    flags=HyundaiFlags.CHECKSUM_CRC8 | HyundaiFlags.HYBRID,
  )
  HYUNDAI_SONATA_LF = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI SONATA (LF)", car_parts=CarParts.common([CarHarness.hyundai_e])),
      HyundaiCarDocs("Hyundai Sonata 2018-19", car_parts=CarParts.common([CarHarness.hyundai_e])),
    ],
    CarSpecs(mass=1640, wheelbase=2.80, steerRatio=15.2),
    flags=HyundaiFlags.UNSUPPORTED_LONGITUDINAL | HyundaiFlags.TCU_GEARS | HyundaiFlags.LEGACY,
  )
  HYUNDAI_SONATA_LF_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI SONATA HYBRID (LF)", car_parts=CarParts.common([CarHarness.hyundai_e])),
      HyundaiCarDocs("Hyundai Sonata Hybrid 2018-19", car_parts=CarParts.common([CarHarness.hyundai_e])),
    ],
    HYUNDAI_SONATA_LF.specs,
    flags=HyundaiFlags.UNSUPPORTED_LONGITUDINAL | HyundaiFlags.HYBRID | HyundaiFlags.LEGACY,
  )
  HYUNDAI_SONATA_DN8 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI SONATA (DN8)", car_parts=CarParts.common([CarHarness.hyundai_a])),
      HyundaiCarDocs("Hyundai Sonata 2020-23", "All", video_link="https://www.youtube.com/watch?v=ix63r9kE3Fw",
                   car_parts=CarParts.common([CarHarness.hyundai_a])),
    ],
    CarSpecs(mass=1615, wheelbase=2.84, steerRatio=15.2, tireStiffnessFactor=0.65),
    flags=HyundaiFlags.MANDO_RADAR | HyundaiFlags.CHECKSUM_CRC8,
  )
  HYUNDAI_SONATA_DN8_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI SONATA HYBRID (DN8)", car_parts=CarParts.common([CarHarness.hyundai_a])),
      HyundaiCarDocs("Hyundai Sonata Hybrid 2020-23", "All", car_parts=CarParts.common([CarHarness.hyundai_a])),
    ],
    HYUNDAI_SONATA_DN8.specs,
    flags=HyundaiFlags.MANDO_RADAR | HyundaiFlags.CHECKSUM_CRC8 | HyundaiFlags.HYBRID,
  )
  HYUNDAI_KONA = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI KONA (OS)", car_parts=CarParts.common([CarHarness.hyundai_b])),
      HyundaiCarDocs("Hyundai Kona 2020", car_parts=CarParts.common([CarHarness.hyundai_b])),
    ],
    CarSpecs(mass=1743, wheelbase=2.60, steerRatio=13.7, tireStiffnessFactor=0.385),
    flags=HyundaiFlags.CLUSTER_GEARS | HyundaiFlags.LEGACY,
  )
  HYUNDAI_KONA_EV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI KONA EV (OS)", car_parts=CarParts.common([CarHarness.hyundai_g])),
      HyundaiCarDocs("Hyundai Kona Electric 2018-21", car_parts=CarParts.common([CarHarness.hyundai_g])),
      HyundaiCarDocs("Hyundai Kona Electric 2022-23", car_parts=CarParts.common([CarHarness.hyundai_o])),
    ],
    HYUNDAI_KONA.specs,
    flags=HyundaiFlags.EV | HyundaiFlags.LEGACY,
  )
  HYUNDAI_KONA_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI KONA HYBRID (OS)", car_parts=CarParts.common([CarHarness.hyundai_i])),
      HyundaiCarDocs("Hyundai Kona Hybrid 2020", car_parts=CarParts.common([CarHarness.hyundai_i])),  # TODO: check packages,
    ],
    HYUNDAI_KONA.specs,
    flags=HyundaiFlags.HYBRID | HyundaiFlags.LEGACY,
  )
  HYUNDAI_IONIQ = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI IONIQ (AE)", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Hyundai Ioniq Hybrid 2017-19", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    CarSpecs(mass=1575, wheelbase=2.70, steerRatio=13.7, tireStiffnessFactor=0.385),
    flags=HyundaiFlags.HYBRID | HyundaiFlags.LEGACY,
  )
  HYUNDAI_IONIQ_EV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI IONIQ EV (AE)", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Hyundai Ioniq Electric 2019", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Hyundai Ioniq Electric 2020", "All", car_parts=CarParts.common([CarHarness.hyundai_h])),
    ],
    HYUNDAI_IONIQ.specs,
    flags=HyundaiFlags.MANDO_RADAR | HyundaiFlags.EV | HyundaiFlags.LEGACY,
  )
  HYUNDAI_IONIQ_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI IONIQ HYBRID (AE)", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Hyundai Ioniq Hybrid 2020-22", car_parts=CarParts.common([CarHarness.hyundai_h])),  # TODO: confirm 2020-21 harness,
      HyundaiCarDocs("Hyundai Ioniq Plug-in Hybrid 2019", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Hyundai Ioniq Plug-in Hybrid 2020-22", "All", car_parts=CarParts.common([CarHarness.hyundai_h])),
    ],
    HYUNDAI_IONIQ.specs,
    flags=HyundaiFlags.HYBRID | HyundaiFlags.LEGACY,
  )
  HYUNDAI_TUCSON = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI TUCSON (TL)", car_parts=CarParts.common([CarHarness.hyundai_l])),
      HyundaiCarDocs("Hyundai Tucson 2021", min_enable_speed=19 * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_l])),
      HyundaiCarDocs("Hyundai Tucson Diesel 2019", car_parts=CarParts.common([CarHarness.hyundai_l])),
    ],
    CarSpecs(mass=1596, wheelbase=2.67, steerRatio=16.0, tireStiffnessFactor=0.385),
    flags=HyundaiFlags.TCU_GEARS,
  )
  HYUNDAI_SANTAFE = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI SANTAFE (TM)", car_parts=CarParts.common([CarHarness.hyundai_l])),
      HyundaiCarDocs("Hyundai Santa Fe 2019-20", "All", video_link="https://youtu.be/bjDR0YjM__s",
                     car_parts=CarParts.common([CarHarness.hyundai_d])),
      HyundaiCarDocs("Hyundai Santa Fe 2021-23", "All", video_link="https://youtu.be/VnHzSTygTS4",
                     car_parts=CarParts.common([CarHarness.hyundai_l])),
    ],
    CarSpecs(mass=1910, wheelbase=2.76, steerRatio=15.8, tireStiffnessFactor=0.82),
    flags=HyundaiFlags.MANDO_RADAR | HyundaiFlags.CHECKSUM_CRC8,
  )
  HYUNDAI_SANTAFE_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI SANTAFE HYBRID (TM)", car_parts=CarParts.common([CarHarness.hyundai_l])),
      HyundaiCarDocs("Hyundai Santa Fe Hybrid 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_l])),
      HyundaiCarDocs("Hyundai Santa Fe Plug-in Hybrid 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_l])),
    ],
    HYUNDAI_SANTAFE.specs,
    flags=HyundaiFlags.CHECKSUM_CRC8 | HyundaiFlags.HYBRID,
  )
  HYUNDAI_PALISADE = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI PALISADE (LX2)", car_parts=CarParts.common([CarHarness.hyundai_h])),
      HyundaiCarDocs("Hyundai Palisade 2020-22", "All", video_link="https://youtu.be/TAnDqjF4fDY?t=456", car_parts=CarParts.common([CarHarness.hyundai_h])),
    ],
    CarSpecs(mass=2060, wheelbase=2.90, steerRatio=15.8, tireStiffnessFactor=0.63),
    flags=HyundaiFlags.MANDO_RADAR | HyundaiFlags.CHECKSUM_CRC8,
  )
  HYUNDAI_VELOSTER = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI VELOSTER (JS)", car_parts=CarParts.common([CarHarness.hyundai_e])),
      HyundaiCarDocs("Hyundai Veloster 2019-20", min_enable_speed=5. * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_e])),
    ],
    CarSpecs(mass=1350, wheelbase=2.65, steerRatio=15.8, tireStiffnessFactor=0.5),
    flags=HyundaiFlags.LEGACY | HyundaiFlags.TCU_GEARS,
  )
  HYUNDAI_GRANDEUR = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI GRANDEUR (IG)", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    CarSpecs(mass=1719, wheelbase=2.88, steerRatio=12.5),
    flags=HyundaiFlags.CLUSTER_GEARS | HyundaiFlags.LEGACY,
  )
  HYUNDAI_GRANDEUR_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI GRANDEUR HEV (IG)", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    HYUNDAI_GRANDEUR.specs,
    flags=HyundaiFlags.HYBRID | HyundaiFlags.LEGACY,
  )
  HYUNDAI_GRANDEUR_FL = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI GRANDEUR FL (IG)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Hyundai Azera 2022", "All", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    CarSpecs(mass=1600, wheelbase=2.885, steerRatio=14.5),
  )
  HYUNDAI_GRANDEUR_FL_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI GRANDEUR FL HYBRID (IG)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Hyundai Azera Hybrid 2020", "All", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    HYUNDAI_GRANDEUR_FL.specs,
    flags=HyundaiFlags.HYBRID,
  )
  HYUNDAI_NEXO = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI NEXO (FE)", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    CarSpecs(mass=1873, wheelbase=2.79, steerRatio=13.7),
    flags=HyundaiFlags.EV,
  )

  # CanFD Hyundai
  HYUNDAI_SONATA_DN8_24 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI SONATA_2024 (DN8)", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    HYUNDAI_SONATA_DN8.specs,
  )
  HYUNDAI_KONA_SX2_EV = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI KONA EV (SX2)", car_parts=CarParts.common([CarHarness.hyundai_r])),
      HyundaiCarDocs("Hyundai Kona Electric (with HDA II, Korea only) 2023", video_link="https://www.youtube.com/watch?v=U2fOCmcQ8hw",
                     car_parts=CarParts.common([CarHarness.hyundai_r])),
    ],
    HYUNDAI_KONA.specs,
    flags=HyundaiFlags.EV | HyundaiFlags.CANFD_NO_RADAR_DISABLE,
  )
  HYUNDAI_IONIQ5 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI IONIQ 5 (NE1)", car_parts=CarParts.common([CarHarness.hyundai_q])),
      HyundaiCarDocs("Hyundai Ioniq 5 (Southeast Asia only) 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_q])),
      HyundaiCarDocs("Hyundai Ioniq 5 (without HDA II) 2022-23", "Highway Driving Assist", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Hyundai Ioniq 5 (with HDA II) 2022-23", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_q])),
    ],
    CarSpecs(mass=2012, wheelbase=3.0, steerRatio=14.26, tireStiffnessFactor=0.65),
    flags=HyundaiFlags.EV,
  )
  HYUNDAI_IONIQ5_PE = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI IONIQ 5 PE (NE1)", car_parts=CarParts.common([CarHarness.hyundai_q])),
      HyundaiCarDocs("Hyundai Ioniq 5 PE (with HDA II) 2024", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_q])),
    ],
    CarSpecs(mass=2012, wheelbase=3.0, steerRatio=14.26, tireStiffnessFactor=0.65),
    flags=HyundaiFlags.EV | HyundaiFlags.ANGLE_CONTROL,
  )
  HYUNDAI_IONIQ6 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI IONIQ 6 (CE1)", car_parts=CarParts.common([CarHarness.hyundai_p])),
      HyundaiCarDocs("Hyundai Ioniq 6 (with HDA II) 2023", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_p])),
    ],
    HYUNDAI_IONIQ5.specs,
    flags=HyundaiFlags.EV | HyundaiFlags.CANFD_NO_RADAR_DISABLE,
  )
  HYUNDAI_TUCSON_NX4 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI TUCSON (NX4)", car_parts=CarParts.common([CarHarness.hyundai_n])),
      HyundaiCarDocs("Hyundai Tucson 2022", car_parts=CarParts.common([CarHarness.hyundai_n])),
      HyundaiCarDocs("Hyundai Tucson 2023", "All", car_parts=CarParts.common([CarHarness.hyundai_n])),
    ],
    CarSpecs(mass=1680, wheelbase=2.756, steerRatio=16, tireStiffnessFactor=0.385),
  )
  HYUNDAI_TUCSON_NX4_HEV = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI TUCSON HYBRID (NX4)", car_parts=CarParts.common([CarHarness.hyundai_n])),
      HyundaiCarDocs("Hyundai Tucson Hybrid 2022-24", "All", car_parts=CarParts.common([CarHarness.hyundai_n])),
    ],
    HYUNDAI_TUCSON_NX4.specs,
  )
  HYUNDAI_STARIA = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("HYUNDAI STARIA (US4)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Hyundai Staria 2023", "All", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    CarSpecs(mass=2205, wheelbase=3.273, steerRatio=11.94),
  )

  # Kia
  KIA_K3 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA K3 (BD)", car_parts=CarParts.common([CarHarness.hyundai_e])),
      HyundaiCarDocs("Kia Forte 2019-21", car_parts=CarParts.common([CarHarness.hyundai_g])),
      HyundaiCarDocs("Kia Forte 2023", car_parts=CarParts.common([CarHarness.hyundai_e])),
    ],
    CarSpecs(mass=1345, wheelbase=2.70, steerRatio=13.7, tireStiffnessFactor=0.5),
  )
  KIA_K5 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA K5 (JF)", car_parts=CarParts.common([CarHarness.hyundai_b])),
      HyundaiCarDocs("Kia Optima 2017", "Advanced Smart Cruise Control", car_parts=CarParts.common([CarHarness.hyundai_b])),
      HyundaiCarDocs("Kia Optima 2019-20", car_parts=CarParts.common([CarHarness.hyundai_g])),
    ],
    CarSpecs(mass=1613, wheelbase=2.8, steerRatio=13.75, tireStiffnessFactor=0.5),
    flags=HyundaiFlags.LEGACY | HyundaiFlags.TCU_GEARS,
  )
  KIA_K5_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA K5 HYBRID (JF)", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Kia Optima Hybrid 2017", "Advanced Smart Cruise Control", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Kia Optima Hybrid 2019", car_parts=CarParts.common([CarHarness.hyundai_h])),
    ],
    KIA_K5.specs,
    flags=HyundaiFlags.HYBRID | HyundaiFlags.LEGACY,
  )
  KIA_K5_DL3 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA K5 (DL3)", car_parts=CarParts.common([CarHarness.hyundai_a])),
      HyundaiCarDocs("Kia K5 2021-24", car_parts=CarParts.common([CarHarness.hyundai_a])),
    ],
    CarSpecs(mass=1553, wheelbase=2.85, steerRatio=13.27, tireStiffnessFactor=0.5),
    flags=HyundaiFlags.CHECKSUM_CRC8,
  )
  KIA_K5_DL3_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA K5 HYBRID (DL3)", car_parts=CarParts.common([CarHarness.hyundai_a])),
      HyundaiCarDocs("Kia K5 Hybrid 2020-22", car_parts=CarParts.common([CarHarness.hyundai_a])),
    ],
    KIA_K5_DL3.specs,
    flags=HyundaiFlags.MANDO_RADAR | HyundaiFlags.CHECKSUM_CRC8 | HyundaiFlags.HYBRID,
  )
  KIA_K7 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA K7 (YG)", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    CarSpecs(mass=1730, wheelbase=2.85, steerRatio=12.5),
    flags=HyundaiFlags.CLUSTER_GEARS | HyundaiFlags.LEGACY,
  )
  KIA_K7_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA K7 HYBRID (YG)", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    KIA_K7.specs,
    flags=HyundaiFlags.HYBRID | HyundaiFlags.LEGACY,
  )
  KIA_K9 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA K9 (RJ)", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    CarSpecs(mass=2005, wheelbase=3.15, steerRatio=16.5),
  )
  KIA_SPORTAGE = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA SPORTAGE (QL)", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    CarSpecs(mass=1770, wheelbase=2.67, steerRatio=15.8),
  )
  KIA_SORENTO = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA SORENTO (UM)", car_parts=CarParts.common([CarHarness.hyundai_e])),
      HyundaiCarDocs("Kia Sorento 2018", "Advanced Smart Cruise Control & LKAS", video_link="https://www.youtube.com/watch?v=Fkh3s6WHJz8",
                     car_parts=CarParts.common([CarHarness.hyundai_e])),
      HyundaiCarDocs("Kia Sorento 2019", video_link="https://www.youtube.com/watch?v=Fkh3s6WHJz8",
                     car_parts=CarParts.common([CarHarness.hyundai_e])),
    ],
    CarSpecs(mass=1885, wheelbase=2.81, steerRatio=15.8),
    flags=HyundaiFlags.CHECKSUM_6B | HyundaiFlags.UNSUPPORTED_LONGITUDINAL | HyundaiFlags.LEGACY,
  )
  KIA_MOHAVE = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA MOHAVE (HM)", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    CarSpecs(mass=2305, wheelbase=2.89, steerRatio=14.1),
  )
  KIA_STINGER = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA STINGER (CK)", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Kia Stinger 2018-20", video_link="https://www.youtube.com/watch?v=MJ94qoofYw0",
                     car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Kia Stinger 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    CarSpecs(mass=1913, wheelbase=2.90, steerRatio=13.5),
    flags=HyundaiFlags.LEGACY,
  )
  KIA_NIRO_EV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA NIRO EV (DE)", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Kia Niro EV 2019", "All", video_link="https://www.youtube.com/watch?v=lT7zcG6ZpGo", car_parts=CarParts.common([CarHarness.hyundai_h])),
      HyundaiCarDocs("Kia Niro EV 2020", "All", video_link="https://www.youtube.com/watch?v=lT7zcG6ZpGo", car_parts=CarParts.common([CarHarness.hyundai_f])),
      HyundaiCarDocs("Kia Niro EV 2021", "All", video_link="https://www.youtube.com/watch?v=lT7zcG6ZpGo", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Kia Niro EV 2022", "All", video_link="https://www.youtube.com/watch?v=lT7zcG6ZpGo", car_parts=CarParts.common([CarHarness.hyundai_h])),
    ],
    CarSpecs(mass=1748, wheelbase=2.70, steerRatio=13.7, tireStiffnessFactor=0.385),
    flags=HyundaiFlags.MANDO_RADAR | HyundaiFlags.EV,
  )
  KIA_NIRO_HEV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA NIRO HYBRID (DE)", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Kia Niro Plug-in Hybrid 2018-19", "All", min_enable_speed=10. * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Kia Niro Plug-in Hybrid 2020", "All", car_parts=CarParts.common([CarHarness.hyundai_d])),
      HyundaiCarDocs("Kia Niro Plug-in Hybrid 2021", "All", car_parts=CarParts.common([CarHarness.hyundai_d])),
      HyundaiCarDocs("Kia Niro Plug-in Hybrid 2022", "All", car_parts=CarParts.common([CarHarness.hyundai_f])),
      HyundaiCarDocs("Kia Niro Hybrid 2021", car_parts=CarParts.common([CarHarness.hyundai_d])),
      HyundaiCarDocs("Kia Niro Hybrid 2022", car_parts=CarParts.common([CarHarness.hyundai_f])),
    ],
    CarSpecs(mass=1748, wheelbase=2.70, steerRatio=13.7, tireStiffnessFactor=0.385),
    flags=HyundaiFlags.MANDO_RADAR | HyundaiFlags.HYBRID, #| HyundaiFlags.CLUSTER_GEARS | HyundaiFlags.UNSUPPORTED_LONGITUDINAL
  )
  KIA_SOUL_EV = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA SOUL EV (SK3)", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    CarSpecs(mass=1375, wheelbase=2.60, steerRatio=13.7, tireStiffnessFactor=0.385),
    flags=HyundaiFlags.EV | HyundaiFlags.CHECKSUM_CRC8,
  )
  KIA_SELTOS = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("KIA SELTOS (SP2)", car_parts=CarParts.common([CarHarness.hyundai_a])),
    ],
    CarSpecs(mass=1510, wheelbase=2.63, steerRatio=13.0),
    flags=HyundaiFlags.CHECKSUM_CRC8,
  )

  # CanFD Kia
  KIA_EV6 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA EV6 (CV1)", car_parts=CarParts.common([CarHarness.hyundai_p])),
      HyundaiCarDocs("Kia EV6 (Southeast Asia only) 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_p])),
      HyundaiCarDocs("Kia EV6 (without HDA II) 2022-23", "Highway Driving Assist", car_parts=CarParts.common([CarHarness.hyundai_l])),
      HyundaiCarDocs("Kia EV6 (with HDA II) 2022-23", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_p]))
    ],
    CarSpecs(mass=2055, wheelbase=2.90, steerRatio=16.0, tireStiffnessFactor=0.65),
    flags=HyundaiFlags.EV,
  )
  KIA_K5_DL3_24 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA K5 2024 (DL3)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Kia K5 2024", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    KIA_K5_DL3.specs,
  )
  KIA_K5_DL3_24_HEV = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA K5 HYBRID 2024 (DL3)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Kia K5 Hybrid 2024", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    KIA_K5_DL3.specs,
  )
  KIA_K8_GL3 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA K8 (GL3)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Kia K8 2023", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_q])),
    ],
    CarSpecs(mass=1630, wheelbase=2.895, steerRatio=13.27),
  )
  KIA_K8_GL3_HEV = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA K8 HYBRID (GL3)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Kia K8 Hybrid 2023", "Highway Driving Assist II", car_parts=CarParts.common([CarHarness.hyundai_q])),
    ],
    KIA_K8_GL3.specs,
  )
  KIA_SPORTAGE_NQ5 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA SPORTAGE (NQ5)", car_parts=CarParts.common([CarHarness.hyundai_n])),
      HyundaiCarDocs("Kia Sportage 2023", car_parts=CarParts.common([CarHarness.hyundai_n])),
    ],
    CarSpecs(mass=1767, wheelbase=2.756, steerRatio=13.6),
  )
  KIA_SPORTAGE_NQ5_HEV = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA SPORTAGE HYBRID (NQ5)", car_parts=CarParts.common([CarHarness.hyundai_n])),
      HyundaiCarDocs("Kia Sportage Hybrid 2023", car_parts=CarParts.common([CarHarness.hyundai_n])),
    ],
    KIA_SPORTAGE_NQ5.specs,
  )
  KIA_SORENTO_MQ4 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA SORENTO (MQ4)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Kia Sorento 2021-23", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    CarSpecs(mass=1857, wheelbase=2.81, steerRatio=13.27),
    flags=HyundaiFlags.RADAR_SCC,
  )
  KIA_SORENTO_MQ4_HEV = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA SORENTO HYBRID (MQ4)", car_parts=CarParts.common([CarHarness.hyundai_a])),
      HyundaiCarDocs("Kia Sorento Hybrid 2021-23", "All", car_parts=CarParts.common([CarHarness.hyundai_a])),
      HyundaiCarDocs("Kia Sorento Plug-in Hybrid 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_a])),
    ],
    KIA_SORENTO_MQ4.specs,
    flags=HyundaiFlags.RADAR_SCC,
  )
  KIA_CARNIVAL_KA4 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA CARNIVAL (KA4)", car_parts=CarParts.common([CarHarness.hyundai_k])),
      HyundaiCarDocs("Kia Carnival 2022-24", car_parts=CarParts.common([CarHarness.hyundai_a])),
    ],
    CarSpecs(mass=2087, wheelbase=3.09, steerRatio=14.23),
    flags=HyundaiFlags.RADAR_SCC,
  )
  KIA_NIRO_SG2_EV = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA NIRO EV (SG2)", car_parts=CarParts.common([CarHarness.hyundai_a])),
    ],
    CarSpecs(mass=1472, wheelbase=2.72, steerRatio=13.7),
    flags=HyundaiFlags.EV,
  )
  KIA_NIRO_SG2_HEV = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA NIRO HYBRID (SG2)", car_parts=CarParts.common([CarHarness.hyundai_a])),
    ],
    KIA_NIRO_SG2_EV.specs,
  )
  KIA_EV9 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("KIA EV9 (MV)", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    CarSpecs(mass=2625, wheelbase=3.1, steerRatio=16.02),
    flags=HyundaiFlags.EV | HyundaiFlags.ANGLE_CONTROL,
  )

  # Genesis
  GENESIS = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("GENESIS (DH)", car_parts=CarParts.common([CarHarness.hyundai_j])),
      HyundaiCarDocs("Hyundai Genesis 2014-17", min_enable_speed=19 * CV.MPH_TO_MS, car_parts=CarParts.common([CarHarness.hyundai_j])),
    ],
    CarSpecs(mass=2060, wheelbase=3.01, steerRatio=16.5),
    flags=HyundaiFlags.CHECKSUM_6B | HyundaiFlags.LEGACY,
  )
  GENESIS_G70 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("GENESIS G70 (IK)", car_parts=CarParts.common([CarHarness.hyundai_f])),
      HyundaiCarDocs("Genesis G70 2018-19", "All", car_parts=CarParts.common([CarHarness.hyundai_f])),
      HyundaiCarDocs("Genesis G70 2020", "All", car_parts=CarParts.common([CarHarness.hyundai_f])),
    ],
    CarSpecs(mass=1795, wheelbase=2.83, steerRatio=16.5),
    flags=HyundaiFlags.MANDO_RADAR | HyundaiFlags.LEGACY,
  )
  GENESIS_G80 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("GENESIS G80 (DH)", car_parts=CarParts.common([CarHarness.hyundai_h])),
      HyundaiCarDocs("Genesis G80 2018-19", "All", car_parts=CarParts.common([CarHarness.hyundai_h])),
    ],
    CarSpecs(mass=2035, wheelbase=3.01, steerRatio=16.5),
    flags=HyundaiFlags.LEGACY,
  )
  GENESIS_G90 = HyundaiPlatformConfig(
    [
      HyundaiCarDocs("GENESIS G90 (HI)", car_parts=CarParts.common([CarHarness.hyundai_c])),
      HyundaiCarDocs("Genesis G90 2017-18", "All", car_parts=CarParts.common([CarHarness.hyundai_c])),
    ],
    CarSpecs(mass=2185, wheelbase=3.16, steerRatio=12.0),
  )

  # Canfd Genesis
  GENESIS_GV60 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("GENESIS GV60 (JW1)", car_parts=CarParts.common([CarHarness.hyundai_a])),
      HyundaiCarDocs("Genesis GV60 (Advanced Trim) 2023", "All", car_parts=CarParts.common([CarHarness.hyundai_a])),
      HyundaiCarDocs("Genesis GV60 (Performance Trim) 2023", "All", car_parts=CarParts.common([CarHarness.hyundai_k])),
    ],
    CarSpecs(mass=2205, wheelbase=2.9, steerRatio=12.6),
    flags=HyundaiFlags.EV,
  )
  GENESIS_GV70 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("GENESIS GV70 (JK1)", car_parts=CarParts.common([CarHarness.hyundai_l])),
      HyundaiCarDocs("Genesis GV70 (2.5T Trim) 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_l])),
      HyundaiCarDocs("Genesis GV70 (3.5T Trim) 2022-23", "All", car_parts=CarParts.common([CarHarness.hyundai_m])),
    ],
    CarSpecs(mass=1950, wheelbase=2.87, steerRatio=15.2, tireStiffnessFactor=0.65),
    flags=HyundaiFlags.RADAR_SCC,
  )
  GENESIS_GV80 = HyundaiCanFDPlatformConfig(
    [
      HyundaiCarDocs("GENESIS GV80 (JX1)", car_parts=CarParts.common([CarHarness.hyundai_m])),
      HyundaiCarDocs("Genesis GV80 2023", "All", car_parts=CarParts.common([CarHarness.hyundai_m])),
    ],
    CarSpecs(mass=2258, wheelbase=2.95, steerRatio=14.14),
    flags=HyundaiFlags.RADAR_SCC,
  )


class Buttons:
  NONE = 0
  RES_ACCEL = 1
  SET_DECEL = 2
  GAP_DIST = 3
  CANCEL = 4  # on newer models, this is a pause/resume button


def get_platform_codes(fw_versions: list[bytes]) -> set[tuple[bytes, bytes | None]]:
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


def match_fw_to_car_fuzzy(live_fw_versions, vin, offline_fw_versions) -> set[str]:
  # Non-electric CAN FD platforms often do not have platform code specifiers needed
  # to distinguish between hybrid and ICE. All EVs so far are either exclusively
  # electric or specify electric in the platform code.
  fuzzy_platform_blacklist = {str(c) for c in (CANFD_CAR - EV_CAR - CANFD_FUZZY_WHITELIST)}
  candidates: set[str] = set()

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

HYUNDAI_ECU_MANUFACTURING_DATE = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER]) + \
  p16(uds.DATA_IDENTIFIER_TYPE.ECU_MANUFACTURING_DATE)

HYUNDAI_VERSION_RESPONSE = bytes([uds.SERVICE_TYPE.READ_DATA_BY_IDENTIFIER + 0x40])

# Regex patterns for parsing platform code, FW date, and part number from FW versions
PLATFORM_CODE_FW_PATTERN = re.compile(b'((?<=' + HYUNDAI_VERSION_REQUEST_LONG[1:] +
                                      b')[A-Z]{2}[A-Za-z0-9]{0,2})')
DATE_FW_PATTERN = re.compile(b'(?<=[ -])([0-9]{6}$)')
PART_NUMBER_FW_PATTERN = re.compile(b'(?<=[0-9][.,][0-9]{2} )([0-9]{5}[-/]?[A-Z][A-Z0-9]{3}[0-9])')

# We've seen both ICE and hybrid for these platforms, and they have hybrid descriptors (e.g. MQ4 vs MQ4H)
CANFD_FUZZY_WHITELIST = {CAR.KIA_SORENTO_MQ4, CAR.KIA_SORENTO_MQ4_HEV,
                         CAR.KIA_K8_GL3_HEV, CAR.KIA_CARNIVAL_KA4}

# List of ECUs expected to have platform codes, camera and radar should exist on all cars
# TODO: use abs, it has the platform code and part number on many platforms
PLATFORM_CODE_ECUS = [Ecu.fwdRadar, Ecu.fwdCamera, Ecu.eps]
# So far we've only seen dates in fwdCamera
# TODO: there are date codes in the ABS firmware versions in hex
DATE_FW_ECUS = [Ecu.fwdCamera]

# Note: an ECU on CAN FD cars may sometimes send 0x30080aaaaaaaaaaa (flow control continue) while we
# are attempting to query ECUs. This currently does not seem to affect fingerprinting from the camera
FW_QUERY_CONFIG = FwQueryConfig(
  requests=[
    # TODO: add back whitelists
    # CAN queries (OBD-II port)
    Request(
      [HYUNDAI_VERSION_REQUEST_LONG],
      [HYUNDAI_VERSION_RESPONSE],
    ),

    # CAN & CAN-FD queries (from camera)
    Request(
      [HYUNDAI_VERSION_REQUEST_LONG],
      [HYUNDAI_VERSION_RESPONSE],
      bus=0,
      auxiliary=True,
    ),
    Request(
      [HYUNDAI_VERSION_REQUEST_LONG],
      [HYUNDAI_VERSION_RESPONSE],
      bus=1,
      auxiliary=True,
      obd_multiplexing=False,
    ),

    # CAN & CAN FD query to understand the three digit date code
    # HDA2 cars usually use 6 digit date codes, so skip bus 1
    Request(
      [HYUNDAI_ECU_MANUFACTURING_DATE],
      [HYUNDAI_VERSION_RESPONSE],
      bus=0,
      auxiliary=True,
      logging=True,
    ),

    # CAN-FD alt request logging queries for hvac and parkingAdas
    Request(
      [HYUNDAI_VERSION_REQUEST_ALT],
      [HYUNDAI_VERSION_RESPONSE],
      bus=0,
      auxiliary=True,
      logging=True,
    ),
    Request(
      [HYUNDAI_VERSION_REQUEST_ALT],
      [HYUNDAI_VERSION_RESPONSE],
      bus=1,
      auxiliary=True,
      logging=True,
      obd_multiplexing=False,
    ),
  ],
  # We lose these ECUs without the comma power on these cars.
  # Note that we still attempt to match with them when they are present
  non_essential_ecus={
    Ecu.abs: [CAR.HYUNDAI_PALISADE, CAR.HYUNDAI_SONATA_DN8, CAR.HYUNDAI_SANTAFE, CAR.HYUNDAI_AVANTE_CN7, CAR.HYUNDAI_KONA_EV,
              CAR.KIA_K5_DL3],
  },
  extra_ecus=[
    (Ecu.adas, 0x730, None),              # ADAS Driving ECU on HDA2 platforms
    (Ecu.parkingAdas, 0x7b1, None),       # ADAS Parking ECU (may exist on all platforms)
    (Ecu.hvac, 0x7b3, None),              # HVAC Control Assembly
    (Ecu.cornerRadar, 0x7b7, None),
    (Ecu.combinationMeter, 0x7c6, None),  # CAN FD Instrument cluster
  ],
  # Custom fuzzy fingerprinting function using platform codes, part numbers + FW dates:
  match_fw_to_car_fuzzy=match_fw_to_car_fuzzy,
)

CHECKSUM = {
  "crc8": CAR.with_flags(HyundaiFlags.CHECKSUM_CRC8),
  "6B": CAR.with_flags(HyundaiFlags.CHECKSUM_6B),
}

CAN_GEARS = {
  # which message has the gear. hybrid and EV use ELECT_GEAR
  # Use Cluster for Gear Selection, rather than Transmission [ CLU15 ]
  "use_cluster_gears": CAR.with_flags(HyundaiFlags.CLUSTER_GEARS),
  # Use TCU Message for Gear Selection [ TCU12 ]
  "use_tcu_gears": CAR.with_flags(HyundaiFlags.TCU_GEARS),
  # Use Elect GEAR Message for Gear Selection [ ELECT_GEAR ]
  # Gear not set is [ LVR12 ]
  "send_mdps12": {CAR.GENESIS_G90, CAR.KIA_K9},
}

CANFD_CAR = CAR.with_flags(HyundaiFlags.CANFD)
CANFD_RADAR_SCC_CAR = CAR.with_flags(HyundaiFlags.RADAR_SCC)

# These CAN FD cars do not accept communication control to disable the ADAS ECU,
# responds with 0x7F2822 - 'conditions not correct'
CANFD_UNSUPPORTED_LONGITUDINAL_CAR = CAR.with_flags(HyundaiFlags.CANFD_NO_RADAR_DISABLE)

# The camera does SCC on these cars, rather than the radar
CAMERA_SCC_CAR = CAR.with_flags(HyundaiFlags.CAMERA_SCC)

HYBRID_CAR = CAR.with_flags(HyundaiFlags.HYBRID)

EV_CAR = CAR.with_flags(HyundaiFlags.EV)

LEGACY_SAFETY_MODE_CAR = CAR.with_flags(HyundaiFlags.LEGACY)

ANGLE_CONTROL_CAR = CAR.with_flags(HyundaiFlags.ANGLE_CONTROL)

UNSUPPORTED_LONGITUDINAL_CAR = CAR.with_flags(HyundaiFlags.UNSUPPORTED_LONGITUDINAL) #| CAR.with_flags(HyundaiFlags.LEGACY)

DBC = CAR.create_dbc_map()
