/**
 * Snappy FM tuning bed between station switches.
 *
 * Disable: localStorage.setItem('alchemyfm-tuning-fx', '0') then reload.
 * Re-enable: localStorage.removeItem('alchemyfm-tuning-fx')
 */
const LiveTuningFx = {
  MIN_MS: 120,
  MAX_MS: 800,
  _cachedEnabled: null,

  isEnabled() {
    if (this._cachedEnabled === null) {
      try {
        this._cachedEnabled = localStorage.getItem('alchemyfm-tuning-fx') !== '0';
      } catch {
        this._cachedEnabled = true;
      }
    }
    return this._cachedEnabled;
  },

  disable() {
    try {
      localStorage.setItem('alchemyfm-tuning-fx', '0');
    } catch {}
    this._cachedEnabled = false;
  },

  enable() {
    try {
      localStorage.removeItem('alchemyfm-tuning-fx');
    } catch {}
    this._cachedEnabled = true;
  },

  shouldPlaySwitchFx(audio, fromSlug, toSlug) {
    if (!this.isEnabled() || !audio || !fromSlug || !toSlug) return false;
    if (fromSlug === toSlug) return false;
    return audio.dataset.wantLive === '1';
  },

  playMobileStaticBurst() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;

    const ctx = new Ctx();
    const sampleRate = ctx.sampleRate;
    const length = Math.floor(sampleRate * 0.45);
    const buffer = ctx.createBuffer(1, length, sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) {
      data[i] = (Math.random() * 2 - 1) * (0.35 + Math.random() * 0.25);
    }

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const gain = ctx.createGain();
    gain.gain.value = 0.22;
    const filter = ctx.createBiquadFilter();
    filter.type = 'bandpass';
    filter.frequency.value = 900 + Math.random() * 1800;
    filter.Q.value = 1.4;
    source.connect(filter);
    filter.connect(gain);
    gain.connect(ctx.destination);
    source.start();
    source.stop(ctx.currentTime + 0.45);
    window.setTimeout(() => {
      ctx.close?.();
    }, 600);
    return ctx;
  },

  startMobileSwitchFx(audio) {
    const started = Date.now();
    const prevVol = audio.volume;
    let settled = false;

    return new Promise((resolve) => {
      let maxTimer;

      const finish = () => {
        if (settled) return;
        settled = true;
        audio.removeEventListener('playing', onPlaying);
        clearTimeout(maxTimer);
        audio.volume = prevVol;
        resolve();
      };

      const onPlaying = () => {
        const elapsed = Date.now() - started;
        if (elapsed >= this.MIN_MS) {
          finish();
        } else {
          setTimeout(finish, this.MIN_MS - elapsed);
        }
      };

      try {
        this.playMobileStaticBurst();
      } catch {
        /* optional bed */
      }
      audio.volume = Math.max(0.04, prevVol * 0.18);
      audio.addEventListener('playing', onPlaying);
      maxTimer = setTimeout(finish, this.MAX_MS);
    });
  },

  /** Returns a promise that resolves when the tuning bed has finished. */
  startSwitch(audio, { fromSlug, toSlug } = {}) {
    if (!this.shouldPlaySwitchFx(audio, fromSlug, toSlug)) {
      return Promise.resolve();
    }

    if (typeof RadioApp !== 'undefined' && !RadioApp.useStripWebAudio()) {
      return this.startMobileSwitchFx(audio);
    }

    if (!audio._liveAudioGraph && typeof LiveAudioGraph !== 'undefined') {
      audio._liveAudioGraph = LiveAudioGraph.attach(audio);
    }

    const graph = audio._liveAudioGraph;
    graph?.ensureGraph?.();
    if (graph?.audioContext?.state === 'suspended') {
      graph.audioContext.resume();
    }
    graph?.beginTuning?.();

    const started = Date.now();
    let settled = false;

    return new Promise((resolve) => {
      let maxTimer;

      const finish = () => {
        if (settled) return;
        settled = true;
        audio.removeEventListener('playing', onPlaying);
        clearTimeout(maxTimer);
        graph?.finishTuning?.();
        resolve();
      };

      const onPlaying = () => {
        const elapsed = Date.now() - started;
        if (elapsed >= this.MIN_MS) {
          finish();
        } else {
          setTimeout(finish, this.MIN_MS - elapsed);
        }
      };

      audio.addEventListener('playing', onPlaying);
      maxTimer = setTimeout(finish, this.MAX_MS);
    });
  },
};
