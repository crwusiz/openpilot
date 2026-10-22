from nicegui import ui


DRIVES_HTML = '''
  <section id="dashboard-drives" class="w-full mt-4" aria-label="저장된 주행영상">
    <p class="text-blue-200 text-lg font-bold">저장된 주행영상</p>
    <p class="text-slate-300 mb-4">기기에 저장된 전방 주행영상(qcamera)을 재생합니다. 주행과 구간을 선택한 후 재생을 누르세요.
      구간이 끝나면 같은 주행의 다음 재생 가능한 구간으로 이어집니다. 녹화 중인 구간은 완료 후 새로고침하세요.</p>
    <div class="drives-selectors">
      <label>주행 (날짜 · 이름 · 구간 수)<select id="dashboard-drives-route" disabled aria-label="주행 선택"></select></label>
      <label>구간<select id="dashboard-drives-segment" disabled aria-label="구간 선택"></select></label>
    </div>
    <div class="drives-controls">
      <button id="dashboard-drives-refresh" type="button" class="custom-btn btn-restart">새로고침</button>
      <button id="dashboard-drives-play" type="button" class="custom-btn btn-green-start" disabled>재생</button>
      <button id="dashboard-drives-stop" type="button" class="custom-btn btn-red-stop">중지</button>
    </div>
    <p id="dashboard-drives-warning" class="text-amber-200 mt-3" hidden></p>
    <p id="dashboard-drives-status" class="text-slate-300 my-3" role="status" aria-live="polite">주행영상 목록을 불러오는 중입니다.</p>
    <video id="dashboard-drives-video" controls playsinline preload="metadata" aria-label="주행영상 재생"></video>
  </section>
'''

DRIVES_STYLES = '''
  <style>
    #dashboard-drives .drives-selectors { display: grid; grid-template-columns: minmax(0, 3fr) minmax(100px, 1fr); gap: 12px; }
    #dashboard-drives label { display: block; min-width: 0; color: #93C5FD; font-weight: 600; }
    #dashboard-drives select {
      display: block; width: 100%; min-height: 56px; margin-top: 6px; padding: 8px 16px;
      border: 1.5px solid #3A4A6B; border-radius: 20px; background: #1A2235; color: #E8EEFF;
      text-overflow: ellipsis; font: inherit; color-scheme: dark;
    }
    #dashboard-drives .drives-controls { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 16px; }
    #dashboard-drives button { cursor: pointer; }
    #dashboard-drives button:disabled, #dashboard-drives select:disabled { opacity: .45; cursor: default; }
    #dashboard-drives-status { overflow-wrap: anywhere; }
    #dashboard-drives video { display: block; width: 100%; max-height: 65vh; background: #000; border: 1px solid #3A4A6B; border-radius: 12px; }
    @media (max-width: 600px) {
      #dashboard-drives .drives-selectors { grid-template-columns: minmax(0, 1fr); }
    }
  </style>
'''

