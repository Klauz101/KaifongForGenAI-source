/** A short two-tone alert beep (Web Audio, no asset). Browsers may block it until the user has interacted. */

let ctx: AudioContext | null = null;

export function soundEnabled(): boolean {
  try {
    return localStorage.getItem("kf_sound") !== "off";
  } catch {
    return true;
  }
}

export function setSoundEnabled(on: boolean): void {
  try {
    localStorage.setItem("kf_sound", on ? "on" : "off");
  } catch {
    /* storage unavailable */
  }
}

export function playAlertTone(): void {
  if (!soundEnabled()) return;
  try {
    ctx = ctx ?? new AudioContext();
    void ctx.resume();
    [0, 0.35].forEach((offset, i) => {
      const osc = ctx!.createOscillator();
      const gain = ctx!.createGain();
      osc.frequency.value = i === 0 ? 880 : 660;
      gain.gain.setValueAtTime(0.0001, ctx!.currentTime + offset);
      gain.gain.exponentialRampToValueAtTime(0.25, ctx!.currentTime + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx!.currentTime + offset + 0.3);
      osc.connect(gain).connect(ctx!.destination);
      osc.start(ctx!.currentTime + offset);
      osc.stop(ctx!.currentTime + offset + 0.32);
    });
  } catch {
    /* audio blocked or unsupported: the visual toast still shows */
  }
}
