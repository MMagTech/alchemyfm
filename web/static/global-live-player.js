/**
 * Shared live stream audio + bottom mini-player for listener pages.
 * Persists tune-in across navigation via sessionStorage + soft navigation.
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
      const raw = sessionStorage.getItem(this.STORAGE_KEY);
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
      const raw = sessionStorage.getItem(this.STATIONS_CACHE_KEY);
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
        sessionStorage.setItem(this.STATIONS_CACHE_KEY, JSON.stringify(stations));
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

    const stations = await this.ensureStationList();
    if (!stations.length) return;

    this._stationSkipInFlight = true;
    try {
      const slug = this.activePlayingSlug() || this.readSession()?.slug;
      let idx = stations.findIndex((s) => s.slug === slug);
      if (idx < 0) idx = 0;
      const nextIdx = (idx + delta + stations.length) % stations.length;
      await this.switchToStation(stations[nextIdx]);
    } finally {
      this._stationSkipInFlight = false;
    }
  },

  writeSession(data) {
    try {
      if (!data) {
        sessionStorage.removeItem(this.STORAGE_KEY);
        return;
      }
      sessionStorage.setItem(this.STORAGE_KEY, JSON.stringify(data));
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

  clearSession() {
    this.writeSession(null);
    this._heardMeta = null;
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
    const slug = typeof station === 'string' ? station : station.slug;
    const proxy = new URL(
      `/api/stations/${encodeURIComponent(slug)}/listen`,
      location.origin
    ).href;
    const direct = typeof station === 'object' ? station.stream_url : '';
    if (direct) {
      try {
        const url = new URL(direct);
        if (url.origin === location.origin) return url.href;
      } catch {
        /* use proxy */
      }
    }
    return proxy;
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
    </div>`;
    document.body.appendChild(shell);
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
    const active = session?.wantLive === true || audio.dataset.wantLive === '1';
    const onHome = this.isHomePage() || this.isOnSoftHome();
    const streamSlug = this.slugFromStreamSrc(audio.dataset.streamSrc || audio.src);
    const activeSlug = streamSlug || session?.slug;
    const show = onHome && active && Boolean(activeSlug);

    bar.hidden = !show;
    document.body.classList.toggle('has-live-mini-player', show);

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
    const prevTrackKey = art.dataset.trackKey || '';
    const prevSlug = art.dataset.stationSlug || '';
    if (slug) art.dataset.stationSlug = slug;
    if (
      forceGlitch ||
      (trackKey && prevTrackKey && prevTrackKey !== trackKey) ||
      (slug && prevSlug && prevSlug !== slug)
    ) {
      art.innerHTML = art.dataset.placeholder;
    }
    RadioApp.setCoverImage(art, artworkUrl, trackKey, {
      waitForLoad: true,
      forceGlitch: Boolean(
        forceGlitch ||
        (trackKey && prevTrackKey && prevTrackKey !== trackKey) ||
        (slug && prevSlug && prevSlug !== slug)
      ),
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
      this.clearSession();
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
      this.updateHeardMeta({
        slug: session.slug,
        stationName: session.stationName,
        np: meta.nowPlaying,
        artworkUrl: meta.artworkUrl,
      });
      this.updateMiniMeta({
        stationName: session.stationName,
        slug: session.slug,
        np: meta.nowPlaying,
        artworkUrl: meta.artworkUrl,
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
    add('/static/home-desktop.css?v=16', '(min-width: 641px)');
    add('/static/home-mobile.css?v=11', '(max-width: 640px)');
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
      await this.loadScriptOnce('/static/tuning-static.js?v=4', 'tuning-static');
    }
    if (typeof StripVisualizer === 'undefined') {
      await this.loadScriptOnce('/static/strip-visualizer.js?v=3', 'strip-visualizer');
    }

    await this.loadScriptOnce('/static/station-boot.js?v=9', 'station-boot');
  },

  async ensureTuningReady() {
    if (typeof LiveAudioGraph === 'undefined') {
      await this.loadScriptOnce('/static/live-audio-graph.js?v=8', 'live-audio-graph');
    }
    if (typeof LiveTuningFx === 'undefined') {
      await this.loadScriptOnce('/static/tuning-static.js?v=4', 'tuning-static');
    }
  },

  homeMainMarkup() {
    return `
      <p class="subtitle">Curated stations on the air. Everyone tuned in hears the same track.</p>
      <div id="stations" class="station-grid">
        <p class="empty">Loading stations…</p>
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
      main.innerHTML = '<p class="empty">Loading…</p>';
    }

    const nav = document.querySelector('header nav');
    if (nav) nav.hidden = false;

    document.body.classList.remove('live-home-page');
    document.body.classList.add('live-station-page');
    document.title = 'Station — Alchemy FM';

    history.pushState({ alchemyfm: 'station', slug }, '', this.stationUrl(slug));
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

  init() {
    if (!this.isListenerPage()) return null;

    this.ensureShell();
    document.body.classList.toggle('live-station-page', this.isStationPage());
    document.body.classList.toggle('live-home-page', this.isHomePage());

    const audio = this.getAudio();
    const session = this.readSession();

    document.addEventListener('pointerdown', () => this.primePlayback(), {
      once: true,
      passive: true,
    });

    this.bindSoftNavigation();

    if (this.isHomePage()) {
      this.bindMiniUi();
      if (session?.wantLive && session.slug) {
        this.resumeFromSession(session);
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

  isPlayingOtherThan(slug) {
    if (!this.isBrowsingOtherStation(slug)) return false;
    const audio = this.getAudio();
    return Boolean(audio?.dataset?.wantLive === '1' && !audio.paused);
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

  async switchToStation(station) {
    const audio = this.getAudio();
    if (!audio || !station || typeof RadioApp === 'undefined') return;

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
      try {
        await this.ensureTuningReady();
      } catch {
        /* tuning bed is optional */
      }
      if (typeof LiveTuningFx !== 'undefined') {
        tuningWait = LiveTuningFx.startSwitch(audio, { fromSlug, toSlug: station.slug });
      }
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

    const connectTask = audio._liveEngine.connectStream(isSwitch).catch(() => {
      audio.reconnectLiveStream?.();
    });

    const metaTask = RadioApp.fetchJSON(
      `/api/stations/${encodeURIComponent(station.slug)}`
    ).then((meta) => {
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

    this._miniSwitching = false;
    this.syncMiniVisibility();
    audio._liveUi?.syncStationUi?.();
    audio._liveUi?.syncVizFromAudio?.();
    AlchemyHome?.syncAllCardPlayUi?.();
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
