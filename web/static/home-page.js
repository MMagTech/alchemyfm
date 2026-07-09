/**
 * All Stations grid — used on index.html and after soft-nav from a station page.
 */
const AlchemyHome = {
  _pollTimer: null,
  _resizeHandler: null,
  _gridAbort: null,
  _mounted: false,
  _stationsCacheKey: 'alchemyfm-stations-cache',
  _stationsBySlug: new Map(),
  _playInFlight: null,

  artMountHtml() {
    return `<div class="station-card-art-mount" data-img-class="station-card-art"
      data-placeholder='<div class="station-card-art station-card-art-placeholder">♪</div>'></div>
      <button type="button" class="station-card-play-btn" aria-label="Listen">
        <span class="station-card-play-icon" aria-hidden="true"></span>
      </button>`;
  },

  ensureArtMount(card) {
    const wrap = card.querySelector('.station-card-art-wrap');
    if (!wrap) return null;

    let mount = wrap.querySelector('.station-card-art-mount');
    if (mount) return mount;

    const placeholder = wrap.dataset.placeholder ||
      '<div class="station-card-art station-card-art-placeholder">♪</div>';
    wrap.innerHTML = `<div class="station-card-art-mount" data-img-class="station-card-art"
      data-placeholder='${placeholder.replace(/'/g, '&#39;')}'></div>
      <button type="button" class="station-card-play-btn" aria-label="Listen">
        <span class="station-card-play-icon" aria-hidden="true"></span>
      </button>`;
    delete wrap.dataset.imgClass;
    delete wrap.dataset.placeholder;
    return wrap.querySelector('.station-card-art-mount');
  },

  ensureCard(s) {
    let card = document.querySelector(`[data-slug="${CSS.escape(s.slug)}"]`);
    if (card) return card;

    card = document.createElement('article');
    card.className = 'station-card';
    card.dataset.slug = s.slug;
    card.innerHTML = `
      <div class="station-card-art-wrap">
        ${this.artMountHtml()}
      </div>
      <div class="station-card-body">
        <div class="station-card-head">
          <h2></h2>
        </div>
        <div class="station-card-actions">
          <span class="station-card-listeners" title="Live listeners"></span>
          <a class="btn station-card-tune-in" href="#">Open</a>
        </div>
      </div>
      <div class="station-card-now">
        <span class="now-label">On Air</span>
        <span class="now-track"></span>
      </div>`;
    return card;
  },

  syncTrackMarquee(trackEl) {
    RadioApp.syncOverflowMarquee(trackEl, { innerSelector: '.now-track-inner' });
  },

  setNowPlayingTrack(trackEl, np) {
    let inner = trackEl.querySelector('.now-track-inner');
    if (!inner) {
      trackEl.innerHTML = `<span class="now-track-inner"></span>`;
      inner = trackEl.querySelector('.now-track-inner');
    }

    if (!np) {
      trackEl.classList.add('is-waiting');
      inner.textContent = 'Starting up…';
      trackEl.title = 'Starting up…';
      this.syncTrackMarquee(trackEl);
      return;
    }

    trackEl.classList.remove('is-waiting');
    const label = `${np.artist} — ${np.title}`;
    inner.innerHTML = `
      <span class="now-track-artist">${RadioApp.escape(np.artist)}</span><span class="now-track-sep" aria-hidden="true"> — </span><span class="now-track-title">${RadioApp.escape(np.title)}</span>`;
    trackEl.title = label;
    this.syncTrackMarquee(trackEl);
  },

  syncCardPlayUi(card) {
    if (!card) return;
    const slug = card.dataset.slug;
    const btn = card.querySelector('.station-card-play-btn');
    const name = card.querySelector('.station-card-head h2')?.textContent || 'station';
    if (!btn || !slug || typeof GlobalLivePlayer === 'undefined') return;

    const audio = GlobalLivePlayer.getAudio();
    const onThis = GlobalLivePlayer.isPlayingSlug(slug);
    const wantLive = audio?.dataset?.wantLive === '1';
    const playing = Boolean(onThis && wantLive && audio && !audio.paused);
    const connecting = Boolean(onThis && wantLive && audio?.paused);

    card.classList.toggle('is-heard', onThis && wantLive);
    btn.classList.toggle('is-playing', playing);
    btn.classList.toggle('is-connecting', connecting && !playing);
    btn.disabled = Boolean(this._playInFlight === slug);

    if (playing) {
      btn.setAttribute('aria-label', `Pause ${name}`);
    } else if (connecting) {
      btn.setAttribute('aria-label', `Connecting to ${name}`);
    } else {
      btn.setAttribute('aria-label', `Listen to ${name}`);
    }
  },

  syncAllCardPlayUi() {
    document.querySelectorAll('.station-card[data-slug]').forEach((card) => {
      this.syncCardPlayUi(card);
    });
  },

  pulseCardSwitch(slug) {
    if (!slug) return;
    const card = document.querySelector(`[data-slug="${CSS.escape(slug)}"]`);
    const artMount = card ? this.ensureArtMount(card) : null;
    if (!artMount) return;

    const frame = artMount.querySelector('.cover-frame');
    if (frame) {
      frame.classList.add('cover-switching');
      const done = () => frame.classList.remove('cover-switching');
      frame.addEventListener('animationend', done, { once: true });
      setTimeout(done, 700);
      return;
    }

    artMount.classList.add('cover-switching');
    const done = () => artMount.classList.remove('cover-switching');
    artMount.addEventListener('animationend', done, { once: true });
    setTimeout(done, 700);
  },

  async handleCardPlay(slug) {
    const station = this._stationsBySlug.get(slug);
    if (!station || typeof GlobalLivePlayer === 'undefined') return;

    const audio = GlobalLivePlayer.getAudio();
    if (!audio) return;

    const onThis = GlobalLivePlayer.isPlayingSlug(slug);
    const wantLive = audio.dataset?.wantLive === '1';

    if (onThis && wantLive) {
      audio._liveEngine?.togglePlay?.();
      this.syncAllCardPlayUi();
      GlobalLivePlayer.syncMiniVisibility?.();
      return;
    }

    this._playInFlight = slug;
    this.syncAllCardPlayUi();
    GlobalLivePlayer.primePlayback?.();
    try {
      await GlobalLivePlayer.switchToStation(station);
    } finally {
      this._playInFlight = null;
      this.syncAllCardPlayUi();
    }
  },

  onGridClick(e) {
    const playBtn = e.target.closest('.station-card-play-btn');
    if (!playBtn || playBtn.disabled) return;

    const card = playBtn.closest('.station-card');
    const slug = card?.dataset?.slug;
    if (!slug) return;

    e.preventDefault();
    e.stopPropagation();
    void this.handleCardPlay(slug);
  },

  bindGridUi() {
    if (this._gridAbort) {
      this._gridAbort.abort();
    }
    this._gridAbort = new AbortController();
    const { signal } = this._gridAbort;

    const root = document.getElementById('stations');
    root?.addEventListener('click', (e) => this.onGridClick(e), { signal });

    const audio = typeof GlobalLivePlayer !== 'undefined'
      ? GlobalLivePlayer.getAudio()
      : null;
    if (audio) {
      ['play', 'pause', 'playing'].forEach((ev) => {
        audio.addEventListener(ev, () => this.syncAllCardPlayUi(), { signal });
      });
    }
  },

  updateCard(s) {
    const card = this.ensureCard(s);
    this._stationsBySlug.set(s.slug, s);
    const np = s.now_playing;
    const trackKey = RadioApp.trackKey(np);
    const coverUrl = (np && np.cover_url) ? np.cover_url : (s.artwork_url || '');

    card.querySelector('h2').textContent = s.name;
    const listenersEl = card.querySelector('.station-card-listeners');
    const count = Number(s.listeners) || 0;
    listenersEl.textContent = count === 1 ? '1 Listening' : `${count} Listening`;
    listenersEl.classList.toggle('is-live', count > 0);
    listenersEl.setAttribute('aria-label', `${count} live listeners`);
    const trackEl = card.querySelector('.now-track');
    if (trackEl.dataset.trackKey !== trackKey) {
      trackEl.dataset.trackKey = trackKey || '';
      this.setNowPlayingTrack(trackEl, np);
      trackEl.classList.add('track-switch');
      requestAnimationFrame(() => trackEl.classList.remove('track-switch'));
    } else {
      this.setNowPlayingTrack(trackEl, np);
    }
    card.querySelector('.station-card-tune-in').href = `/station.html?slug=${encodeURIComponent(s.slug)}`;
    card.querySelector('.station-card-tune-in').textContent = 'Open';

    const artMount = this.ensureArtMount(card);
    if (artMount) {
      if (!coverUrl) {
        artMount.dataset.trackKey = '';
        artMount.innerHTML = artMount.dataset.placeholder;
      } else {
        RadioApp.setCoverImage(artMount, coverUrl, trackKey);
      }
    }

    this.syncCardPlayUi(card);
    return card;
  },

  renderStations(stations) {
    const root = document.getElementById('stations');
    if (!root || !stations?.length) return false;
    const seen = new Set();
    root.querySelector('.empty')?.remove();
    const frag = document.createDocumentFragment();
    stations.forEach((s) => {
      seen.add(s.slug);
      frag.appendChild(this.updateCard(s));
    });
    root.querySelectorAll('[data-slug]').forEach((el) => {
      if (!seen.has(el.dataset.slug)) {
        this._stationsBySlug.delete(el.dataset.slug);
        el.remove();
      }
    });
    root.appendChild(frag);
    this.syncAllCardPlayUi();
    return true;
  },

  readStationsCache() {
    try {
      const raw = sessionStorage.getItem(this._stationsCacheKey);
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
        sessionStorage.setItem(this._stationsCacheKey, JSON.stringify(stations));
        if (typeof GlobalLivePlayer !== 'undefined') {
          GlobalLivePlayer._stationList = stations;
        }
      }
    } catch {}
  },

  async loadStations() {
    const root = document.getElementById('stations');
    if (!root) return;

    const cached = this.readStationsCache();
    if (cached?.length) {
      this.renderStations(cached);
    }

    try {
      const stations = await RadioApp.fetchJSON('/api/stations');
      if (!stations.length) {
        root.innerHTML = '<p class="empty">No stations on air yet.</p>';
        return;
      }
      this.writeStationsCache(stations);
      this.renderStations(stations);
    } catch (err) {
      if (cached?.length) return;
      root.innerHTML = `<p class="empty">Could not load stations: ${RadioApp.escape(err.message)}</p>`;
    }
  },

  mount() {
    if (!document.getElementById('stations')) return;
    this.unmount();
    this._mounted = true;
    this.bindGridUi();
    this.loadStations();
    this._pollTimer = setInterval(() => this.loadStations(), 8000);
    this._resizeHandler = () => {
      document.querySelectorAll('.now-track').forEach((el) => this.syncTrackMarquee(el));
    };
    window.addEventListener('resize', this._resizeHandler);
  },

  unmount() {
    this._mounted = false;
    this._gridAbort?.abort();
    this._gridAbort = null;
    clearInterval(this._pollTimer);
    this._pollTimer = null;
    if (this._resizeHandler) {
      window.removeEventListener('resize', this._resizeHandler);
      this._resizeHandler = null;
    }
  },
};
