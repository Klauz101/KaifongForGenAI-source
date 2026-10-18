import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useI18n } from "../i18n";

export interface JyutpingResult {
  forText: string; // the exact input this conversion belongs to (guards against sending a stale preview)
  hanzi: string;
}

interface Segment {
  kind: string;
  text: string;
  options: string[];
  chosen: number;
  known: boolean;
}

const WORD = /[A-Za-z][A-Za-z0-9]*/g;

/** Quick client-side guess: every Latin word is tone-numbered (zou2, san4...). The server decides for real. */
export function looksLikeJyutping(text: string): boolean {
  const words = text.match(WORD);
  return !!words && words.every((w) => /^([a-z]+[1-6])+$/i.test(w));
}

/** Live Jyutping -> characters preview. Click a word to cycle through other characters it could be. */
export function JyutpingPreview({ text, onResult }: { text: string; onResult: (r: JyutpingResult | null) => void }) {
  const { t } = useI18n();
  const [segments, setSegments] = useState<Segment[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!looksLikeJyutping(text)) {
      setSegments(null);
      onResult(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      api
        .jyutpingConvert(text)
        .then((r) => {
          if (cancelled) return;
          setFailed(false);
          setSegments(r.is_jyutping ? r.segments : null);
          onResult(r.is_jyutping ? { forText: text, hanzi: r.hanzi } : null);
        })
        .catch(() => !cancelled && (setFailed(true), onResult(null)));
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);

  function cycle(i: number) {
    if (!segments) return;
    const next = segments.map((s, k) => (k === i && s.options.length > 1 ? { ...s, chosen: (s.chosen + 1) % s.options.length } : s));
    setSegments(next);
    onResult({ forText: text, hanzi: next.map((s) => (s.kind === "jyutping" && s.options.length ? s.options[s.chosen] : s.text)).join("") });
  }

  if (failed) return <p className="text-xs text-yellow-300">{t("sim_jyutpingFailed")}</p>;
  if (!segments) return null;
  return (
    <div className="rounded-lg bg-gray-800 p-2 text-lg" aria-live="polite">
      <span className="mr-2 text-xs text-gray-400">{t("sim_jyutpingAs")}</span>
      {segments.map((s, i) =>
        s.kind === "jyutping" ? (
          <button
            key={i}
            type="button"
            onClick={() => cycle(i)}
            title={`${s.text} · ${s.options.length > 1 ? t("sim_jyutpingChange") : ""}`}
            className={`mx-0.5 rounded px-1 underline decoration-dotted ${s.known ? "text-white" : "bg-red-900 text-red-100"}`}
          >
            {s.options[s.chosen] ?? s.text}
          </button>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
      {segments.some((s) => s.kind === "jyutping" && !s.known) && (
        <p className="text-xs text-red-300">{t("sim_jyutpingUnknown")}</p>
      )}
    </div>
  );
}
