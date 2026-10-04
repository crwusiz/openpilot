import ctypes
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import ipaddress
import logging
import os
import subprocess
import time


LOG = logging.getLogger("cluster_receiver.hdmi")
NETWORK_REFRESH_SECONDS = 2.0


def _run_nmcli(*args):
  return subprocess.run(
    ["nmcli", "--terse", "--escape", "no", "--colors", "no", *args],
    check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=1.0, env={**os.environ, "LC_ALL": "C"},
  ).stdout


def get_network_status(interface):
  """Read the active network and local IPv4 address without scanning for APs."""
  try:
    details = _run_nmcli("--fields", "GENERAL.STATE,GENERAL.TYPE,IP4.ADDRESS", "device", "show", interface)
    fields = dict(line.split(":", 1) for line in details.splitlines() if ":" in line)
    if fields.get("GENERAL.STATE", "").split(" ", 1)[0] != "100":
      return None
    local_ip = None
    for key, value in fields.items():
      if key.startswith("IP4.ADDRESS") and value:
        address = ipaddress.IPv4Interface(value).ip
        if not (address.is_loopback or address.is_link_local or address.is_unspecified):
          local_ip = str(address)
          break
    if local_ip is None:
      return None
    if fields.get("GENERAL.TYPE") != "wifi":
      return None, local_ip
    access_points = _run_nmcli("--fields", "IN-USE,SSID", "device", "wifi", "list", "ifname", interface, "--rescan", "no")
    for line in access_points.splitlines():
      if line.startswith("*:"):
        # Split only the IN-USE field so colons and backslashes in SSIDs survive.
        return line[2:] or "Wi-Fi", local_ip
  except (OSError, subprocess.SubprocessError, ValueError):
    LOG.debug("Unable to read network status for %s", interface, exc_info=True)
  return None


def get_sdl_video_drivers(pygame_module):
  """Inspect pygame's linked SDL on Linux without initializing a display."""
  try:
    # Loading a separate system SDL could report different build capabilities
    # from the library actually used by a pygame wheel.
    sdl = ctypes.CDLL(pygame_module.base.__file__)
    sdl.SDL_GetNumVideoDrivers.argtypes = []
    sdl.SDL_GetNumVideoDrivers.restype = ctypes.c_int
    sdl.SDL_GetVideoDriver.argtypes = [ctypes.c_int]
    sdl.SDL_GetVideoDriver.restype = ctypes.c_char_p
    return [sdl.SDL_GetVideoDriver(i).decode() for i in range(sdl.SDL_GetNumVideoDrivers())]
  except (AttributeError, OSError):
    LOG.debug("Unable to inspect pygame's linked SDL", exc_info=True)
    return None


