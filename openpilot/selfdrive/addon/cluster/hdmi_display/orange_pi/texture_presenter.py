import ctypes
import logging
import math
from pathlib import Path
import threading
import time


ARGB_MASKS = (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
LOG = logging.getLogger("cluster_receiver.hdmi")


class _Point(ctypes.Structure):
  _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]


class _Color(ctypes.Structure):
  _fields_ = [(name, ctypes.c_uint8) for name in ("r", "g", "b", "a")]


class _Vertex(ctypes.Structure):
  _fields_ = [("position", _Point), ("color", _Color), ("tex_coord", _Point)]


class _LayerTextures:
  """Use public SDL APIs for floating-point UVs absent from pygame Texture.draw.

  The window ID resolves the renderer owned by pygame; no Python extension
  object layouts or pointers are inspected. All calls share pygame's linked
  SDL, renderer queue and owner thread. SDL 2.0.18 introduced RenderGeometry.
  """

  def __init__(self, pygame_module, window):
    library = ctypes.CDLL(pygame_module.base.__file__)
    if not hasattr(library, "SDL_GetWindowFromID"):
      # Windows extension DLLs do not re-export their dependencies' symbols.
      library = ctypes.CDLL(str(Path(pygame_module.base.__file__).with_name("SDL2.dll")))
    self.sdl = library
    signatures = {
      "SDL_GetWindowFromID": ([ctypes.c_uint32], ctypes.c_void_p),
      "SDL_GetRenderer": ([ctypes.c_void_p], ctypes.c_void_p),
      "SDL_CreateTexture": ([ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_int, ctypes.c_int], ctypes.c_void_p),
      "SDL_DestroyTexture": ([ctypes.c_void_p], None),
      "SDL_SetTextureBlendMode": ([ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
      "SDL_ComposeCustomBlendMode": ([ctypes.c_int] * 6, ctypes.c_int),
      "SDL_SetTextureScaleMode": ([ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
      "SDL_MasksToPixelFormatEnum": ([ctypes.c_int] + [ctypes.c_uint32] * 4, ctypes.c_uint32),
      "SDL_UpdateTexture": ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
      "SDL_RenderGeometry": ([ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Vertex), ctypes.c_int,
                               ctypes.POINTER(ctypes.c_int), ctypes.c_int], ctypes.c_int),
      "SDL_RenderFlush": ([ctypes.c_void_p], ctypes.c_int),
      "SDL_GetError": ([], ctypes.c_char_p),
    }
    for name, (arguments, result) in signatures.items():
      function = getattr(library, name)
      function.argtypes, function.restype = arguments, result
    native_window = library.SDL_GetWindowFromID(window.id)
    self.renderer = library.SDL_GetRenderer(native_window) if native_window else None
    if not self.renderer:
      raise RuntimeError("SDL could not resolve the texture presenter's renderer")
    self.textures = {}
    self.sources = {}
    # Premultiplied path RGB already includes alpha. Applying straight alpha
    # here would darken its edges and make overlapping paths too faint.
    self._premultiplied_blend = library.SDL_ComposeCustomBlendMode(2, 6, 1, 2, 6, 1)
    self._vertices = (_Vertex * 4)()
    self._triangles = ((ctypes.c_int * 3)(0, 1, 2), (ctypes.c_int * 3)(0, 2, 3))

  def _check(self, result):
    if result < 0:
      message = self.sdl.SDL_GetError()
      raise RuntimeError(message.decode("utf-8", errors="replace") if message else "SDL layer operation failed")

  def upload(self, name, surface, alpha=False, premultiplied=False, source_token=None):
    size = tuple(surface.get_size())
    format_key = surface.get_bitsize(), *surface.get_masks()
    blend = self._premultiplied_blend if premultiplied else int(bool(alpha))
    key = size, format_key, blend
    current = self.textures.get(name)
    # Only explicit immutable sources qualify. Ordinary Surface callers may
    # mutate pixels in place and therefore upload on every call by default.
    # Retain the object itself so recycled Python ids cannot match old pixels.
    if source_token is not None and current is not None and current[0] == key and self.sources.get(name) is source_token:
      return 0
    self.sources.pop(name, None)
    if current is None or current[0] != key:
      if current is not None:
        self.sdl.SDL_DestroyTexture(current[1])
        del self.textures[name]
      pixel_format = self.sdl.SDL_MasksToPixelFormatEnum(*format_key)
      if not pixel_format:
        raise RuntimeError("SDL does not support the camera/overlay pixel format")
      texture = self.sdl.SDL_CreateTexture(self.renderer, pixel_format, 1, *size)  # SDL_TEXTUREACCESS_STREAMING
      if not texture:
        self._check(-1)
      self.textures[name] = key, texture
      self._check(self.sdl.SDL_SetTextureBlendMode(texture, blend))
      self._check(self.sdl.SDL_SetTextureScaleMode(texture, 1))  # SDL_ScaleModeLinear
    else:
      texture = current[1]
    # Keep the exported pixel buffer alive through the synchronous texture
    # upload. This locks the Surface without copying its RGB/RGBA pixels.
    pixels = surface.get_buffer()
    buffer = (ctypes.c_uint8 * pixels.length).from_buffer(pixels)
    self._check(self.sdl.SDL_UpdateTexture(texture, None, ctypes.addressof(buffer), surface.get_pitch()))
    if source_token is not None:
      self.sources[name] = source_token
    return surface.get_pitch() * size[1]

  def forget_source(self, name):
    self.sources.pop(name, None)

  def draw(self, name, positions, uv, color):
    texture = self.textures[name][1]
    u0, v0, u1, v1 = uv
    for vertex, position, tex_coord in zip(self._vertices, positions, ((u0, v0), (u1, v0), (u1, v1), (u0, v1)), strict=True):
      vertex.position = _Point(*position)
      vertex.color = _Color(*color, 255)
      vertex.tex_coord = _Point(*tex_coord)
    # Separate triangles also avoid SDL's software rotated-quad shortcut,
    # which drops the UV rotation. The accelerated renderer still batches them.
    for indices in self._triangles:
      self._check(self.sdl.SDL_RenderGeometry(self.renderer, texture, self._vertices, 4, indices, 3))

  def flush(self):
    self._check(self.sdl.SDL_RenderFlush(self.renderer))

  def close(self):
    self.sources.clear()
    for _, texture in self.textures.values():
      self.sdl.SDL_DestroyTexture(texture)
    self.textures.clear()


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
    self._upload_surface = None
    self._has_frame = False
    self._menu_texture = None
    self._menu_surface = None
    self._frame_size = None
    self._layers = None
    self._layers_disabled = False
    self._layered_frame = None
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

  @property
  def can_present_layers(self):
    """Probe optional floating-point GPU camera composition once, on SDL's thread."""
    self._check_thread()
    if self._layers is None and not self._layers_disabled:
      try:
        self._layers = _LayerTextures(self.pygame, self.window)
        LOG.info("[CLUSTER_HDMI_LAYERS] enabled: SDL GPU camera crop, scaling and alpha composition")
      except Exception as error:
        self._layers_disabled = True
        LOG.warning("[CLUSTER_HDMI_LAYERS] unavailable; retaining composed-frame presentation: %s", error)
    return self._layers is not None

  def disable_layers(self):
    """Drop optional camera layers while retaining the normal SDL texture path."""
    self._check_thread()
    layers, self._layers = self._layers, None
    self._layers_disabled = True
    if self._layered_frame is not None:
      self._has_frame = False
    self._layered_frame = None
    if layers is not None:
      layers.close()

  def _positions(self, rect, logical_size, rotate_180=False):
    physical_width, physical_height = self.get_size()
    target_width, target_height = ((physical_height, physical_width) if self.rotation in (90, 270)
                                   else (physical_width, physical_height))
    scale_x, scale_y = target_width / logical_size[0], target_height / logical_size[1]
    x, y, width, height = rect
    positions = []
    for corner_x, corner_y in ((x, y), (x + width, y), (x + width, y + height), (x, y + height)):
      center_x = (corner_x - logical_size[0] / 2) * scale_x
      center_y = (corner_y - logical_size[1] / 2) * scale_y
      if rotate_180:
        center_x, center_y = -center_x, -center_y
      if self.rotation == 90:
        center_x, center_y = -center_y, center_x
      elif self.rotation == 180:
        center_x, center_y = -center_x, -center_y
      elif self.rotation == 270:
        center_x, center_y = center_y, -center_x
      positions.append((physical_width / 2 + center_x, physical_height / 2 + center_y))
    return positions

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
    converted_at = started
    if controls is None or not controls.screen_off:
      size = tuple(frame.get_size())
      if self._texture is None or self._texture_size != size:
        self._texture = self._video.Texture(self.renderer, size, depth=32, streaming=True)
        self._texture_size = size
        # Match the texture's explicit ARGB8888 format. pygame Texture.update
        # otherwise allocates/converts an entire RGB24 Surface every frame.
        # Keep one conversion destination and upload it without that allocation.
        self._upload_surface = self.pygame.Surface(size, depth=32, masks=ARGB_MASKS)
        self._upload_surface.set_alpha(None)
        depth = getattr(frame, "get_bitsize", lambda: "n/a")()
        LOG.info("[CLUSTER_HDMI_UPLOAD] source_depth=%s | texture_depth=32 | staging=reused_argb8888 | size=%sx%s",
                 depth, *size)
      self._upload_surface.blit(frame, (0, 0))
      converted_at = time.monotonic()
      self._texture.update(self._upload_surface)
      self._frame_size = size
      self._has_frame = True
      self._layered_frame = None
      if self._layers is not None:
        self._layers.sources.clear()
    uploaded_at = time.monotonic()
    result = self.redraw(controls)
    result["convert"] = converted_at - started
    result["upload"] = uploaded_at - converted_at
    result["draw"] = result["blit"]
    result["blit"] += uploaded_at - started
    return result

  def present_layers(self, overlay_surface, camera_surface=None, camera_rect=None,
                     camera_uv=(0.0, 0.0, 1.0, 1.0), controls=None, rotate_180=False, *, path_surface=None,
                     camera_token=None, path_token=None, overlay_token=None):
    """Upload raw camera and RGBA HUD separately; let SDL's GPU compose them.

    UV bounds retain the original sensor's fractional crop in the scaled H264
    frame. The transparent camera hole and path/HUD come from the unchanged
    projection; no rounded source rectangle or CPU camera resize is needed.
    Optional source tokens identify immutable pixel buffers; a changed buffer
    must have a new token. Untokened mutable Surfaces always upload normally.
    """
    self._check_thread()
    if not self.can_present_layers:
      raise RuntimeError("SDL GPU camera layers are unavailable")
    started = time.monotonic()
    upload_bytes = camera_skipped = path_skipped = overlay_skipped = 0
    if controls is None or not controls.screen_off:
      size = tuple(overlay_surface.get_size())
      if camera_surface is not None or path_surface is not None:
        if (camera_rect is None or len(camera_rect) != 4 or any(not math.isfinite(value) for value in camera_rect)
            or camera_rect[2] <= 0 or camera_rect[3] <= 0):
          raise ValueError("GPU camera rectangle must have finite positive dimensions")
      if camera_surface is not None:
        if (len(camera_uv) != 4 or any(not math.isfinite(value) or not 0 <= value <= 1 for value in camera_uv)
            or camera_uv[0] >= camera_uv[2] or camera_uv[1] >= camera_uv[3]):
          raise ValueError("GPU camera UV bounds must be a nonempty normalized crop")
      # Validate all geometry before touching texture/cache state.
      if camera_surface is not None:
        size_bytes = self._layers.upload("camera", camera_surface, source_token=camera_token)
        upload_bytes += size_bytes
        camera_skipped = int(size_bytes == 0)
      else:
        self._layers.forget_source("camera")
      if path_surface is not None:
        size_bytes = self._layers.upload("path", path_surface, premultiplied=True, source_token=path_token)
        upload_bytes += size_bytes
        path_skipped = int(size_bytes == 0)
      else:
        self._layers.forget_source("path")
      size_bytes = self._layers.upload("overlay", overlay_surface, alpha=True, source_token=overlay_token)
      upload_bytes += size_bytes
      overlay_skipped = int(size_bytes == 0)
      self._frame_size = size
      self._layered_frame = (camera_surface is not None, camera_rect, tuple(camera_uv), bool(rotate_180), path_surface is not None)
      self._has_frame = True
    else:
      self._layers.sources.clear()
    uploaded_at = time.monotonic()
    result = self.redraw(controls)
    result.update(convert=0.0, upload=uploaded_at - started, draw=result["blit"])
    result.update(upload_bytes=upload_bytes, camera_upload_skipped=camera_skipped, path_upload_skipped=path_skipped,
                  overlay_upload_skipped=overlay_skipped)
    result["blit"] += uploaded_at - started
    return result

  def redraw(self, controls=None):
    """Redraw a retained texture on touch changes, with no frame conversion/upload."""
    self._check_thread()
    started = time.monotonic()
    self.renderer.clear()
    if self._has_frame and (controls is None or not controls.screen_off):
      tint = (255, 255, 255) if controls is None else controls.tint_color
      if self._layered_frame is not None:
        camera_present, camera_rect, uv, rotate_180, path_present = self._layered_frame
        # Flush queued pygame commands before/after the native layer calls.
        # Both APIs use this renderer, with no separate GL context or state.
        self._layers.flush()
        if camera_present:
          self._layers.draw("camera", self._positions(camera_rect, self._frame_size, rotate_180), uv, tint)
        if path_present:
          self._layers.draw("path", self._positions(camera_rect, self._frame_size, rotate_180), (0, 0, 1, 1), tint)
        positions = self._positions((0, 0, *self._frame_size), self._frame_size, rotate_180)
        self._layers.draw("overlay", positions, (0, 0, 1, 1), tint)
        self._layers.flush()
      else:
        self._texture.color = tint
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
    self._layered_frame = None
    if self._layers is not None:
      self._layers.sources.clear()
    if self.renderer is not None:
      self.renderer.clear()
      self.renderer.present()

  def close(self):
    self._check_thread()
    self.disable_layers()
    # Texture objects retain their renderer. Release them before the window.
    self._texture = None
    self._upload_surface = None
    self._menu_texture = None
    self._menu_surface = None
    self.renderer = None
    window = self.window
    self.window = None
    self._has_frame = False
    if window is not None:
      window.destroy()
