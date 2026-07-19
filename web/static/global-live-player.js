/**
 * Diagnostic log for the mobile lock-screen/CarPlay station-skip
 * investigation. Persisted to localStorage (survives the app being fully
 * killed while backgrounded, unlike sessionStorage) so a failed skip can be
 * inspected afterward via /?debug=1. Remove once that issue is resolved.
 */
const AlchemyDiag = {
  KEY: 'alchemyfm-diag-log',
  MAX_ENTRIES: 250,

  log(event, data = {}) {
    try {
      const entries = this._read();
      entries.push({
        t: new Date().toISOString(),
        event,
        hidden: typeof document !== 'undefined' ? document.hidden : null,
        visibility: typeof document !== 'undefined' ? document.visibilityState : null,
        ...data,
      });
      while (entries.length > this.MAX_ENTRIES) entries.shift();
      localStorage.setItem(this.KEY, JSON.stringify(entries));
    } catch {}
  },

  _read() {
    try {
      const raw = localStorage.getItem(this.KEY);
      const data = raw ? JSON.parse(raw) : [];
      return Array.isArray(data) ? data : [];
    } catch {
      return [];
    }
  },

  dump() {
    return JSON.stringify(this._read(), null, 2);
  },

  clear() {
    try {
      localStorage.removeItem(this.KEY);
    } catch {}
  },
};

/**
 * Shared live stream audio + bottom mini-player for listener pages.
 * Persists tune-in across navigation via localStorage + soft navigation --
 * sessionStorage was tried first but is destroyed the moment the tab/app is
 * actually closed, not just backgrounded, which defeats "show my last
 * station when I reopen the app" for the one scenario that matters most.
 */
