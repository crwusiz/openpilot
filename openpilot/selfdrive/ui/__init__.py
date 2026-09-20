from types import SimpleNamespace

import pyray as rl


UI_BORDER_SIZE = 30

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

def colors_alpha(color, alpha):
  if isinstance(color, tuple):
    return rl.Color(*color, alpha)
  return rl.Color(color.r, color.g, color.b, alpha)


class Colors:
  TRANSPARENT = rl.BLANK
  RED = rl.Color(201, 34, 49, 255)
  ORANGE = rl.Color(255, 149, 0, 255)
  STEERING = rl.Color(0, 191, 255, 255)
  ENGAGED = rl.Color(128, 216, 166, 255)
  DISENGAGED = rl.Color(145, 155, 149, 255)
  OVERRIDE = DISENGAGED

  GRAY = rl.Color(84, 84, 84, 255)
  LIGHT_GRAY = rl.Color(170, 170, 170, 255)
  MEDIUM_GRAY = rl.GRAY
  DARK_RED = rl.Color(139, 0, 0, 255)
  LIGHT_RED = rl.Color(255, 100, 100, 150)
  LIME = rl.Color(120, 255, 120, 255)
  LIGHT_ORANGE = rl.Color(255, 228, 191, 255)
  AMBER = rl.Color(255, 200, 100, 255)
  PRIMARY_BLUE = rl.Color(70, 91, 234, 255)

  WARNING = rl.Color(218, 202, 37, 255)
  SUBSCRIBED = rl.Color(134, 255, 78, 255)

  DARK_PANEL = rl.Color(51, 51, 51, 255)

  DRIVER_ACTIVE = rl.Color(26, 242, 66, 255)
  DRIVER_INACTIVE = rl.Color(139, 139, 139, 255)

  UPDATE_ACTIVE = rl.Color(75, 95, 255, 255)
  UPDATE_INACTIVE = rl.Color(54, 77, 239, 255)

  ALERT_ACTIVE = rl.Color(255, 70, 70, 255)
  ALERT_INACTIVE = rl.Color(226, 44, 44, 255)

  EXPERIMENTAL_START = rl.Color(255, 155, 63, 255)
  EXPERIMENTAL_END = rl.Color(219, 56, 34, 255)

  CHILL_START = rl.Color(20, 255, 171, 255)
  CHILL_END = rl.Color(35, 149, 255, 255)

  DEBUG_BORDER = rl.Color(100, 100, 100, 255)
  DEBUG_VALID = rl.Color(0, 255, 0, 255)

  Border = SimpleNamespace(
    DISENGAGED=rl.Color(18, 40, 57, 255),
    OVERRIDE=rl.Color(137, 146, 141, 255),
    ENGAGED=rl.Color(22, 127, 64, 255),
    RED=RED,
    STEERING=STEERING,
    BLINKER=ORANGE,
    ACTIVE=rl.Color(111, 192, 201, 255),
    READY=rl.Color(143, 201, 192, 255),
  )

  OnroadAlert = SimpleNamespace(
    NORMAL=rl.Color(21, 21, 21, 100),
    USER_PROMPT=rl.Color(218, 111, 37, 100),
    CRITICAL=colors_alpha(RED, 100),
  )

  MiciOnroadAlert = SimpleNamespace(
    NORMAL=colors_alpha(rl.BLACK, 100),
    USER_PROMPT=rl.Color(255, 115, 0, 100),
    CRITICAL=rl.Color(255, 0, 21, 100),
  )

  OffroadAlert = SimpleNamespace(
    HIGH_SEVERITY=ALERT_INACTIVE,
    LOW_SEVERITY=rl.Color(41, 41, 41, 255),
    BACKGROUND=rl.Color(57, 57, 57, 255),
    TEXT=rl.WHITE,
    BUTTON=rl.WHITE,
    BUTTON_PRESSED=rl.LIGHTGRAY,
    BUTTON_TEXT=rl.BLACK,
    SNOOZE_BG=rl.Color(79, 79, 79, 255),
    SNOOZE_BG_PRESSED=rl.Color(100, 100, 100, 255),
  )

  Settings = SimpleNamespace(
    PANEL=OffroadAlert.LOW_SEVERITY,
    CLOSE_BUTTON=OffroadAlert.LOW_SEVERITY,
    CLOSE_BUTTON_PRESSED=rl.Color(59, 59, 59, 255),
    TEXT_NORMAL=rl.Color(128, 128, 128, 255),
    ICON_PRESSED=rl.Color(220, 220, 220, 255),
  )

  Esim = SimpleNamespace(
    SUB_LABEL_DISABLED=colors_alpha(rl.WHITE, int(255 * 0.585)),
    CHECK_ICON=colors_alpha(rl.WHITE, int(255 * 0.585)),
    LABEL=colors_alpha(rl.WHITE, int(255 * 0.9)),
    DELETE=rl.Color(255, 105, 115, 255),
  )

  Firehose = SimpleNamespace(
    GREEN=rl.Color(46, 204, 113, 255),
    RED=rl.Color(231, 76, 60, 255),
    GRAY=rl.Color(68, 68, 68, 255),
    LIGHT_GRAY=rl.Color(228, 228, 228, 255),
  )

  ConfidenceBall = SimpleNamespace(
    ACTIVE_TOP=rl.Color(0, 255, 204, 255),
    ACTIVE_BOTTOM=rl.Color(0, 255, 38, 255),
    WARNING_TOP=rl.Color(255, 200, 0, 255),
    WARNING_BOTTOM=rl.Color(255, 115, 0, 255),
    CRITICAL_TOP=rl.Color(255, 0, 21, 255),
    CRITICAL_BOTTOM=rl.Color(255, 0, 89, 255),
    INACTIVE_TOP=rl.WHITE,
    INACTIVE_BOTTOM=rl.Color(82, 82, 82, 255),
    HIDDEN_TOP=rl.Color(50, 50, 50, 255),
    HIDDEN_BOTTOM=rl.Color(13, 13, 13, 255),
  )

  Pairing = SimpleNamespace(
    BACKGROUND=rl.Color(224, 224, 224, 255),
    DOT=rl.Color(70, 70, 70, 255),
    CARD=rl.Color(240, 240, 240, 255),
  )

  Community = SimpleNamespace(
    ENABLED=rl.Color(44, 44, 226, 255),
    DISABLED=rl.Color(60, 60, 60, 255),
    UNAVAILABLE=rl.Color(40, 40, 40, 255),
  )

  Watch3 = SimpleNamespace(
    BACKGROUND=rl.Color(12, 16, 22, 255),
    PANEL=rl.Color(22, 28, 36, 255),
    VIDEO=rl.Color(8, 11, 16, 255),
    BORDER=rl.Color(43, 53, 65, 255),
    TEXT=rl.Color(238, 243, 249, 255),
    TEXT_MUTED=rl.Color(145, 160, 179, 255),
    ACCENT=rl.Color(113, 195, 225, 255),
    CLOSE_HOVER=rl.Color(46, 58, 73, 255),
  )

  Network = SimpleNamespace(
    NAV_BUTTON=rl.Color(57, 57, 57, 255),
    NAV_BUTTON_PRESSED=rl.Color(74, 74, 74, 255),
  )

  Toggle = SimpleNamespace(
    ON=rl.Color(51, 171, 76, 255),
    OFF=RED,
    KNOB=rl.WHITE,
    DISABLED_ON=rl.Color(34, 119, 34, 255),  # Dark green when disabled + on
    DISABLED_OFF=rl.Color(57, 57, 57, 255),
    DISABLED_KNOB=rl.Color(136, 136, 136, 255),
  )

  THROTTLE = [
    rl.Color(13, 248, 122, 102),
    rl.Color(114, 255, 92, 89),
    rl.Color(114, 255, 92, 0),
  ]

  NO_THROTTLE = [
    rl.Color(242, 242, 242, 102),
    rl.Color(242, 242, 242, 89),
    rl.Color(242, 242, 242, 0),
  ]

  STEERING_PRESSED = [
    rl.Color(0, 191, 255, 102),
    rl.Color(0, 191, 255, 89),
    rl.Color(0, 191, 255, 0),
  ]
