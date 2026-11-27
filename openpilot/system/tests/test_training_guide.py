"""Exercise the Tizi training preview without graphics or vehicle services."""

import importlib.util
from collections import namedtuple
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock


UI = Path(__file__).parents[2] / "selfdrive/ui"


def test_training_guide_loads_and_draws_without_onroad_renderers(monkeypatch):
  def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module

  color = namedtuple("Color", "r g b a")
  vector = namedtuple("Vector2", "x y")
  raylib = {
    "Color": color,
    "Rectangle": namedtuple("Rectangle", "x y width height"),
    "Vector2": vector,
    "Font": object,
    "Texture": object,
    "BLACK": color(0, 0, 0, 255),
    "WHITE": color(255, 255, 255, 255),
  }
  for name in ("rl_push_matrix", "rl_translatef", "rl_scalef", "draw_rectangle", "draw_texture_pro",
               "draw_rectangle_rounded", "draw_rectangle_rounded_lines_ex", "draw_text_ex", "rl_pop_matrix"):
    raylib[name] = Mock()

  dependencies = {
    "raylib_nb": raylib,
    "openpilot.selfdrive.ui.onroad.augmented_road_view": {},
    "openpilot.selfdrive.ui.onroad.hud_renderer": {},
    "openpilot.selfdrive.ui.ui_state": {"ui_state": Mock()},
    "openpilot.selfdrive.ui.onroad.alert_renderer": {
      "ALERT_BORDER_RADIUS": 30, "ALERT_COLORS": {1: color(218, 111, 37, 100)}, "ALERT_FONT_BIG": 88,
      "ALERT_FONT_SMALL": 66, "ALERT_HEIGHTS": {1: 420}, "ALERT_LINE_SPACING": 45,
      "ALERT_MARGIN": 40, "ALERT_PADDING": 60, "AlertSize": SimpleNamespace(mid=1),
      "AlertStatus": SimpleNamespace(userPrompt=1),
    },
    "openpilot.system.ui.lib.application": {
      "FontWeight": Mock(), "gui_app": SimpleNamespace(width=2160, height=1080, font=Mock()),
    },
    "openpilot.system.ui.lib.multilang": {"tr": str, "tr_noop": str},
    "openpilot.system.ui.lib.text_measure": {"measure_text_cached": Mock(return_value=vector(100, 40))},
    "openpilot.system.ui.widgets": {"Widget": object},
    "openpilot.system.ui.widgets.button": {"Button": object},
  }
  for name, attributes in dependencies.items():
    dependency = ModuleType(name)
    dependency.__dict__.update(attributes)
    monkeypatch.setitem(sys.modules, name, dependency)

  ui = load("openpilot.selfdrive.ui", UI / "__init__.py")
  guide = load("openpilot.selfdrive.ui.layouts.training_guide", UI / "layouts/training_guide.py")
  assert guide.TrainingGuide.__name__ == "TrainingGuide"

  guide.draw_distracted_screen(vector(0, 0), object())
  raylib["draw_rectangle"].assert_called_once_with(0, 0, 2160, 1080, ui.Colors.Border.ENGAGED)
  assert [(call.args[1], call.args[-1]) for call in raylib["draw_text_ex"].call_args_list[:4]] == [
    ("MAX", ui.Colors.ENGAGED), ("70", ui.Colors.WHITE),
    ("70", ui.Colors.WHITE), ("mph", ui.colors_alpha(ui.Colors.WHITE, 200)),
  ]
