/**
 * Theme model
 * ───────────
 * A theme is two independent choices:
 *
 *   ACCENT — one colour (amber by default). Everything accent-derived is
 *            color-mix()'d from it in CSS, so this is a single hex value.
 *   MODE   — 'system' (default), 'light' or 'dark'. Only sets the neutrals.
 *
 * Resolution order
 *   Accent: listener's localStorage pick → server default (admin) → amber.
 *   Mode:   listener's localStorage pick → the OS, via prefers-color-scheme.
 *
 * Admin pages always use the server default accent and never the listener's
 * personal override, so the operator sees the site's real default.
 */

const RadioThemes = {
  ACCENT_KEY: 'alchemyfm-accent',
  MODE_KEY: 'alchemyfm-mode',
  DEFAULT_ID: 'amber',
  _siteDefaultPromise: null,

  /** Order here is the order shown in the picker. */
  themes: [
    { id: 'amber', label: 'Amber', swatch: '#f59e0b' },
    { id: 'violet', label: 'Violet', swatch: '#8b5cf6' },
    { id: 'blue', label: 'Blue', swatch: '#3b82f6' },
    { id: 'emerald', label: 'Emerald', swatch: '#10b981' },
    { id: 'rose', label: 'Rose', swatch: '#f43f5e' },
    { id: 'cyan', label: 'Cyan', swatch: '#06b6d4' },
  ],

  /**
   * Installs from before the accent model stored one of 22 full palettes.
   * Mirrors LEGACY_THEME_MAP in backend/app/themes.py — keep them in step.
   */
  legacy: {
    gold: 'amber', sunset: 'amber', citrus: 'amber', copper: 'amber',
    ember: 'amber', mocha: 'amber',
    ocean: 'cyan', arctic: 'cyan',
    midnight: 'blue', steel: 'blue',
    sage: 'emerald', forest: 'emerald',
    crimson: 'rose', wine: 'rose', berry: 'rose',
    lavender: 'violet', orchid: 'violet', graphite: 'violet',
  },

  /** The browser-tab colour has to match whichever neutral base is showing. */
  MODE_COLORS: { dark: '#09090b', light: '#eeeef1' },

  isAdminPage() {
    return /^\/admin(?:\.html|-login\.html|\/)/.test(location.pathname);
  },

  themeIds() {
    return this.themes.map((t) => t.id);
  },

  isValid(id) {
    return this.themeIds().includes(id);
  },

  /** Accept a current accent, an old palette id, or nothing. */
  normalize(id) {
    if (this.isValid(id)) return id;
    return this.legacy[id] || this.DEFAULT_ID;
  },

  meta(id) {
    return this.themes.find((t) => t.id === this.normalize(id)) || this.themes[0];
  },

  // ── accent ────────────────────────────────────────────────

  /** Personal choice — only set when the listener picks one. */
  getUserAccent() {
    try {
      const value = localStorage.getItem(this.ACCENT_KEY);
      return this.isValid(value) ? value : null;
    } catch (_) {
      return null;
    }
  },

  setUserAccent(id) {
    try {
      if (!this.isValid(id)) return;
      localStorage.setItem(this.ACCENT_KEY, id);
    } catch (_) {}
  },

  // ── mode ──────────────────────────────────────────────────

  /** 'system' | 'light' | 'dark' — what the listener asked for. */
  getUserMode() {
    try {
      const value = localStorage.getItem(this.MODE_KEY);
      return value === 'light' || value === 'dark' ? value : 'system';
    } catch (_) {
      return 'system';
    }
  },

  setUserMode(mode) {
    try {
      if (mode === 'system') localStorage.removeItem(this.MODE_KEY);
      else if (mode === 'light' || mode === 'dark') localStorage.setItem(this.MODE_KEY, mode);
    } catch (_) {}
  },

  /** What the OS currently wants. */
  systemMode() {
    try {
      return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    } catch (_) {
      return 'dark';
    }
  },

  /** The mode actually on screen, resolving 'system'. */
  effectiveMode() {
    const mode = this.getUserMode();
    return mode === 'system' ? this.systemMode() : mode;
  },

  // ── server default ────────────────────────────────────────

  fetchSiteDefault() {
    if (!this._siteDefaultPromise) {
      this._siteDefaultPromise = (async () => {
        try {
          const res = await fetch('/api/health', { credentials: 'same-origin' });
          if (!res.ok) return this.DEFAULT_ID;
          const data = await res.json();
          return this.normalize(data.default_theme);
        } catch (_) {
          return this.DEFAULT_ID;
        }
      })();
    }
    return this._siteDefaultPromise;
  },

  async fetchAdminDefault() {
    try {
      let res = await fetch('/api/admin/broadcast/appearance', { credentials: 'same-origin' });
      if (res.status === 404) {
        res = await fetch('/api/admin/broadcast', { credentials: 'same-origin' });
      }
      if (!res.ok) return null;
      const data = await res.json();
      return this.normalize(data.default_theme);
    } catch (_) {
      return null;
    }
  },

  invalidateSiteDefaultCache() {
    this._siteDefaultPromise = null;
  },

  /** What listeners should see: personal accent, else the site default. */
  async resolveListenerAccent() {
    return this.getUserAccent() || (await this.fetchSiteDefault());
  },

  // ── applying ──────────────────────────────────────────────

  applyAccent(id) {
    const accent = this.normalize(id);
    // Amber is the :root default, so it needs no attribute.
    if (accent === this.DEFAULT_ID) document.documentElement.removeAttribute('data-accent');
    else document.documentElement.dataset.accent = accent;
  },

  applyMode(mode) {
    const root = document.documentElement;
    // No attribute means "follow the OS" and lets the media query drive.
    if (mode === 'light' || mode === 'dark') root.dataset.mode = mode;
    else root.removeAttribute('data-mode');
    this.updateThemeColor();
  },

  /** Back-compat shim: older callers applied a single theme id. */
  apply(id) {
    this.applyAccent(id);
  },

  updateThemeColor() {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) return;
    meta.setAttribute('content', this.MODE_COLORS[this.effectiveMode()]);
  },

  /** Repaint when the OS flips and the listener hasn't pinned a mode. */
  watchSystemMode() {
    if (this._watching) return;
    this._watching = true;
    try {
      const mq = window.matchMedia('(prefers-color-scheme: light)');
      const onChange = () => {
        if (this.getUserMode() === 'system') this.updateThemeColor();
      };
      if (mq.addEventListener) mq.addEventListener('change', onChange);
      else if (mq.addListener) mq.addListener(onChange);
    } catch (_) {}
  },

  // ── boot ──────────────────────────────────────────────────

  /** Paint as early as possible, before DOM ready, to avoid a flash. */
  async earlyPaint() {
    this.applyMode(this.getUserMode());
    if (this.isAdminPage()) {
      this.applyAccent(await this.fetchSiteDefault());
      return;
    }
    const userAccent = this.getUserAccent();
    if (userAccent) {
      this.applyAccent(userAccent);
      return;
    }
    this.applyAccent(await this.fetchSiteDefault());
  },

  async initListener() {
    const accent = await this.resolveListenerAccent();
    this.applyAccent(accent);
    this.applyMode(this.getUserMode());
    this.watchSystemMode();
    this.mountPicker(accent);
  },

  /**
   * Swatch row + light/dark toggle. Replaces the old <select>; if a page
   * still has one, it is left alone and simply unused.
   */
  mountPicker(activeAccent) {
    const host = document.getElementById('theme-picker');
    if (!host) return;

    const accent = this.normalize(activeAccent);
    const mode = this.getUserMode();

    host.innerHTML = `
      <div class="theme-accents" role="group" aria-label="Accent colour">
        ${this.themes.map((t) => `
          <button type="button" class="theme-swatch" data-accent-id="${t.id}"
            style="--swatch:${t.swatch}" title="${t.label}"
            aria-label="${t.label}" aria-pressed="${t.id === accent}"></button>`).join('')}
      </div>
      <button type="button" class="theme-mode-toggle" id="theme-mode-toggle"
        aria-label="Colour mode" title="Colour mode" data-mode="${mode}">
        <span class="theme-mode-icon" aria-hidden="true"></span>
      </button>`;

    host.querySelectorAll('.theme-swatch').forEach((btn) => {
      btn.addEventListener('click', () => {
        const next = btn.dataset.accentId;
        this.setUserAccent(next);
        this.applyAccent(next);
        host.querySelectorAll('.theme-swatch').forEach((b) => {
          b.setAttribute('aria-pressed', String(b.dataset.accentId === next));
        });
      });
    });

    const toggle = host.querySelector('#theme-mode-toggle');
    toggle?.addEventListener('click', () => {
      // system → light → dark → system
      const order = ['system', 'light', 'dark'];
      const next = order[(order.indexOf(this.getUserMode()) + 1) % order.length];
      this.setUserMode(next);
      this.applyMode(next);
      toggle.dataset.mode = next;
      toggle.title = next === 'system' ? 'Colour mode: follows your device' : `Colour mode: ${next}`;
    });
  },

  async resolveAdminTheme() {
    return (await this.fetchAdminDefault()) || (await this.fetchSiteDefault());
  },

  async initAdminTheme() {
    if (!this.isAdminPage()) return null;
    const siteDefault = await this.resolveAdminTheme();
    this.applyAccent(siteDefault);
    this.applyMode(this.getUserMode());
    this.watchSystemMode();
    return siteDefault;
  },

  /** The admin's "default accent for new visitors" control. */
  async initAdminDefault() {
    const host = document.getElementById('default-accent');
    if (!host) return null;

    const siteDefault = (await this.initAdminTheme()) || this.DEFAULT_ID;
    this.renderAdminAccentPicker(host, siteDefault);
    return siteDefault;
  },

  renderAdminAccentPicker(host, selected) {
    const accent = this.normalize(selected);
    host.innerHTML = `
      <input type="hidden" id="default_theme" name="default_theme" value="${accent}">
      <div class="theme-accents" role="group" aria-label="Default accent colour">
        ${this.themes.map((t) => `
          <button type="button" class="theme-swatch" data-accent-id="${t.id}"
            style="--swatch:${t.swatch}" title="${t.label}"
            aria-label="${t.label}" aria-pressed="${t.id === accent}"></button>`).join('')}
      </div>`;

    host.querySelectorAll('.theme-swatch').forEach((btn) => {
      btn.addEventListener('click', () => {
        const next = btn.dataset.accentId;
        host.querySelector('#default_theme').value = next;
        this.applyAccent(next);
        host.querySelectorAll('.theme-swatch').forEach((b) => {
          b.setAttribute('aria-pressed', String(b.dataset.accentId === next));
        });
      });
    });
  },

  setAdminDefaultSelect(themeId) {
    const host = document.getElementById('default-accent');
    if (!host) return;
    const id = this.normalize(themeId);
    this.renderAdminAccentPicker(host, id);
    this.applyAccent(id);
    this.invalidateSiteDefaultCache();
  },
};

RadioThemes.earlyPaint();

function bootThemeUi() {
  if (RadioThemes.isAdminPage()) {
    if (document.getElementById('default-accent')) {
      RadioThemes.initAdminDefault();
    } else {
      RadioThemes.initAdminTheme();
    }
    return;
  }
  RadioThemes.initListener();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootThemeUi);
} else {
  bootThemeUi();
}
