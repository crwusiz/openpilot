# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
# Adapted from turing-smart-screen-python's lcd_comm_turing_usb.py.
# https://github.com/mathoudebine/turing-smart-screen-python/
# Distributed under the GNU GPL version 3 or later, without any warranty.
"""Command framing required by the TURZX H.264 stream and PNG overlay."""

import struct
import time

from Crypto.Cipher import DES


def build_command_packet_header(command):
  packet = bytearray(500)
  packet[0] = command
  packet[2:4] = b"\x1a\x6d"
  midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
  # The panel protocol requires wall-clock milliseconds since local midnight.
  packet[4:8] = struct.pack("<I", int((time.time() - midnight) * 1000))  # noqa: TID251
  return packet


def encrypt_command_packet(data):
  key = b"slv3tuzx"
  padded = bytes(data).ljust((len(data) + 7) // 8 * 8, b"\x00")
  encrypted = DES.new(key, DES.MODE_CBC, key).encrypt(padded)
  packet = bytearray(512)
  packet[:len(encrypted)] = encrypted
  packet[510:512] = b"\xa1\x1a"
  return packet


def response_ok(response):
  return bool(response) and ((len(response) > 1 and response[1] == 0xC8) or (len(response) > 8 and response[8] == 0xC8))
