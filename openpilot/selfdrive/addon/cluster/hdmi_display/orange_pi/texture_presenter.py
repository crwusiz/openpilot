import threading
import time


class TexturePresenter:
  """SDL texture presentation without CPU frame rotation or brightness blends.

  Owns a standalone window, rather than attaching a second renderer to the
  pygame.display Surface. Construction raises when the optional SDL2 module or
  accelerated backend is unavailable, allowing a caller's Surface fallback.
  """

  def __init__(self, pygame_module, size=(1920, 480), display_index=0, fullscreen=True,
               rotation=0, video_module=None, accelerated=True, vsync=True, hidden=False):
    self.pygame = pygame_module
    self.rotation = int(rotation)
    if self.rotation not in (0, 90, 180, 270):
      raise ValueError("Texture rotation must be 0, 90, 180, or 270 degrees")
    self._owner_thread = threading.get_ident()
    self._video = video_module
    if self._video is None:
      from pygame._sdl2 import video
      self._video = video
    self.window = None
    self.renderer = None
    self._texture = None
    self._texture_size = None
    self._has_frame = False
    self._menu_texture = None
    self._menu_surface = None
    self._frame_size = None
    size = tuple(max(1, int(value)) for value in size)
    display_index = max(0, int(display_index))
    # SDL_WINDOWPOS_CENTERED_DISPLAY encodes the display in its low bits.
    # pygame's Window accepts a tuple of encoded x/y coordinates.
    centered = self._video.WINDOWPOS_CENTERED | display_index
    try:
      self.window = self._video.Window("C4 Cluster", size=size, position=(centered, centered),
                                      fullscreen=bool(fullscreen), hidden=bool(hidden))
      self.renderer = self._video.Renderer(self.window, accelerated=int(bool(accelerated)), vsync=bool(vsync))
      self.renderer.draw_color = (0, 0, 0, 255)
      self.clear()
    except Exception as error:
      # A renderer method's traceback may retain SDL wrappers after our own
      # references are cleared. Release it before destroying the window.
      error.__traceback__ = None
      self.close()
      raise

  def _check_thread(self):
    if threading.get_ident() != self._owner_thread:
      raise RuntimeError("SDL texture presentation must stay on its owner thread")

  def get_size(self):
    return tuple(self.window.size)

  def _destination(self, rect, logical_size):
    """Return the destination before SDL rotates around its own center."""
    physical_width, physical_height = self.get_size()
    target_width, target_height = ((physical_height, physical_width) if self.rotation in (90, 270)
                                   else (physical_width, physical_height))
    scale_x, scale_y = target_width / logical_size[0], target_height / logical_size[1]
    x, y, width, height = rect
    width, height = width * scale_x, height * scale_y
    center_x = (x - logical_size[0] / 2) * scale_x + width / 2
    center_y = (y - logical_size[1] / 2) * scale_y + height / 2
    if self.rotation == 90:
      center_x, center_y = -center_y, center_x
    elif self.rotation == 180:
      center_x, center_y = -center_x, -center_y
    elif self.rotation == 270:
      center_x, center_y = center_y, -center_x
    return self.pygame.Rect(round(physical_width / 2 + center_x - width / 2),
                            round(physical_height / 2 + center_y - height / 2),
                            max(1, round(width)), max(1, round(height)))

  def present(self, frame, controls=None):
    """Upload an unrotated frame and present it; return stage timings."""
    self._check_thread()
    started = time.monotonic()
    if controls is None or not controls.screen_off:
      size = tuple(frame.get_size())
      if self._texture is None or self._texture_size != size:
        self._texture = self._video.Texture(self.renderer, size, streaming=True)
        self._texture_size = size
      self._texture.update(frame)
      self._frame_size = size
      self._has_frame = True
    uploaded_at = time.monotonic()
    result = self.redraw(controls)
    result["upload"] = uploaded_at - started
    result["blit"] += result["upload"]
    return result

  def redraw(self, controls=None):
    """Redraw a retained texture on touch changes, with no JPEG decode/upload."""
    self._check_thread()
    started = time.monotonic()
    self.renderer.clear()
    if self._has_frame and (controls is None or not controls.screen_off):
      self._texture.color = (255, 255, 255) if controls is None else controls.tint_color
      self._texture.draw(dstrect=self._destination((0, 0, *self._frame_size), self._frame_size), angle=self.rotation)
    if controls is not None:
      menu = controls.render_menu()
      if menu is not None:
        surface, position = menu
        if self._menu_surface is not surface:
          self._menu_texture = self._video.Texture.from_surface(self.renderer, surface)
          self._menu_surface = surface
        rect = (*position, *surface.get_size())
        self._menu_texture.draw(dstrect=self._destination(rect, controls.logical_size), angle=self.rotation)
      else:
        self._menu_texture = None
        self._menu_surface = None
    drawn_at = time.monotonic()
    self.renderer.present()
    return {"rotate": 0.0, "scale": 0.0, "blit": drawn_at - started,
            "flip": time.monotonic() - drawn_at}

  def clear(self):
    self._check_thread()
    self._has_frame = False
    if self.renderer is not None:
      self.renderer.clear()
      self.renderer.present()

  def close(self):
    self._check_thread()
    # Texture objects retain their renderer. Release them before the window.
    self._texture = None
    self._menu_texture = None
    self._menu_surface = None
    self.renderer = None
    window = self.window
    self.window = None
    self._has_frame = False
    if window is not None:
      window.destroy()
