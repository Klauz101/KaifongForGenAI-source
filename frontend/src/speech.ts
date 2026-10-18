/** The browser's own speech recognition (Chrome/Edge/Safari): a keyless way to transcribe Cantonese.
 *  Used when the server has no real speech-to-text. Audio goes to the browser vendor's service, not to Kaifong. */

type Rec = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string; confidence: number }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
};

const CONSTRUCTOR = (): (new () => Rec) | null => {
  const w = window as unknown as { SpeechRecognition?: new () => Rec; webkitSpeechRecognition?: new () => Rec };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

export function speechRecognitionSupported(): boolean {
  return CONSTRUCTOR() !== null;
}

// Cantonese (Hong Kong) tag first; zh-HK as the fallback some browsers prefer.
const LANGS = { yue: ["yue-Hant-HK", "zh-HK"], en: ["en-US"] } as const;

export interface Heard {
  text: string;
  confidence: number | null;
}

export class BrowserListener {
  private rec: Rec | null = null;
  private finals = "";
  private interim = "";
  private confs: number[] = [];
  private done: ((h: Heard | null) => void) | null = null;
  private attempt = 0;
  private language: "yue" | "en" = "yue";
  private onInterim: (t: string) => void = () => undefined;
  private onProblem: (m: string) => void = () => undefined;

  start(language: "yue" | "en", onInterim: (t: string) => void, onProblem: (m: string) => void): void {
    const Ctor = CONSTRUCTOR();
    if (!Ctor) return onProblem("This browser has no speech recognition.");
    this.language = language;
    this.attempt = 0;
    this.finals = this.interim = "";
    this.confs = [];
    this.onInterim = onInterim;
    this.onProblem = onProblem;
    this.begin(Ctor);
  }

  private begin(Ctor: new () => Rec): void {
    const rec = new Ctor();
    this.rec = rec;
    rec.lang = LANGS[this.language][this.attempt];
    rec.interimResults = true;
    rec.continuous = true;
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        const alt = r[0];
        if (r.isFinal) {
          this.finals += alt.transcript;
          this.confs.push(alt.confidence);
        } else interim += alt.transcript;
      }
      this.interim = interim;
      this.onInterim(this.finals + interim);
    };
    rec.onerror = (e) => {
      const next = this.attempt + 1;
      if (e.error === "language-not-supported" && next < LANGS[this.language].length) {
        this.attempt = next; // e.g. fall back from yue-Hant-HK to zh-HK
        this.begin(Ctor);
        return;
      }
      if (e.error !== "no-speech" && e.error !== "aborted") this.onProblem(`Speech recognition problem: ${e.error}`);
    };
    rec.onend = () => {
      const text = (this.finals + this.interim).trim();
      const known = this.confs.filter((c) => c > 0);
      const confidence = known.length ? known.reduce((a, b) => a + b, 0) / known.length : null;
      this.done?.(text ? { text, confidence } : null);
      this.done = null;
    };
    try {
      rec.start();
    } catch {
      this.onProblem("Could not start speech recognition (is the microphone allowed?).");
    }
  }

  /** Stop listening and resolve with what was heard (null if nothing). */
  stop(): Promise<Heard | null> {
    return new Promise((resolve) => {
      if (!this.rec) return resolve(null);
      this.done = resolve;
      const guard = window.setTimeout(() => {
        this.done?.(null);
        this.done = null;
      }, 4000); // never hang if the browser never fires onend
      const prev = this.done;
      this.done = (h) => {
        window.clearTimeout(guard);
        prev?.(h);
      };
      try {
        this.rec.stop();
      } catch {
        this.done?.(null);
        this.done = null;
      }
    });
  }

  release(): void {
    try {
      this.rec?.abort();
    } catch {
      /* already stopped */
    }
    this.rec = null;
  }
}
