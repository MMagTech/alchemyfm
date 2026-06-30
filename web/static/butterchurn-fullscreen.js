/**
 * Optional fullscreen MilkDrop-style visuals via Butterchurn.
 * Lazy-loaded — no impact until the user opens fullscreen.
 */

const BUTTERCHURN_URL = 'https://unpkg.com/butterchurn@3.0.0-beta.5/dist/butterchurn.js';
const PRESETS_BASE = 'https://unpkg.com/butterchurn-presets@3.0.0-beta.4/dist';
/** Butterchurn splits presets into packs; base alone is only a small subset. */
const PRESET_PACKS = [
  { url: `${PRESETS_BASE}/base.min.js`, global: 'base' },
  { url: `${PRESETS_BASE}/extra.min.js`, global: 'extra' },
  { url: `${PRESETS_BASE}/md1.min.js`, global: 'md1' },
  { url: `${PRESETS_BASE}/nonMinimal.min.js`, global: 'nonMinimal' },
];
const OVERLAY_VISIBLE_MS = 4500;
const MAX_PIXEL_RATIO = 1.5;
const SETTINGS_KEY = 'milkdrop-fullscreen-settings';

const DEFAULT_SETTINGS = {
  autoCycle: true,
  cycleSec: 45,
  blendSec: 3,
  intensity: 3,
  presetKey: '',
};

let session = null;
let butterchurnLib = null;
let presetsCache = null;

export function isActive() {
  return session != null;
}

export function isWebGL2Supported() {
  try {
    const canvas = document.createElement('canvas');
    return !!canvas.getContext('webgl2');
  } catch {
    return false;
  }
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
    return { ...DEFAULT_SETTINGS, ...saved };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettings(settings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    /* ignore quota errors */
  }
}

function presetPool(presets) {
  return Object.keys(presets).sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: 'base' })
  );
}

function pickPresetKey(presets, preferred) {
  const pool = presetPool(presets);
  if (!pool.length) return null;
  if (preferred && presets[preferred]) return preferred;
  return pool[Math.floor(Math.random() * pool.length)];
}

function nextPresetKey(presets, currentKey, step = 1) {
  const pool = presetPool(presets);
  if (!pool.length) return currentKey;
  const idx = Math.max(0, pool.indexOf(currentKey));
  const nextIdx = (idx + step + pool.length) % pool.length;
  return pool[nextIdx];
}

async function loadButterchurn() {
  if (butterchurnLib) return butterchurnLib;
  const mod = await import(BUTTERCHURN_URL);
  butterchurnLib = mod.default;
  return butterchurnLib;
}

async function loadPresetScript(url, globalName) {
  if (window[globalName]?.default) return;
  const existing = document.querySelector(`script[data-butterchurn-pack="${globalName}"]`);
  if (existing) {
    await new Promise((resolve, reject) => {
      existing.addEventListener('load', resolve, { once: true });
      existing.addEventListener('error', reject, { once: true });
    });
    return;
  }
  await new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = url;
    script.dataset.butterchurnPack = globalName;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`Could not load preset pack: ${globalName}`));
    document.head.appendChild(script);
  });
}

async function loadPresets() {
  if (presetsCache) return presetsCache;

  await Promise.all(
    PRESET_PACKS.map((pack) => loadPresetScript(pack.url, pack.global))
  );

  const merged = {};
  for (const pack of PRESET_PACKS) {
    const data = window[pack.global]?.default;
    if (data) Object.assign(merged, data);
  }

  if (!Object.keys(merged).length) {
    throw new Error('No MilkDrop presets loaded.');
  }

  presetsCache = merged;
  return presetsCache;
}

function truncatePresetName(name, max = 42) {
  if (!name) return '';
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}

function scrollPresetIntoView(session) {
  const list = session.root.querySelector('#milkdrop-preset-list');
  const selected = list?.querySelector('.milkdrop-preset-item.is-selected');
  selected?.scrollIntoView({ block: 'nearest' });
}

