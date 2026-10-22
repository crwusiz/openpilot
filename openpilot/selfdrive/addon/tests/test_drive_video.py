import asyncio
import os
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from openpilot.selfdrive.addon.drive_video import DriveVideoError, DriveVideoLibrary, create_drive_video_router


class DriveVideoFixture:
  def setUp(self):
    super().setUp()
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.base = Path(self.temporary.name)
    self.root = self.base / 'recordings'
    self.root.mkdir()
    self.cache = self.base / 'cache'
    self.library = DriveVideoLibrary(self.root, self.cache)

  def footage(self, name='2026-10-22--12-00-00--0', filename='qcamera.ts', content=b'transport stream'):
    segment = self.root / name
    segment.mkdir(exist_ok=True)
    source = segment / filename
    source.write_bytes(content)
    return source

  def remux(self, command, *_args, **kwargs):
    self.assertFalse(kwargs.get('shell', False))
    self.assertGreater(kwargs['timeout'], 0)
    Path(command[-1]).write_bytes(b'prepared browser video')
    return subprocess.CompletedProcess(command, 0, stdout=b'', stderr=b'')

  def assert_error(self, status, name):
    with self.assertRaises(DriveVideoError) as error:
      self.library.prepare(name)
    self.assertEqual(error.exception.status_code, status)


