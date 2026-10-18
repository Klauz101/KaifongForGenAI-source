import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { TodayCard } from "../api/types";
import { QuickActionForm } from "../components/AlertActions";
import { Empty, ErrorBox, Loading } from "../components/States";
import { RiskBadge } from "../components/RiskBadge";
import { elderName, formatHK, reasonText } from "../format";
import { useAsync, useDashboardEvents } from "../hooks";
import { useI18n } from "../i18n";

const BORDER: Record<TodayCard["bucket"], string> = {
  red: "border-red-800",
  amber: "border-amber-600",
  green: "border-green-700",
  not_called: "border-gray-500",
};

export default function Today() {
  const { t, lang } = useI18n();
  const { data, error, loading, reload } = useAsync(api.today, [], 30_000);
  const [form, setForm] = useState<{ id: number; action: "home_visit" | "phone_call" } | null>(null);
  useDashboardEvents((ev) => {
    if (ev.type.startsWith("call.") || ev.type.startsWith("alert.") || ev.type.startsWith("elder.") || ev.type.startsWith("demo.") || ev.type === "risk.updated") reload();
  });

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return null;
  if (data.cards.length === 0) return <Empty text={t("emptyCaseload")} />;

  async function acknowledge(alertId: number) {
    await api.ackAlert(alertId);
    reload();
  }
  async function resume(elderId: number) {
    await api.setStatus(elderId, "active");
    reload();
  }

  const { summary } = data;
  return (
    <div>
      <p role="status" className="mb-4 rounded bg-blue-900 p-3 text-lg font-semibold text-white">
        {t("summaryStrip", { n: summary.need_attention, u: summary.urgent, c: summary.calls_completed })}
      </p>
      <ul className="space-y-3">
        {data.cards.map((c) => (
          <li key={c.id} className={`rounded-lg border-l-8 bg-white p-4 shadow ${BORDER[c.bucket]}`}>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-bold">{elderName(c, lang)}</h2>
              <span className="text-gray-700">
                {c.age} · {c.district}
              </span>
              {c.status === "paused" && (
                <span className="rounded bg-gray-200 px-2 py-0.5 text-sm font-semibold">{t("paused")}</span>
              )}
              <span className="ml-auto">
                {c.bucket === "not_called" ? (
                  <span className="font-semibold">{t("bucket_not_called")}</span>
                ) : (
                  <RiskBadge level={c.level} />
                )}
              </span>
            </div>
            {c.top_reasons.length > 0 && (
              <ul className="mt-2 list-disc pl-6 text-lg">
                {c.top_reasons.map((r) => (
                  <li key={r.code}>{reasonText(r, lang)}</li>
                ))}
              </ul>
            )}
            {c.open_alert && (
              <p className="mt-1 text-sm font-semibold text-red-900">
                {c.open_alert.status === "open" ? t("today_alertOpen") : t("today_alertAck")}
              </p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2 text-gray-700">
              <span>
                {t("lastCall")}:{" "}
                {c.last_call_at
                  ? `${formatHK(c.last_call_at)}${c.last_call_status === "no_answer" ? ` (${t("noAnswer")})` : ""}`
                  : t("noCall")}
              </span>
              <span className="ml-auto flex flex-wrap gap-2">
                {c.open_alert?.status === "open" && (
                  <button
                    onClick={() => void acknowledge(c.open_alert!.id)}
                    className="rounded bg-amber-700 px-3 py-2 font-semibold text-white"
                  >
                    {t("alerts_ack")}
                  </button>
                )}
                <button
                  onClick={() => setForm({ id: c.id, action: "home_visit" })}
                  className="rounded border-2 border-blue-900 px-3 py-2 font-semibold text-blue-900"
                >
                  {t("today_logVisit")}
                </button>
                <button
                  onClick={() => setForm({ id: c.id, action: "phone_call" })}
                  className="rounded border-2 border-blue-900 px-3 py-2 font-semibold text-blue-900"
                >
                  {t("today_logCall")}
                </button>
                {c.status === "paused" && (
                  <button onClick={() => void resume(c.id)} className="rounded bg-green-800 px-3 py-2 font-semibold text-white">
                    {t("resumeCalls")}
                  </button>
                )}
                <Link to={`/elders/${c.id}`} className="rounded bg-blue-900 px-4 py-2 font-semibold text-white">
                  {t("open")}
                </Link>
              </span>
            </div>
            {form?.id === c.id && (
              <QuickActionForm
                elderId={c.id}
                action={form.action}
                onCancel={() => setForm(null)}
                onDone={() => {
                  setForm(null);
                  reload();
                }}
              />
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
