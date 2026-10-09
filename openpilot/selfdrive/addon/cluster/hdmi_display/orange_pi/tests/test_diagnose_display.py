import ctypes as ct
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.addon.cluster.hdmi_display.orange_pi import diagnose_display as diagnostic


def _write_int(pointer, value):
  ct.cast(pointer, ct.POINTER(ct.c_int))[0] = value


def _native_libraries(calls, failure=None):
  def function(name, result):
    def invoke(*args):
      calls.append((name, args))
      return 0 if name == failure else result
    return invoke

  gbm = SimpleNamespace(**{name: function(name, result) for name, result in (
    ("gbm_create_device", 100), ("gbm_device_destroy", None),
    ("gbm_device_is_format_supported", 1), ("gbm_surface_create", 200), ("gbm_surface_destroy", None),
  )})
  egl = SimpleNamespace(**{name: function(name, result) for name, result in (
    ("eglGetDisplay", 300), ("eglGetPlatformDisplay", 300), ("eglGetError", 0x3009),
    ("eglInitialize", 1), ("eglQueryString", b"test vendor"), ("eglBindAPI", 1),
    ("eglCreateWindowSurface", 400), ("eglCreateContext", 500), ("eglMakeCurrent", 1),
    ("eglDestroyContext", 1), ("eglDestroySurface", 1), ("eglTerminate", 1),
  )})

  def get_configs(display, configs, size, count):
    _write_int(count, 3)
    if configs is not None:
      configs[:] = [10, 11, 12]
    return 1

  def get_config_attribute(display, config, attribute, pointer):
    # Only ES2 is supported; exclude one pbuffer-only and one wrong-format config.
    attributes = {
      10: {diagnostic.EGL_NATIVE_VISUAL_ID: diagnostic.GBM_FORMAT_ARGB8888,
           diagnostic.EGL_SURFACE_TYPE: diagnostic.EGL_WINDOW_BIT, diagnostic.EGL_RENDERABLE_TYPE: diagnostic.EGL_OPENGL_ES2_BIT},
      11: {diagnostic.EGL_NATIVE_VISUAL_ID: diagnostic.GBM_FORMAT_ARGB8888,
           diagnostic.EGL_SURFACE_TYPE: 1, diagnostic.EGL_RENDERABLE_TYPE: diagnostic.EGL_OPENGL_BIT},
      12: {diagnostic.EGL_NATIVE_VISUAL_ID: 0, diagnostic.EGL_SURFACE_TYPE: diagnostic.EGL_WINDOW_BIT,
           diagnostic.EGL_RENDERABLE_TYPE: diagnostic.EGL_OPENGL_ES2_BIT},
    }
    _write_int(pointer, attributes[config][attribute])
    return 1

  egl.eglGetConfigs = get_configs
  egl.eglGetConfigAttrib = get_config_attribute
  return gbm, egl


def _mock_native_access(monkeypatch, calls, failure=None):
  gbm, egl = _native_libraries(calls, failure)
  monkeypatch.setattr(diagnostic.ct, "CDLL", lambda name, **kwargs: gbm if "gbm" in name else egl)
  monkeypatch.setattr(diagnostic.os, "O_CLOEXEC", 0, raising=False)
  monkeypatch.setattr(diagnostic.os, "open", lambda path, flags: 9)
  monkeypatch.setattr(diagnostic.os, "close", lambda fd: calls.append(("close", (fd,))))
  return gbm, egl


def test_probe_uses_scanout_surface_and_releases_resources_in_order(monkeypatch, capsys):
  calls = []
  _mock_native_access(monkeypatch, calls)
  diagnostic.probe_gbm_egl(0, 480, 1920)

  assert ("gbm_surface_create", (100, 480, 1920, diagnostic.GBM_FORMAT_ARGB8888, 5)) in calls
  assert ("eglCreateWindowSurface", (300, 10, 200, None)) in calls
  assert calls[-7:] == [
    ("eglMakeCurrent", (300, None, None, None)), ("eglDestroyContext", (300, 500)),
    ("eglDestroySurface", (300, 400)), ("eglTerminate", (300,)),
    ("gbm_surface_destroy", (200,)), ("gbm_device_destroy", (100,)), ("close", (9,)),
  ]
  assert "OpenGL=0, OpenGL ES2=1" in capsys.readouterr().out


@pytest.mark.parametrize("failure, expected_cleanup", [
  ("gbm_create_device", ["close"]),
  ("eglInitialize", ["gbm_device_destroy", "close"]),
  ("gbm_surface_create", ["eglTerminate", "gbm_device_destroy", "close"]),
  ("eglCreateWindowSurface", ["eglTerminate", "gbm_surface_destroy", "gbm_device_destroy", "close"]),
  ("eglCreateContext", ["eglDestroySurface", "eglTerminate", "gbm_surface_destroy", "gbm_device_destroy", "close"]),
  ("eglMakeCurrent", ["eglMakeCurrent", "eglDestroyContext", "eglDestroySurface", "eglTerminate", "gbm_surface_destroy", "gbm_device_destroy", "close"]),
])
def test_probe_failure_identifies_stage_and_cleans_up(monkeypatch, failure, expected_cleanup):
  calls = []
  _mock_native_access(monkeypatch, calls, failure)

  with pytest.raises(RuntimeError, match=failure):
    diagnostic.probe_gbm_egl(0, 480, 1920)
  assert [name for name, args in calls[-len(expected_cleanup):]] == expected_cleanup
  if failure.startswith("egl"):
    # A driver error is reported before cleanup overwrites the EGL error state.
    error_calls = [args for name, args in calls if name == "eglGetError"]
    assert error_calls == [()]