class HdmiDisplay:
  """Fullscreen SDL display for the Orange Pi HDMI panel.

  SDL also owns the event queue, so USB touch controllers exposed as SDL finger
  events remain usable without coupling touch behavior to the frame protocol.
  """

  def __init__(self, width=1920, height=480, display_index=0, fullscreen=True,
               show_cursor=False, touch_handler=None, pygame_module=None,
               rotation=0, touch_rotation=None, log_touch=False, network_interface="wlan0"):
    self.size = (max(1, int(width)), max(1, int(height)))
    self.rotation = int(rotation)
    # Touch coordinates are reported in panel orientation unless overridden for
    # a controller/desktop that has already rotated its input to landscape.
    self.touch_rotation = (-self.rotation) % 360 if touch_rotation is None else int(touch_rotation)
    if self.rotation not in (0, 90, 180, 270) or self.touch_rotation not in (0, 90, 180, 270):
      raise ValueError("Display and touch rotations must be 0, 90, 180, or 270 degrees")
    self.log_touch = bool(log_touch)
    self.display_index = max(0, int(display_index))
    self.fullscreen = bool(fullscreen)
    self.show_cursor = bool(show_cursor)
    self.touch_handler = touch_handler
    self._pygame = pygame_module
    self.screen = None
    self.connected = False
    self.close_requested = False
    self.last_touch = None
    self._waiting_frame = None
    self._showing_waiting = False
    self.network_interface = network_interface
    self._network_status = None
    self._waiting_network_status = None
    self._network_executor = None
    self._network_future = None
    self._network_next_check = 0.0

  def open(self):
    if self.connected:
      return True
    try:
      if self._pygame is None:
        import pygame
        self._pygame = pygame
      pygame = self._pygame
      pygame.display.init()
      if pygame.display.get_driver().lower() == "kmsdrm":
        # KMSDRM uses EGL even for a normal, CPU-blitted pygame Surface. Request
        # GLES explicitly before window creation; embedded drivers may not have
        # a desktop OpenGL window config. The SDL texture renderer must agree.
        os.environ.setdefault("SDL_RENDER_DRIVER", "opengles2")
        for attribute, value in (
          (pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_ES),
          (pygame.GL_CONTEXT_MAJOR_VERSION, 2),
          (pygame.GL_CONTEXT_MINOR_VERSION, 0),
          (pygame.GL_DEPTH_SIZE, 0),
          (pygame.GL_STENCIL_SIZE, 0),
        ):
          pygame.display.gl_set_attribute(attribute, value)
        LOG.info("KMSDRM: requesting OpenGL ES 2, SDL_RENDER_DRIVER=%s", os.environ["SDL_RENDER_DRIVER"])
      flags = pygame.DOUBLEBUF | (pygame.FULLSCREEN if self.fullscreen else 0)
      try:
        self.screen = pygame.display.set_mode(
          self.size, flags, display=self.display_index, vsync=1,
        )
      except TypeError:
        # Compatibility with older distro pygame builds.
        self.screen = pygame.display.set_mode(self.size, flags)
      pygame.display.set_caption("C4 Cluster")
      pygame.mouse.set_visible(self.show_cursor)
      self.screen.fill((0, 0, 0))
      pygame.display.flip()
      self.close_requested = False
      self.connected = True
      LOG.info("Orange Pi HDMI display ready at %dx%d, frame rotation=%d clockwise, touch correction=%d clockwise",
               *self.screen.get_size(), self.rotation, self.touch_rotation)
      return True
    except Exception as e:
      LOG.exception("Failed to initialize HDMI display: pygame module=%s, SDL_VIDEODRIVER=%s",
                    getattr(self._pygame, "__file__", "unknown"), os.environ.get("SDL_VIDEODRIVER", "auto"))
      if "kmsdrm not available" in str(e).lower():
        drivers = get_sdl_video_drivers(self._pygame)
        if drivers is None:
          LOG.info("Unable to enumerate this pygame's SDL video drivers.")
        else:
          LOG.info("Compiled SDL video drivers: %s", drivers)
          if any(driver.lower() == "kmsdrm" for driver in drivers):
            LOG.info("KMSDRM is compiled in; inspect DRM cards, connected outputs, libraries, and DRM master ownership.")
          else:
            LOG.info("This SDL build has no KMSDRM backend.")
        LOG.info("See README.md: collect the KMSDRM diagnostics before reinstalling packages.")
      elif "gbm" in str(e).lower() or "egl" in str(e).lower():
        LOG.info("GBM/EGL window creation failed after SDL video initialization.")
        LOG.info("Run sudo bash scripts/diagnose.sh --egl to collect GBM/EGL details; see README.md for the X11 fallback.")
      self.close()
      return False

  def pump_events(self):
    pygame = self._pygame
    if pygame is None:
      return False
    for event in pygame.event.get():
      if event.type == pygame.QUIT or (
        event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
      ):
        self.close_requested = True
        self.connected = False
        return False
      if event.type in (pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP):
        x, y = event.x, event.y
        if self.touch_rotation == 90:
          x, y = 1 - y, x
        elif self.touch_rotation == 180:
          x, y = 1 - x, 1 - y
        elif self.touch_rotation == 270:
          x, y = y, 1 - x
        self.last_touch = {
          "type": event.type,
          "finger_id": event.finger_id,
          "x": x,
          "y": y,
        }
        if self.log_touch:
          LOG.info("Touch type=%s finger=%s raw=(%.4f, %.4f) cluster=(%.4f, %.4f)",
                   event.type, event.finger_id, event.x, event.y, x, y)
        if self.touch_handler is not None:
          self.touch_handler(self.last_touch)
    if self._showing_waiting:
      self.show_waiting()
    return True

  def send_jpeg(self, jpeg):
    if self.close_requested:
      return False
    if not self.connected and not self.open():
      return False
    if not self.pump_events():
      return False
    try:
      frame = self._pygame.image.load(BytesIO(jpeg), "cluster.jpg").convert()
      self._present_frame(frame)
      return True
    except Exception as e:
      LOG.warning("Failed to display HDMI frame: %s", e)
      return False

  def _present_frame(self, frame):
    # Invalidate the status cache before drawing, including a failed page flip.
    self._showing_waiting = False
    if self.rotation:
      # pygame's positive angles are counterclockwise; our option is clockwise.
      frame = self._pygame.transform.rotate(frame, -self.rotation)
    if frame.get_size() != self.screen.get_size():
      frame = self._pygame.transform.smoothscale(frame, self.screen.get_size())
    self.screen.blit(frame, (0, 0))
    self._pygame.display.flip()

  def _make_waiting_frame(self):
    pygame = self._pygame
    pygame.font.init()
    font_path = next((path for path in (
      "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
      "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ) if os.path.isfile(path)), None)
    if font_path is None:
      font_path = pygame.font.match_font("notosanscjkkr,nanumgothic,malgungothic,applegothic")
    if font_path:
      title = "COMMA 연결 대기 중"
      subtitle = "네트워크와 클러스터 옵션을 확인하세요"
    else:
      # pygame's bundled font has no Korean glyphs. Keep the screen readable
      # until scripts/install.sh installs the OS fonts-noto-cjk package.
      title = "Waiting for COMMA connection"
      subtitle = "Check Network and COMMA cluster option"
      LOG.info("Korean font unavailable; using English on the waiting screen")
    width, height = self.screen.get_size()
    if self.rotation in (90, 270):
      width, height = height, width
    frame = pygame.Surface((width, height))
    frame.fill((0, 0, 0))
    scale = min(width / 1920, height / 480)
    labels = [
      (title, 64, (240, 240, 240), -30),
      (subtitle, 32, (150, 160, 170), 55),
    ]
    if self._network_status is None:
      labels.append(("Network Offline", 32, (220, 130, 100), 130))
    else:
      network, local_ip = self._network_status
      labels.extend([
        (f"SSID: {network}" if network is not None else f"Network: {self.network_interface}", 32, (120, 200, 160), 115),
        (f"IP: {local_ip}", 32, (120, 200, 160), 160),
      ])
    for text, size, color, offset in labels:
      font = pygame.font.Font(font_path, max(1, round(size * scale)))
      label = font.render(text, True, color)
      frame.blit(label, label.get_rect(center=(width // 2, height // 2 + round(offset * scale))))
    return frame

  def _refresh_network_status(self):
    # Run nmcli outside SDL's thread so a slow daemon cannot stall touch events
    # or delay the first live frame. Only the display thread updates the cache.
    if self._network_future is not None and self._network_future.done():
      self._network_status = self._network_future.result()
      self._network_future = None
    now = time.monotonic()
    if self._network_future is None and now >= self._network_next_check:
      if self._network_executor is None:
        self._network_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cluster-network")
      self._network_future = self._network_executor.submit(get_network_status, self.network_interface)
      self._network_next_check = now + NETWORK_REFRESH_SECONDS

  def show_waiting(self):
    """Replace an unavailable C4 image with a local, correctly rotated status."""
    if self.screen is None or self.close_requested:
      return False
    self._refresh_network_status()
    if self._waiting_frame is None or self._waiting_network_status != self._network_status:
      self._waiting_frame = self._make_waiting_frame()
      self._waiting_network_status = self._network_status
      self._showing_waiting = False
    if not self._showing_waiting:
      self._present_frame(self._waiting_frame)
      self._showing_waiting = True
    return True

  def clear(self):
    """Remove the last driving frame when the connection becomes unavailable."""
    if self.screen is not None:
      self.screen.fill((0, 0, 0))
      self._pygame.display.flip()
      self._showing_waiting = False

  def close(self):
    self.connected = False
    screen = self.screen
    self.screen = None
    self._waiting_frame = None
    self._showing_waiting = False
    self._network_status = None
    self._waiting_network_status = None
    self._network_future = None
    self._network_next_check = 0.0
    if self._network_executor is not None:
      self._network_executor.shutdown(wait=False, cancel_futures=True)
      self._network_executor = None
    if self._pygame is None:
      return
    try:
      if screen is not None:
        screen.fill((0, 0, 0))
        self._pygame.display.flip()
      self._pygame.display.quit()
    except Exception:
      pass
