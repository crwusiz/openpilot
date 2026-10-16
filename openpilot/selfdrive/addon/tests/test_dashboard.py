import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from nicegui import Client, core, ui
from starlette.requests import Request

from openpilot.selfdrive.addon import dashboard


class TestDashboardHelpers(unittest.TestCase):
  def test_script_arguments_and_environment(self):
    args = ['a path with spaces', 'literal;argument']
    self.assertEqual(dashboard._script_command('/tmp/test.sh', args), ['bash', '/tmp/test.sh', *args])
    self.assertEqual(dashboard._script_command('/tmp/test.py'), [sys.executable, '/tmp/test.py'])
    with patch.dict(os.environ, {'TMUX': 'session', 'TMUX_PANE': 'pane', 'KEEP_ME': 'yes'}):
      env = dashboard._subprocess_env()
      self.assertNotIn('TMUX', env)
      self.assertNotIn('TMUX_PANE', env)
      self.assertEqual(env['KEEP_ME'], 'yes')
      self.assertIn('TMUX', os.environ)

  def test_param_text_handles_bytes_and_missing_values(self):
    with patch.object(dashboard, 'params') as params:
      for value, expected in ((b'HYUNDAI', 'HYUNDAI'), ('GENESIS', 'GENESIS'), (None, 'default'), (b'', 'default')):
        params.get.return_value = value
        self.assertEqual(dashboard.get_param_text('key', 'default'), expected)

  def test_error_summary_prefers_curl_error(self):
    self.assertEqual(dashboard._get_error_summary('progress\rcurl: (7) connection refused\nmore progress'), 'curl: (7) connection refused')
    self.assertEqual(dashboard._get_error_summary('starting\nERROR: upload failed'), 'ERROR: upload failed')
    self.assertEqual(dashboard._get_error_summary(''), 'Unknown error')

  def test_orange_pi_ip_only_shows_for_enabled_network_transport(self):
    params = Mock(get=Mock(return_value=b'network'), get_bool=Mock(return_value=True))
    with patch.object(dashboard, 'params', params), patch.object(dashboard, 'get_connected_pi_ip', return_value='192.168.0.84') as peer:
      self.assertEqual(dashboard.get_orange_pi_status_text(), 'Orange Pi 연결됨 · IP: 192.168.0.84')
      peer.return_value = None
      self.assertEqual(dashboard.get_orange_pi_status_text(), 'Orange Pi 연결 대기 중')
      peer.reset_mock()
      params.get.return_value = 'usb'
      self.assertIn('Network 모드', dashboard.get_orange_pi_status_text())
      params.get.return_value = 'network'
      params.get_bool.return_value = False
      self.assertIn('Cluster를 켜고', dashboard.get_orange_pi_status_text())
      peer.assert_not_called()

  def test_orange_pi_label_updates_after_reconnect_and_disconnect(self):
    client = Client(ui.page('/test-orange-pi-status'))
    self.addCleanup(client.delete)
    params = Mock(get=Mock(return_value=b'network'), get_bool=Mock(side_effect=lambda key: key == 'ClusterEnable'))
    with client, patch.object(dashboard, 'params', params), patch.object(ui, 'timer') as timer, \
         patch.object(dashboard, 'get_connected_pi_ip', return_value='192.168.0.84') as peer:
      dashboard.render_tab_toggles()
      label = next(element for element in client.elements.values() if isinstance(element, ui.label) and 'IP: 192.168.0.84' in element.text)
      interval, refresh = timer.call_args.args
      self.assertEqual(interval, 1.0)
      peer.return_value = '192.168.0.90'
      refresh()
      self.assertEqual(label.text, 'Orange Pi 연결됨 · IP: 192.168.0.90')
      peer.return_value = None
      refresh()
      self.assertEqual(label.text, 'Orange Pi 연결 대기 중')

  def test_network_codec_selector_is_hidden_for_usb_and_disabled_cluster(self):
    for enabled, transport in ((True, 'usb'), (False, 'network')):
      with self.subTest(enabled=enabled, transport=transport):
        client = Client(ui.page(f'/test-codec-hidden-{enabled}-{transport}'))
        self.addCleanup(client.delete)
        values = {'ClusterDisplayTransport': transport, 'ClusterNetworkCodec': 'h264'}
        params = Mock(get=Mock(side_effect=values.get), get_bool=Mock(side_effect=lambda key, on=enabled: on and key == 'ClusterEnable'))
        with client, patch.object(dashboard, 'params', params), patch.object(ui, 'timer'), \
             patch.object(dashboard, 'get_connected_pi_ip', return_value=None):
          dashboard.render_tab_toggles()
        self.assertFalse(any(isinstance(element, ui.select) and element.label == 'Cluster network codec'
                             for element in client.elements.values()))
        params.put.assert_not_called()

  def test_network_codec_defaults_and_changes_are_persisted_with_restart_notice(self):
    for stored, expected in ((None, 'jpeg'), ('vp9', 'jpeg'), (b' H264 ', 'h264')):
      with self.subTest(stored=stored):
        client = Client(ui.page('/test-codec-selection-' + expected))
        self.addCleanup(client.delete)
        values = {'ClusterDisplayTransport': 'network', 'ClusterNetworkCodec': stored}
        params = Mock(get=Mock(side_effect=values.get), get_bool=Mock(side_effect=lambda key: key == 'ClusterEnable'))
        with client, patch.object(dashboard, 'params', params), patch.object(ui, 'timer'), patch.object(ui, 'notify') as notify, \
             patch.object(dashboard, 'get_connected_pi_ip', return_value=None):
          dashboard.render_tab_toggles()
          select = next(element for element in client.elements.values()
                        if isinstance(element, ui.select) and element.label == 'Cluster network codec')
          self.assertEqual(select.value, expected)
          self.assertEqual(select.options, {'jpeg': 'JPEG', 'h264': 'H.264'})
          params.put.assert_not_called()
          selected = 'h264' if expected == 'jpeg' else 'jpeg'
          select.set_value(selected)
          params.put.assert_called_once_with('ClusterNetworkCodec', selected, block=True)
          self.assertIn('Cluster restarting', notify.call_args.args[0])

  def test_switching_transport_refreshes_network_codec_controls(self):
    async def exercise_changes():
      client = Client(ui.page('/test-codec-transport-refresh'))
      self.addCleanup(client.delete)
      values = {'ClusterDisplayTransport': 'usb', 'ClusterNetworkCodec': 'h264'}
      params = Mock(get=Mock(side_effect=values.get), get_bool=Mock(side_effect=lambda key: key == 'ClusterEnable'))
      params.put.side_effect = lambda key, value, **_kwargs: values.__setitem__(key, value)
      with client, patch.object(core, 'loop', asyncio.get_running_loop()), patch.object(dashboard, 'params', params), \
           patch.object(ui, 'timer'), patch.object(ui, 'notify'), patch.object(dashboard, 'get_connected_pi_ip', return_value=None):
        dashboard.render_tab_toggles()
        transport = next(element for element in client.elements.values()
                         if isinstance(element, ui.select) and element.label == 'Cluster display transport')
        transport.set_value('network')
        await asyncio.sleep(0)
        params.put.assert_called_once_with('ClusterDisplayTransport', 'network', block=True)
        codec = next(element for element in client.elements.values()
                     if isinstance(element, ui.select) and element.label == 'Cluster network codec')
        self.assertEqual(codec.value, 'h264')
        transport = next(element for element in client.elements.values()
                         if isinstance(element, ui.select) and element.label == 'Cluster display transport')
        transport.set_value('usb')
        await asyncio.sleep(0)
        self.assertFalse(any(isinstance(element, ui.select) and element.label == 'Cluster network codec'
                             for element in client.elements.values()))

    asyncio.run(exercise_changes())

  def test_routes_ignore_invalid_names_and_sort_segments_numerically(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      for name in ('old--route--10', 'old--route--2', 'new--route--0', 'boot', 'invalid--name', 'bad--route--tail'):
        path = root / name
        path.mkdir()
        timestamp = 20 if name.startswith('new') else 10
        os.utime(path, (timestamp, timestamp))
      (root / 'file--route--0').touch()
      routes = dashboard.get_routes(root)
      self.assertEqual([route.name for route in routes], ['new--route', 'old--route'])
      self.assertEqual([path.name for path in routes[1].paths], ['old--route--2', 'old--route--10'])
      self.assertEqual(dashboard.get_routes(root / 'missing'), [])

  def test_camera_tab_keeps_three_panels_and_connects_web_device_and_stop_buttons(self):
    client = Client(ui.page('/test-dashboard-camera'))
    self.addCleanup(client.delete)
    with client, patch.object(dashboard, 'params') as params, patch.object(ui, 'run_javascript') as javascript, \
         patch.object(ui, 'notify'):
      dashboard.render_tab_camera()
      params.put_bool.assert_not_called()
      javascript.assert_not_called()
      self.assertFalse(any(isinstance(element, ui.select) for element in client.elements.values()))
      buttons = {element.text: element for element in client.elements.values() if isinstance(element, ui.button)}
      self.assertEqual(list(buttons), ['CAMERA VIEW (WEB)', 'CAMERA VIEW (DEVICE)', 'STOP'])
      panels = next(element for element in client.elements.values() if isinstance(element, ui.html) and '<video' in element.content)
      content = panels.content
      self.assertEqual(content.count('<video'), 3)
      for camera in ('road', 'driver', 'wideRoad'):
        self.assertIn(f'id="dashboard-camera-{camera}-video"', content)
        self.assertIn(f'id="dashboard-camera-{camera}-status"', content)

      def click(text):
        listener = next(listener for listener in buttons[text]._event_listeners.values() if listener.type == 'click')
        listener.handler(None)

      click('CAMERA VIEW (WEB)')
      javascript.assert_called_once_with(dashboard._camera_start_javascript())
      params.put_bool.assert_not_called()
      click('CAMERA VIEW (DEVICE)')
      params.put_bool.assert_called_once_with('CameraPreview', True, block=True)
      params.put_bool.reset_mock()
      javascript.reset_mock()
      click('STOP')
      params.put_bool.assert_called_once_with('CameraPreview', False, block=True)
      javascript.assert_called_once_with(dashboard.CAMERA_STOP_JAVASCRIPT)
      self.assertEqual(panels.content, content)


class TestDashboardCameraSignaling(unittest.TestCase):
  def setUp(self):
    self.session = Mock()
    self.session.get.return_value.ok = True
    self.session.post.return_value.ok = True
    self.session.post.return_value.json.return_value = {'sdp': 'answer-sdp', 'type': 'answer'}
    self.session.__enter__ = Mock(return_value=self.session)
    self.session.__exit__ = Mock(return_value=False)
    self.params = Mock()
    self.params.get_bool.return_value = True
    self.session_patch = patch.object(dashboard.requests, 'Session', return_value=self.session)
    self.params_patch = patch.object(dashboard, 'params', self.params)
    self.session_patch.start()
    self.params_patch.start()
    self.addCleanup(self.session_patch.stop)
    self.addCleanup(self.params_patch.stop)

  def test_offroad_start_uses_loopback_and_video_only_payload(self):
    cameras = ['road', 'driver', 'wideRoad']
    self.assertEqual(dashboard._request_camera_stream(cameras, 'v=0\r\n'), (200, {'sdp': 'answer-sdp', 'type': 'answer'}))
    self.params.put_bool.assert_called_once_with('IsLiveStreaming', True)
    self.assertFalse(self.session.trust_env)
    self.session.get.assert_called_once_with('http://127.0.0.1:5001/schema', timeout=1)
    self.session.post.assert_called_once_with('http://127.0.0.1:5001/stream', json={
      'sdp': 'v=0\r\n', 'cameras': cameras, 'enabled': True, 'bridge_services_in': [], 'bridge_services_out': [],
    }, timeout=(2, 35))

  def test_onroad_start_does_not_change_livestream_parameter(self):
    self.params.get_bool.return_value = False
    dashboard._request_camera_stream(['road'], 'v=0\r\n')
    self.params.put_bool.assert_not_called()

  def test_waits_for_manager_start_then_posts_offer(self):
    self.session.get.side_effect = [dashboard.requests.ConnectionError(), dashboard.requests.Timeout(), Mock(ok=True)]
    with patch.object(dashboard.time, 'sleep') as sleep:
      self.assertEqual(dashboard._request_camera_stream(['driver'], 'v=0')[0], 200)
    self.assertEqual(sleep.call_count, 2)
    self.assertEqual(self.session.post.call_count, 1)

  def test_service_start_has_bounded_failure(self):
    self.session.get.side_effect = dashboard.requests.ConnectionError('private transport detail')
    with patch.object(dashboard.time, 'monotonic', side_effect=[0, 11]):
      status, result = dashboard._request_camera_stream(['road'], 'v=0')
    self.assertEqual(status, 503)
    self.assertNotIn('private transport detail', result['error'])
    self.session.post.assert_not_called()

  def test_negotiation_errors_are_reported_and_invalid_answer_is_rejected(self):
    for error, expected in ((dashboard.requests.Timeout(), 504), (dashboard.requests.ConnectionError(), 502), (ValueError(), 502)):
      with self.subTest(error=error):
        self.session.post.side_effect = error
        self.assertEqual(dashboard._request_camera_stream(['road'], 'v=0')[0], expected)
    self.session.post.side_effect = None
    self.session.post.return_value.ok = False
    self.session.post.return_value.status_code = 500
    self.assertEqual(dashboard._request_camera_stream(['road'], 'v=0')[0], 502)
    self.session.post.return_value.ok = True
    self.session.post.return_value.json.return_value = {'error': 'busy'}
    self.assertEqual(dashboard._request_camera_stream(['road'], 'v=0')[0], 502)


class TestDashboardCameraProxy(unittest.IsolatedAsyncioTestCase):
  async def request(self, body):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = Request({'type': 'http', 'method': 'POST', 'path': dashboard.CAMERA_STREAM_PATH},
                      AsyncMock(return_value={'type': 'http.request', 'body': raw, 'more_body': False}))
    return await dashboard.camera_stream(request)

  async def test_proxy_forwards_only_camera_list_and_sdp(self):
    cameras = ['road', 'driver', 'wideRoad']
    with patch.object(dashboard, '_request_camera_stream', return_value=(200, {'type': 'answer', 'sdp': 'answer'})) as relay:
      response = await self.request({'cameras': cameras, 'sdp': 'v=0', 'bridge_services_in': ['testJoystick'], 'enabled': False})
    self.assertEqual(response.status_code, 200)
    relay.assert_called_once_with(cameras, 'v=0')
    self.assertEqual(json.loads(response.body), {'type': 'answer', 'sdp': 'answer'})

  async def test_http_route_is_available_at_browser_same_origin(self):
    with patch.object(dashboard, '_request_camera_stream', return_value=(200, {'type': 'answer', 'sdp': 'answer'})) as relay:
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=dashboard.app), base_url='http://dashboard:7000') as client:
        response = await client.post('/api/camera/stream', json={'cameras': ['wideRoad'], 'sdp': 'v=0'})
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json(), {'type': 'answer', 'sdp': 'answer'})
    relay.assert_called_once_with(['wideRoad'], 'v=0')

  async def test_proxy_rejects_invalid_requests_before_starting_camera(self):
    for body, status in ((b'not json', 400), ([], 400), ({'camera': 'road', 'sdp': 'v=0'}, 400),
                         ({'cameras': ['cluster'], 'sdp': 'v=0'}, 400), ({'cameras': 'road', 'sdp': 'v=0'}, 400),
                         ({'cameras': [], 'sdp': 'v=0'}, 400), ({'cameras': ['road', 'road'], 'sdp': 'v=0'}, 400),
                         ({'cameras': ['road', 'driver', 'wideRoad', 'road'], 'sdp': 'v=0'}, 400),
                         ({'cameras': [None], 'sdp': 'v=0'}, 400), ({'cameras': [['road']], 'sdp': 'v=0'}, 400),
                         ({'cameras': [123], 'sdp': 'v=0'}, 400), ({'cameras': ['road'], 'sdp': 123}, 400),
                         ({'cameras': ['road'], 'sdp': 'bad'}, 400),
                         ({'cameras': ['road'], 'sdp': 'v=0' + 'x' * dashboard.MAX_CAMERA_SDP}, 400),
                         (b'x' * (dashboard.MAX_CAMERA_SDP + 1025), 413)):
      with self.subTest(body=str(body)[:50]), patch.object(dashboard, '_request_camera_stream') as relay:
        self.assertEqual((await self.request(body)).status_code, status)
        relay.assert_not_called()

  async def test_proxy_preserves_service_failure_status(self):
    with patch.object(dashboard, '_request_camera_stream', return_value=(503, {'error': 'service did not start'})):
      response = await self.request({'cameras': ['road'], 'sdp': 'v=0'})
    self.assertEqual(response.status_code, 503)

  async def test_concurrent_start_requests_are_serialized(self):
    first_started, release_first = asyncio.Event(), asyncio.Event()
    calls = []

    async def relay(_function, cameras, _sdp):
      calls.append(cameras)
      if cameras == ['road']:
        first_started.set()
        await release_first.wait()
      return 200, {'type': 'answer', 'sdp': cameras[0]}

    with patch.object(dashboard, 'CAMERA_STREAM_LOCK', asyncio.Lock()), patch.object(dashboard.asyncio, 'to_thread', relay):
      first = asyncio.create_task(self.request({'cameras': ['road'], 'sdp': 'v=0'}))
      await first_started.wait()
      second = asyncio.create_task(self.request({'cameras': ['driver'], 'sdp': 'v=0'}))
      await asyncio.sleep(0)
      self.assertEqual(calls, [['road']])
      release_first.set()
      await asyncio.gather(first, second)
    self.assertEqual(calls, [['road'], ['driver']])