function renderPresetList(session, filter = '') {
  const list = session.root.querySelector('#milkdrop-preset-list');
  if (!list || !session.presetPool) return;

  const q = filter.trim().toLowerCase();
  const filtered = q
    ? session.presetPool.filter((key) => key.toLowerCase().includes(q))
    : session.presetPool;

  const frag = document.createDocumentFragment();
  if (!filtered.length) {
    const empty = document.createElement('li');
    empty.className = 'milkdrop-preset-empty';
    empty.textContent = 'No matches';
    frag.appendChild(empty);
  } else {
    for (const key of filtered) {
      const li = document.createElement('li');
      li.className = 'milkdrop-preset-item';
      if (key === session.presetKey) li.classList.add('is-selected');
      li.dataset.key = key;
      li.title = key;
      li.textContent = truncatePresetName(key, 52);
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', key === session.presetKey ? 'true' : 'false');
      frag.appendChild(li);
    }
  }
  list.replaceChildren(frag);
}

function syncPresetListSelection(session) {
  const list = session.root.querySelector('#milkdrop-preset-list');
  const current = session.root.querySelector('#milkdrop-preset-current');
  const name = session.presetKey || '';
  if (current) {
    current.textContent = name;
    current.title = name;
  }
  if (!list) return;
  list.querySelectorAll('.milkdrop-preset-item').forEach((li) => {
    const selected = li.dataset.key === session.presetKey;
    li.classList.toggle('is-selected', selected);
    li.setAttribute('aria-selected', selected ? 'true' : 'false');
  });
  scrollPresetIntoView(session);
}

function buildOverlay(mobile = false) {
  const root = document.createElement('div');
  root.id = 'milkdrop-fullscreen';
  root.className = 'milkdrop-fullscreen';
  if (mobile) root.classList.add('milkdrop-mobile');
  root.innerHTML = `
    <canvas class="milkdrop-canvas" id="milkdrop-canvas" aria-hidden="true"></canvas>
    <div class="milkdrop-dismiss-hint milkdrop-chrome">${mobile ? 'Tap × to exit · ⚙ settings' : 'Esc or click to exit · S settings'}</div>
    ${mobile ? `<button type="button" class="milkdrop-close-btn milkdrop-chrome" id="milkdrop-close-btn" aria-label="Close fullscreen visuals">×</button>` : ''}
    <button type="button" class="milkdrop-settings-toggle milkdrop-chrome" id="milkdrop-settings-toggle"
      aria-expanded="false" aria-controls="milkdrop-settings" title="Settings (S)">
      <span aria-hidden="true">⚙</span>
    </button>
    <div class="milkdrop-settings milkdrop-chrome" id="milkdrop-settings" hidden>
      <h4 class="milkdrop-settings-title">Visualizer</h4>
      <label class="milkdrop-field milkdrop-field-preset">
        <span class="milkdrop-field-label">Preset</span>
        <p class="milkdrop-preset-current" id="milkdrop-preset-current" title=""></p>
        <input type="search" id="milkdrop-preset-search" class="milkdrop-input"
          placeholder="Search presets…" autocomplete="off" spellcheck="false" aria-label="Search presets">
        <ul class="milkdrop-preset-list" id="milkdrop-preset-list" role="listbox" aria-label="Presets"></ul>
      </label>
      <div class="milkdrop-settings-row">
        <button type="button" class="milkdrop-btn" id="milkdrop-preset-prev">Previous</button>
        <button type="button" class="milkdrop-btn" id="milkdrop-preset-next">Next</button>
      </div>
      <label class="milkdrop-check">
        <input type="checkbox" id="milkdrop-auto-cycle">
        <span>Auto-Cycle Presets</span>
      </label>
      <label class="milkdrop-field">
        <span class="milkdrop-field-label">Cycle Every</span>
        <select id="milkdrop-cycle-secs" class="milkdrop-select">
          <option value="30">30 seconds</option>
          <option value="45">45 seconds</option>
          <option value="60">1 minute</option>
          <option value="90">90 seconds</option>
          <option value="120">2 minutes</option>
        </select>
      </label>
      <label class="milkdrop-field milkdrop-field-range">
        <span class="milkdrop-field-label">Blend <output id="milkdrop-blend-out">3s</output></span>
        <input type="range" id="milkdrop-blend" min="0" max="8" step="0.5" value="3">
      </label>
      <label class="milkdrop-field milkdrop-field-range">
        <span class="milkdrop-field-label">Intensity <output id="milkdrop-intensity-out">3.0×</output></span>
        <input type="range" id="milkdrop-intensity" min="1.5" max="5" step="0.1" value="3">
      </label>
      <button type="button" class="milkdrop-btn milkdrop-btn-block" id="milkdrop-show-track">
        Show Now Playing
      </button>
      <p class="milkdrop-shortcuts">← → change preset · O now playing · S settings · Esc exit</p>
    </div>
    <div class="milkdrop-overlay" id="milkdrop-overlay">
      <img class="milkdrop-overlay-art" id="milkdrop-overlay-art" alt="" hidden>
      <div class="milkdrop-overlay-text">
        <p class="milkdrop-overlay-station" id="milkdrop-overlay-station"></p>
        <p class="milkdrop-overlay-title" id="milkdrop-overlay-title"></p>
        <p class="milkdrop-overlay-artist" id="milkdrop-overlay-artist"></p>
      </div>
    </div>`;
  document.body.appendChild(root);
  return root;
}