class TestDriveVideoLibrary(DriveVideoFixture, unittest.TestCase):
  def test_catalog_groups_routes_and_sorts_segment_indices_numerically(self):
    for name in ('old--route--10', 'old--route--2', 'new--route--0'):
      source = self.footage(name)
      timestamp = 20 if name.startswith('new') else 10
      os.utime(source.parent, (timestamp, timestamp))
    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'):
      catalog = self.library.catalog()
    self.assertTrue(catalog['ffmpeg_available'])
    self.assertEqual([route['name'] for route in catalog['routes']], ['new--route', 'old--route'])
    self.assertEqual([segment['index'] for segment in catalog['routes'][1]['segments']], [2, 10])
    self.assertEqual(catalog['routes'][1]['segments'][1]['name'], 'old--route--10')
    self.assertTrue(all('modified' in route for route in catalog['routes']))

  def test_catalog_ignores_empty_missing_invalid_and_non_camera_recordings(self):
    for name in ('boot', 'invalid--name', 'bad--route--tail'):
      self.footage(name)
    self.footage('empty--route--0', content=b'')
    self.footage('road--route--0', filename='fcamera.hevc')
    self.footage('missing--route--0').unlink()
    (self.root / 'file--route--0').write_bytes(b'not a directory')
    self.assertEqual(self.library.catalog()['routes'], [])
    self.assertEqual(DriveVideoLibrary(self.base / 'missing', self.cache).catalog()['routes'], [])

  def test_catalog_marks_recording_segment_and_prepare_rejects_it(self):
    source = self.footage()
    source.with_name(source.name + '.lock').touch()
    segment = self.library.catalog()['routes'][0]['segments'][0]
    self.assertTrue(segment['recording'])
    self.assert_error(409, source.parent.name)

  def test_prepare_prefers_existing_nonempty_mp4_without_ffmpeg(self):
    source = self.footage()
    mp4 = self.footage(filename='qcamera.mp4', content=b'original mp4')
    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value=None), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run') as convert:
      self.assertEqual(self.library.prepare(source.parent.name), mp4)
    convert.assert_not_called()
    self.assertEqual(mp4.read_bytes(), b'original mp4')

  def test_prepare_rejects_malformed_names_and_traversal(self):
    for name in ('', '.', '..', '../outside--route--0', 'valid--route--0/../other--route--0',
                 r'valid--route--0\qcamera.mp4', '/absolute--route--0', 'invalid', 'bad--route--tail'):
      with self.subTest(name=name):
        self.assert_error(400, name)

  def test_prepare_reports_missing_deleted_and_empty_footage(self):
    self.assert_error(404, 'missing--route--0')
    source = self.footage()
    source.unlink()
    self.assert_error(404, source.parent.name)
    source.write_bytes(b'')
    self.assert_error(404, source.parent.name)

  def test_prepare_rejects_segment_and_video_symlinks_outside_recording_root(self):
    outside = self.base / 'outside'
    outside.mkdir()
    (outside / 'qcamera.mp4').write_bytes(b'private file')
    segment_link = self.root / 'linked--route--0'
    try:
      segment_link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
      self.skipTest(f'Symlink creation unavailable: {error}')
    with self.assertRaises(DriveVideoError):
      self.library.prepare(segment_link.name)
    self.assertEqual(self.library.catalog()['routes'], [])
    source = self.footage('linked--video--0', filename='qcamera.mp4')
    source.unlink()
    source.symlink_to(outside / 'qcamera.mp4')
    with self.assertRaises(DriveVideoError):
      self.library.prepare(source.parent.name)
    self.assertEqual(self.library.catalog()['routes'], [])

  def test_ts_remux_is_cached_and_rebuilt_when_source_changes(self):
    source = self.footage()
    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=self.remux) as convert:
      output = self.library.prepare(source.parent.name)
      self.assertEqual(output.read_bytes(), b'prepared browser video')
      self.assertEqual(output.suffix, '.mp4')
      self.assertEqual(self.library.prepare(source.parent.name), output)
      convert.assert_called_once()
      self.assertIn(str(source), convert.call_args.args[0])
      self.assertIn('copy', convert.call_args.args[0])
      source.write_bytes(b'newer, longer transport stream')
      updated = self.library.prepare(source.parent.name)
      self.assertNotEqual(updated, output)
      self.assertEqual(updated.read_bytes(), b'prepared browser video')
      self.assertEqual(convert.call_count, 2)
    self.assertEqual(source.read_bytes(), b'newer, longer transport stream')

  def test_ts_without_ffmpeg_reports_service_unavailable(self):
    source = self.footage()
    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value=None):
      self.assertFalse(self.library.catalog()['ffmpeg_available'])
      self.assert_error(503, source.parent.name)

  def test_remux_retries_without_audio(self):
    source = self.footage()
    calls = []

    def convert(command, *args, **kwargs):
      calls.append(command)
      if len(calls) == 1:
        Path(command[-1]).write_bytes(b'incomplete output')
        return subprocess.CompletedProcess(command, 1, stdout=b'', stderr=b'audio muxing failed')
      return self.remux(command, *args, **kwargs)

    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=convert):
      output = self.library.prepare(source.parent.name)
    self.assertEqual(output.read_bytes(), b'prepared browser video')
    self.assertEqual(len(calls), 2)
    self.assertIn('-an', calls[1])

  def test_prepare_removes_partial_files_left_by_an_interrupted_process(self):
    source = self.footage()
    self.cache.mkdir()
    partial = self.cache / 'interrupted.part.mp4'
    partial.write_bytes(b'incomplete video')
    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=self.remux):
      output = self.library.prepare(source.parent.name)
    self.assertFalse(partial.exists())
    self.assertEqual(list(self.cache.iterdir()), [output])

  def test_failed_or_timed_out_remux_removes_partial_cache_files(self):
    source = self.footage()
    for failure in ('failure', 'timeout'):
      with self.subTest(failure=failure):
        def convert(command, *_args, failure=failure, **_kwargs):
          Path(command[-1]).write_bytes(b'partial output')
          if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 1)
          return subprocess.CompletedProcess(command, 1, stdout=b'', stderr=b'invalid input')

        with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
             patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=convert):
          self.assert_error(502, source.parent.name)
        self.assertEqual([path for path in self.cache.glob('**/*') if path.is_file()], [])
        self.assertEqual(source.read_bytes(), b'transport stream')

  def test_concurrent_prepare_runs_only_one_remux(self):
    source = self.footage()
    entered = threading.Event()
    finish = threading.Event()

    def convert(command, *args, **kwargs):
      entered.set()
      self.assertTrue(finish.wait(timeout=5))
      return self.remux(command, *args, **kwargs)

    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=convert) as process, \
         ThreadPoolExecutor(max_workers=2) as pool:
      first = pool.submit(self.library.prepare, source.parent.name)
      try:
        self.assertTrue(entered.wait(timeout=5))
        second = pool.submit(self.library.prepare, source.parent.name)
      finally:
        finish.set()
      self.assertEqual(first.result(timeout=5), second.result(timeout=5))
      process.assert_called_once()

  def test_cache_eviction_preserves_acquired_video_until_release(self):
    self.library = DriveVideoLibrary(self.root, self.cache, max_cache_files=1)
    first = self.footage('first--route--0')
    second = self.footage('second--route--0')
    third = self.footage('third--route--0')
    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=self.remux):
      pinned = self.library.acquire(first.parent.name)
      try:
        self.library.prepare(second.parent.name)
        self.assertTrue(pinned.is_file())
      finally:
        self.library.release(pinned)
      newest = self.library.prepare(third.parent.name)
    self.assertTrue(newest.is_file())
    self.assertFalse(pinned.exists())


