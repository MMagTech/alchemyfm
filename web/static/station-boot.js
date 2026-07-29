    const params = new URLSearchParams(location.search);
    let slug = params.get('slug');
    let shellReady = false;

    const ARTIST_BIO_STORAGE_KEY = 'alchemyfm-artist-bio';

    let knowledgeFeature = false;
    let artistBioAdminEnabled = false;
    let artistBioUserVisible = true;
    let lastNowPlaying = null;
    let knowledgeFacts = [];
    let knowledgeFactsSig = '';
    let knowledgeFactIndex = 0;
    let knowledgeRotateTimer = null;
    let knowledgeTipTimer = null;
    let knowledgeTrackKey = '';
    let stationDescription = '';
    const KNOWLEDGE_TIP_AUTO_CLOSE_MS = 6000;
    const reduceMotion = window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

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

    function paintKnowledgeFact() {
      const textEl = document.getElementById('knowledge-fact-text');
      const linkEl = document.getElementById('knowledge-fact-source');
      const dotsEl = document.getElementById('knowledge-dots');
      const fact = knowledgeFacts[knowledgeFactIndex % knowledgeFacts.length];
      if (!fact) return;
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
      if (dotsEl) {
        [...dotsEl.children].forEach((dot, i) => {
          dot.classList.toggle('is-current', i === knowledgeFactIndex);
          dot.setAttribute('aria-current', i === knowledgeFactIndex ? 'true' : 'false');
        });
      }
    }

    function rebuildKnowledgeDots() {
      const dotsEl = document.getElementById('knowledge-dots');
      const nav = document.getElementById('knowledge-popover-nav');
      if (!dotsEl || !nav) return;
      nav.hidden = knowledgeFacts.length < 2;
      const frag = document.createDocumentFragment();
      knowledgeFacts.forEach((_, i) => {
        const dot = document.createElement('button');
        dot.type = 'button';
        dot.className = 'knowledge-dot';
        dot.setAttribute('aria-label', `Fact ${i + 1} of ${knowledgeFacts.length}`);
        dot.addEventListener('click', (e) => {
          e.stopPropagation();
          goToKnowledgeFact(i, true);
        });
        frag.appendChild(dot);
      });
      dotsEl.replaceChildren(frag);
    }

    /** Slide to fact n; manual moves restart the auto-rotation clock. */
    function goToKnowledgeFact(n, manual = false) {
      if (!knowledgeFacts.length) return;
      const from = knowledgeFactIndex;
      const next = ((n % knowledgeFacts.length) + knowledgeFacts.length) % knowledgeFacts.length;
      if (next === from && manual) return;
      const dir = (next > from || (from === knowledgeFacts.length - 1 && next === 0)) ? 1 : -1;
      knowledgeFactIndex = next;
      const body = document.getElementById('knowledge-popover-body');
      if (reduceMotion || !body) {
        paintKnowledgeFact();
      } else {
        body.style.opacity = '0';
        body.style.transform = `translateX(${dir * 10}px)`;
        setTimeout(() => {
          paintKnowledgeFact();
          body.style.transform = `translateX(${-dir * 10}px)`;
          requestAnimationFrame(() => {
            body.style.opacity = '1';
            body.style.transform = 'translateX(0)';
          });
        }, 160);
      }
      if (manual) restartKnowledgeRotation();
    }

    let knowledgeRotateMs = 15000;

    function restartKnowledgeRotation() {
      clearKnowledgeRotation();
      if (knowledgeFacts.length < 2) return;
      knowledgeRotateTimer = setInterval(() => {
        goToKnowledgeFact(knowledgeFactIndex + 1, false);
      }, knowledgeRotateMs);
    }

    function applyKnowledgeTip() {
      const tip = document.getElementById('knowledge-tip');
      if (!tip) return;

      if (!knowledgeFeature || !knowledgeFacts.length) {
        tip.hidden = true;
        closeKnowledgeTip();
        return;
      }

      tip.hidden = false;
      rebuildKnowledgeDots();
      paintKnowledgeFact();
    }

    /** Spring-pop the ✦ in when facts land after the track already rendered. */
    function popKnowledgeStar() {
      const btn = document.querySelector('#knowledge-tip .knowledge-tip-btn');
      if (!btn || reduceMotion) return;
      btn.classList.remove('is-arriving');
      void btn.offsetWidth;
      btn.classList.add('is-arriving');
      btn.addEventListener(
        'animationend',
        () => btn.classList.remove('is-arriving'),
        { once: true }
      );
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
      document.getElementById('knowledge-prev')?.addEventListener('click', (e) => {
        e.stopPropagation();
        goToKnowledgeFact(knowledgeFactIndex - 1, true);
      });
      document.getElementById('knowledge-next')?.addEventListener('click', (e) => {
        e.stopPropagation();
        goToKnowledgeFact(knowledgeFactIndex + 1, true);
      });
      const pop = document.getElementById('knowledge-popover');
      if (pop) {
        let touchX = null;
        pop.addEventListener('touchstart', (e) => {
          touchX = e.touches[0]?.clientX ?? null;
        }, { passive: true });
        pop.addEventListener('touchend', (e) => {
          if (touchX == null) return;
          const dx = (e.changedTouches[0]?.clientX ?? touchX) - touchX;
          touchX = null;
          if (Math.abs(dx) < 30) return;
          goToKnowledgeFact(knowledgeFactIndex + (dx < 0 ? 1 : -1), true);
        }, { passive: true });
      }
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
        knowledgeFactsSig = '';
        applyKnowledgeTip();
        applyStationDescLine();
        return;
      }
      const trackKey = RadioApp.trackKey(np);
      const block = np && np.knowledge;
      if (!block || !block.facts || !block.facts.length) {
        clearKnowledgeRotation();
        knowledgeFacts = [];
        knowledgeFactsSig = '';
        knowledgeTrackKey = trackKey || '';
        closeKnowledgeTip();
        applyKnowledgeTip();
        applyStationDescLine();
        return;
      }
      const factsSig = block.facts.map((f) => f.text).join('\0');
      const trackChanged = trackKey !== knowledgeTrackKey;
      if (!trackChanged && factsSig === knowledgeFactsSig) return;
      // Facts landing after the track already rendered bare — the star pops in.
      const lateArrival = !trackChanged && !knowledgeFacts.length;
      knowledgeTrackKey = trackKey;
      knowledgeFacts = block.facts;
      knowledgeFactsSig = factsSig;
      knowledgeFactIndex = 0;
      closeKnowledgeTip();
      applyKnowledgeTip();
      applyStationDescLine();
      knowledgeRotateMs = (block.rotation_interval_sec || 15) * 1000;
      restartKnowledgeRotation();
      if (lateArrival) popKnowledgeStar();
    }

    function browserStreamUrl(station) {
      return RadioApp.browserStreamUrl(station);
    }

    function renderShell(s) {
      RadioApp.updateTabTitle({ stationName: s.name, playing: false });
      document.getElementById('content').innerHTML = `
        <aside class="shelf-rail station-rail" aria-labelledby="shelf-rail-label">
          <p class="shelf-rail-label" id="shelf-rail-label">Stations<span class="shelf-rail-count" id="shelf-rail-count"></span></p>
          <ul class="shelf-rail-list" id="shelf-rail-list"></ul>
        </aside>
        <div class="shelf-main station-main">
        <div class="panel station-tune-in">
          <h2 class="station-heading" id="station-name"></h2>
          <div class="tune-in-top">
            <div class="tune-in-art-wrap">
              <div id="hero-art" class="tune-in-art-mount"></div>
              ${s.knowledge_feature ? `
              <div id="knowledge-tip" class="knowledge-tip" hidden>
                <button type="button" class="knowledge-tip-btn" aria-label="Track trivia" aria-expanded="false" aria-controls="knowledge-popover" title="Did you know?">✦</button>
                <div id="knowledge-popover" class="knowledge-popover" role="tooltip">
                  <p class="knowledge-popover-label">Did you know?</p>
                  <div class="knowledge-popover-body" id="knowledge-popover-body">
                    <p class="knowledge-popover-text" id="knowledge-fact-text"></p>
                    <a class="knowledge-popover-source" id="knowledge-fact-source" href="#" target="_blank" rel="noopener noreferrer" hidden>Source</a>
                  </div>
                  <div class="knowledge-popover-nav" id="knowledge-popover-nav" hidden>
                    <button type="button" class="knowledge-arrow" id="knowledge-prev" aria-label="Previous fact">&#8249;</button>
                    <div class="knowledge-dots" id="knowledge-dots"></div>
                    <button type="button" class="knowledge-arrow" id="knowledge-next" aria-label="Next fact">&#8250;</button>
                  </div>
                </div>
              </div>` : ''}
            </div>
            <div class="tune-in-meta">
              <div id="now-playing-body" class="tune-in-track"></div>
              <p class="tune-in-desc hint" id="station-desc"></p>
              <div class="tune-in-metarow">
                <span class="tune-in-listeners" id="station-listeners" hidden></span>
                <span class="tune-in-status">
                  <span class="live-status-dot" id="live-status-dot" aria-hidden="true"></span>
                  <span id="live-status-text" class="live-status-text">Ready</span>
                </span>
                <span class="live-timer" id="live-timer" aria-label="Time listened">0:00</span>
                <div id="operator-station-heart-meta-slot" class="operator-station-heart-meta-slot" hidden></div>
              </div>
            </div>
            <div class="tune-in-play-col">
              <button type="button" class="live-play-btn" id="live-play-btn" aria-label="Play">
                <span class="live-play-icon" aria-hidden="true"></span>
              </button>
              <div class="live-volume">
                <input type="range" id="live-volume" class="live-volume-slider"
                  min="0" max="1" step="0.05" value="1" aria-label="Volume" tabindex="-1">
              </div>
              <div id="operator-station-heart-slot" class="operator-station-heart-slot" hidden></div>
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
            <span class="tune-in-foot-right">
              <span class="format-badge" id="stream-format-badge" hidden></span>
              <button type="button" class="viz-fullscreen-btn" id="viz-fullscreen-btn" hidden>
                Fullscreen Visuals
              </button>
            </span>
          </div>
        </div>
        <div class="station-panels">
          <div class="panel">
            <h3>Up Next</h3>
            <ul class="track-list track-rows" id="up-next-list"></ul>
          </div>
          <div class="panel">
            <h3>Recently Played</h3>
            <ul class="track-list track-rows" id="recent-list"></ul>
          </div>
        </div>
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
          // Never assign audio.src while idle — iOS opens a stream connection on
          // src alone; connectStream/reconnectLiveStream owns the live connect.
          RadioApp.bindLivePlayerStationUi(audio, {
            viewingSlug: s.slug,
            pageStationName: s.name,
            pageStation: s,
          });
          if (onThisStation && audio.dataset.wantLive === '1' && audio.paused) {
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
      startRail();
      void loadFormatBadge();
      shellReady = true;
      AdminLibraryControls?.onStationShellReady?.();
    }

    /* ---------- Station rail (desktop sidebar) ---------- */

    let railTimer = null;
    const RAIL_POLL_MS = 30000;

    function syncRailViewing() {
      document.querySelectorAll('.shelf-rail-item').forEach((li) => {
        li.classList.toggle('is-viewing', li.dataset.slug === slug);
      });
    }

    async function refreshRail() {
      if (typeof AlchemyHome === 'undefined') return;
      try {
        const stations = await RadioApp.fetchJSON('/api/stations');
        if (!document.getElementById('shelf-rail-list')) return;
        AlchemyHome.writeStationsCache(stations);
        AlchemyHome.renderRail(stations);
        syncRailViewing();
      } catch {
        /* rail is decorative — the cached render (if any) stands */
      }
    }

    function startRail() {
      stopRail();
      if (typeof AlchemyHome === 'undefined') return;
      // renderRail keys off AlchemyHome._railKey; a fresh empty list needs a
      // fresh key or the rebuild is skipped and the rail stays empty.
      AlchemyHome._railKey = '';
      const cached = AlchemyHome.readStationsCache();
      if (cached?.length) {
        AlchemyHome.renderRail(cached);
        syncRailViewing();
      }
      void refreshRail();
      railTimer = setInterval(() => void refreshRail(), RAIL_POLL_MS);
    }

    function stopRail() {
      clearInterval(railTimer);
      railTimer = null;
    }

    /* ---------- Stream format badge (from /api/health) ---------- */

    let healthPromise = null;

    async function loadFormatBadge() {
      const badge = document.getElementById('stream-format-badge');
      if (!badge) return;
      try {
        healthPromise = healthPromise || RadioApp.fetchJSON('/api/health');
        const health = await healthPromise;
        const fmt = (health.encode_format || '').toUpperCase();
        if (!fmt) return;
        const el = document.getElementById('stream-format-badge');
        if (!el) return;
        el.textContent = health.bitrate ? `${fmt} ${health.bitrate}` : fmt;
        el.hidden = false;
      } catch {
        healthPromise = null;
      }
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

    /** played_at is naive UTC from the backend — pin it before parsing. */
    function playedAgoText(playedAt) {
      if (!playedAt) return '';
      const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(playedAt) ? playedAt : `${playedAt}Z`;
      const then = Date.parse(iso);
      if (!Number.isFinite(then)) return '';
      const mins = Math.floor((Date.now() - then) / 60000);
      if (mins < 1) return 'just now';
      if (mins < 60) return `${mins} min ago`;
      const hrs = Math.floor(mins / 60);
      return hrs === 1 ? '1 hr ago' : `${hrs} hr ago`;
    }

    function trackRowHtml(t, { withAgo }) {
      const cover = t.cover_url || '';
      const ago = withAgo ? playedAgoText(t.played_at) : '';
      return `
        <li class="trow" data-item-id="${RadioApp.escapeAttr(t.item_id || '')}">
          <span class="trow-art">${
            cover
              ? `<img src="${RadioApp.escapeAttr(cover)}" alt="" loading="lazy" decoding="async">`
              : '<span class="trow-art-placeholder" aria-hidden="true">♪</span>'
          }</span>
          <span class="trow-name">
            <b>${RadioApp.escape(t.title)}</b>
            <span>${RadioApp.escape(t.artist)}</span>
          </span>${ago ? `
          <span class="trow-ago">${RadioApp.escape(ago)}</span>` : ''}
        </li>`;
    }

    /**
     * Keyed render: the station poll runs sub-second while listening, and
     * rebuilding <img> rows every tick flickers. Rebuild only when the rows
     * actually change; otherwise just refresh the "N min ago" labels.
     */
    function updateTrackList(el, tracks, emptyMsg, { withAgo = false, withHearts = false } = {}) {
      const key = tracks
        .map((t) => `${t.item_id}|${t.played_at || ''}|${t.cover_url || ''}`)
        .join(',');
      if (el.dataset.rowsKey === key) {
        if (withAgo) {
          el.querySelectorAll('.trow').forEach((li, i) => {
            const agoEl = li.querySelector('.trow-ago');
            if (agoEl && tracks[i]) agoEl.textContent = playedAgoText(tracks[i].played_at);
          });
        }
        return;
      }
      el.dataset.rowsKey = key;
      el.innerHTML = tracks.length
        ? tracks.map((t) => trackRowHtml(t, { withAgo })).join('')
        : `<li class="muted">${emptyMsg}</li>`;
      if (withHearts) AdminLibraryControls?.onRecentListRendered?.();
    }

    function updateListeners(s) {
      const el = document.getElementById('station-listeners');
      if (!el) return;
      if (s.on_air === false) {
        el.hidden = false;
        el.textContent = 'Off air';
        el.classList.add('is-off-air');
        el.classList.remove('is-live');
        return;
      }
      const count = Number(s.listeners) || 0;
      el.hidden = false;
      el.classList.remove('is-off-air');
      el.classList.toggle('is-live', count > 0);
      el.textContent = count === 1 ? '1 listening' : `${count} listening`;
    }

    let pollTimer = null;
    let refreshToken = 0;
    let lastStreamEpoch = null;
    let currentStation = null;

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
      stopRail();
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

        if (!playingOther) {
          audio.dataset.streamSrc = listenUrl;

          if (onThisStation && audio.dataset.wantLive === '1') {
            const srcMismatch = !streamUrlsMatch(audio.currentSrc || audio.src, listenUrl);
            if (audio.error || (!audio.paused && srcMismatch)) {
              audio.reconnectLiveStream?.();
            }
          }
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
      updateListeners(s);
      document.querySelector('.station-tune-in')
        ?.classList.toggle('is-off-air', s.on_air === false);
      if (typeof AlchemyHome !== 'undefined') AlchemyHome.syncRailPlaying();
      syncRailViewing();
      updateTrackList(
        document.getElementById('up-next-list'),
        s.up_next,
        'Queue filling…'
      );
      updateTrackList(
        document.getElementById('recent-list'),
        s.recently_played,
        'Nothing played yet',
        { withAgo: true, withHearts: true }
      );
      if (audio) {
        GlobalLivePlayer.notifyStationMeta(s);
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
      stopRail();
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