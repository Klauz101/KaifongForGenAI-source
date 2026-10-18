/** Sequential playback of reply sentences. Uses server audio when present, else the browser's speechSynthesis. */

export interface Speakable {
  text: string;
  lang: "yue" | "en";
  audio_b64?: string | null;
  mime?: string;
}

const BCP47 = { yue: "zh-HK", en: "en-US" } as const;

function speakWithBrowser(item: Speakable): Promise<void> {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window) || !item.text) return resolve();
    const u = new SpeechSynthesisUtterance(item.text);
    u.lang = BCP47[item.lang];
    u.rate = 0.9; // a little slower for older listeners
    const voice = window.speechSynthesis.getVoices().find((v) => v.lang.replace("_", "-") === u.lang);
    if (voice) u.voice = voice;
    // onend is unreliable in some browsers: never let the queue hang.
    const guard = window.setTimeout(resolve, 2000 + item.text.length * 350);
    u.onend = u.onerror = () => {
      window.clearTimeout(guard);
      resolve();
    };
    window.speechSynthesis.speak(u);
  });
}

function playAudio(item: Speakable, setCurrent: (a: HTMLAudioElement | null) => void): Promise<void> {
  return new Promise((resolve) => {
    const audio = new Audio(`data:${item.mime ?? "audio/mpeg"};base64,${item.audio_b64}`);
    setCurrent(audio);
    audio.onended = audio.onerror = audio.onpause = () => {
      setCurrent(null);
      resolve();
    };
    audio.play().catch(() => {
      setCurrent(null);
      resolve();
    });
  });
}

export class AudioQueue {
  private queue: Speakable[] = [];
  private running = false;
  private current: HTMLAudioElement | null = null;
  private generation = 0;

  enqueue(item: Speakable): void {
    this.queue.push(item);
    void this.run();
  }

  private async run(): Promise<void> {
    if (this.running) return;
    this.running = true;
    const gen = this.generation;
    while (this.queue.length && gen === this.generation) {
      const item = this.queue.shift()!;
      if (item.audio_b64) await playAudio(item, (a) => (this.current = a));
      else await speakWithBrowser(item);
    }
    if (gen === this.generation) this.running = false;
  }

  stop(): void {
    this.generation += 1;
    this.queue = [];
    this.current?.pause();
    this.current = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    this.running = false;
  }
}
