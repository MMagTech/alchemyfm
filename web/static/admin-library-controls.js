/**
 * Operator-only Navidrome heart controls on listener pages (PWA).
 * Sign-in via long-press on the logo button (not the text link).
 */
const AdminLibraryControls = {
  _admin: false,
  _adminBootPromise: null,
  _sessionWatch: false,
  _heartState: new Map(),
  _heartInFlight: new Map(),
  _busy: false,
  _currentItemId: null,
  _miniItemId: null,
  _heartPin: null,
  _HEART_CACHE_KEY: 'alchemyfm-heart-cache-v1',
  _HEART_PIN_MS: 180000,

  HEART_SVG: `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
    <path fill="currentColor" d="M10 17.5l-1.1-1C4.6 12.4 2 10.1 2 7a4 4 0 0 1 7-2.2A4 4 0 0 1 16 7c0 3.1-2.6 5.4-6.9 9.5L10 17.5z"/>
  </svg>`,

  loginUrl() {
    return `/admin-login.html?next=${encodeURIComponent(location.pathname + location.search)}`;
  },

  adminFetch(url, options = {}) {
    return fetch(url, {
      cache: 'no-store',
      credentials: 'same-origin',
      ...options,
    });
  },

  handleAuthFailure() {
    this.deactivateOperatorSession({
      quiet: false,
      message: 'Operator session expired. Long-press the logo to sign in again.',
    });
  },

  hydrateHeartCache() {
    try {
      const raw = sessionStorage.getItem(this._HEART_CACHE_KEY);
      if (!raw) return;
      const entries = JSON.parse(raw);
      if (!Array.isArray(entries)) return;
      entries.forEach(([itemId, hearted]) => {
        if (itemId) this._heartState.set(String(itemId), Boolean(hearted));
      });
    } catch {
      /* ignore corrupt cache */
    }
  },

  persistHeartCache() {
    try {
      sessionStorage.setItem(
        this._HEART_CACHE_KEY,
        JSON.stringify([...this._heartState.entries()])
      );
    } catch {
      /* storage full or private mode */
    }
  },

  async boot() {
    this.hydrateHeartCache();
    this.setupWordmarkGesture();
    this.setupSessionWatch();
    this._adminBootPromise = this.refreshAdminSession();
    await this._adminBootPromise;
  },

  setupSessionWatch() {
    if (this._sessionWatch) return;
    this._sessionWatch = true;
    window.addEventListener('pageshow', () => {
      void this.refreshAdminSession();
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        void this.refreshAdminSession();
      }
    });
  },

  async refreshAdminSession() {
    const admin = await this.checkAdmin();
    if (admin && !this._admin) {
      this._admin = true;
      this.activateOperatorUI();
    } else if (!admin && this._admin) {
      this.deactivateOperatorSession({ quiet: true });
    }
    return admin;
  },

  activateOperatorUI() {
    this.mountMiniHeart();
    this.mountStationHeartSlot();
    this.hookMiniMeta();
    this.hookStationNowPlaying();
    this.resyncCurrentTrack();
  },

  deactivateOperatorSession({ quiet = false, message = '' } = {}) {
    if (!this._admin) return;
    this._admin = false;
    this._currentItemId = null;
    this._miniItemId = null;
    this._heartPin = null;
    this._heartInFlight.clear();
    this._busy = false;
    this.ensureStationHeartRow();
    document.getElementById('operator-mini-heart')?.remove();
    if (!quiet) {
      this.showHint(
        message || 'Operator session expired. Long-press the logo to sign in again.',
        'error'
      );
    }
  },

  onStationShellReady() {
    if (!this._admin) return;
    this.mountStationHeartSlot();
    if (typeof window.__alchemyCurrentStation === 'function') {
      const station = window.__alchemyCurrentStation();
      if (station?.now_playing) {
        void this.syncStationHeart(station.now_playing);
      }
    }
  },

  syncStationHeartWhenReady(np) {
    const run = () => {
      if (this._admin) void this.syncStationHeart(np);
    };
    if (this._admin) {
      run();
      return;
    }
    if (this._adminBootPromise) {
      void this._adminBootPromise.then(run);
    }
  },

  resyncCurrentTrack() {
    if (!this._admin) return;

    if (typeof window.__alchemyCurrentStation === 'function') {
      const station = window.__alchemyCurrentStation();
      if (station?.now_playing) {
        void this.syncStationHeart(station.now_playing);
      }
    }

    const session = typeof GlobalLivePlayer !== 'undefined'
      ? GlobalLivePlayer.readSession?.()
      : null;
    if (session?.slug && typeof RadioApp !== 'undefined') {
      void RadioApp.fetchJSON(`/api/stations/${encodeURIComponent(session.slug)}`)
        .then((station) => this.syncMiniHeart(station?.now_playing || null))
        .catch(() => {});
    }
  },

  async checkAdmin() {
    try {
      const res = await this.adminFetch('/api/admin/me');
      return res.ok;
    } catch {
      return false;
    }
  },

  setupWordmarkGesture() {
    const btn = document.querySelector('button.site-wordmark-icon');
    if (!btn || btn.dataset.operatorGesture === '1') return;
    btn.dataset.operatorGesture = '1';

    const LONG_PRESS_MS = 850;
    const MOVE_CANCEL_PX = 12;

    let pressTimer = null;
    let didLongPress = false;
    let suppressNextTap = false;
    let startX = 0;
    let startY = 0;

    const goLogin = () => {
      suppressNextTap = true;
      location.href = this.loginUrl();
    };

    const goHome = () => {
      if (typeof GlobalLivePlayer !== 'undefined' && GlobalLivePlayer.isStationPage?.()) {
        GlobalLivePlayer.softNavigateToHome();
        return;
      }
      const path = location.pathname.replace(/\/$/, '') || '/';
      if (path !== '/' && path !== '/index.html') {
        location.href = '/';
      }
    };

    const clearPress = () => {
      if (pressTimer) {
        window.clearTimeout(pressTimer);
        pressTimer = null;
      }
    };

    btn.addEventListener('contextmenu', (e) => {
      e.preventDefault();
    });

    btn.addEventListener('selectstart', (e) => {
      e.preventDefault();
    });

    btn.addEventListener('click', (e) => {
      e.preventDefault();
    });

    btn.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;

      didLongPress = false;
      suppressNextTap = false;
      startX = e.clientX;
      startY = e.clientY;
      clearPress();

      try {
        btn.setPointerCapture(e.pointerId);
      } catch {}

      pressTimer = window.setTimeout(() => {
        pressTimer = null;
        didLongPress = true;
        if (navigator.vibrate) navigator.vibrate(10);
        goLogin();
      }, LONG_PRESS_MS);
    });

    btn.addEventListener('pointermove', (e) => {
      if (!pressTimer) return;
      const dx = Math.abs(e.clientX - startX);
      const dy = Math.abs(e.clientY - startY);
      if (dx > MOVE_CANCEL_PX || dy > MOVE_CANCEL_PX) {
        clearPress();
      }
    });

    const onPointerEnd = (e) => {
      clearPress();

      try {
        if (btn.hasPointerCapture?.(e.pointerId)) {
          btn.releasePointerCapture(e.pointerId);
        }
      } catch {}

      if (didLongPress || suppressNextTap) {
        e.preventDefault();
        didLongPress = false;
        return;
      }

      goHome();
    };

    btn.addEventListener('pointerup', onPointerEnd);
    btn.addEventListener('pointercancel', (e) => {
      clearPress();
      didLongPress = false;
      try {
        if (btn.hasPointerCapture?.(e.pointerId)) {
          btn.releasePointerCapture(e.pointerId);
        }
      } catch {}
    });
  },

  createHeartButton({ id, label }) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = id;
    btn.className = 'operator-heart-btn';
    btn.setAttribute('aria-pressed', 'false');
    btn.setAttribute('aria-label', label);
    btn.title = label;
    btn.innerHTML = this.HEART_SVG;
    btn.addEventListener('click', () => this.toggleHeart(btn));
    return btn;
  },

  mountStationHeartSlot() {
    if (!document.getElementById('now-playing-body')
      && !document.getElementById('operator-station-heart-meta-slot')
      && !document.getElementById('operator-station-heart-slot')) return;
    this.ensureStationHeartRow();
  },

  isStationMobilePlayerHeart() {
    return window.matchMedia('(max-width: 640px)').matches
      && Boolean(document.getElementById('operator-station-heart-slot'));
  },

  ensureStationHeartRow() {
    const metaSlot = document.getElementById('operator-station-heart-meta-slot');
    const playerSlot = document.getElementById('operator-station-heart-slot');
    const body = document.getElementById('now-playing-body');

    if (!this._admin) {
      if (metaSlot) metaSlot.hidden = true;
      if (playerSlot) playerSlot.hidden = true;
      document.getElementById('operator-station-heart-row')?.remove();
      return null;
    }

    const mobile = this.isStationMobilePlayerHeart();
    if (!mobile && !metaSlot && !body) return null;
    if (mobile && !playerSlot) return null;

    let row = document.getElementById('operator-station-heart-row');
    if (!row) {
      row = document.createElement('div');
      row.id = 'operator-station-heart-row';
      row.className = 'operator-library-row operator-station-heart-row';
      row.appendChild(this.createHeartButton({
        id: 'operator-station-heart',
        label: 'Heart on-air track',
      }));
    }

    if (mobile && playerSlot) {
      if (metaSlot) metaSlot.hidden = true;
      playerSlot.hidden = false;
      row.classList.add('is-player-heart');
      if (row.parentElement !== playerSlot) {
        playerSlot.appendChild(row);
      }
    } else if (metaSlot) {
      if (playerSlot) playerSlot.hidden = true;
      metaSlot.hidden = false;
      row.classList.remove('is-player-heart');
      if (row.parentElement !== metaSlot) {
        metaSlot.appendChild(row);
      }
    } else if (body) {
      if (playerSlot) playerSlot.hidden = true;
      row.classList.remove('is-player-heart');
      if (row.parentElement !== body.parentElement || row.previousElementSibling !== body) {
        body.insertAdjacentElement('afterend', row);
      }
    }

    return row.querySelector('.operator-heart-btn');
  },

  mountMiniHeart() {
    const meta = document.querySelector('.live-mini-meta');
    if (!meta || document.getElementById('operator-mini-heart')) return;
    const btn = this.createHeartButton({
      id: 'operator-mini-heart',
      label: 'Heart on-air track',
    });
    meta.insertAdjacentElement('afterend', btn);
    if (typeof GlobalLivePlayer !== 'undefined' && GlobalLivePlayer.isStationPage()) {
      btn.hidden = true;
    }
    const track = document.getElementById('live-mini-track');
    if (track) GlobalLivePlayer.syncMiniTrackMarquee(track);
  },

  hookMiniMeta() {
    if (typeof GlobalLivePlayer === 'undefined') return;
    const orig = GlobalLivePlayer.updateMiniMeta.bind(GlobalLivePlayer);
    GlobalLivePlayer.updateMiniMeta = (opts = {}) => {
      orig(opts);
      if (!this._admin) return;
      if (opts.pending) return;
      this.syncMiniHeart(opts.np ?? null);
    };
  },

  hookStationNowPlaying() {
    const self = this;
    const orig = window.__alchemyOnStreamSwitch;
    window.__alchemyOnStreamSwitch = function (station) {
      if (typeof orig === 'function') orig(station);
      if (self._admin && station?.slug) {
        self.syncStationHeart(station.now_playing || null);
      }
    };
  },

  itemId(np) {
    return np?.item_id ? String(np.item_id) : null;
  },

  heartedFromNp(np) {
    return typeof np?.hearted === 'boolean' ? np.hearted : null;
  },

  pinHeart(itemId, hearted) {
    this._heartPin = {
      itemId: String(itemId),
      hearted: Boolean(hearted),
      until: Date.now() + this._HEART_PIN_MS,
    };
  },

  clearHeartPinIfTrackChanged(itemId) {
    if (!this._heartPin || !itemId) return;
    if (this._heartPin.itemId !== String(itemId)) {
      this._heartPin = null;
    }
  },

  activeHeartPin(itemId) {
    const pin = this._heartPin;
    if (!pin || Date.now() >= pin.until) {
      this._heartPin = null;
      return null;
    }
    if (itemId && pin.itemId !== String(itemId)) return null;
    return pin;
  },

  /** Prefer user intent and in-flight state over poll snapshots. */
  heartedForSync(itemId, np) {
    if (!itemId) return null;

    const pin = this.activeHeartPin(itemId);
    const fromPoll = this.heartedFromNp(np);

    if (pin) {
      if (fromPoll === true && pin.hearted === true) {
        this._heartPin = null;
        return true;
      }
      return pin.hearted;
    }

    if (this._heartInFlight.has(itemId)) {
      return this._heartInFlight.get(itemId);
    }

    if (fromPoll === null) {
      return this._heartState.has(itemId) ? this._heartState.get(itemId) : null;
    }
    if (fromPoll === true) {
      return true;
    }
    if (this._heartState.get(itemId) === true) {
      return true;
    }
    return false;
  },

  lastKnownItemId(btn) {
    if (btn?.id === 'operator-mini-heart') {
      return this._miniItemId || btn.dataset.itemId || null;
    }
    return this._currentItemId || btn?.dataset?.itemId || null;
  },

  rememberItemId(btn, itemId) {
    if (!itemId) return;
    if (btn?.id === 'operator-mini-heart') {
      this._miniItemId = itemId;
    } else {
      this._currentItemId = itemId;
    }
  },

  syncHeartFromPoll(btn, np) {
    let itemId = this.itemId(np);
    if (itemId) {
      this.clearHeartPinIfTrackChanged(itemId);
    }

    if (!itemId) {
      if (!btn) return;
      const pin = this.activeHeartPin();
      if (pin) {
        this.syncHeartButton(btn, pin.itemId, pin.hearted);
        return;
      }
      itemId = this.lastKnownItemId(btn);
      if (!itemId) return;
      const hearted = this.heartedForSync(itemId, null);
      if (hearted === null) return;
      this.syncHeartButton(btn, itemId, hearted);
      return;
    }

    this.rememberItemId(btn, itemId);
    const hearted = this.heartedForSync(itemId, np);
    if (hearted === null) return;
    this.syncHeartButton(btn, itemId, hearted);
    if (hearted === true) {
      this._heartState.set(itemId, true);
      this.persistHeartCache();
    }
  },

  applyHeartState(itemId, hearted) {
    if (!itemId) return;
    this._heartState.set(itemId, hearted);
    this.persistHeartCache();
    document.querySelectorAll('.operator-heart-btn').forEach((el) => {
      const elId = el.dataset.itemId;
      if (elId && elId !== String(itemId)) return;
      this.rememberItemId(el, itemId);
      this.syncHeartButton(el, itemId, hearted);
    });
  },

  syncHeartButton(btn, itemId, hearted = null) {
    if (!btn) return;
    if (!itemId) {
      const pin = this.activeHeartPin();
      if (pin) {
        this.syncHeartButton(btn, pin.itemId, pin.hearted);
        return;
      }
      const fallbackId = this.lastKnownItemId(btn);
      if (fallbackId) return;
      btn.hidden = true;
      btn.removeAttribute('aria-busy');
      btn.setAttribute('aria-pressed', 'false');
      btn.classList.remove('is-hearted');
      return;
    }
    btn.hidden = false;
    btn.dataset.itemId = itemId;
    if (hearted === null) return;
    btn.setAttribute('aria-pressed', hearted ? 'true' : 'false');
    btn.classList.toggle('is-hearted', hearted);
    btn.setAttribute(
      'aria-label',
      hearted ? 'Unheart on-air track' : 'Heart on-air track'
    );
  },

  async syncStationHeart(np) {
    if (!this._admin) return;
    const btn = this.ensureStationHeartRow();
    this.syncHeartFromPoll(btn, np);
  },

  async syncMiniHeart(np) {
    if (!this._admin) return;
    const btn = document.getElementById('operator-mini-heart');
    if (btn && typeof GlobalLivePlayer !== 'undefined' && GlobalLivePlayer.isStationPage()) {
      btn.hidden = true;
      return;
    }

    this.syncHeartFromPoll(btn, np);
  },

  setHeartBusy(itemId, busy) {
    document.querySelectorAll(`.operator-heart-btn[data-item-id="${CSS.escape(itemId)}"]`)
      .forEach((el) => {
        if (busy) {
          el.setAttribute('aria-busy', 'true');
        } else {
          el.removeAttribute('aria-busy');
        }
      });
  },

  async toggleHeart(btn) {
    if (!btn || this._busy) return;
    const itemId = btn.dataset.itemId;
    if (!itemId) return;

    const next = btn.getAttribute('aria-pressed') !== 'true';
    const previous = !next;

    this._busy = true;
    this._heartInFlight.set(itemId, next);
    this.pinHeart(itemId, next);
    this.syncHeartButton(btn, itemId, next);
    this._heartState.set(itemId, next);
    this.persistHeartCache();
    this.rememberItemId(btn, itemId);
    this.setHeartBusy(itemId, true);

    try {
      const res = await this.adminFetch(
        `/api/admin/navidrome/songs/${encodeURIComponent(itemId)}/heart`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hearted: next }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) {
        this.handleAuthFailure();
        throw new Error('Operator session expired. Long-press the logo to sign in again.');
      }
      if (!res.ok) {
        const detail = data.detail;
        const msg = typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map((d) => d.msg || String(d)).join(', ')
            : 'Heart action failed';
        throw new Error(msg);
      }
      const hearted = Boolean(data.hearted);
      this.pinHeart(itemId, hearted);
      this.applyHeartState(itemId, hearted);

      if (hearted && data.playlist_configured === false) {
        this.showHint('No Playlist Set', 'playlist');
      }
    } catch (err) {
      this.pinHeart(itemId, previous);
      this.applyHeartState(itemId, previous);
      this.showHint(err.message || 'Heart action failed', 'error');
    } finally {
      this._heartInFlight.delete(itemId);
      this._busy = false;
      this.setHeartBusy(itemId, false);
    }
  },

  hintIcon(type) {
    if (type === 'error') {
      return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>';
    }
    if (type === 'playlist') {
      return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6h12M9 12h12M9 18h12"/><circle cx="4" cy="6" r="1.25" fill="currentColor" stroke="none"/><circle cx="4" cy="12" r="1.25" fill="currentColor" stroke="none"/><circle cx="4" cy="18" r="1.25" fill="currentColor" stroke="none"/></svg>';
    }
    return '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg>';
  },

  dismissHint() {
    const host = document.getElementById('operator-hint-host');
    host?.classList.remove('is-visible');
    window.clearTimeout(this._hintTimer);
    this._hintTimer = null;
  },

  showHint(message, type = 'info') {
    const autoMs = 5000;
    let host = document.getElementById('operator-hint-host');
    if (!host) {
      host = document.createElement('div');
      host.id = 'operator-hint-host';
      host.className = 'operator-hint-host';
      host.setAttribute('aria-live', 'polite');
      document.body.appendChild(host);
    }

    document.getElementById('operator-hint')?.remove();

    host.replaceChildren();
    host.className = `operator-hint-host is-visible operator-hint-host-${type}`;

    const card = document.createElement('div');
    card.className = `operator-hint-card operator-hint-${type}`;
    card.setAttribute('role', 'status');

    const icon = document.createElement('div');
    icon.className = 'operator-hint-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = this.hintIcon(type);

    const copy = document.createElement('p');
    copy.className = 'operator-hint-message';
    copy.textContent = message;

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'operator-hint-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';
    close.addEventListener('click', () => this.dismissHint());

    const progress = document.createElement('span');
    progress.className = 'operator-hint-progress';
    progress.style.animationDuration = `${autoMs}ms`;

    card.append(icon, copy, close, progress);
    host.appendChild(card);

    window.clearTimeout(this._hintTimer);
    this._hintTimer = window.setTimeout(() => this.dismissHint(), autoMs);
  },
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => AdminLibraryControls.boot());
} else {
  AdminLibraryControls.boot();
}