function setOverlayMeta(root, meta) {
  const station = root.querySelector('#milkdrop-overlay-station');
  const title = root.querySelector('#milkdrop-overlay-title');
  const artist = root.querySelector('#milkdrop-overlay-artist');
  const art = root.querySelector('#milkdrop-overlay-art');
  const overlay = root.querySelector('#milkdrop-overlay');

  station.textContent = meta.stationName || '';
  title.textContent = meta.title || 'Live';
  artist.textContent = meta.artist || '';

  if (meta.artworkUrl) {
    art.src = meta.artworkUrl;
    art.hidden = false;
  } else {
    art.removeAttribute('src');
    art.hidden = true;
  }

  overlay.classList.remove('is-faded');
  overlay.classList.add('is-visible');
  clearTimeout(root._overlayTimer);
  root._overlayTimer = setTimeout(() => {
    overlay.classList.remove('is-visible');
    overlay.classList.add('is-faded');
  }, OVERLAY_VISIBLE_MS);
}

function resizeVisualizer(visualizer, canvas) {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const ratio = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
  canvas.width = Math.floor(w * ratio);
  canvas.height = Math.floor(h * ratio);
  visualizer.setRendererSize(canvas.width, canvas.height);
}

function boostTimeDomain(buffer, intensity) {
  let peak = 1;
  for (let i = 0; i < buffer.length; i++) {
    peak = Math.max(peak, Math.abs(buffer[i] - 128));
  }
  const gain = (intensity * 110) / Math.max(peak, 6);
  for (let i = 0; i < buffer.length; i++) {
    const v = (buffer[i] - 128) * gain + 128;
    buffer[i] = Math.max(0, Math.min(255, Math.round(v)));
  }
}

function syncSettingsUi(session) {
  const { root, settings } = session;
  const cycle = root.querySelector('#milkdrop-cycle-secs');
  const blend = root.querySelector('#milkdrop-blend');
  const blendOut = root.querySelector('#milkdrop-blend-out');
  const intensity = root.querySelector('#milkdrop-intensity');
  const intensityOut = root.querySelector('#milkdrop-intensity-out');
  const autoCycle = root.querySelector('#milkdrop-auto-cycle');

  syncPresetListSelection(session);
  if (cycle) cycle.value = String(settings.cycleSec);
  if (blend) blend.value = String(settings.blendSec);
  if (blendOut) blendOut.textContent = `${settings.blendSec}s`;
  if (intensity) intensity.value = String(settings.intensity);
  if (intensityOut) intensityOut.textContent = `${settings.intensity.toFixed(1)}×`;
  if (autoCycle) autoCycle.checked = settings.autoCycle;
  session.graph?.setMilkdropGain?.(settings.intensity);
}

function persistSessionSettings(session) {
  saveSettings({
    autoCycle: session.settings.autoCycle,
    cycleSec: session.settings.cycleSec,
    blendSec: session.settings.blendSec,
    intensity: session.settings.intensity,
    presetKey: session.presetKey,
  });
}

