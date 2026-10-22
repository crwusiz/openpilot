"""Seekable playback of device recordings without decoding or re-encoding video."""

import asyncio
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse


class DriveVideoError(Exception):
  def __init__(self, status_code: int, message: str):
    super().__init__(message)
    self.status_code = status_code


class DriveVideoLibrary:
  def __init__(self, root: Path, cache_root: Path, *, max_cache_bytes: int = 256 * 1024 * 1024, max_cache_files: int = 8):
    self.root = root
    self.cache_root = cache_root
    self.max_cache_bytes = max_cache_bytes
    self.max_cache_files = max_cache_files
    self._lock = threading.RLock()
    self._readers: dict[Path, int] = {}

  def _directory(self, name: str) -> Path:
    route, _, index = name.rpartition('--')
    if ('--' not in route or not index.isascii() or not index.isdecimal()
        or any(character in name for character in ('/', '\\', '\0')) or name in ('.', '..')):
      raise DriveVideoError(400, 'Invalid recording segment.')
    directory = self.root / name
    if directory.is_symlink() or directory.resolve().parent != self.root.resolve():
      raise DriveVideoError(400, 'Invalid recording segment.')
    if not directory.is_dir():
      raise DriveVideoError(404, 'Recording was removed or is no longer available. Refresh the list.')
    return directory

  @staticmethod
  def _source(directory: Path) -> Path | None:
    for filename in ('qcamera.mp4', 'qcamera.ts'):
      source = directory / filename
      try:
        if not source.is_symlink() and source.is_file() and source.stat().st_size > 0:
          return source
      except FileNotFoundError:
        continue  # loggerd/deleter can remove a file during a scan.
    return None

  def catalog(self) -> dict:
    routes: dict[str, dict] = {}
    try:
      entries = list(self.root.iterdir())
    except FileNotFoundError:
      entries = []
    for entry in entries:
      try:
        directory = self._directory(entry.name)
        if self._source(directory) is None:
          continue
        route_name, _, index = entry.name.rpartition('--')
        route = routes.setdefault(route_name, {'name': route_name, 'modified': 0.0, 'segments': []})
        route['modified'] = max(route['modified'], directory.stat().st_mtime)
        route['segments'].append({'name': entry.name, 'index': int(index), 'recording': any(directory.glob('*.lock'))})
      except (DriveVideoError, FileNotFoundError):
        continue
    for route in routes.values():
      route['segments'].sort(key=lambda segment: segment['index'])
    return {'routes': sorted(routes.values(), key=lambda route: route['modified'], reverse=True),
            'ffmpeg_available': shutil.which('ffmpeg') is not None}

  def _recording_source(self, name: str) -> Path:
    directory = self._directory(name)
    if any(directory.glob('*.lock')):
      raise DriveVideoError(409, 'This segment is still recording. Select a completed segment or refresh later.')
    source = self._source(directory)
    if source is None:
      raise DriveVideoError(404, 'No playable recording exists for this segment. Refresh the list.')
    return source

  @staticmethod
  def _signature(source: Path) -> tuple[int, int]:
    stat = source.stat()
    return stat.st_size, stat.st_mtime_ns

  def _prune(self, protected: Path | None = None):
    files = []
    for path in self.cache_root.glob('*.mp4'):
      if path.name.endswith('.part.mp4'):
        continue
      try:
        stat = path.stat()
        files.append((path, stat.st_size, stat.st_mtime_ns))
      except FileNotFoundError:
        continue
    total, count = sum(size for _, size, _ in files), len(files)
    for path, size, _ in sorted(files, key=lambda item: item[2]):
      if total <= self.max_cache_bytes and count <= self.max_cache_files:
        break
      if path == protected or self._readers.get(path):
        continue
      path.unlink(missing_ok=True)
      total -= size
      count -= 1

  def prepare(self, name: str) -> Path:
    # Both HTTP paths and simultaneous clients share one remux worker and cache.
    with self._lock:
      try:
        source = self._recording_source(name)
        if source.suffix == '.mp4':
          return source
        # A reboot or killed dashboard cannot run the conversion's finally block.
        # No conversion can own a partial while this instance holds its lock.
        for partial in self.cache_root.glob('*.part.mp4'):
          partial.unlink(missing_ok=True)
        signature = self._signature(source)
        digest = hashlib.sha256(f'v1:{source.resolve()}:{signature}'.encode()).hexdigest()
        target = self.cache_root / f'{digest}.mp4'
        if target.is_file() and target.stat().st_size > 0:
          self._prune(protected=target)
          return target
        ffmpeg = shutil.which('ffmpeg')
        if ffmpeg is None:
          raise DriveVideoError(503, 'ffmpeg is unavailable on the device. TS recording playback requires ffmpeg.')
        self.cache_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=digest + '-', suffix='.part.mp4', dir=self.cache_root, delete=False) as output:
          temporary = Path(output.name)
        try:
          command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source), '-map', '0:v:0']
          for audio in (['-map', '0:a?', '-c', 'copy'], ['-c:v', 'copy', '-an']):
            result = subprocess.run([*command, *audio, '-movflags', '+faststart', str(temporary)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=90, check=False)
            if result.returncode == 0 and temporary.stat().st_size > 0:
              break
          else:
            raise DriveVideoError(502, 'Could not prepare this recording. Select another segment or refresh the list.')
          if self._recording_source(name) != source or self._signature(source) != signature:
            raise DriveVideoError(409, 'Recording changed while preparing playback. Refresh the list and try again.')
          os.replace(temporary, target)
          self._prune(protected=target)
          return target
        finally:
          temporary.unlink(missing_ok=True)
      except subprocess.TimeoutExpired as error:
        raise DriveVideoError(502, 'Preparing the recording timed out. Try another segment.') from error
      except FileNotFoundError as error:
        raise DriveVideoError(404, 'Recording is no longer available. Refresh the list.') from error
      except OSError as error:
        raise DriveVideoError(503, 'Could not read or prepare the recording on the device.') from error

  def acquire(self, name: str) -> Path:
    with self._lock:
      path = self.prepare(name)
      if path.parent == self.cache_root:
        self._readers[path] = self._readers.get(path, 0) + 1
      return path

  def release(self, path: Path):
    with self._lock:
      if path in self._readers:
        remaining = self._readers[path] - 1
        if remaining:
          self._readers[path] = remaining
        else:
          del self._readers[path]
      try:
        self._prune()
      except OSError:
        pass  # Cache cleanup must not turn successful playback into an error.


def create_drive_video_router(library: DriveVideoLibrary) -> APIRouter:
  router = APIRouter(prefix='/api/drives')
  # Await the worker before consuming a thread, even when many clients connect.
  worker = asyncio.Lock()
  cleanups: set[asyncio.Task] = set()

  def release_later(path: Path) -> asyncio.Task:
    task = asyncio.create_task(asyncio.to_thread(library.release, path))
    cleanups.add(task)
    task.add_done_callback(cleanups.discard)
    return task

  async def run_worker(action, segment: str, *, pins: bool = False):
    await worker.acquire()
    task = asyncio.create_task(asyncio.to_thread(action, segment))
    # The job retains the slot even if its HTTP client disconnects mid-remux.
    task.add_done_callback(lambda _done: worker.release())
    try:
      return await asyncio.shield(task)
    except asyncio.CancelledError:
      def abandoned(done):
        try:
          path = done.result()
        except (Exception, asyncio.CancelledError):
          return
        if pins:
          release_later(path)
      task.add_done_callback(abandoned)
      raise

  class RecordingResponse(FileResponse):
    async def __call__(self, scope, receive, send):
      try:
        await super().__call__(scope, receive, send)
      finally:
        # Starlette skips BackgroundTask for invalid ranges and interrupted sends.
        await asyncio.shield(release_later(Path(self.path)))

  @router.get('')
  async def recordings():
    try:
      return await asyncio.to_thread(library.catalog)
    except OSError:
      return JSONResponse({'error': 'Could not read recordings on the device.'}, status_code=503)

  @router.get('/prepare/{segment}')
  async def prepare(segment: str):
    try:
      await run_worker(library.prepare, segment)
      return {'url': f'/api/drives/video/{quote(segment, safe="")}'}
    except DriveVideoError as error:
      return JSONResponse({'error': str(error)}, status_code=error.status_code)

  @router.get('/video/{segment}')
  async def video(segment: str):
    try:
      path = await run_worker(library.acquire, segment, pins=True)
      return RecordingResponse(path, media_type='video/mp4', headers={'Cache-Control': 'private, no-store'})
    except DriveVideoError as error:
      return JSONResponse({'error': str(error)}, status_code=error.status_code)

  return router
