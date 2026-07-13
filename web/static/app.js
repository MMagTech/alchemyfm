const RadioApp = {
  async fetchJSON(url, options = {}) {
    const res = await fetch(url, {
      cache: 'no-store',
      credentials: 'same-origin',
      ...options,
    });
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

  /** Horizontal marquee when label text overflows its container. */
  syncOverflowMarquee(trackEl, options = {}) {
    const innerSelector = options.innerSelector || '.marquee-inner';
    const scrollingClass = options.scrollingClass || 'is-scrolling';
    const inner = trackEl?.querySelector(innerSelector);
    if (!inner) return;

    const clearScroll = () => {
      trackEl.classList.remove(scrollingClass);
      trackEl.style.removeProperty('--scroll-distance');
      trackEl.style.removeProperty('--scroll-duration');
      inner.style.removeProperty('--scroll-distance');
      inner.style.removeProperty('--scroll-duration');
    };

    clearScroll();

    const measure = () => {
      inner.style.display = 'inline-block';
      const cs = getComputedStyle(trackEl);
      const rootSize = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      const padX = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
      const fadePad = (options.fadePadRem ?? 1.1) * rootSize;
      const available = trackEl.clientWidth - padX;
      const overflow = inner.scrollWidth - available + fadePad;
      inner.style.removeProperty('display');
      if (overflow > 4) {
        const distance = `-${overflow}px`;
        const duration = `${Math.max(16, overflow / 11)}s`;
        trackEl.classList.add(scrollingClass);
        // Vars on both nodes: Safari applies keyframe transforms only when the
        // custom property lives on the animated element.
        trackEl.style.setProperty('--scroll-distance', distance);
        trackEl.style.setProperty('--scroll-duration', duration);
        inner.style.setProperty('--scroll-distance', distance);
        inner.style.setProperty('--scroll-duration', duration);
      }
    };

    requestAnimationFrame(() => requestAnimationFrame(measure));
  },

  /** Stable key for now-playing cover updates */
  trackKey(np) {
    if (!np) return '';
    return np.item_id || `${np.artist}\0${np.title}`;
  },

  setCoverImage(container, url, trackKey, options = {}) {
    if (!container) return;
    const forceReset = options.reset === true;
    const prevKey = forceReset ? '' : (container.dataset.trackKey || '');
    const prevUrl = forceReset ? '' : (container.dataset.coverUrl || '');
    const nextUrl = url || '';
    if (!forceReset && trackKey && prevKey === trackKey && prevUrl === nextUrl) return;

    const placeholder = container.dataset.placeholder || '';
    const imgClass = container.dataset.imgClass || '';

    if (!url) {
      container.dataset.trackKey = trackKey || '';
      container.dataset.coverUrl = '';
      container.innerHTML = placeholder;
      return;
    }

    const isSwitch = !!(options.forceGlitch || (
      prevKey &&
      prevKey !== (trackKey || '') &&
      (options.waitForLoad || container.querySelector('.cover-frame, img, .artwork, .station-card-art'))
    ));

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

    if (isSwitch && !options.reset && !options.waitForLoad) {
      const probe = new Image();
      probe.onload = mount;
      probe.onerror = mount;
      probe.src = url;
      return;
    }
    if (options.waitForLoad) {
      const probe = new Image();
      const finish = () => {
        if (options.isStale?.()) return;
        options.onApplied?.();
        mount();
      };
      probe.onload = finish;
      probe.onerror = finish;
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

  wireMediaSessionControls(audio, onPlay, onPause, skip = {}, onStop = null) {
    if (!('mediaSession' in navigator)) return;

    const setHandler = (action, fn) => {
      try {
        navigator.mediaSession.setActionHandler(action, fn);
      } catch {
        /* action unsupported */
      }
    };

    const wireHandlers = () => {
      setHandler('play', () => onPlay?.());
      setHandler('pause', () => onPause?.());
      setHandler('stop', () => (onStop ?? onPause)?.());
      if (skip.onNext || skip.onPrevious) {
        setHandler('nexttrack', skip.onNext ? () => skip.onNext() : null);
        setHandler('previoustrack', skip.onPrevious ? () => skip.onPrevious() : null);
      } else {
        setHandler('previoustrack', null);
        setHandler('nexttrack', null);
      }
      // Registering seekforward/seekbackward at all is what makes iOS/CarPlay
      // show the +/-10s scrub buttons instead of prev/next skip icons for this
      // live stream — leave them unset so only nexttrack/previoustrack exist
      // and CarPlay/steering-wheel controls render as permanent skip icons.
      setHandler('seekforward', null);
      setHandler('seekbackward', null);
      setHandler('seekto', null);
    };

    wireHandlers();

    // iOS may ignore handlers registered before playback starts.
    if (audio && (skip.onNext || skip.onPrevious)) {
      audio.addEventListener('playing', wireHandlers);
    }
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

  applySavedLiveVolume(audio) {
    if (!audio) return;
    const saved = localStorage.getItem('radio-volume');
    if (saved == null) return;
    const v = parseFloat(saved);
    if (audio._liveEngine) {
      audio._liveEngine.setVolume(v);
    } else {
      audio.volume = v;
    }
    document.querySelectorAll('#live-volume, #live-mini-volume').forEach((el) => {
      if (el) el.value = saved;
    });
  },

  wireLiveVolumeControl(input, audio, options = {}) {
    if (!input || !audio) return;
    const saved = localStorage.getItem('radio-volume');
    if (saved != null) input.value = saved;

    const onInput = () => {
      const v = input.value;
      audio._liveEngine?.setVolume(v);
      localStorage.setItem('radio-volume', v);
      document.querySelectorAll('#live-volume, #live-mini-volume').forEach((el) => {
        if (el && el !== input) el.value = v;
      });
    };

    const listenerOpts = options.signal ? { signal: options.signal } : undefined;
    input.addEventListener('input', onInput, listenerOpts);
  },

  /** Core stream engine — once per audio element. */
  initLivePlayerEngine(audio, options = {}) {
    if (!audio) return null;
    if (audio._liveEngine) {
      if (options.onSessionChange) {
        audio._liveSessionNotify = options.onSessionChange;
      }
      return audio._liveEngine;
    }

    audio.classList.add('live-audio-hidden');

    let tick = null;
    let reconnectTimer = null;
    let reconnectAttempt = 0;
    let streamLive = false;
    let stallTimer = null;
    let connectInFlight = false;
    let lastProgressAt = 0;
    let listenElapsedMs = 0;
    let listenStartedAt = null;
    let resumeDebounceTimer = null;
    let userPaused = false;
    audio._liveSessionNotify = options.onSessionChange;

    const listenSeconds = () => {
      let ms = listenElapsedMs;
      if (listenStartedAt !== null) {
        ms += Date.now() - listenStartedAt;
      }
      return Math.max(0, Math.floor(ms / 1000));
    };

    const resetListenTimer = () => {
      listenElapsedMs = 0;
      listenStartedAt = null;
      if (audio._liveUi?.timer) {
        audio._liveUi.timer.textContent = '0:00';
      }
    };

    const startListenTimer = () => {
      if (listenStartedAt === null) {
        listenStartedAt = Date.now();
      }
    };

    const stopListenTimer = () => {
      if (listenStartedAt !== null) {
        listenElapsedMs += Date.now() - listenStartedAt;
        listenStartedAt = null;
      }
    };

    const notifySession = (meta = {}) => {
      audio._liveSessionNotify?.(meta);
    };

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

    const setVolume = (value) => {
      const v = parseFloat(value);
      const graph = audio._liveAudioGraph;
      if (graph?.gain) {
        graph.gain.gain.value = v;
      } else {
        audio.volume = v;
      }
    };

    const primeAudioGraph = () => {
      const graph = audio._liveAudioGraph;
      graph?.ensureGraph?.();
      if (graph?.audioContext?.state === 'suspended') {
        graph.audioContext.resume();
      }
    };

    const setWantLive = (want) => {
      audio.dataset.wantLive = want ? '1' : '0';
      if (!want) {
        clearTimeout(reconnectTimer);
        reconnectAttempt = 0;
        clearStallWatch();
        notifySession({ wantLive: false });
      } else {
        notifySession({ wantLive: true });
      }
    };

    const streamUrlsMatch = (audioSrc, canonical) => {
      if (!audioSrc || !canonical) return audioSrc === canonical;
      try {
        const a = new URL(audioSrc, location.origin);
        const b = new URL(canonical, location.origin);
        return a.origin === b.origin && a.pathname === b.pathname;
      } catch {
        return audioSrc === canonical;
      }
    };

    const connectStream = (bustCache = false, { skipReset = false } = {}) => {
      if (connectInFlight) return Promise.resolve();
      const base = baseStreamUrl();
      if (!base) return Promise.reject(new Error('No stream URL'));
      audio.dataset.streamSrc = base;
      if (this.useStripWebAudio()) {
        primeAudioGraph();
      }
      const mobileHandoff = this.isMobileStation();
      const useCacheBust = bustCache && !mobileHandoff;
      let src = base;
      if (useCacheBust) {
        const url = new URL(base, location.origin);
        url.searchParams.set('t', String(Date.now()));
        src = url.href;
      }
      const prevSrc = audio.currentSrc || audio.src || '';
      const sameStream = Boolean(prevSrc && streamUrlsMatch(prevSrc, src));

      if (audio.dataset.wantLive === '1' && !audio.paused && sameStream && !useCacheBust) {
        return Promise.resolve();
      }

      connectInFlight = true;
      userPaused = false;
      audio._liveUi?.setConnectingUi?.(bustCache ? 'Reconnecting…' : 'Connecting…');

      // A lock-screen/CarPlay nexttrack press only grants iOS a brief autoplay
      // window. Tearing the element down first (pause/removeAttribute/load)
      // asks iOS to revive a freshly-emptied element from scratch; assigning
      // .src directly already resets the media element per spec, so the hard
      // reset is skipped for that path and left in place everywhere else
      // (foreground switches, error-recovery reconnects) where it's unlikely
      // to be gating anything and a clean re-init is the safer default.
      if (prevSrc && (!sameStream || bustCache) && !skipReset) {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
      }

      if (!prevSrc || !sameStream) {
        resetListenTimer();
      }
      audio.src = src;
      this.configurePlaybackSession();
      const playStartedAt = Date.now();
      return audio.play().then(
        () => {
          if (skipReset && typeof AlchemyDiag !== 'undefined') {
            AlchemyDiag.log('play-resolved', { src, msSincePlayCall: Date.now() - playStartedAt });
          }
        },
        (err) => {
          if (skipReset && typeof AlchemyDiag !== 'undefined') {
            AlchemyDiag.log('play-rejected', {
              src,
              errorName: err?.name,
              errorMessage: String(err?.message || err),
              msSincePlayCall: Date.now() - playStartedAt,
            });
          }
          throw err;
        },
      ).finally(() => {
        connectInFlight = false;
      });
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

    const pauseAndFlushBuffer = () => {
      const base = baseStreamUrl();
      if (base) audio.dataset.streamSrc = base;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      clearInterval(tick);
      resetListenTimer();
      audio._liveUi?.setIdleUi?.('Ready');
    };

    const markStreamOffline = (message = 'Off air') => {
      if (audio.dataset.wantLive !== '1') return;
      streamLive = false;
      clearTimeout(reconnectTimer);
      clearStallWatch();
      pauseAndFlushBuffer();
      audio._liveUi?.setIdleUi?.(message);
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

    const updateTimer = () => {
      if (!audio.paused && audio._liveUi?.timer) {
        audio._liveUi.timer.textContent = this.formatListenTime(listenSeconds());
      }
    };

    const togglePlay = () => {
      if (audio.paused) {
        setWantLive(true);
        reconnectAttempt = 0;
        clearTimeout(reconnectTimer);
        primeAudioGraph();
        connectStream(true).catch(() => scheduleReconnect());
      } else {
        setWantLive(false);
        audio.pause();
        audio._liveUi?.setIdleUi?.('Paused');
      }
    };

    const softPause = () => {
      if (audio.paused) return;
      userPaused = true;
      audio.pause();
      audio._liveUi?.setPlayingUi?.(false);
      audio._liveUi?.setStatusText?.('Paused');
    };

    const hardStop = () => {
      setWantLive(false);
      audio.pause();
      audio._liveUi?.setIdleUi?.('Paused');
    };

    const resumeIfWanted = () => {
      if (audio.dataset.wantLive !== '1' || !audio.paused || connectInFlight) return;
      reconnectAttempt = 0;
      clearTimeout(reconnectTimer);
      primeAudioGraph();
      connectStream(true).catch(() => scheduleReconnect());
    };

    const scheduleResumeIfWanted = () => {
      clearTimeout(resumeDebounceTimer);
      resumeDebounceTimer = setTimeout(() => resumeIfWanted(), 400);
    };

    const skipOpts = this.isMobileStation()
      ? {
          onNext: () => { void GlobalLivePlayer?.switchToAdjacentStation?.(1); },
          onPrevious: () => { void GlobalLivePlayer?.switchToAdjacentStation?.(-1); },
        }
      : {};

    this.wireMediaSessionControls(
      audio,
      () => resumeIfWanted(),
      () => softPause(),
      skipOpts,
      () => hardStop(),
    );

    if (!audio._liveInterruptionHooks) {
      audio._liveInterruptionHooks = true;
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') scheduleResumeIfWanted();
      });
      window.addEventListener('pageshow', () => scheduleResumeIfWanted());
      window.addEventListener('focus', () => scheduleResumeIfWanted());
    }

    audio.addEventListener('play', () => {
      setWantLive(true);
      startListenTimer();
      clearInterval(tick);
      tick = setInterval(updateTimer, 1000);
      updateTimer();
    });

    audio.addEventListener('pause', () => {
      stopListenTimer();
      clearInterval(tick);
      clearStallWatch();
      if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = 'paused';
      }
      if (audio.dataset.wantLive !== '1') {
        audio._liveUi?.setPlayingUi?.(false);
        audio._liveUi?.setStatusText?.('Paused');
      } else if (!userPaused) {
        // Unexpected pause while still wanted live — e.g. iOS suspending the
        // audio session (route change, Siri, phone call) or a network drop.
        // Resume from this event directly rather than waiting on the next
        // visibilitychange/focus: iOS throttles setTimeout/setInterval hard
        // once the page stops actively playing audio, so the stall-watch and
        // reconnect timers below can silently stop firing in the background.
        scheduleResumeIfWanted();
      }
    });

    audio.addEventListener('ended', () => {
      // A same-origin chunked stream can also end "cleanly" (no error event)
      // when the underlying connection is dropped, e.g. a WiFi/cellular
      // handoff on mobile. Treat it the same as a stall so we reconnect.
      if (audio.dataset.wantLive === '1') {
        markStreamOffline('Stream interrupted');
      }
    });

    audio.addEventListener('waiting', () => {
      audio._liveUi?.setConnectingUi?.('Buffering…');
      armStallWatch();
    });

    audio.addEventListener('playing', () => {
      reconnectAttempt = 0;
      clearTimeout(reconnectTimer);
      lastProgressAt = Date.now();
      this.applySavedLiveVolume(audio);
      audio._liveUi?.setPlayingUi?.(true);
      armStallWatch();
      this.configurePlaybackSession();
      if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = 'playing';
      }
      notifySession({ wantLive: true });
    });

    audio.addEventListener('stalled', () => {
      audio._liveUi?.setConnectingUi?.('Buffering…');
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
      if (typeof AlchemyDiag !== 'undefined') {
        AlchemyDiag.log('audio-error-event', {
          errorCode: audio.error ? audio.error.code : null,
          src: audio.currentSrc || audio.src || '',
        });
      }
      clearInterval(tick);
      clearStallWatch();
      stopListenTimer();
      resetListenTimer();
      const base = baseStreamUrl();
      if (base) audio.dataset.streamSrc = base;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      audio._liveUi?.setIdleUi?.('Stream error');
      scheduleReconnect();
    });

    const savedVol = localStorage.getItem('radio-volume');
    if (savedVol != null) {
      setVolume(savedVol);
    }

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
        const playing = audio._liveUi?.playBtn?.classList.contains('is-playing') || !audio.paused;
        if (playing && wasLive) {
          markStreamOffline('Off air');
        } else if (!connectInFlight) {
          audio._liveUi?.setStatusText?.('Off air');
          audio._liveUi?.setLiveDot?.(false);
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

    audio._liveEngine = {
      togglePlay,
      setWantLive,
      setVolume,
      connectStream,
      getListenSeconds: listenSeconds,
      resetListenTimer,
    };

    return audio._liveEngine;
  },

  /** Detach strip visualizer surface (canvas is recreated per station shell). */
  detachStripViz(audio) {
    if (!audio) return;
    audio._stripViz?.destroy?.();
    audio._stripViz = null;
  },

  /** Station tune-in controls — re-bind when shell re-renders. */
  bindLivePlayerStationUi(audio, options = {}) {
    if (!audio) return;

    const viewingSlug = options.viewingSlug || '';
    const pageStationName = options.pageStationName || '';
    const pageStation = options.pageStation || null;

    const btn = document.getElementById('live-play-btn');
    const timer = document.getElementById('live-timer');
    const status = document.getElementById('live-status-text');
    const dot = document.getElementById('live-status-dot');
    const vol = document.getElementById('live-volume');
    const stripCanvas = document.getElementById('live-visualizer');
    if (!btn || !timer || !status || !vol) return;

    if (audio._liveUiAbort) {
      audio._liveUiAbort.abort();
    }
    audio._liveUiAbort = new AbortController();
    const { signal } = audio._liveUiAbort;

    const useWebAudio = this.useStripWebAudio();
    if (!audio._liveAudioGraph && useWebAudio && typeof LiveAudioGraph !== 'undefined') {
      audio._liveAudioGraph = LiveAudioGraph.attach(audio);
    }
    this.detachStripViz(audio);
    if (stripCanvas && audio._liveAudioGraph && useWebAudio &&
        typeof StripVisualizer !== 'undefined') {
      audio._stripViz = StripVisualizer.create(audio._liveAudioGraph, stripCanvas);
    }
    const viz = audio._stripViz;
    const vizWrap = document.querySelector('.tune-in-visualizer-wrap');

    const setPlayButtonPlaying = (playing) => {
      btn.classList.toggle('is-playing', playing);
      btn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    };

    const setLiveStatus = (live) => {
      status.classList.toggle('is-live', live);
      if (dot) dot.classList.toggle('is-live', live);
    };

    const syncVizFromAudio = () => {
      if (!viz || !useWebAudio) return;
      const heardLive = audio.dataset.wantLive === '1' && !audio.paused;
      if (heardLive) viz.start();
      else viz.stop();

      if (vizWrap && typeof GlobalLivePlayer !== 'undefined') {
        const pres = GlobalLivePlayer.getPlaybackPresentation(viewingSlug);
        vizWrap.classList.toggle(
          'is-heard-other',
          pres.browsingOther && pres.heardLive
        );
      }
    };

    const setIdleUi = (message = 'Ready') => {
      setPlayButtonPlaying(false);
      status.textContent = message;
      setLiveStatus(false);
    };

    const setConnectingUi = (message = 'Connecting…') => {
      setPlayButtonPlaying(false);
      status.textContent = message;
      setLiveStatus(false);
    };

    const setPlayingUi = (playing) => {
      setPlayButtonPlaying(playing);
      if (playing) {
        status.textContent = 'Live';
        setLiveStatus(true);
      } else {
        setLiveStatus(false);
      }
    };

    const syncStationUi = () => {
      const session = typeof GlobalLivePlayer !== 'undefined'
        ? GlobalLivePlayer.readSession()
        : null;
      const pres = typeof GlobalLivePlayer !== 'undefined'
        ? GlobalLivePlayer.getPlaybackPresentation(viewingSlug)
        : null;
      const playingSlug = pres?.heardSlug ||
        (typeof GlobalLivePlayer !== 'undefined'
          ? GlobalLivePlayer.activePlayingSlug()
          : (session?.slug || ''));
      const onThisStation = Boolean(
        viewingSlug &&
        audio.dataset.wantLive === '1' &&
        playingSlug === viewingSlug
      );
      const browsingOther = pres?.browsingOther ||
        (typeof GlobalLivePlayer !== 'undefined'
          && GlobalLivePlayer.isBrowsingOtherStation(viewingSlug));

      if (onThisStation && !audio.paused) {
        setPlayingUi(true);
        timer.textContent = this.formatListenTime(
          audio._liveEngine?.getListenSeconds?.() ?? 0
        );
      } else if (onThisStation && audio.dataset.wantLive === '1' && audio.paused) {
        setConnectingUi('Connecting…');
      } else if (browsingOther) {
        const name = pres?.heardStationName ||
          (session?.slug === playingSlug && session?.stationName) ||
          'another station';
        setIdleUi(`Playing ${name}`);
        btn.setAttribute('aria-label', pageStationName
          ? `Listen to ${pageStationName}`
          : 'Play');
        if (pres?.heardLive) {
          timer.textContent = this.formatListenTime(
            audio._liveEngine?.getListenSeconds?.() ?? 0
          );
        }
      } else {
        setIdleUi(audio.dataset.wantLive === '1' && audio.paused ? 'Paused' : 'Ready');
      }
      syncVizFromAudio();
    };

    audio._liveUi = {
      playBtn: btn,
      timer,
      setIdleUi,
      setConnectingUi,
      setPlayingUi,
      setStatusText: (text) => { status.textContent = text; },
      setLiveDot: (on) => {
        status.classList.toggle('is-live', on);
        if (dot) dot.classList.toggle('is-live', on);
      },
      syncStationUi,
      syncVizFromAudio,
    };

    const engine = audio._liveEngine;
    if (!engine) return;

    const savedVol = localStorage.getItem('radio-volume');
    if (savedVol != null) {
      vol.value = savedVol;
    }
    engine.setVolume(vol.value);

    btn.addEventListener('click', async () => {
      const session = typeof GlobalLivePlayer !== 'undefined'
        ? GlobalLivePlayer.readSession()
        : null;
      const playingSlug = typeof GlobalLivePlayer !== 'undefined'
        ? GlobalLivePlayer.activePlayingSlug()
        : (session?.slug || '');
      const onThis = Boolean(viewingSlug && playingSlug === viewingSlug);
      const browsingOther = typeof GlobalLivePlayer !== 'undefined'
        && GlobalLivePlayer.isBrowsingOtherStation(viewingSlug);

      if (pageStation && typeof GlobalLivePlayer !== 'undefined' && browsingOther) {
        const liveStation = typeof window.__alchemyCurrentStation === 'function'
          ? window.__alchemyCurrentStation()
          : pageStation;
        await GlobalLivePlayer.switchToStation(liveStation || pageStation);
        syncStationUi();
        return;
      }

      if (pageStation && typeof GlobalLivePlayer !== 'undefined' && !onThis &&
          audio.paused && session?.wantLive) {
        const liveStation = typeof window.__alchemyCurrentStation === 'function'
          ? window.__alchemyCurrentStation()
          : pageStation;
        await GlobalLivePlayer.switchToStation(liveStation || pageStation);
        syncStationUi();
        return;
      }

      engine.togglePlay();
      syncStationUi();
    }, { signal });

    this.wireLiveVolumeControl(vol, audio, { signal });

    ['play', 'pause', 'playing'].forEach((ev) => {
      audio.addEventListener(ev, syncStationUi, { signal });
    });

    syncStationUi();
  },

  _milkdropMod: null,

  async loadMilkdropFullscreen() {
    if (!this._milkdropMod) {
      this._milkdropMod = await import('/static/butterchurn-fullscreen.js?v=13');
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

  /**
   * In-browser playback URL. Always the same-origin /listen proxy so Safari's
   * bytes=0-1 probe is answered locally (no extra Icecast listener). External
   * players and "Copy stream link" still use station.stream_url (direct mount).
   */
  browserStreamUrl(station) {
    const slug = typeof station === 'string' ? station : station.slug;
    return new URL(
      `/api/stations/${encodeURIComponent(slug)}/listen`,
      location.origin
    ).href;
  },

  isMobileStation() {
    if (/iPhone|iPad|iPod/i.test(navigator.userAgent)) return true;
    return window.matchMedia('(max-width: 640px)').matches;
  },

  /** Desktop strip visualizer needs Web Audio; mobile/iOS uses direct audio element output (CarPlay-safe). */
  useStripWebAudio() {
    return !this.isMobileStation();
  },

  configurePlaybackSession() {
    try {
      if (navigator.audioSession) {
        navigator.audioSession.type = 'playback';
      }
    } catch (_) {}
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