function restartPresetTimer(session) {
  clearInterval(session.presetTimer);
  session.presetTimer = null;
  if (!session.settings.autoCycle) return;
  const ms = Math.max(15000, session.settings.cycleSec * 1000);
  session.presetTimer = setInterval(() => {
    loadPresetByKey(session, nextPresetKey(session.presets, session.presetKey, 1), session.settings.blendSec);
  }, ms);
}

function isMobileVisualizer() {
  if (/iPhone|iPad|iPod/i.test(navigator.userAgent)) return true;
  return window.matchMedia('(max-width: 640px)').matches;
}

function fillAudioLevels(session) {
  const { analyserNode, settings } = session;
  if (!analyserNode) return;

  if (session.mobile) {
    const f = session.freqByteArray;
    analyserNode.getByteFrequencyData(f);
    const t = session.timeByteArray;
    session.wavePhase = (session.wavePhase || 0) + 0.1;
    let bass = 0;
    for (let i = 0; i < 10; i++) bass += f[i];
    bass /= 10;
    const bassN = bass / 255;
    for (let i = 0; i < t.length; i++) {
      const fi = Math.min(f.length - 1, Math.floor((i / t.length) * f.length * 0.8));
      const amp = (f[fi] / 255) * 0.8 + bassN * 0.2;
      t[i] = 128 + Math.sin(session.wavePhase + i * 0.12) * amp * 130;
    }
    boostTimeDomain(t, settings.intensity * 2.4);
    session.timeByteArrayL.set(t);
    session.timeByteArrayR.set(t);
    return;
  }

  analyserNode.getByteTimeDomainData(session.timeByteArray);
  analyserNode.getByteTimeDomainData(session.scratchL);
  analyserNode.getByteTimeDomainData(session.scratchR);
  session.timeByteArrayL.set(session.scratchL);
  session.timeByteArrayR.set(session.scratchR);
  boostTimeDomain(session.timeByteArray, settings.intensity);
  boostTimeDomain(session.timeByteArrayL, settings.intensity);
  boostTimeDomain(session.timeByteArrayR, settings.intensity);
}

function loadPresetByKey(session, key, blendSec) {
  if (!key || !session.presets[key]) return;
  session.presetKey = key;
  const blend = session.mobile ? 0 : (blendSec ?? session.settings.blendSec);

  try {
    session.visualizer.loadPreset(session.presets[key], blend);
  } catch {
    if (session.mobile) {
      try {
        session.visualizer.loadPreset(session.presets[key], 0);
      } catch {
        /* ignore */
      }
    }
  }

  if (session.mobile) {
    session.graph?.audioContext?.resume?.().catch(() => {});
  }

  syncSettingsUi(session);
  persistSessionSettings(session);
}

function toggleSettingsPanel(session, forceOpen) {
  const panel = session.root.querySelector('#milkdrop-settings');
  const toggle = session.root.querySelector('#milkdrop-settings-toggle');
  if (!panel || !toggle) return;
  const open = forceOpen ?? panel.hidden;
  panel.hidden = !open;
  toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  session.root.classList.toggle('milkdrop-settings-open', open);
  if (open) {
    session.root.classList.remove('milkdrop-chrome-hidden');
    clearTimeout(session.chromeIdleTimer);
    requestAnimationFrame(() => scrollPresetIntoView(session));
  } else {
    session.showChrome?.();
  }
}

const CHROME_IDLE_MS = 2800;

function wireMobileWebGLRecovery(session) {
  if (!session.mobile) return;
  const canvas = session.canvas;
  canvas.addEventListener('webglcontextlost', (e) => {
    e.preventDefault();
    session.webglContextLost = true;
    clearTimeout(session.webglRecoverTimer);
    session.webglRecoverTimer = setTimeout(() => {
      if (!session?.rendering) return;
      const key = session.presetKey;
      if (!key || !session.presets[key]) return;
      try {
        session.visualizer.loadPreset(session.presets[key], 0);
        session.webglContextLost = false;
      } catch {
        /* ignore */
      }
    }, 300);
  }, false);
  canvas.addEventListener('webglcontextrestored', () => {
    session.webglContextLost = false;
    clearTimeout(session.webglRecoverTimer);
    const key = session.presetKey;
    if (!key || !session.presets[key]) return;
    try {
      session.visualizer.loadPreset(session.presets[key], 0);
    } catch {
      /* ignore */
    }
  }, false);
}