DRIVES_INIT_JAVASCRIPT = r'''
  (async () => {
    const initGeneration = (window.dashboardDrivesInit || 0) + 1;
    window.dashboardDrivesInit = initGeneration;
    // Quasar mounts tab content lazily, after the tab-change event arrives.
    let root;
    for (let attempt = 0; attempt < 60; attempt++) {
      if (window.dashboardDrivesInit !== initGeneration) return;
      root = document.getElementById('dashboard-drives');
      if (root) break;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    if (!root || window.dashboardDrivesInit !== initGeneration) return;
    if (window.dashboardDrives?.root === root) {
      window.dashboardDrives.refresh();
      return;
    }
    window.dashboardDrives?.dispose();
    const element = name => root.querySelector(`#dashboard-drives-${name}`);
    const routeSelect = element('route'), segmentSelect = element('segment'), video = element('video');
    const playButton = element('play'), status = element('status'), warning = element('warning');
    let routes = [], generation = 0, controller = null, loading = false, preparing = false, activeName = null;
    const listeners = [];
    const listen = (target, name, callback) => {
      target.addEventListener(name, callback);
      listeners.push(() => target.removeEventListener(name, callback));
    };
    const selectedRoute = () => routes.find(route => route.name === routeSelect.value);
    const selectedSegment = () => selectedRoute()?.segments.find(segment => segment.name === segmentSelect.value);
    const message = (text, error = false) => {
      status.textContent = text;
      status.style.color = error ? '#FCA5A5' : '';
    };
    const updateControls = () => {
      routeSelect.disabled = loading || !routes.length;
      segmentSelect.disabled = loading || !selectedRoute()?.segments.some(segment => !segment.recording);
      playButton.disabled = loading || preparing || !selectedSegment() || selectedSegment().recording;
    };
    const stop = (text = '재생을 중지했습니다.') => {
      generation++;
      controller?.abort();
      controller = null;
      loading = preparing = false;
      activeName = null;
      video.pause();
      video.removeAttribute('src');
      video.load();
      updateControls();
      if (text !== null) message(text);
    };
    const request = () => {
      const id = ++generation;
      controller = new AbortController();
      return {signal: controller.signal, current: () => generation === id && root.isConnected};
    };
    const getJson = async (url, pending) => {
      const response = await fetch(url, {signal: pending.signal, cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `요청에 실패했습니다. (HTTP ${response.status})`);
      return data;
    };
    const fillSegments = preferred => {
      segmentSelect.replaceChildren();
      for (const segment of selectedRoute()?.segments || []) {
        // Option text is escaped by the DOM, including names supplied by the device.
        const option = new Option(`${segment.index}번 구간${segment.recording ? ' · 녹화 중' : ''}`, segment.name);
        option.disabled = segment.recording;
        segmentSelect.add(option);
      }
      const available = selectedRoute()?.segments.filter(segment => !segment.recording) || [];
      segmentSelect.value = available.find(segment => segment.name === preferred)?.name || available[0]?.name || '';
      updateControls();
    };
    const ready = () => message(!routes.length ? '저장된 주행영상이 없습니다.'
      : selectedSegment() ? '구간을 선택한 후 재생을 누르세요.' : '이 주행의 영상은 녹화 중입니다. 완료 후 새로고침하세요.');
    const refresh = async () => {
      const previousRoute = routeSelect.value, previousSegment = segmentSelect.value;
      stop(null);
      const pending = request();
      loading = true;
      updateControls();
      message('주행영상 목록을 불러오는 중입니다.');
      try {
        const data = await getJson('/api/drives', pending);
        if (!pending.current()) return;
        routes = data.routes.map(route => ({...route, segments: [...route.segments].sort((a, b) => a.index - b.index)}));
        routeSelect.replaceChildren();
        for (const route of routes) {
          const date = new Date(route.modified * 1000).toLocaleString('ko-KR', {hour12: false});
          routeSelect.add(new Option(`${date} · ${route.name} (${route.segments.length}개 구간)`, route.name));
        }
        routeSelect.value = routes.find(route => route.name === previousRoute)?.name || routes[0]?.name || '';
        loading = false;
        fillSegments(previousSegment);
        warning.hidden = data.ffmpeg_available !== false;
        warning.textContent = warning.hidden ? '' : '기기에 ffmpeg가 없어 TS 영상을 준비할 수 없습니다. MP4 영상은 재생할 수 있습니다.';
        ready();
      } catch (error) {
        if (!pending.current()) return;
        loading = false;
        updateControls();
        message(`목록을 불러오지 못했습니다: ${error.message}`, true);
      }
    };
    const play = async () => {
      const segment = selectedSegment();
      if (!segment || segment.recording) return;
      stop(null);
      const pending = request();
      preparing = true;
      updateControls();
      message(`${segment.index}번 구간을 준비하는 중입니다. 처음 재생할 때는 잠시 걸릴 수 있습니다.`);
      try {
        const data = await getJson(`/api/drives/prepare/${encodeURIComponent(segment.name)}`, pending);
        if (!pending.current()) return;
        const source = new URL(data.url, window.location.href);
        if (source.origin !== window.location.origin || !source.pathname.startsWith('/api/drives/video/')) {
          throw new Error('영상 주소가 올바르지 않습니다.');
        }
        activeName = segment.name;
        video.src = source.href;
        preparing = false;
        updateControls();
        await video.play();
        if (pending.current()) message(`${segment.index}번 구간 재생 중`);
      } catch (error) {
        if (!pending.current()) return;
        if (error.name === 'NotAllowedError') {
          message('영상의 재생 버튼을 눌러 재생하세요.');
        } else {
          stop(null);
          message(`영상을 재생하지 못했습니다: ${error.message}`, true);
        }
      }
    };
    const session = {root, stop, refresh, dispose: () => {
      stop(null);
      listeners.forEach(remove => remove());
    }};
    window.dashboardDrives = session;
    listen(element('refresh'), 'click', refresh);
    listen(playButton, 'click', play);
    listen(element('stop'), 'click', () => stop());
    listen(routeSelect, 'change', () => { stop(null); fillSegments(); ready(); });
    listen(segmentSelect, 'change', () => { stop(null); ready(); });
    listen(video, 'ended', () => {
      if (!activeName || selectedSegment()?.name !== activeName) return;
      const next = selectedRoute().segments.find(segment => segment.index > selectedSegment().index && !segment.recording);
      if (next) {
        segmentSelect.value = next.name;
        play();
      } else {
        message('이 주행의 마지막 재생 가능한 구간입니다.');
      }
    });
    listen(video, 'playing', () => { if (activeName) message(`${selectedSegment().index}번 구간 재생 중`); });
    listen(video, 'error', () => {
      if (!activeName) return;
      const code = video.error?.code;
      stop(null);
      message(code === 4 ? '브라우저가 이 영상 형식을 지원하지 않습니다. 다른 브라우저에서 재생해 보세요.'
        : '영상을 불러오지 못했습니다. 연결 상태를 확인하고 목록을 새로고침하세요.', true);
    });
    for (const name of ['pagehide', 'popstate', 'beforeunload']) listen(window, name, () => stop());
    await refresh();
  })();
'''

DRIVES_STOP_JAVASCRIPT = '''
  window.dashboardDrivesInit = (window.dashboardDrivesInit || 0) + 1;
  window.dashboardDrives?.stop();
'''


def render_tab_drives(tabs):
  ui.add_head_html(DRIVES_STYLES)
  ui.html(DRIVES_HTML).classes('w-full')

  def initialize():
    if tabs.value == 'DRIVES':
      ui.run_javascript(DRIVES_INIT_JAVASCRIPT)
    else:
      ui.run_javascript(DRIVES_STOP_JAVASCRIPT)

  tabs.on_value_change(lambda _event: initialize())
  ui.timer(0.1, initialize, once=True)
