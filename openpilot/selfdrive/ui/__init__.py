import pyray as rl


UI_BORDER_SIZE = 30


def colors_alpha(color, alpha):
  if isinstance(color, tuple):
    return rl.Color(*color, alpha)
  return rl.Color(color.r, color.g, color.b, alpha)


class Colors:
  BLACK = rl.BLACK
  WHITE = rl.WHITE
  TRANSPARENT = rl.BLANK
  RED = rl.Color(201, 34, 49, 255)
  ORANGE = rl.Color(255, 149, 0, 255)
  GREEN = rl.Color(128, 216, 166, 255)
  STEERING = rl.Color(0, 191, 255, 255)
  DISENGAGED = rl.Color(145, 155, 149, 255)
  OVERRIDE = DISENGAGED

  GRAY = rl.Color(84, 84, 84, 255)
  LIGHT_GRAY = rl.Color(170, 170, 170, 255)
  MEDIUM_GRAY = rl.Color(130, 130, 130, 255)
  DARK_RED = rl.Color(139, 0, 0, 255)
  LIGHT_RED = rl.Color(255, 100, 100, 150)
  LIME = rl.Color(120, 255, 120, 255)
  LIGHT_ORANGE = rl.Color(255, 228, 191, 255)
  AMBER = rl.Color(255, 200, 100, 255)
  WARNING = rl.Color(218, 202, 37, 255)
  PRIMARY_BLUE = rl.Color(70, 91, 234, 255)
  SUBSCRIBED_GREEN = rl.Color(134, 255, 78, 255)
  DARK_PANEL = rl.Color(51, 51, 51, 255)

  DRIVER_ACTIVE = rl.Color(26, 242, 66, 255)
  DRIVER_INACTIVE = rl.Color(139, 139, 139, 255)
  MICI_DRIVER_ACTIVE = rl.Color(0, 255, 64, 255)
  MICI_DRIVER_INACTIVE = rl.Color(166, 166, 166, 255)

  HOME_UPDATE_ACTIVE = rl.Color(75, 95, 255, 255)
  HOME_UPDATE_INACTIVE = rl.Color(54, 77, 239, 255)
  HOME_ALERT_ACTIVE = rl.Color(255, 70, 70, 255)
  HOME_ALERT_INACTIVE = rl.Color(226, 44, 44, 255)

  EXPERIMENTAL_START = rl.Color(255, 155, 63, 255)
  EXPERIMENTAL_END = rl.Color(219, 56, 34, 255)
  CHILL_START = rl.Color(20, 255, 171, 255)
  CHILL_END = rl.Color(35, 149, 255, 255)

  BORDER_DISENGAGED = rl.Color(18, 40, 57, 255)
  BORDER_OVERRIDE = rl.Color(137, 146, 141, 255)
  BORDER_ENGAGED = rl.Color(22, 127, 64, 255)
  BORDER_ACTIVE = rl.Color(111, 192, 201, 255)
  BORDER_READY = rl.Color(143, 201, 192, 255)

  BLACK_TRANSLUCENT = colors_alpha(BLACK, 166)
  BODY_OVERLAY = colors_alpha(BLACK, 175)
  WHITE_TRANSLUCENT = colors_alpha(WHITE, 200)
  WHITE_DIM = colors_alpha(WHITE, 85)
  BORDER_TRANSLUCENT = colors_alpha(WHITE, 75)
  BOX_BG = colors_alpha(BLACK, 100)
  DISENGAGED_BG = colors_alpha(BLACK, 153)
  OVERRIDE_BG = colors_alpha(OVERRIDE, 204)
  ENGAGED_BG = colors_alpha(GREEN, 204)
  HEADER_GRADIENT_START = colors_alpha(BLACK, 114)
  HEADER_GRADIENT_END = TRANSPARENT
  ROAD_EDGE = colors_alpha(RED, 100)

  ENGAGED = GREEN
  DANGER = RED
  GOOD = WHITE
  METRIC_BORDER = WHITE_DIM
  BUTTON_NORMAL = WHITE
  BUTTON_PRESSED = colors_alpha(WHITE, 166)
  UP_TO_DATE = GREEN
  BSD = rl.Color(255, 0, 0, 100)
  WATCH_CLOSE_BUTTON = rl.Color(80, 80, 80, 200)
  DEBUG_BORDER = rl.Color(100, 100, 100, 255)
  DEBUG_VALID = rl.Color(0, 255, 0, 255)
  DEBUG_RED = rl.Color(230, 41, 55, 255)
  DEBUG_GREEN = rl.Color(0, 228, 48, 255)
  DEBUG_PURPLE = rl.Color(200, 122, 255, 255)
  ERROR = DEBUG_RED