function wireChromeIdleHide(session) {
  const { root } = session;
  session.chromeAbort = new AbortController();
  const { signal } = session.chromeAbort;

  session.showChrome = () => {
    root.classList.remove('milkdrop-chrome-hidden');
    clearTimeout(session.chromeIdleTimer);
    if (root.classList.contains('milkdrop-settings-open')) return;
    session.chromeIdleTimer = setTimeout(() => {
      if (!root.classList.contains('milkdrop-settings-open')) {
        root.classList.add('milkdrop-chrome-hidden');
      }
    }, CHROME_IDLE_MS);
  };

  root.addEventListener('mousemove', session.showChrome, { signal });
  root.addEventListener('mousedown', session.showChrome, { signal });
  root.addEventListener('touchstart', session.showChrome, { passive: true, signal });
  session.showChrome();
}

function wireSettings(session) {
  const { root, presets } = session;
  const pool = presetPool(presets);
  session.presetPool = pool;
  const title = root.querySelector('.milkdrop-settings-title');
  if (title) title.textContent = `Visualizer (${pool.length} presets)`;

  renderPresetList(session);

  const search = root.querySelector('#milkdrop-preset-search');
  search?.addEventListener('input', () => {
    renderPresetList(session, search.value);
    syncPresetListSelection(session);
  });

  root.querySelector('#milkdrop-preset-list')?.addEventListener('click', (e) => {
    const item = e.target.closest('.milkdrop-preset-item[data-key]');
    if (!item) return;
    e.stopPropagation();
    loadPresetByKey(session, item.dataset.key, session.settings.blendSec);
    restartPresetTimer(session);
  });

  syncSettingsUi(session);

  root.querySelector('#milkdrop-settings-toggle')?.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleSettingsPanel(session);
  });

  root.querySelector('#milkdrop-settings')?.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  root.querySelector('#milkdrop-preset-prev')?.addEventListener('click', (e) => {
    e.stopPropagation();
    loadPresetByKey(session, nextPresetKey(presets, session.presetKey, -1), session.settings.blendSec);
    restartPresetTimer(session);
  });

  root.querySelector('#milkdrop-preset-next')?.addEventListener('click', (e) => {
    e.stopPropagation();
    loadPresetByKey(session, nextPresetKey(presets, session.presetKey, 1), session.settings.blendSec);
    restartPresetTimer(session);
  });

  root.querySelector('#milkdrop-auto-cycle')?.addEventListener('change', (e) => {
    session.settings.autoCycle = e.target.checked;
    persistSessionSettings(session);
    restartPresetTimer(session);
  });

  root.querySelector('#milkdrop-cycle-secs')?.addEventListener('change', (e) => {
    session.settings.cycleSec = Number(e.target.value);
    persistSessionSettings(session);
    restartPresetTimer(session);
  });

  root.querySelector('#milkdrop-blend')?.addEventListener('input', (e) => {
    session.settings.blendSec = Number(e.target.value);
    root.querySelector('#milkdrop-blend-out').textContent = `${session.settings.blendSec}s`;
    persistSessionSettings(session);
  });

  root.querySelector('#milkdrop-intensity')?.addEventListener('input', (e) => {
    session.settings.intensity = Number(e.target.value);
    root.querySelector('#milkdrop-intensity-out').textContent = `${session.settings.intensity.toFixed(1)}×`;
    session.graph?.setMilkdropGain?.(session.settings.intensity);
    persistSessionSettings(session);
  });

  root.querySelector('#milkdrop-show-track')?.addEventListener('click', (e) => {
    e.stopPropagation();
    const meta = session.getMeta?.() || {};
    session.overlayShown = false;
    setOverlayMeta(root, meta);
  });
}