const GlobalLivePlayer = {
  STORAGE_KEY: 'alchemyfm-live-session',
  STATIONS_CACHE_KEY: 'alchemyfm-stations-cache',
  _miniPollTimer: null,
  _miniUiAbort: null,
  _miniSwitching: false,
  _miniMetaEpoch: 0,
  _navClickAbort: null,
  _homeStylesLoaded: false,
  _heardMeta: null,
  _playbackPrimed: false,
  _stationList: null,
  _stationSkipInFlight: false,

  primePlayback() {
    if (this._playbackPrimed || typeof RadioApp === 'undefined') return;
    this._playbackPrimed = true;
    const audio = this.getAudio();
    if (!audio) return;
    this.ensureEngine(audio);
    if (RadioApp.useStripWebAudio?.() && typeof LiveAudioGraph !== 'undefined') {
      if (!audio._liveAudioGraph) {
        audio._liveAudioGraph = LiveAudioGraph.attach(audio);
      }
      const graph = audio._liveAudioGraph;
      graph?.ensureGraph?.();
      const ctx = graph?.audioContext;
      if (ctx?.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
    }
    RadioApp.configurePlaybackSession?.();
  },

  isStationPage() {
    return /station(?:\.html)?$/i.test(location.pathname.replace(/\/$/, ''));
  },

  isHomePage() {
    const p = location.pathname.replace(/\/$/, '') || '/';
    return p === '' || p === '/' || p === '/index.html';
  },

  isListenerPage() {
    return this.isHomePage() || this.isStationPage();
  },

  readSession() {
    try {
      const raw = localStorage.getItem(this.STORAGE_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || typeof data !== 'object') return null;
      return data;
    } catch {
      return null;
    }
  },

  readStationsCache() {
    try {
      const raw = localStorage.getItem(this.STATIONS_CACHE_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      return Array.isArray(data) ? data : null;
    } catch {
      return null;
    }
  },

  writeStationsCache(stations) {
    try {
      if (stations?.length) {
        localStorage.setItem(this.STATIONS_CACHE_KEY, JSON.stringify(stations));
        this._stationList = stations;
      }
    } catch {}
  },

  async ensureStationList() {
    if (this._stationList?.length) return this._stationList;

    const fromHome = typeof AlchemyHome !== 'undefined'
      ? AlchemyHome.readStationsCache?.()
      : null;
    if (fromHome?.length) {
      this._stationList = fromHome;
      return fromHome;
    }

    const cached = this.readStationsCache();
    if (cached?.length) {
      this._stationList = cached;
      return cached;
    }

    if (typeof RadioApp === 'undefined') return [];

    const stations = await RadioApp.fetchJSON('/api/stations');
    if (stations?.length) {
      this.writeStationsCache(stations);
      if (typeof AlchemyHome !== 'undefined') {
        AlchemyHome.writeStationsCache?.(stations);
      }
    }
    return this._stationList || [];
  },

  async switchToAdjacentStation(delta) {
    if (!delta || typeof RadioApp === 'undefined') return;
    if (!RadioApp.isMobileStation?.()) return;
    if (!this.isListening() && !this.readSession()?.wantLive) return;
    if (this._stationSkipInFlight || this._miniSwitching) return;

    AlchemyDiag.log('hw-skip-start', { delta, cachedListWarm: Boolean(this._stationList?.length) });

    // Skip the async ensureStationList() hop entirely when the list is
    // already warm — a lock-screen/CarPlay nexttrack press only grants iOS a
    // brief autoplay window, and even one avoidable microtask before we can
    // pick the next station eats into it.
    const stations = this._stationList?.length
      ? this._stationList
      : await this.ensureStationList();
    if (!stations.length) return;

    this._stationSkipInFlight = true;
    try {
      const slug = this.activePlayingSlug() || this.readSession()?.slug;
      let idx = stations.findIndex((s) => s.slug === slug);
      if (idx < 0) idx = 0;
      const nextIdx = (idx + delta + stations.length) % stations.length;
      await this.switchToStation(stations[nextIdx], { hardwareSkip: true });
    } finally {
      this._stationSkipInFlight = false;
    }
  },

  writeSession(data) {
    try {
      if (!data) {
        localStorage.removeItem(this.STORAGE_KEY);
        return;
      }
      localStorage.setItem(this.STORAGE_KEY, JSON.stringify(data));
    } catch {}
  },

  mergeSession(patch) {
    const prev = this.readSession() || {};
    const next = { ...prev, ...patch };
    if (!next.slug) {
      this.writeSession(null);
      return null;
    }
    this.writeSession(next);
    return next;
  },

  isListening() {
    const audio = this.getAudio();
    return Boolean(audio && audio.dataset.wantLive === '1');
  },

  persistListeningSession(extra = {}) {
    const audio = this.getAudio();
    if (!audio || audio.dataset.wantLive !== '1') return;
    const session = this.readSession() || {};
    const slug = extra.slug ||
      this.slugFromStreamSrc(audio.dataset.streamSrc || audio.src) ||
      session.slug;
    this.mergeSession({
      slug,
      stationName: extra.stationName || session.stationName,
      streamSrc: audio.dataset.streamSrc || audio.src || session.streamSrc,
      wantLive: true,
    });
  },

  stationUrl(slug) {
    return `/station.html?slug=${encodeURIComponent(slug)}`;
  },

  browserStreamUrl(station) {
    return RadioApp.browserStreamUrl(station);
  },

  ensureShell() {
    if (document.getElementById('global-live-shell')) return;

    const shell = document.createElement('div');
    shell.id = 'global-live-shell';
    shell.innerHTML = `
      <audio id="live-audio" preload="none" playsinline></audio>
      <div id="live-mini-player" class="live-mini-player" hidden>
        <button type="button" id="live-mini-play-btn" class="live-mini-play-btn" aria-label="Play">
          <span class="live-mini-play-icon" aria-hidden="true"></span>
        </button>
        <div class="live-mini-art" id="live-mini-art" aria-hidden="true">
          <div class="live-mini-art-placeholder">♪</div>
        </div>
        <div class="live-mini-meta">
          <a id="live-mini-station" class="live-mini-station" href="#">Station</a>
          <p id="live-mini-track" class="live-mini-track">Live</p>
        </div>
        <div class="live-mini-status">
          <span id="live-mini-live" class="live-mini-live" hidden>Live</span>
          <span id="live-mini-listeners" class="live-mini-listeners" hidden></span>
        </div>
        <button type="button" id="live-mini-viz-btn" class="live-mini-viz-btn"
          aria-label="Fullscreen visuals" title="Fullscreen visuals" hidden>
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
            <path fill="currentColor" d="M3 5a2 2 0 0 1 2-2h2.5a1 1 0 1 1 0 2H5v2.5a1 1 0 1 1-2 0V5zm12-2h-2.5a1 1 0 1 1 0-2H15a2 2 0 0 1 2 2v2.5a1 1 0 1 1-2 0V5h-2.5a1 1 0 1 1 0-2H15zM3 15v-2.5a1 1 0 1 1 2 0V15h2.5a1 1 0 1 1 0 2H5a2 2 0 0 1-2-2zm14 0a2 2 0 0 1-2 2h-2.5a1 1 0 1 1 0-2H15v-2.5a1 1 0 1 1 2 0V15zM6 10a1 1 0 0 1 1-1h6a1 1 0 1 1 0 2H7a1 1 0 0 1-1-1z"/>
          </svg>
        </button>
        <div class="live-mini-volume">
          <button type="button" id="live-mini-volume-btn" class="live-mini-volume-btn"
            aria-label="Volume" aria-expanded="false" aria-controls="live-mini-volume-pop">
            <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
              <path fill="currentColor" d="M11 4.5v11l-4.5-3H3a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1h3.5L11 4.5zm2.2 2.3a1 1 0 0 1 1.4 0 5 5 0 0 1 0 7.1 1 1 0 1 1-1.4-1.4 3 3 0 0 0 0-4.3 1 1 0 0 1 0-1.4zm1.4-1.4a1 1 0 0 1 1.4 0 7 7 0 0 1 0 9.9 1 1 0 1 1-1.4-1.4 5 5 0 0 0 0-7.1 1 1 0 0 1 0-1.4z"/>
            </svg>
          </button>
          <div id="live-mini-volume-pop" class="live-mini-volume-pop">
            <input type="range" id="live-mini-volume" class="live-volume-slider"
              min="0" max="1" step="0.05" value="1" aria-label="Volume">
          </div>
        </div>
      </div>
      ${this.mobileNavMarkup()}
    </div>`;
    document.body.appendChild(shell);
  },

  /** Mobile-only: a two-tab bar and the Now Playing surface it reveals.
      Kept identical in index.html and station.html — see ensureShell(). */
  mobileNavMarkup() {
    return `
      <section id="now-playing-panel" class="now-playing-panel" hidden aria-label="Now playing">
        <div class="np-live" id="np-live" hidden>
          <div class="np-art" id="np-art"><div class="np-art-placeholder" aria-hidden="true">♪</div></div>
          <p class="np-station" id="np-station"></p>
          <h2 class="np-title" id="np-title"></h2>
          <p class="np-artist" id="np-artist"></p>
          <p class="np-status" id="np-status"></p>
          <div class="np-transport">
            <button type="button" class="np-play" id="np-play" aria-label="Play">
              <span class="np-play-icon" aria-hidden="true"></span>
            </button>
          </div>
        </div>
        <div class="np-idle" id="np-idle" hidden>
          <span class="np-idle-icon" aria-hidden="true"></span>
          <h2 class="np-idle-title">You're not tuned in</h2>
          <p class="np-idle-sub">Pick a station and it plays here.</p>
          <div class="np-resume" id="np-resume" hidden></div>
          <div class="np-shortcuts" id="np-shortcuts"></div>
        </div>
      </section>
      <nav id="live-tabs" class="live-tabs" aria-label="Sections" hidden>
        <button type="button" class="live-tab is-active" data-tab="stations" aria-pressed="true">
          <span class="live-tab-icon live-tab-grid" aria-hidden="true"></span>
          <span class="live-tab-label">Stations</span>
        </button>
        <button type="button" class="live-tab" data-tab="now" aria-pressed="false">
          <span class="live-tab-icon live-tab-play" aria-hidden="true"></span>
          <span class="live-tab-eq" aria-hidden="true"><i></i><i></i><i></i></span>
          <span class="live-tab-label">Now playing</span>
        </button>
      </nav>`;
  },

  getAudio() {
    if (!this.isListenerPage()) return null;
    this.ensureShell();
    return document.getElementById('live-audio');
  },

  miniElements() {
    return {
      bar: document.getElementById('live-mini-player'),
      playBtn: document.getElementById('live-mini-play-btn'),
      station: document.getElementById('live-mini-station'),
      track: document.getElementById('live-mini-track'),
      art: document.getElementById('live-mini-art'),
      volumeBtn: document.getElementById('live-mini-volume-btn'),
      volumePop: document.getElementById('live-mini-volume-pop'),
      volume: document.getElementById('live-mini-volume'),
      vizBtn: document.getElementById('live-mini-viz-btn'),
    };
  },

  /**
   * Live / Off air pill and listener count in the playbar. Kept separate from
   * updateMiniMeta() because most callers of that have no station detail to
   * hand, and passing undefined would blank a perfectly good reading.
   * Values are cached so play/pause can re-render without a fetch.
   */
  syncMiniStatus(next) {
    if (next) this._miniStatus = { ...(this._miniStatus || {}), ...next };
    const { listeners = null, onAir = null } = this._miniStatus || {};

    const liveEl = document.getElementById('live-mini-live');
    const listenersEl = document.getElementById('live-mini-listeners');
    const audio = this.getAudio();
    const wantLive = audio?.dataset?.wantLive === '1';

    if (liveEl) {
      const offAir = onAir === false;
      liveEl.classList.toggle('is-off-air', offAir);
      if (offAir) {
        liveEl.textContent = 'Off air';
        liveEl.hidden = false;
      } else {
        liveEl.textContent = 'Live';
        liveEl.hidden = !wantLive;
      }
    }

    if (listenersEl) {
      const count = Number(listeners);
      const show = Number.isFinite(count) && count > 0 && onAir !== false;
      listenersEl.hidden = !show;
      if (show) {
        listenersEl.textContent = count === 1 ? '1 listening' : `${count} listening`;
      }
    }
  },

  // ── Mobile two-tab navigation ─────────────────────────────
  // The phone has no room for a mini player *and* a nav bar, so Now Playing
  // becomes a tab rather than a strip above one.

  _activeTab: 'stations',

  /** Only on a phone, and only on home — the station page is its own player. */
  tabsApply() {
    const tabs = document.getElementById('live-tabs');
    if (!tabs) return false;
    const eligible = this.isHomePage() && !this.isStationPage();
    tabs.hidden = !eligible;
    document.body.classList.toggle('has-live-tabs', eligible);
    if (!eligible) this.setTab('stations', { silent: true });
    return eligible;
  },

  setTab(name, { silent = false } = {}) {
    const tab = name === 'now' ? 'now' : 'stations';
    this._activeTab = tab;

    const panel = document.getElementById('now-playing-panel');
    if (panel) panel.hidden = tab !== 'now';
    document.body.classList.toggle('np-open', tab === 'now');

    document.querySelectorAll('#live-tabs .live-tab').forEach((btn) => {
      const on = btn.dataset.tab === tab;
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-pressed', String(on));
    });

    if (tab === 'now' && !silent) this.renderNowPlaying();
    this.syncMiniVisibility();
  },

  /** Equaliser on the tab icon whenever something is actually playing. */
  syncTabIndicator() {
    const tabs = document.getElementById('live-tabs');
    if (!tabs) return;
    const audio = this.getAudio();
    const live = audio?.dataset?.wantLive === '1';
    tabs.classList.toggle('is-live', Boolean(live));
  },

  renderNowPlaying() {
    const panel = document.getElementById('now-playing-panel');
    if (!panel) return;

    const session = this.readSession();
    const audio = this.getAudio();
    const tuned = Boolean(session?.slug) && (audio?.dataset?.wantLive === '1' || this.isListening());

    const liveEl = document.getElementById('np-live');
    const idleEl = document.getElementById('np-idle');
    if (liveEl) liveEl.hidden = !tuned;
    if (idleEl) idleEl.hidden = tuned;

    if (tuned) this.renderNowPlayingLive(session);
    else this.renderNowPlayingIdle(session);
  },

  renderNowPlayingLive(session) {
    const { np = null, stationName = '', artworkUrl = '' } = this._miniStatus?.meta || {};
    const set = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    };
    set('np-station', stationName || session?.stationName || '');
    set('np-title', np?.title || 'Live');
    set('np-artist', np?.artist || '');

    const { listeners = null, onAir = null } = this._miniStatus || {};
    const bits = [];
    bits.push(onAir === false ? 'Off air' : 'Live');
    if (Number(listeners) > 0) {
      bits.push(Number(listeners) === 1 ? '1 listening' : `${listeners} listening`);
    }
    set('np-status', bits.join(' · '));

    const art = document.getElementById('np-art');
    const cover = artworkUrl || '';
    if (art) {
      if (cover) art.innerHTML = `<img src="${RadioApp.escapeAttr(cover)}" alt="" decoding="async">`;
      else art.innerHTML = '<div class="np-art-placeholder" aria-hidden="true">♪</div>';
    }

    const playBtn = document.getElementById('np-play');
    const audio = this.getAudio();
    const playing = Boolean(audio && !audio.paused && audio.dataset.wantLive === '1');
    if (playBtn) {
      playBtn.classList.toggle('is-playing', playing);
      playBtn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    }
  },

  /** Idle is an invitation, not a dead end: last station first, then featured. */
  renderNowPlayingIdle(session) {
    const resume = document.getElementById('np-resume');
    const shortcuts = document.getElementById('np-shortcuts');
    const stations = this.readStationsCache() || [];

    if (resume) {
      const last = session?.slug
        ? stations.find((s) => s.slug === session.slug) || { slug: session.slug, name: session.stationName }
        : null;
      if (last?.slug) {
        resume.hidden = false;
        resume.innerHTML = `
          <a class="np-resume-link" href="${this.stationUrl(last.slug)}">
            <span class="np-resume-label">Back to</span>
            <span class="np-resume-name">${RadioApp.escape(last.name || last.slug)}</span>
          </a>`;
      } else {
        resume.hidden = true;
        resume.innerHTML = '';
      }
    }

    if (!shortcuts) return;
    const featured = stations.filter((s) => s.featured).slice(0, 4);
    const picks = featured.length ? featured : stations.slice(0, 4);
    if (!picks.length) {
      shortcuts.innerHTML = '';
      return;
    }
    shortcuts.innerHTML = `
      <p class="np-shortcuts-label">${featured.length ? '★ Featured' : 'Stations'}</p>
      ${picks.map((s) => `
        <a class="np-shortcut" href="${this.stationUrl(s.slug)}">
          <span class="np-shortcut-name">${RadioApp.escape(s.name)}</span>
          <span class="np-shortcut-go" aria-hidden="true"></span>
        </a>`).join('')}`;
  },

  bindTabs() {
    const tabs = document.getElementById('live-tabs');
    if (!tabs || tabs.dataset.bound === '1') return;
    tabs.dataset.bound = '1';
    tabs.addEventListener('click', (e) => {
      const btn = e.target.closest('.live-tab');
      if (!btn) return;
      this.setTab(btn.dataset.tab);
    });

    const play = document.getElementById('np-play');
    play?.addEventListener('click', () => {
      const audio = this.getAudio();
      audio?._liveEngine?.togglePlay?.();
      setTimeout(() => this.renderNowPlaying(), 60);
    });
  },

  syncMiniVizButton() {
    const { vizBtn } = this.miniElements();
    if (!vizBtn || typeof RadioApp === 'undefined') return;
    const available = RadioApp.fullscreenVizAvailable();
    vizBtn.hidden = !available;
    vizBtn.classList.toggle('is-active', Boolean(window.__milkdropActive));
  },

  async ensureVizAudioGraph(audio) {
    if (!audio || typeof RadioApp === 'undefined') return false;
    if (!RadioApp.fullscreenVizAvailable() || !RadioApp.useStripWebAudio()) {
      return false;
    }
    if (!audio._liveAudioGraph && typeof LiveAudioGraph !== 'undefined') {
      audio._liveAudioGraph = LiveAudioGraph.attach(audio);
    }
    audio._liveAudioGraph?.ensureGraph?.();
    const ctx = audio._liveAudioGraph?.audioContext;
    if (ctx?.state === 'suspended') {
      await ctx.resume();
    }
    return Boolean(audio._liveAudioGraph?.analyserNode);
  },

  async toggleFullscreenViz() {
    if (typeof RadioApp === 'undefined' || !RadioApp.fullscreenVizAvailable()) {
      return;
    }
    const audio = this.getAudio();
    if (!audio) return;

    const mod = await RadioApp.loadMilkdropFullscreen();
    if (mod.isActive()) {
      mod.close();
      this.syncMiniVizButton();
      return;
    }

    const graphReady = await this.ensureVizAudioGraph(audio);
    if (!graphReady) {
      throw new Error('Audio player is not ready.');
    }

    if (audio.paused && audio.dataset.wantLive === '1') {
      try {
        await audio._liveEngine?.connectStream?.(true);
      } catch {
        audio.reconnectLiveStream?.();
      }
    }

    await mod.open(audio, () => this.getHeardVizMeta());
    this.syncMiniVizButton();
  },

  syncMiniVisibility() {
    const audio = this.getAudio();
    const { bar } = this.miniElements();
    if (!bar || !audio) return;

    const session = this.readSession();
    const onHome = this.isHomePage() || this.isOnSoftHome();
    const streamSlug = this.slugFromStreamSrc(audio.dataset.streamSrc || audio.src);
    const activeSlug = streamSlug || session?.slug;
    // Show whenever there's a known last station, playing or paused — the
    // play button below already reflects the real paused/playing state, so
    // this just controls whether there's a "last station" to show at all.
    const show = onHome && Boolean(activeSlug);

    bar.hidden = !show;
    document.body.classList.toggle('has-live-mini-player', show);
    this.syncTabIndicator();

    if (!show) {
      this.stopMiniPoll();
      return;
    }

    this.syncMiniPlayButton(audio);
    this.syncMiniVizButton();
    this.startMiniPoll(activeSlug);
    const { track } = this.miniElements();
    if (track) this.syncMiniTrackMarquee(track);
  },

  syncMiniPlayButton(audio) {
    const { playBtn } = this.miniElements();
    if (!playBtn || !audio) return;
    const playing = !audio.paused && audio.dataset.wantLive === '1';
    playBtn.classList.toggle('is-playing', playing);
    playBtn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
  },

  setMiniTrackLabel(trackEl, label) {
    if (!trackEl) return;
    let inner = trackEl.querySelector('.live-mini-track-inner');
    if (!inner) {
      trackEl.innerHTML = '<span class="live-mini-track-inner"></span>';
      inner = trackEl.querySelector('.live-mini-track-inner');
    }
    // The mini player polls now-playing every 750ms while live (startMiniPoll)
    // and called this on every tick regardless of whether the text changed --
    // syncMiniTrackMarquee resets the scroll animation each time, so a long
    // title (16s+ cycle) never got more than 750ms of progress before being
    // restarted. Only resync on an actual change; a resize still re-syncs
    // directly via the resize listener, independent of this guard.
    if (trackEl.dataset.label === label) return;
    trackEl.dataset.label = label;
    inner.textContent = label;
    trackEl.title = label;
    this.syncMiniTrackMarquee(trackEl);
  },

  syncMiniTrackMarquee(trackEl) {
    if (!trackEl || typeof RadioApp === 'undefined') return;
    if (!window.matchMedia('(max-width: 640px)').matches) {
      trackEl.classList.remove('is-scrolling');
      trackEl.style.removeProperty('--scroll-distance');
      trackEl.style.removeProperty('--scroll-duration');
      return;
    }
    RadioApp.syncOverflowMarquee(trackEl, { innerSelector: '.live-mini-track-inner' });
  },

  updateMiniMeta({ stationName, slug, np, artworkUrl, pending = false, forceGlitch = false } = {}) {
    const { station, track, art } = this.miniElements();
    if (!station || !track) return;

    const epoch = ++this._miniMetaEpoch;
    const trackKey = typeof RadioApp !== 'undefined' ? RadioApp.trackKey(np) : '';

    if (slug && stationName) {
      station.textContent = stationName;
      station.href = this.stationUrl(slug);
    }

    if (pending) {
      this.setMiniTrackLabel(track, 'Connecting…');
      if (art) {
        art.dataset.trackKey = '';
        art.dataset.coverUrl = '';
        art.innerHTML = '<div class="live-mini-art-placeholder">♪</div>';
      }
      return;
    }

    if (np?.artist && np?.title) {
      this.setMiniTrackLabel(track, `${np.artist} — ${np.title}`);
    } else {
      this.setMiniTrackLabel(track, 'Live');
    }

    if (!art || !artworkUrl || typeof RadioApp === 'undefined') {
      if (art) {
        art.dataset.trackKey = '';
        art.dataset.coverUrl = '';
        art.innerHTML = '<div class="live-mini-art-placeholder">♪</div>';
      }
      return;
    }

    art.dataset.imgClass = 'live-mini-art-img';
    art.dataset.placeholder = '<div class="live-mini-art-placeholder">♪</div>';
    const prevSlug = art.dataset.stationSlug || '';
    const prevCoverKey = art.dataset.trackKey || '';
    const stationChanged = Boolean(slug && prevSlug && prevSlug !== slug);
    const forceReset = stationChanged;
    if (forceReset) {
      art.dataset.trackKey = '';
      art.dataset.coverUrl = '';
      art.innerHTML = art.dataset.placeholder;
    }
    if (slug) art.dataset.stationSlug = slug;
    const coverKey = slug ? `${slug}\0${trackKey}` : trackKey;
    RadioApp.setCoverImage(art, artworkUrl, coverKey, {
      reset: forceReset,
      waitForLoad: true,
      forceGlitch: Boolean(
        forceGlitch ||
        stationChanged ||
        (coverKey && prevCoverKey && prevCoverKey !== coverKey)
      ),
      isStale: () => epoch !== this._miniMetaEpoch,
      onApplied: () => {
        if (epoch !== this._miniMetaEpoch) return;
      },
    });
  },

  applySwitchVisuals(station, { pending = false, switching = false } = {}) {
    if (pending) {
      this.updateMiniMeta({
        stationName: station?.name,
        slug: station?.slug,
        pending: true,
        forceGlitch: switching,
      });
      if (typeof window.__alchemyOnStreamSwitchPending === 'function') {
        window.__alchemyOnStreamSwitchPending(station);
      }
      if (switching && station?.slug) {
        AlchemyHome?.pulseCardSwitch?.(station.slug);
      }
      AlchemyHome?.syncAllCardPlayUi?.();
      return;
    }

    if (!station) return;

    const np = station.now_playing;
    const artworkUrl = (np && np.cover_url) || station.artwork_url || '';

    this.updateMiniMeta({
      stationName: station.name,
      slug: station.slug,
      np,
      artworkUrl,
      forceGlitch: switching,
    });
    this.updateHeardMeta({
      slug: station.slug,
      stationName: station.name,
      np,
      artworkUrl,
    });

    if (typeof window.__alchemyOnStreamSwitch === 'function') {
      window.__alchemyOnStreamSwitch(station);
    }
  },

  async refreshMiniNowPlaying(slug) {
    if (!slug || typeof RadioApp === 'undefined' || this._miniSwitching) return;
    const audio = this.getAudio();
    const streamSlug = this.slugFromStreamSrc(audio?.dataset?.streamSrc || audio?.src);
    const effectiveSlug = streamSlug || slug;
    const epochAtStart = this._miniMetaEpoch;
    try {
      const s = await RadioApp.fetchJSON(`/api/stations/${encodeURIComponent(effectiveSlug)}`);
      if (this._miniSwitching || epochAtStart !== this._miniMetaEpoch) return;
      const session = this.readSession() || {};
      this.mergeSession({
        slug: s.slug,
        stationName: s.name,
        streamSrc: this.browserStreamUrl(s),
        wantLive: session.wantLive === true || this.isListening(),
      });
      this.updateMiniMeta({
        stationName: s.name,
        slug: s.slug,
        np: s.now_playing,
        artworkUrl: (s.now_playing && s.now_playing.cover_url) || s.artwork_url || '',
      });
      this.syncMiniStatus({
        listeners: s.listeners,
        onAir: s.on_air,
        meta: {
          stationName: s.name,
          slug: s.slug,
          np: s.now_playing,
          artworkUrl: (s.now_playing && s.now_playing.cover_url) || s.artwork_url || '',
        },
      });
      this.syncTabIndicator();
      if (this._activeTab === 'now') this.renderNowPlaying();

      if ((this.isHomePage() || this.isOnSoftHome()) &&
          typeof AlchemyHome !== 'undefined' &&
          document.querySelector(`[data-slug="${CSS.escape(s.slug)}"]`)) {
        AlchemyHome.updateCard(s);
      }

      if (this.isListening() && effectiveSlug === this.activePlayingSlug()) {
        this.updateHeardMeta({
          slug: s.slug,
          stationName: s.name,
          np: s.now_playing,
          artworkUrl: (s.now_playing && s.now_playing.cover_url) || s.artwork_url || '',
        });
      }

      if (audio?.setStreamLive) {
        audio.setStreamLive(Boolean(s.on_air));
      }
      if (audio && this.isListening() && s.stream_epoch != null) {
        const prevEpoch = audio.dataset.streamEpoch;
        if (prevEpoch && prevEpoch !== String(s.stream_epoch)) {
          audio.forceStreamOffline?.('Off air');
        }
        audio.dataset.streamEpoch = String(s.stream_epoch);
      }

      if ((this.isHomePage() || this.isOnSoftHome()) && audio && typeof RadioApp !== 'undefined') {
        const np = s.now_playing;
        const playing = !audio.paused && audio.dataset.wantLive === '1';
        RadioApp.updateMediaSession({
          title: np?.title || s.name,
          artist: np?.artist || s.name,
          album: s.name,
          artworkUrl: (np && np.cover_url) || s.artwork_url || '',
          playing,
        });
        RadioApp.updateTabTitle({
          stationName: s.name,
          nowPlaying: np,
          playing,
        });
      }

      if (window.__milkdropActive && typeof RadioApp !== 'undefined') {
        RadioApp.loadMilkdropFullscreen().then((mod) => {
          if (mod.isActive()) mod.updateMeta(this.getHeardVizMeta());
        }).catch(() => {});
      }
    } catch {
      /* keep last meta */
    }
  },

  startMiniPoll(slug) {
    this.stopMiniPoll();
    const poll = () => {
      const audio = this.getAudio();
      const session = this.readSession();
      const activeSlug = this.slugFromStreamSrc(audio?.dataset?.streamSrc || audio?.src) ||
        session?.slug ||
        slug;
      return this.refreshMiniNowPlaying(activeSlug);
    };
    poll();
    const audio = this.getAudio();
    const ms = audio && audio.dataset.wantLive === '1' ? 750 : 5000;
    this._miniPollTimer = setInterval(poll, ms);
  },

  stopMiniPoll() {
    clearInterval(this._miniPollTimer);
    this._miniPollTimer = null;
  },

  bindMiniUi() {
    if (this._miniUiAbort) {
      this._miniUiAbort.abort();
    }
    this._miniUiAbort = new AbortController();
    const { signal } = this._miniUiAbort;
    const { playBtn, volumeBtn, volumePop, volume, vizBtn } = this.miniElements();
    const audio = this.getAudio();
    if (!playBtn || !audio) return;

    this.syncMiniVizButton();

    playBtn.addEventListener('click', () => {
      audio._liveEngine?.togglePlay?.();
    }, { signal });

    vizBtn?.addEventListener('click', async () => {
      vizBtn.disabled = true;
      try {
        await this.toggleFullscreenViz();
      } catch (err) {
        alert(err.message || 'Could not open fullscreen visuals');
      } finally {
        vizBtn.disabled = false;
      }
    }, { signal });

    if (volume && typeof RadioApp !== 'undefined') {
      RadioApp.wireLiveVolumeControl(volume, audio, { signal });
    }

    const closeVolumePop = () => {
      if (!volumePop || !volumeBtn) return;
      volumePop.classList.remove('is-open');
      volumeBtn.setAttribute('aria-expanded', 'false');
    };

    volumeBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!volumePop) return;
      const open = volumePop.classList.toggle('is-open');
      volumeBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }, { signal });

    document.addEventListener('click', (e) => {
      if (!volumePop?.classList.contains('is-open')) return;
      if (e.target.closest('.live-mini-volume')) return;
      closeVolumePop();
    }, { signal });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeVolumePop();
    }, { signal });

    const onAudioChange = () => {
      this.syncMiniPlayButton(audio);
      this.syncMiniVisibility();
    };

    ['play', 'pause', 'playing'].forEach((ev) => {
      audio.addEventListener(ev, onAudioChange, { signal });
    });

    const onMiniLayout = () => {
      const { track } = this.miniElements();
      if (track) this.syncMiniTrackMarquee(track);
    };
    window.addEventListener('resize', onMiniLayout, { signal });
    if (typeof ResizeObserver !== 'undefined') {
      const { bar, track } = this.miniElements();
      if (!this._miniTrackResizeObs) {
        this._miniTrackResizeObs = new ResizeObserver(onMiniLayout);
      }
      if (bar) {
        this._miniTrackResizeObs.observe(bar);
      }
      const meta = bar?.querySelector('.live-mini-meta');
      if (meta) {
        this._miniTrackResizeObs.observe(meta);
      }
      if (track) {
        this._miniTrackResizeObs.observe(track);
      }
    }
  },

  slugFromStreamSrc(src) {
    if (!src) return '';
    try {
      const path = new URL(src, location.origin).pathname;
      const match = path.match(/\/api\/stations\/([^/]+)\/listen/i);
      return match ? decodeURIComponent(match[1]) : '';
    } catch {
      return '';
    }
  },

  activePlayingSlug() {
    const audio = this.getAudio();
    const session = this.readSession();
    return this.slugFromStreamSrc(audio?.dataset?.streamSrc || audio?.src) ||
      session?.slug ||
      '';
  },

  updateHeardMeta({ slug, stationName, np, artworkUrl } = {}) {
    if (!slug) {
      this._heardMeta = null;
      return;
    }
    this._heardMeta = {
      slug,
      stationName: stationName || this._heardMeta?.stationName || '',
      np: np !== undefined ? np : (this._heardMeta?.np ?? null),
      artworkUrl: artworkUrl !== undefined
        ? artworkUrl
        : (this._heardMeta?.artworkUrl || ''),
    };
  },

  /** Metadata for fullscreen viz — always the heard stream. */
  getHeardVizMeta() {
    const session = this.readSession() || {};
    const heardSlug = this.activePlayingSlug();
    const cached = this._heardMeta;
    const np = (cached?.slug === heardSlug && cached?.np) || null;
    const stationName = (cached?.slug === heardSlug && cached?.stationName) ||
      session.stationName ||
      heardSlug ||
      '';
    const artworkUrl = (cached?.slug === heardSlug && cached?.artworkUrl) ||
      (np && np.cover_url) ||
      '';
    return {
      stationName,
      artist: np?.artist || stationName,
      title: np?.title || 'Live',
      artworkUrl,
      trackKey: typeof RadioApp !== 'undefined'
        ? `${heardSlug}\0${RadioApp.trackKey(np) || ''}`
        : '',
    };
  },

  /** Heard vs viewed station context for station UI / viz. */
  getPlaybackPresentation(viewedSlug = '') {
    const audio = this.getAudio();
    const session = this.readSession() || {};
    const heardSlug = this.activePlayingSlug();
    const pageSlug = viewedSlug ||
      audio?.dataset?.pageSlug ||
      '';
    const wantLive = audio?.dataset?.wantLive === '1';
    const heardLive = Boolean(wantLive && audio && !audio.paused);
    const browsingOther = this.isBrowsingOtherStation(pageSlug);
    const onViewedStation = Boolean(pageSlug && heardSlug === pageSlug && wantLive);
    const cached = this._heardMeta;
    const heardNp = (cached?.slug === heardSlug && cached?.np) || null;
    const heardStationName = (cached?.slug === heardSlug && cached?.stationName) ||
      session.stationName ||
      '';

    return {
      heardSlug,
      heardStationName,
      heardNp,
      heardLive,
      viewedSlug: pageSlug,
      browsingOther,
      onViewedStation,
      wantLive,
    };
  },

  ensureEngine(audio) {
    if (!audio || typeof RadioApp === 'undefined') return null;
    RadioApp.initLivePlayerEngine(audio, {
      onSessionChange: (meta) => this.onSessionChange(audio, meta),
    });
    return audio._liveEngine;
  },

  onSessionChange(audio, meta = {}) {
    const wantLive = audio?.dataset?.wantLive === '1';
    if (!wantLive) {
      // A pause (or media-session stop) shouldn't erase which station you
      // were on — keep slug/stationName/streamSrc so the mini player can
      // still show "last station, tap to resume" after reopening the app.
      this.mergeSession({ wantLive: false });
      this.syncMiniVisibility();
      return;
    }

    const prev = this.readSession() || {};
    const streamSlug = this.slugFromStreamSrc(
      audio?.dataset?.streamSrc || audio?.src
    );
    const slug = streamSlug || prev.slug || meta.slug;
    let stationName = meta.stationName;
    if (!stationName) {
      if (!streamSlug || streamSlug === prev.slug) {
        stationName = prev.stationName;
      }
    }

    const session = this.mergeSession({
      slug,
      stationName,
      streamSrc: audio?.dataset?.streamSrc || audio?.src || prev.streamSrc,
      wantLive: true,
    });

    if (session) {
      const heardNp = (this._heardMeta?.slug === session.slug && this._heardMeta?.np) || null;
      const np = meta.nowPlaying ?? heardNp;
      const artworkUrl = meta.artworkUrl ??
        ((np && np.cover_url) || this._heardMeta?.artworkUrl || '');
      this.updateHeardMeta({
        slug: session.slug,
        stationName: session.stationName,
        np,
        artworkUrl,
      });
      this.updateMiniMeta({
        stationName: session.stationName,
        slug: session.slug,
        np,
        artworkUrl,
      });
      if (session.slug && typeof RadioApp !== 'undefined' &&
          (this.isHomePage() || this.isOnSoftHome())) {
        void this.refreshMiniNowPlaying(session.slug);
      }
    }
    this.syncMiniVisibility();
  },

  ensureHomeStyles() {
    if (this._homeStylesLoaded) return;
    const head = document.head;
    const add = (href, media) => {
      if (head.querySelector(`link[data-alchemy-home-style="${href}"]`)) return;
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      link.media = media;
      link.dataset.alchemyHomeStyle = href;
      head.appendChild(link);
    };
    // Keep these in step with index.html — a stale version here means a
    // soft-navigated home silently loads a different stylesheet than a
    // full page load does.
    add('/static/home-desktop.css?v=17', '(min-width: 641px)');
    add('/static/home-mobile.css?v=12', '(max-width: 640px)');
    this._homeStylesLoaded = true;
  },

  ensureStationStyles() {
    if (this._stationStylesLoaded) return;
    const head = document.head;
    const add = (href, media) => {
      if (head.querySelector(`link[data-alchemy-station-style="${href}"]`)) return;
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      link.media = media;
      link.dataset.alchemyStationStyle = href;
      head.appendChild(link);
    };
    add('/static/station-desktop.css?v=13', '(min-width: 641px)');
    add('/static/station-mobile.css?v=20', '(max-width: 640px)');
    this._stationStylesLoaded = true;
  },

  loadScriptOnce(src, datasetKey) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[data-alchemy-script="${datasetKey}"]`);
      if (existing) {
        if (existing.dataset.loaded === '1') {
          resolve();
          return;
        }
        existing.addEventListener('load', () => resolve(), { once: true });
        existing.addEventListener('error', reject, { once: true });
        return;
      }
      const script = document.createElement('script');
      script.src = src;
      script.dataset.alchemyScript = datasetKey;
      script.onload = () => {
        script.dataset.loaded = '1';
        resolve();
      };
      script.onerror = reject;
      document.head.appendChild(script);
    });
  },

  async ensureStationBoot() {
    if (typeof window.__alchemyStationNavigate === 'function') return;

    this.ensureStationStyles();

    if (typeof LiveAudioGraph === 'undefined') {
      await this.loadScriptOnce('/static/live-audio-graph.js?v=8', 'live-audio-graph');
    }
    if (typeof LiveTuningFx === 'undefined') {
      await this.loadScriptOnce('/static/tuning-static.js?v=5', 'tuning-static');
    }
    if (typeof StripVisualizer === 'undefined') {
      await this.loadScriptOnce('/static/strip-visualizer.js?v=3', 'strip-visualizer');
    }

    await this.loadScriptOnce('/static/station-boot.js?v=10', 'station-boot');
  },

  async ensureTuningReady() {
    if (typeof LiveAudioGraph === 'undefined') {
      await this.loadScriptOnce('/static/live-audio-graph.js?v=8', 'live-audio-graph');
    }
    if (typeof LiveTuningFx === 'undefined') {
      await this.loadScriptOnce('/static/tuning-static.js?v=5', 'tuning-static');
    }
  },

  /** Must mirror the <main> of index.html, or a soft-navigated home comes
      back without the rail, greeting or featured section. */
  homeMainMarkup() {
    return `
      <aside class="shelf-rail" aria-labelledby="shelf-rail-label">
        <p class="shelf-rail-label" id="shelf-rail-label">Stations<span class="shelf-rail-count" id="shelf-rail-count"></span></p>
        <ul class="shelf-rail-list" id="shelf-rail-list"></ul>
      </aside>
      <div class="shelf-main">
        <div class="home-greeting">
          <h2 class="home-greeting-title" id="home-greeting">Welcome</h2>
          <p class="home-greeting-sub" id="home-broadcast-line"></p>
        </div>
        <div id="featured-section" hidden>
          <p class="section-label"><span class="section-label-star" aria-hidden="true">&#9733;</span>Featured</p>
          <div id="stations-featured" class="station-grid"></div>
          <p class="section-label" id="all-stations-label" hidden>All Stations</p>
        </div>
        <div id="stations" class="station-grid">
          <p class="empty">Loading stations…</p>
        </div>
      </div>`;
  },

  softNavigateToHome() {
    if (this.isOnSoftHome()) {
      return;
    }

    if (this.isHomePage() && !this.isStationPage()) {
      this.persistListeningSession();
      this.syncMiniVisibility();
      return;
    }

    if (!this.isStationPage()) {
      location.href = '/';
      return;
    }

    this.persistListeningSession();
    this.ensureHomeStyles();

    if (typeof RadioApp !== 'undefined') {
      RadioApp.applySavedLiveVolume(this.getAudio());
    }

    window.__alchemyStationStop?.();

    const main = document.querySelector('main');
    if (main) {
      main.removeAttribute('id');
      main.classList.add('shelf');
      main.innerHTML = this.homeMainMarkup();
    }

    const nav = document.querySelector('header nav');
    if (nav) nav.hidden = true;

    const bioToggle = document.getElementById('artist-bio-toggle');
    if (bioToggle) bioToggle.hidden = true;

    document.body.classList.remove('live-station-page');
    document.body.classList.add('live-home-page');
    document.title = 'Alchemy FM';

    history.pushState({ alchemyfm: 'home' }, '', '/');

    this.bindMiniUi();
    const session = this.readSession();
    if (session) {
      this.updateMiniMeta({
        stationName: session.stationName,
        slug: session.slug,
      });
    }
    this.syncMiniVisibility();
    this.tabsApply();
    if (session?.slug) {
      this.refreshMiniNowPlaying(session.slug);
    }

    this.ensureHomeGrid().catch(() => {
      const root = document.getElementById('stations');
      if (root) {
        root.innerHTML = '<p class="empty">Could not load stations. <a href="/">Refresh</a></p>';
      }
    });
  },

  async ensureHomeGrid() {
    if (typeof AlchemyHome !== 'undefined') {
      AlchemyHome.mount();
      return;
    }
    await new Promise((resolve, reject) => {
      const existing = document.querySelector('script[data-alchemy-home-page]');
      if (existing) {
        existing.addEventListener('load', resolve, { once: true });
        existing.addEventListener('error', reject, { once: true });
        return;
      }
      const script = document.createElement('script');
      script.src = '/static/home-page.js?v=5';
      script.dataset.alchemyHomePage = '1';
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
    AlchemyHome.mount();
  },

  isHomeNavUrl(url) {
    const p = url.pathname.replace(/\/$/, '') || '/';
    return p === '/' || p === '/index.html';
  },

  async softNavigateToStation(slug) {
    if (!slug) return;

    this.persistListeningSession();
    AlchemyHome?.unmount?.();

    const main = document.querySelector('main');
    if (main) {
      main.id = 'content';
      // The station page is a single column; drop the home shelf grid.
      main.classList.remove('shelf');
      main.innerHTML = '<p class="empty">Loading…</p>';
    }

    const nav = document.querySelector('header nav');
    if (nav) nav.hidden = false;

    document.body.classList.remove('live-home-page');
    document.body.classList.add('live-station-page');
    document.title = 'Station — Alchemy FM';

    history.pushState({ alchemyfm: 'station', slug }, '', this.stationUrl(slug));
    this.tabsApply();
    this.syncMiniVisibility();

    try {
      await this.ensureStationBoot();
    } catch {
      location.href = this.stationUrl(slug);
      return;
    }

    if (typeof window.__alchemyStationNavigate === 'function') {
      window.__alchemyStationNavigate(slug);
      return;
    }

    location.href = this.stationUrl(slug);
  },

  bindSoftNavigation() {
    if (this._navClickAbort) {
      this._navClickAbort.abort();
    }
    this._navClickAbort = new AbortController();
    const { signal } = this._navClickAbort;

    document.addEventListener('click', (e) => {
      const link = e.target.closest('a[href]');
      if (!link || link.target === '_blank' || link.hasAttribute('download')) return;

      let url;
      try {
        url = new URL(link.href, location.origin);
      } catch {
        return;
      }
      if (url.origin !== location.origin) return;

      const stationSlug = this.slugFromStationUrl(url);

      if (this.isHomeNavUrl(url)) {
        e.preventDefault();
        this.softNavigateToHome();
        return;
      }

      if (stationSlug && this.isListening()) {
        if (this.isOnSoftHome() || this.isStationPage()) {
          e.preventDefault();
          void this.softNavigateToStation(stationSlug);
          return;
        }
        if (this.isHomePage() && !this.isStationPage()) {
          this.persistListeningSession();
        }
      }
    }, { capture: true, signal });

    window.addEventListener('pagehide', () => {
      this.persistListeningSession();
    }, { signal });
  },

  async resumeFromSession(session) {
    const audio = this.getAudio();
    if (!audio || !session?.wantLive || !session.slug) return;

    const listenUrl = session.streamSrc || this.browserStreamUrl(session.slug);
    audio.dataset.streamSrc = listenUrl;
    audio.dataset.wantLive = '1';

    if (typeof RadioApp === 'undefined') return;

    this.ensureEngine(audio);

    this.bindMiniUi();
    this.updateMiniMeta({
      stationName: session.stationName,
      slug: session.slug,
    });
    this.syncMiniVisibility();

    await this.refreshMiniNowPlaying(session.slug);

    if (typeof RadioApp !== 'undefined') {
      RadioApp.applySavedLiveVolume(audio);
    }

    if (audio.paused && audio.dataset.wantLive === '1') {
      try {
        await audio._liveEngine?.connectStream?.(true);
      } catch {
        audio.reconnectLiveStream?.();
      }
    }
  },

  maybeShowDebugLog() {
    if (!/[?&]debug=1(&|$)/.test(location.search)) return false;
    document.body.innerHTML = '';
    document.body.style.cssText = 'margin:0;background:#0b0b0b;color:#0f0;font-family:monospace;padding:1rem;';
    const heading = document.createElement('p');
    heading.textContent = 'Alchemy FM diagnostic log — tap the text below, Select All, Copy, then paste it in chat.';
    heading.style.cssText = 'font-size:14px;margin:0 0 0.75rem;';
    const textarea = document.createElement('textarea');
    textarea.readOnly = true;
    textarea.value = AlchemyDiag.dump();
    textarea.style.cssText = 'width:100%;height:65vh;background:#111;color:#0f0;font-family:monospace;font-size:11px;border:1px solid #333;box-sizing:border-box;';
    const clearBtn = document.createElement('button');
    clearBtn.textContent = 'Clear log';
    clearBtn.style.cssText = 'margin-top:0.75rem;padding:0.6rem 1.2rem;font-size:14px;';
    clearBtn.addEventListener('click', () => {
      AlchemyDiag.clear();
      textarea.value = AlchemyDiag.dump();
    });
    document.body.append(heading, textarea, clearBtn);
    return true;
  },

  /**
   * Standalone "Add to Home Screen" PWAs have no address bar, so /?debug=1
   * can't be typed -- and iOS gives that PWA its own storage partition,
   * separate from Safari, so viewing the debug page in Safari shows a
   * different (empty) log than the one the PWA actually wrote. A long-press
   * on the header wordmark TEXT (distinct from the icon button's existing
   * tap-for-home / short-hold-for-operator-signin gestures) navigates the
   * current window in place, same storage context, no URL bar needed.
   * Remove alongside AlchemyDiag once the investigation concludes.
   */
  setupDebugGesture() {
    const el = document.querySelector('.site-wordmark-text');
    if (!el || el.dataset.debugGesture === '1') return;
    el.dataset.debugGesture = '1';

    const HOLD_MS = 3000;
    const MOVE_CANCEL_PX = 12;
    let pressTimer = null;
    let didHold = false;
    let startX = 0;
    let startY = 0;

    const clearPress = () => {
      if (pressTimer) {
        window.clearTimeout(pressTimer);
        pressTimer = null;
      }
    };

    const openDebugView = () => {
      const sep = location.search ? '&' : '?';
      location.href = `${location.pathname}${location.search}${sep}debug=1`;
    };

    el.addEventListener('contextmenu', (e) => e.preventDefault());
    el.addEventListener('selectstart', (e) => e.preventDefault());

    el.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      didHold = false;
      startX = e.clientX;
      startY = e.clientY;
      clearPress();
      try {
        el.setPointerCapture(e.pointerId);
      } catch {}
      pressTimer = window.setTimeout(() => {
        pressTimer = null;
        didHold = true;
        if (navigator.vibrate) navigator.vibrate(15);
        openDebugView();
      }, HOLD_MS);
    });

    el.addEventListener('pointermove', (e) => {
      if (!pressTimer) return;
      if (Math.abs(e.clientX - startX) > MOVE_CANCEL_PX || Math.abs(e.clientY - startY) > MOVE_CANCEL_PX) {
        clearPress();
      }
    });

    const onPointerEnd = (e) => {
      clearPress();
      try {
        if (el.hasPointerCapture?.(e.pointerId)) el.releasePointerCapture(e.pointerId);
      } catch {}
      if (didHold) {
        e.preventDefault();
        didHold = false;
      }
    };

    el.addEventListener('pointerup', onPointerEnd);
    el.addEventListener('pointercancel', (e) => {
      clearPress();
      didHold = false;
      try {
        if (el.hasPointerCapture?.(e.pointerId)) el.releasePointerCapture(e.pointerId);
      } catch {}
    });

    el.addEventListener('click', (e) => {
      if (didHold) e.preventDefault();
    });
  },

  init() {
    if (this.maybeShowDebugLog()) return null;
    if (!this.isListenerPage()) return null;

    this.setupDebugGesture();
    this.ensureShell();
    document.body.classList.toggle('live-station-page', this.isStationPage());
    document.body.classList.toggle('live-home-page', this.isHomePage());

    // Warm the station list now, well ahead of any lock-screen/CarPlay
    // nexttrack press. iOS grants autoplay for a media-session action only
    // briefly, and switchToAdjacentStation awaits this list before it can
    // even pick the next station — a cold sessionStorage cache (common after
    // iOS kills and relaunches a backgrounded PWA) would otherwise force a
    // live network fetch inside that narrow gesture window.
    if (typeof RadioApp !== 'undefined' && RadioApp.isMobileStation?.()) {
      void this.ensureStationList();
    }

    const audio = this.getAudio();
    const session = this.readSession();

    document.addEventListener('pointerdown', () => this.primePlayback(), {
      once: true,
      passive: true,
    });

    this.bindSoftNavigation();
    this.bindTabs();
    this.tabsApply();

    if (this.isHomePage()) {
      this.bindMiniUi();
      if (session?.wantLive && session.slug) {
        this.resumeFromSession(session);
      } else if (session?.slug) {
        // Paused/stopped last session — show it in the mini player without
        // attempting to reconnect; that only happens on an explicit tap.
        // Still set streamSrc so a later tap on play has a URL to connect to,
        // and still set up the engine so the mini play button's togglePlay()
        // actually exists to call -- without this, audio._liveEngine is
        // never created and tapping play silently does nothing.
        audio.dataset.streamSrc = session.streamSrc || this.browserStreamUrl(session.slug);
        this.ensureEngine(audio);
        this.updateMiniMeta({ stationName: session.stationName, slug: session.slug });
        this.syncMiniVisibility();
        void this.refreshMiniNowPlaying(session.slug);
      } else {
        this.syncMiniVisibility();
      }
    }

    return audio;
  },

  isPlayingSlug(slug) {
    const audio = this.getAudio();
    return Boolean(
      slug &&
      audio?.dataset?.wantLive === '1' &&
      this.activePlayingSlug() === slug
    );
  },

  isBrowsingOtherStation(viewingSlug) {
    const audio = this.getAudio();
    const playingSlug = this.activePlayingSlug();
    return Boolean(
      viewingSlug &&
      audio?.dataset?.wantLive === '1' &&
      playingSlug &&
      playingSlug !== viewingSlug
    );
  },

  slugFromStationUrl(url) {
    try {
      const parsed = new URL(url, location.origin);
      if (!/station(?:\.html)?$/i.test(parsed.pathname.replace(/\/$/, ''))) return '';
      return parsed.searchParams.get('slug') || '';
    } catch {
      return '';
    }
  },

  isOnSoftHome() {
    return document.body.classList.contains('live-home-page');
  },

  async syncPlayingMediaSession() {
    const session = this.readSession();
    if (!session?.wantLive || !session.slug || typeof RadioApp === 'undefined') return;
    try {
      const s = await RadioApp.fetchJSON(`/api/stations/${encodeURIComponent(session.slug)}`);
      const np = s.now_playing;
      const audio = this.getAudio();
      const playing = Boolean(audio && !audio.paused && audio.dataset.wantLive === '1');
      if (this.activePlayingSlug() === s.slug) {
        this.updateHeardMeta({
          slug: s.slug,
          stationName: s.name,
          np,
          artworkUrl: (np && np.cover_url) || s.artwork_url || '',
        });
      }
      RadioApp.updateMediaSession({
        title: np?.title || s.name,
        artist: np?.artist || s.name,
        album: s.name,
        artworkUrl: (np && np.cover_url) || s.artwork_url || '',
        playing,
      });
    } catch {
      /* keep current lock-screen meta */
    }
  },

  async resumePlaybackForSession(session) {
    const audio = this.getAudio();
    if (!audio || !session?.wantLive || !session.slug) return;

    const listenUrl = session.streamSrc || this.browserStreamUrl(session.slug);
    audio.dataset.streamSrc = listenUrl;
    audio.dataset.wantLive = '1';

    if (typeof RadioApp === 'undefined') return;

    this.ensureEngine(audio);

    if (typeof RadioApp !== 'undefined') {
      RadioApp.applySavedLiveVolume(audio);
    }

    if (audio.paused) {
      try {
        await audio._liveEngine?.connectStream?.(true);
      } catch {
        audio.reconnectLiveStream?.();
      }
    }

    audio._liveUi?.syncStationUi?.();
    audio._liveUi?.syncVizFromAudio?.();
  },

  async switchToStation(station, { hardwareSkip = false } = {}) {
    const audio = this.getAudio();
    if (!audio || !station || typeof RadioApp === 'undefined') return;

    // Guard against overlapping calls (e.g. rapid station-card clicks): only
    // the most recent call is allowed to apply its late-resolving metadata
    // fetch or finalize the "now playing" UI state, so an earlier,
    // already-superseded switch can't clobber a newer one's display after
    // the fact.
    this._switchEpoch = (this._switchEpoch || 0) + 1;
    const myEpoch = this._switchEpoch;

    if (hardwareSkip) {
      AlchemyDiag.log('switch-station-start', { toSlug: station.slug, hardwareSkip });
    }

    if (RadioApp.isMobileStation?.()) {
      void this.ensureStationList();
    }

    this._miniSwitching = true;
    this.stopMiniPoll();

    const fromSlug = this.activePlayingSlug();
    const isSwitch = Boolean(fromSlug && fromSlug !== station.slug);
    this.ensureEngine(audio);

    if (RadioApp.useStripWebAudio?.() && typeof LiveAudioGraph !== 'undefined') {
      if (!audio._liveAudioGraph) {
        audio._liveAudioGraph = LiveAudioGraph.attach(audio);
      }
      audio._liveAudioGraph?.ensureGraph?.();
      const ctx = audio._liveAudioGraph?.audioContext;
      if (ctx?.state === 'suspended') {
        try {
          await ctx.resume();
        } catch {
          /* ignore */
        }
      }
    }

    let tuningWait = Promise.resolve();
    if (isSwitch) {
      // Don't await asset loading here — a lock-screen/CarPlay nexttrack
      // press only grants iOS a brief autoplay window, and any delay before
      // connectStream() below can make the real audio.play() get silently
      // rejected while the device is locked. Run the tuning bed as a
      // fire-and-forget side effect instead of gating the station connect.
      tuningWait = this.ensureTuningReady()
        .then(() => {
          if (typeof LiveTuningFx !== 'undefined') {
            return LiveTuningFx.startSwitch(audio, { fromSlug, toSlug: station.slug });
          }
        })
        .catch(() => {
          /* tuning bed is optional */
        });
    }

    this.applySwitchVisuals(station, { pending: true, switching: isSwitch });

    const listenUrl = this.browserStreamUrl(station);
    audio.dataset.streamSrc = listenUrl;
    audio.dataset.pageStreamSrc = listenUrl;
    audio.dataset.pageSlug = station.slug;
    audio.dataset.wantLive = '1';

    this.mergeSession({
      slug: station.slug,
      stationName: station.name,
      streamSrc: listenUrl,
      wantLive: true,
    });

    this.applySwitchVisuals(station, { switching: isSwitch });
    RadioApp.applySavedLiveVolume(audio);

    if (hardwareSkip) {
      AlchemyDiag.log('connect-stream-call', { toSlug: station.slug });
    }
    const connectTask = audio._liveEngine.connectStream(isSwitch, {
      skipReset: isSwitch && hardwareSkip,
      slowStart: isSwitch && hardwareSkip,
    }).catch(() => {
      audio.reconnectLiveStream?.();
    });

    const metaTask = RadioApp.fetchJSON(
      `/api/stations/${encodeURIComponent(station.slug)}`
    ).then((meta) => {
      if (myEpoch !== this._switchEpoch) return meta;
      this.applySwitchVisuals(meta, { switching: isSwitch });
      if (meta.name && meta.name !== station.name) {
        this.mergeSession({
          slug: meta.slug,
          stationName: meta.name,
        });
      }
      return meta;
    }).catch(() => station);

    await connectTask;
    await tuningWait;
    await metaTask;

    // A newer switchToStation call has already started (rapid station
    // switching) -- let it own finishing up; applying our now-stale state
    // here would flip _miniSwitching off under the current switch's feet
    // and could resync the UI back to this abandoned station.
    if (myEpoch !== this._switchEpoch) return;

    this._miniSwitching = false;
    this.syncMiniVisibility();
    audio._liveUi?.syncStationUi?.();
    audio._liveUi?.syncVizFromAudio?.();
    AlchemyHome?.syncAllCardPlayUi?.();

    if (hardwareSkip) {
      // Snapshot the new audio element's real state a few seconds out --
      // tells us whether it's actually playing, stuck buffering, or errored,
      // independent of whether the play() promise itself resolved/rejected.
      setTimeout(() => {
        AlchemyDiag.log('post-skip-check', {
          toSlug: station.slug,
          paused: audio.paused,
          readyState: audio.readyState,
          networkState: audio.networkState,
          currentTime: audio.currentTime,
          errorCode: audio.error ? audio.error.code : null,
          wantLive: audio.dataset.wantLive,
        });
      }, 4000);
    }
  },

  prepareStationAudio(station) {
    const audio = this.getAudio();
    if (!audio || !station) return null;

    const listenUrl = this.browserStreamUrl(station);
    const session = this.readSession();
    const onThisStation = session?.wantLive && session.slug === station.slug;
    const playingOther = session?.wantLive && session.slug && session.slug !== station.slug;

    audio.dataset.pageStreamSrc = listenUrl;
    audio.dataset.pageSlug = station.slug;

    if (onThisStation || playingOther) {
      audio.dataset.wantLive = '1';
    }

    if (onThisStation) {
      audio.dataset.streamSrc = listenUrl;
    } else if (playingOther) {
      audio.dataset.streamSrc = session.streamSrc || this.browserStreamUrl(session.slug);
    } else {
      audio.dataset.streamSrc = listenUrl;
    }

    if (typeof RadioApp !== 'undefined') {
      this.ensureEngine(audio);
    }

    if (!playingOther) {
      const patch = {
        slug: station.slug,
        stationName: station.name,
        streamSrc: listenUrl,
      };
      if (onThisStation) patch.wantLive = true;
      this.mergeSession(patch);
    }

    this.syncMiniVisibility();
    return audio;
  },

  notifyStationMeta(station) {
    const audio = this.getAudio();
    if (!audio || !station) return;

    const onThisStation = this.activePlayingSlug() === station.slug;
    const np = station.now_playing;
    const playing = !audio.paused && audio.dataset.wantLive === '1';

    if (playing && onThisStation) {
      this.mergeSession({
        slug: station.slug,
        stationName: station.name,
        streamSrc: this.browserStreamUrl(station),
        wantLive: true,
      });
    }

    if (typeof RadioApp !== 'undefined') {
      if (playing && onThisStation) {
        RadioApp.updateMediaSession({
          title: np?.title || station.name,
          artist: np?.artist || station.name,
          album: station.name,
          artworkUrl: (np && np.cover_url) || station.artwork_url || '',
          playing: true,
        });
      } else if (playing && !onThisStation) {
        this.syncPlayingMediaSession();
      } else {
        RadioApp.updateMediaSession({
          title: np?.title || station.name,
          artist: np?.artist || station.name,
          album: station.name,
          artworkUrl: (np && np.cover_url) || station.artwork_url || '',
          playing: false,
        });
      }

      RadioApp.updateTabTitle({
        stationName: station.name,
        nowPlaying: onThisStation ? np : null,
        playing: playing && onThisStation,
      });
    }

    if (playing && onThisStation) {
      this.onSessionChange(audio, {
        slug: station.slug,
        stationName: station.name,
        nowPlaying: np,
        artworkUrl: (np && np.cover_url) || station.artwork_url || '',
      });
    }
  },
};

if (GlobalLivePlayer.isListenerPage()) {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => GlobalLivePlayer.init());
  } else {
    GlobalLivePlayer.init();
  }
}