class OnroadAlertColors:
  NORMAL = rl.Color(21, 21, 21, 100)
  USER_PROMPT = rl.Color(218, 111, 37, 100)
  CRITICAL = colors_alpha(Colors.RED, 100)


class MiciOnroadAlertColors:
  NORMAL = Colors.BOX_BG
  USER_PROMPT = rl.Color(255, 115, 0, 100)
  CRITICAL = rl.Color(255, 0, 21, 100)


class OffroadAlertColors:
  HIGH_SEVERITY = Colors.HOME_ALERT_INACTIVE
  LOW_SEVERITY = rl.Color(41, 41, 41, 255)
  BACKGROUND = rl.Color(57, 57, 57, 255)
  TEXT = Colors.WHITE
  BUTTON = Colors.WHITE
  BUTTON_PRESSED = rl.Color(200, 200, 200, 255)
  BUTTON_TEXT = Colors.BLACK
  SNOOZE_BG = rl.Color(79, 79, 79, 255)
  SNOOZE_BG_PRESSED = rl.Color(100, 100, 100, 255)


class SettingsColors:
  PANEL = OffroadAlertColors.LOW_SEVERITY
  CLOSE_BUTTON = PANEL
  CLOSE_BUTTON_PRESSED = rl.Color(59, 59, 59, 255)
  TEXT_NORMAL = rl.Color(128, 128, 128, 255)
  ICON_PRESSED = rl.Color(220, 220, 220, 255)


class FirehoseColors:
  GREEN = rl.Color(46, 204, 113, 255)
  RED = rl.Color(231, 76, 60, 255)
  GRAY = rl.Color(68, 68, 68, 255)
  LIGHT_GRAY = rl.Color(228, 228, 228, 255)


class ConfidenceBallColors:
  ACTIVE_TOP = rl.Color(0, 255, 204, 255)
  ACTIVE_BOTTOM = rl.Color(0, 255, 38, 255)
  WARNING_TOP = rl.Color(255, 200, 0, 255)
  WARNING_BOTTOM = rl.Color(255, 115, 0, 255)
  CRITICAL_TOP = rl.Color(255, 0, 21, 255)
  CRITICAL_BOTTOM = rl.Color(255, 0, 89, 255)
  INACTIVE_TOP = Colors.WHITE
  INACTIVE_BOTTOM = rl.Color(82, 82, 82, 255)
  HIDDEN_TOP = rl.Color(50, 50, 50, 255)
  HIDDEN_BOTTOM = rl.Color(13, 13, 13, 255)


class PairingColors:
  BACKGROUND = rl.Color(224, 224, 224, 255)
  DOT = rl.Color(70, 70, 70, 255)
  CARD = rl.Color(240, 240, 240, 255)


class CommunityColors:
  ENABLED = rl.Color(44, 44, 226, 255)
  DISABLED = rl.Color(60, 60, 60, 255)
  UNAVAILABLE = rl.Color(40, 40, 40, 255)
  BORDER = rl.Color(80, 80, 80, 255)


THROTTLE_COLORS = [
  rl.Color(13, 248, 122, 102),
  rl.Color(114, 255, 92, 89),
  rl.Color(114, 255, 92, 0),
]

NO_THROTTLE_COLORS = [
  rl.Color(242, 242, 242, 102),
  rl.Color(242, 242, 242, 89),
  rl.Color(242, 242, 242, 0),
]

STEERING_COLORS = [
  rl.Color(0, 191, 255, 102),
  rl.Color(0, 191, 255, 89),
  rl.Color(0, 191, 255, 0),
]
