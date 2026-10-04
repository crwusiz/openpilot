#!/usr/bin/env python3
"""SDL metadata and an optional GBM/EGL allocation probe without a modeset."""
import argparse
import ctypes as ct
import os
import sys

try:
  from .hdmi_display import get_sdl_video_drivers
except ImportError:
  from hdmi_display import get_sdl_video_drivers


GBM_FORMAT_ARGB8888 = int.from_bytes(b"AR24", "little")
GBM_BO_USE_SCANOUT = 1 << 0
GBM_BO_USE_RENDERING = 1 << 2
EGL_PLATFORM_GBM = 0x31D7
EGL_NONE = 0x3038
EGL_NATIVE_VISUAL_ID = 0x302E
EGL_SURFACE_TYPE = 0x3033
EGL_RENDERABLE_TYPE = 0x3040
EGL_WINDOW_BIT = 0x0004
EGL_OPENGL_BIT = 0x0008
EGL_OPENGL_ES2_BIT = 0x0004
EGL_OPENGL_ES_API = 0x30A0
EGL_CONTEXT_CLIENT_VERSION = 0x3098


def _bind(library, name, result_type, *argument_types):
  function = getattr(library, name)
  function.restype = result_type
  function.argtypes = list(argument_types)
  return function


def _check_egl(egl, result, stage):
  if not result:
    error = egl.eglGetError()
    raise RuntimeError(f"{stage}: EGL error=0x{error:04x}")
  return result


def _check_gbm(result, stage):
  if not result:
    error = ct.get_errno()
    detail = os.strerror(error) if error else "driver returned NULL without errno"
    raise RuntimeError(f"{stage}: {detail} (errno={error})")
  return result


def _matching_configs(egl, display):
  count = ct.c_int()
  _check_egl(egl, egl.eglGetConfigs(display, None, 0, ct.byref(count)), "eglGetConfigs(count)")
  configs = (ct.c_void_p * count.value)()
  _check_egl(egl, egl.eglGetConfigs(display, configs, len(configs), ct.byref(count)), "eglGetConfigs")
  matches = {"OpenGL": [], "OpenGL ES2": []}
  for config in configs[:count.value]:
    attributes = {}
    for attribute in (EGL_NATIVE_VISUAL_ID, EGL_SURFACE_TYPE, EGL_RENDERABLE_TYPE):
      value = ct.c_int()
      _check_egl(egl, egl.eglGetConfigAttrib(display, config, attribute, ct.byref(value)), "eglGetConfigAttrib")
      attributes[attribute] = value.value
    if attributes[EGL_NATIVE_VISUAL_ID] != GBM_FORMAT_ARGB8888 or not attributes[EGL_SURFACE_TYPE] & EGL_WINDOW_BIT:
      continue
    for name, bit in (("OpenGL", EGL_OPENGL_BIT), ("OpenGL ES2", EGL_OPENGL_ES2_BIT)):
      if attributes[EGL_RENDERABLE_TYPE] & bit:
        matches[name].append(config)
  print(f"EGL configs: total={count.value}, ARGB8888 window OpenGL={len(matches['OpenGL'])}, OpenGL ES2={len(matches['OpenGL ES2'])}")
  return matches