class TestDashboardClusterActions(unittest.IsolatedAsyncioTestCase):
  async def test_touch_job_runs_after_the_http_reply_and_survives_video_disconnect(self):
    started, finish = asyncio.Event(), asyncio.Event()

    async def runner(_command, _timeout):
      started.set()
      await finish.wait()
      return 0, 'CLUSTER_PI_LOG_WARNING: Pi file retained for retry'

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      (root / 'log_upload.sh').touch()
      (root / 'cluster_debug.log').touch()
      actions = dashboard.ClusterActions(root, root / 'cluster_debug.log', runner=runner)
      with patch.object(dashboard, 'CLUSTER_ACTIONS', actions), \
           patch.object(dashboard, 'get_connected_pi_ip', return_value='192.168.0.84') as peer:
        transport = httpx.ASGITransport(app=dashboard.app, client=('192.168.0.84', 1234))
        async with httpx.AsyncClient(transport=transport, base_url='http://c4:7000') as client:
          payload = {'action': 'upload_logs', 'request_id': 'c' * 32}
          response = await client.post('/api/cluster/actions', json=payload)
          self.assertEqual(response.status_code, 202)
          self.assertEqual(response.json()['state'], 'running')
          await started.wait()
          peer.return_value = None
          repeated = await client.post('/api/cluster/actions', json=payload)
          self.assertEqual(repeated.json()['job_id'], response.json()['job_id'])
          self.assertEqual((await client.get('/api/cluster/actions')).json()['state'], 'running')
          finish.set()
          await asyncio.gather(*actions.tasks)
          result = (await client.get('/api/cluster/actions')).json()
          self.assertEqual(result['state'], 'warning')

  async def test_touch_endpoint_rejects_other_clients_and_arbitrary_targets(self):
    actions = dashboard.ClusterActions(runner=AsyncMock())
    with patch.object(dashboard, 'CLUSTER_ACTIONS', actions), \
         patch.object(dashboard, 'get_connected_pi_ip', return_value='192.168.0.84'):
      transport = httpx.ASGITransport(app=dashboard.app, client=('192.168.0.99', 1234))
      async with httpx.AsyncClient(transport=transport, base_url='http://c4:7000') as client:
        self.assertEqual((await client.get('/api/cluster/actions')).status_code, 403)
        payload = {'action': 'pi_update', 'request_id': 'd' * 32}
        self.assertEqual((await client.post('/api/cluster/actions', json=payload)).status_code, 403)
        payload['host'] = '192.168.0.84'
        self.assertEqual((await client.post('/api/cluster/actions', json=payload)).status_code, 400)
        self.assertEqual((await client.post('/api/cluster/actions', content=b'x' * 1025)).status_code, 413)
    actions.runner.assert_not_called()


