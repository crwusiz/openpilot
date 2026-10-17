import ctypes
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from PIL import Image
import pytest

from openpilot.selfdrive.addon.cluster.usb_display import h264_encoder


@pytest.fixture(scope="module")
def native_rgb(tmp_path_factory):
  """Compile/run the production converter without opening any video device."""
  directory = tmp_path_factory.mktemp("usb_h264_rgb")
  source = Path(__file__).parents[4] / "system/loggerd/encoder/cluster_h264_rgb.cc"
  environment = dict(os.environ)
  if os.name == "nt":
    compiler = shutil.which("cl")
    if compiler is None:
      pytest.skip("MSVC is needed to test the native converter on Windows")
    if not environment.get("INCLUDE"):
      vcvars = Path(compiler).parents[6] / "Auxiliary/Build/vcvars64.bat"
      setup = directory / "compiler_environment.cmd"
      setup.write_text(f'@echo off\ncall "{vcvars}" >nul\nif errorlevel 1 exit /b 1\nset\n', encoding="utf-8")
      configured = subprocess.run(["cmd", "/d", "/u", "/c", str(setup)], capture_output=True, timeout=30)
      assert configured.returncode == 0, configured.stderr.decode("utf-16-le", errors="replace")
      settings = configured.stdout.decode("utf-16-le")
      environment.update(line.split("=", 1) for line in settings.splitlines() if "=" in line and not line.startswith("="))
    library_path = directory / "converter.dll"
    command = [compiler, "/nologo", "/LD", "/O2", "/W4", "/WX", "/std:c++17", str(source),
               f"/Fo{directory / 'converter.obj'}", f"/Fe{library_path}"]
  else:
    compiler = shutil.which("c++")
    if compiler is None:
      pytest.skip("A C++ compiler is needed to test the native converter")
    library_path = directory / "converter.so"
    command = [compiler, "-std=c++17", "-O3", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", str(source), "-o", str(library_path)]
  result = subprocess.run(command, cwd=directory, env=environment, capture_output=True, text=True, errors="replace", timeout=30)
  assert result.returncode == 0, result.stdout + result.stderr
  library = ctypes.CDLL(str(library_path))
  function = library.cluster_h264_encoder_bridge_convert_rgb
  function.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_size_t,
                       ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
  function.restype = ctypes.c_int
  return SimpleNamespace(library=library, function=function)


@pytest.mark.parametrize("rotate", [False, True])
@pytest.mark.parametrize("width,height", [(2, 2), (16, 8), (30, 22), (1920, 462), (1920, 480)])
@pytest.mark.parametrize("pattern", ["random", "edges"])
def test_native_nv12_is_byte_identical_to_opencv_including_chroma_phase_and_padding(native_rgb, rotate, width, height, pattern):
  rgb = np.random.default_rng(123).integers(0, 256, (height, width, 3), dtype=np.uint8)
  if pattern == "edges":
    rgb[::2, ::2] = (255, 0, 0)
    rgb[1::2, ::2] = (0, 255, 0)
    rgb[::2, 1::2] = (0, 0, 255)
    rgb[1::2, 1::2] = (255, 255, 255)
  expected = h264_encoder.portrait_nv12(rgb, width, height, rotate)
  # Padded source rows and sentinel bytes also exercise real stride arithmetic.
  source = np.full((height, width * 3 + 13), 0xA7, dtype=np.uint8)
  source[:, :width * 3] = rgb.reshape(height, width * 3)
  original = source.copy()
  output = np.full(expected.size + 32, 0xD3, dtype=np.uint8)
  assert native_rgb.function(source.ctypes.data, source.nbytes, width, height, source.strides[0], int(rotate),
                             output.ctypes.data + 16, expected.nbytes) == 0
  np.testing.assert_array_equal(output[16:-16], expected)
  np.testing.assert_array_equal(output[:16], 0xD3)
  np.testing.assert_array_equal(output[-16:], 0xD3)
  np.testing.assert_array_equal(source, original)


@pytest.mark.parametrize("invalid", [
  "null_source", "null_output", "odd_width", "odd_height", "zero_width", "large_height",
  "short_stride", "large_stride", "short_source", "short_output", "invalid_rotation", "overlap",
])
def test_invalid_native_inputs_are_rejected_before_writing(native_rgb, invalid):
  source = np.zeros((8, 16, 3), dtype=np.uint8)
  output = np.full(16 * 16 * 3 // 2, 0xD3, dtype=np.uint8)
  arguments = [source.ctypes.data, source.nbytes, 16, 8, source.strides[0], 0, output.ctypes.data, output.nbytes]
  changes = {
    "null_source": (0, None), "null_output": (6, None), "odd_width": (2, 15), "odd_height": (3, 7),
    "zero_width": (2, 0), "large_height": (3, 4098), "short_stride": (4, 16 * 3 - 1),
    "large_stride": (4, 4096 * 4 + 1), "short_source": (1, source.nbytes - 1),
    "short_output": (7, output.nbytes - 1), "invalid_rotation": (5, 2), "overlap": (0, output.ctypes.data),
  }
  index, value = changes[invalid]
  arguments[index] = value
  assert native_rgb.function(*arguments) == -1
  np.testing.assert_array_equal(source, 0)
  np.testing.assert_array_equal(output, 0xD3)


def encoder_with_converter(monkeypatch, native_rgb):
  names = ("create", "open", "close", "destroy", "last_error", "encode_nv12_packed", "drain",
           "set_slice_max_bytes", "set_rate_control", "set_realtime_priority")
  functions = {name: Mock(return_value=0) for name in names}
  library = SimpleNamespace(**{"cluster_h264_encoder_bridge_" + name: function for name, function in functions.items()},
                            cluster_h264_encoder_bridge_convert_rgb=native_rgb.function)
  monkeypatch.setattr(h264_encoder, "flog", Mock())
  encoder = h264_encoder.HardwareH264Encoder(SimpleNamespace(width=30, height=22), library=library)
  return encoder


@pytest.mark.parametrize("kind", ["pil", "numpy", "strided", "readonly"])
def test_python_native_path_reuses_output_and_never_uses_opencv_conversion(native_rgb, monkeypatch, kind):
  encoder = encoder_with_converter(monkeypatch, native_rgb)
  image = np.random.default_rng(42).integers(0, 256, (22, 30, 3), dtype=np.uint8)
  expected = h264_encoder.portrait_nv12(image, 30, 22)
  if kind == "pil":
    image = Image.fromarray(image)
  if kind == "strided":
    storage = np.zeros((22, 60, 3), dtype=np.uint8)
    storage[:, ::2] = image
    image = storage[:, ::2]
  if kind == "readonly":
    image.setflags(write=False)
  monkeypatch.setattr(h264_encoder, "portrait_nv12", lambda *_args: pytest.fail("native conversion must bypass OpenCV"))
  first, first_metrics = encoder._to_nv12(image)
  second, second_metrics = encoder._to_nv12(image)
  assert first is second is encoder._packed
  np.testing.assert_array_equal(second, expected)
  for metrics in (first_metrics, second_metrics):
    assert metrics.backend == "native_tiled"
    assert metrics.cpu_elapsed >= 0
    assert metrics.elapsed == pytest.approx(metrics.rgb_export_elapsed + metrics.pixels_elapsed)


def test_native_converter_error_is_propagated_without_silently_switching_backends(native_rgb, monkeypatch):
  encoder = encoder_with_converter(monkeypatch, native_rgb)
  encoder._convert_rgb = Mock(return_value=-1)
  monkeypatch.setattr(h264_encoder, "portrait_nv12", lambda *_args: pytest.fail("must not mask a native conversion error"))
  with pytest.raises(RuntimeError, match="native RGB conversion rejected"):
    encoder._to_nv12(Image.new("RGB", (30, 22)))
