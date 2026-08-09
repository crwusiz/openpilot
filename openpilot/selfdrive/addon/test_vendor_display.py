#!/usr/bin/env python3
"""
벤더 라이브러리 공식 API로 최소 테스트.
cluster 프로세스와 무관하게 단독 실행해서 장치/펌웨어 자체가
이미지를 표시할 수 있는지 확인합니다.

실행 전에 cluster 프로세스(system manager)를 잠깐 꺼두는 걸 권장합니다.
(같은 USB 장치를 동시에 열려고 하면 충돌 가능)

사용법:
  cd <addon 폴더>/cluster/.vendor/turing-smart-screen-python-main
  python3 /path/to/test_vendor_display.py
"""
import sys
import time
from pathlib import Path

# .vendor 폴더를 sys.path에 추가 (library.* import를 위해)
VENDOR_ROOT = Path(__file__).resolve().parent / "cluster" / ".vendor" / "turing-smart-screen-python-main"
sys.path.insert(0, str(VENDOR_ROOT))

from PIL import Image, ImageDraw
from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB

print("Connecting via official LcdCommTuringUSB class...")
lcd = LcdCommTuringUSB()
print(f"Native display size (portrait): {lcd.display_width}x{lcd.display_height}")

lcd.InitializeComm()
time.sleep(0.5)

lcd.SetOrientation(Orientation.LANDSCAPE)
w, h = lcd.get_width(), lcd.get_height()
print(f"Landscape canvas size: {w}x{h}")

lcd.SetBrightness(100)

# 눈에 확 띄는 테스트 이미지: 빨간 배경 + 흰 십자가 + 텍스트
img = Image.new("RGB", (w, h), (255, 0, 0))
draw = ImageDraw.Draw(img)
draw.line([(0, 0), (w, h)], fill=(255, 255, 255), width=10)
draw.line([(0, h), (w, 0)], fill=(255, 255, 255), width=10)
draw.rectangle([0, 0, w - 1, h - 1], outline=(0, 255, 0), width=15)
draw.text((w // 2 - 100, h // 2 - 20), "TEST OK", fill=(255, 255, 0))

print("Sending test image via official DisplayPILImage()...")
lcd.DisplayPILImage(img)
print("Sent. Check the physical screen now.")

time.sleep(3)

# 다른 색으로 한 번 더 (첫 프레임이 우연히 안 보였을 경우 대비)
img2 = Image.new("RGB", (w, h), (0, 0, 255))
draw2 = ImageDraw.Draw(img2)
draw2.text((w // 2 - 100, h // 2 - 20), "FRAME 2", fill=(255, 255, 255))
lcd.DisplayPILImage(img2)
print("Sent second frame (blue).")
