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

  _bufferToWav(buffer) {
    const channelData = buffer.getChannelData(0);
    const dataLength = channelData.length * 2;
    const arrayBuffer = new ArrayBuffer(44 + dataLength);
    const view = new DataView(arrayBuffer);

    const writeString = (offset, str) => {
      for (let i = 0; i < str.length; i += 1) {
        view.setUint8(offset + i, str.charCodeAt(i));
      }
    };

    writeString(0, 'RIFF');
    view.setUint32(4, 36 + dataLength, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, buffer.sampleRate, true);
    view.setUint32(28, buffer.sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, 'data');
    view.setUint32(40, dataLength, true);

    let offset = 44;
    for (let i = 0; i < channelData.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, channelData[i]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }

    return new Blob([arrayBuffer], { type: 'audio/wav' });
  },

  _ensureMobileStaticAudio() {
    let el = document.getElementById('alchemy-tuning-static');
    if (!el) {
      el = document.createElement('audio');
      el.id = 'alchemy-tuning-static';
      el.preload = 'auto';
      el.setAttribute('playsinline', '');
      el.hidden = true;
      document.body.appendChild(el);
    }
    return el;
  },

  async playMobileStaticBurst() {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;

    try {
      if (navigator.audioSession) {
        navigator.audioSession.type = 'playback';
      }
    } catch {
      /* ignore */
    }

    const sampleRate = 44100;
    const durationSec = 0.45;
    const length = Math.floor(sampleRate * durationSec);
    const offline = new OfflineAudioContext(1, length, sampleRate);

    const buffer = offline.createBuffer(1, length, sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) {
      data[i] = (Math.random() * 2 - 1) * (0.35 + Math.random() * 0.25);
    }

    const source = offline.createBufferSource();
    source.buffer = buffer;
    const filter = offline.createBiquadFilter();
    filter.type = 'bandpass';
    filter.frequency.value = 900 + Math.random() * 1800;
    filter.Q.value = 1.4;
    const gain = offline.createGain();
    gain.gain.value = 0.22;
    source.connect(filter);
    filter.connect(gain);
    gain.connect(offline.destination);
    source.start();

    const rendered = await offline.startRendering();
    const blob = this._bufferToWav(rendered);
    const url = URL.createObjectURL(blob);

    const el = this._ensureMobileStaticAudio();
    if (el._revokeUrl) {
      URL.revokeObjectURL(el._revokeUrl);
      el._revokeUrl = null;
    }
    el._revokeUrl = url;
    el.src = url;

    const cleanup = () => {
      if (el._revokeUrl === url) {
        URL.revokeObjectURL(url);
        el._revokeUrl = null;
      }
    };
    el.addEventListener('ended', cleanup, { once: true });

    try {
      await el.play();
      return el;
    } catch {
      cleanup();
      return null;
    }
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
        void this.playMobileStaticBurst().catch(() => {});
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
