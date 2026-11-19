"""Color values and conversions shared by the on-device UI and cluster."""

import colorsys
from types import SimpleNamespace


RGBColor = tuple[int, int, int]
RGBAColor = tuple[int, int, int, int]


def colors_alpha(color: RGBColor | RGBAColor, alpha: int = 255) -> RGBAColor:
  return (*color[:3], alpha)


def hsla_to_rgba(hue: float, saturation: float, lightness: float, alpha: float) -> RGBAColor:
  red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
  return int(red * 255), int(green * 255), int(blue * 255), int(alpha * 255)


STOCK_PATH_COLORS = [(13, 248, 122, 102), (114, 255, 92, 89), (114, 255, 92, 0)]


class Colors:
  BLACK = (0, 0, 0, 255)
  WHITE = (255, 255, 255, 255)
  RED = (201, 34, 49, 255)
  ORANGE = (255, 149, 0, 255)
  STEERING = (0, 191, 255, 255)
  ENGAGED = (128, 216, 166, 255)
  DISENGAGED = (145, 155, 149, 255)
  OVERRIDE = DISENGAGED

  GRAY = (84, 84, 84, 255)
  LIGHT_GRAY = (170, 170, 170, 255)
  MEDIUM_GRAY = (130, 130, 130, 255)
  DARK_RED = (139, 0, 0, 255)
  LIME = (120, 255, 120, 255)
  LIGHT_ORANGE = (255, 228, 191, 255)
  AMBER = (255, 200, 100, 255)
  PRIMARY_BLUE = (70, 91, 234, 255)

  WARNING = (218, 202, 37, 255)
  SUBSCRIBED = (134, 255, 78, 255)

  DARK_PANEL = (51, 51, 51, 255)

  DRIVER_ACTIVE = (26, 242, 66, 255)
  DRIVER_INACTIVE = (139, 139, 139, 255)

  UPDATE_ACTIVE = (75, 95, 255, 255)
  UPDATE_INACTIVE = (54, 77, 239, 255)

  ALERT_ACTIVE = (255, 70, 70, 255)
  ALERT_INACTIVE = (226, 44, 44, 255)

  CHILL_START = (20, 255, 171, 255)
  CHILL_END = (35, 149, 255, 255)

  DEBUG_BORDER = (100, 100, 100, 255)
  DEBUG_VALID = (0, 255, 0, 255)

  Border = SimpleNamespace(
    DISENGAGED=(18, 40, 57, 255),
    OVERRIDE=(137, 146, 141, 255),
    ENGAGED=(22, 127, 64, 255),
    RED=RED,
    STEERING=STEERING,
    BLINKER=ORANGE,
    ACTIVE=(111, 192, 201, 255),
    READY=(143, 201, 192, 255),
  )

  OnroadAlert = SimpleNamespace(
    NORMAL=(21, 21, 21, 100),
    USER_PROMPT=(218, 111, 37, 100),
    CRITICAL=colors_alpha(RED, 100),
  )

  MiciOnroadAlert = SimpleNamespace(
    NORMAL=colors_alpha(BLACK, 100),
    USER_PROMPT=(255, 115, 0, 100),
    CRITICAL=(255, 0, 21, 100),
  )

  OffroadAlert = SimpleNamespace(
    HIGH_SEVERITY=ALERT_INACTIVE,
    LOW_SEVERITY=(41, 41, 41, 255),
    BACKGROUND=(57, 57, 57, 255),
    TEXT=WHITE,
    BUTTON=WHITE,
    BUTTON_PRESSED=(200, 200, 200, 255),
    BUTTON_TEXT=BLACK,
    SNOOZE_BG=(79, 79, 79, 255),
    SNOOZE_BG_PRESSED=(100, 100, 100, 255),
  )

  Settings = SimpleNamespace(
    PANEL=OffroadAlert.LOW_SEVERITY,
    CLOSE_BUTTON=OffroadAlert.LOW_SEVERITY,
    CLOSE_BUTTON_PRESSED=(59, 59, 59, 255),
    TEXT_NORMAL=(128, 128, 128, 255),
    ICON_PRESSED=(220, 220, 220, 255),
  )

  Esim = SimpleNamespace(
    SUB_LABEL_DISABLED=colors_alpha(WHITE, int(255 * 0.585)),
    CHECK_ICON=colors_alpha(WHITE, int(255 * 0.585)),
    LABEL=colors_alpha(WHITE, int(255 * 0.9)),
    DELETE=(255, 105, 115, 255),
  )

  Firehose = SimpleNamespace(
    GREEN=(46, 204, 113, 255),
    RED=(231, 76, 60, 255),
    GRAY=(68, 68, 68, 255),
    LIGHT_GRAY=(228, 228, 228, 255),
  )

  Pairing = SimpleNamespace(
    BACKGROUND=(224, 224, 224, 255),
    DOT=(70, 70, 70, 255),
    CARD=(240, 240, 240, 255),
  )

  Community = SimpleNamespace(
    ENABLED=(44, 44, 226, 255),
    DISABLED=(60, 60, 60, 255),
    UNAVAILABLE=(40, 40, 40, 255),
  )

  Watch3 = SimpleNamespace(
    BACKGROUND=(12, 16, 22, 255),
    PANEL=(22, 28, 36, 255),
    VIDEO=(8, 11, 16, 255),
    BORDER=(43, 53, 65, 255),
    TEXT=(238, 243, 249, 255),
    TEXT_MUTED=(145, 160, 179, 255),
    ACCENT=(113, 195, 225, 255),
    CLOSE_HOVER=(46, 58, 73, 255),
  )

  Network = SimpleNamespace(
    NAV_BUTTON=(57, 57, 57, 255),
    NAV_BUTTON_PRESSED=(74, 74, 74, 255),
  )

  Toggle = SimpleNamespace(
    ON=(51, 171, 76, 255),
    OFF=RED,
    KNOB=WHITE,
    DISABLED_ON=(34, 119, 34, 255),  # Dark green when disabled + on
    DISABLED_OFF=(57, 57, 57, 255),
    DISABLED_KNOB=(136, 136, 136, 255),
  )

  THROTTLE = STOCK_PATH_COLORS
  NO_THROTTLE = [(242, 242, 242, 102), (242, 242, 242, 89), (242, 242, 242, 0)]
  STEERING_PRESSED = [colors_alpha(STEERING, 102), colors_alpha(STEERING, 89), colors_alpha(STEERING, 0)]


