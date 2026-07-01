/**
 * Theme model
 * ───────────
 * 1. Admin "default station theme" → saved on server (first-time visitors).
 * 2. Listener header dropdown → saved in localStorage (personal override).
 *
 * Priority on Tune In / home: localStorage override → server default → Violet.
 * Admin pages always use the server default only (never localStorage).
 */

const RadioThemes = {
  STORAGE_KEY: 'alchemyfm-theme',
  DEFAULT_ID: 'violet',
  _siteDefaultPromise: null,

  themes: [
    { id: 'violet', label: 'Violet', themeColor: '#09090b' },
    { id: 'gold', label: 'Gold', themeColor: '#16120d' },
    { id: 'cyan', label: 'Blue', themeColor: '#0e141c' },
    { id: 'emerald', label: 'Green', themeColor: '#0e1612' },
    { id: 'rose', label: 'Rose', themeColor: '#161014' },
    { id: 'crimson', label: 'Crimson', themeColor: '#160e10' },
    { id: 'sunset', label: 'Sunset', themeColor: '#161008' },
    { id: 'ocean', label: 'Ocean', themeColor: '#0a1416' },
    { id: 'lavender', label: 'Lavender', themeColor: '#121218' },
    { id: 'copper', label: 'Copper', themeColor: '#161010' },
    { id: 'midnight', label: 'Midnight', themeColor: '#080c14' },
    { id: 'sage', label: 'Sage', themeColor: '#121610' },
    { id: 'berry', label: 'Berry', themeColor: '#140e14' },
    { id: 'steel', label: 'Steel', themeColor: '#101216' },
    { id: 'wine', label: 'Wine', themeColor: '#140a10' },
    { id: 'ember', label: 'Ember', themeColor: '#160c0a' },
    { id: 'mocha', label: 'Mocha', themeColor: '#14100c' },
    { id: 'citrus', label: 'Citrus', themeColor: '#14140a' },
    { id: 'orchid', label: 'Orchid', themeColor: '#140e18' },
    { id: 'arctic', label: 'Arctic', themeColor: '#0e1218' },
    { id: 'forest', label: 'Forest', themeColor: '#0a120c' },
    { id: 'graphite', label: 'Graphite', themeColor: '#0c0c0e' },
  ],

  isAdminPage() {
    return /^\/admin(?:\.html|-login\.html|\/)/.test(location.pathname);
  },

  isListenerPage() {
    return Boolean(document.getElementById('theme-select'));
  },

  themeIds() {
    return this.themes.map((t) => t.id);
  },

  isValid(id) {
    return this.themeIds().includes(id);
  },

  meta(id) {
    return this.themes.find((t) => t.id === id) || this.themes[0];
  },

  /** Personal choice — only set when the listener changes the header dropdown. */
  getUserTheme() {
    try {
      const value = localStorage.getItem(this.STORAGE_KEY);
      return this.isValid(value) ? value : null;
    } catch (_) {
      return null;
    }
  },

  setUserTheme(id) {
    try {
      if (!this.isValid(id)) return;
      localStorage.setItem(this.STORAGE_KEY, id);
    } catch (_) {}
  },

  /** Site-wide default from the server (admin setting). */
  fetchSiteDefault() {
    if (!this._siteDefaultPromise) {
      this._siteDefaultPromise = (async () => {
        try {
          const res = await fetch('/api/health', { credentials: 'same-origin' });
          if (!res.ok) return this.DEFAULT_ID;
          const data = await res.json();
          return this.isValid(data.default_theme) ? data.default_theme : this.DEFAULT_ID;
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
      return this.isValid(data.default_theme) ? data.default_theme : this.DEFAULT_ID;
    } catch (_) {
      return null;
    }
  },

  invalidateSiteDefaultCache() {
    this._siteDefaultPromise = null;
  },

  /** What listeners should see: personal override, else site default. */
  async resolveListenerTheme() {
    const userTheme = this.getUserTheme();
    if (userTheme) return userTheme;
    return this.fetchSiteDefault();
  },

  apply(id) {
    const themeId = this.isValid(id) ? id : this.DEFAULT_ID;
    if (themeId === this.DEFAULT_ID) {
      document.documentElement.removeAttribute('data-theme');
    } else {
      document.documentElement.dataset.theme = themeId;
    }
    this.updateThemeColor(themeId);
  },

  updateThemeColor(id) {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) return;
    meta.setAttribute('content', this.meta(id).themeColor);
  },

  populateSelect(selectEl, selectedId) {
    if (!selectEl) return;
    const selected = this.isValid(selectedId) ? selectedId : this.DEFAULT_ID;
    selectEl.innerHTML = this.themes
      .map(
        (t) =>
          `<option value="${t.id}"${t.id === selected ? ' selected' : ''}>${t.label}</option>`
      )
      .join('');
    selectEl.value = selected;
  },

  /** Paint as early as possible (before DOM ready). */
  async earlyPaint() {
    if (this.isAdminPage()) {
      const siteDefault = await this.fetchSiteDefault();
      this.apply(siteDefault);
      return;
    }
    const userTheme = this.getUserTheme();
    if (userTheme) {
      this.apply(userTheme);
      return;
    }
    const siteDefault = await this.fetchSiteDefault();
    this.apply(siteDefault);
  },

  async initListener() {
    const select = document.getElementById('theme-select');
    const active = await this.resolveListenerTheme();

    this.apply(active);
    this.populateSelect(select, active);

    if (!select) return;

    select.addEventListener('change', () => {
      const next = select.value;
      this.setUserTheme(next);
      this.apply(next);
    });
  },

  async resolveAdminTheme() {
    return (await this.fetchAdminDefault()) || (await this.fetchSiteDefault());
  },

  async initAdminTheme() {
    if (!this.isAdminPage()) return null;
    const siteDefault = await this.resolveAdminTheme();
    this.apply(siteDefault);
    return siteDefault;
  },

  async initAdminDefault() {
    const select = document.getElementById('default_theme');
    if (!select) return null;

    const siteDefault = (await this.initAdminTheme()) || this.DEFAULT_ID;
    this.populateSelect(select, siteDefault);

    select.addEventListener('change', () => {
      this.apply(select.value);
    });

    return siteDefault;
  },

  setAdminDefaultSelect(themeId) {
    const select = document.getElementById('default_theme');
    if (!select) return;
    const id = this.isValid(themeId) ? themeId : this.DEFAULT_ID;
    this.populateSelect(select, id);
    this.apply(id);
    this.invalidateSiteDefaultCache();
  },
};

RadioThemes.earlyPaint();

function bootThemeUi() {
  if (RadioThemes.isAdminPage()) {
    if (document.getElementById('default_theme')) {
      RadioThemes.initAdminDefault();
    } else {
      RadioThemes.initAdminTheme();
    }
    return;
  }
  if (document.getElementById('theme-select')) {
    RadioThemes.initListener();
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootThemeUi);
} else {
  bootThemeUi();
}
