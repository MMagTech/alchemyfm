/**
 * Small mirrored spectrum strip under the station player (2D canvas).
 * Independent from fullscreen MilkDrop / Butterchurn.
 */
const StripVisualizer = {
  create(graph, canvas) {
    const ctx = canvas.getContext('2d');
    let animId = null;
    let bassAvg = 0;
    let beat = 0;
    let phase = 0;
    let stripLive = false;
    let stripSuspended = false;
    const halfBars = 28;
    let data = new Uint8Array(0);

    const accentRgb = () => {
      const raw = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
      if (raw.startsWith('#')) {
        const hex = raw.slice(1);
        const full = hex.length === 3
          ? hex.split('').map((c) => c + c).join('')
          : hex;
        const n = Number.parseInt(full, 16);
        if (!Number.isNaN(n)) {
          return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
        }
      }
      return { r: 139, g: 92, b: 246 };
    };

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const updateBeat = () => {
      const analyser = graph.analyserNode;
      if (!analyser) return;
      let bass = 0;
      for (let i = 1; i < 12; i++) bass += data[i];
      bass /= 11;
      const threshold = bassAvg * 1.06 + 6;
      if (bass > threshold && bass > 40) beat = 1;
      bassAvg = bassAvg * 0.85 + bass * 0.15;
      beat *= 0.8;
    };

    const drawMirrored = (live) => {
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (w < 1 || h < 1) return;
      const cx = w / 2;
      const { r, g, b } = accentRgb();
      ctx.clearRect(0, 0, w, h);

      if (!live) {
        phase += 0.04;
        const barW = (w / 2) / halfBars;
        for (let i = 0; i < halfBars; i++) {
          const wave = (Math.sin(phase + i * 0.28) + 1) * 0.5;
          const barH = 3 + wave * h * 0.14;
          ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${0.14 + wave * 0.22})`;
          ctx.fillRect(cx + i * barW + 1, h - barH, barW - 2, barH);
          ctx.fillRect(cx - (i + 1) * barW + 1, h - barH, barW - 2, barH);
        }
        return;
      }

      const step = Math.max(1, Math.floor(data.length / halfBars));
      const barW = (w / 2) / halfBars;
      for (let i = 0; i < halfBars; i++) {
        let sum = 0;
        for (let j = 0; j < step; j++) sum += data[i * step + j];
        const v = Math.pow(sum / step / 255, 0.82);
        const barH = Math.max(3, v * h * (0.62 + beat * 0.28));
        const alpha = 0.4 + v * 0.55;
        const mix = 0.55 + v * 0.35 + beat * 0.1;
        ctx.fillStyle = `rgba(${Math.round(r * mix)}, ${Math.round(g * mix)}, ${Math.round(b * mix)}, ${alpha})`;
        ctx.fillRect(cx + i * barW + 1, h - barH, barW - 2, barH);
        ctx.fillRect(cx - (i + 1) * barW + 1, h - barH, barW - 2, barH);
      }

      if (beat > 0.06) {
        ctx.fillStyle = `rgba(255, 72, 72, ${beat * 0.28})`;
        ctx.fillRect(cx - 24 - beat * 40, h - 2, 48 + beat * 80, 2);
      }
    };

    const drawLive = () => {
      const analyser = graph.analyserNode;
      if (!analyser) {
        drawMirrored(false);
        animId = requestAnimationFrame(drawLive);
        return;
      }
      if (!data.length) data = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(data);
      updateBeat();
      drawMirrored(true);
      animId = requestAnimationFrame(drawLive);
    };

    const drawIdle = () => {
      drawMirrored(false);
      animId = requestAnimationFrame(drawIdle);
    };

    resize();
    const onResize = () => resize();
    window.addEventListener('resize', onResize);
    let ro = null;
    if (typeof ResizeObserver !== 'undefined' && canvas.parentElement) {
      ro = new ResizeObserver(() => resize());
      ro.observe(canvas.parentElement);
    }

    const api = {
      suspend() {
        stripSuspended = true;
        cancelAnimationFrame(animId);
        animId = null;
      },
      resume() {
        stripSuspended = false;
        graph.ensureGraph();
        if (graph.audioContext?.state === 'suspended') graph.audioContext.resume();
        cancelAnimationFrame(animId);
        if (stripLive) {
          animId = requestAnimationFrame(drawLive);
        } else {
          animId = requestAnimationFrame(drawIdle);
        }
      },
      start() {
        graph.ensureGraph();
        if (graph.audioContext?.state === 'suspended') graph.audioContext.resume();
        if (graph.analyserNode) {
          data = new Uint8Array(graph.analyserNode.frequencyBinCount);
        }
        stripLive = true;
        if (stripSuspended) return;
        cancelAnimationFrame(animId);
        animId = requestAnimationFrame(drawLive);
      },
      stop() {
        stripLive = false;
        if (stripSuspended) return;
        cancelAnimationFrame(animId);
        beat = 0;
        animId = requestAnimationFrame(drawIdle);
      },
      destroy() {
        stripSuspended = true;
        stripLive = false;
        cancelAnimationFrame(animId);
        animId = null;
        window.removeEventListener('resize', onResize);
        ro?.disconnect();
      },
    };

    api.stop();
    return api;
  },
};
