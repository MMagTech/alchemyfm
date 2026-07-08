/**
 * Snappy FM tuning bed between station switches.
 *
 * Disable: localStorage.setItem('alchemyfm-tuning-fx', '0') then reload.
 * Re-enable: localStorage.removeItem('alchemyfm-tuning-fx')
 */
const LiveTuningFx = {
  MIN_MS: 280,
  MAX_MS: 1200,
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

  /** Returns a promise that resolves when the tuning bed has finished. */
  startSwitch(audio, { fromSlug, toSlug } = {}) {
    if (!this.shouldPlaySwitchFx(audio, fromSlug, toSlug)) {
      return Promise.resolve();
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
