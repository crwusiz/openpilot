"""Bounded Annex B formatting for the USB panel's independent H.264 frames.

Only the hardware encoder's progressive Baseline stream is accepted. Parameter
sets travel with every image so reconnecting or discarding an old image never
requires a previously delivered frame.
"""

MAX_ACCESS_UNIT_BYTES = 2 * 1024 * 1024
MAX_PARAMETER_SET_BYTES = 8 * 1024
MAX_NALS = 1024
START_CODE = b"\x00\x00\x00\x01"


class _Bits:
  def __init__(self, data, limit=None):
    self.data = data
    self.pos = 0
    self.limit = len(data) * 8 if limit is None else limit

  def read(self, count=1):
    if not 0 <= count <= 32 or self.pos + count > self.limit:
      raise ValueError("truncated H.264 header")
    value = 0
    for _ in range(count):
      value = (value << 1) | ((self.data[self.pos // 8] >> (7 - self.pos % 8)) & 1)
      self.pos += 1
    return value

  def ue(self, maximum=(1 << 31) - 1):
    zeros = 0
    while not self.read():
      zeros += 1
      if zeros > 31:
        raise ValueError("oversized H.264 Exp-Golomb value")
    value = (1 << zeros) - 1 + self.read(zeros)
    if value > maximum:
      raise ValueError("H.264 header value exceeds its supported limit")
    return value

  def se(self):
    value = self.ue()
    return (value + 1) // 2 if value & 1 else -(value // 2)


class _Writer:
  def __init__(self):
    self.bits = []

  def write(self, value, count=1):
    self.bits.extend((value >> shift) & 1 for shift in range(count - 1, -1, -1))

  def ue(self, value):
    code = value + 1
    self.write(0, code.bit_length() - 1)
    self.write(code, code.bit_length())

  def prefix(self, data, count):
    for pos in range(count):
      self.write((data[pos // 8] >> (7 - pos % 8)) & 1)

  def finish(self):
    self.write(1)  # rbsp_stop_one_bit
    self.bits.extend([0] * (-len(self.bits) % 8))
    result = bytearray(len(self.bits) // 8)
    for pos, bit in enumerate(self.bits):
      result[pos // 8] |= bit << (7 - pos % 8)
    return bytes(result)


def _unescape(data, max_bytes=None):
  result = bytearray()
  zeros = 0
  for index, byte in enumerate(data):
    if zeros >= 2 and byte == 3:
      if index + 1 >= len(data) or data[index + 1] > 3:
        raise ValueError("invalid H.264 emulation prevention byte")
      zeros = 0
      continue
    result.append(byte)
    if max_bytes is not None and len(result) >= max_bytes:
      break
    zeros = zeros + 1 if byte == 0 else 0
  return bytes(result)


def _escape(data):
  result = bytearray()
  zeros = 0
  for byte in data:
    if zeros >= 2 and byte <= 3:
      result.append(3)
      zeros = 0
    result.append(byte)
    zeros = zeros + 1 if byte == 0 else 0
  return bytes(result)


def _nals(data):
  start = data.find(b"\x00\x00\x01")
  if start < 0 or any(data[:start]):
    raise ValueError("hardware encoder output must use Annex B start codes")
  result = []
  while start >= 0:
    nal_start = start + 3
    start = data.find(b"\x00\x00\x01", nal_start)
    nal = data[nal_start:start if start >= 0 else len(data)].rstrip(b"\x00")
    if not nal or nal[0] & 0x80:
      raise ValueError("empty or invalid H.264 NAL header")
    result.append(nal)
    if len(result) > MAX_NALS:
      raise ValueError("too many H.264 NAL units")
  return result


def _rbsp_reader(nal):
  rbsp = _unescape(nal[1:])
  if not rbsp or not rbsp[-1]:
    raise ValueError("missing H.264 RBSP trailing bits")
  trailing_zeros = (rbsp[-1] & -rbsp[-1]).bit_length() - 1
  stop_bit = len(rbsp) * 8 - trailing_zeros - 1
  return rbsp, _Bits(rbsp, stop_bit)


def _skip_hrd(reader):
  count = reader.ue(31) + 1
  reader.read(8)
  for _ in range(count):
    reader.ue()
    reader.ue()
    reader.read()
  reader.read(20)


def _skip_vui(reader):
  if reader.read():
    if reader.read(8) == 255:
      reader.read(32)
  if reader.read():
    reader.read()
  if reader.read():
    reader.read(4)
    if reader.read():
      reader.read(24)
  if reader.read():
    reader.ue()
    reader.ue()
  if reader.read():
    reader.read(32)
    reader.read(32)
    reader.read()
  nal_hrd = reader.read()
  if nal_hrd:
    _skip_hrd(reader)
  vcl_hrd = reader.read()
  if vcl_hrd:
    _skip_hrd(reader)
  if nal_hrd or vcl_hrd:
    reader.read()
  reader.read()
  if reader.read():
    reader.read()
    for _ in range(6):
      reader.ue()


def _patch_sps(nal, width, height, fps):
  if len(nal) > MAX_PARAMETER_SET_BYTES:
    raise ValueError("SPS exceeds 8 KiB")
  rbsp, reader = _rbsp_reader(nal)
  if reader.read(8) != 66:
    raise ValueError("USB H.264 requires Baseline profile 66")
  constraints = reader.read(8)
  if constraints & 3:
    raise ValueError("nonzero reserved SPS constraint bits")
  reader.read(8)  # level_idc
  sps_id = reader.ue(31)
  reader.ue(12)  # log2_max_frame_num_minus4
  poc_type = reader.ue(2)
  if poc_type == 0:
    reader.ue(12)
  elif poc_type == 1:
    reader.read()
    reader.se()
    reader.se()
    for _ in range(reader.ue(255)):
      reader.se()
  reference_frames = reader.ue(16)
  reader.read()  # gaps_in_frame_num_value_allowed_flag
  coded_width = (reader.ue(511) + 1) * 16
  coded_height = (reader.ue(511) + 1) * 16
  if not reader.read():
    raise ValueError("interlaced H.264 is unsupported")
  reader.read()  # direct_8x8_inference_flag
  crop_bit = reader.pos
  if reader.read():
    for _ in range(4):
      reader.ue(4096)
  if reader.read():
    _skip_vui(reader)
  if reader.pos != reader.limit:
    raise ValueError("unexpected data after SPS VUI")
  if (coded_width, coded_height) != (((width + 15) // 16) * 16, ((height + 15) // 16) * 16):
    raise ValueError(f"unexpected hardware encoder geometry {coded_width}x{coded_height}; wanted {width}x{height}")

  writer = _Writer()
  writer.prefix(rbsp, crop_bit)
  right, bottom = (coded_width - width) // 2, (coded_height - height) // 2
  writer.write(int(bool(right or bottom)))
  if right or bottom:
    for offset in (0, right, 0, bottom):
      writer.ue(offset)
  writer.write(1)  # vui_parameters_present_flag
  writer.write(0, 4)  # aspect ratio, overscan, video signal, chroma location absent
  writer.write(1)  # timing_info_present_flag
  writer.write(1, 32)  # num_units_in_tick
  writer.write(fps * 2, 32)  # time_scale
  writer.write(1)  # fixed_frame_rate_flag
  writer.write(0, 3)  # NAL HRD, VCL HRD, pic_struct absent
  writer.write(1)  # bitstream_restriction_flag
  writer.write(1)  # motion_vectors_over_pic_boundaries_flag
  for value in (0, 0, 10, 10, 0, max(1, reference_frames)):
    writer.ue(value)
  patched = bytearray(writer.finish())
  patched[1] = constraints | 0x40  # constraint_set1_flag: Constrained Baseline
  return bytes([nal[0]]) + _escape(patched), sps_id


def _pps_ids(nal):
  if len(nal) > MAX_PARAMETER_SET_BYTES:
    raise ValueError("PPS exceeds 8 KiB")
  _rbsp, reader = _rbsp_reader(nal)
  pps_id, sps_id = reader.ue(255), reader.ue(31)
  if reader.read():
    raise ValueError("Baseline PPS must use CAVLC entropy coding")
  reader.read()  # bottom_field_pic_order_in_frame_present_flag
  if reader.ue(7):
    raise ValueError("USB H.264 does not support slice groups")
  reader.ue(31)
  reader.ue(31)
  reader.read(3)  # weighted_pred_flag, weighted_bipred_idc
  for _ in range(3):
    reader.se()
  reader.read(3)  # deblocking, constrained intra, redundant pic flags
  if reader.pos < reader.limit:
    if reader.read() or reader.read():
      raise ValueError("Baseline PPS transform extensions are unsupported")
    reader.se()  # second_chroma_qp_index_offset
  if reader.pos != reader.limit:
    raise ValueError("unexpected data after PPS")
  return pps_id, sps_id


class H264AccessUnitFormatter:
  def __init__(self, width, height, fps):
    if any(not isinstance(value, int) or isinstance(value, bool) for value in (width, height, fps)):
      raise ValueError("USB H.264 dimensions and FPS must be integers")
    if not (2 <= width <= 8192 and 2 <= height <= 8192 and width % 2 == height % 2 == 0 and 1 <= fps <= 60):
      raise ValueError("USB H.264 requires even dimensions up to 8192 and 1..60 FPS")
    self.width, self.height, self.fps = width, height, fps
    self._sps_source = self._sps = self._pps = None
    self._sps_id = self._pps_id = self._pps_sps_id = None

  def format(self, data: bytes) -> bytes | None:
    if not isinstance(data, bytes):
      raise TypeError("H.264 access units must be bytes")
    if not data:
      return None
    if len(data) > MAX_ACCESS_UNIT_BYTES:
      raise RuntimeError("USB H.264 access unit exceeds 2 MiB")
    try:
      return self._format(data)
    except ValueError as error:
      raise RuntimeError(f"Invalid USB H.264 stream: {error}") from error

  def _format(self, data):
    sps_source, sps, pps = self._sps_source, self._sps, self._pps
    sps_id, pps_id, pps_sps_id = self._sps_id, self._pps_id, self._pps_sps_id
    image_nals = []
    idr_pps_ids = set()
    for nal in _nals(data):
      kind = nal[0] & 31
      if kind in (5, 7, 8) and not nal[0] & 0x60:
        raise ValueError("IDR and parameter-set NALs must be reference units")
      if kind in (1, 2, 3, 4, 19, 20, 21):
        raise ValueError("non-IDR frame received; USB H.264 requires independent all-I frames")
      if kind == 7:
        if nal != sps_source:
          sps, sps_id = _patch_sps(nal, self.width, self.height, self.fps)
          sps_source = nal
      elif kind == 8:
        if nal != pps:
          pps_id, pps_sps_id = _pps_ids(nal)
          pps = nal
      elif kind == 5:
        reader = _Bits(_unescape(nal[1:64], max_bytes=16))
        reader.ue(262143)  # first_mb_in_slice, bounded to 8192x8192 macroblocks
        if reader.ue(9) not in (2, 7):
          raise ValueError("IDR NAL does not contain an I slice")
        idr_pps_ids.add(reader.ue(255))
        image_nals.append(nal)
      elif kind in (6, 9, 10, 11, 12):
        image_nals.append(nal)
      else:
        raise ValueError(f"unsupported H.264 NAL type {kind}")
    if idr_pps_ids:
      if sps is None or pps is None or pps_sps_id != sps_id or idr_pps_ids != {pps_id}:
        raise ValueError("IDR frame has no matching SPS/PPS")
      # AUD precedes parameter sets; remaining metadata and slices retain order.
      auds = [nal for nal in image_nals if nal[0] & 31 == 9]
      remaining = [nal for nal in image_nals if nal[0] & 31 != 9]
      output = b"".join(START_CODE + nal for nal in (*auds, sps, pps, *remaining))
      if len(output) > MAX_ACCESS_UNIT_BYTES:
        raise ValueError("formatted access unit exceeds 2 MiB")
    else:
      output = None
    self._sps_source, self._sps, self._pps = sps_source, sps, pps
    self._sps_id, self._pps_id, self._pps_sps_id = sps_id, pps_id, pps_sps_id
    return output