class ClusterColors(Colors):
  PANEL = (7, 12, 18, 255)
  DIVIDER = (42, 54, 68, 255)
  MUTED_TEXT = (120, 132, 148, 255)
  SIGN_TEXT = (20, 25, 30, 255)
  DISTANCE_BADGE = (18, 25, 34, 255)
  MAX_ACTIVE = Colors.ENGAGED
  CAUTION = Colors.AMBER


def make_ui_colors(color_factory):
  """Materialize the shared palette in the UI's native color type."""
  def convert(value):
    if isinstance(value, SimpleNamespace):
      return SimpleNamespace(**{name: convert(color) for name, color in vars(value).items()})
    if isinstance(value, list):
      return [convert(color) for color in value]
    return color_factory(*value)

  return type("Colors", (), {name: convert(value) for name, value in vars(Colors).items() if not name.startswith("_")})


"""
// Some Basic Colors
// NOTE: Custom raylib color palette for amazing visuals on WHITE background
#define LIGHTGRAY  CLITERAL(Color){ 200, 200, 200, 255 }   // Light Gray
#define GRAY       CLITERAL(Color){ 130, 130, 130, 255 }   // Gray
#define DARKGRAY   CLITERAL(Color){ 80, 80, 80, 255 }      // Dark Gray
#define YELLOW     CLITERAL(Color){ 253, 249, 0, 255 }     // Yellow
#define GOLD       CLITERAL(Color){ 255, 203, 0, 255 }     // Gold
#define ORANGE     CLITERAL(Color){ 255, 161, 0, 255 }     // Orange
#define PINK       CLITERAL(Color){ 255, 109, 194, 255 }   // Pink
#define RED        CLITERAL(Color){ 230, 41, 55, 255 }     // Red
#define MAROON     CLITERAL(Color){ 190, 33, 55, 255 }     // Maroon
#define GREEN      CLITERAL(Color){ 0, 228, 48, 255 }      // Green
#define LIME       CLITERAL(Color){ 0, 158, 47, 255 }      // Lime
#define DARKGREEN  CLITERAL(Color){ 0, 117, 44, 255 }      // Dark Green
#define SKYBLUE    CLITERAL(Color){ 102, 191, 255, 255 }   // Sky Blue
#define BLUE       CLITERAL(Color){ 0, 121, 241, 255 }     // Blue
#define DARKBLUE   CLITERAL(Color){ 0, 82, 172, 255 }      // Dark Blue
#define PURPLE     CLITERAL(Color){ 200, 122, 255, 255 }   // Purple
#define VIOLET     CLITERAL(Color){ 135, 60, 190, 255 }    // Violet
#define DARKPURPLE CLITERAL(Color){ 112, 31, 126, 255 }    // Dark Purple
#define BEIGE      CLITERAL(Color){ 211, 176, 131, 255 }   // Beige
#define BROWN      CLITERAL(Color){ 127, 106, 79, 255 }    // Brown
#define DARKBROWN  CLITERAL(Color){ 76, 63, 47, 255 }      // Dark Brown

#define WHITE      CLITERAL(Color){ 255, 255, 255, 255 }   // White
#define BLACK      CLITERAL(Color){ 0, 0, 0, 255 }         // Black
#define BLANK      CLITERAL(Color){ 0, 0, 0, 0 }           // Blank (Transparent)
#define MAGENTA    CLITERAL(Color){ 255, 0, 255, 255 }     // Magenta
#define RAYWHITE   CLITERAL(Color){ 245, 245, 245, 255 }   // My own White (raylib logo)
"""
