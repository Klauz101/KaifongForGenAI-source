import { Mic, Phone, PhoneOff, Send } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { CallEvent, ElderDetail } from "../api/types";
import { AudioQueue } from "../audio";
import { JyutpingPreview, looksLikeJyutping, type JyutpingResult } from "../components/JyutpingPreview";
import { PushToTalk } from "../recorder";
import { BrowserListener, speechRecognitionSupported } from "../speech";
import { useI18n } from "../i18n";

type Phase = "ringing" | "connecting" | "in_call" | "ended";
type Lang = "yue" | "en";

interface Caption {
  key: number;
  speaker: "elder" | "kaifong";
  index: number | null;
  text: string;
  text_en: string | null;
  raw?: string | null; // the Jyutping that was typed, when the text is its conversion
}

/** The older person's "phone": incoming call, hold-to-talk, type-instead fallback, live captions. */
export default function PhoneSimulator() {
  const { elderId } = useParams();
  const id = Number(elderId);
  const nav = useNavigate();
  const { t } = useI18n();

  const [elder, setElder] = useState<ElderDetail["elder"] | null>(null);
  const [phase, setPhase] = useState<Phase>("ringing");
  const [lang, setLang] = useState<Lang>("yue");
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [subtitles, setSubtitles] = useState(true);
  const [recording, setRecording] = useState(false);
  const [micOk, setMicOk] = useState(true);
  // who does the listening: the server (real STT configured), the browser (keyless), or nobody (type instead)
  const [micMode, setMicMode] = useState<"server" | "browser" | "none">("server");
  const [interim, setInterim] = useState("");
  const [jp, setJp] = useState<JyutpingResult | null>(null);
  const listener = useRef(new BrowserListener());
  const [typed, setTyped] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const ws = useRef<WebSocket | null>(null);
  const callId = useRef<number | null>(null);
  const queue = useRef(new AudioQueue());
  const ptt = useRef(new PushToTalk());
  const keyCounter = useRef(0);
  const pendingKaifong = useRef<number | null>(null); // key of the caption being streamed
  const chunkText = useRef<Map<number, string>>(new Map());
  const langRef = useRef<Lang>("yue");
  const inputRef = useRef<HTMLInputElement>(null);
  const finished = useRef(false); // true once the call has ended (server- or user-initiated)
  const fillerCache = useRef<Map<string, { text: string; audio_b64: string | null; mime: string }>>(new Map());
  langRef.current = lang;

  useEffect(() => {
    api
      .elder(id)
      .then((d) => {
        setElder(d.elder);
        // language: ?lang=, else the demo page's choice, else the person's own
        let pref: string | null = new URLSearchParams(location.search).get("lang");
        try {
          const saved = localStorage.getItem("kf_conv_lang");
          pref = pref ?? (saved && saved !== "auto" ? saved : null);
        } catch {
          /* storage unavailable */
        }
        setLang(pref === "en" || pref === "yue" ? pref : d.elder.language === "en" ? "en" : "yue");
      })
      .catch((e: Error) => setError(e.message));
  }, [id]);

  // call timer
  useEffect(() => {
    if (phase !== "in_call") return;
    const timer = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  // always release mic / sockets / speech when leaving the page
  useEffect(() => {
    const q = queue.current;
    const p = ptt.current;
    return () => {
      q.stop();
      p.release();
      listener.current.release();
      ws.current?.close();
      // Leaving the page mid-call must not leave the call open on the server.
      if (callId.current && !finished.current) {
        void fetch(`/api/calls/${callId.current}/end`, { method: "POST", credentials: "include", keepalive: true });
      }
    };
  }, []);

  const addCaption = useCallback((c: Omit<Caption, "key">): number => {
    const key = (keyCounter.current += 1);
    setCaptions((prev) => [...prev, { ...c, key }]);
    return key;
  }, []);

  const patchCaption = useCallback((key: number, patch: Partial<Caption>) => {
    setCaptions((prev) => prev.map((c) => (c.key === key ? { ...c, ...patch } : c)));
  }, []);

  const onEvent = useCallback(
    (ev: CallEvent) => {
      switch (ev.type) {
        case "turn.transcript":
          addCaption({ speaker: "elder", index: ev.index, text: ev.text || "…", text_en: null, raw: ev.raw });
          break;
        case "turn.reply_chunk": {
          chunkText.current.set(ev.seq, ev.text);
          if (pendingKaifong.current === null) {
            pendingKaifong.current = addCaption({ speaker: "kaifong", index: null, text: ev.text, text_en: null });
          } else {
            const key = pendingKaifong.current;
            setCaptions((prev) =>
              prev.map((c) => (c.key === key ? { ...c, text: c.text + (langRef.current === "en" ? " " : "") + ev.text } : c)),
            );
          }
          break;
        }
        case "turn.reply_audio":
          queue.current.enqueue({
            text: chunkText.current.get(ev.seq) ?? "",
            lang: langRef.current,
            audio_b64: ev.audio_b64,
            mime: ev.mime,
          });
          break;
        case "turn.reply_text": {
          const key = pendingKaifong.current;
          chunkText.current.clear();
          pendingKaifong.current = null;
          if (key !== null) patchCaption(key, { index: ev.index, text: ev.text, text_en: ev.text_en });
          else addCaption({ speaker: "kaifong", index: ev.index, text: ev.text, text_en: ev.text_en });
          break;
        }
        case "turn.translation":
          setCaptions((prev) => prev.map((c) => (c.index === ev.index ? { ...c, text_en: ev.text_en } : c)));
          break;
        case "call.ended":
          finished.current = true;
          setPhase("ended");
          ptt.current.release();
          break;
        case "error":
          setError(ev.message);
          break;
      }
    },
    [addCaption, patchCaption],
  );

  async function answer() {
    setPhase("connecting");
    setError(null);
    try {
      const start = await api.startCall(id, lang);
      callId.current = start.call_id;
      addCaption({ speaker: "kaifong", index: 0, text: start.greeting.text, text_en: start.greeting.text_en });
      queue.current.enqueue({
        text: start.greeting.text,
        lang: start.language,
        audio_b64: start.greeting.audio_b64,
        mime: start.greeting.mime,
      });
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      const socket = new WebSocket(`${scheme}://${location.host}/ws/calls/${start.call_id}`);
      socket.onmessage = (m) => onEvent(JSON.parse(m.data) as CallEvent);
      socket.onerror = () => setError(t("error"));
      ws.current = socket;
      setPhase("in_call");
      if (start.stt === "mock") {
        // The server cannot transcribe audio. Let the browser listen if it can; otherwise typing only.
        const ok = speechRecognitionSupported();
        setMicMode(ok ? "browser" : "none");
        setMicOk(ok);
      } else {
        setMicMode("server");
        if (PushToTalk.supported()) ptt.current.prepare().catch(() => setMicOk(false));
        else setMicOk(false);
      }
    } catch (e) {
      setError((e as Error).message);
      setPhase("ringing");
    }
  }

  const playFiller = useCallback(async () => {
    const l = langRef.current;
    try {
      let f = fillerCache.current.get(l);
      if (!f) {
        f = await api.filler(l);
        fillerCache.current.set(l, f);
      }
      queue.current.enqueue({ text: f.text, lang: l, audio_b64: f.audio_b64, mime: f.mime });
    } catch {
      /* filler is a nicety only */
    }
  }, []);

  const send = useCallback((msg: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg));
  }, []);

  const startTalking = useCallback(() => {
    if (phase !== "in_call" || recording || !micOk) return;
    queue.current.stop(); // let the person speak without Kaifong talking over them
    if (micMode === "browser") {
      setInterim("");
      listener.current.start(langRef.current, setInterim, setError);
    } else {
      ptt.current.start();
    }
    setRecording(true);
  }, [phase, recording, micOk, micMode]);

  const stopTalking = useCallback(async () => {
    if (!recording) return;
    setRecording(false);
    if (micMode === "browser") {
      const heard = await listener.current.stop();
      setInterim("");
      if (!heard) return;
      void playFiller();
      // No confidence is sent: the server's low-confidence re-prompt is tuned for server STT, and browser scores
      // for Cantonese run low even when the text is right. The text is shown in the call for the presenter to see.
      send({ type: "text", text: heard.text });
      return;
    }
    const clip = await ptt.current.stop();
    if (!clip) return;
    void playFiller();
    send({ type: "audio", data: clip.data, mime: clip.mime });
  }, [recording, micMode, send, playFiller]);

  // keyboard shortcuts (never while typing in the box): A answer, D decline, T type, S subtitles, Esc leave the box
  const shortcutRef = useRef<(key: string) => void>(() => undefined);
  shortcutRef.current = (key: string) => {
    if (phase === "ringing" && key === "a" && elder) void answer();
    else if (phase === "ringing" && key === "d") nav(-1);
    else if (phase === "in_call" && key === "t") inputRef.current?.focus();
    else if (key === "s") setSubtitles((v) => !v);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) {
        if (e.key === "Escape") (el as HTMLElement).blur();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (["a", "d", "t", "s"].includes(k)) {
        e.preventDefault();
        shortcutRef.current(k);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // space bar = hold to talk (ignored while typing)
  useEffect(() => {
    const typing = (e: KeyboardEvent) => ["INPUT", "TEXTAREA"].includes((e.target as HTMLElement).tagName);
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && !typing(e)) {
        e.preventDefault();
        startTalking();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && !typing(e)) {
        e.preventDefault();
        void stopTalking();
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [startTalking, stopTalking]);

  function submitTyped(e: FormEvent) {
    e.preventDefault();
    const text = typed.trim();
    if (!text) return;
    queue.current.stop();
    void playFiller();
    // Typed Jyutping: send the characters the preview shows (with the original kept), if it is current
    if (looksLikeJyutping(text) && jp && jp.forText === typed) send({ type: "text", text: jp.hanzi, jyutping: text });
    else send({ type: "text", text });
    setTyped("");
    setJp(null);
  }

  async function hangUp() {
    queue.current.stop();
    if (ws.current?.readyState === WebSocket.OPEN) send({ type: "end" });
    else if (callId.current) await api.endCall(callId.current).catch(() => undefined);
    finished.current = true;
    setPhase("ended");
    ptt.current.release();
  }

  const clock = useMemo(
    () => `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`,
    [seconds],
  );
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [captions]);

  const name = elder ? `${elder.display_name} ${elder.display_name_en}` : "";

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-200 p-3">
      <p className="fixed bottom-1 left-0 right-0 z-10 text-center text-xs text-gray-600">{t("demo_shortcuts")}</p>
      <div className="flex h-[min(46rem,96vh)] w-full max-w-sm flex-col overflow-hidden rounded-[2rem] border-8 border-gray-900 bg-gray-950 text-white shadow-2xl">
        <div className="bg-yellow-200 px-3 py-1 text-center text-xs font-semibold text-yellow-950">
          {t("syntheticBanner")}
        </div>

        {phase === "ringing" && (
          <div className="flex flex-1 flex-col items-center justify-between p-8 text-center">
            <div>
              <p className="text-lg text-gray-300">{t("sim_incoming")}</p>
              <h1 className="mt-4 text-4xl font-bold">街坊</h1>
              <p className="text-2xl">Kaifong</p>
              {name && <p className="mt-4 text-gray-300">→ {name}</p>}
              <label className="mt-6 block text-sm text-gray-300">
                {t("sim_language")}{" "}
                <select
                  value={lang}
                  onChange={(e) => setLang(e.target.value as Lang)}
                  className="ml-1 rounded bg-gray-800 p-1 text-white"
                >
                  <option value="yue">廣東話</option>
                  <option value="en">English</option>
                </select>
              </label>
              {error && <p role="alert" className="mt-4 rounded bg-red-900 p-2">{error}</p>}
            </div>
            <div className="flex w-full justify-around">
              <button
                onClick={() => nav(-1)}
                className="flex h-20 w-20 flex-col items-center justify-center rounded-full bg-red-700 text-sm font-semibold"
                aria-label={t("sim_decline")}
              >
                <PhoneOff aria-hidden size={28} />
                {t("sim_decline")}
              </button>
              <button
                onClick={() => void answer()}
                disabled={!elder}
                className="flex h-20 w-20 animate-pulse flex-col items-center justify-center rounded-full bg-green-600 text-sm font-semibold disabled:opacity-50"
                aria-label={t("sim_answer")}
              >
                <Phone aria-hidden size={28} />
                {t("sim_answer")}
              </button>
            </div>
          </div>
        )}

        {phase === "connecting" && <p role="status" className="p-8 text-center text-xl">{t("sim_connecting")}</p>}

        {(phase === "in_call" || phase === "ended") && (
          <>
            <div className="flex items-center justify-between px-4 py-2">
              <div>
                <div className="font-bold">街坊 Kaifong</div>
                <div className="text-sm text-gray-300">{phase === "ended" ? t("sim_ended") : clock}</div>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={subtitles} onChange={(e) => setSubtitles(e.target.checked)} />
                {t("sim_subtitles")}
              </label>
            </div>

            <div className="flex-1 space-y-2 overflow-y-auto px-3 py-2" aria-live="polite">
              {captions.map((c) => (
                <div
                  key={c.key}
                  className={`max-w-[88%] rounded-2xl px-3 py-2 text-lg ${
                    c.speaker === "kaifong" ? "bg-blue-800" : "ml-auto bg-gray-700"
                  }`}
                >
                  <div className="text-xs font-semibold text-gray-300">
                    {c.speaker === "kaifong" ? t("sim_kaifong") : t("sim_you")}
                  </div>
                  {c.text}
                  {c.raw && <div className="text-xs text-gray-300">{c.raw}</div>}
                  {subtitles && c.text_en && <div className="mt-1 text-sm italic text-yellow-200">{c.text_en}</div>}
                </div>
              ))}
              <div ref={bottom} />
            </div>

            {error && <p role="alert" className="mx-3 mb-1 rounded bg-red-900 p-2 text-sm">{error}</p>}

            {phase === "in_call" ? (
              <div className="space-y-2 p-3">
                {micOk ? (
                  <button
                    onPointerDown={startTalking}
                    onPointerUp={() => void stopTalking()}
                    onPointerLeave={() => void stopTalking()}
                    className={`flex h-28 w-full touch-none select-none items-center justify-center gap-3 rounded-3xl text-2xl font-bold ${
                      recording ? "bg-red-600" : "bg-green-700"
                    }`}
                  >
                    <Mic aria-hidden size={36} />
                    {recording ? t("sim_recording") : t("sim_hold")}
                  </button>
                ) : (
                  <p className="rounded bg-yellow-900 p-2 text-sm">{micMode === "none" ? t("sim_noSpeech") : t("sim_micDenied")}</p>
                )}
                {recording && micMode === "browser" && (
                  <p role="status" className="rounded bg-gray-800 p-2 text-center text-lg">{interim || t("sim_listening")}</p>
                )}
                <p className="text-center text-xs text-gray-400">
                  {micMode === "browser" ? t("sim_browserSpeech") : t("sim_hint")}
                </p>
                <JyutpingPreview text={typed} onResult={setJp} />
                <form onSubmit={submitTyped} className="flex gap-2">
                  <input
                    ref={inputRef}
                    value={typed}
                    onChange={(e) => setTyped(e.target.value)}
                    placeholder={t("sim_typePlaceholder")}
                    aria-label={t("sim_type")}
                    className="min-w-0 flex-1 rounded-lg bg-gray-800 p-3 text-lg text-white"
                  />
                  <button className="rounded-lg bg-blue-700 px-4" aria-label={t("sim_send")}>
                    <Send aria-hidden />
                  </button>
                </form>
                <button
                  onClick={() => void hangUp()}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-red-700 p-3 font-semibold"
                >
                  <PhoneOff aria-hidden size={20} />
                  {t("sim_end")}
                </button>
              </div>
            ) : (
              <div className="p-3">
                <Link to={`/elders/${id}`} className="block rounded-lg bg-blue-700 p-3 text-center font-semibold">
                  {t("sim_backToDashboard")}
                </Link>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
