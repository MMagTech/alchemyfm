    const params = new URLSearchParams(location.search);
    let slug = params.get('slug');
    let shellReady = false;

    const ARTIST_BIO_STORAGE_KEY = 'alchemyfm-artist-bio';

    let knowledgeFeature = false;
    let artistBioAdminEnabled = false;
    let artistBioUserVisible = true;
    let lastNowPlaying = null;
    let knowledgeFacts = [];
    let knowledgeFactIndex = 0;
    let knowledgeRotateTimer = null;
    let knowledgeTipTimer = null;
    let knowledgeTrackKey = '';
    let stationDescription = '';
    const KNOWLEDGE_TIP_AUTO_CLOSE_MS = 6000;

    function clearKnowledgeRotation() {
      clearInterval(knowledgeRotateTimer);
      knowledgeRotateTimer = null;
    }

    function clearKnowledgeTipTimer() {
      clearTimeout(knowledgeTipTimer);
      knowledgeTipTimer = null;
    }

    function applyStationDescLine() {
      const descEl = document.getElementById('station-desc');
      if (!descEl) return;
      const text = stationDescription || '';
      descEl.textContent = text;
      descEl.hidden = !text;
    }

    function closeKnowledgeTip(suppressHover = false) {
      const tip = document.getElementById('knowledge-tip');
      if (!tip) return;
      clearKnowledgeTipTimer();
      tip.classList.remove('is-open');
      if (suppressHover) tip.classList.add('suppress-hover');
      tip.querySelector('.knowledge-tip-btn')?.setAttribute('aria-expanded', 'false');
    }

    function openKnowledgeTip() {
      const tip = document.getElementById('knowledge-tip');
      const btn = tip?.querySelector('.knowledge-tip-btn');
      if (!tip) return;
      tip.classList.remove('suppress-hover');
      tip.classList.add('is-open');
      btn?.setAttribute('aria-expanded', 'true');
      clearKnowledgeTipTimer();
      knowledgeTipTimer = setTimeout(() => {
        knowledgeTipTimer = null;
        closeKnowledgeTip(true);
      }, KNOWLEDGE_TIP_AUTO_CLOSE_MS);
    }

    function applyKnowledgeTip() {
      const tip = document.getElementById('knowledge-tip');
      if (!tip) return;
      const textEl = document.getElementById('knowledge-fact-text');
      const linkEl = document.getElementById('knowledge-fact-source');

      if (!knowledgeFeature || !knowledgeFacts.length) {
        tip.hidden = true;
        closeKnowledgeTip();
        return;
      }

      tip.hidden = false;
      const fact = knowledgeFacts[knowledgeFactIndex % knowledgeFacts.length];
      if (textEl) textEl.textContent = fact.text;
      const src = (fact.sources && fact.sources[0]) || null;
      if (linkEl) {
        if (src && src.url) {
          linkEl.href = src.url;
          linkEl.textContent = src.title || 'Source';
          linkEl.hidden = false;
        } else {
          linkEl.hidden = true;
          linkEl.removeAttribute('href');
        }
      }
    }

    function wireKnowledgeTip() {
      const tip = document.getElementById('knowledge-tip');
      if (!tip || tip.dataset.wired === '1') return;
      tip.dataset.wired = '1';
      const btn = tip.querySelector('.knowledge-tip-btn');
      btn?.addEventListener('click', (e) => {
        e.stopPropagation();
        openKnowledgeTip();
      });
      tip.addEventListener('mouseleave', () => {
        tip.classList.remove('suppress-hover');
      });
      document.addEventListener('click', (e) => {
        if (!tip.hidden && !tip.contains(e.target)) {
          closeKnowledgeTip(true);
        }
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeKnowledgeTip(true);
      });
    }

    function updateKnowledge(np, station) {
      stationDescription = station.description || '';
      if (!knowledgeFeature) {
        clearKnowledgeRotation();
        knowledgeFacts = [];
        applyKnowledgeTip();
        applyStationDescLine();
        return;
      }
      const trackKey = RadioApp.trackKey(np);
      const block = np && np.knowledge;
      if (!block || !block.facts || !block.facts.length) {
        clearKnowledgeRotation();
        knowledgeFacts = [];
        knowledgeTrackKey = trackKey || '';
        closeKnowledgeTip();
        applyKnowledgeTip();
        applyStationDescLine();
        return;
      }
      if (trackKey !== knowledgeTrackKey) {
        knowledgeTrackKey = trackKey;
        knowledgeFacts = block.facts;
        knowledgeFactIndex = 0;
        clearKnowledgeRotation();
        closeKnowledgeTip();
        applyKnowledgeTip();
        applyStationDescLine();
        const interval = (block.rotation_interval_sec || 15) * 1000;
        knowledgeRotateTimer = setInterval(() => {
          knowledgeFactIndex = (knowledgeFactIndex + 1) % knowledgeFacts.length;
          applyKnowledgeTip();
        }, interval);
      }
    }

    function browserStreamUrl(station) {
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
    }

    function renderShell(s) {
      RadioApp.updateTabTitle({ stationName: s.name, playing: false });
      document.getElementById('content').innerHTML = `
        <div class="panel station-tune-in">
          <div class="tune-in-top">
            <div class="tune-in-art-wrap">
              <div id="hero-art" class="tune-in-art-mount"></div>
              ${s.knowledge_feature ? `
              <div id="knowledge-tip" class="knowledge-tip" hidden>
                <button type="button" class="knowledge-tip-btn" aria-label="Track trivia" aria-expanded="false" aria-controls="knowledge-popover" title="Did you know?">✦</button>
                <div id="knowledge-popover" class="knowledge-popover" role="tooltip">
                  <p class="knowledge-popover-label">Did you know?</p>
                  <p class="knowledge-popover-text" id="knowledge-fact-text"></p>
                  <a class="knowledge-popover-source" id="knowledge-fact-source" href="#" target="_blank" rel="noopener noreferrer" hidden>Source</a>
                </div>
              </div>` : ''}
            </div>
            <div class="tune-in-meta">
              <div class="tune-in-station-row">
                <h2 id="station-name"></h2>
              </div>
              <div id="now-playing-body" class="tune-in-track"></div>
              <p class="tune-in-desc hint" id="station-desc"></p>
              <div id="operator-station-heart-meta-slot" class="operator-station-heart-meta-slot" hidden></div>
            </div>
            <div class="tune-in-player">
              <div class="tune-in-player-controls">
                <button type="button" class="live-play-btn" id="live-play-btn" aria-label="Play">
                  <span class="live-play-icon" aria-hidden="true"></span>
                </button>
                <div class="live-player-center">
                  <span class="live-status-dot" id="live-status-dot" aria-hidden="true"></span>
                  <span id="live-status-text" class="live-status-text">Ready</span>
                  <span class="live-timer" id="live-timer" aria-label="Time listened">0:00</span>
                </div>
                <div id="operator-station-heart-slot" class="operator-station-heart-slot" hidden></div>
              </div>
              <div class="tune-in-player-divider" aria-hidden="true"></div>
              <div class="live-volume">
                <input type="range" id="live-volume" class="live-volume-slider"
                  min="0" max="1" step="0.05" value="1" aria-label="Volume" tabindex="-1">
              </div>
            </div>
          </div>
          <div id="artist-bio" class="tune-in-artist-bio" hidden>
            <p id="artist-bio-text" class="tune-in-artist-bio-text"></p>
            <div class="tune-in-artist-bio-actions">
              <button type="button" id="artist-bio-expand" class="tune-in-artist-bio-expand"
                hidden>Read More</button>
              <span id="artist-bio-actions-sep" class="tune-in-artist-bio-actions-sep"
                hidden aria-hidden="true">·</span>
              <a id="artist-bio-link" class="tune-in-artist-bio-link" href="#" target="_blank"
                rel="noopener noreferrer" hidden>On Last.fm</a>
            </div>
          </div>
          <div class="tune-in-visualizer-wrap">
            <canvas id="live-visualizer" class="tune-in-visualizer" aria-hidden="true"></canvas>
          </div>
          <div class="tune-in-foot">
            <p class="hint tune-in-foot-links">
              <button type="button" class="copy-link" id="copy-stream-url"
                title="Copy stream URL"></button>
              <span class="tune-in-foot-sep" aria-hidden="true">·</span>
              <button type="button" class="copy-link" id="copy-m3u-url"
                title="Copy playlist URL">listen.m3u</button>
            </p>
            <button type="button" class="viz-fullscreen-btn" id="viz-fullscreen-btn" hidden>
              Fullscreen Visuals
            </button>
          </div>
        </div>
        <div class="panel">
          <h3>Up Next</h3>
          <ul class="track-list" id="up-next-list"></ul>
        </div>
        <div class="panel">
          <h3>Recently Played</h3>
          <ul class="track-list" id="recent-list"></ul>
        </div>`;

      const audio = GlobalLivePlayer.prepareStationAudio(s);
      const listenUrl = browserStreamUrl(s);
      currentStation = s;
      const session = GlobalLivePlayer.readSession();
      const onThisStation = GlobalLivePlayer.isPlayingSlug(s.slug);
      const playingOther = session?.wantLive && session.slug && session.slug !== s.slug;

      if (audio) {
        audio.dataset.pageStreamSrc = listenUrl;

        if (playingOther) {
          RadioApp.bindLivePlayerStationUi(audio, {
            viewingSlug: s.slug,
            pageStationName: s.name,
            pageStation: s,
          });
          void GlobalLivePlayer.resumePlaybackForSession(session);
        } else {
          audio.dataset.streamSrc = listenUrl;
          // Only prime the element src when it isn't already pointed at this
          // stream. Reassigning while already live (src carries a cache-bust
          // query) tears down the active connection and makes iOS open a fresh
          // one while the previous upstream lingers -> duplicate listener.
          if (!streamUrlsMatch(audio.src, listenUrl)) {
            audio.src = listenUrl;
          }
          RadioApp.bindLivePlayerStationUi(audio, {
            viewingSlug: s.slug,
            pageStationName: s.name,
            pageStation: s,
          });
          if (onThisStation && audio.paused) {
            audio.reconnectLiveStream?.();
          }
        }
      }
      const streamUrl = s.stream_url;
      const m3uUrl = new URL(
        `/api/stations/${encodeURIComponent(s.slug)}/listen.m3u`,
        location.origin
      ).href;
      const streamBtn = document.getElementById('copy-stream-url');
      streamBtn.textContent = streamUrl;
      streamBtn.onclick = () => RadioApp.copyText(streamUrl, streamBtn);
      const m3uBtn = document.getElementById('copy-m3u-url');
      m3uBtn.onclick = () => RadioApp.copyText(m3uUrl, m3uBtn);
      document.getElementById('station-name').textContent = s.name;
      stationDescription = s.description || '';
      if (audio) wireFullscreenViz(audio);
      wireKnowledgeTip();
      wireArtistBioActions();
      shellReady = true;
      AdminLibraryControls?.onStationShellReady?.();
    }

    function resetHeroPlaceholder() {
      const el = document.getElementById('hero-art');
      if (!el) return;
      el.dataset.stationSlug = '';
      el.dataset.trackKey = '';
      el.dataset.coverUrl = '';
      el.dataset.imgClass = 'artwork tune-in-art';
      el.dataset.placeholder = '<div class="artwork tune-in-art artwork-placeholder">📻</div>';
      el.innerHTML = el.dataset.placeholder;
    }

    function updateHeroArt(s, { reset = false, forceGlitch = false } = {}) {
      const np = s.now_playing;
      const coverSrc = (np && np.cover_url) || s.artwork_url;
      const el = document.getElementById('hero-art');
      if (!el) return;
      el.dataset.imgClass = 'artwork tune-in-art';
      el.dataset.placeholder = '<div class="artwork tune-in-art artwork-placeholder">📻</div>';
      const stationChanged = Boolean(el.dataset.stationSlug && el.dataset.stationSlug !== s.slug);
      const forceReset = reset || stationChanged;
      if (forceReset) {
        el.dataset.trackKey = '';
        el.dataset.coverUrl = '';
        el.innerHTML = el.dataset.placeholder;
      }
      el.dataset.stationSlug = s.slug;
      if (!coverSrc) {
        el.dataset.trackKey = '';
        el.innerHTML = el.dataset.placeholder;
        return;
      }
      const coverKey = `${s.slug}\0${RadioApp.trackKey(np) || ''}`;
      RadioApp.setCoverImage(el, coverSrc, coverKey, {
        reset: forceReset,
        forceGlitch: forceGlitch || stationChanged || reset,
      });
    }

    function updateNowPlayingView(s, { forceGlitch = false } = {}) {
      updateHeroArt(s, { forceGlitch });
      updateNowPlaying(s.now_playing);
    }

    function readUserArtistBioVisible() {
      try {
        return localStorage.getItem(ARTIST_BIO_STORAGE_KEY) !== 'off';
      } catch (_) {
        return true;
      }
    }

    function writeUserArtistBioVisible(visible) {
      try {
        if (visible) localStorage.removeItem(ARTIST_BIO_STORAGE_KEY);
        else localStorage.setItem(ARTIST_BIO_STORAGE_KEY, 'off');
      } catch (_) {}
    }

    function syncArtistBioToggle() {
      const btn = document.getElementById('artist-bio-toggle');
      if (!btn) return;
      btn.hidden = !artistBioAdminEnabled;
      if (!artistBioAdminEnabled) return;
      btn.setAttribute('aria-pressed', artistBioUserVisible ? 'true' : 'false');
      btn.title = artistBioUserVisible
        ? 'Hide artist biography'
        : 'Show artist biography';
    }

    function wireArtistBioToggle() {
      const btn = document.getElementById('artist-bio-toggle');
      if (!btn || btn.dataset.wired === '1') return;
      btn.dataset.wired = '1';
      btn.addEventListener('click', () => {
        artistBioUserVisible = !artistBioUserVisible;
        writeUserArtistBioVisible(artistBioUserVisible);
        syncArtistBioToggle();
        updateArtistBio(lastNowPlaying);
      });
    }

    function updateNowPlaying(np) {
      lastNowPlaying = np || null;
      const el = document.getElementById('now-playing-body');
      if (!np) {
        el.innerHTML = '<p class="now-playing-artist">Waiting for first track…</p>';
        el.dataset.trackKey = '';
        updateArtistBio(null);
        if (AdminLibraryControls?.syncStationHeartWhenReady) {
          AdminLibraryControls.syncStationHeartWhenReady(null);
        }
        return;
      }
      const trackKey = RadioApp.trackKey(np);
      if (el.dataset.trackKey === trackKey) {
        el.querySelector('.now-playing-title').textContent = np.title;
        el.querySelector('.now-playing-artist').textContent = np.artist;
        updateArtistBio(np);
        if (AdminLibraryControls?.syncStationHeartWhenReady) {
          AdminLibraryControls.syncStationHeartWhenReady(np);
        }
        return;
      }
      el.dataset.trackKey = trackKey;
      el.innerHTML = `
        <p class="now-playing-title track-switch">${RadioApp.escape(np.title)}</p>
        <p class="now-playing-artist">${RadioApp.escape(np.artist)}</p>`;
      requestAnimationFrame(() => {
        el.querySelector('.track-switch')?.classList.remove('track-switch');
      });
      updateArtistBio(np);
      if (AdminLibraryControls?.syncStationHeartWhenReady) {
        AdminLibraryControls.syncStationHeartWhenReady(np);
      }
    }

    function artistBioElements() {
      return {
        wrap: document.getElementById('artist-bio'),
        text: document.getElementById('artist-bio-text'),
        expand: document.getElementById('artist-bio-expand'),
        sep: document.getElementById('artist-bio-actions-sep'),
        link: document.getElementById('artist-bio-link'),
      };
    }

    function bioNeedsExpand(textEl) {
      return textEl.scrollHeight > textEl.clientHeight + 2;
    }

    function syncArtistBioActions(bioUrl) {
      const { wrap, text, expand, sep, link } = artistBioElements();
      if (!wrap || !text) return;

      const expanded = wrap.classList.contains('is-expanded');
      const hasUrl = Boolean(bioUrl);
      const showReadMore = !expanded && bioNeedsExpand(text);

      if (expand) {
        if (expanded) {
          expand.textContent = 'Show Less';
          expand.hidden = false;
        } else {
          expand.textContent = 'Read More';
          expand.hidden = !showReadMore;
        }
      }
      if (link) {
        if (hasUrl) {
          link.href = bioUrl;
          link.hidden = false;
        } else {
          link.hidden = true;
          link.removeAttribute('href');
        }
      }
      if (sep) {
        const showExpandControl = expand && !expand.hidden;
        sep.hidden = !showExpandControl || !hasUrl;
      }
    }

    function resetArtistBioExpanded(wrap) {
      wrap?.classList.remove('is-expanded');
    }

    function scrollStationPageToTopOnMobile() {
      if (!window.matchMedia('(max-width: 640px)').matches) return;
      try {
        window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
      } catch {
        window.scrollTo(0, 0);
      }
    }

    function wireArtistBioActions() {
      const { wrap, expand } = artistBioElements();
      if (!expand || expand.dataset.wired === '1') return;
      expand.dataset.wired = '1';
      expand.addEventListener('click', () => {
        if (!wrap) return;
        const wasExpanded = wrap.classList.contains('is-expanded');
        wrap.classList.toggle('is-expanded');
        if (wasExpanded) {
          scrollStationPageToTopOnMobile();
        }
        const link = document.getElementById('artist-bio-link');
        const bioUrl = (link?.getAttribute('href') || '').trim();
        requestAnimationFrame(() => {
          syncArtistBioActions(bioUrl && bioUrl !== '#' ? bioUrl : '');
        });
      });
    }

    function syncArtistBioVisibility(wrap) {
      if (!wrap) return;
      if (!artistBioUserVisible) wrap.classList.add('is-user-hidden');
      else wrap.classList.remove('is-user-hidden');
    }

    function clearArtistBio(wrap, text, expand, sep, link) {
      wrap.hidden = true;
      wrap.classList.remove('is-user-hidden', 'is-expanded');
      wrap.dataset.trackKey = '';
      text.textContent = '';
      if (expand) {
        expand.hidden = true;
        expand.textContent = 'Read More';
      }
      if (sep) sep.hidden = true;
      if (link) {
        link.hidden = true;
        link.removeAttribute('href');
      }
    }

    function updateArtistBio(np) {
      const { wrap, text, expand, sep, link } = artistBioElements();
      if (!wrap || !text) return;

      if (!artistBioAdminEnabled) {
        clearArtistBio(wrap, text, expand, sep, link);
        return;
      }

      const trackKey = np ? RadioApp.trackKey(np) : '';
      const bio = np?.artist_bio?.trim();
      const bioUrl = np?.artist_bio_url?.trim() || '';

      if (!bio) {
        clearArtistBio(wrap, text, expand, sep, link);
        return;
      }

      const bioChanged =
        wrap.dataset.trackKey !== trackKey ||
        text.textContent !== bio ||
        (link?.getAttribute('href') || '') !== bioUrl;

      if (!bioChanged) {
        syncArtistBioVisibility(wrap);
        return;
      }

      resetArtistBioExpanded(wrap);
      wrap.dataset.trackKey = trackKey;
      text.textContent = bio;
      wrap.hidden = false;
      syncArtistBioVisibility(wrap);
      requestAnimationFrame(() => syncArtistBioActions(bioUrl));
    }

    function updateTrackList(el, tracks, emptyMsg) {
      el.innerHTML = tracks.length
        ? tracks.map(t =>
            `<li><span class="artist">${RadioApp.escape(t.artist)}</span> — ${RadioApp.escape(t.title)}</li>`
          ).join('')
        : `<li class="muted">${emptyMsg}</li>`;
    }

    let pollTimer = null;
    let refreshToken = 0;
    let lastStreamEpoch = null;
    let milkdropMod = null;
    let currentStation = null;

    async function ensureMilkdropMod() {
      if (!milkdropMod) {
        milkdropMod = await RadioApp.loadMilkdropFullscreen();
      }
      return milkdropMod;
    }

    function syncFullscreenVizButton() {
      const btn = document.getElementById('viz-fullscreen-btn');
      if (btn) btn.hidden = !RadioApp.fullscreenVizAvailable();
    }

    function wireFullscreenViz(audio) {
      const btn = document.getElementById('viz-fullscreen-btn');
      if (!btn || btn.dataset.wired === '1') return;
      btn.dataset.wired = '1';
      syncFullscreenVizButton();
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          await GlobalLivePlayer.toggleFullscreenViz();
        } catch (err) {
          alert(err.message || 'Could not open fullscreen visuals');
        } finally {
          btn.disabled = false;
        }
      });
    }

    function schedulePoll(ms) {
      clearInterval(pollTimer);
      pollTimer = setInterval(refresh, ms);
    }

    window.__alchemyStationStop = () => {
      refreshToken += 1;
      clearInterval(pollTimer);
      pollTimer = null;
      shellReady = false;
      currentStation = null;
      lastNowPlaying = null;
      RadioApp.detachStripViz(GlobalLivePlayer.getAudio());
    };

    function streamUrlsMatch(audioSrc, canonical) {
      if (!audioSrc || !canonical) return audioSrc === canonical;
      try {
        const a = new URL(audioSrc, location.origin);
        const b = new URL(canonical, location.origin);
        return a.origin === b.origin && a.pathname === b.pathname;
      } catch {
        return audioSrc === canonical;
      }
    }

    function updateMeta(s) {
      if (!s?.slug || s.slug !== slug) return;

      const stationChanged = Boolean(currentStation?.slug && currentStation.slug !== s.slug);
      if (!shellReady || stationChanged) {
        shellReady = false;
        renderShell(s);
      } else {
        const nameEl = document.getElementById('station-name');
        if (nameEl) nameEl.textContent = s.name;
        stationDescription = s.description || '';
        applyStationDescLine();
      }
      currentStation = s;
      const audio = GlobalLivePlayer.getAudio();
      const listenUrl = browserStreamUrl(s);
      const session = GlobalLivePlayer.readSession();
      const onThisStation = GlobalLivePlayer.isPlayingSlug(s.slug);
      const playingOther = session?.wantLive && session.slug && session.slug !== s.slug;

      if (audio) {
        audio.dataset.pageStreamSrc = listenUrl;
        audio.dataset.pageSlug = s.slug;

        if (onThisStation && !streamUrlsMatch(audio.src, listenUrl)) {
          const wasPlaying = !audio.paused || audio.dataset.wantLive === '1';
          audio.dataset.streamSrc = listenUrl;
          if (wasPlaying) {
            audio.dataset.wantLive = '1';
            audio.reconnectLiveStream?.();
          } else {
            audio.src = listenUrl;
          }
        } else if (!session?.wantLive) {
          audio.dataset.streamSrc = listenUrl;
          if (!streamUrlsMatch(audio.src, listenUrl)) {
            audio.src = listenUrl;
          }
        } else if (audio.error && onThisStation && audio.dataset.wantLive === '1') {
          audio.reconnectLiveStream?.();
        }

        if (onThisStation && s.stream_epoch != null) {
          const epochChanged = lastStreamEpoch != null && s.stream_epoch !== lastStreamEpoch;
          lastStreamEpoch = s.stream_epoch;
          if (epochChanged && audio.dataset.wantLive === '1') {
            audio.forceStreamOffline?.('Off air');
          }
        } else if (!onThisStation) {
          lastStreamEpoch = s.stream_epoch ?? lastStreamEpoch;
        }
      }

      if (audio?.setStreamLive && onThisStation) {
        audio.setStreamLive(Boolean(s.on_air));
      }
      const listening = audio && audio.dataset.wantLive === '1';
      schedulePoll(listening ? 750 : 5000);
      updateNowPlayingView(s, { forceGlitch: playingOther });
      artistBioAdminEnabled = s.artist_bio_enabled !== false;
      syncArtistBioToggle();
      knowledgeFeature = Boolean(s.knowledge_feature);
      updateKnowledge(s.now_playing, s);
      updateTrackList(
        document.getElementById('up-next-list'),
        s.up_next,
        'Queue filling…'
      );
      updateTrackList(
        document.getElementById('recent-list'),
        s.recently_played,
        'Nothing played yet'
      );
      if (audio) {
        GlobalLivePlayer.notifyStationMeta(s);
      }
      if (window.__milkdropActive && milkdropMod) {
        milkdropMod.updateMeta(GlobalLivePlayer.getHeardVizMeta());
      }
    }

    async function refresh() {
      const requestSlug = slug;
      if (!requestSlug) {
        document.getElementById('content').innerHTML =
          '<p class="empty">No station selected. <a href="/">Pick a station</a>.</p>';
        shellReady = false;
        currentStation = null;
        return;
      }
      const token = ++refreshToken;
      try {
        const s = await RadioApp.fetchJSON(
          `/api/stations/${encodeURIComponent(requestSlug)}`
        );
        if (token !== refreshToken || requestSlug !== slug || s.slug !== slug) return;
        updateMeta(s);
      } catch (err) {
        if (token !== refreshToken || requestSlug !== slug) return;
        document.getElementById('content').innerHTML =
          `<p class="empty">${RadioApp.escape(err.message)}</p>`;
        shellReady = false;
        currentStation = null;
      }
    }

    artistBioUserVisible = readUserArtistBioVisible();
    wireArtistBioToggle();
    syncArtistBioToggle();

    window.__alchemyCurrentStation = function() {
      return currentStation;
    };

    window.__alchemyOnStreamSwitchPending = function(station) {
      if (!station || station.slug !== slug) return;
      resetHeroPlaceholder();
      const npEl = document.getElementById('now-playing-body');
      if (npEl) {
        npEl.dataset.trackKey = '';
        npEl.innerHTML = '<p class="now-playing-artist">Connecting…</p>';
      }
      updateArtistBio(null);
    };

    window.__alchemyOnStreamSwitch = function(station) {
      if (!station) return;
      const onViewedStation = station.slug === slug;
      const onPlayingStation = typeof GlobalLivePlayer !== 'undefined'
        && GlobalLivePlayer.activePlayingSlug() === station.slug;
      if (!onViewedStation && !onPlayingStation) return;
      if (onViewedStation) {
        updateHeroArt(station, { reset: true, forceGlitch: true });
      }
      if (onViewedStation) {
        updateNowPlaying(station.now_playing);
      }
      artistBioAdminEnabled = station.artist_bio_enabled !== false;
      syncArtistBioToggle();
      if (knowledgeFeature) {
        updateKnowledge(station.now_playing, station);
      }
    };

    window.__alchemyStationNavigate = function(newSlug) {
      if (newSlug) slug = newSlug;
      refreshToken += 1;
      shellReady = false;
      currentStation = null;
      lastNowPlaying = null;
      clearInterval(pollTimer);
      pollTimer = null;
      lastStreamEpoch = null;
      const content = document.getElementById('content');
      if (content) content.innerHTML = '<p class="empty">Loading…</p>';
      const nav = document.querySelector('header nav');
      if (nav) nav.hidden = false;
      document.body.classList.remove('live-home-page');
      document.body.classList.add('live-station-page');
      GlobalLivePlayer.syncMiniVisibility();
      refresh().catch((err) => {
        if (content) {
          content.innerHTML = `<p class="empty">${RadioApp.escape(err.message)}</p>`;
        }
        shellReady = false;
      });
    };

    window.AlchemyStationPage = {
      start() {
        refresh();
        schedulePoll(5000);
      },
    };