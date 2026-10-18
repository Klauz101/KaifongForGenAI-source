import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { ErrorBox, Loading } from "../components/States";
import { RiskBadge } from "../components/RiskBadge";
import { elderName } from "../format";
import { useAsync, useDashboardEvents } from "../hooks";
import { useI18n } from "../i18n";

export const LANG_KEY = "kf_conv_lang"; // read by the phone simulator

function storedLang(): string {
  try {
    return localStorage.getItem(LANG_KEY) ?? "auto";
  } catch {
    return "auto";
  }
}

/** Presenter's rehearsal panel: synthetic data only, admin and dev/demo environments only. */
export default function DemoControl() {
  const { t, lang } = useI18n();
  const { data, error, loading, reload } = useAsync(api.demoStatus, [], 3000);
  const [message, setMessage] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [convLang, setConvLang] = useState(storedLang);
  useDashboardEvents((e) => {
    if (e.type.startsWith("call.") || e.type.startsWith("alert.") || e.type === "risk.updated") reload();
  });

  async function run(label: string, fn: () => Promise<{ level?: string } | unknown>) {
    setProblem(null);
    try {
      const res = (await fn()) as { level?: string };
      setMessage(res?.level ? t("demo_done", { l: res.level }) : label);
      reload();
    } catch (e) {
      setProblem((e as Error).message);
    }
  }

  function chooseLang(v: string) {
    setConvLang(v);
    try {
      localStorage.setItem(LANG_KEY, v);
    } catch {
      /* storage unavailable: the simulator falls back to each person's language */
    }
  }

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return null;
  const mock = Object.values(data.providers).every((p) => p === "mock");

  return (
    <div>
      <h1 className="text-2xl font-bold">{t("demo_title")}</h1>
      <p className="text-gray-700">{t("demo_note")}</p>
      <section className="mt-3 rounded border-2 p-3">
        <h2 className="font-bold">{t("demo_providers")}</h2>
        <p>{Object.entries(data.providers).map(([k, v]) => `${k}=${v}`).join(", ")}</p>
        <p className={mock ? "text-gray-700" : "font-semibold text-red-900"}>{mock ? t("demo_mockNote") : t("demo_realNote")}</p>
        <p className="text-sm text-gray-700">{t("demo_calls", { n: data.calls_in_progress, a: data.open_alerts })}</p>
      </section>
      <div className="mt-3 flex flex-wrap items-center gap-4">
        <label>
          {t("demo_language")}{" "}
          <select value={convLang} onChange={(e) => chooseLang(e.target.value)} className="rounded border-2 p-1">
            <option value="auto">{t("demo_langAuto")}</option>
            <option value="yue">廣東話</option>
            <option value="en">English</option>
          </select>
        </label>
        <button
          onClick={() => {
            if (window.confirm(t("demo_resetConfirm"))) void run(t("demo_resetDone"), api.demoReset);
          }}
          className="rounded bg-red-800 px-4 py-2 font-semibold text-white"
        >
          {t("demo_reset")}
        </button>
      </div>
      {message && <p role="status" className="mt-2 font-semibold text-green-900">{message}</p>}
      {problem && <p role="alert" className="mt-2 font-semibold text-red-800">{problem}</p>}
      <ul className="mt-4 space-y-3">
        {data.people.map((p) => (
          <li key={p.id} className="rounded border-2 p-3">
            <div className="flex flex-wrap items-center gap-3">
              <Link to={`/elders/${p.id}`} className="text-lg font-bold text-blue-900 underline">{elderName(p, lang)}</Link>
              <RiskBadge level={p.level} />
              {p.status !== "active" && <span className="rounded bg-gray-200 px-2 text-sm font-semibold">{p.status}</span>}
              {p.call_in_progress && <span className="rounded bg-green-200 px-2 text-sm font-semibold text-green-950">● {t("demo_calls", { n: 1, a: 0 }).split(" · ")[0]}</span>}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <a
                href={`/simulator/${p.id}${convLang === "auto" ? "" : `?lang=${convLang}`}`}
                target="_blank"
                rel="noreferrer"
                className="rounded bg-green-800 px-3 py-2 font-semibold text-white"
              >
                {t("demo_startCall")}
              </a>
              <button
                disabled={!p.call_in_progress}
                title={p.call_in_progress ? "" : t("demo_injectHint")}
                onClick={() => void run(t("demo_inject"), () => api.demoInject(p.id))}
                className="rounded border-2 border-red-800 px-3 py-2 font-semibold text-red-800 disabled:opacity-40"
              >
                {t("demo_inject")}
              </button>
              <button onClick={() => void run(t("demo_decline"), () => api.demoDecline(p.id, 3))} className="rounded border-2 border-blue-900 px-3 py-2 font-semibold text-blue-900">
                {t("demo_decline")}
              </button>
              <button onClick={() => void run(t("demo_missed"), () => api.demoMissedCall(p.id))} className="rounded border-2 px-3 py-2">
                {t("demo_missed")}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