class TestDriveVideoAPI(DriveVideoFixture, unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.app = FastAPI()
    self.app.include_router(create_drive_video_router(self.library))
    self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://device')
    self.addAsyncCleanup(self.client.aclose)

  async def test_catalog_and_prepare_return_playable_url(self):
    source = self.footage(filename='qcamera.mp4', content=b'0123456789')
    catalog = await self.client.get('/api/drives')
    self.assertEqual(catalog.status_code, 200)
    self.assertEqual(catalog.json()['routes'][0]['segments'][0]['name'], source.parent.name)
    prepared = await self.client.get(f'/api/drives/prepare/{source.parent.name}')
    self.assertEqual(prepared.status_code, 200)
    self.assertEqual(prepared.json()['url'], f'/api/drives/video/{source.parent.name}')
    response = await self.client.get(prepared.json()['url'])
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.content, b'0123456789')
    self.assertTrue(response.headers['content-type'].startswith('video/mp4'))

  async def test_direct_mp4_supports_byte_and_suffix_ranges(self):
    source = self.footage(filename='qcamera.mp4', content=b'0123456789')
    url = f'/api/drives/video/{source.parent.name}'
    for byte_range, expected, content_range in (('bytes=2-5', b'2345', 'bytes 2-5/10'), ('bytes=-3', b'789', 'bytes 7-9/10')):
      with self.subTest(byte_range=byte_range):
        response = await self.client.get(url, headers={'Range': byte_range})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, expected)
        self.assertEqual(response.headers['content-range'], content_range)
        self.assertEqual(response.headers['accept-ranges'], 'bytes')
    invalid = await self.client.get(url, headers={'Range': 'bytes=100-200'})
    self.assertEqual(invalid.status_code, 416)

  async def test_remuxed_video_supports_seeking_and_reuses_prepared_cache(self):
    source = self.footage()
    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=self.remux) as process:
      prepared = await self.client.get(f'/api/drives/prepare/{source.parent.name}')
      self.assertEqual(prepared.status_code, 200)
      response = await self.client.get(prepared.json()['url'], headers={'Range': 'bytes=0-7'})
    self.assertEqual(response.status_code, 206)
    self.assertEqual(response.content, b'prepared')
    self.assertEqual(self.library._readers, {})
    process.assert_called_once()

  async def test_invalid_ranges_release_cached_video_pins(self):
    source = self.footage()
    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=self.remux):
      for byte_range, status in (('bytes=not-a-range', 400), ('bytes=1000-2000', 416)):
        with self.subTest(byte_range=byte_range):
          response = await self.client.get(f'/api/drives/video/{source.parent.name}', headers={'Range': byte_range})
          self.assertEqual(response.status_code, status)
          self.assertEqual(self.library._readers, {})

  async def test_client_disconnect_releases_cached_video_pin(self):
    source = self.footage()
    url = f'/api/drives/video/{source.parent.name}'
    scope = {'type': 'http', 'method': 'GET', 'path': url, 'raw_path': url.encode(), 'headers': [],
             'query_string': b'', 'scheme': 'http', 'http_version': '1.1', 'server': ('device', 80),
             'client': ('browser', 123), 'asgi': {'version': '3.0'}}

    async def receive():
      return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
      if message['type'] == 'http.response.start':
        raise asyncio.CancelledError

    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=self.remux), \
         self.assertRaises(asyncio.CancelledError):
      await self.app(scope, receive, send)
    self.assertEqual(self.library._readers, {})

  async def test_disconnect_during_remux_releases_eventual_video_pin(self):
    source = self.footage()
    entered = threading.Event()
    finish = threading.Event()
    released = threading.Event()
    original_release = self.library.release

    def convert(command, *args, **kwargs):
      entered.set()
      self.assertTrue(finish.wait(timeout=5))
      return self.remux(command, *args, **kwargs)

    def release(path):
      original_release(path)
      released.set()

    with patch('openpilot.selfdrive.addon.drive_video.shutil.which', return_value='/usr/bin/ffmpeg'), \
         patch('openpilot.selfdrive.addon.drive_video.subprocess.run', side_effect=convert), \
         patch.object(self.library, 'release', side_effect=release):
      request = asyncio.create_task(self.client.get(f'/api/drives/video/{source.parent.name}'))
      try:
        self.assertTrue(await asyncio.to_thread(entered.wait, 5))
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
          await request
      finally:
        finish.set()
      self.assertTrue(await asyncio.to_thread(released.wait, 5))
    self.assertEqual(self.library._readers, {})

  async def test_prepare_and_video_expose_expected_errors(self):
    source = self.footage()
    source.with_name(source.name + '.lock').touch()
    for endpoint in ('prepare', 'video'):
      with self.subTest(endpoint=endpoint):
        locked = await self.client.get(f'/api/drives/{endpoint}/{source.parent.name}')
        self.assertEqual(locked.status_code, 409)
        missing = await self.client.get(f'/api/drives/{endpoint}/missing--route--0')
        self.assertEqual(missing.status_code, 404)
        malformed = await self.client.get(f'/api/drives/{endpoint}/invalid')
        self.assertEqual(malformed.status_code, 400)


if __name__ == '__main__':
  unittest.main()
