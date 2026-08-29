from io import BytesIO
import logging


LOG = logging.getLogger("cluster_receiver.hdmi")


class HdmiDisplay:
  """Fullscreen SDL display for the Orange Pi HDMI panel.

  SDL also owns the event queue, so USB touch controllers exposed as SDL finger
  events remain usable without coupling touch behavior to the frame protocol.
  """

  def __init__(self, width=1920, height=720, display_index=0, fullscreen=True,
               show_cursor=False, touch_handler=None, pygame_module=None):
    self.size = (max(1, int(width)), max(1, int(height)))
    self.display_index = max(0, int(display_index))
    self.fullscreen = bool(fullscreen)
    self.show_cursor = bool(show_cursor)
    self.touch_handler = touch_handler
    self._pygame = pygame_module
    self.screen = None
    self.connected = False
    self.close_requested = False
    self.last_touch = None

  def open(self):
    if self.connected:
      return True
    try:
      if self._pygame is None:
        import pygame
        self._pygame = pygame
      pygame = self._pygame
      pygame.display.init()
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
      LOG.info("Orange Pi HDMI display ready at %dx%d", *self.screen.get_size())
      return True
    except Exception:
      LOG.exception("Failed to initialize HDMI display")
      self.close()
      return False

  def _pump_events(self):
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
        self.last_touch = {
          "type": event.type,
          "finger_id": event.finger_id,
          "x": event.x,
          "y": event.y,
        }
        if self.touch_handler is not None:
          self.touch_handler(self.last_touch)
    return True

  def send_jpeg(self, jpeg):
    if self.close_requested:
      return False
    if not self.connected and not self.open():
      return False
    if not self._pump_events():
      return False
    try:
      frame = self._pygame.image.load(BytesIO(jpeg), "cluster.jpg").convert()
      if frame.get_size() != self.screen.get_size():
        frame = self._pygame.transform.smoothscale(frame, self.screen.get_size())
      self.screen.blit(frame, (0, 0))
      self._pygame.display.flip()
      return True
    except Exception as e:
      LOG.warning("Failed to display HDMI frame: %s", e)
      return False

  def close(self):
    self.connected = False
    screen = self.screen
    self.screen = None
    if self._pygame is None:
      return
    try:
      if screen is not None:
        screen.fill((0, 0, 0))
        self._pygame.display.flip()
      self._pygame.display.quit()
    except Exception:
      pass
