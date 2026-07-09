/**
 * Web Audio graph for the live station player.
 * Shared by the strip visualizer and optional fullscreen MilkDrop (separate module).
 */
const LiveAudioGraph = {
  attach(audio) {
    let audioCtx = null;
    let vizBus = null;
    let analyser = null;
    let gain = null;
    let streamFadeGain = null;
    let staticGain = null;
    let staticMixGain = null;
    let staticHighpass = null;
    let staticFilter = null;
    let staticClip = null;
    let staticSource = null;
    let staticHissSource = null;
    let noiseBuffer = null;
    let hissBuffer = null;
    let tuningActive = false;
    let tuningExtras = null;
    let milkdropTap = null;
    let milkdropSink = null;

    const makeClipCurve = (drive = 4) => {
      const samples = 256;
      const curve = new Float32Array(samples);
      const k = Math.max(1, drive);
      for (let i = 0; i < samples; i += 1) {
        const x = (i * 2) / (samples - 1) - 1;
        curve[i] = Math.tanh(k * x) / Math.tanh(k);
      }
      return curve;
    };

    const createGrittyStaticBuffer = (ctx, durationSec = 1.4) => {
      const sampleRate = ctx.sampleRate;
      const length = Math.floor(sampleRate * durationSec);
      const buffer = ctx.createBuffer(1, length, sampleRate);
      const data = buffer.getChannelData(0);
      let b0 = 0;
      let b1 = 0;
      let b2 = 0;
      let burstLeft = 0;
      let burstGain = 1;

      for (let i = 0; i < length; i += 1) {
        if (burstLeft <= 0 && Math.random() < 0.0018) {
          burstLeft = Math.floor(sampleRate * (0.03 + Math.random() * 0.07));
          burstGain = 1.2 + Math.random() * 0.9;
        }
        if (burstLeft > 0) {
          burstLeft -= 1;
        } else {
          burstGain += (1 - burstGain) * 0.08;
        }

        const white = Math.random() * 2 - 1;
        b0 = 0.99886 * b0 + white * 0.0555179;
        b1 = 0.99332 * b1 + white * 0.0750759;
        b2 = 0.969 * b2 + white * 0.153852;
        let sample = (b0 + b1 + b2) * 0.11 + white * 0.07;

        if (Math.random() < 0.0012) {
          const popLen = 18 + Math.floor(Math.random() * 55);
          const popAmp = 0.25 + Math.random() * 0.55;
          for (let j = 0; j < popLen && i + j < length; j += 1) {
            const env = 1 - j / popLen;
            data[i + j] += (Math.random() * 2 - 1) * popAmp * env * env;
          }
        }

        sample *= burstGain;
        sample += (Math.random() * 2 - 1) * 0.018;
        data[i] = Math.max(-1, Math.min(1, sample));
      }
      return buffer;
    };

    const createHissBuffer = (ctx, durationSec = 0.35) => {
      const sampleRate = ctx.sampleRate;
      const length = Math.floor(sampleRate * durationSec);
      const buffer = ctx.createBuffer(1, length, sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < length; i += 1) {
        const white = Math.random() * 2 - 1;
        data[i] = white * (0.06 + Math.random() * 0.05);
      }
      return buffer;
    };

    const ensureStaticNodes = () => {
      if (!audioCtx || staticGain) return;

      staticMixGain = audioCtx.createGain();
      staticMixGain.gain.value = 1;
      staticHighpass = audioCtx.createBiquadFilter();
      staticHighpass.type = 'highpass';
      staticHighpass.frequency.value = 220;
      staticHighpass.Q.value = 0.7;
      staticFilter = audioCtx.createBiquadFilter();
      staticFilter.type = 'bandpass';
      staticFilter.Q.value = 1.65;
      staticFilter.frequency.value = 1200;
      staticClip = audioCtx.createWaveShaper();
      staticClip.curve = makeClipCurve(4.2);
      staticClip.oversample = '2x';
      staticGain = audioCtx.createGain();
      staticGain.gain.value = 0;
      noiseBuffer = createGrittyStaticBuffer(audioCtx);
      hissBuffer = createHissBuffer(audioCtx);

      staticMixGain.connect(staticHighpass);
      staticHighpass.connect(staticFilter);
      staticFilter.connect(staticClip);
      staticClip.connect(staticGain);
      staticGain.connect(gain);
    };

    const stopTuningExtras = () => {
      tuningExtras?.forEach((node) => {
        try {
          node.stop?.();
        } catch {
          /* already stopped */
        }
        node.disconnect?.();
      });
      tuningExtras = null;
    };

    const stopStaticSource = () => {
      stopTuningExtras();
      if (staticSource) {
        try {
          staticSource.stop();
        } catch {
          /* already stopped */
        }
        staticSource.disconnect();
        staticSource = null;
      }
      if (staticHissSource) {
        try {
          staticHissSource.stop();
        } catch {
          /* already stopped */
        }
        staticHissSource.disconnect();
        staticHissSource = null;
      }
    };

    const ensureGraph = () => {
      if (audioCtx) return;
      if (typeof RadioApp !== 'undefined' && !RadioApp.useStripWebAudio()) {
        return;
      }
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      audioCtx = new Ctx();
      const source = audioCtx.createMediaElementSource(audio);
      vizBus = audioCtx.createGain();
      vizBus.gain.value = 1;
      analyser = audioCtx.createAnalyser();
      gain = audioCtx.createGain();
      streamFadeGain = audioCtx.createGain();
      streamFadeGain.gain.value = 1;
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.55;
      source.connect(vizBus);
      vizBus.connect(analyser);
      analyser.connect(streamFadeGain);
      streamFadeGain.connect(gain);
      gain.connect(audioCtx.destination);
      ensureStaticNodes();
      const volEl = document.getElementById('live-volume');
      let vol = volEl?.value;
      if (vol == null) {
        try {
          vol = localStorage.getItem('radio-volume');
        } catch {
          /* ignore */
        }
      }
      gain.gain.value = parseFloat(vol ?? 1);
    };

    const readUserVolume = () => {
      const volEl = document.getElementById('live-volume') ||
        document.getElementById('live-mini-volume');
      let vol = volEl?.value;
      if (vol == null) {
        try {
          vol = localStorage.getItem('radio-volume');
        } catch {
          /* ignore */
        }
      }
      return parseFloat(vol ?? 1);
    };

    const syncMasterVolume = () => {
      if (!gain) return;
      gain.gain.value = readUserVolume();
    };

    const beginTuning = () => {
      ensureGraph();
      if (!audioCtx || !staticGain || !streamFadeGain || !staticFilter) return;

      syncMasterVolume();

      stopStaticSource();
      tuningActive = true;
      tuningExtras = [];

      staticSource = audioCtx.createBufferSource();
      staticSource.buffer = noiseBuffer;
      staticSource.loop = true;
      staticSource.connect(staticMixGain);
      staticSource.start();

      staticHissSource = audioCtx.createBufferSource();
      staticHissSource.buffer = hissBuffer;
      staticHissSource.loop = true;
      staticHissSource.playbackRate.value = 0.92 + Math.random() * 0.18;
      staticHissSource.connect(staticMixGain);
      staticHissSource.start();

      const t = audioCtx.currentTime;
      const duckSec = 0.07;
      const riseSec = 0.1;
      // Slightly below stream level so the switch feels natural, not louder.
      const staticLevel = 0.78;

      streamFadeGain.gain.cancelScheduledValues(t);
      streamFadeGain.gain.setValueAtTime(streamFadeGain.gain.value, t);
      streamFadeGain.gain.linearRampToValueAtTime(0.32, t + duckSec);

      staticGain.gain.cancelScheduledValues(t);
      staticGain.gain.setValueAtTime(0, t);
      staticGain.gain.linearRampToValueAtTime(staticLevel, t + riseSec);

      staticMixGain.gain.cancelScheduledValues(t);
      staticMixGain.gain.setValueAtTime(1, t);

      staticFilter.frequency.cancelScheduledValues(t);
      staticFilter.frequency.setValueAtTime(420, t);
      staticFilter.frequency.exponentialRampToValueAtTime(3400, t + 0.1);
      staticFilter.frequency.exponentialRampToValueAtTime(980, t + 0.28);

      staticFilter.Q.cancelScheduledValues(t);
      staticFilter.Q.setValueAtTime(1.4, t);
      staticFilter.Q.linearRampToValueAtTime(2.4, t + 0.12);
      staticFilter.Q.linearRampToValueAtTime(1.1, t + 0.3);

      const flutter = audioCtx.createOscillator();
      flutter.type = 'square';
      flutter.frequency.value = 4.5 + Math.random() * 5;
      const flutterDepth = audioCtx.createGain();
      flutterDepth.gain.value = 0.2;
      flutter.connect(flutterDepth);
      flutterDepth.connect(staticMixGain.gain);
      flutter.start(t);
      tuningExtras.push(flutter, flutterDepth);

      for (let i = 0; i < 4; i += 1) {
        const at = t + 0.04 + Math.random() * 0.22;
        staticMixGain.gain.setValueAtTime(1, at);
        staticMixGain.gain.linearRampToValueAtTime(1.1 + Math.random() * 0.12, at + 0.008);
        staticMixGain.gain.linearRampToValueAtTime(0.86 + Math.random() * 0.1, at + 0.035);
      }
    };

    const finishTuning = () => {
      if (!audioCtx || !staticGain || !streamFadeGain) {
        tuningActive = false;
        stopStaticSource();
        return;
      }

      tuningActive = false;
      const t = audioCtx.currentTime;
      const fadeSec = 0.08;

      staticGain.gain.cancelScheduledValues(t);
      staticGain.gain.setValueAtTime(staticGain.gain.value, t);
      staticGain.gain.linearRampToValueAtTime(0, t + fadeSec);

      streamFadeGain.gain.cancelScheduledValues(t);
      streamFadeGain.gain.setValueAtTime(streamFadeGain.gain.value, t);
      streamFadeGain.gain.linearRampToValueAtTime(1, t + fadeSec + 0.02);

      window.setTimeout(stopStaticSource, Math.ceil((fadeSec + 0.05) * 1000));
    };

    const ensureMilkdropTap = () => {
      ensureGraph();
      if (!audioCtx || !vizBus) return null;
      if (milkdropTap) return milkdropTap;
      milkdropTap = audioCtx.createGain();
      milkdropTap.gain.value = 3;
      milkdropSink = audioCtx.createGain();
      milkdropSink.gain.value = 0;
      vizBus.connect(milkdropTap);
      milkdropTap.connect(milkdropSink);
      milkdropSink.connect(audioCtx.destination);
      return milkdropTap;
    };

    const setMilkdropGain = (value) => {
      if (milkdropTap) {
        milkdropTap.gain.value = Math.max(0.5, Number(value) || 3);
      }
    };

    return {
      ensureGraph,
      ensureMilkdropTap,
      setMilkdropGain,
      beginTuning,
      finishTuning,
      get isTuning() { return tuningActive; },
      get audioContext() { return audioCtx; },
      get analyserNode() { return analyser; },
      get gain() { return gain; },
    };
  },
};
