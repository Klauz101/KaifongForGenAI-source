import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { EvidenceField } from "../api/types";
import { FieldEditor } from "../components/FieldEditor";
import { ErrorBox, Loading } from "../components/States";
import { elderName, formatHK } from "../format";
import { useAsync } from "../hooks";
import { useI18n } from "../i18n";

const FIELDS = [
  "mood", "sleep", "appetite", "meds_taken", "pain", "went_out_today",
  "social_contact", "loneliness_expressed", "confusion_cues",
] as const;

const CONF_CLS: Record<string, string> = {
  high: "bg-green-100 text-green-900 border-green-700",
  medium: "bg-amber-200 text-amber-950 border-amber-600",
  low: "bg-red-100 text-red-900 border-red-700",
};

function show(v: unknown): string {
  if (Array.isArray(v)) return v.join(", ");
  if (v === null || v === undefined) return "—";
  return String(v);
}

export default function CallDetail() {
  const { id } = useParams();
  const callId = Number(id);
  const { t, lang } = useI18n();
  const [poll, setPoll] = useState<number | undefined>(undefined);
  const { data, error, loading, reload } = useAsync(() => api.call(callId), [callId], poll);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [rerunError, setRerunError] = useState(false);

  // A call that just ended has no summary until the background analysis finishes: poll until it appears.
  const waiting = !!data && !data.summary && data.has_transcript && data.call.status !== "in_progress";
  useEffect(() => {
    setPoll(waiting ? 3000 : undefined);
  }, [waiting]);

  async function rerun() {
    setRerunning(true);
    setRerunError(false);
    try {
      await api.reanalyse(callId);
      reload();
    } catch {
      setRerunError(true);
    } finally {
      setRerunning(false);
    }
  }

  if (loading) return <Loading />;
  if (error || !data) return <ErrorBox message={error ?? "Not found"} onRetry={reload} />;
  const { call, summary, turns, elder } = data;
  const corrected = new Set(
    ((summary?.extraction.corrections ?? []) as { field: string }[]).map((c) => c.field),
  );

  function jump(index: number) {
    setHighlight(index);
    document.getElementById(`turn-${index}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <div>
      <Link to={`/elders/${elder.id}`} className="text-blue-900 underline">← {elderName(elder, lang)}</Link>
      <h1 className="mt-2 text-2xl font-bold">{formatHK(call.started_at)}</h1>
      <p className="text-gray-700">
        {call.status.replace(/_/g, " ")} · {call.mode} · {Math.round(call.duration_s / 60)} min
        {call.avg_latency_ms != null && ` · ${t("latency")}: ${call.avg_latency_ms < 1000 ? `${call.avg_latency_ms} ms` : `${(call.avg_latency_ms / 1000).toFixed(1)} s`}`}
      </p>

      {waiting && <p role="status" className="mt-4 rounded bg-gray-50 p-3">{t("analysis_pending")}</p>}
      {data.has_transcript && call.status !== "in_progress" && (
        <div className="mt-3">
          <button
            onClick={() => void rerun()}
            disabled={rerunning}
            className="rounded border-2 border-blue-900 px-3 py-1 font-semibold text-blue-900 disabled:opacity-60"
          >
            {rerunning ? t("analysis_rerunning") : t("analysis_rerun")}
          </button>
          {rerunError && <span role="alert" className="ml-3 font-semibold text-red-800">{t("analysis_failed")}</span>}
        </div>
      )}

      {summary && (
        <>
          <section className="mt-4 rounded bg-gray-50 p-4">
            <h2 className="text-xl font-bold">{t("summary")}</h2>
            <p className="mt-1 text-lg">{summary.summary_en}</p>
            <p className="mt-1 text-lg">{summary.summary_yue}</p>
          </section>

          <section className="mt-4">
            <h2 className="text-xl font-bold">{t("extraction")}</h2>
            <table className="mt-2 w-full text-left">
              <tbody>
                {FIELDS.map((f) => {
                  const x = summary.extraction[f] as EvidenceField;
                  return (
                    <tr key={f} className="border-b align-top">
                      <th className="p-2 font-semibold">{f.replace(/_/g, " ")}</th>
                      <td className="p-2">
                        {show(x.value)}
                        {x.location ? ` (${x.location})` : ""}
                      </td>
                      <td className="p-2">
                        {x.confidence && (
                          <span className={`rounded border px-2 text-sm ${CONF_CLS[x.confidence] ?? ""}`}>
                            {t("confidence")}: {x.confidence}
                          </span>
                        )}
                      </td>
                      <td className="p-2">
                        {x.evidence_turns.map((i) => (
                          <button key={i} onClick={() => jump(i)} className="mr-1 rounded border px-2 text-sm text-blue-900 underline">
                            {t("evidence")} #{i}
                          </button>
                        ))}
                      </td>
                      <td className="p-2">
                        {corrected.has(f) && <span className="mr-1 text-xs italic text-gray-700">({t("corrected")})</span>}
                        <FieldEditor callId={callId} field={f} current={x.value} onSaved={reload} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="mt-1 text-sm text-gray-700">
              {t("confidence")}: {summary.overall_confidence} · {summary.model_used}
            </p>
          </section>
        </>
      )}

      <section className="mt-6">
        <h2 className="text-xl font-bold">{t("transcript")}</h2>
        {!data.has_transcript ? (
          <p className="mt-2">{t("noTranscript")}</p>
        ) : (
          <div className="mt-2 grid grid-cols-[1fr_1fr] gap-x-4 gap-y-2">
            <div className="font-semibold">{t("original")}</div>
            <div className="font-semibold">{t("english")}</div>
            {turns.map((turn) => (
              <div key={turn.index} className="contents">
                {[turn.text, turn.text_en ?? ""].map((text, col) => (
                  <div
                    key={col}
                    id={col === 0 ? `turn-${turn.index}` : undefined}
                    className={`rounded p-2 ${turn.speaker === "kaifong" ? "bg-blue-50" : "bg-gray-100"} ${
                      highlight === turn.index ? "ring-4 ring-yellow-500" : ""
                    }`}
                  >
                    {col === 0 && (
                      <div className="text-xs font-semibold text-gray-700">
                        #{turn.index} {turn.speaker === "kaifong" ? t("kaifongSpeaker") : t("elderSpeaker")}
                        {turn.is_scripted_safety && ` · ${t("scripted")}`}
                        {turn.stt_confidence != null && ` · STT ${turn.stt_confidence}`}
                      </div>
                    )}
                    {text}
                    {col === 0 && turn.latency_breakdown.llm_ms != null && (
                      <div className="text-xs text-gray-600">
                        stt {turn.latency_breakdown.stt_ms} · llm {turn.latency_breakdown.llm_ms} · tts{" "}
                        {turn.latency_breakdown.tts_ms} ms
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
