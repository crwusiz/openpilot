from cereal import car
from openpilot.selfdrive.car.hyundai.values import CAR

Ecu = car.CarParams.Ecu

# The existence of SCC or RDR in the fwdRadar FW usually determines the radar's function,
# i.e. if it sends the SCC messages or if another ECU like the camera or ADAS Driving ECU does


FW_VERSIONS = {
  # hyundai
  CAR.HYUNDAI_AVANTE: { # (AD)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00AD  LKAS AT USA LHD 1.01 1.01 95895-F2000 251',
      b'\xf1\x00ADP LKAS AT USA LHD 1.00 1.03 99211-F2000 X31',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00AD ESC \x11 11 \x18\x05\x06 58910-F2840',
      b'\xf1\x00AD ESC \x11 12 \x15\t\t 58920-F2810',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00AD__ SCC H-CUP      1.00 1.00 99110-F2100         ',
      b'\xf1\x00AD__ SCC H-CUP      1.00 1.01 96400-F2100         ',
    ],
  },
  CAR.HYUNDAI_I30: { # (PD)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00PD  LKAS AT KOR LHD 1.00 1.02 95740-G3000 A51',
      b'\xf1\x00PD  LKAS AT USA LHD 1.00 1.02 95740-G3000 A51',
      b'\xf1\x00PD  LKAS AT USA LHD 1.01 1.01 95740-G3100 A54',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00PD  MDPS C 1.00 1.00 56310G3300\x00 4PDDC100',
      b'\xf1\x00PD  MDPS C 1.00 1.03 56310/G3300 4PDDC103',
      b'\xf1\x00PD  MDPS C 1.00 1.04 56310/G3300 4PDDC104',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00PD ESC \t 104\x18\t\x03 58920-G3350',
      b'\xf1\x00PD ESC \x0b 103\x17\x110 58920-G3350',
      b'\xf1\x00PD ESC \x0b 104\x18\t\x03 58920-G3350',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00PD__ SCC F-CUP      1.00 1.00 96400-G3300         ',
      b'\xf1\x00PD__ SCC F-CUP      1.01 1.00 96400-G3100         ',
      b'\xf1\x00PD__ SCC FNCUP      1.01 1.00 96400-G3000         ',
    ],
  },
  CAR.HYUNDAI_AVANTE_CN7: { # (CN7)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00CN7_ SCC F-CUP      1.00 1.01 99110-AA000         ',
      b'\xf1\x00CN7_ SCC FHCUP      1.00 1.01 99110-AA000         ',
      b'\xf1\x00CN7_ SCC FNCUP      1.00 1.01 99110-AA000         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00CN7 MDPS C 1.00 1.06 56310/AA070 4CNDC106',
      b'\xf1\x00CN7 MDPS C 1.00 1.06 56310AA050\x00 4CNDC106',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00CN7 MFC  AT USA LHD 1.00 1.00 99210-AB000 200819',
      b'\xf1\x00CN7 MFC  AT USA LHD 1.00 1.01 99210-AB000 210205',
      b'\xf1\x00CN7 MFC  AT USA LHD 1.00 1.03 99210-AA000 200819',
      b'\xf1\x00CN7 MFC  AT USA LHD 1.00 1.03 99210-AB000 220426',
      b'\xf1\x00CN7 MFC  AT USA LHD 1.00 1.06 99210-AA000 220111',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00CN ESC \t 101 \x10\x03 58910-AB800',
      b'\xf1\x00CN ESC \t 104 \x08\x03 58910-AA800',
      b'\xf1\x00CN ESC \t 105 \x10\x03 58910-AA800',
    ],
  },
  CAR.HYUNDAI_AVANTE_CN7_HEV: { # (CN7)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00CN7HMFC  AT USA LHD 1.00 1.03 99210-AA000 200819',
      b'\xf1\x00CN7HMFC  AT USA LHD 1.00 1.05 99210-AA000 210930',
      b'\xf1\x00CN7HMFC  AT USA LHD 1.00 1.07 99210-AA000 220426',
      b'\xf1\x00CN7HMFC  AT USA LHD 1.00 1.08 99210-AA000 220728',
      b'\xf1\x00CN7HMFC  AT USA LHD 1.00 1.09 99210-AA000 221108',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00CNhe SCC FHCUP      1.00 1.01 99110-BY000         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00CN7 MDPS C 1.00 1.02 56310/BY050 4CNHC102',
      b'\xf1\x00CN7 MDPS C 1.00 1.03 56310/BY050 4CNHC103',
      b'\xf1\x00CN7 MDPS C 1.00 1.03 56310BY0500 4CNHC103',
      b'\xf1\x00CN7 MDPS C 1.00 1.04 56310BY050\x00 4CNHC104',
    ],
  },
  CAR.HYUNDAI_SONATA_DN8: { # (DN8)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00DN8_ SCC F-CU-      1.00 1.00 99110-L0000         ',
      b'\xf1\x00DN8_ SCC F-CUP      1.00 1.00 99110-L0000         ',
      b'\xf1\x00DN8_ SCC F-CUP      1.00 1.02 99110-L1000         ',
      b'\xf1\x00DN8_ SCC FHCUP      1.00 1.00 99110-L0000         ',
      b'\xf1\x00DN8_ SCC FHCUP      1.00 1.01 99110-L1000         ',
      b'\xf1\x00DN8_ SCC FHCUP      1.00 1.02 99110-L1000         ',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00DN ESC \x01 102\x19\x04\x13 58910-L1300',
      b'\xf1\x00DN ESC \x03 100 \x08\x01 58910-L0300',
      b'\xf1\x00DN ESC \x06 104\x19\x08\x01 58910-L0100',
      b'\xf1\x00DN ESC \x06 106 \x07\x01 58910-L0100',
      b'\xf1\x00DN ESC \x06 107 \x07\x03 58910-L1300',
      b'\xf1\x00DN ESC \x06 107"\x08\x07 58910-L0100',
      b'\xf1\x00DN ESC \x07 104\x19\x08\x01 58910-L0100',
      b'\xf1\x00DN ESC \x07 106 \x07\x01 58910-L0100',
      b'\xf1\x00DN ESC \x07 107"\x08\x07 58910-L0100',
      b'\xf1\x00DN ESC \x08 103\x19\x06\x01 58910-L1300',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00DN8 MDPS C 1,00 1,01 56310L0010\x00 4DNAC101',
      b'\xf1\x00DN8 MDPS C 1.00 1.01 56310-L0010 4DNAC101',
      b'\xf1\x00DN8 MDPS C 1.00 1.01 56310-L0210 4DNAC101',
      b'\xf1\x00DN8 MDPS C 1.00 1.01 56310-L0210 4DNAC102',
      b'\xf1\x00DN8 MDPS C 1.00 1.01 56310L0010\x00 4DNAC101',
      b'\xf1\x00DN8 MDPS C 1.00 1.01 56310L0200\x00 4DNAC102',
      b'\xf1\x00DN8 MDPS C 1.00 1.01 56310L0210\x00 4DNAC101',
      b'\xf1\x00DN8 MDPS C 1.00 1.01 56310L0210\x00 4DNAC102',
      b'\xf1\x00DN8 MDPS C 1.00 1.03 56310-L1010 4DNDC103',
      b'\xf1\x00DN8 MDPS C 1.00 1.03 56310-L1030 4DNDC103',
      b'\xf1\x00DN8 MDPS R 1.00 1.00 57700-L0000 4DNAP100',
      b'\xf1\x00DN8 MDPS R 1.00 1.00 57700-L0000 4DNAP101',
      b'\xf1\x00DN8 MDPS R 1.00 1.02 57700-L1000 4DNDP105',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DN8 MFC  AT KOR LHD 1.00 1.02 99211-L1000 190422',
      b'\xf1\x00DN8 MFC  AT KOR LHD 1.00 1.04 99211-L1000 191016',
      b'\xf1\x00DN8 MFC  AT RUS LHD 1.00 1.03 99211-L1000 190705',
      b'\xf1\x00DN8 MFC  AT USA LHD 1.00 1.00 99211-L0000 190716',
      b'\xf1\x00DN8 MFC  AT USA LHD 1.00 1.01 99211-L0000 191016',
      b'\xf1\x00DN8 MFC  AT USA LHD 1.00 1.03 99211-L0000 210603',
      b'\xf1\x00DN8 MFC  AT USA LHD 1.00 1.05 99211-L1000 201109',
      b'\xf1\x00DN8 MFC  AT USA LHD 1.00 1.06 99211-L1000 210325',
      b'\xf1\x00DN8 MFC  AT USA LHD 1.00 1.07 99211-L1000 211223',
    ],
  },
  CAR.HYUNDAI_SONATA_DN8_HEV: { # (DN8)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00DNhe SCC F-CUP      1.00 1.02 99110-L5000         ',
      b'\xf1\x00DNhe SCC FHCUP      1.00 1.00 99110-L5000         ',
      b'\xf1\x00DNhe SCC FHCUP      1.00 1.02 99110-L5000         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00DN8 MDPS C 1.00 1.01 56310-L5000 4DNHC101',
      b'\xf1\x00DN8 MDPS C 1.00 1.02 56310-L5450 4DNHC102',
      b'\xf1\x00DN8 MDPS C 1.00 1.02 56310-L5500 4DNHC102',
      b'\xf1\x00DN8 MDPS C 1.00 1.03 56310-L5450 4DNHC103',
      b'\xf1\x00DN8 MDPS C 1.00 1.03 56310L5450\x00 4DNHC104',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DN8HMFC  AT KOR LHD 1.00 1.03 99211-L1000 190705',
      b'\xf1\x00DN8HMFC  AT USA LHD 1.00 1.04 99211-L1000 191016',
      b'\xf1\x00DN8HMFC  AT USA LHD 1.00 1.05 99211-L1000 201109',
      b'\xf1\x00DN8HMFC  AT USA LHD 1.00 1.06 99211-L1000 210325',
      b'\xf1\x00DN8HMFC  AT USA LHD 1.00 1.07 99211-L1000 211223',
    ],
  },
  CAR.HYUNDAI_SONATA_LF: { # (LF)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00LF__ SCC F-CUP      1.00 1.00 96401-C2200         ',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00LF ESC \t 11 \x17\x01\x13 58920-C2610',
      b'\xf1\x00LF ESC \x0c 11 \x17\x01\x13 58920-C2610',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00LFF LKAS AT USA LHD 1.00 1.01 95740-C1000 E51',
      b'\xf1\x00LFF LKAS AT USA LHD 1.01 1.02 95740-C1000 E52',
    ],
  },
  CAR.HYUNDAI_KONA: { # (OS)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00OS__ SCC F-CUP      1.00 1.00 95655-J9200         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00OS  MDPS C 1.00 1.05 56310J9030\x00 4OSDC105',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00OS9 LKAS AT USA LHD 1.00 1.00 95740-J9300 g21',
    ],
  },
  CAR.HYUNDAI_KONA_EV: { # (OS)
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00OS IEB \x01 212 \x11\x13 58520-K4000',
      b'\xf1\x00OS IEB \x02 212 \x11\x13 58520-K4000',
      b'\xf1\x00OS IEB \x03 210 \x02\x14 58520-K4000',
      b'\xf1\x00OS IEB \x03 212 \x11\x13 58520-K4000',
      b'\xf1\x00OS IEB \r 105\x18\t\x18 58520-K4000',
      # 2022
      b'\xf1\x00OS IEB \x02 102"\x05\x16 58520-K4010',
      b'\xf1\x00OS IEB \x03 101 \x11\x13 58520-K4010',
      b'\xf1\x00OS IEB \x03 102"\x05\x16 58520-K4010',
      b'\xf1\x00OS IEB \r 102"\x05\x16 58520-K4010',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00OE2 LKAS AT EUR LHD 1.00 1.00 95740-K4200 200',
      b'\xf1\x00OSE LKAS AT EUR LHD 1.00 1.00 95740-K4100 W40',
      b'\xf1\x00OSE LKAS AT EUR RHD 1.00 1.00 95740-K4100 W40',
      b'\xf1\x00OSE LKAS AT KOR LHD 1.00 1.00 95740-K4100 W40',
      b'\xf1\x00OSE LKAS AT USA LHD 1.00 1.00 95740-K4300 W50',
      # 2022
      b'\xf1\x00OSP LKA  AT AUS RHD 1.00 1.04 99211-J9200 904',
      b'\xf1\x00OSP LKA  AT CND LHD 1.00 1.02 99211-J9110 802',
      b'\xf1\x00OSP LKA  AT EUR LHD 1.00 1.04 99211-J9200 904',
      b'\xf1\x00OSP LKA  AT EUR RHD 1.00 1.02 99211-J9110 802',
      b'\xf1\x00OSP LKA  AT EUR RHD 1.00 1.04 99211-J9200 904',
      b'\xf1\x00OSP LKA  AT USA LHD 1.00 1.04 99211-J9200 904',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00OS  MDPS C 1.00 1.03 56310/K4550 4OEDC103',
      b'\xf1\x00OS  MDPS C 1.00 1.04 56310-XX000 4OEDC104',
      b'\xf1\x00OS  MDPS C 1.00 1.04 56310K4000\x00 4OEDC104',
      b'\xf1\x00OS  MDPS C 1.00 1.04 56310K4050\x00 4OEDC104',
      # 2022
      b'\xf1\x00OSP MDPS C 1.00 1.02 56310-K4271 4OEPC102',
      b'\xf1\x00OSP MDPS C 1.00 1.02 56310/K4271 4OEPC102',
      b'\xf1\x00OSP MDPS C 1.00 1.02 56310/K4970 4OEPC102',
      b'\xf1\x00OSP MDPS C 1.00 1.02 56310K4260\x00 4OEPC102',
      b'\xf1\x00OSP MDPS C 1.00 1.02 56310K4261\x00 4OEPC102',
      b'\xf1\x00OSP MDPS C 1.00 1.02 56310K4971\x00 4OEPC102',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00OSev SCC F-CUP      1.00 1.00 99110-K4000         ',
      b'\xf1\x00OSev SCC F-CUP      1.00 1.00 99110-K4100         ',
      b'\xf1\x00OSev SCC F-CUP      1.00 1.01 99110-K4000         ',
      b'\xf1\x00OSev SCC FNCUP      1.00 1.01 99110-K4000         ',
      # 2022
      b'\xf1\x00YB__ FCA -----      1.00 1.01 99110-K4500      \x00\x00\x00',
    ],
  },
  CAR.HYUNDAI_KONA_HEV: {
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00OS IEB \x01 104 \x11  58520-CM000',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00OShe SCC FNCUP      1.00 1.01 99110-CM000         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00OS  MDPS C 1.00 1.00 56310CM030\x00 4OHDC100',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00OSH LKAS AT KOR LHD 1.00 1.01 95740-CM000 l31',
    ],
  },
  CAR.HYUNDAI_IONIQ: { # (AE)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00AEhe SCC H-CUP      1.01 1.01 96400-G2000         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00AE  MDPS C 1.00 1.07 56310/G2301 4AEHC107',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00AEH MFC  AT EUR LHD 1.00 1.00 95740-G2400 180222',
    ],
  },
  CAR.HYUNDAI_IONIQ_EV: { # (AE)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00AEev SCC F-CUP      1.00 1.00 96400-G7000         ',
      b'\xf1\x00AEev SCC F-CUP      1.00 1.00 96400-G7100         ',
      # 2020
      b'\xf1\x00AEev SCC F-CUP      1.00 1.00 99110-G7200         ',
      b'\xf1\x00AEev SCC F-CUP      1.00 1.00 99110-G7500         ',
      b'\xf1\x00AEev SCC F-CUP      1.00 1.01 99110-G7000         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00AE  MDPS C 1.00 1.02 56310G7300\x00 4AEEC102',
      b'\xf1\x00AE  MDPS C 1.00 1.03 56310/G7300 4AEEC103',
      b'\xf1\x00AE  MDPS C 1.00 1.03 56310G7300\x00 4AEEC103',
      b'\xf1\x00AE  MDPS C 1.00 1.04 56310/G7301 4AEEC104',
      b'\xf1\x00AE  MDPS C 1.00 1.04 56310/G7501 4AEEC104',
      # 2020
      b'\xf1\x00AE  MDPS C 1.00 1.01 56310/G7310 4APEC101',
      b'\xf1\x00AE  MDPS C 1.00 1.01 56310/G7560 4APEC101',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00AEE MFC  AT EUR LHD 1.00 1.00 95740-G2300 170703',
      b'\xf1\x00AEE MFC  AT EUR LHD 1.00 1.00 95740-G2400 180222',
      b'\xf1\x00AEE MFC  AT EUR LHD 1.00 1.00 95740-G7200 160418',
      b'\xf1\x00AEE MFC  AT USA LHD 1.00 1.00 95740-G2400 180222',
      #
      b'\xf1\x00AEE MFC  AT EUR LHD 1.00 1.00 95740-G2600 190730',
      b'\xf1\x00AEE MFC  AT EUR LHD 1.00 1.00 95740-G2700 201027',
      b'\xf1\x00AEE MFC  AT EUR LHD 1.00 1.01 95740-G2600 190819',
      b'\xf1\x00AEE MFC  AT EUR LHD 1.00 1.03 95740-G2500 190516',
      b'\xf1\x00AEE MFC  AT EUR RHD 1.00 1.01 95740-G2600 190819',
    ],
  },
  CAR.HYUNDAI_IONIQ_HEV: { # (AE)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00AEhe SCC F-CUP      1.00 1.00 99110-G2200         ',
      b'\xf1\x00AEhe SCC F-CUP      1.00 1.02 99110-G2100         ',
      b'\xf1\x00AEhe SCC FHCUP      1.00 1.02 99110-G2100         ',
      #
      b'\xf1\x00AEhe SCC H-CUP      1.01 1.01 96400-G2100         ',
      # 2022
      b'\xf1\x00AEhe SCC F-CUP      1.00 1.00 99110-G2600         ',
      b'\xf1\x00AEhe SCC FHCUP      1.00 1.00 99110-G2600         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00AE  MDPS C 1.00 1.01 56310/G2310 4APHC101',
      b'\xf1\x00AE  MDPS C 1.00 1.01 56310/G2510 4APHC101',
      b'\xf1\x00AE  MDPS C 1.00 1.01 56310/G2560 4APHC101',
      #
      b'\xf1\x00AE  MDPS C 1.00 1.05 56310/G2501 4AEHC105',
      b'\xf1\x00AE  MDPS C 1.00 1.07 56310/G2501 4AEHC107',
      #
      b'\xf1\x00AE  MDPS C 1.00 1.01 56310G2510\x00 4APHC101',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00AEP MFC  AT EUR LHD 1.00 1.01 95740-G2600 190819',
      b'\xf1\x00AEP MFC  AT EUR RHD 1.00 1.01 95740-G2600 190819',
      b'\xf1\x00AEP MFC  AT USA LHD 1.00 1.00 95740-G2700 201027',
      b'\xf1\x00AEP MFC  AT USA LHD 1.00 1.01 95740-G2600 190819',
      #
      b'\xf1\x00AEP MFC  AT USA LHD 1.00 1.00 95740-G2400 180222',
    ],
  },
  CAR.HYUNDAI_TUCSON: { # (TL)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00TL__ FCA F-CUP      1.00 1.01 99110-D3500         ',
      b'\xf1\x00TL__ FCA F-CUP      1.00 1.02 99110-D3510         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00TL  MFC  AT KOR LHD 1.00 1.02 95895-D3800 180719',
      b'\xf1\x00TL  MFC  AT USA LHD 1.00 1.06 95895-D3800 190107',
    ],
  },
  CAR.HYUNDAI_SANTAFE: { # (TM)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00TM__ SCC F-CUP      1.00 1.00 99110-S1210         ',
      b'\xf1\x00TM__ SCC F-CUP      1.00 1.01 99110-S2000         ',
      b'\xf1\x00TM__ SCC F-CUP      1.00 1.02 99110-S2000         ',
      b'\xf1\x00TM__ SCC F-CUP      1.00 1.03 99110-S2000         ',
      # 2022
      b'\xf1\x00TM__ SCC F-CUP      1.00 1.00 99110-S1500         ',
      b'\xf1\x00TM__ SCC FHCUP      1.00 1.00 99110-S1500         ',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00TM ESC \x02 100\x18\x030 58910-S2600',
      b'\xf1\x00TM ESC \x02 102\x18\x07\x01 58910-S2600',
      b'\xf1\x00TM ESC \x02 103\x18\x11\x07 58910-S2600',
      b'\xf1\x00TM ESC \x02 104\x19\x07\x07 58910-S2600',
      b'\xf1\x00TM ESC \x03 103\x18\x11\x07 58910-S2600',
      b'\xf1\x00TM ESC \x0c 103\x18\x11\x08 58910-S2650',
      b'\xf1\x00TM ESC \r 100\x18\x031 58910-S2650',
      b'\xf1\x00TM ESC \r 103\x18\x11\x08 58910-S2650',
      b'\xf1\x00TM ESC \r 104\x19\x07\x08 58910-S2650',
      b'\xf1\x00TM ESC \r 105\x19\x05# 58910-S1500',
      # 2022
      b'\xf1\x00TM ESC \x01 102!\x04\x03 58910-S2DA0',
      b'\xf1\x00TM ESC \x01 104"\x10\x07 58910-S2DA0',
      b'\xf1\x00TM ESC \x02 101 \x08\x04 58910-S2GA0',
      b'\xf1\x00TM ESC \x02 103"\x07\x08 58910-S2GA0',
      b'\xf1\x00TM ESC \x03 101 \x08\x02 58910-S2DA0',
      b'\xf1\x00TM ESC \x03 102!\x04\x03 58910-S2DA0',
      b'\xf1\x00TM ESC \x04 101 \x08\x04 58910-S2GA0',
      b'\xf1\x00TM ESC \x04 102!\x04\x05 58910-S2GA0',
      b'\xf1\x00TM ESC \x04 103"\x07\x08 58910-S2GA0',
      b'\xf1\x00TM ESC \x1e 102 \x08\x08 58910-S1DA0',
      b'\xf1\x00TM ESC   103!\x030 58910-S1MA0',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00TM  MDPS C 1.00 1.00 56340-S2000 8409',
      b'\xf1\x00TM  MDPS C 1.00 1.00 56340-S2000 8A12',
      b'\xf1\x00TM  MDPS C 1.00 1.01 56340-S2000 9129',
      b'\xf1\x00TM  MDPS R 1.00 1.02 57700-S1100 4TMDP102',
      # 2022
      b'\xf1\x00TM  MDPS C 1.00 1.01 56310-S1AB0 4TSDC101',
      b'\xf1\x00TM  MDPS C 1.00 1.01 56310-S1EB0 4TSDC101',
      b'\xf1\x00TM  MDPS C 1.00 1.02 56370-S2AA0 0B19',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00TM  MFC  AT EUR LHD 1.00 1.01 99211-S1010 181207',
      b'\xf1\x00TM  MFC  AT USA LHD 1.00 1.00 99211-S2000 180409',
      # 2022
      b'\xf1\x00TM  MFC  AT EUR LHD 1.00 1.03 99211-S1500 210224',
      b'\xf1\x00TM  MFC  AT MES LHD 1.00 1.05 99211-S1500 220126',
      b'\xf1\x00TMA MFC  AT MEX LHD 1.00 1.01 99211-S2500 210205',
      b'\xf1\x00TMA MFC  AT USA LHD 1.00 1.00 99211-S2500 200720',
      b'\xf1\x00TMA MFC  AT USA LHD 1.00 1.01 99211-S2500 210205',
      b'\xf1\x00TMA MFC  AT USA LHD 1.00 1.03 99211-S2500 220414',
    ],
  },
  CAR.HYUNDAI_SANTAFE_HEV: { # (TM)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00TMhe SCC FHCUP      1.00 1.00 99110-CL500         ',
      #
      b'\xf1\x00TMhe SCC FHCUP      1.00 1.00 99110-CL500         ',
      b'\xf1\x00TMhe SCC FHCUP      1.00 1.01 99110-CL500         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00TM  MDPS C 1.00 1.02 56310-CLAC0 4TSHC102',
      b'\xf1\x00TM  MDPS C 1.00 1.02 56310-CLEC0 4TSHC102',
      b'\xf1\x00TM  MDPS C 1.00 1.02 56310-GA000 4TSHA100',
      b'\xf1\x00TM  MDPS C 1.00 1.02 56310GA000\x00 4TSHA100',
      b'\xf1\x00TM  MDPS R 1.00 1.05 57700-CL000 4TSHP105',
      b'\xf1\x00TM  MDPS R 1.00 1.06 57700-CL000 4TSHP106',
      #
      b'\xf1\x00TM  MDPS C 1.00 1.02 56310-CLAC0 4TSHC102',
      b'\xf1\x00TM  MDPS C 1.00 1.02 56310-CLEC0 4TSHC102',
      b'\xf1\x00TM  MDPS C 1.00 1.02 56310CLEC0\x00 4TSHC102',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00TMA MFC  AT USA LHD 1.00 1.03 99211-S2500 220414',
      b'\xf1\x00TMH MFC  AT EUR LHD 1.00 1.06 99211-S1500 220727',
      b'\xf1\x00TMH MFC  AT KOR LHD 1.00 1.06 99211-S1500 220727',
      b'\xf1\x00TMH MFC  AT USA LHD 1.00 1.03 99211-S1500 210224',
      b'\xf1\x00TMH MFC  AT USA LHD 1.00 1.05 99211-S1500 220126',
      b'\xf1\x00TMH MFC  AT USA LHD 1.00 1.06 99211-S1500 220727',
      #
      b'\xf1\x00TMP MFC  AT USA LHD 1.00 1.03 99211-S1500 210224',
      b'\xf1\x00TMP MFC  AT USA LHD 1.00 1.05 99211-S1500 220126',
      b'\xf1\x00TMP MFC  AT USA LHD 1.00 1.06 99211-S1500 220727',
    ],
  },
  CAR.HYUNDAI_PALISADE: { # (LX2)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00LX2 SCC FHCUP      1.00 1.04 99110-S8100         ',
      b'\xf1\x00LX2_ SCC F-CUP      1.00 1.04 99110-S8100         ',
      b'\xf1\x00LX2_ SCC F-CUP      1.00 1.05 99110-S8100         ',
      b'\xf1\x00LX2_ SCC FHCU-      1.00 1.05 99110-S8100         ',
      b'\xf1\x00LX2_ SCC FHCUP      1.00 1.00 99110-S8110         ',
      b'\xf1\x00LX2_ SCC FHCUP      1.00 1.03 99110-S8100         ',
      b'\xf1\x00LX2_ SCC FHCUP      1.00 1.04 99110-S8100         ',
      b'\xf1\x00LX2_ SCC FHCUP      1.00 1.05 99110-S8100         ',
      b'\xf1\x00ON__ FCA FHCUP      1.00 1.00 99110-S9110         ',
      b'\xf1\x00ON__ FCA FHCUP      1.00 1.01 99110-S9110         ',
      b'\xf1\x00ON__ FCA FHCUP      1.00 1.02 99110-S9100         ',
      b'\xf1\x00ON__ FCA FHCUP      1.00 1.03 99110-S9100         ',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00LX ESC \x01 103\x19\t\x10 58910-S8360',
      b'\xf1\x00LX ESC \x01 1031\t\x10 58910-S8360',
      b'\xf1\x00LX ESC \x01 104 \x10\x15 58910-S8350',
      b'\xf1\x00LX ESC \x01 104 \x10\x16 58910-S8360',
      b'\xf1\x00LX ESC \x0b 101\x19\x03\x17 58910-S8330',
      b'\xf1\x00LX ESC \x0b 101\x19\x03  58910-S8360',
      b'\xf1\x00LX ESC \x0b 102\x19\x05\x07 58910-S8330',
      b'\xf1\x00LX ESC \x0b 103\x19\t\x07 58910-S8330',
      b'\xf1\x00LX ESC \x0b 103\x19\t\t 58910-S8350',
      b'\xf1\x00LX ESC \x0b 103\x19\t\x10 58910-S8360',
      b'\xf1\x00LX ESC \x0b 104 \x10\x16 58910-S8360',
      b'\xf1\x00ON ESC \x01 101\x19\t\x08 58910-S9360',
      b'\xf1\x00ON ESC \x0b 100\x18\x12\x18 58910-S9360',
      b'\xf1\x00ON ESC \x0b 101\x19\t\x05 58910-S9320',
      b'\xf1\x00ON ESC \x0b 101\x19\t\x08 58910-S9360',
      b'\xf1\x00ON ESC \x0b 103$\x04\x08 58910-S9360',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00LX2 MDPS C 1,00 1,03 56310-S8020 4LXDC103',
      b'\xf1\x00LX2 MDPS C 1.00 1.03 56310-S8000 4LXDC103',
      b'\xf1\x00LX2 MDPS C 1.00 1.03 56310-S8020 4LXDC103',
      b'\xf1\x00LX2 MDPS C 1.00 1.03 56310-XX000 4LXDC103',
      b'\xf1\x00LX2 MDPS C 1.00 1.04 56310-S8020 4LXDC104',
      b'\xf1\x00LX2 MDPS C 1.00 1.04 56310-S8420 4LXDC104',
      b'\xf1\x00LX2 MDPS R 1.00 1.02 56370-S8300 9318',
      b'\xf1\x00ON  MDPS C 1.00 1.00 56340-S9000 8B13',
      b'\xf1\x00ON  MDPS C 1.00 1.01 56340-S9000 9201',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00LX2 MFC  AT KOR LHD 1.00 1.08 99211-S8100 200903',
      b'\xf1\x00LX2 MFC  AT USA LHD 1.00 1.00 99211-S8110 210226',
      b'\xf1\x00LX2 MFC  AT USA LHD 1.00 1.03 99211-S8100 190125',
      b'\xf1\x00LX2 MFC  AT USA LHD 1.00 1.05 99211-S8100 190909',
      b'\xf1\x00LX2 MFC  AT USA LHD 1.00 1.07 99211-S8100 200422',
      b'\xf1\x00LX2 MFC  AT USA LHD 1.00 1.08 99211-S8100 200903',
      b'\xf1\x00ON  MFC  AT USA LHD 1.00 1.01 99211-S9100 181105',
      b'\xf1\x00ON  MFC  AT USA LHD 1.00 1.03 99211-S9100 200720',
      b'\xf1\x00ON  MFC  AT USA LHD 1.00 1.04 99211-S9100 211227',
    ],
  },
  CAR.HYUNDAI_VELOSTER: { # (JS)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00JS__ SCC H-CUP      1.00 1.02 95650-J3200         ',
      b'\xf1\x00JS__ SCC HNCUP      1.00 1.02 95650-J3100         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00JSL MDPS C 1.00 1.03 56340-J3000 8308',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00JS  LKAS AT KOR LHD 1.00 1.03 95740-J3000 K33',
      b'\xf1\x00JS  LKAS AT USA LHD 1.00 1.02 95740-J3000 K32',
    ],
  },
  CAR.HYUNDAI_NEXO: { # (FE)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00FE__ SCC FHCUP      1.00 1.03 99110-M5000         ',
    ],
    (Ecu.abs, 0x7c1, None): [
      b'\xf1\x00FE IEB \x01 312 \x11\x13 58520-M5000',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00FE  MDPS C 1.00 1.05 56340-M5000 9903',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00FE  MFC  AT KOR LHD 1.00 1.04 99211-M5000 180918',
    ],
  },
  CAR.HYUNDAI_GRANDEUR: { # (IG)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00IG__ SCC F_CUP   1.00 1.00 95400M9500     \xf1\xa01.00',
      b'\xf1\x00IG__ SCC F_CUP   1.00 1.00 96400G8000     \xf1\xa01.00',
      b'\xf1\x00IG__ SCC F-CU-      1.00 1.00 99110-G8100         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00IG  MDPS C 1.00 1.02 56310G8510\x00 4IGSC103',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\000IG MFC  1.00 1.00 95740F9200 180916',
    ],
  },
  CAR.HYUNDAI_GRANDEUR_HEV: { # (IG)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00IGhe SCC FHCUP      1.00 1.00 99110-M9100         ',
      b'\xf1\x00IGhe SCC FHCUP      1.00 1.01 99110-M9000         ',
      b'\xf1\x00IGhe SCC FHCUP      1.00 1.02 99110-M9000         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00IG  MDPS C 1.00 1.00 56310M9600\x00 4IHSC100',
      b'\xf1\x00IG  MDPS C 1.00 1.01 56310M9350\x00 4IH8C101',
      b'\xf1\x00IG  MDPS C 1.00 1.02 56310M9350\x00 4IH8C102',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00IGH MFC  AT KOR LHD 1.00 1.00 99211-G8000 180903',
      b'\xf1\x00IGH MFC  AT KOR LHD 1.00 1.01 99211-G8000 181109',
      b'\xf1\x00IGH MFC  AT KOR LHD 1.00 1.02 99211-G8100 191029',
    ],
  },
  CAR.HYUNDAI_GRANDEUR_FL: { # (IG)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00IG__ SCC F-CU-      1.00 1.00 99110-G8100         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00IG  MDPS C 1.00 1.02 56310G8510\x00 4IGSC103',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00IG  MFC  AT MES LHD 1.00 1.04 99211-G8100 200511',
    ],
  },
  CAR.HYUNDAI_GRANDEUR_FL_HEV: {  # (IG)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00IGH MFC  AT KOR LHD 1.00 1.00 99211-G8000 180903',
      b'\xf1\x00IGH MFC  AT KOR LHD 1.00 1.01 99211-G8000 181109',
      b'\xf1\x00IGH MFC  AT KOR LHD 1.00 1.02 99211-G8100 191029',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00IG  MDPS C 1.00 1.00 56310M9600\x00 4IHSC100',
      b'\xf1\x00IG  MDPS C 1.00 1.01 56310M9350\x00 4IH8C101',
      b'\xf1\x00IG  MDPS C 1.00 1.02 56310M9350\x00 4IH8C102',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00IGhe SCC FHCUP      1.00 1.00 99110-M9100         ',
      b'\xf1\x00IGhe SCC FHCUP      1.00 1.01 99110-M9000         ',
      b'\xf1\x00IGhe SCC FHCUP      1.00 1.02 99110-M9000         ',
    ],
  },

  # CANFD hyundai
  CAR.HYUNDAI_IONIQ5: {  # (NE1)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00NE1_ RDR -----      1.00 1.00 99110-GI000         ',
      # 5N
      b'\xf1\x00NE1N RDR -----      1.00 1.00 99110-NI000         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00NE1 MFC  AT CAN LHD 1.00 1.01 99211-GI010 211007',
      b'\xf1\x00NE1 MFC  AT CAN LHD 1.00 1.05 99211-GI010 220614',
      b'\xf1\x00NE1 MFC  AT EUR LHD 1.00 1.01 99211-GI010 211007',
      b'\xf1\x00NE1 MFC  AT EUR LHD 1.00 1.06 99211-GI000 210813',
      b'\xf1\000NE1 MFC  AT KOR LHD 1.00 1.06 99211-GI000 210813',
      b'\xf1\x00NE1 MFC  AT EUR LHD 1.00 1.06 99211-GI010 230110',
      b'\xf1\x00NE1 MFC  AT EUR RHD 1.00 1.01 99211-GI010 211007',
      b'\xf1\x00NE1 MFC  AT EUR RHD 1.00 1.02 99211-GI010 211206',
      b'\xf1\x00NE1 MFC  AT KOR LHD 1.00 1.00 99211-GI020 230719',
      b'\xf1\x00NE1 MFC  AT KOR LHD 1.00 1.05 99211-GI010 220614',
      b'\xf1\x00NE1 MFC  AT USA LHD 1.00 1.00 99211-GI020 230719',
      b'\xf1\x00NE1 MFC  AT USA LHD 1.00 1.01 99211-GI010 211007',
      b'\xf1\x00NE1 MFC  AT USA LHD 1.00 1.02 99211-GI010 211206',
      b'\xf1\x00NE1 MFC  AT USA LHD 1.00 1.03 99211-GI010 220401',
      b'\xf1\x00NE1 MFC  AT USA LHD 1.00 1.05 99211-GI010 220614',
      b'\xf1\x00NE1 MFC  AT USA LHD 1.00 1.06 99211-GI010 230110',
      b'\xf1\x00NE1 MFC  AT USA LHD 1.00 1.00 99211-GI100 230915',
      b'\xf1\x00NE1 MFC  AT EUR LHD 1.00 1.00 99211-GI100 230915',
      # 5N
      b'\xf1\x00NE1NMFC  AT KOR LHD 1.00 1.04 99211-NI000 231219',
    ],
  },
  CAR.HYUNDAI_IONIQ5_PE: {  # (NE1)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00NE__ RDR -----      1.00 1.00 99110-GI500         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00NE  MFC  AT KOR LHD 1.00 1.02 99211-GI500 240221',
    ],
  },
  CAR.HYUNDAI_IONIQ6: {  # (CE1)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00CE__ RDR -----      1.00 1.01 99110-KL000         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00CE  MFC  AT CAN LHD 1.00 1.04 99211-KL000 221213',
      b'\xf1\x00CE  MFC  AT EUR LHD 1.00 1.03 99211-KL000 221011',
      b'\xf1\x00CE  MFC  AT USA LHD 1.00 1.04 99211-KL000 221213',
    ],
  },
  CAR.HYUNDAI_TUCSON_NX4: {  # (NX4)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00NX4 FR_CMR AT CAN LHD 1.00 1.01 99211-N9100 14A',
      b'\xf1\x00NX4 FR_CMR AT EUR LHD 1.00 1.00 99211-N9220 14K',
      b'\xf1\x00NX4 FR_CMR AT EUR LHD 1.00 2.02 99211-N9000 14E',
      b'\xf1\x00NX4 FR_CMR AT USA LHD 1.00 1.00 99211-N9210 14G',
      b'\xf1\x00NX4 FR_CMR AT USA LHD 1.00 1.00 99211-N9220 14K',
      b'\xf1\x00NX4 FR_CMR AT USA LHD 1.00 1.00 99211-N9240 14Q',
      b'\xf1\x00NX4 FR_CMR AT USA LHD 1.00 1.00 99211-N9250 14W',
      b'\xf1\x00NX4 FR_CMR AT USA LHD 1.00 1.00 99211-N9260 14Y',
      b'\xf1\x00NX4 FR_CMR AT USA LHD 1.00 1.01 99211-N9100 14A',
      b'\xf1\x00NX4 FR_CMR AT USA LHD 1.00 1.01 99211-N9240 14T',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00NX4__               1.00 1.00 99110-N9100         ',
      b'\xf1\x00NX4__               1.00 1.01 99110-N9000         ',
      b'\xf1\x00NX4__               1.00 1.02 99110-N9000         ',
      b'\xf1\x00NX4__               1.01 1.00 99110-N9100         ',
    ],
  },
  CAR.HYUNDAI_KONA_SX2_EV: {
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00SXev RDR -----      1.00 1.00 99110-BF000         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00SX2EMFC  AT KOR LHD 1.00 1.00 99211-BF000 230410',
    ],
  },
  CAR.HYUNDAI_STARIA: {  # (US4)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00US4 MFC  AT KOR LHD 1.00 1.04 99211-CG000 210819',
      b'\xf1\x00US4 MFC  AT KOR LHD 1.00 1.06 99211-CG000 230524',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00US4_ RDR -----      1.00 1.00 99110-CG000         ',
    ],
  },
  CAR.HYUNDAI_SONATA_DN8_24: {  # (DN8)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00DN8_ RDR -----      1.00 1.00 99110-L1800         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DN8 MFC  AT KOR LHD 1.00 1.01 99211-L1800 230512',
    ],
  },

  # kia
  CAR.KIA_K3: { # (BD)
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00BD  MDPS C 1.00 1.02 56310-XX000 4BD2C102',
      b'\xf1\x00BD  MDPS C 1.00 1.08 56310/M6300 4BDDC108',
      b'\xf1\x00BD  MDPS C 1.00 1.08 56310M6300\x00 4BDDC108',
      b'\xf1\x00BDm MDPS C A.01 1.01 56310M7800\x00 4BPMC101',
      b'\xf1\x00BDm MDPS C A.01 1.03 56310M7800\x00 4BPMC103',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00BD  LKAS AT USA LHD 1.00 1.04 95740-M6000 J33',
      b'\xf1\x00BDP LKAS AT USA LHD 1.00 1.05 99211-M6500 744',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00BDPE_SCC FHCUPC     1.00 1.04 99110-M6500\x00\x00\x00\x00\x00\x00\x00\x00\x00',
      b'\xf1\x00BD__ SCC H-CUP      1.00 1.02 99110-M6000         ',
    ],
  },
  CAR.KIA_K5: { # (JF)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00JF__ SCC F-CUP      1.00 1.00 96400-D4000         ',
      b'\xf1\x00JF__ SCC F-CUP      1.00 1.00 96400-D4100         ',
      #
      b'\xf1\x00JF__ SCC F-CUP      1.00 1.00 96400-D4110         ',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00JF ESC \r 14 \026\003\026 58920-D4030',
      b'\xf1\x00JF ESC \x0f 16 \x16\x06\x17 58920-D5080',
      #
      b"\xf1\x00JF ESC \t 11 \x18\x03' 58920-D5260",
      b'\xf1\x00JF ESC \x0b 11 \x18\x030 58920-D5180',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00JFWGN LDWS AT USA LHD 1.00 1.02 95895-D4100 G21',
      b'\xf1\x00JFT LKAS AT EUR LHD 1.00 1.00 95895-D4301 T34',
      b'\xf1\x00JFT LKAS AT KOR LHD 1.00 1.00 95895-D4301 T34',
      #
      b'\xf1\x00JFA LKAS AT USA LHD 1.00 1.02 95895-D5000 h31',
      b'\xf1\x00JFA LKAS AT USA LHD 1.00 1.00 95895-D5001 h32',
      b'\xf1\x00JFA LKAS AT USA LHD 1.00 1.00 95895-D5100 h32',
    ],
  },
  CAR.KIA_K5_HEV: { # (JF)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00JFhe SCC FNCUP      1.00 1.00 96400-A8000         ',
      #
      b'\xf1\x00JFhe SCC FHCUP      1.00 1.01 99110-A8500         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00JFP LKAS AT EUR LHD 1.00 1.03 95895-A8100 160711',
      #
      b'\xf1\x00JFH MFC  AT KOR LHD 1.00 1.01 95895-A8200 180323',
    ],
  },
  CAR.KIA_K5_DL3: { # (DL3)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00DL3_ SCC F-CUP      1.00 1.03 99110-L2100         ',
      b'\xf1\x00DL3_ SCC FHCUP      1.00 1.03 99110-L2000         ',
      b'\xf1\x00DL3_ SCC FHCUP      1.00 1.03 99110-L2100         ',
      b'\xf1\x00DL3_ SCC FHCUP      1.00 1.04 99110-L2100         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00DL3 MDPS C 1.00 1.01 56310-L3110 4DLAC101',
      b'\xf1\x00DL3 MDPS C 1.00 1.01 56310-L3220 4DLAC101',
      b'\xf1\x00DL3 MDPS C 1.00 1.02 56310-L2220 4DLDC102',
      b'\xf1\x00DL3 MDPS C 1.00 1.02 56310L3220\x00 4DLAC102',
      b'\xf1\x00DL3 MDPS R 1.00 1.02 57700-L3000 4DLAP102',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DL3 MFC  AT KOR LHD 1.00 1.04 99210-L2000 210527',
      b'\xf1\x00DL3 MFC  AT USA LHD 1.00 1.03 99210-L3000 200915',
      b'\xf1\x00DL3 MFC  AT USA LHD 1.00 1.04 99210-L3000 210208',
      b'\xf1\x00DL3 MFC  AT USA LHD 1.00 1.05 99210-L3000 211222',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00DL ESC \x01 104 \x07\x12 58910-L2200',
      b'\xf1\x00DL ESC \x03 100 \x08\x02 58910-L3600',
      b'\xf1\x00DL ESC \x06 101 \x04\x02 58910-L3200',
      b'\xf1\x00DL ESC \x06 103"\x08\x06 58910-L3200',
      b'\xf1\x00DL ESC \t 100 \x06\x02 58910-L3800',
      b'\xf1\x00DL ESC \t 101 \x07\x02 58910-L3800',
      b'\xf1\x00DL ESC \t 102"\x08\x10 58910-L3800',
    ],
  },
  CAR.KIA_K5_DL3_HEV: { # (DL3)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00DLhe SCC FHCUP      1.00 1.02 99110-L7000         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00DL3 MDPS C 1.00 1.02 56310-L7000 4DLHC102',
      b'\xf1\x00DL3 MDPS C 1.00 1.02 56310-L7220 4DLHC102',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DL3HMFC  AT KOR LHD 1.00 1.02 99210-L2000 200309',
      b'\xf1\x00DL3HMFC  AT KOR LHD 1.00 1.04 99210-L2000 210527',
    ],
  },
  CAR.KIA_STINGER: { # (CK)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00CK__ SCC F_CUP      1.00 1.01 96400-J5000         ',
      b'\xf1\x00CK__ SCC F_CUP      1.00 1.01 96400-J5100         ',
      b'\xf1\x00CK__ SCC F_CUP      1.00 1.02 96400-J5100         ',
      b'\xf1\x00CK__ SCC F_CUP      1.00 1.03 96400-J5100         ',
      # 2022
      b'\xf1\x00CK__ SCC F-CUP      1.00 1.00 99110-J5500         ',
      b'\xf1\x00CK__ SCC FHCUP      1.00 1.00 99110-J5500         ',
      b'\xf1\x00CK__ SCC FHCUP      1.00 1.00 99110-J5600         ',
      b'\xf1\x00CK__ SCC FHCUP      1.00 1.01 99110-J5100         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00CK  MDPS R 1.00 1.04 57700-J5200 4C2CL104',
      b'\xf1\x00CK  MDPS R 1.00 1.04 57700-J5220 4C2VL104',
      b'\xf1\x00CK  MDPS R 1.00 1.04 57700-J5420 4C4VL104',
      b'\xf1\x00CK  MDPS R 1.00 1.06 57700-J5220 4C2VL106',
      b'\xf1\x00CK  MDPS R 1.00 1.06 57700-J5420 4C4VL106',
      b'\xf1\x00CK  MDPS R 1.00 1.07 57700-J5220 4C2VL107',
      # 2022
      b'\xf1\x00CK  MDPS R 1.00 5.03 57700-J5300 4C2CL503',
      b'\xf1\x00CK  MDPS R 1.00 5.03 57700-J5320 4C2VL503',
      b'\xf1\x00CK  MDPS R 1.00 5.03 57700-J5380 4C2VR503',
      b'\xf1\x00CK  MDPS R 1.00 5.04 57700-J5520 4C4VL504',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00CK  MFC  AT EUR LHD 1.00 1.03 95740-J5000 170822',
      b'\xf1\x00CK  MFC  AT USA LHD 1.00 1.03 95740-J5000 170822',
      b'\xf1\x00CK  MFC  AT USA LHD 1.00 1.04 95740-J5000 180504',
      # 2022
      b'\xf1\x00CK  MFC  AT AUS RHD 1.00 1.00 99211-J5500 210622',
      b'\xf1\x00CK  MFC  AT KOR LHD 1.00 1.00 99211-J5500 210622',
      b'\xf1\x00CK  MFC  AT USA LHD 1.00 1.00 99211-J5500 210622',
      b'\xf1\x00CK  MFC  AT USA LHD 1.00 1.03 99211-J5000 201209',
    ],
  },
  CAR.KIA_K7: { # (YG)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\000YG MFC  1.00 1.01 95740F6100 170717',
      b'\xf1\000YG MFC  1.00 1.03 95740F6200 190605',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00YG__ SCC F_CUP   1.01 1.01 96400F6000     \xf1\xa01.01',
      b'\xf1\x00YG__ SCC F_CUP   1.00 1.04 96400F6400     \xf1\xa01.04',
      b'\xf1\x00YG__ SCC F_CUP   1.01 1.02 96400F6000     \xf1\xa01.02',
      b'\xf1\x00YG__ SCC F_CUP   1.00 1.01 99110F6000     \xf1\xa01.01',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x8758920-F6230\xf1\000NC MGH \t 101\031\t\005 58920F6230\xf1\xa01.01',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\000YG  MDPS C 1.00 1.00 99800F6563\000 4YGAC100',
      b'\xf1\000YG  MDPS C 1.00 1.00 E0000F6563\000 4YGSC100',
      b'\xf1\000YG  MDPS C 1.01 99500F6563\000 4YGDC103',
      b'\xf1\000YG  MDPS C 1.00 1.01 56310F6350\000 4YG7C101',
    ],
  },
  CAR.KIA_SORENTO: { # (UM)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00UMP LKAS AT USA LHD 1.00 1.00 95740-C6550 d00',
      b'\xf1\x00UMP LKAS AT USA LHD 1.01 1.01 95740-C6550 d01',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00UM ESC \x02 12 \x18\x05\x05 58910-C6300',
      b'\xf1\x00UM ESC \x0c 12 \x18\x05\x06 58910-C6330',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00UM__ SCC F-CUP      1.00 1.00 96400-C6500         ',
    ],
  },
  CAR.KIA_MOHAVE: { # (HM)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00HM  MFC  AT KOR LHD 1.00 1.00 99211-2J730 211220',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x87991102J730\xf1\x00HM__ SCC -----      1.00 1.00 99110-2J730         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x8757700-2J800\xf1\x00HM  MDPS R 1.00 1.02 57700-2J800 4HS1R102',
    ],
  },
  CAR.KIA_NIRO_EV: { # (DE)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00DEev SCC F-CUP      1.00 1.00 99110-Q4000         ',
      b'\xf1\x00DEev SCC F-CUP      1.00 1.00 99110-Q4100         ',
      b'\xf1\x00DEev SCC F-CUP      1.00 1.00 99110-Q4500         ',
      b'\xf1\x00DEev SCC F-CUP      1.00 1.00 99110-Q4600         ',
      b'\xf1\x00DEev SCC F-CUP      1.00 1.02 96400-Q4000         ',
      b'\xf1\x00DEev SCC F-CUP      1.00 1.02 96400-Q4100         ',
      b'\xf1\x00DEev SCC F-CUP      1.00 1.03 96400-Q4100         ',
      b'\xf1\x00DEev SCC FHCUP      1.00 1.00 99110-Q4600         ',
      b'\xf1\x00DEev SCC FHCUP      1.00 1.03 96400-Q4000         ',
      b'\xf1\x00DEev SCC FNCUP      1.00 1.00 99110-Q4600         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00DE  MDPS C 1.00 1.04 56310Q4100\x00 4DEEC104',
      b'\xf1\x00DE  MDPS C 1.00 1.05 56310Q4000\x00 4DEEC105',
      b'\xf1\x00DE  MDPS C 1.00 1.05 56310Q4100\x00 4DEEC105',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DEE MFC  AT EUR LHD 1.00 1.00 99211-Q4000 191211',
      b'\xf1\x00DEE MFC  AT EUR LHD 1.00 1.00 99211-Q4100 200706',
      b'\xf1\x00DEE MFC  AT EUR LHD 1.00 1.03 95740-Q4000 180821',
      b'\xf1\x00DEE MFC  AT KOR LHD 1.00 1.03 95740-Q4000 180821',
      b'\xf1\x00DEE MFC  AT USA LHD 1.00 1.00 99211-Q4000 191211',
      b'\xf1\x00DEE MFC  AT USA LHD 1.00 1.01 99211-Q4500 210428',
      b'\xf1\x00DEE MFC  AT USA LHD 1.00 1.03 95740-Q4000 180821',
    ],
  },
  CAR.KIA_NIRO_HEV: { # (DE)
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00DE  MDPS C 1.00 1.01 56310G5520\x00 4DEPC101',
      b'\xf1\x00DE  MDPS C 1.00 1.09 56310G5301\x00 4DEHC109',
      # 2022
      b'\xf1\x00DE  MDPS C 1.00 1.01 56310G5520\x00 4DEPC101',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DEH MFC  AT USA LHD 1.00 1.00 95740-G5010 170117',
      b'\xf1\x00DEP MFC  AT USA LHD 1.00 1.00 95740-G5010 170117',
      b'\xf1\x00DEP MFC  AT USA LHD 1.00 1.01 95740-G5010 170424',
      b'\xf1\x00DEP MFC  AT USA LHD 1.00 1.05 99211-G5000 190826',
      # 2021
      b'\xf1\x00DEH MFC  AT USA LHD 1.00 1.00 99211-G5500 210428',
      b'\xf1\x00DEH MFC  AT USA LHD 1.00 1.07 99211-G5000 201221',
      # 2022
      b'\xf1\x00DEP MFC  AT USA LHD 1.00 1.00 99211-G5500 210428',
      b'\xf1\x00DEP MFC  AT USA LHD 1.00 1.06 99211-G5000 201028',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00DEhe SCC F-CUP      1.00 1.02 99110-G5100         ',
      b'\xf1\x00DEhe SCC H-CUP      1.01 1.02 96400-G5100         ',
      #
      b'\xf1\x00DEhe SCC F-CUP      1.00 1.00 99110-G5600         ',
    ],
  },
  CAR.KIA_SELTOS: { # (SP2)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00SP2_ SCC FHCUP      1.01 1.05 99110-Q5100         ',
    ],
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00SP ESC \x07 101\x19\t\x05 58910-Q5450',
      b'\xf1\x00SP ESC \t 101\x19\t\x05 58910-Q5450',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00SP2 MDPS C 1.00 1.04 56300Q5200          ',
      b'\xf1\x00SP2 MDPS C 1.01 1.05 56300Q5200          ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00SP2 MFC  AT USA LHD 1.00 1.04 99210-Q5000 191114',
      b'\xf1\x00SP2 MFC  AT USA LHD 1.00 1.05 99210-Q5000 201012',
    ],
  },
  CAR.KIA_SOUL_EV: { # (SK3)
    (Ecu.abs, 0x7d1, None): [
      b'\xf1\x00SK IEB \x01 207 \x11  58520-J2000',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00SK3EMFC  AT KOR LHD 1.00 1.03 99211-J2000 190116',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00SK3 MDPS C 1.00 1.02 56300J2200          ',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00SKev SCC FHCUP      1.00 1.03 99110-J2000         ',
    ],
  },

  # CANFD kia
  CAR.KIA_EV6: {  # (CV1)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00CV1_ RDR -----      1.00 1.01 99110-CV000         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00CV1 MFC  AT EUR LHD 1.00 1.05 99210-CV000 211027',
      b'\xf1\x00CV1 MFC  AT EUR LHD 1.00 1.06 99210-CV000 220328',
      b'\xf1\x00CV1 MFC  AT EUR RHD 1.00 1.00 99210-CV100 220630',
      b'\xf1\x00CV1 MFC  AT KOR LHD 1.00 1.04 99210-CV000 210823',
      b'\xf1\x00CV1 MFC  AT KOR LHD 1.00 1.05 99210-CV000 211027',
      b'\xf1\x00CV1 MFC  AT KOR LHD 1.00 1.06 99210-CV000 220328',
      b'\xf1\x00CV1 MFC  AT USA LHD 1.00 1.00 99210-CV100 220630',
      b'\xf1\x00CV1 MFC  AT USA LHD 1.00 1.00 99210-CV200 230510',
      b'\xf1\x00CV1 MFC  AT USA LHD 1.00 1.05 99210-CV000 211027',
      b'\xf1\x00CV1 MFC  AT USA LHD 1.00 1.06 99210-CV000 220328',
      #
      b'\xf1\000CV1 MFC  AT KOR LHD 1.00 1.05 99210-CV000 211027',
    ],
  },
  CAR.KIA_SPORTAGE_NQ5: {  # (NQ5)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00NQ5 FR_CMR AT AUS RHD 1.00 1.00 99211-P1040 663',
      b'\xf1\x00NQ5 FR_CMR AT EUR LHD 1.00 1.00 99211-P1040 663',
      b'\xf1\x00NQ5 FR_CMR AT GEN LHD 1.00 1.00 99211-P1060 665',
      b'\xf1\x00NQ5 FR_CMR AT USA LHD 1.00 1.00 99211-P1030 662',
      b'\xf1\x00NQ5 FR_CMR AT USA LHD 1.00 1.00 99211-P1040 663',
      b'\xf1\x00NQ5 FR_CMR AT USA LHD 1.00 1.00 99211-P1060 665',
      b'\xf1\x00NQ5 FR_CMR AT USA LHD 1.00 1.00 99211-P1070 690',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00NQ5__               1.00 1.02 99110-P1000         ',
      b'\xf1\x00NQ5__               1.00 1.03 99110-CH000         ',
      b'\xf1\x00NQ5__               1.00 1.03 99110-P1000         ',
      b'\xf1\x00NQ5__               1.01 1.03 99110-CH000         ',
      b'\xf1\x00NQ5__               1.01 1.03 99110-P1000         ',
    ],
  },
  CAR.KIA_SPORTAGE_NQ5_HEV: {  # (NQ5)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00NQ5 FR_CMR AT USA LHD 1.00 1.00 99211-P1060 665',
      b'\xf1\x00NQ5 FR_CMR AT GEN LHD 1.00 1.00 99211-P1060 665',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00NQ5__               1.01 1.03 99110-CH000         ',
    ],
  },
  CAR.KIA_SORENTO_MQ4: {  # (MQ4)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00MQ4 MFC  AT USA LHD 1.00 1.00 99210-R5100 221019',
      b'\xf1\x00MQ4 MFC  AT USA LHD 1.00 1.03 99210-R5000 200903',
      b'\xf1\x00MQ4 MFC  AT USA LHD 1.00 1.05 99210-R5000 210623',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00MQ4_ SCC F-CUP      1.00 1.06 99110-P2000         ',
      b'\xf1\x00MQ4_ SCC FHCUP      1.00 1.00 99110-R5000         ',
      b'\xf1\x00MQ4_ SCC FHCUP      1.00 1.06 99110-P2000         ',
      b'\xf1\x00MQ4_ SCC FHCUP      1.00 1.08 99110-P2000         ',
    ],
  },
  CAR.KIA_SORENTO_MQ4_HEV: {  # (MQ4)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00MQ4HMFC  AT KOR LHD 1.00 1.04 99210-P2000 200330',
      b'\xf1\x00MQ4HMFC  AT KOR LHD 1.00 1.12 99210-P2000 230331',
      b'\xf1\x00MQ4HMFC  AT USA LHD 1.00 1.10 99210-P2000 210406',
      b'\xf1\x00MQ4HMFC  AT USA LHD 1.00 1.11 99210-P2000 211217',
      #
      b'\xf1\x00MQ4HMFC  AT KOR LHD 1.01 1.03 99210-P2500 230914',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00MQhe SCC FHCUP      1.00 1.04 99110-P4000         ',
      b'\xf1\x00MQhe SCC FHCUP      1.00 1.06 99110-P4000         ',
      b'\xf1\x00MQhe SCC FHCUP      1.00 1.07 99110-P4000         ',
    ],
  },
  CAR.KIA_NIRO_SG2_EV: {
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00SG2_ RDR -----      1.00 1.01 99110-AT000         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00SG2EMFC  AT EUR LHD 1.01 1.09 99211-AT000 220801',
      b'\xf1\x00SG2EMFC  AT USA LHD 1.01 1.09 99211-AT000 220801',
    ],
  },
  CAR.KIA_NIRO_SG2_HEV: {  # (SG2)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00SG2HMFC  AT USA LHD 1.01 1.08 99211-AT000 220531',
      b'\xf1\x00SG2HMFC  AT USA LHD 1.01 1.09 99211-AT000 220801',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00SG2_ RDR -----      1.00 1.01 99110-AT000         ',
    ],
  },
  CAR.KIA_CARNIVAL_KA4: {  # (KA4)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00KA4 MFC  AT EUR LHD 1.00 1.06 99210-R0000 220221',
      b'\xf1\x00KA4 MFC  AT KOR LHD 1.00 1.06 99210-R0000 220221',
      b'\xf1\x00KA4 MFC  AT USA LHD 1.00 1.00 99210-R0100 230105',
      b'\xf1\x00KA4 MFC  AT USA LHD 1.00 1.01 99210-R0100 230710',
      b'\xf1\x00KA4 MFC  AT USA LHD 1.00 1.05 99210-R0000 201221',
      b'\xf1\x00KA4 MFC  AT USA LHD 1.00 1.06 99210-R0000 220221',
      b'\xf1\x00KA4CMFC  AT CHN LHD 1.00 1.01 99211-I4000 210525',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00KA4_ SCC F-CUP      1.00 1.03 99110-R0000         ',
      b'\xf1\x00KA4_ SCC FHCUP      1.00 1.00 99110-R0100         ',
      b'\xf1\x00KA4_ SCC FHCUP      1.00 1.03 99110-R0000         ',
      b'\xf1\x00KA4c SCC FHCUP      1.00 1.01 99110-I4000         ',
    ],
  },
  CAR.KIA_K8_GL3: {  # (GL3)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00GL3 MFC  AT MES LHD 1.00 1.03 99211-L8000 210907',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00GL3_ RDR -----      1.00 1.02 99110-L8000         ',
    ],
  },
  CAR.KIA_K8_GL3_HEV: {  # (GL3)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00GL3HMFC  AT KOR LHD 1.00 1.03 99211-L8000 210907',
      b'\xf1\x00GL3HMFC  AT KOR LHD 1.00 1.04 99211-L8000 230207',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00GL3_ RDR -----      1.00 1.02 99110-L8000         ',
    ],
  },
  CAR.KIA_EV9: {  # (MV)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00MV__ RDR -----      1.00 1.02 99110-DO700         ',
      b'\xf1\x00MV__ RDR -----      1.00 1.02 99110-DO000         '
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00MV  MFC  AT KOR LHD 1.00 1.01 99211-DO000 230419',
      b'\xf1\x00MV  MFC  AT USA LHD 1.00 1.02 99211-DO000 230616',
      b'\xf1\x00MV  MFC  AT EUR LHD 1.00 1.02 99211-DO000 230616',
    ],
  },
  CAR.KIA_K5_DL3_24_HEV: {  # (DL3)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DL3HMFC  AT KOR LHD 1.00 1.02 99210-L2500 230911'
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00DL3_ RDR -----      1.00 1.01 99110-L2500         ',
    ],
  },

  # Genesis
  CAR.GENESIS: { # (DH)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DH LKAS 1.1 -150210',
      b'\xf1\x00DH LKAS 1.4 -140110',
      b'\xf1\x00DH LKAS 1.5 -140425',
    ],
  },
  CAR.GENESIS_G70: { # (IK)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00IK__ SCC F-CUP      1.00 1.01 96400-G9100         ',
      b'\xf1\x00IK__ SCC F-CUP      1.00 1.02 96400-G9100         ',
      # 2020
      b'\xf1\x00IK__ SCC F-CUP      1.00 1.02 96400-G9100         ',
      b'\xf1\x00IK__ SCC F-CUP      1.00 1.02 96400-G9100         \xf1\xa01.02',
      b'\xf1\x00IK__ SCC FHCUP      1.00 1.02 96400-G9000         ',
      b'\xf1\x00IK__ SCC FHCUP      1.00 1.00 99110-G9300         ',
    ],
    (Ecu.eps, 0x7d4, None): [
      b'\xf1\x00IK  MDPS R 1.00 1.06 57700-G9420 4I4VL106',
      # 2020
      b'\xf1\x00IK  MDPS R 1.00 1.07 57700-G9220 4I2VL107',
      b'\xf1\x00IK  MDPS R 1.00 1.07 57700-G9420 4I4VL107',
      b'\xf1\x00IK  MDPS R 1.00 1.08 57700-G9200 4I2CL108',
      b'\xf1\x00IK  MDPS R 1.00 1.08 57700-G9420 4I4VL108',
      b'\xf1\x00IK  MDPS R 1.00 5.09 57700-G9520 4I4VL509',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00IK  MFC  AT USA LHD 1.00 1.01 95740-G9000 170920',
      # 2020
      b'\xf1\x00IK  MFC  AT KOR LHD 1.00 1.01 95740-G9000 170920',
      b'\xf1\x00IK  MFC  AT USA LHD 1.00 1.04 99211-G9000 220401',
    ],
  },
  CAR.GENESIS_G80: { # (DH)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00DH__ SCC F-CUP      1.00 1.01 96400-B1120         ',
      b'\xf1\x00DH__ SCC F-CUP      1.00 1.02 96400-B1120         ',
      b'\xf1\x00DH__ SCC FHCUP      1.00 1.01 96400-B1110         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00DH  LKAS AT KOR LHD 1.01 1.02 95895-B1500 170810',
      b'\xf1\x00DH  LKAS AT USA LHD 1.01 1.01 95895-B1500 161014',
      b'\xf1\x00DH  LKAS AT KOR LHD 1.01 1.01 95895-B1500 161014',
      b'\xf1\x00DH  LKAS AT USA LHD 1.01 1.02 95895-B1500 170810',
      b'\xf1\x00DH  LKAS AT USA LHD 1.01 1.03 95895-B1500 180713',
      b'\xf1\x00DH  LKAS AT USA LHD 1.01 1.04 95895-B1500 181213',
    ],
  },
  CAR.GENESIS_G90: { # (HI)
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00HI__ SCC F-CUP      1.00 1.01 96400-D2100         ',
      b'\xf1\x00HI__ SCC FHCUP      1.00 1.02 99110-D2100         ',
    ],
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00HI  LKAS AT USA LHD 1.00 1.00 95895-D2020 160302',
      b'\xf1\x00HI  LKAS AT USA LHD 1.00 1.00 95895-D2030 170208',
      b'\xf1\x00HI  MFC  AT USA LHD 1.00 1.03 99211-D2000 190831',
    ],
  },

  # CANFD genesis
  CAR.GENESIS_GV60: {  # (JW1)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00JW1 MFC  AT USA LHD 1.00 1.02 99211-CU000 211215',
      b'\xf1\x00JW1 MFC  AT USA LHD 1.00 1.02 99211-CU100 211215',
      b'\xf1\x00JW1 MFC  AT USA LHD 1.00 1.03 99211-CU000 221118',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00JW1_ RDR -----      1.00 1.00 99110-CU000         ',
    ],
  },
  CAR.GENESIS_GV70: {  # (JK1)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00JK1 MFC  AT USA LHD 1.00 1.01 99211-AR200 220125',
      b'\xf1\x00JK1 MFC  AT USA LHD 1.00 1.01 99211-AR300 220125',
      b'\xf1\x00JK1 MFC  AT USA LHD 1.00 1.04 99211-AR000 210204',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00JK1_ SCC FHCUP      1.00 1.00 99110-AR200         ',
      b'\xf1\x00JK1_ SCC FHCUP      1.00 1.00 99110-AR300         ',
      b'\xf1\x00JK1_ SCC FHCUP      1.00 1.02 99110-AR000         ',
    ],
  },
  CAR.GENESIS_GV80: {  # (JX1)
    (Ecu.fwdCamera, 0x7c4, None): [
      b'\xf1\x00JX1 MFC  AT USA LHD 1.00 1.02 99211-T6110 220513',
      #
      b'\xf1\x00JX1 MFC  AT KOR LHD 1.00 1.02 99211-T6110 220513',
      b'\xf1\x00JX1 MFC  AT MES LHD 1.00 1.04 99211-T6010 200514',
    ],
    (Ecu.fwdRadar, 0x7d0, None): [
      b'\xf1\x00JX1_ SCC FHCUP      1.00 1.01 99110-T6100         ',
      #
      b'\xf1\x00JX1_ SCC -----      1.00 1.03 99110-T6000         ',
    ],
  },
}