@unittest.skipUnless(shutil.which('node'), 'Node is needed for the mocked browser regression tests')
class TestDashboardCameraJavascript(unittest.TestCase):
  def run_browser(self, scenario):
    # Execute the actual UI code with mocked browser APIs. No sockets or camera.
    harness = r'''
      const assert = require('node:assert/strict');
      const scenario = process.argv[1];
      const timers = new Map(); let nextTimer = 0;
      global.setTimeout = (fn, ms) => {timers.set(++nextTimer, {fn, ms}); return nextTimer;};
      global.clearTimeout = id => timers.delete(id);
      global.requestAnimationFrame = fn => fn();
      global.window = {};
      const cameras = ['road', 'driver', 'wideRoad'];
      const statuses = Object.fromEntries(cameras.map(camera => [camera, {innerText: '', style: {}}]));
      const videos = Object.fromEntries(cameras.map(camera => {
        const video = {srcObject: null, play: async () => video.onplaying()};
        return [camera, video];
      }));
      const elements = Object.fromEntries(cameras.flatMap(camera => [
        [`dashboard-camera-${camera}-video`, videos[camera]], [`dashboard-camera-${camera}-status`, statuses[camera]],
      ]));
      global.document = {getElementById: id => elements[id], querySelectorAll: () => Object.values(statuses)};
      let pc, fetches = 0, body, url, fetchSignal;
      const peers = [], received = [];
      const track = camera => ({id: `${camera}:track-${received.length}`, kind: 'video', stopped: 0, stop() {this.stopped++;}});
      global.MediaStream = class {constructor(tracks) {this.tracks = tracks;} getTracks() {return this.tracks;}};
      const sendTracks = peer => {
        const tracks = Object.fromEntries(cameras.map(camera => [camera, track(camera)]));
        received.push(tracks);
        const sharedStream = new MediaStream(Object.values(tracks));
        for (const camera of ['wideRoad', 'road', 'driver']) peer.ontrack({streams: [sharedStream], track: tracks[camera]});
      };
      global.RTCPeerConnection = class {
        constructor() {
          if (scenario === 'constructor_fail') throw new Error('WebRTC initialization failed');
          pc = this; peers.push(this); this.transceivers = []; this.listeners = {};
          this.iceGatheringState = 'new'; this.connectionState = 'new';
        }
        addTransceiver(kind, options) {
          const transceiver = {kind, ...options}; this.transceivers.push(transceiver); return transceiver;
        }
        async createOffer() {return {type:'offer', sdp: scenario === 'no_h264' ? 'v=0\r\na=rtpmap:96 VP8/90000' : 'v=0\r\na=rtpmap:96 H264/90000'};}
        async setLocalDescription(offer) {
          this.localDescription = offer;
          this.iceGatheringState = ['late_ice', 'ice_timeout', 'stop'].includes(scenario) ? 'gathering' : 'complete';
        }
        addEventListener(name, callback) {this.listeners[name] = callback;}
        removeEventListener(name, callback) {if (this.listeners[name] === callback) delete this.listeners[name];}
        async setRemoteDescription(answer) {
          this.answer = answer;
          if (['playing', 'stop_tracks', 'failed', 'replace'].includes(scenario)) sendTracks(this);
        }
        close() {this.closed = true;}
      };
      global.fetch = async (requestUrl, options) => {
        fetches++; url = requestUrl; body = JSON.parse(options.body); fetchSignal = options.signal;
        if (scenario === 'stop_fetch') return await new Promise((resolve, reject) => {
          options.signal.addEventListener('abort', () => reject(Object.assign(new Error('Aborted'), {name: 'AbortError'})));
        });
        return {ok: scenario !== 'service_fail', status:503, json:async () => scenario === 'service_fail'
          ? {error:'Camera streaming service did not start.'} : {type:'answer', sdp:'answer'}};
      };
    '''
    checks = r'''
      (async () => {
        const started = eval(SCRIPT);
        for (let i=0; i<30; i++) await Promise.resolve();
        if (scenario === 'late_ice') {
          assert.equal(fetches, 0);
          assert.equal([...timers.values()].some(t => t.ms === 2000), false);
          pc.localDescription.sdp += '\r\na=candidate:late-host';
          pc.iceGatheringState = 'complete'; pc.listeners.icegatheringstatechange();
        } else if (scenario === 'ice_timeout') {
          [...timers.values()].find(t => t.ms === 10000).fn();
        } else if (['stop', 'stop_fetch'].includes(scenario)) {
          eval(STOP);
        }
        await started;
        assert.equal(timers.size, 0);
        if (fetches) {
          assert.equal(url, '/api/camera/stream');
          assert.deepEqual(body.cameras, cameras);
          assert.equal(Object.hasOwn(body, 'camera'), false);
        }
        if (pc) assert.deepEqual(pc.transceivers.map(t => [t.kind, t.direction]), cameras.map(() => ['video', 'recvonly']));
        assert.equal(peers.length, scenario === 'constructor_fail' ? 0 : 1);
        if (scenario === 'late_ice') assert.match(body.sdp, /late-host/);
        if (scenario === 'playing') {
          for (const camera of cameras) {
            assert.equal(statuses[camera].innerText, '● Stream Active');
            assert.deepEqual(videos[camera].srcObject.getTracks(), [received[0][camera]]);
          }
          videos.road.onwaiting();
          assert.match(statuses.road.innerText, /Waiting/);
          assert.equal(statuses.driver.innerText, '● Stream Active');
          assert.equal(statuses.wideRoad.innerText, '● Stream Active');
        }
        if (scenario === 'failed') {
          pc.connectionState = 'failed'; pc.onconnectionstatechange();
          for (const status of Object.values(statuses)) assert.match(status.innerText, /Connection failed/);
          assert.equal(pc.closed, true);
        }
        if (scenario === 'stop') {
          assert.equal(fetches, 0); assert.equal(pc.closed, true);
          assert.equal(Object.keys(pc.listeners).length, 0);
        }
        if (scenario === 'stop_fetch') {
          assert.equal(fetches, 1); assert.equal(pc.closed, true); assert.equal(fetchSignal.aborted, true);
          assert.equal(pc.answer, undefined);
        }
        if (scenario === 'stop_tracks') eval(STOP);
        if (['stop_tracks', 'failed'].includes(scenario)) {
          for (const camera of cameras) {
            assert.equal(received[0][camera].stopped, 1);
            assert.equal(videos[camera].srcObject, null);
          }
          assert.equal(pc.closed, true);
        }
        if (scenario === 'late_track') {
          eval(STOP);
          const late = track('road'); pc.ontrack({streams: [], track: late});
          assert.equal(late.stopped, 1); assert.equal(videos.road.srcObject, null);
        }
        if (scenario === 'replace') {
          const first = pc;
          await eval(SCRIPT);
          assert.equal(first.closed, true); assert.equal(peers.length, 2);
          for (const camera of cameras) {
            assert.equal(received[0][camera].stopped, 1);
            assert.deepEqual(videos[camera].srcObject.getTracks(), [received[1][camera]]);
          }
          const stale = track('driver'); first.ontrack({streams: [], track: stale});
          assert.equal(stale.stopped, 1);
          assert.deepEqual(videos.driver.srcObject.getTracks(), [received[1].driver]);
          eval(STOP);
        }
        if (scenario === 'ice_timeout') {
          assert.equal(fetches, 0);
          for (const status of Object.values(statuses)) assert.match(status.innerText, /discovery timed out/);
        }
        if (scenario === 'no_h264') {
          assert.equal(fetches, 0);
          for (const status of Object.values(statuses)) assert.match(status.innerText, /H.264/);
        }
        if (scenario === 'constructor_fail') for (const status of Object.values(statuses)) assert.match(status.innerText, /initialization failed/);
        if (scenario === 'service_fail') for (const status of Object.values(statuses)) assert.match(status.innerText, /did not start/);
        assert.equal(timers.size, 0);
      })().catch(error => {console.error(error); process.exitCode = 1;});
    '''
    code = harness + '\nconst SCRIPT = ' + json.dumps(dashboard._camera_start_javascript()) + ';\n'
    code += 'const STOP = ' + json.dumps(dashboard.CAMERA_STOP_JAVASCRIPT) + ';\n' + checks
    result = subprocess.run([shutil.which('node'), '-e', code, scenario], capture_output=True, text=True, timeout=10)
    self.assertEqual(result.returncode, 0, result.stderr)

  def test_late_ice_candidates_are_included_in_offer(self):
    self.run_browser('late_ice')

  def test_ice_timeout_does_not_post_incomplete_offer(self):
    self.run_browser('ice_timeout')

  def test_out_of_order_tracks_use_separate_camera_streams_and_playback_states(self):
    self.run_browser('playing')

  def test_connection_failure_clears_active_state_and_closes_peer(self):
    self.run_browser('failed')

  def test_stop_during_ice_gathering_cancels_pending_start(self):
    self.run_browser('stop')

  def test_stop_aborts_pending_signaling_and_does_not_apply_answer(self):
    self.run_browser('stop_fetch')

  def test_stop_releases_tracks_for_all_three_cameras(self):
    self.run_browser('stop_tracks')

  def test_late_track_after_stop_is_released(self):
    self.run_browser('late_track')

  def test_restart_releases_old_session_and_ignores_its_late_tracks(self):
    self.run_browser('replace')

  def test_missing_codec_and_constructor_errors_are_visible(self):
    self.run_browser('no_h264')
    self.run_browser('constructor_fail')

  def test_proxy_error_is_visible(self):
    self.run_browser('service_fail')


