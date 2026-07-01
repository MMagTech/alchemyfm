const RadioApp = {
  async fetchJSON(url, options = {}) {
    const res = await fetch(url, { credentials: 'same-origin', ...options });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || data.error || res.statusText);
    }
    return data;
  },

  escape(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  },

  escapeAttr(s) {
    return this.escape(s).replace(/'/g, '&#39;');
  },

  /** Stable key for now-playing cover updates */
  trackKey(np) {
    if (!np) return '';
    return np.item_id || `${np.artist}\0${np.title}`;
  },

  setCoverImage(container, url, trackKey) {
    if (!container) return;
    const prevKey = container.dataset.trackKey || '';
    const prevUrl = container.dataset.coverUrl || '';
    const nextUrl = url || '';
    if (trackKey && prevKey === trackKey && prevUrl === nextUrl) return;

    const placeholder = container.dataset.placeholder || '';
    const imgClass = container.dataset.imgClass || '';

    if (!url) {
      container.dataset.trackKey = trackKey || '';
      container.dataset.coverUrl = '';
      container.innerHTML = placeholder;
      return;
    }

    const isSwitch = !!(prevKey && prevKey !== (trackKey || '') &&
      container.querySelector('.cover-frame, img, .artwork, .station-card-art'));

    const mount = () => {
      container.dataset.trackKey = trackKey || '';
      container.dataset.coverUrl = nextUrl;
      const frameClass = imgClass ? ` ${imgClass}` : '';
      container.innerHTML = `
        <div class="cover-frame${frameClass}">
          <img class="cover-img" src="${this.escapeAttr(url)}" alt="">
          <div class="cover-static" aria-hidden="true"></div>
          <div class="cover-glitch-slice" aria-hidden="true"></div>
        </div>`;
      if (!isSwitch) return;
      const frame = container.querySelector('.cover-frame');
      requestAnimationFrame(() => {
        frame.classList.add('cover-switching');
        const done = () => frame.classList.remove('cover-switching');
        frame.addEventListener('animationend', done, { once: true });
        setTimeout(done, 700);
      });
    };

    if (isSwitch) {
      const probe = new Image();
      probe.onload = mount;
      probe.onerror = mount;
      probe.src = url;
      return;
    }
    mount();
  },

  formatListenTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  },

  absoluteMediaUrl(url) {
    if (!url) return '';
    try {
      return new URL(url, location.origin).href;
    } catch {
      return String(url);
    }
  },

  lockScreenArtworkUrl(url) {
    const abs = this.absoluteMediaUrl(url);
    if (!abs) return '';
    try {
      const u = new URL(abs);
      if (u.pathname.startsWith('/api/cover/')) {
        u.searchParams.set('size', '512');
      }
      return u.href;
    } catch {
      return abs;
    }
  },

  buildMediaSessionArtwork(url) {
    const src = this.lockScreenArtworkUrl(url);
    if (!src) return [];
    return ['96x96', '128x128', '256x256', '512x512'].map((sizes) => ({
      src,
      sizes,
      type: 'image/png',
    }));
  },

  updateMediaSession({ title, artist, album, artworkUrl, playing } = {}) {
    if (!('mediaSession' in navigator)) return;

    if (title || artist) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: title || 'Live',
        artist: artist || '',
        album: album || '',
        artwork: this.buildMediaSessionArtwork(artworkUrl),
      });
    } else {
      navigator.mediaSession.metadata = null;
    }

    if (playing !== undefined) {
      navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
    }
  },

  formatTabTitleNowPlaying(artist, title, stationName, maxLen = 50) {
    const suffix = ` · ${stationName}`;
    const a = String(artist || '').trim();
    const t = String(title || '').trim();
    if (!a && !t) return `${stationName} — Alchemy FM`;

    let core = a && t ? `${a} — ${t}` : (t || a);
    const maxCore = Math.max(12, maxLen - suffix.length);
    if (core.length > maxCore) {
      core = `${core.slice(0, maxCore - 1).trimEnd()}…`;
    }
    return `${core}${suffix}`;
  },

  updateTabTitle({ stationName, nowPlaying, playing } = {}) {
    if (!stationName) return;

    const defaultTitle = `${stationName} — Alchemy FM`;
    const np = nowPlaying;
    const hasTrack = Boolean(np?.title?.trim() && np?.artist?.trim());
    const showTrack = Boolean(playing && hasTrack);

    const nextTitle = showTrack
      ? this.formatTabTitleNowPlaying(np.artist, np.title, stationName)
      : defaultTitle;

    if (document.title !== nextTitle) {
      document.title = nextTitle;
    }
  },

  wireMediaSessionControls(audio, onPlay, onPause) {
    if (!('mediaSession' in navigator)) return;

    const setHandler = (action, fn) => {
      try {
        navigator.mediaSession.setActionHandler(action, fn);
      } catch {
        /* action unsupported */
      }
    };

    setHandler('play', () => onPlay?.());
    setHandler('pause', () => onPause?.());
    setHandler('stop', () => onPause?.());
    setHandler('previoustrack', null);
    setHandler('nexttrack', null);
  },

  async copyText(text, button) {
    const showCopied = () => {
      if (!button) return;
      const prev = button.textContent;
      button.textContent = 'Copied!';
      button.classList.add('is-copied');
      setTimeout(() => {
        button.textContent = prev;
        button.classList.remove('is-copied');
      }, 1500);
    };

    try {
      await navigator.clipboard.writeText(text);
      showCopied();
      return true;
    } catch {
      /* fallback for non-secure contexts */
    }

    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch {
      ok = false;
    }
    document.body.removeChild(ta);
    if (ok) showCopied();
    return ok;
  },

  /** Custom live player — play/pause, tuned-in timer, vertical volume, spectrum visualizer. */
  initLivePlayer(audio) {
    if (!audio || audio.dataset.playerReady === '1') return;
    audio.dataset.playerReady = '1';
    audio.classList.add('live-audio-hidden');

    const btn = document.getElementById('live-play-btn');
    const timer = document.getElementById('live-timer');
    const status = document.getElementById('live-status-text');
    const dot = document.getElementById('live-status-dot');
    const vol = document.getElementById('live-volume');
    const stripCanvas = document.getElementById('live-visualizer');
    if (!btn || !timer || !status || !vol) return;

    let tick = null;
    let reconnectTimer = null;
    let reconnectAttempt = 0;
    let streamLive = false;
    let stallTimer = null;
    let connectInFlight = false;
    let lastProgressAt = 0;
    const graph = typeof LiveAudioGraph !== 'undefined'
      ? LiveAudioGraph.attach(audio)
      : null;
    if (graph) {
      audio._liveAudioGraph = graph;
    }
    const viz = stripCanvas && graph
      ? StripVisualizer.create(graph, stripCanvas)
      : null;
    if (viz) {
      audio._stripViz = viz;
    }

    const baseStreamUrl = () => {
      const raw = audio.dataset.streamSrc || audio.src || '';
      if (!raw) return '';
      try {
        const url = new URL(raw, location.origin);
        url.search = '';
        return url.href;
      } catch {
        return String(raw).split('?')[0];
      }
    };

    const setIdleUi = (message = 'Ready') => {
      btn.classList.remove('is-playing');
      btn.setAttribute('aria-label', 'Play');
      status.textContent = message;
      status.classList.remove('is-live');
      if (dot) dot.classList.remove('is-live');
      if (viz) viz.stop();
    };

    const setConnectingUi = (message = 'Connecting…') => {
      btn.classList.remove('is-playing');
      btn.setAttribute('aria-label', 'Play');
      status.textContent = message;
      status.classList.remove('is-live');
      if (dot) dot.classList.remove('is-live');
      if (viz) viz.stop();
    };

    const setPlayingUi = (playing) => {
      btn.classList.toggle('is-playing', playing);
      btn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
      if (playing) {
        status.textContent = 'Live';
        status.classList.add('is-live');
        if (dot) dot.classList.add('is-live');
        if (viz) viz.start();
      } else {
        status.classList.remove('is-live');
        if (dot) dot.classList.remove('is-live');
        if (viz) viz.stop();
      }
    };

    const pauseAndFlushBuffer = () => {
      const base = baseStreamUrl();
      if (base) audio.dataset.streamSrc = base;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      clearInterval(tick);
      timer.textContent = '0:00';
      btn.classList.remove('is-playing');
      btn.setAttribute('aria-label', 'Play');
      status.classList.remove('is-live');
      if (dot) dot.classList.remove('is-live');
      if (viz) viz.stop();
    };

    const connectStream = (bustCache = false) => {
      if (connectInFlight) return Promise.resolve();
      const base = baseStreamUrl();
      if (!base) return Promise.reject(new Error('No stream URL'));
      audio.dataset.streamSrc = base;
      let src = base;
      if (bustCache) {
        const url = new URL(base, location.origin);
        url.searchParams.set('t', String(Date.now()));
        src = url.href;
      }
      connectInFlight = true;
      setConnectingUi(bustCache ? 'Reconnecting…' : 'Connecting…');
      if (audio.src && audio.src !== src) {
        audio.pause();
      }
      audio.src = src;
      audio.load();
      return audio.play().finally(() => {
        connectInFlight = false;
      });
    };

    const setWantLive = (want) => {
      audio.dataset.wantLive = want ? '1' : '0';
      if (!want) {
        clearTimeout(reconnectTimer);
        reconnectAttempt = 0;
        clearStallWatch();
      }
    };

    const scheduleReconnect = () => {
      if (audio.dataset.wantLive !== '1') return;
      clearTimeout(reconnectTimer);
      const delay = Math.min(15000, 2000 + reconnectAttempt * 2000);
      reconnectTimer = setTimeout(() => {
        reconnectAttempt += 1;
        connectStream(true).catch(() => scheduleReconnect());
      }, delay);
    };

    const markStreamOffline = (message = 'Off air') => {
      if (audio.dataset.wantLive !== '1') return;
      streamLive = false;
      clearTimeout(reconnectTimer);
      clearStallWatch();
      pauseAndFlushBuffer();
      setIdleUi(message);
      scheduleReconnect();
    };

    const bufferAheadSec = () => {
      if (!audio.buffered.length) return 0;
      return Math.max(0, audio.buffered.end(audio.buffered.length - 1) - audio.currentTime);
    };

    const checkBufferDrain = () => {
      if (audio.dataset.wantLive !== '1' || audio.paused || !streamLive) return;
      const sinceProgress = Date.now() - (lastProgressAt || Date.now());
      if (sinceProgress > 3500 && bufferAheadSec() < 1.0) {
        markStreamOffline('Stream interrupted');
      }
    };

    const clearStallWatch = () => {
      clearTimeout(stallTimer);
      stallTimer = null;
    };

    const armStallWatch = () => {
      clearStallWatch();
      if (audio.dataset.wantLive !== '1' || audio.paused || !streamLive) return;
      stallTimer = setTimeout(() => {
        if (audio.dataset.wantLive !== '1' || audio.paused || !streamLive) return;
        if (audio.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) {
          armStallWatch();
          return;
        }
        markStreamOffline('Stream interrupted');
      }, 12000);
    };

    const setVolume = (value) => {
      const v = parseFloat(value);
      if (graph?.gain) {
        graph.gain.gain.value = v;
      } else {
        audio.volume = v;
      }
    };

    const updateTimer = () => {
      if (!audio.paused) {
        timer.textContent = this.formatListenTime(audio.currentTime);
      }
    };

    const primeAudioGraph = () => {
      graph?.ensureGraph?.();
      if (graph?.audioContext?.state === 'suspended') {
        graph.audioContext.resume();
      }
    };

    btn.addEventListener('click', () => {
      if (audio.paused) {
        setWantLive(true);
        reconnectAttempt = 0;
        clearTimeout(reconnectTimer);
        primeAudioGraph();
        connectStream(true).catch(() => scheduleReconnect());
      } else {
        setWantLive(false);
        audio.pause();
        setIdleUi('Paused');
      }
    });

    this.wireMediaSessionControls(
      audio,
      () => {
        if (audio.paused) btn.click();
      },
      () => {
        if (!audio.paused) btn.click();
      },
    );

    audio.addEventListener('play', () => {
      setWantLive(true);
      clearInterval(tick);
      tick = setInterval(updateTimer, 1000);
      updateTimer();
    });

    audio.addEventListener('pause', () => {
      clearInterval(tick);
      clearStallWatch();
      if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = 'paused';
      }
      if (audio.dataset.wantLive !== '1') {
        setPlayingUi(false);
        status.textContent = 'Paused';
      } else if (!btn.classList.contains('is-playing')) {
        /* keep Off air / Reconnecting label */
      } else {
        btn.classList.remove('is-playing');
        btn.setAttribute('aria-label', 'Play');
        if (viz) viz.stop();
      }
    });

    audio.addEventListener('waiting', () => {
      setConnectingUi('Buffering…');
      armStallWatch();
    });

    audio.addEventListener('playing', () => {
      reconnectAttempt = 0;
      clearTimeout(reconnectTimer);
      lastProgressAt = Date.now();
      setPlayingUi(true);
      armStallWatch();
      if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = 'playing';
      }
    });

    audio.addEventListener('stalled', () => {
      setConnectingUi('Buffering…');
      armStallWatch();
    });

    audio.addEventListener('timeupdate', () => {
      if (!audio.paused && audio.dataset.wantLive === '1') {
        armStallWatch();
        checkBufferDrain();
      }
    });

    audio.addEventListener('progress', () => {
      lastProgressAt = Date.now();
    });

    audio.addEventListener('error', () => {
      clearInterval(tick);
      clearStallWatch();
      const base = baseStreamUrl();
      if (base) audio.dataset.streamSrc = base;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      setIdleUi('Stream error');
      scheduleReconnect();
    });

    const savedVol = localStorage.getItem('radio-volume');
    if (savedVol != null) {
      vol.value = savedVol;
      setVolume(savedVol);
    }

    vol.addEventListener('input', () => {
      setVolume(vol.value);
      localStorage.setItem('radio-volume', vol.value);
    });

    setIdleUi('Ready');

    audio.reconnectLiveStream = () => {
      if (audio.dataset.wantLive !== '1') return;
      reconnectAttempt = 0;
      clearTimeout(reconnectTimer);
      connectStream(true).catch(() => scheduleReconnect());
    };

    audio.setStreamLive = (live) => {
      const next = Boolean(live);
      if (streamLive === next) return;
      const wasLive = streamLive;
      streamLive = next;
      if (!next) {
        if (audio.dataset.wantLive !== '1') return;
        const playing = btn.classList.contains('is-playing') || !audio.paused;
        if (playing && wasLive) {
          markStreamOffline('Off air');
        } else if (!connectInFlight) {
          status.textContent = 'Off air';
          status.classList.remove('is-live');
          if (dot) dot.classList.remove('is-live');
        }
        return;
      }
      if (audio.dataset.wantLive === '1' && audio.paused && !connectInFlight) {
        reconnectAttempt = 0;
        clearTimeout(reconnectTimer);
        connectStream(true).catch(() => scheduleReconnect());
      }
    };

    audio.forceStreamOffline = (message = 'Off air') => {
      markStreamOffline(message);
    };
  },

  _milkdropMod: null,

  async loadMilkdropFullscreen() {
    if (!this._milkdropMod) {
      this._milkdropMod = await import('/static/butterchurn-fullscreen.js?v=12');
    }
    return this._milkdropMod;
  },

  milkdropWebGL2Supported() {
    try {
      const canvas = document.createElement('canvas');
      return !!canvas.getContext('webgl2');
    } catch {
      return false;
    }
  },

  isMobileStation() {
    if (/iPhone|iPad|iPod/i.test(navigator.userAgent)) return true;
    return window.matchMedia('(max-width: 640px)').matches;
  },

  fullscreenVizAvailable() {
    return !this.isMobileStation() && this.milkdropWebGL2Supported();
  },

  formatOutgoingBandwidth(kbps) {
    const n = Number(kbps) || 0;
    if (n >= 1000) return `${(n / 1000).toFixed(1)} Mbps`;
    return `${Math.round(n)} kbps`;
  },

  _adminToastTimer: null,

  clearAdminToast(hostId = 'broadcast-message') {
    if (this._adminToastTimer) {
      clearTimeout(this._adminToastTimer);
      this._adminToastTimer = null;
    }
    const el = document.getElementById(hostId);
    if (!el) return;
    el.replaceChildren();
    el.className = 'broadcast-toast-host';
    el.hidden = true;
  },

  resolveAdminToastCopy(text, type, variant = '') {
    let title = type === 'error' ? 'Something went wrong' : 'Saved';
    let body = text;
    if (variant === 'icecast' && type === 'success') {
      title = 'Icecast restarted';
      body = 'Streams should reconnect in a few seconds.';
    } else if (variant === 'icecast' && type === 'error') {
      title = 'Icecast restart failed';
    } else if (type === 'success' && text === 'Broadcast settings saved.') {
      title = 'Broadcast settings saved';
      body = 'Encoding and listener limits are updated.';
    } else if (variant === 'appearance' && type === 'success') {
      title = 'Default theme saved';
      body = text;
    } else if (variant === 'appearance' && type === 'error') {
      title = 'Could not save theme';
      body = text;
    } else if (variant === 'knowledge' && type === 'success') {
      title = 'Knowledge settings saved';
      body = 'Enrichment and on-air display settings are updated.';
    } else if (variant === 'knowledge' && type === 'error') {
      title = 'Could not save settings';
      body = text;
    } else if (variant === 'knowledge-purge' && type === 'success') {
      title = 'Cache cleared';
      body = text;
    } else if (variant === 'knowledge-purge' && type === 'error') {
      title = 'Could not clear cache';
      body = text;
    }
    return { title, body };
  },

  showAdminToast(text, type, autoDismissMs = 0, variant = '', hostId = 'broadcast-message') {
    if (this._adminToastTimer) {
      clearTimeout(this._adminToastTimer);
      this._adminToastTimer = null;
    }
    const el = document.getElementById(hostId);
    if (!el) return;

    el.hidden = false;
    el.className = `broadcast-toast-host is-visible toast-${type}${variant ? ` toast-${variant}` : ''}`;

    const icon = type === 'error'
      ? '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>'
      : '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6 9 17l-5-5"/></svg>';

    const { title, body } = this.resolveAdminToastCopy(text, type, variant);

    el.innerHTML = `
      <div class="broadcast-toast" role="status">
        <div class="broadcast-toast-icon" aria-hidden="true">${icon}</div>
        <div class="broadcast-toast-copy">
          <strong class="broadcast-toast-title">${this.escape(title)}</strong>
          <span class="broadcast-toast-body">${this.escape(body)}</span>
        </div>
        <button type="button" class="broadcast-toast-close" aria-label="Dismiss">&times;</button>
        ${autoDismissMs > 0 ? `<span class="broadcast-toast-progress" style="animation-duration:${autoDismissMs}ms"></span>` : ''}
      </div>`;

    el.querySelector('.broadcast-toast-close')?.addEventListener(
      'click',
      () => this.clearAdminToast(hostId),
      { once: true }
    );

    if (autoDismissMs > 0) {
      this._adminToastTimer = setTimeout(() => this.clearAdminToast(hostId), autoDismissMs);
    }
  },

  initHeaderBroadcastStats() {
    const el = document.getElementById('header-broadcast-stats');
    if (!el) return;

    const render = (listeners, outgoingKbps, bandwidthKnown = true) => {
      const bandwidth = bandwidthKnown
        ? `<span class="header-stat" title="Estimated outbound bandwidth (listeners × encode bitrate)">
            <span class="header-stat-value">${this.escape(this.formatOutgoingBandwidth(outgoingKbps))}</span>
            <span class="header-stat-label">out</span>
          </span>`
        : '';
      el.innerHTML = `
        <span class="header-stat" title="Live listeners across enabled stations">
          <span class="header-stat-value">${listeners}</span>
          <span class="header-stat-label">Listening</span>
        </span>${bandwidth}`;
    };

    const refresh = async () => {
      try {
        const stats = await this.fetchJSON('/api/broadcast/stats');
        render(Number(stats.listeners) || 0, Number(stats.outgoing_kbps) || 0, true);
        return;
      } catch {
        // New endpoint may be missing until backend restart — fall back to /api/stations.
      }
      try {
        const stations = await this.fetchJSON('/api/stations');
        const listeners = stations.reduce((sum, s) => sum + (Number(s.listeners) || 0), 0);
        render(listeners, 0, false);
      } catch {
        el.innerHTML = `
          <span class="header-stat" title="Could not load listener count">
            <span class="header-stat-value">—</span>
            <span class="header-stat-label">Listening</span>
          </span>`;
      }
    };

    refresh();
    setInterval(refresh, 15000);
  },
};

if (document.getElementById('header-broadcast-stats')) {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => RadioApp.initHeaderBroadcastStats());
  } else {
    RadioApp.initHeaderBroadcastStats();
  }
}
