/** Push-to-talk recording with MediaRecorder (webm/opus). Raw audio only leaves the browser as base64 over the call socket. */

export class PushToTalk {
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private startedAt = 0;

  static supported(): boolean {
    return typeof MediaRecorder !== "undefined" && !!navigator.mediaDevices?.getUserMedia;
  }

  /** Ask for the microphone (call from a user gesture). Throws if denied. */
  async prepare(): Promise<void> {
    if (!this.stream) this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  }

  start(): void {
    if (!this.stream || this.recorder) return;
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : undefined;
    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
    this.recorder.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.startedAt = Date.now();
    this.recorder.start();
  }

  /** Stop and return base64 audio, or null if the press was too short to be speech. */
  stop(): Promise<{ data: string; mime: string } | null> {
    const rec = this.recorder;
    if (!rec) return Promise.resolve(null);
    this.recorder = null;
    const tooShort = Date.now() - this.startedAt < 300;
    return new Promise((resolve) => {
      rec.onstop = async () => {
        if (tooShort || !this.chunks.length) return resolve(null);
        const blob = new Blob(this.chunks, { type: rec.mimeType || "audio/webm" });
        const bytes = new Uint8Array(await blob.arrayBuffer());
        let bin = "";
        for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        resolve({ data: btoa(bin), mime: blob.type });
      };
      rec.stop();
    });
  }

  release(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }
}
