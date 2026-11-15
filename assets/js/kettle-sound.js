/* synthesised kettle: hiss, bubbles, a low rumble at the boil, and a whistle whose pitch rises with temperature */
function createKettleSound(ctx, out) {
  const smooth = (a, b, x) => { const t = Math.min(Math.max((x - a) / (b - a), 0), 1); return t * t * (3 - 2 * t); };
  const buffer = (seconds, fill) => {
    const b = ctx.createBuffer(1, Math.round(ctx.sampleRate * seconds), ctx.sampleRate);
    fill(b.getChannelData(0), ctx.sampleRate);
    return b;
  };
  const loop = (b, offset = 0) => { const s = ctx.createBufferSource(); s.buffer = b; s.loop = true; s.start(ctx.currentTime, offset); return s; };
  const gain = (value = 0) => { const g = ctx.createGain(); g.gain.value = value; return g; };
  const filter = (type, frequency, q = 1) => { const f = ctx.createBiquadFilter(); f.type = type; f.frequency.value = frequency; f.Q.value = q; return f; };

  const white = buffer(3, (d) => { for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1; });
  // sparse decaying bursts: collapsing bubbles
  const crackles = buffer(3, (d, rate) => {
    for (let n = 0; n < 900; n++) {
      const at = Math.floor(Math.random() * d.length), len = Math.floor(rate * (0.001 + Math.random() * 0.005)), amp = 0.3 + Math.random() * 0.7;
      for (let i = 0; i < len && at + i < d.length; i++) d[at + i] += (Math.random() * 2 - 1) * amp * Math.exp(-6 * i / len);
    }
  });

  const master = gain(0); master.connect(out);
  const sources = [];

  const hissBand = filter("bandpass", 400, 0.9), hissGain = gain();
  sources.push(loop(white)); sources[0].connect(hissBand).connect(hissGain).connect(master);

  const crackleSrc = loop(crackles), crackleHp = filter("highpass", 1200, 0.7), crackleGain = gain();
  sources.push(crackleSrc); crackleSrc.connect(crackleHp).connect(crackleGain).connect(master);

  const rumbleLp = filter("lowpass", 260, 1.5), rumbleGain = gain(), rumbleDepth = gain();
  sources.push(loop(white, 1.3)); sources[2].connect(rumbleLp).connect(rumbleGain).connect(master);
  [4.7, 7.3].forEach((f) => { const lfo = ctx.createOscillator(); lfo.frequency.value = f; lfo.connect(rumbleDepth); lfo.start(); sources.push(lfo); });
  rumbleDepth.connect(rumbleGain.gain);

  const whistle = ctx.createOscillator(), overtone = ctx.createOscillator(), overtoneGain = gain(0.12);
  const breath = filter("bandpass", 900, 28), breathGain = gain(), whistleGain = gain();
  const vibrato = ctx.createOscillator(), vibratoDepth = gain(); vibrato.frequency.value = 5.6;
  vibrato.connect(vibratoDepth); vibratoDepth.connect(whistle.frequency); vibratoDepth.connect(breath.frequency);
  whistle.connect(whistleGain); overtone.connect(overtoneGain).connect(whistleGain);
  sources.push(loop(white, 2.1)); sources[sources.length - 1].connect(breath).connect(breathGain).connect(whistleGain);
  whistleGain.connect(master);
  [whistle, overtone, vibrato].forEach((o) => { o.start(); sources.push(o); });

  return {
    set(celsius, at = ctx.currentTime) {
      const heat = Math.min(Math.max((celsius - 20) / 80, 0), 1);
      const ease = (p, v, k = 0.08) => p.setTargetAtTime(v, at, k);
      ease(master.gain, 0.9);
      ease(hissBand.frequency, 350 * 2 ** (heat * 3));
      ease(hissGain.gain, 0.015 + 0.2 * smooth(0.15, 0.8, heat) * (1 - 0.4 * smooth(0.9, 1, heat)));
      ease(crackleGain.gain, 0.22 * smooth(0.3, 0.85, heat) * (1 - 0.5 * smooth(0.92, 1, heat)));
      crackleSrc.playbackRate.setTargetAtTime(0.6 + heat * 1.2, at, 0.1);
      const boil = smooth(0.85, 1, heat);
      ease(rumbleGain.gain, 0.25 * boil); ease(rumbleDepth.gain, 0.2 * boil);
      const rise = smooth(0.35, 1, heat), pitch = 900 * 2 ** (rise * 1.15);
      ease(whistle.frequency, pitch, 0.05); ease(overtone.frequency, pitch * 2, 0.05); ease(breath.frequency, pitch, 0.05);
      ease(vibratoDepth.gain, pitch * 0.006);
      ease(whistleGain.gain, 0.08 * rise ** 1.6);
      ease(breathGain.gain, 0.9);
    },
    stop(at = ctx.currentTime) {
      master.gain.setTargetAtTime(0, at, 0.08);
      sources.forEach((s) => { try { s.stop(at + 0.5); } catch {} });
    },
  };
}