def probe_gbm_egl(card, width, height):
  """Match SDL's ARGB8888 scanout/rendering surface, then make an ES2 context."""
  gbm = ct.CDLL("libgbm.so.1", use_errno=True)
  egl = ct.CDLL("libEGL.so.1")
  pointer, integer, uint = ct.c_void_p, ct.c_int, ct.c_uint
  _bind(gbm, "gbm_create_device", pointer, integer)
  _bind(gbm, "gbm_device_destroy", None, pointer)
  _bind(gbm, "gbm_device_is_format_supported", integer, pointer, uint, uint)
  _bind(gbm, "gbm_surface_create", pointer, pointer, uint, uint, uint, uint)
  _bind(gbm, "gbm_surface_destroy", None, pointer)
  _bind(egl, "eglGetError", integer)
  _bind(egl, "eglGetDisplay", pointer, pointer)
  _bind(egl, "eglInitialize", uint, pointer, ct.POINTER(integer), ct.POINTER(integer))
  _bind(egl, "eglQueryString", ct.c_char_p, pointer, integer)
  _bind(egl, "eglGetConfigs", uint, pointer, ct.POINTER(pointer), integer, ct.POINTER(integer))
  _bind(egl, "eglGetConfigAttrib", uint, pointer, pointer, integer, ct.POINTER(integer))
  _bind(egl, "eglBindAPI", uint, uint)
  _bind(egl, "eglCreateWindowSurface", pointer, pointer, pointer, pointer, ct.POINTER(integer))
  _bind(egl, "eglCreateContext", pointer, pointer, pointer, pointer, ct.POINTER(integer))
  _bind(egl, "eglMakeCurrent", uint, pointer, pointer, pointer, pointer)
  _bind(egl, "eglDestroyContext", uint, pointer, pointer)
  _bind(egl, "eglDestroySurface", uint, pointer, pointer)
  _bind(egl, "eglTerminate", uint, pointer)

  fd = os.open(f"/dev/dri/card{card}", os.O_RDWR | os.O_CLOEXEC)
  device = display = surface = window = context = None
  initialized = False
  try:
    print(f"GBM/EGL probe: card{card}, {width}x{height}, ARGB8888, SCANOUT | RENDERING")
    ct.set_errno(0)
    device = _check_gbm(gbm.gbm_create_device(fd), "gbm_create_device")
    if hasattr(gbm, "gbm_device_get_backend_name"):
      backend = _bind(gbm, "gbm_device_get_backend_name", ct.c_char_p, pointer)(device)
      print("GBM backend:", backend.decode() if backend else "unknown")
    if hasattr(egl, "eglGetPlatformDisplay"):
      get_display = _bind(egl, "eglGetPlatformDisplay", pointer, uint, pointer, pointer)
      display = get_display(EGL_PLATFORM_GBM, device, None)
    else:
      get_proc = _bind(egl, "eglGetProcAddress", pointer, ct.c_char_p)
      address = get_proc(b"eglGetPlatformDisplayEXT")
      if address:
        display = ct.CFUNCTYPE(pointer, uint, pointer, ct.POINTER(integer))(address)(EGL_PLATFORM_GBM, device, None)
      else:
        display = egl.eglGetDisplay(device)
    _check_egl(egl, display, "eglGetPlatformDisplay(GBM) / eglGetDisplay")
    major, minor = integer(), integer()
    _check_egl(egl, egl.eglInitialize(display, ct.byref(major), ct.byref(minor)), "eglInitialize")
    initialized = True
    print(f"EGL initialized: {major.value}.{minor.value}")
    for name, attribute in (("vendor", 0x3053), ("version", 0x3054), ("client APIs", 0x308D)):
      value = egl.eglQueryString(display, attribute)
      print(f"EGL {name}:", value.decode() if value else "unknown")
    configs = _matching_configs(egl, display)
    if not configs["OpenGL ES2"]:
      raise RuntimeError("No ARGB8888 EGL window config supports OpenGL ES2 on this DRM card")
    flags = GBM_BO_USE_SCANOUT | GBM_BO_USE_RENDERING
    print("GBM ARGB8888 scanout/rendering supported:", bool(gbm.gbm_device_is_format_supported(device, GBM_FORMAT_ARGB8888, flags)))
    ct.set_errno(0)
    surface = _check_gbm(gbm.gbm_surface_create(device, width, height, GBM_FORMAT_ARGB8888, flags), "gbm_surface_create")
    print("gbm_surface_create: OK")
    _check_egl(egl, egl.eglBindAPI(EGL_OPENGL_ES_API), "eglBindAPI(OpenGL ES)")
    config = configs["OpenGL ES2"][0]
    window = _check_egl(egl, egl.eglCreateWindowSurface(display, config, surface, None), "eglCreateWindowSurface")
    print("eglCreateWindowSurface: OK")
    attributes = (integer * 3)(EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE)
    context = _check_egl(egl, egl.eglCreateContext(display, config, None, attributes), "eglCreateContext(ES2)")
    _check_egl(egl, egl.eglMakeCurrent(display, window, window, context), "eglMakeCurrent")
    print("GBM/EGL ES2 probe OK (allocation/context only; scanout and page flips are untested)")
  finally:
    if initialized:
      if context:
        egl.eglMakeCurrent(display, None, None, None)
        egl.eglDestroyContext(display, context)
      if window:
        egl.eglDestroySurface(display, window)
      egl.eglTerminate(display)
    if surface:
      gbm.gbm_surface_destroy(surface)
    if device:
      gbm.gbm_device_destroy(device)
    os.close(fd)


def print_metadata():
  print("Python:", sys.executable, sys.version)
  try:
    import pygame
    import pygame.base
    print("pygame:", pygame.__file__)
    print("pygame version:", pygame.version.ver)
    print("SDL:", pygame.get_sdl_version())
    print("Compiled video drivers:", get_sdl_video_drivers(pygame))
  except ImportError as error:
    print("pygame import FAILED:", error)
  for library in ("libdrm.so.2", "libgbm.so.1", "libEGL.so.1", "libGLESv2.so.2"):
    try:
      ct.CDLL(library)
      print(library, "load OK")
    except OSError as error:
      print(library, "load FAILED:", error)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--egl", action="store_true", help="Probe GBM/EGL without setting a display mode")
  parser.add_argument("--card", type=int, default=0)
  parser.add_argument("--width", type=int, default=480)
  parser.add_argument("--height", type=int, default=1920)
  args = parser.parse_args()
  if args.card < 0 or args.width <= 0 or args.height <= 0:
    parser.error("card must be nonnegative and width/height must be positive")
  print_metadata()
  if args.egl:
    try:
      probe_gbm_egl(args.card, args.width, args.height)
    except (OSError, AttributeError, RuntimeError) as error:
      print("GBM/EGL probe FAILED:", error, flush=True)
      return 1
  return 0


if __name__ == "__main__":
  sys.exit(main())