export function updateMeta(meta) {
  if (!session?.root || !meta) return;
  const trackKey = meta.trackKey || `${meta.artist}\0${meta.title}`;
  const artworkUrl = meta.artworkUrl || '';
  if (
    session.lastTrackKey === trackKey
    && session.lastArtworkUrl === artworkUrl
    && session.overlayShown
  ) return;
  session.lastTrackKey = trackKey;
  session.lastArtworkUrl = artworkUrl;
  session.overlayShown = true;
  setOverlayMeta(session.root, meta);
}

export async function open(audio, getMeta) {
  if (session) return;
  if (isMobileVisualizer()) {
    throw new Error('Fullscreen visuals are not available on mobile.');
  }
  if (!isWebGL2Supported()) {
    throw new Error('Fullscreen visuals need WebGL 2 (try Chrome, Firefox, or Edge).');
  }

  const stripViz = audio._stripViz;
  const graph = audio._liveAudioGraph;
  if (!graph?.ensureGraph) {
    throw new Error('Audio player is not ready.');
  }
  graph.ensureGraph();
  const settings = loadSettings();
  const audioCtx = graph.audioContext;
  const milkdropTap = graph.ensureMilkdropTap();
  const analyserNode = graph.analyserNode;
  if (!audioCtx || !milkdropTap || !analyserNode) {
    throw new Error('Audio graph is not available. Press play first, then open fullscreen.');
  }
  if (audio.paused && audio.dataset.wantLive === '1') {
    await audio.play().catch(() => {});
  }
  if (audioCtx.state === 'suspended') {
    await audioCtx.resume();
  }
  graph.setMilkdropGain(settings.intensity);

  const butterchurnFftSize = 1024;
  const timeByteArray = new Uint8Array(butterchurnFftSize);
  const timeByteArrayL = new Uint8Array(butterchurnFftSize);
  const timeByteArrayR = new Uint8Array(butterchurnFftSize);
  const scratchL = new Uint8Array(butterchurnFftSize);
  const scratchR = new Uint8Array(butterchurnFftSize);

  const [butterchurn, presets] = await Promise.all([loadButterchurn(), loadPresets()]);
  const presetKey = pickPresetKey(presets, settings.presetKey);
  if (!presetKey) {
    throw new Error('No MilkDrop presets available.');
  }

  const mobile = isMobileVisualizer();
  const root = buildOverlay(mobile);
  const canvas = root.querySelector('#milkdrop-canvas');
  const freqByteArray = mobile ? new Uint8Array(analyserNode.frequencyBinCount) : null;
  const pixelRatio = mobile
    ? 1
    : Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
  const visualizer = butterchurn.createVisualizer(audioCtx, canvas, {
    width: canvas.width,
    height: canvas.height,
    pixelRatio,
    textureRatio: 1,
  });

  visualizer.connectAudio(milkdropTap);
  visualizer.loadPreset(presets[presetKey], 0);

  session = {
    root,
    canvas,
    visualizer,
    presets,
    presetKey,
    settings,
    getMeta,
    audio,
    stripViz,
    graph,
    milkdropTap,
    analyserNode,
    mobile,
    nativeFullscreen: false,
    freqByteArray,
    wavePhase: 0,
    webglRecoverTimer: null,
    savedAnalyserSmoothing: analyserNode.smoothingTimeConstant,
    timeByteArray,
    timeByteArrayL,
    timeByteArrayR,
    scratchL,
    scratchR,
    rendering: true,
    lastTrackKey: '',
    lastArtworkUrl: '',
    overlayShown: false,
    presetTimer: null,
    onResize: null,
    onKeydown: null,
    onVisibility: null,
    onFullscreenChange: null,
  };

  wireSettings(session);
  wireChromeIdleHide(session);
  wireMobileWebGLRecovery(session);
  analyserNode.smoothingTimeConstant = 0.08;
  stripViz.suspend();
  const initialMeta = getMeta?.() || {};
  session.lastTrackKey = initialMeta.trackKey || '';
  session.overlayShown = true;
  setOverlayMeta(root, initialMeta);
  restartPresetTimer(session);

  const render = () => {
    if (!session?.rendering) return;
    session.animId = requestAnimationFrame(render);
    if (session.webglContextLost) return;
    if (!document.hidden && session.analyserNode) {
      fillAudioLevels(session);
      try {
        session.visualizer.render({
          audioLevels: {
            timeByteArray: session.timeByteArray,
            timeByteArrayL: session.timeByteArrayL,
            timeByteArrayR: session.timeByteArrayR,
          },
        });
      } catch {
        /* WebGL hiccup — next frame retries */
      }
    }
  };
  render();

  session.onResize = () => resizeVisualizer(visualizer, canvas);
  window.addEventListener('resize', session.onResize);
  session.onResize();

  session.onKeydown = (e) => {
    if (!session) return;
    if (e.key === 'Escape') {
      close();
      return;
    }
    if (e.key === 's' || e.key === 'S') {
      e.preventDefault();
      toggleSettingsPanel(session);
      session.showChrome?.();
      return;
    }
    if (e.key === 'o' || e.key === 'O') {
      e.preventDefault();
      const meta = session.getMeta?.() || {};
      session.overlayShown = false;
      setOverlayMeta(session.root, meta);
      session.showChrome?.();
      return;
    }
    if (e.key === 'ArrowRight' || e.key === 'n' || e.key === 'N') {
      e.preventDefault();
      loadPresetByKey(session, nextPresetKey(session.presets, session.presetKey, 1), session.settings.blendSec);
      restartPresetTimer(session);
      return;
    }
    if (e.key === 'ArrowLeft' || e.key === 'p' || e.key === 'P') {
      e.preventDefault();
      loadPresetByKey(session, nextPresetKey(session.presets, session.presetKey, -1), session.settings.blendSec);
      restartPresetTimer(session);
    }
  };
  document.addEventListener('keydown', session.onKeydown);

  session.onVisibility = () => {
    if (!session) return;
    if (!document.hidden && audioCtx.state === 'suspended') {
      audioCtx.resume();
    }
  };
  document.addEventListener('visibilitychange', session.onVisibility);

  root.addEventListener('click', (e) => {
    if (session.mobile) return;
    if (e.target.closest('.milkdrop-overlay, .milkdrop-settings, .milkdrop-settings-toggle')) return;
    close();
  });

  if (mobile) {
    root.querySelector('#milkdrop-close-btn')?.addEventListener('click', (e) => {
      e.stopPropagation();
      close();
    });
  }

  session.onFullscreenChange = () => {
    if (session.nativeFullscreen && !document.fullscreenElement && session) {
      close();
    }
  };
  document.addEventListener('fullscreenchange', session.onFullscreenChange);

  if (!mobile) {
    try {
      await root.requestFullscreen();
      session.nativeFullscreen = true;
    } catch {
      /* fixed overlay still works without native fullscreen */
    }
  }

  document.body.classList.add('milkdrop-fullscreen-active');
  window.__milkdropActive = true;
}

export function close() {
  if (!session) return;
  const s = session;
  session = null;

  s.rendering = false;
  cancelAnimationFrame(s.animId);
  clearInterval(s.presetTimer);
  clearTimeout(s.chromeIdleTimer);
  s.chromeAbort?.abort();
  clearTimeout(s.webglRecoverTimer);
  clearTimeout(s.root._overlayTimer);

  window.removeEventListener('resize', s.onResize);
  document.removeEventListener('keydown', s.onKeydown);
  document.removeEventListener('visibilitychange', s.onVisibility);
  document.removeEventListener('fullscreenchange', s.onFullscreenChange);

  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }

  if (s.visualizer && s.milkdropTap) {
    try {
      s.visualizer.disconnectAudio(s.milkdropTap);
    } catch {
      /* ignore */
    }
  }
  if (s.analyserNode && s.savedAnalyserSmoothing != null) {
    s.analyserNode.smoothingTimeConstant = s.savedAnalyserSmoothing;
  }

  document.body.classList.remove('milkdrop-fullscreen-active');
  window.__milkdropActive = false;
  s.root.remove();

  if (!s.audio.paused && s.audio.dataset.wantLive === '1') {
    s.stripViz.resume();
  } else {
    s.stripViz.suspendIdle();
  }
}
