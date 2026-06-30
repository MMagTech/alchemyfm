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
    let milkdropTap = null;
    let milkdropSink = null;

    const ensureGraph = () => {
      if (audioCtx) return;
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      audioCtx = new Ctx();
      const source = audioCtx.createMediaElementSource(audio);
      vizBus = audioCtx.createGain();
      vizBus.gain.value = 1;
      analyser = audioCtx.createAnalyser();
      gain = audioCtx.createGain();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.55;
      source.connect(vizBus);
      vizBus.connect(analyser);
      analyser.connect(gain);
      gain.connect(audioCtx.destination);
      const volEl = document.getElementById('live-volume');
      gain.gain.value = parseFloat(volEl?.value || 1);
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
      get audioContext() { return audioCtx; },
      get analyserNode() { return analyser; },
      get gain() { return gain; },
    };
  },
};
