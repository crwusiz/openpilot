import shutil
import subprocess
import unittest

from openpilot.selfdrive.addon.dashboard_drives import DRIVES_INIT_JAVASCRIPT, DRIVES_STOP_JAVASCRIPT


@unittest.skipUnless(shutil.which('node'), 'Node is needed for the browser lifecycle tests')
class TestDashboardDrivesJavascript(unittest.TestCase):
  def run_browser(self, scenario):
    harness = r'''
      const assert = require('node:assert/strict');
      const script = process.argv[1], scenario = process.argv[2], stopScript = process.argv[3];
      class Target {
        constructor() { this.listeners = new Map(); this.style = {}; this.value = ''; this.options = []; this.isConnected = true; this.src = ''; }
        addEventListener(name, callback) {
          if (!this.listeners.has(name)) this.listeners.set(name, new Set());
          this.listeners.get(name).add(callback);
        }
        removeEventListener(name, callback) { this.listeners.get(name)?.delete(callback); }
        async emit(name) { await Promise.all([...this.listeners.get(name) || []].map(callback => callback())); }
        replaceChildren() { this.options = []; this.value = ''; }
        add(option) { this.options.push(option); }
        pause() { this.pauses = (this.pauses || 0) + 1; }
        load() { this.loads = (this.loads || 0) + 1; }
        removeAttribute(name) { if (name === 'src') this.src = ''; }
        async play() { this.plays = (this.plays || 0) + 1; }
      }
      const nodes = Object.fromEntries(['route', 'segment', 'play', 'stop', 'refresh', 'video', 'status', 'warning'].map(name => [name, new Target()]));
      const root = new Target();
      root.querySelector = selector => nodes[selector.replace('#dashboard-drives-', '')];
      globalThis.window = new Target();
      window.location = {origin: 'http://dashboard:7000', href: 'http://dashboard:7000/'};
      let mounted = scenario !== 'lazy_leave';
      globalThis.document = {getElementById: name => mounted && name === 'dashboard-drives' ? root : null};
      globalThis.Option = class { constructor(text, value) { this.text = text; this.value = value; this.disabled = false; } };
      const segments = [
        {name: 'one--route--10', index: 10, recording: false},
        {name: 'one--route--3', index: 3, recording: true},
        {name: 'one--route--2', index: 2, recording: false},
      ];
      const data = {routes: [
        {name: 'one--route', modified: 42, segments},
        {name: '<img src=x onerror=alert(1)>', modified: 41, segments: [{name: 'two--route--0', index: 0, recording: false}]},
      ], ffmpeg_available: true};
      const response = (body, ok = true) => ({ok, status: ok ? 200 : 503, json: async () => body});
      const calls = [];
      const defaultFetch = async url => response(url === '/api/drives' ? data : {url: url.replace('/prepare/', '/video/')});
      let fetchHandler = defaultFetch;
      globalThis.fetch = async (url, options) => { calls.push({url, options}); return await fetchHandler(url, options); };
      const tick = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };
      const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return {resolve, promise}; };
      const init = () => eval(script);
      (async () => {
        if (scenario === 'lazy_leave') {
          const initialization = init();
          await tick();
          eval(stopScript);
          mounted = true;
          await initialization;
          assert.equal(window.dashboardDrives, undefined);
          assert.equal(calls.length, 0);
          await init();
          assert.equal(calls.length, 1);
          return;
        }
        await init();
        if (scenario === 'open') {
          assert.equal(nodes.video.plays || 0, 0);
          assert.equal(nodes.video.src, '');
          assert.equal(nodes.segment.value, 'one--route--2');
          assert.deepEqual(nodes.segment.options.map(option => option.value), ['one--route--2', 'one--route--3', 'one--route--10']);
          assert.equal(nodes.segment.options[1].disabled, true);
          assert.ok(nodes.route.options[1].text.includes('<img src=x onerror=alert(1)>'));
          assert.equal(calls.length, 1);
          await init();
          assert.equal(nodes.play.listeners.get('click').size, 1);
          assert.equal(window.listeners.get('pagehide').size, 1);
          assert.equal(nodes.video.plays || 0, 0);
        } else if (scenario === 'advance') {
          await nodes.play.emit('click');
          assert.equal(nodes.video.src, 'http://dashboard:7000/api/drives/video/one--route--2');
          await nodes.video.emit('ended');
          await tick();
          assert.equal(nodes.segment.value, 'one--route--10');
          assert.equal(nodes.video.src, 'http://dashboard:7000/api/drives/video/one--route--10');
          const playCount = nodes.video.plays;
          await nodes.video.emit('ended');
          await tick();
          assert.equal(nodes.video.plays, playCount);
          assert.equal(nodes.route.value, 'one--route');
          assert.ok(nodes.status.textContent.includes('마지막'));
          eval(stopScript);
          assert.equal(nodes.video.src, '');
          assert.ok(nodes.status.textContent.includes('중지'));
        } else if (scenario === 'stale_prepare') {
          const first = deferred();
          fetchHandler = (url, options) => url.endsWith('--2') ? first.promise : defaultFetch(url, options);
          const firstPlay = nodes.play.emit('click');
          await tick();
          const firstSignal = calls.at(-1).options.signal;
          nodes.segment.value = 'one--route--10';
          await nodes.segment.emit('change');
          assert.equal(firstSignal.aborted, true);
          await nodes.play.emit('click');
          first.resolve(response({url: '/api/drives/video/one--route--2'}));
          await firstPlay;
          assert.ok(nodes.video.src.endsWith('--10'));
          assert.equal(nodes.video.plays, 1);
          const loads = nodes.video.loads;
          await window.emit('pagehide');
          assert.equal(nodes.video.src, '');
          assert.ok(nodes.video.loads > loads);
          const second = deferred();
          fetchHandler = () => second.promise;
          const stoppedPlay = nodes.play.emit('click');
          await tick();
          await nodes.stop.emit('click');
          second.resolve(response({url: '/api/drives/video/one--route--10'}));
          await stoppedPlay;
          assert.equal(nodes.video.src, '');
          assert.equal(nodes.video.plays, 1);
        } else if (scenario === 'refresh_race') {
          await nodes.play.emit('click');
          const first = deferred();
          let refreshes = 0;
          fetchHandler = () => ++refreshes === 1 ? first.promise : Promise.resolve(response({...data, routes: [data.routes[1]]}));
          const oldRefresh = nodes.refresh.emit('click');
          await tick();
          const oldSignal = calls.at(-1).options.signal;
          await nodes.refresh.emit('click');
          assert.equal(oldSignal.aborted, true);
          first.resolve(response(data));
          await oldRefresh;
          assert.equal(nodes.route.options.length, 1);
          assert.equal(nodes.segment.value, 'two--route--0');
          assert.equal(nodes.video.src, '');
          assert.equal(nodes.play.disabled, false);
        } else if (scenario === 'errors') {
          fetchHandler = async url => response(url === '/api/drives' ? {...data, ffmpeg_available: false}
            : {error: 'ffmpeg가 필요합니다.'}, url === '/api/drives');
          await nodes.refresh.emit('click');
          assert.equal(nodes.warning.hidden, false);
          await nodes.play.emit('click');
          assert.ok(nodes.status.textContent.includes('ffmpeg가 필요합니다.'));
          assert.equal(nodes.video.src, '');
          assert.equal(nodes.play.disabled, false);
          fetchHandler = defaultFetch;
          await nodes.play.emit('click');
          nodes.video.error = {code: 4};
          await nodes.video.emit('error');
          assert.ok(nodes.status.textContent.includes('브라우저'));
          assert.equal(nodes.video.src, '');
          nodes.video.play = async () => { const error = new Error(); error.name = 'NotAllowedError'; throw error; };
          await nodes.play.emit('click');
          assert.ok(nodes.status.textContent.includes('재생 버튼'));
          assert.ok(nodes.video.src);
        } else {
          throw new Error(`Unknown test scenario: ${scenario}`);
        }
      })().catch(error => { console.error(error); process.exitCode = 1; });
    '''
    result = subprocess.run([shutil.which('node'), '-e', harness, DRIVES_INIT_JAVASCRIPT, scenario, DRIVES_STOP_JAVASCRIPT],
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_open_lists_without_playing_and_reinitialization_keeps_one_listener(self):
    self.run_browser('open')

  def test_ended_advances_numerically_skips_recording_and_stays_in_same_route(self):
    self.run_browser('advance')

  def test_selection_stop_and_pagehide_cancel_pending_playback(self):
    self.run_browser('stale_prepare')

  def test_latest_refresh_wins_and_releases_current_video(self):
    self.run_browser('refresh_race')

  def test_server_codec_and_autoplay_errors_are_actionable(self):
    self.run_browser('errors')

  def test_leave_before_lazy_mount_cancels_initialization_and_allows_reentry(self):
    self.run_browser('lazy_leave')