class TestDashboardAsync(unittest.IsolatedAsyncioTestCase):
  async def test_command_captures_stdout_and_stderr(self):
    snapshots = []
    code, output = await dashboard._run_command(
      [sys.executable, '-c', "import sys; print('stdout', flush=True); print('stderr', file=sys.stderr); sys.exit(7)"],
      snapshots.append,
    )
    self.assertEqual(code, 7)
    self.assertIn('stdout', output)
    self.assertIn('stderr', output)
    self.assertTrue(snapshots)

  async def test_large_output_without_newlines_is_bounded(self):
    with patch.object(dashboard, 'MAX_COMMAND_OUTPUT', 1024):
      code, output = await dashboard._run_command([sys.executable, '-c', "print('x' * 200000, end='TAIL')"])
    self.assertEqual(code, 0)
    self.assertEqual(len(output), 1024)
    self.assertTrue(output.endswith('TAIL'))

  async def test_split_utf8_output_is_decoded_correctly(self):
    process = Mock(returncode=0)
    process.stdout.read = AsyncMock(side_effect=[b'\xed', b'\x95\x9c', b''])
    process.wait = AsyncMock(return_value=0)
    with patch.object(dashboard.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process)):
      code, output = await dashboard._run_command(['mock'])
    self.assertEqual((code, output), (0, '한'))

  async def test_cancellation_reaps_process(self):
    created = asyncio.Event()
    processes = []
    create = asyncio.create_subprocess_exec

    async def record_process(*args, **kwargs):
      process = await create(*args, **kwargs)
      processes.append(process)
      created.set()
      return process

    with patch.object(dashboard.asyncio, 'create_subprocess_exec', record_process):
      task = asyncio.create_task(dashboard._run_command([sys.executable, '-c', 'import time; time.sleep(60)']))
      await asyncio.wait_for(created.wait(), 5)
      task.cancel()
      with self.assertRaises(asyncio.CancelledError):
        await task
    self.assertIsNotNone(processes[0].returncode)

  async def test_tmux_capture_failure_does_not_overwrite_previous_log(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / 'tmux_console.log'
      path.write_text('previous capture', encoding='utf-8')
      with patch.object(dashboard, 'TMUX_LOG_PATH', path), \
           patch.object(dashboard, '_run_command', AsyncMock(side_effect=[(0, ''), (1, 'no server running')])):
        with self.assertRaisesRegex(RuntimeError, 'no server running'):
          await dashboard.save_tmux_log()
      self.assertEqual(path.read_text(encoding='utf-8'), 'previous capture')

  async def test_tmux_capture_saves_only_successful_output(self):
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / 'tmux_console.log'
      with patch.object(dashboard, 'TMUX_LOG_PATH', path), \
           patch.object(dashboard, '_run_command', AsyncMock(side_effect=[(0, ''), (0, 'captured')])) as command:
        self.assertEqual(await dashboard.save_tmux_log(), path)
      self.assertEqual(path.read_text(encoding='utf-8'), 'captured')
      self.assertEqual(command.call_args.args[0][-1], '-500')

  async def test_notifications_include_stdout_errors(self):
    with patch.object(dashboard, '_run_command', AsyncMock(return_value=(1, 'ERROR: file not found'))), \
         patch.object(ui, 'notify') as notify:
      self.assertEqual(await dashboard.run_script_async('Upload', '/tmp/test.sh'), 1)
      self.assertIn('ERROR: file not found', notify.call_args.args[0])

  async def test_partial_cluster_upload_reports_warning_without_failing_c4_upload(self):
    warning = 'C4 로그 업로드 완료. Pi 로그 수집에 실패했습니다.'
    with patch.object(dashboard, '_run_command', AsyncMock(return_value=(0, f'uploaded\nCLUSTER_PI_LOG_WARNING: {warning}\n'))), \
         patch.object(ui, 'notify') as notify:
      self.assertEqual(await dashboard.run_script_async('Log Upload', '/data/openpilot/scripts/log_upload.sh'), 0)
      self.assertIn(warning, notify.call_args.args[0])
      self.assertEqual(notify.call_args.kwargs['type'], 'warning')

  async def test_complete_cluster_upload_reports_success(self):
    with patch.object(dashboard, '_run_command', AsyncMock(return_value=(0, 'C4 and Pi logs uploaded'))), \
         patch.object(ui, 'notify') as notify:
      self.assertEqual(await dashboard.run_script_async('Log Upload', '/data/openpilot/scripts/log_upload.sh'), 0)
      self.assertEqual(notify.call_args.kwargs['type'], 'positive')

  async def test_pi_warning_marker_does_not_change_other_scripts(self):
    with patch.object(dashboard, '_run_command', AsyncMock(return_value=(0, 'CLUSTER_PI_LOG_WARNING: unrelated output'))), \
         patch.object(ui, 'notify') as notify:
      self.assertEqual(await dashboard.run_script_async('Other', '/tmp/other.sh'), 0)
      self.assertEqual(notify.call_args.kwargs['type'], 'positive')

  async def test_git_pull_modal_does_not_reboot(self):
    client = Client(ui.page('/test-dashboard-modal'))
    self.addCleanup(client.delete)
    with client, patch.object(ui, 'run_javascript'), \
         patch.object(dashboard, '_run_command', AsyncMock(return_value=(0, 'tmux started'))), \
         patch.object(dashboard.subprocess, 'Popen') as popen:
      code = await dashboard.run_script_async('Git Pull', '/tmp/gitpull.sh', show_modal=True)
    self.assertEqual(code, 0)
    popen.assert_not_called()
    self.assertTrue(any('tmux에서 업데이트를 시작했습니다.' in element.content for element in client.elements.values() if isinstance(element, ui.html)))

  async def test_page_builds_with_stale_selections_and_byte_params(self):
    client = Client(ui.page('/test-dashboard-page'))
    self.addCleanup(client.delete)
    values = {'SelectedManufacturer': b'HYUNDAI', 'SelectedCar': 'removed car', 'SelectedBranch': 'removed branch', 'DevicePosition': '<device>'}
    params = Mock(get=Mock(side_effect=values.get), get_bool=Mock(return_value=False))
    with client, patch.object(dashboard, 'params', params), patch.object(ui, 'run_javascript'), patch.object(ui, 'timer'), \
         patch.object(dashboard, 'get_list_from_file', return_value=[]), patch.object(dashboard, 'get_routes', return_value=[]):
      dashboard.main_page()
    selects = [element for element in client.elements.values() if isinstance(element, ui.select)]
    self.assertEqual([element.value for element in selects[:3]], ['HYUNDAI', '[ Not Selected ]', '[ Not Selected ]'])
    self.assertTrue(any('&lt;device&gt;' in element.content for element in client.elements.values() if isinstance(element, ui.html)))


if __name__ == '__main__':
  unittest.main()
